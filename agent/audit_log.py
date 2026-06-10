"""
Human-readable audit log for AI calls and tool execution.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from config import AGENT_NAME, LOG_DIR
from agent.time_utils import date_str_local, isoformat_local


JsonDict = Dict[str, Any]

_AUDIT_LOGGER: "AuditLogger | None" = None
_AUDIT_LOGGER_LOCK = threading.Lock()


class AuditLogger:
    """Append-only markdown audit log written once per local agent day."""

    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def log_ai_call(
        self,
        *,
        call_type: str,
        trigger: str,
        trace_id: str,
        parent_id: Optional[str] = None,
        call_id: Optional[str] = None,
        model: str = "",
        provider: str = "",
        reasoning_profile: str = "",
        reasoning_effort: str = "",
        heavy_task_id: Optional[str] = None,
        step_index: Optional[int] = None,
        duration_ms: int = 0,
        status: str = "completed",
        request_payload: Optional[JsonDict] = None,
        response_payload: Optional[JsonDict] = None,
        error: Optional[str] = None,
    ) -> str:
        call_id = call_id or self.new_id(call_type)
        lines = [
            "---",
            f"AI Call `{call_id}`",
            f"- timestamp: {self._utc_now()}",
            f"- trace_id: `{trace_id}`",
            f"- parent_id: `{parent_id}`" if parent_id else "- parent_id: `none`",
            f"- call_type: `{call_type}`",
            f"- trigger: `{trigger}`",
            f"- model: `{model or 'unknown'}`",
            f"- provider: `{provider or 'unknown'}`",
            f"- reasoning_profile: `{reasoning_profile}`" if reasoning_profile else "- reasoning_profile: `none`",
            f"- reasoning_effort: `{reasoning_effort}`" if reasoning_effort else "- reasoning_effort: `none`",
            f"- heavy_task_id: `{heavy_task_id}`" if heavy_task_id else "- heavy_task_id: `none`",
            f"- step_index: `{step_index}`" if step_index is not None else "- step_index: `none`",
            f"- duration_ms: `{duration_ms}`",
            f"- status: `{status}`",
        ]
        if error:
            lines.append(f"- error: {error}")
        if request_payload is not None:
            lines.extend(["", "Sent:", self._render_block(request_payload, language="json")])
        if response_payload is not None:
            lines.extend(["", "Returned:", self._render_block(response_payload, language="json")])
        self._append("\n".join(lines).rstrip() + "\n\n")
        return call_id

    def log_tool_call(
        self,
        *,
        tool_name: str,
        trace_id: str,
        parent_id: Optional[str] = None,
        call_id: Optional[str] = None,
        phase: str = "transport",
        duration_ms: int = 0,
        status: str = "completed",
        request_payload: Optional[JsonDict] = None,
        response_payload: Optional[JsonDict] = None,
        error: Optional[str] = None,
    ) -> str:
        call_id = call_id or self.new_id(tool_name)
        lines = [
            "---",
            f"Tool Call `{call_id}`",
            f"- timestamp: {self._utc_now()}",
            f"- trace_id: `{trace_id}`",
            f"- parent_id: `{parent_id}`" if parent_id else "- parent_id: `none`",
            f"- tool: `{tool_name}`",
            f"- phase: `{phase}`",
            f"- duration_ms: `{duration_ms}`",
            f"- status: `{status}`",
        ]
        if error:
            lines.append(f"- error: {error}")
        if request_payload is not None:
            lines.extend(["", "Sent:", self._render_block(request_payload, language="json")])
        if response_payload is not None:
            lines.extend(["", "Returned:", self._render_block(response_payload, language="json")])
        self._append("\n".join(lines).rstrip() + "\n\n")
        return call_id

    def _append(self, text: str) -> None:
        log_path = self._current_log_path()
        with self._write_lock:
            if not log_path.exists():
                log_path.write_text(self._render_header(), encoding="utf-8")
            with log_path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(text)

    def _current_log_path(self) -> Path:
        return self.log_dir / f"ai_audit_{date_str_local()}.md"

    def _render_header(self) -> str:
        date_text = date_str_local()
        return (
            f"# AI Call Audit Log - {date_text}\n\n"
            f"- Format: plain sections separated by `---`\n"
            f"- Agent: {AGENT_NAME}\n"
            f"- Date: {date_text}\n"
            f"- Timezone: local agent time\n\n"
        )

    def _render_block(self, value: Any, language: str = "text") -> str:
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        else:
            text = str(value)

        fence = "```" if "```" not in text else "````"
        return f"{fence}{language}\n{text}\n{fence}"

    def _utc_now(self) -> str:
        return isoformat_local()


def get_audit_logger() -> AuditLogger:
    """Return the process-wide audit logger."""
    global _AUDIT_LOGGER
    if _AUDIT_LOGGER is None:
        with _AUDIT_LOGGER_LOCK:
            if _AUDIT_LOGGER is None:
                _AUDIT_LOGGER = AuditLogger()
    return _AUDIT_LOGGER
