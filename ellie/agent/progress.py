"""Shared in-memory progress store for agent runs.

The agent graph pushes progress entries as it runs. The web server polls
this store to show step-by-step progress to the user.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


class AgentProgressStore:
    """Thread-safe store for agent run progress."""

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: Dict[str, Dict[str, Any]] = {}

    def create_run(self, run_id: str, task: str) -> None:
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "task": task,
                "started_at": time.time(),
                "phase": "analyze",
                "step_index": 0,
                "steps": [],
                "status": "running",
                "answer": "",
                "error": "",
                "modified_paths": [],
            }
            # Keep only last 10 runs
            if len(self._runs) > 10:
                oldest = sorted(self._runs.keys())[:-10]
                for k in oldest:
                    del self._runs[k]

    def update_phase(self, run_id: str, phase: str, step_index: int = 0) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run["phase"] = phase
                run["step_index"] = step_index

    def add_step(self, run_id: str, step_index: int, phase: str, tool_calls: list, content: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                step = {
                    "step": step_index,
                    "phase": phase,
                    "tool_calls": [
                        {"name": tc.get("name", "?"), "args": str(tc.get("arguments", {}))[:120]}
                        for tc in (tool_calls or [])
                    ],
                    "content_preview": content[:300] if content else "",
                    "timestamp": time.time(),
                }
                run["steps"].append(step)
                run["phase"] = phase
                run["step_index"] = step_index

    def finish_run(self, run_id: str, status: str, answer: str = "", error: str = "", modified_paths: list = None) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run["status"] = status
                run["answer"] = answer[:500]
                run["error"] = error
                run["modified_paths"] = modified_paths or []

    def get_progress(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                return dict(run)
            return None

    def get_latest_run(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._runs:
                return None
            latest_id = max(self._runs.keys(), key=lambda k: self._runs[k].get("started_at", 0))
            return dict(self._runs[latest_id])


# Global singleton
_progress_store: Optional[AgentProgressStore] = None
_progress_lock = threading.Lock()


def get_progress_store() -> AgentProgressStore:
    global _progress_store
    if _progress_store is None:
        _progress_store = AgentProgressStore()
    return _progress_store


# Convenience functions for use in agent graph nodes


def init_progress(run_id: str, task: str) -> None:
    get_progress_store().create_run(run_id, task)


def report_phase(run_id: str, phase: str, step_index: int = 0) -> None:
    get_progress_store().update_phase(run_id, phase, step_index)


def report_step(run_id: str, step_index: int, phase: str, tool_calls: list, content: str) -> None:
    get_progress_store().add_step(run_id, step_index, phase, tool_calls, content)


def report_done(run_id: str, status: str, answer: str = "", error: str = "", modified_paths: list = None) -> None:
    get_progress_store().finish_run(run_id, status, answer, error, modified_paths)
