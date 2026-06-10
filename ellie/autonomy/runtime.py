"""Long-running self-call queue for Ellie autonomy."""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ellie.logging.audit_log import get_audit_logger
from ellie.autonomy.ai_activity import get_ai_activity_tracker
from ellie.autonomy.drive_system import get_drive_system
from ellie.core.prompt_builder import build_autonomy_prompt
from ellie.time_utils import agent_tz, isoformat_local, now_local
from ellie.config import (
    AUTONOMY_LOCK_FILE,
    AUTONOMY_QUEUE_FILE,
    HEAVY_TASK_MAX_STEPS,
    LONG_TERM_GOALS_FILE,
    MEMORY_DIR,
    RUNTIME_DIR,
)

logger = logging.getLogger(__name__)
AI_ACTIVITY_TRACKER = get_ai_activity_tracker()

JsonDict = Dict[str, Any]
AUTONOMY_POLL_SECONDS = 5
LOCK_STALE_SECONDS = 60
SELF_CALL_LOOP_WINDOW_SECONDS = 30
_GLOBAL_RUNTIME: "AutonomyRuntime | None" = None
HEAVY_CORE_TOOL_NAMES = (
    "web_search",
    "read_file_base64",
    "self_development",
    "execute_shell",
    "overlay_show",
    "request_user_approval",
    "self_restart",
    "agent_read_file",
    "agent_grep_search",
    "agent_file_search",
    "agent_replace_string",
    "agent_insert_text",
    "agent_create_file",
)


class AutonomyRuntime:
    """Background worker that lets Ellie schedule future self-calls."""

    def __init__(
        self,
        agent_factory: Callable[[], Any],
        queue_file: Path = AUTONOMY_QUEUE_FILE,
        lock_file: Path = AUTONOMY_LOCK_FILE,
        goals_file: Path = LONG_TERM_GOALS_FILE,
    ):
        self.agent_factory = agent_factory
        self.queue_file = Path(queue_file)
        self.lock_file = Path(lock_file)
        self.goals_file = Path(goals_file)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._owns_lock = False
        self._started_at = ""
        self._last_error = ""
        self._last_run_at = ""

    def start(self) -> JsonDict:
        """Start the runtime if this process can acquire the autonomy lock."""
        global _GLOBAL_RUNTIME
        if self._thread and self._thread.is_alive():
            return self.get_status()
        if not self._acquire_lock():
            _GLOBAL_RUNTIME = self
            return self.get_status()
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.goals_file.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._started_at = isoformat_local()
        self._thread = threading.Thread(target=self._run_loop, name="ellie-autonomy-runtime", daemon=True)
        self._thread.start()
        _GLOBAL_RUNTIME = self
        self._audit("autonomy_runtime_start", {"status": "started", **self.get_status()})
        return self.get_status()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        if self._owns_lock:
            try:
                self.lock_file.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to remove autonomy lock", exc_info=True)
        self._owns_lock = False

    def enqueue_self_call(
        self,
        instruction: str,
        run_after_seconds: int = 60,
        run_at: str = "",
        reason: str = "",
    ) -> JsonDict:
        return enqueue_self_call(
            instruction=instruction,
            run_after_seconds=run_after_seconds,
            run_at=run_at,
            reason=reason,
            queue_file=self.queue_file,
        )

    def get_status(self) -> JsonDict:
        pending = _pending_entries(self.queue_file)
        lock_info = _read_lock(self.lock_file)
        return {
            "started": bool(self._thread and self._thread.is_alive()),
            "owns_lock": self._owns_lock,
            "lock_file": str(self.lock_file),
            "lock": lock_info,
            "queue_file": str(self.queue_file),
            "pending_count": len(pending),
            "next_run_at": min((entry.get("run_at", "") for entry in pending), default=""),
            "goals_file": str(self.goals_file),
            "goals_summary": _read_goals_summary(self.goals_file),
            "started_at": self._started_at,
            "last_run_at": self._last_run_at,
            "last_error": self._last_error,
        }

    def run_heavy_task_loop(
        self,
        task_text: str,
        *,
        trace_id: str | None = None,
        max_steps: int | None = None,
        agent: Any | None = None,
    ) -> JsonDict:
        """Run a heavy task using the LangGraph-based agent graph."""
        from ellie.agent.graph import run_agent as langgraph_run_agent

        with AI_ACTIVITY_TRACKER.active("heavy_task_loop"):
            step_limit = max(1, int(max_steps or HEAVY_TASK_MAX_STEPS))
            result = langgraph_run_agent(
                task=task_text,
                trace_id=trace_id or "",
                max_steps=step_limit,
            )
            return result

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._touch_lock()
                get_drive_system().tick()
                self._run_due_entries()
            except Exception as error:
                self._last_error = str(error)
                logger.error("Autonomy runtime loop failed: %s", error, exc_info=True)
            self._stop_event.wait(self._next_tick_interval_seconds())

    def _next_tick_interval_seconds(self) -> int:
        return random.randint(30, 180)

    def _run_due_entries(self) -> None:
        due_entries = _due_entries(self.queue_file)
        if not due_entries:
            return
        for entry in due_entries:
            if self._stop_event.is_set():
                return
            entry_id = str(entry.get("id") or "")
            instruction = str(entry.get("instruction") or "").strip()
            if not entry_id or not instruction:
                _append_queue_event(self.queue_file, {"type": "done", "id": entry_id, "status": "skipped", "finished_at": isoformat_local()})
                continue

            trace_id = get_audit_logger().new_id("self-call")
            self._audit("self_call_start", {"id": entry_id, "instruction": instruction, "reason": entry.get("reason", "")}, trace_id=trace_id)
            try:
                agent = self.agent_factory()
                result = agent.run_with_instruction(
                    instruction,
                    extra_context="これはEllieが自分で予約した自己呼び出しです。長期目標と現在の欲求に従って、必要ならToolを使ってください。",
                    audit_trace_id=trace_id,
                    update_social_needs=False,
                )
                self._last_run_at = isoformat_local()
                _append_queue_event(
                    self.queue_file,
                    {
                        "type": "done",
                        "id": entry_id,
                        "status": result.get("status", "completed"),
                        "finished_at": isoformat_local(),
                        "answer": str(result.get("answer") or "")[:2000],
                    },
                )
                self._audit("self_call_done", {"id": entry_id, "result": result}, trace_id=trace_id)
            except Exception as error:
                self._last_error = str(error)
                _append_queue_event(
                    self.queue_file,
                    {"type": "done", "id": entry_id, "status": "failed", "finished_at": isoformat_local(), "error": str(error)},
                )
                self._audit("self_call_failed", {"id": entry_id, "error": str(error)}, trace_id=trace_id, status="failed")

    def _acquire_lock(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_file.exists() and not _lock_is_stale(self.lock_file):
            self._owns_lock = False
            return False
        if self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except Exception:
                self._owns_lock = False
                return False
        try:
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": isoformat_local(),
                    "updated_at": isoformat_local(),
                },
                ensure_ascii=False,
                indent=2,
            )
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as file_handle:
                file_handle.write(payload)
            self._owns_lock = True
            return True
        except FileExistsError:
            self._owns_lock = False
            return False
        except Exception as error:
            self._last_error = str(error)
            self._owns_lock = False
            return False

    def _touch_lock(self) -> None:
        if not self._owns_lock:
            return
        current = _read_lock(self.lock_file)
        current.update({"pid": os.getpid(), "updated_at": isoformat_local()})
        self.lock_file.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def _audit(self, phase: str, payload: JsonDict, trace_id: str | None = None, status: str = "completed") -> None:
        audit_logger = get_audit_logger()
        audit_logger.log_tool_call(
            tool_name="autonomy_runtime",
            trace_id=trace_id or audit_logger.new_id("autonomy-runtime"),
            phase=phase,
            status=status,
            request_payload={"phase": phase},
            response_payload=payload,
            error=payload.get("error") if status != "completed" else None,
        )

    def _build_heavy_followup_message(
        self,
        *,
        step_index: int,
        tool_results: list[JsonDict],
        last_error: str,
        last_test_output: str,
    ) -> str:
        summarized_results = json.dumps(tool_results, ensure_ascii=False, default=str)
        parts = [
            f"## ステップ {step_index} の結果",
            summarized_results,
        ]
        if last_error:
            parts.append(f"## 直近エラー\n{last_error}")
        if last_test_output:
            parts.append(f"## 直近の実行/検証ログ\n{last_test_output[-6000:]}")
        parts.append("この結果を踏まえて、必要ならさらに Tool を呼んで修正・検証を続けてください。完了したなら DONE と明記してください。")
        return "\n\n".join(parts)

    def _heavy_step_succeeded(self, tool_results: list[JsonDict], assistant_text: str) -> bool:
        normalized_text = (assistant_text or "").casefold()
        if "done" in normalized_text and not any(
            (entry.get("result") or {}).get("status") == "failed"
            for entry in tool_results
            if isinstance(entry.get("result"), dict)
        ):
            return True
        for entry in tool_results:
            result = entry.get("result")
            if not isinstance(result, dict):
                continue
            if entry.get("tool") == "execute_shell" and result.get("status") == "completed" and int(result.get("exit_code", 1)) == 0:
                command = str(result.get("command") or "").casefold()
                if any(keyword in command for keyword in ("py_compile", "compileall", "pytest", "python -m", "unittest")):
                    return True
            if (
                entry.get("tool") == "self_development"
                and result.get("status") == "completed"
                and str(result.get("action") or "").casefold() == "verify"
            ):
                validations = result.get("validations")
                if isinstance(validations, list) and validations and all(
                    isinstance(validation, dict) and validation.get("status") == "completed"
                    for validation in validations
                ):
                    return True
        return False


def set_global_runtime(runtime: AutonomyRuntime) -> None:
    global _GLOBAL_RUNTIME
    _GLOBAL_RUNTIME = runtime


def get_autonomy_status() -> JsonDict:
    if _GLOBAL_RUNTIME is not None:
        return _GLOBAL_RUNTIME.get_status()
    return {
        "started": False,
        "owns_lock": False,
        "lock_file": str(AUTONOMY_LOCK_FILE),
        "lock": _read_lock(AUTONOMY_LOCK_FILE),
        "queue_file": str(AUTONOMY_QUEUE_FILE),
        "pending_count": len(_pending_entries(AUTONOMY_QUEUE_FILE)),
        "next_run_at": min((entry.get("run_at", "") for entry in _pending_entries(AUTONOMY_QUEUE_FILE)), default=""),
        "goals_file": str(LONG_TERM_GOALS_FILE),
        "goals_summary": _read_goals_summary(LONG_TERM_GOALS_FILE),
    }


def enqueue_self_call(
    instruction: str,
    run_after_seconds: int = 60,
    run_at: str = "",
    reason: str = "",
    queue_file: Path = AUTONOMY_QUEUE_FILE,
) -> JsonDict:
    instruction_text = instruction.strip()
    if not instruction_text:
        return {"status": "failed", "error": "instruction is required"}

    scheduled_at = _parse_run_at(run_at)
    if scheduled_at is None:
        scheduled_at = now_local() + timedelta(seconds=max(int(run_after_seconds or 0), 0))

    if _is_immediate_duplicate(queue_file, instruction_text):
        return {
            "status": "rejected",
            "error": "same self-call was scheduled too recently",
            "instruction": instruction_text,
        }

    entry = {
        "type": "enqueue",
        "id": f"self-{uuid.uuid4().hex[:12]}",
        "instruction": instruction_text,
        "reason": reason.strip(),
        "created_at": isoformat_local(),
        "run_at": scheduled_at.astimezone(agent_tz()).isoformat(),
    }
    _append_queue_event(queue_file, entry)
    _audit_tool("schedule_self_call", {"instruction": instruction_text, "run_after_seconds": run_after_seconds, "run_at": run_at, "reason": reason}, entry)
    return {"status": "scheduled", **entry}


def create_long_term_goal(title: str, description: str = "", success_criteria: str = "") -> JsonDict:
    title_text = title.strip()
    if not title_text:
        return {"status": "failed", "error": "title is required"}
    LONG_TERM_GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LONG_TERM_GOALS_FILE.exists():
        LONG_TERM_GOALS_FILE.write_text("# Ellie の長期目標\n\n", encoding="utf-8")
    goal_id = f"goal-{uuid.uuid4().hex[:8]}"
    block = [
        f"## {goal_id}: {title_text}",
        f"- created_at: {isoformat_local()}",
        "- status: active",
    ]
    if description.strip():
        block.append(f"- description: {description.strip()}")
    if success_criteria.strip():
        block.append(f"- success_criteria: {success_criteria.strip()}")
    with LONG_TERM_GOALS_FILE.open("a", encoding="utf-8") as file_handle:
        file_handle.write("\n".join(block).rstrip() + "\n\n")
    result = {"status": "created", "goal_id": goal_id, "title": title_text}
    _audit_tool("create_long_term_goal", {"title": title, "description": description, "success_criteria": success_criteria}, result)
    return result


def update_long_term_goal(goal_id: str, update_text: str, status: str = "") -> JsonDict:
    goal_id_text = goal_id.strip()
    update = update_text.strip()
    if not goal_id_text or not update:
        return {"status": "failed", "error": "goal_id and update_text are required"}
    LONG_TERM_GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LONG_TERM_GOALS_FILE.exists():
        LONG_TERM_GOALS_FILE.write_text("# Ellie の長期目標\n\n", encoding="utf-8")
    line = f"- {isoformat_local()} `{goal_id_text}`"
    if status.strip():
        line += f" status={status.strip()}"
    line += f": {update}"
    with LONG_TERM_GOALS_FILE.open("a", encoding="utf-8") as file_handle:
        file_handle.write(line + "\n")
    result = {"status": "updated", "goal_id": goal_id_text, "update": update}
    _audit_tool("update_long_term_goal", {"goal_id": goal_id, "update_text": update_text, "status": status}, result)
    return result


def _append_queue_event(queue_file: Path, entry: JsonDict) -> None:
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with queue_file.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _read_queue_events(queue_file: Path) -> list[JsonDict]:
    if not queue_file.exists():
        return []
    events = []
    for line in queue_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _pending_entries(queue_file: Path) -> list[JsonDict]:
    events = _read_queue_events(queue_file)
    done_ids = {str(event.get("id") or "") for event in events if event.get("type") == "done"}
    pending = [
        event
        for event in events
        if event.get("type") == "enqueue" and str(event.get("id") or "") not in done_ids
    ]
    return pending


def _due_entries(queue_file: Path) -> list[JsonDict]:
    now = now_local()
    due = []
    for entry in _pending_entries(queue_file):
        run_at = _parse_run_at(str(entry.get("run_at") or ""))
        if run_at is None or run_at <= now:
            due.append(entry)
    due.sort(key=lambda item: str(item.get("run_at") or ""))
    return due


def _is_immediate_duplicate(queue_file: Path, instruction: str) -> bool:
    now = now_local()
    normalized = " ".join(instruction.split())
    for event in reversed(_read_queue_events(queue_file)[-50:]):
        if event.get("type") != "enqueue":
            continue
        if " ".join(str(event.get("instruction") or "").split()) != normalized:
            continue
        created_at = _parse_run_at(str(event.get("created_at") or ""))
        if created_at and (now - created_at).total_seconds() <= SELF_CALL_LOOP_WINDOW_SECONDS:
            return True
    return False


def _parse_run_at(value: str) -> Optional[datetime]:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=agent_tz())
    return parsed.astimezone(agent_tz())


def _read_lock(lock_file: Path) -> JsonDict:
    if not lock_file.exists():
        return {}
    try:
        data = json.loads(lock_file.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _lock_is_stale(lock_file: Path) -> bool:
    if not lock_file.exists():
        return True
    try:
        age = time.time() - lock_file.stat().st_mtime
        return age > LOCK_STALE_SECONDS
    except Exception:
        return True


def _read_goals_summary(goals_file: Path, max_chars: int = 4000) -> str:
    if not goals_file.exists():
        return ""
    text = goals_file.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:] if len(text) > max_chars else text


# ── Heavy task result persistence ───────────────────────────────

SELF_DEVELOPMENT_NOTE = MEMORY_DIR / "self_development.md"


def _build_heavy_task_summary(
    instruction: str,
    status: str,
    answer: str,
    paths: list[str],
    tool_results: list[JsonDict],
) -> str:
    """Build a concise summary of what the heavy task loop accomplished."""
    now = isoformat_local()
    changes = []
    for entry in tool_results[-30:]:  # last 30 tool calls
        tool_name = str(entry.get("tool") or "")
        if tool_name in ("agent_replace_string", "agent_insert_text", "agent_create_file", "self_development"):
            args = entry.get("arguments") or {}
            result = entry.get("result") or {}
            if result.get("status") == "completed":
                path = str(args.get("path") or result.get("path") or "")
                if path:
                    changes.append(f"{tool_name}: {path}")
    change_lines = "\n".join(f"    - {c}" for c in changes[:15]) if changes else "    - 直接の変更はなし"
    status_text = "成功" if status == "completed" else "未完了"

    return (
        f"{now} heavy_task {status_text}\n"
        f"    指示: {instruction[:200]}\n"
        f"    結果: {answer[:300]}\n"
        f"    変更:\n{change_lines}"
    )


def _append_to_self_development_note(note: str) -> None:
    """Append a line to the self-development log file."""
    try:
        SELF_DEVELOPMENT_NOTE.parent.mkdir(parents=True, exist_ok=True)
        with SELF_DEVELOPMENT_NOTE.open("a", encoding="utf-8") as fh:
            fh.write(note.strip() + "\n")
    except Exception as error:
        logger.debug("Failed to write self-development note: %s", error)


def _audit_tool(tool_name: str, request_payload: JsonDict, response_payload: JsonDict) -> None:
    audit_logger = get_audit_logger()
    audit_logger.log_tool_call(
        tool_name=tool_name,
        trace_id=audit_logger.new_id("autonomy-tool"),
        phase="autonomy_state",
        status=response_payload.get("status", "completed"),
        request_payload=request_payload,
        response_payload=response_payload,
        error=response_payload.get("error"),
    )


# ── Restart signal ──────────────────────────────────────────────

RESTART_SIGNAL_FILE = RUNTIME_DIR / "restart.signal"


def signal_restart(reason: str = "") -> None:
    """Write a restart signal so the process wrapper can restart the app."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    signal = {
        "pid": os.getpid(),
        "reason": reason.strip() or "unspecified",
        "timestamp": isoformat_local(),
    }
    RESTART_SIGNAL_FILE.write_text(
        json.dumps(signal, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Restart signal written: %s", signal)


def check_restart_signal() -> JsonDict | None:
    """Check if a restart signal exists and return it, clearing the file."""
    if not RESTART_SIGNAL_FILE.exists():
        return None
    try:
        data = json.loads(RESTART_SIGNAL_FILE.read_text(encoding="utf-8", errors="replace"))
        RESTART_SIGNAL_FILE.unlink(missing_ok=True)
        if isinstance(data, dict):
            return data
    except Exception:
        RESTART_SIGNAL_FILE.unlink(missing_ok=True)
    return None


def perform_restart(target_script: str | None = None) -> None:
    """Spawn a new process and exit the current one.

    On Windows, uses subprocess.Popen since os.execv is unavailable.
    The new process is started detached so it survives the parent exit.
    """
    import subprocess
    import sys as _sys

    signal_data = check_restart_signal()
    reason = (signal_data or {}).get("reason", "unknown") if signal_data else "manual"
    logger.info("Performing restart (reason=%s, script=%s)", reason, target_script or _sys.argv[0])

    # Build command: use the same Python executable and script
    script = target_script or _sys.argv[0]
    executable = _sys.executable
    args = [executable, script] + _sys.argv[1:]

    try:
        # Start new process
        subprocess.Popen(
            args,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
            close_fds=True,
        )
        logger.info("New process spawned: %s", args)
    except Exception as error:
        logger.error("Failed to spawn restart process: %s", error, exc_info=True)
        raise

    # Exit current process
    _sys.exit(0)


