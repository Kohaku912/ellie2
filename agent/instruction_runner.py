"""Shared direct-instruction runner for CLI and Web chat."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from agent.audit_log import get_audit_logger
from agent.cerebras_agent import READ_LIKE_TOOL_NAMES, ReActAgent
from agent.memory import MemoryManager
from agent.self_model import SelfModelManager
from agent.social_needs import SocialNeedsManager, infer_recovery_info_kind

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class InstructionRunner:
    """Run a user instruction through Tool RAG, PC tools, memory, and the agent."""

    def __init__(
        self,
        memory: Optional[MemoryManager] = None,
        self_model: Optional[SelfModelManager] = None,
        social_needs: Optional[SocialNeedsManager] = None,
    ):
        self.memory = memory or MemoryManager()
        self.self_model = self_model or SelfModelManager(self.memory)
        self.social_needs = social_needs or SocialNeedsManager()
        self.agent = ReActAgent(self.memory, self.self_model, self.social_needs)
        self.audit_logger = get_audit_logger()

    def chat(self, message: str) -> JsonDict:
        """Run one direct chat instruction and return a Web/CLI friendly result."""
        instruction_text = message.strip()
        if not instruction_text:
            return {
                "status": "failed",
                "error": "Instruction text is empty.",
                "answer": "",
                "tasks": [],
            }

        run_trace_id = self.audit_logger.new_id("user-run")
        self.social_needs.apply_user_message(instruction_text)
        memory_context = self.memory.get_tool_memory_context()
        task_type = self.agent.classify_task_type(instruction_text, memory_context)
        tool_context = build_tool_context(
            instruction_text,
            memory_context=memory_context,
            memory_manager=self.memory,
            social_needs=self.social_needs,
            audit_trace_id=run_trace_id,
            audit_parent_id=run_trace_id,
            task_type=task_type,
        )
        read_tools = _read_tools_from_context(tool_context)
        if read_tools:
            self.social_needs.apply_activity_event(
                "new_external_data",
                text=tool_context,
                tool_names=read_tools,
                success=True,
                info_kind=infer_recovery_info_kind("new_external_data", tool_name=read_tools[0], text=tool_context, success=True),
                metadata={"tool_context": tool_context, "read_tools": read_tools},
            )
        result = self.agent.run_with_instruction(
            instruction_text,
            extra_context=tool_context,
            audit_trace_id=run_trace_id,
            update_social_needs=False,
        )

        answer = extract_answer_text(result)
        return {
            **result,
            "instruction": instruction_text,
            "answer": answer,
            "trace_id": run_trace_id,
            "tool_context": tool_context,
            "social_needs": self.social_needs.get_debug_snapshot(),
        }


def extract_answer_text(result: JsonDict) -> str:
    """Return the best display text from an agent result."""
    return str(
        result.get("answer")
        or result.get("reflect")
        or result.get("act")
        or result.get("error")
        or result.get("status")
        or ""
    )


def format_result_for_cli(result: JsonDict) -> str:
    """Format an InstructionRunner result for stdout."""
    lines = [extract_answer_text(result)]
    tasks = result.get("tasks")
    if isinstance(tasks, list) and tasks:
        lines.append("")
        lines.append("Tasks:")
        for task in tasks:
            if isinstance(task, dict):
                lines.append(f"- {task.get('title', 'Unknown')} ({task.get('type', 'unknown')})")
    return "\n".join(lines).rstrip()


def _read_tools_from_context(tool_context: str) -> List[str]:
    if not tool_context.strip() or "used_result:" not in tool_context:
        return []

    tool_names = {
        match.strip()
        for match in re.findall(r"^tool:\s*([^\r\n]+)", tool_context, flags=re.MULTILINE)
    }
    tool_names.update(
        match.strip()
        for match in re.findall(r"^## PC Tool Result:\s*([^\r\n]+)", tool_context, flags=re.MULTILINE)
    )
    return sorted(
        tool_name
        for tool_name in tool_names
        if tool_name in READ_LIKE_TOOL_NAMES or tool_name.startswith("discord_get_")
    )


def build_tool_context(
    instruction_text: str,
    memory_context: str = "",
    memory_manager: Any | None = None,
    social_needs: Any | None = None,
    audit_trace_id: str | None = None,
    audit_parent_id: str | None = None,
    task_type: str = "light",
) -> str:
    """Run PC/Discord tools when the instruction clearly needs external state."""
    from agent.dynamic_tool_rag import DynamicToolRAGController
    from agent.pc_tool_bridge import send_pc_tool_call

    audit_logger = get_audit_logger()
    trace_id = audit_trace_id or audit_logger.new_id("instruction-context")
    parent_id = audit_parent_id or trace_id
    current_parent_id = parent_id

    def send_audited_pc_tool_call(tool_call: JsonDict, timeout_seconds: int = 60) -> Any:
        return send_pc_tool_call(
            tool_call,
            timeout_seconds=timeout_seconds,
            audit_trace_id=trace_id,
            audit_parent_id=current_parent_id,
        )

    if _is_discord_voice_join_request(instruction_text):
        return _run_discord_voice_join(
            instruction_text,
            send_audited_pc_tool_call,
            memory_context=memory_context,
            memory_manager=memory_manager,
        )

    if _is_discord_voice_leave_request(instruction_text):
        return _run_discord_voice_leave(send_audited_pc_tool_call)

    forced_followers_call = _forced_twitter_followers_check_call(instruction_text)
    if forced_followers_call:
        from agent.dynamic_tool_rag import ToolCallHandler

        result = ToolCallHandler().handle(
            forced_followers_call,
            audit_trace_id=trace_id,
            audit_parent_id=parent_id,
            audit_phase="forced_instruction",
        )
        if isinstance(result, dict):
            _apply_local_tool_social_recovery(social_needs, "twitter_followers_check", result)
        return _format_local_tool_context(
            "twitter_followers_check",
            forced_followers_call.get("arguments", {}),
            result if isinstance(result, dict) else {"status": "failed", "result": result},
        )

    forced_profile_call = _forced_twitter_profile_edit_call(instruction_text)
    if forced_profile_call:
        from agent.dynamic_tool_rag import ToolCallHandler

        result = ToolCallHandler().handle(
            forced_profile_call,
            audit_trace_id=trace_id,
            audit_parent_id=parent_id,
            audit_phase="forced_instruction",
        )
        return _format_local_tool_context(
            "twitter_profile_edit",
            forced_profile_call.get("arguments", {}),
            result if isinstance(result, dict) else {"status": "failed", "result": result},
        )

    forced_twitter_call = _forced_twitter_post_call(instruction_text)
    if forced_twitter_call:
        from agent.dynamic_tool_rag import ToolCallHandler

        result = ToolCallHandler().handle(
            forced_twitter_call,
            audit_trace_id=trace_id,
            audit_parent_id=parent_id,
            audit_phase="forced_instruction",
        )
        if isinstance(result, dict):
            _apply_local_tool_social_recovery(social_needs, "twitter_post", result)
        return _format_local_tool_context(
            "twitter_post",
            forced_twitter_call.get("arguments", {}),
            result if isinstance(result, dict) else {"status": "failed", "result": result},
        )

    forced_tool_call = _forced_pc_tool_call(instruction_text)
    if forced_tool_call:
        if str(forced_tool_call.get("tool", "")).startswith("discord_") and forced_tool_call.get("tool") != "discord_status":
            send_audited_pc_tool_call(
                {
                    "type": "tool_call",
                    "call_id": "discord-connect-before-request",
                    "tool": "discord_connect",
                    "arguments": {},
                },
            )
        bridge_result = send_audited_pc_tool_call(forced_tool_call, timeout_seconds=60)
        return _format_pc_tool_context(
            instruction_text,
            bridge_result.tool_result,
            bridge_result.error,
            forced_tool_call,
        )

    controller = DynamicToolRAGController(social_needs=social_needs)
    ai_response = controller.call_ai_with_dynamic_tools(
        instruction_text,
        memory_context=memory_context,
        top_n=5,
        audit_trace_id=trace_id,
        audit_parent_id=parent_id,
        task_type=task_type,
    )
    current_parent_id = ai_response.call_id or parent_id
    context_blocks: List[str] = []

    for tool_call in ai_response.tool_calls:
        result = tool_call.result or {}
        outbound = result.get("tool_call") if isinstance(result, dict) else None
        if not isinstance(outbound, dict):
            if isinstance(result, dict):
                context_blocks.append(_format_local_tool_context(tool_call.name, tool_call.arguments, result))
                _apply_local_tool_social_recovery(social_needs, tool_call.name, result)
            continue

        resolved_context = _resolve_discord_tool_dependencies(
            instruction_text,
            outbound,
            send_audited_pc_tool_call,
            memory_context=memory_context,
            memory_manager=memory_manager,
        )
        if resolved_context:
            context_blocks.append(resolved_context)
            continue

        bridge_result = send_audited_pc_tool_call(outbound, timeout_seconds=60)
        _apply_pc_tool_social_recovery(social_needs, str(outbound.get("tool") or tool_call.name), bridge_result)
        context_blocks.append(
            _format_pc_tool_context(
                instruction_text,
                bridge_result.tool_result,
                bridge_result.error,
                outbound,
            )
        )

    return "\n\n".join(block for block in context_blocks if block).strip()


def _format_local_tool_context(tool_name: str, arguments: JsonDict, result: JsonDict) -> str:
    return "\n".join(
        [
            f"## Local Tool Result: {tool_name}",
            f"tool: {tool_name}",
            f"arguments: {json.dumps(arguments, ensure_ascii=False, default=str)}",
            f"used_result: {json.dumps(result, ensure_ascii=False, default=str)}",
        ]
    )


def _apply_local_tool_social_recovery(social_needs: Any | None, tool_name: str, result: JsonDict) -> None:
    if social_needs is None:
        return
    status = str(result.get("status") or "").strip().casefold()
    if status != "completed":
        return
    if tool_name.startswith("playwright__"):
        social_needs.apply_activity_event(
            "new_external_data",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("new_external_data", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        lowered_tool = tool_name.casefold()
        if any(word in lowered_tool for word in ("login", "auth", "mention", "like", "retweet", "quote", "reply", "notification")):
            social_needs.apply_activity_event(
                "social_feedback",
                tool_names=[tool_name],
                success=True,
                info_kind=infer_recovery_info_kind("social_feedback", tool_name=tool_name, result=result, success=True),
                metadata=result,
            )
        if any(word in lowered_tool for word in ("post", "tweet", "run_code", "navigate", "fill", "click")):
            social_needs.apply_activity_event(
                "medium_challenge_success",
                tool_names=[tool_name],
                success=True,
                info_kind=infer_recovery_info_kind("medium_challenge_success", tool_name=tool_name, result=result, success=True),
                metadata=result,
            )
        return
    if tool_name == "twitter_post":
        social_needs.apply_activity_event(
            "social_feedback",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("social_feedback", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        social_needs.apply_activity_event(
            "creative_expression",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("creative_expression", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        social_needs.apply_activity_event(
            "medium_challenge_success",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("medium_challenge_success", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        return
    if tool_name == "blog_post":
        social_needs.apply_activity_event(
            "social_feedback",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("social_feedback", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        social_needs.apply_activity_event(
            "creative_expression",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("creative_expression", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        social_needs.apply_activity_event(
            "medium_challenge_success",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("medium_challenge_success", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
        return
    if tool_name == "web_search":
        social_needs.apply_activity_event(
            "new_external_data",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("new_external_data", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
    elif tool_name == "creative_expression":
        social_needs.apply_activity_event(
            "creative_expression",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("creative_expression", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )
    elif tool_name == "self_development":
        action = str(result.get("action") or "").strip().casefold()
        if action in {"verify", "write_file"}:
            social_needs.apply_activity_event(
                "self_development_success",
                tool_names=[tool_name],
                success=True,
                info_kind=infer_recovery_info_kind("self_development_success", tool_name=tool_name, result=result, success=True),
                metadata=result,
            )
        else:
            social_needs.apply_activity_event(
                "self_development_inspect",
                tool_names=[tool_name],
                success=True,
                info_kind=infer_recovery_info_kind("self_development_inspect", tool_name=tool_name, result=result, success=True),
                metadata=result,
            )
    elif tool_name == "social_feedback_check":
        social_needs.apply_activity_event(
            "social_feedback",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("social_feedback", tool_name=tool_name, result=result, success=True),
            metadata=result,
        )


def _apply_pc_tool_social_recovery(social_needs: Any | None, tool_name: str, bridge_result: Any) -> None:
    if social_needs is None or not getattr(bridge_result, "ok", False):
        return
    if tool_name in {"twitter_get_notifications", "twitter_get_mentions", "x_get_notifications", "x_get_mentions"}:
        social_needs.apply_activity_event(
            "social_feedback",
            tool_names=[tool_name],
            success=True,
            info_kind=infer_recovery_info_kind("social_feedback", tool_name=tool_name, result=getattr(bridge_result, "tool_result", None), success=True),
            metadata=getattr(bridge_result, "tool_result", None),
        )


def _forced_pc_tool_call(instruction_text: str) -> JsonDict | None:
    lowered = instruction_text.lower()
    if "discord" in lowered and any(word in instruction_text for word in ("サーバー", "サーバ", "ギルド", "所属")):
        return {
            "type": "tool_call",
            "call_id": "discord-get-guilds",
            "tool": "discord_get_guilds",
            "arguments": {},
        }
    return None


def _forced_twitter_post_call(instruction_text: str) -> JsonDict | None:
    lowered = instruction_text.lower()
    if not any(word in lowered for word in ("twitter", "x", "????", "??", "???", "tweet")):
        return None
    if any(word in lowered for word in ("???", "draft", "???")) and not any(word in lowered for word in ("??", "????", "????", "?????", "tweet??")):
        return None
    return {
        "type": "tool_call",
        "call_id": "twitter-post-forced",
        "tool": "twitter_post",
        "arguments": {},
    }


def _forced_twitter_followers_check_call(instruction_text: str) -> JsonDict | None:
    lowered = instruction_text.lower()
    if "フォロワー" not in instruction_text and "followers" not in lowered and "follower" not in lowered:
        return None
    if not any(word in lowered for word in ("twitter", "x", "ツイ", "tweet", "フォロワー")):
        return None
    if not any(word in instruction_text for word in ("確認", "調べ", "見", "count", "number", "数")) and "followers" not in lowered:
        return None
    return {
        "type": "tool_call",
        "call_id": "twitter-followers-check-forced",
        "tool": "twitter_followers_check",
        "arguments": {},
    }


def _forced_twitter_profile_edit_call(instruction_text: str) -> JsonDict | None:
    lowered = instruction_text.lower()
    if not any(word in lowered for word in ("twitter", "x", "?????", "??????", "profile", "????", "bio")):
        return None
    if not any(word in lowered for word in ("??", "??", "???", "update", "edit", "??????")):
        return None
    return {
        "type": "tool_call",
        "call_id": "twitter-profile-edit-forced",
        "tool": "twitter_profile_edit",
        "arguments": {},
    }

def _is_discord_voice_join_request(instruction_text: str) -> bool:
    lowered = instruction_text.lower()
    mentions_voice = any(word in instruction_text for word in ("通話", "ボイス", "voice"))
    mentions_join = any(word in instruction_text for word in ("参加", "入", "join", "実行", "接続", "開始", "つない"))
    mentions_discord_server = "discord" in lowered or any(word in instruction_text for word in ("サーバー", "サーバ", "鯖"))
    return mentions_discord_server and mentions_voice and mentions_join


def _is_discord_voice_leave_request(instruction_text: str) -> bool:
    lowered = instruction_text.lower()
    mentions_voice = any(word in instruction_text for word in ("通話", "ボイス", "voice"))
    mentions_leave = any(word in instruction_text for word in ("退出", "抜け", "切断", "leave", "disconnect"))
    mentions_discord = "discord" in lowered or mentions_voice
    return mentions_discord and mentions_voice and mentions_leave


def _run_discord_voice_join(
    instruction_text: str,
    send_pc_tool_call: Any,
    memory_context: str = "",
    memory_manager: Any | None = None,
) -> str:
    remembered_target = _get_remembered_discord_voice_target(memory_manager)
    if remembered_target.get("channel_id"):
        join_arguments = {"channel_id": remembered_target["channel_id"]}
        if remembered_target.get("guild_id"):
            join_arguments["guild_id"] = remembered_target["guild_id"]
        send_pc_tool_call(
            {
                "type": "tool_call",
                "call_id": "discord-connect-before-voice-join",
                "tool": "discord_connect",
                "arguments": {},
            },
            timeout_seconds=60,
        )
        join_result = send_pc_tool_call(
            {
                "type": "tool_call",
                "call_id": "discord-voice-join-remembered-channel",
                "tool": "discord_select_voice_channel",
                "arguments": join_arguments,
            },
            timeout_seconds=60,
        )
        context = {
            "source": remembered_target.get("source", "memory"),
            "guild_id": remembered_target.get("guild_id", ""),
            "guild_name": remembered_target.get("guild_name", ""),
            "channel_id": remembered_target.get("channel_id", ""),
            "channel_name": remembered_target.get("channel_name", ""),
            "join_result": join_result.tool_result,
            "error": join_result.error,
        }
        _store_discord_voice_target(
            memory_manager,
            guild_id=remembered_target.get("guild_id", ""),
            guild_name=remembered_target.get("guild_name", ""),
            channel_id=remembered_target.get("channel_id", ""),
            channel_name=remembered_target.get("channel_name", ""),
        )
        return "## PC Tool Result: discord_voice_join\n" + json.dumps(context, ensure_ascii=False, indent=2)

    send_pc_tool_call(
        {
            "type": "tool_call",
            "call_id": "discord-connect-before-voice-join",
            "tool": "discord_connect",
            "arguments": {},
        },
        timeout_seconds=60,
    )

    guilds_result = send_pc_tool_call(
        {
            "type": "tool_call",
            "call_id": "discord-voice-join-get-guilds",
            "tool": "discord_get_guilds",
            "arguments": {},
        },
        timeout_seconds=60,
    )
    if not guilds_result.ok:
        return _format_pc_tool_context(instruction_text, guilds_result.tool_result, guilds_result.error)

    guilds = _extract_discord_guilds((guilds_result.tool_result or {}).get("result"))
    requested_guild = _extract_requested_discord_guild_name(instruction_text)
    if not requested_guild and remembered_target.get("guild_name"):
        requested_guild = remembered_target["guild_name"]
    guild = _find_named_item(guilds, requested_guild) if requested_guild else None
    if guild is None:
        names = "\n".join(f"- {item.get('name')}" for item in guilds if item.get("name"))
        return f"## PC Tool Result: discord_voice_join\n指定されたサーバーを特定できませんでした。\n候補:\n{names}"

    guild_id = str(guild.get("id") or "")
    channels_result = send_pc_tool_call(
        {
            "type": "tool_call",
            "call_id": "discord-voice-join-get-channels",
            "tool": "discord_get_channels",
            "arguments": {"guild_id": guild_id},
        },
        timeout_seconds=60,
    )
    if not channels_result.ok:
        return _format_pc_tool_context(instruction_text, channels_result.tool_result, channels_result.error)

    channels = _extract_discord_channels((channels_result.tool_result or {}).get("result"))
    requested_channel = _extract_requested_discord_channel_name(instruction_text)
    if not requested_channel and remembered_target.get("channel_name"):
        requested_channel = remembered_target["channel_name"]
    if not requested_channel and memory_context.strip():
        requested_channel = _extract_requested_discord_channel_name(memory_context)
    voice_channel = _find_voice_channel(channels, requested_channel)
    if voice_channel is None:
        names = "\n".join(f"- {item.get('name')}" for item in channels if _is_voice_channel(item) and item.get("name"))
        return f"## PC Tool Result: discord_voice_join\n{guild.get('name')} の通話チャンネルを特定できませんでした。\n候補:\n{names}"

    channel_id = str(voice_channel.get("id") or "")
    join_result = send_pc_tool_call(
        {
            "type": "tool_call",
            "call_id": "discord-voice-join-select-channel",
            "tool": "discord_select_voice_channel",
            "arguments": {"channel_id": channel_id},
        },
        timeout_seconds=60,
    )

    context = {
        "guild_name": guild.get("name"),
        "guild_id": guild_id,
        "channel_name": voice_channel.get("name"),
        "channel_id": channel_id,
        "join_result": join_result.tool_result,
        "error": join_result.error,
    }
    _store_discord_voice_target(
        memory_manager,
        guild_id=guild_id,
        guild_name=str(guild.get("name") or ""),
        channel_id=channel_id,
        channel_name=str(voice_channel.get("name") or ""),
    )
    return "## PC Tool Result: discord_voice_join\n" + json.dumps(context, ensure_ascii=False, indent=2)


def _run_discord_voice_leave(send_pc_tool_call: Any) -> str:
    send_pc_tool_call(
        {
            "type": "tool_call",
            "call_id": "discord-connect-before-voice-leave",
            "tool": "discord_connect",
            "arguments": {},
        },
        timeout_seconds=60,
    )
    leave_result = send_pc_tool_call(
        {
            "type": "tool_call",
            "call_id": "discord-voice-leave-select-null",
            "tool": "discord_select_voice_channel",
            "arguments": {"channel_id": None},
        },
        timeout_seconds=60,
    )
    context = {
        "tool_used": "discord_select_voice_channel",
        "arguments": {"channel_id": None},
        "leave_result": leave_result.tool_result,
        "error": leave_result.error,
    }
    return "## PC Tool Result: discord_select_voice_channel\n" + json.dumps(
        context,
        ensure_ascii=False,
        indent=2,
    )


def _resolve_discord_tool_dependencies(
    instruction_text: str,
    outbound: JsonDict,
    send_pc_tool_call: Any,
    memory_context: str = "",
    memory_manager: Any | None = None,
) -> str:
    tool_name = str(outbound.get("tool") or "")
    arguments = outbound.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    remembered_target = _get_remembered_discord_voice_target(memory_manager)

    if tool_name == "discord_voice_leave":
        return _run_discord_voice_leave(send_pc_tool_call)

    if tool_name == "discord_get_channels" and not arguments.get("guild_id"):
        if remembered_target.get("guild_id"):
            arguments["guild_id"] = remembered_target["guild_id"]
            outbound["arguments"] = arguments
            return ""
        return _run_discord_voice_join(
            instruction_text,
            send_pc_tool_call,
            memory_context=memory_context,
            memory_manager=memory_manager,
        )

    if tool_name == "discord_select_voice_channel" and not arguments.get("channel_id"):
        if remembered_target.get("channel_id"):
            arguments["channel_id"] = remembered_target["channel_id"]
            if remembered_target.get("guild_id"):
                arguments["guild_id"] = remembered_target["guild_id"]
            outbound["arguments"] = arguments
            return ""
        return _run_discord_voice_join(
            instruction_text,
            send_pc_tool_call,
            memory_context=memory_context,
            memory_manager=memory_manager,
        )

    return ""


def _get_remembered_discord_voice_target(memory_manager: Any | None) -> Dict[str, str]:
    if memory_manager is None or not hasattr(memory_manager, "get_discord_voice_target"):
        return {}

    target = memory_manager.get_discord_voice_target()
    if not isinstance(target, dict):
        return {}

    return {
        "guild_id": _normalize_optional_id(target.get("guild_id")),
        "channel_id": _normalize_optional_id(target.get("channel_id")),
        "guild_name": str(target.get("guild_name") or "").strip(),
        "channel_name": str(target.get("channel_name") or "").strip(),
        "source": str(target.get("source") or "memory").strip(),
    }


def _store_discord_voice_target(
    memory_manager: Any | None,
    guild_id: str = "",
    guild_name: str = "",
    channel_id: str = "",
    channel_name: str = "",
) -> None:
    if memory_manager is None:
        return

    guild_id = _normalize_optional_id(guild_id)
    channel_id = _normalize_optional_id(channel_id)

    if not channel_id and not guild_id:
        return

    if hasattr(memory_manager, "remember_discord_voice_target"):
        memory_manager.remember_discord_voice_target(
            guild_id=guild_id,
            guild_name=guild_name,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        return

    if hasattr(memory_manager, "add_user_preference"):
        memory_manager.add_user_preference(
            "discord_voice_target",
            {
                "guild_id": guild_id,
                "guild_name": guild_name,
                "channel_id": channel_id,
                "channel_name": channel_name,
            },
        )


def _normalize_optional_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"null", "none", "undefined", "nil"}:
        return ""
    return text


def _format_pc_tool_context(
    instruction_text: str,
    tool_result: JsonDict | None,
    error: str | None,
    tool_call: JsonDict | None = None,
) -> str:
    tool_name = str((tool_call or {}).get("tool") or _tool_name_from_result(tool_result or {}) or "unknown")
    arguments = (tool_call or {}).get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    header = [
        f"## PC Tool Result: {tool_name}",
        f"tool: {tool_name}",
        f"arguments: {json.dumps(arguments, ensure_ascii=False)}",
    ]

    if error:
        return "\n".join([*header, f"error: {error}"])
    if not tool_result:
        return ""

    result_payload = tool_result.get("result")

    if tool_name == "discord_get_guilds" or "discord" in instruction_text.lower():
        guild_names = _extract_discord_guild_names(result_payload)
        if guild_names:
            names_text = "\n".join(f"- {name}" for name in guild_names)
            return "\n".join([*header, "used_result: 所属Discordサーバー名", names_text])

    return "\n".join(
        [
            *header,
            "used_result: tool_result",
            json.dumps(tool_result, ensure_ascii=False, indent=2),
        ]
    )


def _tool_name_from_result(tool_result: JsonDict) -> str:
    call_id = str(tool_result.get("call_id", ""))
    if "discord" in call_id and "guild" in call_id:
        return "discord_get_guilds"
    return ""


def _extract_discord_guild_names(result_payload: Any) -> List[str]:
    return [guild["name"] for guild in _extract_discord_guilds(result_payload) if guild.get("name")]


def _extract_discord_guilds(result_payload: Any) -> List[JsonDict]:
    data = result_payload.get("data") if isinstance(result_payload, dict) else None
    if isinstance(data, dict):
        guilds = data.get("guilds") or data.get("guild") or []
    elif isinstance(data, list):
        guilds = data
    else:
        guilds = []

    normalized: List[JsonDict] = []
    if isinstance(guilds, list):
        for guild in guilds:
            if isinstance(guild, dict):
                normalized.append(guild)

    return normalized


def _extract_discord_channels(result_payload: Any) -> List[JsonDict]:
    data = result_payload.get("data") if isinstance(result_payload, dict) else None
    if isinstance(data, dict):
        channels = data.get("channels") or data.get("channel") or []
    elif isinstance(data, list):
        channels = data
    else:
        channels = []

    return [channel for channel in channels if isinstance(channel, dict)] if isinstance(channels, list) else []


def _extract_requested_discord_guild_name(instruction_text: str) -> str:
    patterns = [
        r"discord\s*の\s*(.+?)(?:サーバー|サーバ|鯖)",
        r"(.+?)(?:サーバー|サーバ|鯖)\s*の\s*(?:通話|ボイス)",
    ]
    for pattern in patterns:
        match = re.search(pattern, instruction_text, re.IGNORECASE)
        if match:
            return match.group(1).strip(" 「」\"'")
    return ""


def _extract_requested_discord_channel_name(instruction_text: str) -> str:
    patterns = [
        r"(?:通話|ボイス)(?:チャンネル)?[「\"](.+?)[」\"]",
        r"(.+?)(?:通話|ボイス)チャンネル",
    ]
    for pattern in patterns:
        match = re.search(pattern, instruction_text, re.IGNORECASE)
        if match:
            return match.group(1).strip(" 「」\"'")
    return ""


def _find_named_item(items: List[JsonDict], requested_name: str) -> JsonDict | None:
    needle = requested_name.casefold()
    for item in items:
        name = str(item.get("name") or "")
        if name.casefold() == needle:
            return item
    for item in items:
        name = str(item.get("name") or "")
        if needle and needle in name.casefold():
            return item
    return None


def _find_voice_channel(channels: List[JsonDict], requested_name: str = "") -> JsonDict | None:
    voice_channels = [channel for channel in channels if _is_voice_channel(channel)]
    if requested_name:
        found = _find_named_item(voice_channels, requested_name)
        if found:
            return found
    for channel in voice_channels:
        name = str(channel.get("name") or "").casefold()
        if any(word in name for word in ("通話", "voice", "vc", "general", "雑談")):
            return channel
    return voice_channels[0] if voice_channels else None


def _is_voice_channel(channel: JsonDict) -> bool:
    channel_type = channel.get("type")
    if channel_type in (2, 13):
        return True
    if isinstance(channel_type, str) and channel_type.upper() in {"GUILD_VOICE", "GUILD_STAGE_VOICE", "VOICE", "STAGE"}:
        return True
    name = str(channel.get("name") or "").casefold()
    return any(word in name for word in ("通話", "voice", "vc", "ボイス"))
