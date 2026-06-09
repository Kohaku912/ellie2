"""
Lightweight memory manager for the autonomous agent.
Stores today's memory as short natural-language notes instead of JSON.
"""
import logging
import re
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
from agent.time_utils import date_str_local, hour_local, isoformat_local, now_local

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages today's natural-language memory for the agent."""

    def __init__(self):
        self.memory_file = MEMORY_FILE
        self.long_term_memory_file = LONG_TERM_MEMORY_FILE
        self.task_log_file = TASK_LOG_FILE
        self.memory_dir = MEMORY_DIR
        self.archive_dir = ARCHIVE_DIR

        self.session = self._create_fresh_memory()
        self._load_session_memory()

    def _create_fresh_memory(self) -> Dict[str, Any]:
        """Create fresh in-memory state for the current day."""
        return {
            "date": date_str_local(),
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
                self._append_unique_note(notes, line[2:].strip(), max_notes=8)

        return notes[-8:]

    def _normalize_note_key(self, note: str) -> str:
        """Create a stable key for duplicate short-memory notes."""
        normalized = " ".join(note.strip().split()).casefold()
        normalized = re.sub(r"\d{4}-\d{2}-\d{2}(?:t|\s)\d{2}:\d{2}:\d{2}(?:\.\d+)?z?", "", normalized)
        normalized = re.sub(r"\d{2}:\d{2}(?::\d{2})?", "", normalized)
        return normalized.strip(" 。、,.")

    def _append_unique_note(self, notes: List[str], note: str, max_notes: int = 8) -> bool:
        """Append a note only when it is not already present."""
        cleaned_note = " ".join(note.strip().split())
        if not cleaned_note:
            return False

        existing_keys = {self._normalize_note_key(existing) for existing in notes}
        note_key = self._normalize_note_key(cleaned_note)
        if not note_key or note_key in existing_keys:
            return False

        notes.append(cleaned_note)
        del notes[:-max_notes]
        return True

    def _compose_memory_text(self) -> str:
        """Build the plain-text memory snapshot for disk."""
        stats = self.get_daily_stats()
        recent_notes = self.session.get("memory_notes", [])[-5:]

        if stats.get("tasks_executed", 0) == 0 and not recent_notes:
            summary = "今日はまだ静かに様子を見ている。"
        else:
            summary = (
                f"今日は {stats.get('tasks_generated', 0)} 回の自律判断を行い、"
                f"{stats.get('tasks_executed', 0)} 回ツールを使い、"
                f"{stats.get('tasks_completed', 0)} 回成功し、"
                f"{stats.get('tasks_failed', 0)} 回失敗した。"
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
                "timestamp": isoformat_local(),
                "hour": hour_local(),
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

            if memory_note.strip() and memory_note.strip().upper() != "NONE":
                self._append_unique_note(self.session["memory_notes"], memory_note, max_notes=8)

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
            if not cleaned_insight or cleaned_insight.upper() == "NONE":
                return True

            if not self.should_store_memory_note(cleaned_insight):
                return True

            added = self._append_unique_note(self.session["memory_notes"], cleaned_insight, max_notes=8)
            if not added:
                return True

            insight_entry = {
                "timestamp": isoformat_local(),
                "hour": hour_local(),
                "content": cleaned_insight,
            }
            self.session["today_insights"].append(insight_entry)
            return self.save_memory()
        except Exception as error:
            logger.error(f"Failed to add insight: {error}")
            return False

    def should_store_memory_note(self, note: str) -> bool:
        """Return whether a memory note adds new information worth storing."""
        cleaned_note = " ".join(note.strip().split())
        if not cleaned_note or cleaned_note.upper() == "NONE":
            return False

        candidate_key = self._normalize_note_key(cleaned_note)
        if not candidate_key:
            return False

        existing_notes = [
            *self.session.get("memory_notes", []),
            *self.session.get("long_term_notes", []),
        ]
        for existing_note in existing_notes:
            if candidate_key == self._normalize_note_key(existing_note):
                return False

        return True

    def update_task_generation_count(self, count: int) -> None:
        """Update autonomous run count."""
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
            existing_preference = self.session.get("user_preferences", {}).get(key)
            if isinstance(existing_preference, dict) and existing_preference.get("value") == value:
                return True

            self.session["user_preferences"][key] = {
                "value": value,
                "timestamp": isoformat_local(),
            }
            return self.save_memory()
        except Exception as error:
            logger.error(f"Failed to add preference: {error}")
            return False

    def remember_discord_voice_target(
        self,
        guild_id: str = "",
        guild_name: str = "",
        channel_id: str = "",
        channel_name: str = "",
    ) -> bool:
        """Save the latest Discord voice target into today's memory as a short note."""
        guild_id = self._normalize_optional_id(guild_id)
        channel_id = self._normalize_optional_id(channel_id)
        if not guild_id and not channel_id:
            return False

        note_parts = ["Discord通話先を覚えた"]
        if guild_name.strip():
            note_parts.append(f"guild={guild_name.strip()}")
        if channel_name.strip():
            note_parts.append(f"channel={channel_name.strip()}")
        if guild_id:
            note_parts.append(f"guild_id={guild_id}")
        if channel_id:
            note_parts.append(f"channel_id={channel_id}")

        note = ": ".join([note_parts[0], ", ".join(note_parts[1:])]) if len(note_parts) > 1 else note_parts[0]
        self.add_user_preference(
            "discord_voice_target",
            {
                "guild_id": guild_id,
                "guild_name": guild_name.strip(),
                "channel_id": channel_id,
                "channel_name": channel_name.strip(),
            },
        )
        return self.add_insight(note)

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        """Get a stored user preference value."""
        preference = self.session.get("user_preferences", {}).get(key)
        if isinstance(preference, dict):
            return preference.get("value", default)
        return default

    def get_discord_voice_target(self) -> Dict[str, str]:
        """Return the last known Discord voice target from memory if available."""
        stored_target = self.get_user_preference("discord_voice_target", {})
        if isinstance(stored_target, dict):
            guild_id = self._normalize_optional_id(stored_target.get("guild_id"))
            channel_id = self._normalize_optional_id(stored_target.get("channel_id"))
            if guild_id or channel_id:
                return {
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "guild_name": str(stored_target.get("guild_name") or "").strip(),
                    "channel_name": str(stored_target.get("channel_name") or "").strip(),
                    "source": "user_preference",
                }

        for note in reversed(self.session.get("memory_notes", [])):
            target = self._extract_discord_voice_target_from_text(note)
            if target:
                target["source"] = "memory_note"
                return target

        for note in reversed(self.session.get("long_term_notes", [])):
            target = self._extract_discord_voice_target_from_text(note)
            if target:
                target["source"] = "long_term_memory"
                return target

        return {}

    def _extract_discord_voice_target_from_text(self, text: str) -> Dict[str, str]:
        """Best-effort parse of guild/channel ids from natural-language memory text."""
        if not text:
            return {}

        guild_id_match = re.search(r"guild_id[\"'\s:=：]+([0-9A-Za-z_-]+)", text, re.IGNORECASE)
        channel_id_match = re.search(r"channel_id[\"'\s:=：]+([0-9A-Za-z_-]+)", text, re.IGNORECASE)
        if not guild_id_match and not channel_id_match:
            return {}

        return {
            "guild_id": self._normalize_optional_id(guild_id_match.group(1) if guild_id_match else ""),
            "channel_id": self._normalize_optional_id(channel_id_match.group(1) if channel_id_match else ""),
            "guild_name": "",
            "channel_name": "",
        }

    def _normalize_optional_id(self, value: Any) -> str:
        """Treat null-like strings as missing IDs."""
        text = str(value or "").strip()
        if text.lower() in {"null", "none", "undefined", "nil"}:
            return ""
        return text

    def get_memory_context(self) -> str:
        """Generate a compact natural-language context for the agent."""
        stats = self.get_daily_stats()
        notes = self.session.get("memory_notes", [])[-5:]

        context_lines = [
            "## 今日の状況",
            f"- 日付: {self.session.get('date', 'Unknown')}",
            f"- 今日の自律判断数: {stats.get('tasks_generated', 0)}",
            f"- ツール実行数: {stats.get('tasks_executed', 0)}",
            f"- 成功: {stats.get('tasks_completed', 0)} / 失敗: {stats.get('tasks_failed', 0)}",
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

    def get_tool_memory_context(self) -> str:
        """Generate a concise memory summary for tool selection and argument filling."""
        stats = self.get_daily_stats()
        notes = self.session.get("memory_notes", [])[-4:]
        history = self.session.get("execution_history", [])[-3:]

        lines = [
            "## 今日の記憶",
            f"- 日付: {self.session.get('date', 'Unknown')}",
            f"- 自律判断数: {stats.get('tasks_generated', 0)}",
            f"- ツール実行数: {stats.get('tasks_executed', 0)}",
        ]

        if notes:
            lines.append("- 直近の記憶:")
            lines.extend(f"  - {note}" for note in notes)

        if history:
            lines.append("- 直近の実行:")
            for entry in history:
                title = str(entry.get("title", "unknown"))
                status = str(entry.get("status", "unknown"))
                duration_ms = entry.get("duration_ms")
                duration_text = f"{duration_ms}ms" if isinstance(duration_ms, int) else "unknown"
                lines.append(f"  - {title} / {status} / {duration_text}")

        long_term_notes = self.session.get("long_term_notes") or self._load_long_term_notes()
        if long_term_notes:
            lines.append("- 長期記憶:")
            lines.extend(f"  - {note}" for note in long_term_notes[-3:])

        return "\n".join(lines)

    def reset_daily_memory(self, long_term_note: str = "") -> bool:
        """Reset memory for a new day."""
        try:
            today = date_str_local()
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
            cutoff_date = now_local().replace(tzinfo=None) - timedelta(days=MEMORY_DELETE_DAYS)

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
