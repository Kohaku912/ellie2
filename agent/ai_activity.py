"""Track whether Ellie AI work is currently running."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class AIActivityTracker:
    """Process-local counter for active AI work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_count = 0

    @contextmanager
    def active(self, label: str = "") -> Iterator[None]:
        self.begin(label)
        try:
            yield
        finally:
            self.end(label)

    def begin(self, label: str = "") -> None:
        with self._lock:
            self._active_count += 1

    def end(self, label: str = "") -> None:
        with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def is_active(self) -> bool:
        with self._lock:
            return self._active_count > 0

    def get_active_count(self) -> int:
        with self._lock:
            return self._active_count


_GLOBAL_AI_ACTIVITY_TRACKER = AIActivityTracker()


def get_ai_activity_tracker() -> AIActivityTracker:
    return _GLOBAL_AI_ACTIVITY_TRACKER
