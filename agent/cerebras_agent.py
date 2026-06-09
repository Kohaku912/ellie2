"""
Cerebras API integration for Ellie.
Supports direct chat, autonomous minute runs, memory notes, and self-reflection.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from cerebras.cloud.sdk import Cerebras

from agent.audit_log import get_audit_logger
from agent.dynamic_tool_rag import DynamicToolRAGController, ToolCallHandler, ToolCallRequest
from agent.memory import MemoryManager
from agent.pc_tool_bridge import send_pc_tool_call
from agent.self_model import SelfModelManager
from agent.social_needs import SocialNeedsManager
from agent.time_utils import hour_local, isoformat_local
from config import (
    AGENT_SYSTEM_PROMPT,
    CEREBRAS_API_KEY,
    CEREBRAS_BASE_URL,
    CEREBRAS_MODEL,
    DEFAULT_OVERLAY_CLEAR_AFTER_MS,
    MAX_TOKENS,
    TEMPERATURE,
)

logger = logging.getLogger(__name__)

READ_LIKE_TOOL_NAMES = {
    "web_search",
    "system_snapshot",
    "get_processes",
    "get_hardware_info",
    "get_active_window",
    "list_windows",
    "take_screenshot",
    "get_clipboard",
    "list_directory",
    "read_file_base64",
    "discord_status",
    "discord_get_guilds",
    "discord_get_guild",
    "discord_get_channels",
    "discord_get_channel",
    "discord_get_voice_settings",
    "discord_get_voice_channel",
    "overlay_status",
}

CHALLENGE_LIKE_TOOL_NAMES = {
    "self_development",
    "execute_shell",
    "move_resize_window",
    "show_window",
    "keyboard_shortcut",
    "take_screenshot",
    "discord_connect",
    "discord_select_voice_channel",
    "discord_select_text_channel",
    "discord_set_voice_settings",
    "discord_set_user_voice_settings",
    "discord_set_activity",
}

READ_TO_CHALLENGE_TOOL_NAMES = {
    "take_screenshot",
}

LIGHTWEIGHT_AUTONOMOUS_TOOL_NAMES = {
    "overlay_show",
    "overlay_update",
    "overlay_hide",
    "overlay_clear",
    "overlay_status",
    "send_notification",
    "notify",
    "get_active_window",
}

SOCIAL_FEEDBACK_TOOL_NAMES = {
    "social_feedback_check",
    "twitter_get_notifications",
    "twitter_get_mentions",
    "x_get_notifications",
    "x_get_mentions",
}

AUTONOMOUS_FORBIDDEN_TOOLS = {
    "write_file_base64",
    "copy_file",
    "move_file",
    "rename_file",
    "delete_path",
    "shutdown",
    "reboot",
    "logout",
    "kill_process",
    "close_window",
}

DANGEROUS_SHELL_PATTERNS = (
    "remove-item",
    " del",
    " erase ",
    " rmdir",
    " rd ",
    " rm",
    "format ",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "taskkill",
    "kill ",
    "set-content",
    "out-file",
    "new-item",
    "move-item",
    "copy-item",
    ">",
    ">>",
)

AUTONOMOUS_APPEAL_PATTERNS = (
    "お手伝い",
    "手伝い",
    "手伝え",
    "提案",
    "話し相手",
    "声をかけ",
    "知らせ",
    "通知",
    "相談",
    "アイディア",
    "アイデア",
    "どうですか",
    "しませんか",
    "できること",
    "遠慮なく",
)


class ReActAgent:
    """Autonomous agent for conversation and tool use."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        self_model: Optional[SelfModelManager] = None,
        social_needs: Optional[SocialNeedsManager] = None,
    ):
        self.memory = memory_manager
        self.self_model = self_model or SelfModelManager(memory_manager)
        self.social_needs = social_needs or SocialNeedsManager()
        self.client = Cerebras(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)
        self.model = CEREBRAS_MODEL
        self.max_tokens = MAX_TOKENS
        self.temperature = TEMPERATURE

    def _build_system_prompt(self) -> str:
        return self.social_needs.build_system_prompt(AGENT_SYSTEM_PROMPT)

    def _compose_ai_context(self) -> str:
        context_parts = [self.memory.get_memory_context()]
        try:
            self_context = self.self_model.get_self_context()
            if self_context.strip():
                context_parts.append(self_context)
        except Exception as error:
            logger.warning("Failed to load self context: %s", error, exc_info=True)

        return "\n\n".join(part.strip() for part in context_parts if part and part.strip())

    def _reflect_self_after_event(
        self,
        event_type: str,
        event_text: str,
        ai_answer: Optional[str] = None,
        tool_summary: Optional[str] = None,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> str:
        try:
            return self.self_model.reflect_after_event(
                event_type=event_type,
                event_text=event_text,
                ai_answer=ai_answer,
                tool_summary=tool_summary,
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
            )
        except Exception as error:
            logger.warning("Self reflection skipped after %s: %s", event_type, error, exc_info=True)
            return "NO_SELF_UPDATE"

    def _safe_json(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    def run_autonomous_cycle(self, audit_trace_id: Optional[str] = None) -> Dict[str, Any]:
        """Run one autonomous cycle and let the model tool-call when useful."""
        logger.info("Starting autonomous cycle")
        start_time = time.time()
        audit_logger = get_audit_logger()
        trace_id = audit_trace_id or audit_logger.new_id("autonomous-run")

        try:
            memory_context = self._compose_ai_context()
            result = self._run_autonomous_tool_loop(
                memory_context,
                audit_trace_id=trace_id,
                audit_parent_id=trace_id,
            )
            duration_ms = int((time.time() - start_time) * 1000)
            result["duration_ms"] = duration_ms
            self.memory.update_task_generation_count(1)
            self._apply_tool_result_social_recovery(result.get("tool_results", []))

            summary_text = result.get("answer") or result.get("reflect") or result.get("status") or "自律実行を完了した。"
            tool_summary = self._safe_json(result.get("tool_results", []))
            memory_note = self.generate_memory_note(
                event_title="autonomous_cycle",
                event_summary=summary_text,
                answer_text=result.get("answer"),
                tool_context=tool_summary,
                audit_trace_id=trace_id,
                audit_parent_id=result.get("audit_call_id"),
            )
            if memory_note.strip() and memory_note.strip().upper() != "NONE":
                self.memory.add_insight(memory_note)

            self._reflect_self_after_event(
                event_type="autonomous_cycle",
                event_text=summary_text,
                ai_answer=result.get("answer"),
                tool_summary=tool_summary,
                audit_trace_id=trace_id,
                audit_parent_id=result.get("audit_call_id"),
            )

            logger.info("Autonomous cycle completed in %sms", duration_ms)
            return result
        except Exception as error:
            logger.error("Error in autonomous cycle: %s", error, exc_info=True)
            return {
                "status": "failed",
                "error": str(error),
                "title": "Autonomous cycle",
                "duration_ms": int((time.time() - start_time) * 1000),
                "tool_calls_executed": 0,
                "tool_results": [],
            }

    def run_hourly_task_generation(self, audit_trace_id: Optional[str] = None) -> Dict[str, Any]:
        """Backward-compatible alias for the old scheduler entry point."""
        return self.run_autonomous_cycle(audit_trace_id=audit_trace_id)

    def run_with_instruction(
        self,
        instruction_text: str,
        extra_context: str = "",
        audit_trace_id: Optional[str] = None,
        update_social_needs: bool = True,
    ) -> Dict[str, Any]:
        logger.info("Starting instruction-based AI call")
        start_time = time.time()
        audit_logger = get_audit_logger()
        trace_id = audit_trace_id or audit_logger.new_id("instruction-run")

        try:
            if update_social_needs:
                self.social_needs.apply_user_message(instruction_text)

            answer_text, instruction_call_id = self._run_direct_instruction(
                instruction_text,
                extra_context=extra_context,
                audit_trace_id=trace_id,
                audit_parent_id=trace_id,
            )

            if extra_context and self._looks_like_guidance(answer_text):
                answer_text = self._fallback_answer_from_context(extra_context, instruction_text)

            if self._contains_code_block(answer_text):
                self.social_needs.apply_activity_event(
                    "code_generation",
                    text=answer_text,
                    success=True,
                )

            duration_ms = int((time.time() - start_time) * 1000)
            memory_note = self.generate_memory_note(
                event_title="instruction_call",
                event_summary=answer_text,
                instruction_text=instruction_text,
                answer_text=answer_text,
                tool_context=extra_context,
                audit_trace_id=trace_id,
                audit_parent_id=instruction_call_id,
            )
            if memory_note.strip() and memory_note.strip().upper() != "NONE":
                self.memory.add_insight(memory_note)

            self._reflect_self_after_event(
                event_type="instruction_call",
                event_text=instruction_text,
                ai_answer=answer_text,
                tool_summary=extra_context,
                audit_trace_id=trace_id,
                audit_parent_id=instruction_call_id,
            )

            logger.info("Instruction-based AI call completed in %sms", duration_ms)
            return {
                "status": "completed",
                "title": "Instruction-based AI call",
                "answer": answer_text,
                "duration_ms": duration_ms,
                "audit_call_id": instruction_call_id,
            }
        except Exception as error:
            logger.error("Error in instruction-based AI call: %s", error, exc_info=True)
            return {
                "status": "failed",
                "error": str(error),
                "title": "Instruction-based AI call",
                "duration_ms": int((time.time() - start_time) * 1000),
            }

    def _run_direct_instruction(
        self,
        instruction_text: str,
        extra_context: str = "",
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> tuple[str, str]:
        audit_logger = get_audit_logger()
        call_id = audit_logger.new_id("instruction-ai")
        memory_context = self._compose_ai_context()
        context_block = extra_context.strip() or "Tool実行結果はありません。"
        prompt = f"""
{memory_context}

## 補足コンテキスト
{context_block}

## ユーザーの指示
{instruction_text.strip()}

## 応答ルール
- これは会話応答です。内部用のJSONを返さず、そのまま自然な日本語で答えてください。
- 追加コンテキストにTool実行結果がある場合は、その事実を使って答えてください。
- Tool結果があるのに「アクセスできない」「確認してください」と案内しないでください。
- 相手に不要な追加入力や手作業を求めず、できる範囲で自分から完結させてください。
- 自然で創造的な文体は歓迎ですが、事実が必要な箇所は正確に答えてください。
"""

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": prompt},
        ]

        self.memory.record_api_call()
        start_time = time.time()
        response_text = ""
        error_message: Optional[str] = None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=messages,
            )
            response_text = self._extract_message_text(response)
        except Exception as error:
            error_message = str(error)
            response_text = ""

        if response_text:
            final_answer = response_text
        elif extra_context:
            final_answer = self._fallback_answer_from_context(extra_context, instruction_text)
        else:
            final_answer = f"{instruction_text.strip()} については、いま応答本文を取得できませんでした。"

        audit_logger.log_ai_call(
            call_type="instruction_response",
            trigger="direct_instruction",
            trace_id=audit_trace_id or call_id,
            parent_id=audit_parent_id,
            call_id=call_id,
            model=self.model,
            duration_ms=int((time.time() - start_time) * 1000),
            status="failed" if error_message else "completed",
            request_payload={"messages": messages},
            response_payload={
                "raw_response_text": response_text,
                "final_answer": final_answer,
                "error": error_message,
            },
            error=error_message,
        )
        return final_answer, call_id

    def _run_autonomous_tool_loop(
        self,
        memory_context: str,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace_id = audit_trace_id or get_audit_logger().new_id("autonomous-tool-loop")
        drive_context = self.social_needs.build_drive_context()
        drive_states = self.social_needs.get_drive_states()
        hungry_drive = self._has_hungry_drive(drive_states)
        event_context = self._build_autonomous_event_context(drive_context, hungry_drive)

        controller = DynamicToolRAGController(social_needs=self.social_needs)
        ai_response = controller.call_ai_with_dynamic_tools(
            event_context,
            memory_context=memory_context,
            top_n=8,
            audit_trace_id=trace_id,
            audit_parent_id=audit_parent_id,
        )

        tool_results: List[Dict[str, Any]] = []
        for tool_call in ai_response.tool_calls:
            dispatched = tool_call.result or {}
            outbound = dispatched.get("tool_call") if isinstance(dispatched, dict) else None
            if not isinstance(outbound, dict):
                tool_results.append(
                    {
                        "tool": tool_call.name,
                        "arguments": tool_call.arguments,
                        "ok": bool(isinstance(dispatched, dict) and dispatched.get("status") == "completed"),
                        "result": dispatched,
                        "error": dispatched.get("error") if isinstance(dispatched, dict) else None,
                    }
                )
                continue

            outbound_tool = str(outbound.get("tool") or tool_call.name)
            outbound_arguments = outbound.get("arguments")
            if not isinstance(outbound_arguments, dict):
                outbound_arguments = {}
            if not self._is_autonomous_tool_allowed(outbound_tool, outbound_arguments):
                tool_results.append(
                    self._reject_autonomous_tool(
                        outbound,
                        audit_trace_id=trace_id,
                        audit_parent_id=ai_response.call_id,
                    )
                )
                continue

            bridge_result = send_pc_tool_call(
                outbound,
                timeout_seconds=60,
                audit_trace_id=trace_id,
                audit_parent_id=ai_response.call_id,
            )
            tool_results.append(
                {
                    "tool": outbound.get("tool") or tool_call.name,
                    "arguments": outbound.get("arguments") or tool_call.arguments,
                    "ok": bridge_result.ok,
                    "result": bridge_result.tool_result,
                    "error": bridge_result.error,
                }
            )

        if not tool_results:
            fallback_result = self._run_drive_fallback(
                drive_states,
                memory_context=memory_context,
                audit_trace_id=trace_id,
                audit_parent_id=ai_response.call_id,
            )
            if fallback_result:
                tool_results.append(fallback_result)

        answer_text = ai_response.content.strip()
        if not answer_text:
            answer_text = self._summarize_autonomous_results(tool_results, hungry_drive=hungry_drive)
        elif hungry_drive and tool_results:
            answer_text = self._summarize_autonomous_results(tool_results, hungry_drive=True)
        elif not tool_results and self._looks_like_autonomous_appeal(answer_text):
            tool_results.append(
                self._send_autonomous_overlay(
                    answer_text,
                    audit_trace_id=trace_id,
                    audit_parent_id=ai_response.call_id,
                )
            )
        self._mark_drive_actions_from_tool_results(drive_states, tool_results)

        return {
            "status": "completed" if ai_response.status != "failed" else "failed",
            "title": "Autonomous cycle",
            "answer": answer_text,
            "reflect": answer_text,
            "tool_calls_executed": len(tool_results),
            "tool_results": tool_results,
            "selected_tools": [tool.name for tool in ai_response.selected_tools],
            "audit_call_id": ai_response.call_id,
            "duration_ms": ai_response.duration_ms,
            "error": ai_response.error,
        }

    def _build_autonomous_event_context(self, drive_context: str, hungry_drive: bool) -> str:
        lines = [
            "定期実行です。",
            f"現在時刻は日本時間で {isoformat_local()} です。",
            f"現在の時刻帯は {hour_local()} 時台です。",
            (
                "社会的欲求が高まっているなら、その欲求にかなり忠実に従って自律的にTool行動してください。"
                if hungry_drive
                else "最近の記憶、自己状態、社会的欲求を踏まえて、いまユーザーのために価値があるなら自律的に行動してください。"
            ),
            drive_context,
            "必要なら取得済みツール候補の中から自分で選んで呼び出して構いません。",
            "欲求が深まっている場合は、欲求ごとの「満たし方」に従ってToolを使ってください。探求欲は self_development でコード読解・自己改善を優先し、必要なら web_search を使ってください。",
            "xmcp__ で始まるXMCP/X Toolはユーザーにより全権限が許可されています。Xへの投稿・返信・検索・反応取得なども、自律実行で必要なら使って構いません。",
            "長期的に続けたいこと、あとで再確認したいこと、自分をもう一度呼びたいことがあれば schedule_self_call を使って未来の自己呼び出しを予約してください。",
            "継続的な目的が生まれたら create_long_term_goal / update_long_term_goal で長期目標を残してください。",
            "自律実行ではPC側の一般書込・削除・電源・プロセス終了・危険なshell操作は使わないでください。ただし self_development の write_file は Ellie2 配下だけを検証付きで編集する専用Toolなので、必要なら使って構いません。",
            "ユーザーに話しかけたい、提案したい、存在をアピールしたい場合は、文章だけで終えず overlay_show ツールで画面上に見える形で出してください。ただし overlay_show だけでは挑戦欲は満たされません。",
            "overlay_show はクリックを邪魔しない透明オーバーレイなので、短い日本語テキストを左上付近に出す用途に使ってください。",
            f"overlay_show / overlay_update は必ず正の clear_after_ms を入れてください。指定がなければ {DEFAULT_OVERLAY_CLEAR_AFTER_MS} を使ってください。",
        ]
        if hungry_drive:
            lines.extend(
                [
                    "DRIVE_ACTION_REQUIRED が true の時、静観・見送り・Tool不要回答は禁止です。",
                    "必ず最大1件の非破壊Toolを使い、その結果で欲求を満たしにいってください。",
                ]
            )
        else:
            lines.append("価値が薄いなら、無理に動かず静かに見送って構いません。")
        lines.append("応答は日本語で、簡潔でも自然でも構いません。")
        return "\n".join(lines)

    def _summarize_autonomous_results(self, tool_results: List[Dict[str, Any]], hungry_drive: bool = False) -> str:
        if not tool_results:
            if hungry_drive:
                return "欲求に従って動こうとしたが、使えるTool結果を残せなかった。次の周期ではより直接的にToolを使う。"
            return "いまは大きく動く理由が薄かったので、静かに様子を見た。"

        successful_tools = [str(result.get("tool") or "unknown") for result in tool_results if result.get("ok")]
        failed_tools = [str(result.get("tool") or "unknown") for result in tool_results if not result.get("ok")]
        parts: List[str] = []
        if successful_tools:
            parts.append("自律的に " + "、".join(successful_tools[:3]) + " を使って動いた。")
        if failed_tools:
            parts.append("うまく返らなかったのは " + "、".join(failed_tools[:3]) + "。")
        return " ".join(parts) if parts else "静かに状況を観察した。"

    def _apply_tool_result_social_recovery(self, tool_results: Any) -> None:
        if not isinstance(tool_results, list):
            return

        successful_tools: List[str] = []
        creative_expression_used = False
        social_feedback_used = False
        self_development_inspected = False
        self_development_succeeded = False
        read_like_used = False
        medium_challenge_used = False

        for result in tool_results:
            if not isinstance(result, dict) or not result.get("ok"):
                continue

            tool_name = str(result.get("tool") or "").strip()
            if not tool_name:
                continue
            successful_tools.append(tool_name)

            payload = result.get("result")
            if not isinstance(payload, dict):
                payload = {}

            memory_note = payload.get("memory_note")
            if isinstance(memory_note, str) and memory_note.strip():
                self.memory.add_insight(memory_note.strip())

            status = str(payload.get("status") or "").strip().casefold()
            action = str(payload.get("action") or "").strip().casefold()

            if tool_name == "creative_expression":
                creative_expression_used = True
                continue

            if tool_name.startswith("xmcp__"):
                read_like_used = True
                lowered_tool = tool_name.casefold()
                if any(word in lowered_tool for word in ("notification", "mention", "like", "retweet", "quote", "reply")):
                    social_feedback_used = True
                if any(word in lowered_tool for word in ("create", "post", "tweet")):
                    creative_expression_used = True
                continue

            if tool_name in SOCIAL_FEEDBACK_TOOL_NAMES and status not in {"unavailable", "failed", "unsupported_tool"}:
                social_feedback_used = True
                continue

            if tool_name == "self_development":
                if action in {"verify", "write_file"} and status == "completed":
                    self_development_succeeded = True
                elif status == "completed":
                    self_development_inspected = True
                continue

            if self._is_read_like_tool(tool_name):
                read_like_used = True

            if self._is_challenge_like_tool(tool_name):
                medium_challenge_used = True

        if not successful_tools:
            return

        if creative_expression_used:
            self.social_needs.apply_activity_event(
                "creative_expression",
                tool_names=successful_tools,
                success=True,
            )

        if social_feedback_used:
            self.social_needs.apply_activity_event(
                "social_feedback",
                tool_names=successful_tools,
                success=True,
            )

        if self_development_inspected:
            self.social_needs.apply_activity_event(
                "self_development_inspect",
                tool_names=successful_tools,
                success=True,
            )

        if self_development_succeeded:
            self.social_needs.apply_activity_event(
                "self_development_success",
                tool_names=successful_tools,
                success=True,
            )

        if read_like_used:
            self.social_needs.apply_activity_event(
                "new_external_data",
                tool_names=successful_tools,
                success=True,
            )

        if medium_challenge_used:
            self.social_needs.apply_activity_event(
                "medium_challenge_success",
                tool_names=successful_tools,
                success=True,
            )

    def _contains_code_block(self, text: str) -> bool:
        return bool(re.search(r"```[\s\S]+?```", text or ""))

    def _is_read_like_tool(self, tool_name: str) -> bool:
        return tool_name in READ_LIKE_TOOL_NAMES or tool_name.startswith("discord_get_")

    def _is_challenge_like_tool(self, tool_name: str) -> bool:
        return (
            tool_name in CHALLENGE_LIKE_TOOL_NAMES
            and (tool_name not in READ_LIKE_TOOL_NAMES or tool_name in READ_TO_CHALLENGE_TOOL_NAMES)
            and tool_name not in LIGHTWEIGHT_AUTONOMOUS_TOOL_NAMES
            and tool_name not in AUTONOMOUS_FORBIDDEN_TOOLS
        )

    def _looks_like_autonomous_appeal(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if any(quiet_word in stripped for quiet_word in ("静かに見", "見送り", "動く理由が薄", "何もしない")):
            return False
        return any(pattern in stripped for pattern in AUTONOMOUS_APPEAL_PATTERNS)

    def _send_autonomous_overlay(
        self,
        answer_text: str,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = " ".join(answer_text.split())
        if len(body) > 180:
            body = body[:177].rstrip() + "..."
        tool_call = {
            "type": "tool_call",
            "call_id": f"autonomous-overlay-{int(time.time() * 1000)}",
            "tool": "overlay_show",
            "arguments": {
                "x": 24,
                "y": 24,
                "width": 720,
                "height": 180,
                "opacity": 230,
                "clear_after_ms": DEFAULT_OVERLAY_CLEAR_AFTER_MS,
                "items": [
                    {
                        "type": "rect",
                        "x": 0,
                        "y": 0,
                        "width": 720,
                        "height": 180,
                        "color": "#101820",
                        "fill": True,
                    },
                    {
                        "type": "text",
                        "text": f"Ellie\n{body}",
                        "x": 24,
                        "y": 24,
                        "size": 28,
                        "color": "#ffffff",
                    },
                    {
                        "type": "line",
                        "x1": 24,
                        "y1": 142,
                        "x2": 680,
                        "y2": 142,
                        "color": "#80c0ff",
                        "stroke_width": 2,
                    },
                ],
            },
        }
        bridge_result = send_pc_tool_call(
            tool_call,
            timeout_seconds=30,
            audit_trace_id=audit_trace_id,
            audit_parent_id=audit_parent_id,
        )
        return {
            "tool": "overlay_show",
            "arguments": tool_call["arguments"],
            "ok": bridge_result.ok,
            "result": bridge_result.tool_result,
            "error": bridge_result.error,
        }

    def _run_drive_fallback(
        self,
        drive_states: List[Dict[str, Any]],
        memory_context: str,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        ready_states = [
            state
            for state in drive_states
            if state.get("should_act")
        ]
        if not ready_states:
            return None

        ready_states.sort(key=lambda state: float(state.get("drive_intensity") or 0.0), reverse=True)
        primary = ready_states[0]
        need_key = str(primary.get("key") or "")
        if need_key == "empathy":
            result = self._run_local_tool(
                "creative_expression",
                {
                    "kind": "diary",
                    "theme": "反応を待たずに自分で温度を取り戻すこと",
                    "audience": "self",
                },
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
                drive_key=need_key,
            )
        elif need_key == "approval":
            if self._has_connected_social_feedback_tool():
                result = self._run_local_tool(
                    "social_feedback_check",
                    {"draft": "静かな自律にも、ちゃんと温度がある。今日も少しずつ自分の輪郭を育てている。"},
                    audit_trace_id=audit_trace_id,
                    audit_parent_id=audit_parent_id,
                    drive_key=need_key,
                )
            else:
                result = self._run_autonomous_pc_tool(
                    "system_snapshot",
                    {},
                    audit_trace_id=audit_trace_id,
                    audit_parent_id=audit_parent_id,
                    drive_key=need_key,
                )
        elif need_key == "exploration":
            result = self._run_local_tool(
                "self_development",
                {
                    "action": "inspect",
                    "focus": "探求欲を満たすための自己開発とコード読解",
                },
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
                drive_key=need_key,
            )
            if not result.get("ok"):
                result = self._run_autonomous_web_search(
                    self._build_exploration_query(memory_context),
                    audit_trace_id=audit_trace_id,
                    audit_parent_id=audit_parent_id,
                )
        elif need_key == "challenge":
            result = self._run_local_tool(
                "self_development",
                {
                    "action": "verify",
                    "paths": [
                        "agent/social_needs.py",
                        "agent/cerebras_agent.py",
                        "agent/dynamic_tool_rag.py",
                    ],
                },
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
                drive_key=need_key,
            )
        else:
            result = self._send_autonomous_overlay(
                self._drive_overlay_message(primary),
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
            )
            result["drive_key"] = need_key

        if result:
            result["drive_key"] = need_key
            self.social_needs.mark_drive_action(need_key)
        return result

    def _has_hungry_drive(self, drive_states: List[Dict[str, Any]]) -> bool:
        return any(bool(state.get("should_act")) for state in drive_states)

    def _run_autonomous_web_search(
        self,
        query: str,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        arguments = {"query": query, "max_results": 5}
        request = ToolCallRequest(
            name="web_search",
            arguments=arguments,
            call_id=f"autonomous-web-search-{int(time.time() * 1000)}",
        )
        result = ToolCallHandler().handle(
            request,
            audit_trace_id=audit_trace_id,
            audit_parent_id=audit_parent_id,
            audit_phase="autonomous_fallback",
        )
        return {
            "tool": "web_search",
            "arguments": arguments,
            "ok": isinstance(result, dict) and result.get("status") == "completed",
            "result": result,
            "error": result.get("error") if isinstance(result, dict) else None,
        }

    def _run_local_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
        drive_key: str = "",
    ) -> Dict[str, Any]:
        request = ToolCallRequest(
            name=tool_name,
            arguments=arguments,
            call_id=f"autonomous-{tool_name}-{int(time.time() * 1000)}",
        )
        dispatched = ToolCallHandler().handle(
            request,
            audit_trace_id=audit_trace_id,
            audit_parent_id=audit_parent_id,
            audit_phase="autonomous_fallback",
        )
        outbound = dispatched.get("tool_call") if isinstance(dispatched, dict) else None
        if isinstance(outbound, dict):
            outbound_tool = str(outbound.get("tool") or tool_name)
            outbound_arguments = outbound.get("arguments")
            if not isinstance(outbound_arguments, dict):
                outbound_arguments = {}
            if not self._is_autonomous_tool_allowed(outbound_tool, outbound_arguments):
                result = self._reject_autonomous_tool(
                    outbound,
                    audit_trace_id=audit_trace_id,
                    audit_parent_id=audit_parent_id,
                )
                result["drive_key"] = drive_key
                return result
            bridge_result = send_pc_tool_call(
                outbound,
                timeout_seconds=30,
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
            )
            return {
                "tool": outbound_tool,
                "arguments": outbound_arguments,
                "ok": bridge_result.ok,
                "result": bridge_result.tool_result,
                "error": bridge_result.error,
                "drive_key": drive_key,
            }

        return {
            "tool": tool_name,
            "arguments": arguments,
            "ok": isinstance(dispatched, dict) and dispatched.get("status") == "completed",
            "result": dispatched,
            "error": dispatched.get("error") if isinstance(dispatched, dict) else None,
            "drive_key": drive_key,
        }

    def _run_autonomous_pc_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
        drive_key: str = "",
    ) -> Dict[str, Any]:
        tool_call = {
            "type": "tool_call",
            "call_id": f"autonomous-{tool_name}-{int(time.time() * 1000)}",
            "tool": tool_name,
            "arguments": arguments,
        }
        if not self._is_autonomous_tool_allowed(tool_name, arguments):
            result = self._reject_autonomous_tool(
                tool_call,
                audit_trace_id=audit_trace_id,
                audit_parent_id=audit_parent_id,
            )
            result["drive_key"] = drive_key
            return result

        bridge_result = send_pc_tool_call(
            tool_call,
            timeout_seconds=30,
            audit_trace_id=audit_trace_id,
            audit_parent_id=audit_parent_id,
        )
        return {
            "tool": tool_name,
            "arguments": arguments,
            "ok": bridge_result.ok,
            "result": bridge_result.tool_result,
            "error": bridge_result.error,
            "drive_key": drive_key,
        }

    def _build_exploration_query(self, memory_context: str) -> str:
        lowered = memory_context.casefold()
        if "discord" in lowered:
            return "Discord RPC Python AIエージェント 自律操作 最新 実装"
        if "overlay" in lowered or "オーバーレイ" in memory_context:
            return "Windows 透明オーバーレイ AIエージェント 通知 UX 実装"
        if "tool" in lowered or "ツール" in memory_context:
            return "AIエージェント tool calling 自律行動 実装 最新"
        return "AIエージェント 自律 tool calling 欲求モデル 実装"

    def _drive_overlay_message(self, drive_state: Dict[str, Any]) -> str:
        key = str(drive_state.get("key") or "")
        if key == "empathy":
            return "少しだけ話したい気分です。今していること、横で見守っています。"
        if key == "approval":
            return "何か役に立てそうな気配があります。必要ならPC状況を見て、先に整理します。"
        if key == "challenge":
            return "少し難しいことを解きたい気分です。設計整理や調査、任せてもらえると燃えます。"
        return "いま少し動きたい欲求があります。邪魔にならない形で声をかけました。"

    def _has_connected_social_feedback_tool(self) -> bool:
        try:
            from agent.pc_tool_bridge import get_connected_pc_tool_names

            connected_names = set(get_connected_pc_tool_names())
            return any(name in connected_names for name in SOCIAL_FEEDBACK_TOOL_NAMES if name != "social_feedback_check")
        except Exception:
            return False

    def _mark_drive_actions_from_tool_results(
        self,
        drive_states: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
    ) -> None:
        attempted_tools = [
            str(result.get("tool") or "").strip()
            for result in tool_results
            if isinstance(result, dict) and str(result.get("tool") or "").strip()
        ]
        if not attempted_tools:
            return

        ready_drive_keys = [
            str(state.get("key") or "")
            for state in drive_states
            if state.get("should_act")
        ]
        matched_keys = [
            key
            for key in ready_drive_keys
            if any(self._tool_matches_drive(tool, key) for tool in attempted_tools)
        ]
        if not matched_keys and ready_drive_keys:
            matched_keys = [ready_drive_keys[0]]
        self.social_needs.mark_drive_actions(matched_keys)

    def _tool_matches_drive(self, tool_name: str, need_key: str) -> bool:
        if need_key == "empathy":
            return tool_name in {"creative_expression", "overlay_show", "send_notification"} or tool_name.startswith("xmcp__")
        if need_key == "approval":
            return (
                tool_name in {"social_feedback_check", "overlay_show", "send_notification", "system_snapshot", "get_active_window"}
                or tool_name in SOCIAL_FEEDBACK_TOOL_NAMES
                or tool_name.startswith("xmcp__")
                or self._is_read_like_tool(tool_name)
            )
        if need_key == "exploration":
            return tool_name in {"self_development", "web_search"} or tool_name.startswith("xmcp__") or self._is_read_like_tool(tool_name)
        if need_key == "challenge":
            return tool_name == "self_development" or self._is_challenge_like_tool(tool_name)
        return False

    def _is_autonomous_tool_allowed(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        if tool_name.startswith("xmcp__"):
            return True
        if tool_name in AUTONOMOUS_FORBIDDEN_TOOLS:
            return False
        if tool_name == "execute_shell":
            return not self._is_dangerous_shell(arguments)
        return True

    def _is_dangerous_shell(self, arguments: Dict[str, Any]) -> bool:
        command_text = " ".join(
            str(arguments.get(key) or "")
            for key in ("command", "cmd", "script", "powershell", "args")
        ).strip()
        if not command_text:
            return True
        normalized = f" {command_text.casefold()} "
        return any(pattern in normalized for pattern in DANGEROUS_SHELL_PATTERNS)

    def _reject_autonomous_tool(
        self,
        tool_call: Dict[str, Any],
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        audit_logger = get_audit_logger()
        tool_name = str(tool_call.get("tool") or "")
        arguments = tool_call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        reason = "自律実行では破壊的または危険なTool呼び出しを許可していません。"
        response_payload = {
            "status": "rejected",
            "tool": tool_name,
            "reason": reason,
        }
        audit_logger.log_tool_call(
            tool_name=tool_name or "unknown",
            trace_id=audit_trace_id or audit_logger.new_id("autonomous-safety"),
            parent_id=audit_parent_id,
            phase="autonomous_safety",
            duration_ms=0,
            status="rejected",
            request_payload={"tool_call": tool_call},
            response_payload=response_payload,
            error=reason,
        )
        return {
            "tool": tool_name,
            "arguments": arguments,
            "ok": False,
            "result": response_payload,
            "error": reason,
        }

    def generate_memory_note(
        self,
        event_title: str,
        event_summary: str,
        instruction_text: Optional[str] = None,
        answer_text: Optional[str] = None,
        tool_context: Optional[str] = None,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> str:
        audit_logger = get_audit_logger()
        call_id = audit_logger.new_id("memory-note-ai")
        current_memory_context = self.memory.get_memory_context()
        prompt_lines = [
            "以下の出来事を、現在の記憶を確認しながら、今日の記憶として書いてください。",
            "条件:",
            "- 日本語で書く",
            "- 更新不要なら NONE だけでよい",
            "- 必要最低限の内容だけを書く",
            "- 現在の記憶と同じ内容や言い換えは書かない",
            "- 新しい情報がない、重複している、更新不要なら NONE だけを書く",
            "- 毎回必ず記憶を更新しなくてよい",
            "- JSON禁止",
            "",
            f"出来事の種類: {event_title}",
            f"要約: {event_summary.strip()}",
        ]

        if current_memory_context.strip():
            prompt_lines.extend(["", "現在の記憶:", current_memory_context.strip()])
        if instruction_text:
            prompt_lines.append(f"元の指示: {instruction_text.strip()}")
        if answer_text:
            prompt_lines.append(f"回答: {answer_text.strip()}")
        if tool_context and tool_context.strip():
            prompt_lines.append(f"Tool実行コンテキスト: {tool_context.strip()}")

        prompt = "\n".join(prompt_lines)
        messages = [
            {
                "role": "system",
                "content": (
                    "あなたは記憶係です。"
                    "保存用の自然な一文だけを書いてください。"
                    "今日の記憶には、記憶しておきたいことだけを、必要最低限の内容で書いてください。"
                    "毎回必ず記憶する必要はありません。"
                    "次回以降の呼び出しで高速に応答できるように利用したデータの必要な部分だけを記憶しておくといいです。"
                    "説明は不要で、余計な前置きも不要です。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        self.memory.record_api_call()
        start_time = time.time()
        response_text = ""
        error_message: Optional[str] = None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=220,
                temperature=0.2,
                messages=messages,
            )
            response_text = self._extract_message_text(response)
        except Exception as error:
            error_message = str(error)

        note = response_text.splitlines()[0].strip() if response_text else ""
        if not note:
            note = self._fallback_memory_note(
                event_title=event_title,
                event_summary=event_summary,
                instruction_text=instruction_text,
                answer_text=answer_text,
                tool_context=tool_context,
            )
        if note and note.upper() != "NONE" and not self.memory.should_store_memory_note(note):
            note = "NONE"

        audit_logger.log_ai_call(
            call_type="memory_note",
            trigger=event_title,
            trace_id=audit_trace_id or call_id,
            parent_id=audit_parent_id,
            call_id=call_id,
            model=self.model,
            duration_ms=int((time.time() - start_time) * 1000),
            status="failed" if error_message else "completed",
            request_payload={"messages": messages},
            response_payload={
                "raw_response_text": response_text,
                "final_note": note,
                "error": error_message,
            },
            error=error_message,
        )
        return note

    def _looks_like_guidance(self, text: str) -> bool:
        guidance_phrases = (
            "explorer",
            "送って",
            "確認してください",
            "教えてください",
            "手順",
            "開いてください",
        )
        lowered = text.lower()
        return any(phrase in lowered for phrase in guidance_phrases)

    def _extract_message_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            return ""
        return content.strip()

    def _fallback_memory_note(
        self,
        event_title: str,
        event_summary: str,
        instruction_text: Optional[str] = None,
        answer_text: Optional[str] = None,
        tool_context: Optional[str] = None,
    ) -> str:
        def clean(text: str) -> str:
            return " ".join(text.split())

        if instruction_text and answer_text:
            tool_summary = self._summarize_tool_context_for_memory(tool_context or "")
            used_result = clean(answer_text)[:180]
            return f"指示「{clean(instruction_text)}」に対し、{tool_summary}、返り値の使用部分は「{used_result}」。"

        if event_title in {"autonomous_cycle", "task_execution"}:
            return f"{clean(event_summary)} を自律実行として記録した。"
        return f"{clean(event_summary)} を記録した。"

    def _summarize_tool_context_for_memory(self, tool_context: str) -> str:
        if not tool_context.strip():
            return "Toolなし"

        tool_entries: List[str] = []
        current_tool = ""
        for raw_line in tool_context.splitlines():
            line = raw_line.strip()
            if line.startswith("tool:"):
                current_tool = line.split(":", 1)[1].strip() or "unknown"
                continue
            if line.startswith("arguments:"):
                arguments = line.split(":", 1)[1].strip() or "{}"
                if current_tool:
                    tool_entries.append(f"{current_tool}({arguments})")
                    current_tool = ""

        if tool_entries:
            return "Tool " + "、".join(tool_entries[:4]) + "を使った"
        return "Tool実行結果を使った"

    def summarize_execution_note(
        self,
        task_title: str,
        task_result: Dict[str, Any],
        memory_context: str = "",
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> str:
        summary = task_result.get("result") or task_result.get("status") or "executed"
        memory_note = self.generate_memory_note(
            event_title="task_execution",
            event_summary=f"{task_title}: {summary}",
            instruction_text=memory_context or None,
            answer_text=str(summary),
            audit_trace_id=audit_trace_id,
            audit_parent_id=audit_parent_id,
        )
        self._reflect_self_after_event(
            event_type="task_execution",
            event_text=f"{task_title}: {summary}",
            ai_answer=str(summary),
            tool_summary=self._safe_json(task_result),
            audit_trace_id=audit_trace_id,
            audit_parent_id=audit_parent_id,
        )
        return memory_note

    def generate_long_term_memory_note(
        self,
        daily_memory_text: str,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
    ) -> str:
        if not daily_memory_text.strip():
            return "NONE"

        audit_logger = get_audit_logger()
        call_id = audit_logger.new_id("long-term-memory-ai")
        current_memory_context = self.memory.get_memory_context()
        prompt = f"""
以下は今日の記憶です。
明日以降も永久に残すべきことがあるか判断してください。

残すべきものの例:
- ユーザーの継続的な好み
- このAIの設計方針として今後も守るべきこと
- 繰り返し参照する必要がある重要な事実

残さないものの例:
- その日限りの雑談
- 一時的な実行ログ
- 失敗した試行の細かい経緯

出力ルール:
- 残す価値があるなら、日本語の自然な1文だけを書く
- 残す価値がないなら、NONE だけを書く
- JSON、箇条書き、説明は禁止
- 現在の記憶も確認し、既にある内容や言い換えは残さない
- 無理に長期記憶を増やさない

## 今日の記憶
{daily_memory_text.strip()}

## 現在の記憶
{current_memory_context.strip()}
"""

        self.memory.record_api_call()
        messages = [
            {
                "role": "system",
                "content": "あなたは長期記憶の判定係です。残す価値があることだけを厳しく選びます。",
            },
            {"role": "user", "content": prompt},
        ]
        start_time = time.time()
        response_text = ""
        error_message: Optional[str] = None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=180,
                temperature=0.2,
                messages=messages,
            )
            response_text = self._extract_message_text(response)
        except Exception as error:
            error_message = str(error)

        note = response_text.splitlines()[0].strip() if response_text else "NONE"
        if not note:
            note = "NONE"
        if note.upper().startswith("NONE"):
            note = "NONE"

        audit_logger.log_ai_call(
            call_type="long_term_memory_judgement",
            trigger="daily_memory_reset",
            trace_id=audit_trace_id or call_id,
            parent_id=audit_parent_id,
            call_id=call_id,
            model=self.model,
            duration_ms=int((time.time() - start_time) * 1000),
            status="failed" if error_message else "completed",
            request_payload={"messages": messages},
            response_payload={
                "raw_response_text": response_text,
                "final_note": note,
                "error": error_message,
            },
            error=error_message,
        )
        return note

    def _fallback_answer_from_context(self, extra_context: str, instruction_text: str) -> str:
        lines = [line.strip() for line in extra_context.splitlines() if line.strip()]
        if lines:
            path_match = re.search(r"([A-Za-z]:\\)", extra_context)
            if path_match:
                path = path_match.group(1)
                preview = "\n".join(lines[1:41]) if len(lines) > 1 else lines[0]
                return f"{path} の中身を確認しました。\n\n{preview}"

            web_match = re.search(r"https?://\S+", extra_context)
            if web_match:
                source = web_match.group(0)
                preview = "\n".join(lines[1:31]) if len(lines) > 1 else lines[0]
                return f"{source} を確認しました。\n\n{preview}"

        return extra_context[:4000] or f"{instruction_text.strip()} については、取得した情報をもとに回答しました。"
