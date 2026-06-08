"""
Lightweight memory manager for the autonomous agent.
Stores today's memory as short natural-language notes instead of JSON.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

from config import (
    AGENT_NAME,
    MEMORY_FILE,
    LONG_TERM_MEMORY_FILE,
    TASK_LOG_FILE,
    MEMORY_DIR,
    ARCHIVE_DIR,
    MEMORY_DELETE_DAYS,
)

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages short natural-language memory for the agent."""

    def __init__(self):
        self.memory_file = MEMORY_FILE
        self.long_term_memory_file = LONG_TERM_MEMORY_FILE
        self.task_log_file = TASK_LOG_FILE
        self.memory_dir = MEMORY_DIR
        self.archive_dir = ARCHIVE_DIR

        self.immediate: Dict[str, Any] = {}
        self.session = self._create_fresh_memory()
        self._load_session_memory()

    def _create_fresh_memory(self) -> Dict[str, Any]:
        """Create fresh in-memory state for the current day."""
        return {
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "agent_name": AGENT_NAME,
            "daily_stats": {
                "tasks_generated": 0,
                "tasks_executed": 0,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_execution_time_ms": 0,
                "total_api_calls": 0,
            },
            "execution_history": [],
            "user_preferences": {},
            "today_insights": [],
            "memory_notes": [],
            "completed_tasks": [],
            "failed_tasks": [],
            "memory_text": "",
            "long_term_notes": [],
        }

    def _load_session_memory(self) -> None:
        """Load today's natural-language memory snapshot if it exists."""
        if not self.memory_file.exists():
            self.session["memory_text"] = self._compose_memory_text()
            self.session["long_term_notes"] = self._load_long_term_notes()
            return

        try:
            with open(self.memory_file, "r", encoding="utf-8") as file_handle:
                memory_text = file_handle.read().strip()

            self.session["memory_text"] = memory_text
            self.session["memory_notes"] = self._extract_notes(memory_text)
            self.session["long_term_notes"] = self._load_long_term_notes()
            logger.debug(f"Loaded memory snapshot from {self.memory_file}")
        except Exception as error:
            logger.warning(f"Failed to load memory file: {error}")
            self.session["memory_text"] = self._compose_memory_text()
            self.session["long_term_notes"] = self._load_long_term_notes()

    def _load_long_term_notes(self) -> List[str]:
        """Load durable natural-language memory notes."""
        if not self.long_term_memory_file.exists():
            return []

        try:
            with open(self.long_term_memory_file, "r", encoding="utf-8") as file_handle:
                memory_text = file_handle.read()
        except Exception as error:
            logger.warning(f"Failed to load long-term memory file: {error}")
            return []

        notes: List[str] = []
        in_notes_section = False
        for raw_line in memory_text.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                in_notes_section = line == "## 残すこと"
                continue

            if in_notes_section and line.startswith("- "):
                note = line[2:].strip()
                if note != "まだ長期的に残すことはない。":
                    notes.append(note)

        return notes[-30:]

    def _compose_long_term_memory_text(self) -> str:
        """Build the long-term memory snapshot for disk."""
        notes = self.session.get("long_term_notes", [])[-30:]
        lines = [
            f"# {self.session.get('agent_name', AGENT_NAME)} の長期記憶",
            "ここには、日々の記憶を消す前に残す価値があると判断したことだけを保存する。",
            "",
            "## 残すこと",
        ]

        if notes:
            lines.extend(f"- {note}" for note in notes)
        else:
            lines.append("- まだ長期的に残すことはない。")

        return "\n".join(lines).strip() + "\n"

    def _extract_notes(self, memory_text: str) -> List[str]:
        """Extract short notes from the saved text format."""
        notes: List[str] = []
        in_notes_section = False

        for raw_line in memory_text.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                in_notes_section = line == "## 今日のメモ"
                continue

            if in_notes_section and line.startswith("- "):
                notes.append(line[2:].strip())

        return notes[-8:]

    def _compose_memory_text(self) -> str:
        """Build the plain-text memory snapshot for disk."""
        stats = self.get_daily_stats()
        recent_notes = self.session.get("memory_notes", [])[-5:]

        if stats.get("tasks_executed", 0) == 0 and not recent_notes:
            summary = "今日はまだ静かに様子を見ている。"
        else:
            summary = (
                f"今日は {stats.get('tasks_generated', 0)} 件の候補を考え、"
                f"{stats.get('tasks_executed', 0)} 件を実行し、"
                f"{stats.get('tasks_completed', 0)} 件完了、"
                f"{stats.get('tasks_failed', 0)} 件失敗した。"
            )

        lines = [
            f"# {self.session.get('agent_name', AGENT_NAME)} の今日の記憶",
            f"日付: {self.session.get('date', 'Unknown')}",
            f"ひとこと: {summary}",
            "",
            "## 今日のメモ",
        ]

        if recent_notes:
            lines.extend(f"- {note}" for note in recent_notes)
        else:
            lines.append("- まだ特筆すべきことは少ない。")

        return "\n".join(lines).strip() + "\n"

    def save_memory(self) -> bool:
        """Save the plain-text memory snapshot to disk."""
        try:
            self.session["memory_text"] = self._compose_memory_text()
            with open(self.memory_file, "w", encoding="utf-8") as file_handle:
                file_handle.write(self.session["memory_text"])
            logger.debug("Memory snapshot saved successfully")
            return True
        except Exception as error:
            logger.error(f"Failed to save memory: {error}")
            return False

    def save_long_term_memory(self) -> bool:
        """Save durable memory notes to disk."""
        try:
            with open(self.long_term_memory_file, "w", encoding="utf-8") as file_handle:
                file_handle.write(self._compose_long_term_memory_text())
            logger.debug("Long-term memory saved successfully")
            return True
        except Exception as error:
            logger.error(f"Failed to save long-term memory: {error}")
            return False

    def add_long_term_memory(self, note: str) -> bool:
        """Add a durable natural-language memory note."""
        try:
            cleaned_note = " ".join(note.strip().split())
            if not cleaned_note or cleaned_note.upper() == "NONE":
                return True

            self.session["long_term_notes"] = self._load_long_term_notes()
            self.session["long_term_notes"].append(cleaned_note)
            self.session["long_term_notes"] = self.session["long_term_notes"][-30:]
            return self.save_long_term_memory()
        except Exception as error:
            logger.error(f"Failed to add long-term memory: {error}")
            return False

    def log_task_execution(self, task: Dict[str, Any], memory_note: str = "") -> bool:
        """Log a task execution and keep a brief AI-written note."""
        try:
            task_entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "hour": datetime.utcnow().hour,
                **task,
            }

            self.session["execution_history"].append(task_entry)
            self.session["daily_stats"]["tasks_executed"] += 1

            if task.get("status") == "completed":
                self.session["daily_stats"]["tasks_completed"] += 1
                self.session["completed_tasks"].append(task_entry)
            elif task.get("status") == "failed":
                self.session["daily_stats"]["tasks_failed"] += 1
                self.session["failed_tasks"].append(task_entry)

            if "duration_ms" in task:
                self.session["daily_stats"]["total_execution_time_ms"] += task["duration_ms"]

            if memory_note.strip():
                self.session["memory_notes"].append(memory_note.strip())
                self.session["memory_notes"] = self.session["memory_notes"][-8:]

            return self.save_memory()
        except Exception as error:
            logger.error(f"Failed to log task: {error}")
            return False

    def record_api_call(self) -> None:
        """Record an API call to memory."""
        self.session["daily_stats"]["total_api_calls"] += 1

    def add_insight(self, insight: str) -> bool:
        """Add a short insight or observation written by the AI."""
        try:
            cleaned_insight = " ".join(insight.strip().split())
            if not cleaned_insight:
                return True

            insight_entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "hour": datetime.utcnow().hour,
                "content": cleaned_insight,
            }
            self.session["today_insights"].append(insight_entry)
            self.session["memory_notes"].append(cleaned_insight)
            self.session["memory_notes"] = self.session["memory_notes"][-8:]
            return self.save_memory()
        except Exception as error:
            logger.error(f"Failed to add insight: {error}")
            return False

    def update_task_generation_count(self, count: int) -> None:
        """Update number of tasks generated."""
        self.session["daily_stats"]["tasks_generated"] += count

    def get_execution_history(self) -> List[Dict[str, Any]]:
        """Get today's execution history."""
        return self.session.get("execution_history", [])

    def get_daily_stats(self) -> Dict[str, Any]:
        """Get today's statistics."""
        return self.session.get("daily_stats", {})

    def get_insights(self) -> List[Dict[str, Any]]:
        """Get today's insights."""
        return self.session.get("today_insights", [])

    def add_user_preference(self, key: str, value: Any) -> bool:
        """Store user preference."""
        try:
            self.session["user_preferences"][key] = {
                "value": value,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            return self.save_memory()
        except Exception as error:
            logger.error(f"Failed to add preference: {error}")
            return False

    def get_memory_context(self) -> str:
        """Generate a compact natural-language context for the agent."""
        stats = self.get_daily_stats()
        notes = self.session.get("memory_notes", [])[-5:]

        context_lines = [
            "## 今日の状況",
            f"- 日付: {self.session.get('date', 'Unknown')}",
            f"- 今日の候補数: {stats.get('tasks_generated', 0)}",
            f"- 実行数: {stats.get('tasks_executed', 0)}",
            f"- 完了: {stats.get('tasks_completed', 0)} / 失敗: {stats.get('tasks_failed', 0)}",
            "",
            "## ひとことメモ",
        ]

        if notes:
            context_lines.extend(f"- {note}" for note in notes)
        else:
            context_lines.append("- まだ少ないので、静かに様子を見る。")

        long_term_notes = self.session.get("long_term_notes") or self._load_long_term_notes()
        if long_term_notes:
            context_lines.extend(["", "## 長期記憶"])
            context_lines.extend(f"- {note}" for note in long_term_notes[-5:])

        return "\n".join(context_lines)

    def reset_daily_memory(self, long_term_note: str = "") -> bool:
        """Reset memory for a new day."""
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            archive_date = self.session.get("date") or today

            if self.memory_file.exists():
                archive_filename = self.archive_dir / f"memory_{archive_date}.md"
                with open(self.memory_file, "r", encoding="utf-8") as file_handle:
                    old_memory = file_handle.read()

                with open(archive_filename, "w", encoding="utf-8") as file_handle:
                    file_handle.write(old_memory)
                logger.info(f"Archived previous day memory to {archive_filename}")

            if long_term_note.strip() and long_term_note.strip().upper() != "NONE":
                self.add_long_term_memory(long_term_note)

            long_term_notes = self._load_long_term_notes()
            self.session = self._create_fresh_memory()
            self.session["long_term_notes"] = long_term_notes
            self.save_memory()
            logger.info("Daily memory reset successfully")

            self._cleanup_old_archives()
            return True
        except Exception as error:
            logger.error(f"Failed to reset daily memory: {error}")
            return False

    def _cleanup_old_archives(self) -> None:
        """Remove memory archives older than configured days."""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=MEMORY_DELETE_DAYS)

            for archive_file in self.archive_dir.glob("memory_*.md"):
                try:
                    file_stat = archive_file.stat()
                    file_time = datetime.fromtimestamp(file_stat.st_mtime)

                    if file_time < cutoff_date:
                        archive_file.unlink()
                        logger.debug(f"Deleted old archive: {archive_file.name}")
                except Exception as error:
                    logger.debug(f"Failed to cleanup archive {archive_file.name}: {error}")
        except Exception as error:
            logger.error(f"Error during archive cleanup: {error}")

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get overall memory statistics."""
        return {
            "memory_file_size": self.memory_file.stat().st_size if self.memory_file.exists() else 0,
            "archive_count": len(list(self.archive_dir.glob("memory_*.md"))),
            "long_term_memory_file_size": (
                self.long_term_memory_file.stat().st_size if self.long_term_memory_file.exists() else 0
            ),
            "daily_stats": self.get_daily_stats(),
            "total_tasks_today": len(self.session.get("execution_history", [])),
        }
