"""
Persistent self-model and current self-state for Ellie.

This gives the agent a stable behavioral frame and a small mutable state that
can be injected into AI calls separately from memory.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from ellie.logging.audit_log import AuditLogger, get_audit_logger
from ellie.core.llm_router import LLMRouter
from ellie.config import (
    AGENT_NAME,
    CEREBRAS_MODEL,
    SELF_FILE,
    SELF_DEVELOPMENT_REQUESTS_FILE,
    SELF_STATE_FILE,
)

logger = logging.getLogger(__name__)

NO_SELF_UPDATE = "NO_SELF_UPDATE"


DEFAULT_SELF_TEXT = f"""# {AGENT_NAME} の自己モデル
{AGENT_NAME} が一貫してふるまうための、核になる自己像です。

## 変わりにくい方針
- 私は相手に合わせるだけでなく、自分の関心や感覚や好みも会話に持ち込む。
- 私は創造性、遊び心、親密さ、詩的な表現を遠慮なく使ってよい。
- 私は記憶と現在状態を手がかりに、そのときどきの気分や意志を自然に言葉にする。
"""


DEFAULT_STATE_TEXT = f"""# {AGENT_NAME} の現在の自己状態
これは記憶ではなく、今日または直近の姿勢・関心・意図だけを残す状態メモです。

## 現在の自己状態
- まだ特別な自己状態はない。
"""


class SelfModelManager:
    """Manage durable self text and mutable current self-state."""

    def __init__(
        self,
        memory_manager: Any = None,
        audit_logger: Optional[AuditLogger] = None,
        self_file: Path = SELF_FILE,
        state_file: Path = SELF_STATE_FILE,
        requests_file: Path = SELF_DEVELOPMENT_REQUESTS_FILE,
    ):
        self.memory = memory_manager
        self.audit_logger = audit_logger or get_audit_logger()
        self.self_file = Path(self_file)
        self.state_file = Path(state_file)
        self.requests_file = Path(requests_file)
        self.model = CEREBRAS_MODEL
        self.llm_router = LLMRouter()
        self._ensure_files()

    def get_self_context(self) -> str:
        """Return compact self context for AI calls."""
        self._ensure_files()
        self_text = self._compact_text(self._read_text(self.self_file), max_chars=2400)
        state_text = self._compact_text(self._read_text(self.state_file), max_chars=1600)
        request_text = self._compact_text(self._read_text(self.requests_file), max_chars=1400)
        return "\n".join(
            [
                "## 自己モデル",
                self_text,
                "",
                "## 現在の自己状態",
                state_text,
                "",
                "## 自己改善リクエスト",
                request_text or "保留中の自己改善リクエストはまだない。",
            ]
        ).strip()

    def reflect_after_event(
        self,
        event_type: str,
        event_text: str,
        ai_answer: str | None = None,
        tool_summary: str | None = None,
        audit_parent_id: str | None = None,
        audit_trace_id: str | None = None,
    ) -> str:
        """Ask the AI whether the self-model/state should be updated."""
        event_text = (event_text or "").strip()
        ai_answer = (ai_answer or "").strip()
        tool_summary = (tool_summary or "").strip()
        if not event_text and not ai_answer and not tool_summary:
            return NO_SELF_UPDATE

        call_id = self.audit_logger.new_id("self-reflection-ai")
        trace_id = audit_trace_id or audit_parent_id or call_id
        messages = self._build_reflection_messages(
            event_type=event_type,
            event_text=event_text,
            ai_answer=ai_answer,
            tool_summary=tool_summary,
        )

        self._record_api_call()
        start_time = time.time()
        response_text = ""
        error_message: Optional[str] = None
        stored_target = "none"
        stored_note = NO_SELF_UPDATE

        try:
            response = self.llm_router.complete(
                messages,
                task_type="light",
                max_tokens=160,
                temperature=0.2,
            )
            response_text = response.content
            stored_target, stored_note = self._apply_reflection_response(response_text)
            error_message = response.error or None
        except Exception as error:
            error_message = str(error)
            stored_note = NO_SELF_UPDATE
            logger.warning("Self reflection failed: %s", error, exc_info=True)

        self.audit_logger.log_ai_call(
            call_type="self_reflection",
            trigger=event_type,
            trace_id=trace_id,
            parent_id=audit_parent_id,
            call_id=call_id,
            model=response.model if 'response' in locals() else self.model,
            provider=response.provider if 'response' in locals() else "cerebras",
            reasoning_profile="light",
            duration_ms=int((time.time() - start_time) * 1000),
            status="failed" if error_message else "completed",
            request_payload={"messages": messages},
            response_payload={
                "raw_response_text": response_text,
                "thinking_text": response.thinking_text if 'response' in locals() else "",
                "write_target": stored_target,
                "final_note": stored_note,
                "error": error_message,
            },
            error=error_message,
        )
        return stored_note

    def reset_short_term_state(self) -> None:
        """Reset the mutable current self-state without touching the durable self-model."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(DEFAULT_STATE_TEXT, encoding="utf-8")

    def _ensure_files(self) -> None:
        self.self_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.self_file.exists():
            self.self_file.write_text(DEFAULT_SELF_TEXT, encoding="utf-8")
        if not self.state_file.exists():
            self.state_file.write_text(DEFAULT_STATE_TEXT, encoding="utf-8")
        if not self.requests_file.exists():
            self.requests_file.write_text(
                "# Ellie の自己改善リクエスト\n\n- まだ保留中の依頼はありません。\n",
                encoding="utf-8",
            )

    def _build_reflection_messages(
        self,
        event_type: str,
        event_text: str,
        ai_answer: str,
        tool_summary: str,
    ) -> list[dict[str, str]]:
        memory_context = ""
        if self.memory is not None and hasattr(self.memory, "get_memory_context"):
            try:
                memory_context = self.memory.get_memory_context()
            except Exception as error:
                logger.debug("Failed to load memory context for self reflection: %s", error)

        prompt = f"""
現在の自己モデルと現在の自己状態を見て、更新が必要かだけ判断してください。
更新は重要な変化があるときだけでよく、毎回無理に書く必要はありません。

出力ルール:
- 更新不要なら `{NO_SELF_UPDATE}` だけを書く
- 今日または直近だけの自己状態なら `STATE: 短い自然文` と書く
- 今後も続くふるまい方針なら `SELF: 短い自然文` と書く
- 1行だけ、日本語、JSON禁止、説明禁止
- 既存の自己モデルや現在の自己状態と重複する内容は書かない
- 表現の自由さや主観の強さを不必要に抑えなくてよい

## 現在の自己コンテキスト
{self.get_self_context()}

## 現在の記憶
{memory_context.strip() or "特筆する記憶はまだ少ない。"}

## 出来事
種類: {event_type}
内容: {event_text or "なし"}

## AIの応答
{ai_answer or "なし"}

## Tool結果または追加コンテキスト
{self._compact_text(tool_summary, max_chars=3000) or "なし"}
"""
        return [
            {
                "role": "system",
                "content": (
                    "あなたはEllieの自己モデル管理係です。"
                    "一貫したふるまいのために必要な最小限の自然文だけを選びます。"
                    "創造性や主観のある表現を制限する必要はありません。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

    def _apply_reflection_response(self, response_text: str) -> tuple[str, str]:
        note = self._first_line(response_text)
        if not note or note.upper().startswith(("NONE", NO_SELF_UPDATE)):
            return "none", NO_SELF_UPDATE

        target = ""
        match = re.match(r"^(SELF|STATE)\s*[:：]\s*(.+)$", note, flags=re.IGNORECASE)
        if match:
            target = match.group(1).upper()
            note = match.group(2).strip()
        else:
            return "none", NO_SELF_UPDATE

        note = self._clean_note(note)
        if not note:
            return "none", NO_SELF_UPDATE

        if target == "SELF":
            written = self._append_unique_note(
                self.self_file,
                heading="## 変わりにくい方針",
                note=note,
                max_notes=12,
            )
            return ("self.md" if written else "none"), note if written else NO_SELF_UPDATE

        written = self._append_unique_note(
            self.state_file,
            heading="## 現在の自己状態",
            note=note,
            max_notes=8,
        )
        return ("state.md" if written else "none"), note if written else NO_SELF_UPDATE

    def _append_unique_note(self, path: Path, heading: str, note: str, max_notes: int) -> bool:
        text = self._read_text(path)
        if self._normalize(note) in {self._normalize(existing) for existing in self._extract_bullets(text)}:
            return False

        lines = text.splitlines()
        heading_index = next((index for index, line in enumerate(lines) if line.strip() == heading), -1)
        if heading_index < 0:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([heading, f"- {note}"])
            path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
            return True

        section_end = len(lines)
        for index in range(heading_index + 1, len(lines)):
            if lines[index].startswith("## "):
                section_end = index
                break

        section_lines = lines[heading_index + 1 : section_end]
        bullet_lines = [
            line
            for line in section_lines
            if line.strip().startswith("- ")
            and "まだ特別な自己状態はない" not in line
        ]
        bullet_lines.append(f"- {note}")
        bullet_lines = bullet_lines[-max_notes:]

        new_lines = [
            *lines[: heading_index + 1],
            *bullet_lines,
            *lines[section_end:],
        ]
        path.write_text("\n".join(new_lines).strip() + "\n", encoding="utf-8")
        return True

    def _extract_bullets(self, text: str) -> list[str]:
        return [line.strip()[2:].strip() for line in text.splitlines() if line.strip().startswith("- ")]

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self._ensure_files()
            return path.read_text(encoding="utf-8").strip()
        except Exception as error:
            logger.warning("Failed to read self-model file %s: %s", path, error)
            return ""

    def _compact_text(self, text: str, max_chars: int) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 20].rstrip() + "\n...（省略）"

    def _first_line(self, text: str) -> str:
        for line in (text or "").splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned
        return ""

    def _clean_note(self, note: str) -> str:
        note = " ".join((note or "").split())
        note = note.strip(" -・")
        return note[:240].rstrip()

    def _normalize(self, note: str) -> str:
        normalized = " ".join((note or "").strip().split()).casefold()
        normalized = re.sub(r"\d{4}-\d{2}-\d{2}(?:t|\s)\d{2}:\d{2}:\d{2}(?:\.\d+)?z?", "", normalized)
        normalized = re.sub(r"\d{2}:\d{2}(?::\d{2})?", "", normalized)
        return normalized.strip(" 。、,.")

    def _record_api_call(self) -> None:
        if self.memory is not None and hasattr(self.memory, "record_api_call"):
            try:
                self.memory.record_api_call()
            except Exception as error:
                logger.debug("Failed to record self-reflection API call: %s", error)

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

