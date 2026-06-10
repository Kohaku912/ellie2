"""Long-running self-call queue for Ellie autonomy."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.audit_log import get_audit_logger
from agent.time_utils import agent_tz, isoformat_local, now_local
from config import AUTONOMY_LOCK_FILE, AUTONOMY_QUEUE_FILE, HEAVY_TASK_MAX_STEPS, LONG_TERM_GOALS_FILE

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
AUTONOMY_POLL_SECONDS = 5
LOCK_STALE_SECONDS = 60
SELF_CALL_LOOP_WINDOW_SECONDS = 30
_GLOBAL_RUNTIME: "AutonomyRuntime | None" = None
HEAVY_CORE_TOOL_NAMES = ("web_search", "read_file_base64", "self_development", "execute_shell")


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
        from agent.dynamic_tool_rag import ToolCallHandler, ToolCallRequest
        from agent.llm_router import LLMRouter
        from agent.tool_registry import get_available_tool_definitions

        started = time.time()
        audit_logger = get_audit_logger()
        heavy_task_id = audit_logger.new_id("heavy-task")
        resolved_trace_id = trace_id or heavy_task_id
        step_limit = max(1, int(max_steps or HEAVY_TASK_MAX_STEPS))
        llm_router = LLMRouter()
        tool_handler = ToolCallHandler()
        available_tools = {tool.name: tool for tool in get_available_tool_definitions()}
        selected_tools = [available_tools[name] for name in HEAVY_CORE_TOOL_NAMES if name in available_tools]
        tool_schemas = [tool.to_openai_tool() for tool in selected_tools]
        system_prompt = (
            agent._build_system_prompt()
            if agent is not None and hasattr(agent, "_build_system_prompt")
            else "あなたは Ellie の重タスク実行レイヤーです。"
        )
        heavy_rules = (
            "これは重タスクの同期実行セッションです。"
            "コード修正・調査・実行検証を同じ文脈のまま粘り強く続けてください。"
            "使えるコアツールは web_search / read_file_base64 / self_development / execute_shell です。"
            "必要なら self_development で inspect・write_file・verify を行い、execute_shell で py_compile やテストを実行してください。"
            "不足機能を見つけたら、まず調査し、可能なら小さく実装し、危険な操作やプロジェクト外編集は行わないでください。"
            "各ステップでは可能な限り1件以上の Tool を呼び、失敗したらエラーを踏まえて次手を変えてください。"
            "完了できたと判断した場合だけ、短い日本語で DONE と書き、その理由を1〜3文で述べてください。"
        )
        conversation_history: list[JsonDict] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"{heavy_rules}\n\n"
                    f"## 依頼\n{task_text.strip()}\n\n"
                    "まず現在の方針を決め、必要な Tool を使って進めてください。"
                ),
            },
        ]
        modified_paths: list[str] = []
        all_tool_results: list[JsonDict] = []
        last_error = ""
        last_test_output = ""
        final_answer = ""
        final_status = "failed"
        final_call_id = ""

        for step_index in range(1, step_limit + 1):
            call_id = audit_logger.new_id("heavy-step")
            final_call_id = call_id
            response = llm_router.complete(
                conversation_history,
                task_type="heavy",
                max_tokens=2400,
                temperature=0.3,
                tools=tool_schemas,
                tool_choice="auto",
            )
            audit_logger.log_ai_call(
                call_type="heavy_task_step",
                trigger="heavy_task_loop",
                trace_id=resolved_trace_id,
                parent_id=heavy_task_id,
                call_id=call_id,
                model=response.model,
                provider=response.provider,
                reasoning_profile="heavy",
                reasoning_effort=response.reasoning_effort,
                heavy_task_id=heavy_task_id,
                step_index=step_index,
                duration_ms=response.duration_ms,
                status="failed" if response.error else ("tool_call_requested" if response.tool_calls else "completed"),
                request_payload={"messages": conversation_history, "tools": tool_schemas},
                response_payload={
                    "content": response.content,
                    "thinking_text": response.thinking_text,
                    "tool_calls": response.tool_calls,
                },
                error=response.error or None,
            )
            if response.error:
                final_answer = "重タスクの推論呼び出しに失敗しました。"
                last_error = response.error
                break

            assistant_text = (response.content or "").strip()
            final_answer = assistant_text or final_answer
            step_tool_results: list[JsonDict] = []
            tool_calls = response.tool_calls or []

            if not tool_calls:
                fallback_arguments: JsonDict
                fallback_name: str
                if modified_paths:
                    fallback_name = "self_development"
                    fallback_arguments = {"action": "verify", "paths": modified_paths[-8:]}
                else:
                    fallback_name = "self_development"
                    fallback_arguments = {"action": "inspect", "focus": task_text[:200]}
                tool_calls = [{"id": f"{call_id}-fallback", "name": fallback_name, "arguments": fallback_arguments}]

            for raw_call in tool_calls:
                tool_call = ToolCallRequest(
                    name=str(raw_call.get("name") or "").strip(),
                    arguments=raw_call.get("arguments") if isinstance(raw_call.get("arguments"), dict) else {},
                    call_id=str(raw_call.get("id") or "").strip() or None,
                )
                result = tool_handler.handle(
                    tool_call,
                    audit_trace_id=resolved_trace_id,
                    audit_parent_id=call_id,
                    audit_phase="heavy_task_loop",
                )
                step_tool_results.append({"tool": tool_call.name, "arguments": tool_call.arguments, "result": result})
                all_tool_results.append({"step_index": step_index, "tool": tool_call.name, "arguments": tool_call.arguments, "result": result})

                if isinstance(result, dict):
                    if result.get("path"):
                        modified_paths.append(str(result.get("path")))
                    validation = result.get("validation")
                    if isinstance(validation, dict) and validation.get("path"):
                        modified_paths.append(str(validation.get("path")))
                    if tool_call.name == "execute_shell":
                        combined_output = "\n".join(
                            part for part in [str(result.get("stdout") or ""), str(result.get("stderr") or "")] if part
                        ).strip()
                        last_test_output = combined_output[-12000:]
                    if result.get("status") == "failed":
                        last_error = str(result.get("error") or result.get("stderr") or "tool failed")

            conversation_history.append({"role": "assistant", "content": assistant_text or f"step {step_index}"})
            conversation_history.append(
                {
                    "role": "user",
                    "content": self._build_heavy_followup_message(
                        step_index=step_index,
                        tool_results=step_tool_results,
                        last_error=last_error,
                        last_test_output=last_test_output,
                    ),
                }
            )

            if self._heavy_step_succeeded(step_tool_results, assistant_text):
                final_status = "completed"
                if not final_answer:
                    final_answer = "重タスクの修正と検証が成功しました。"
                break
            if step_index == step_limit:
                final_answer = final_answer or "重タスクは上限ステップまで試行しましたが、完了には至りませんでした。"

        return {
            "status": final_status,
            "title": "Heavy task loop",
            "summary": final_answer,
            "answer": final_answer,
            "duration_ms": int((time.time() - started) * 1000),
            "audit_call_id": final_call_id or heavy_task_id,
            "heavy_task_id": heavy_task_id,
            "steps": step_limit if final_status == "completed" else min(step_limit, len(all_tool_results) or step_limit),
            "tool_results": all_tool_results,
            "last_error": last_error,
            "last_test_output": last_test_output,
            "modified_paths": sorted(dict.fromkeys(modified_paths)),
        }

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._touch_lock()
                self._run_due_entries()
            except Exception as error:
                self._last_error = str(error)
                logger.error("Autonomy runtime loop failed: %s", error, exc_info=True)
            self._stop_event.wait(AUTONOMY_POLL_SECONDS)

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
