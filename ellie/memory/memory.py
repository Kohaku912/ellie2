"""SQLite-backed memory manager for Ellie."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ellie.config import AGENT_NAME, ARCHIVE_DIR, LONG_TERM_MEMORY_FILE, MEMORY_DB_FILE, MEMORY_DELETE_DAYS, MEMORY_DIR, MEMORY_FILE, TASK_LOG_FILE
from ellie.time_utils import date_str_local, hour_local, isoformat_local, now_local

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
EMBEDDING_DIMENSION = 384
SEARCH_RESULTS_TOP_K = 7


class _EmbedderProtocol:
    def encode(self, texts: Sequence[str], *, normalize_embeddings: bool = False) -> Any:  # pragma: no cover - protocol
        raise NotImplementedError


class _FallbackEmbedder:
    """Deterministic hashed bag-of-words embedder used when the real model is unavailable."""

    def __init__(self, dimension: int = EMBEDDING_DIMENSION):
        self.dimension = dimension

    def encode(self, texts: Sequence[str], *, normalize_embeddings: bool = False) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in _tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimension
                weight = 1.0 + (int.from_bytes(digest[4:8], "little") % 7) / 10.0
                vector[index] += weight
            if normalize_embeddings:
                vector = _normalize_vector(vector)
            vectors.append(vector)
        return vectors


class _SentenceTransformersEmbedder:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str], *, normalize_embeddings: bool = False) -> Any:
        return self.model.encode(list(texts), normalize_embeddings=normalize_embeddings)


_GLOBAL_EMBEDDER: _EmbedderProtocol | None = None
_GLOBAL_EMBEDDER_LOCK = threading.Lock()


def _get_embedder() -> _EmbedderProtocol:
    global _GLOBAL_EMBEDDER
    if _GLOBAL_EMBEDDER is not None:
        return _GLOBAL_EMBEDDER
    with _GLOBAL_EMBEDDER_LOCK:
        if _GLOBAL_EMBEDDER is not None:
            return _GLOBAL_EMBEDDER
        try:
            _GLOBAL_EMBEDDER = _SentenceTransformersEmbedder()
        except Exception as error:
            logger.warning("Falling back to hashed memory embedder: %s", error)
            _GLOBAL_EMBEDDER = _FallbackEmbedder()
        return _GLOBAL_EMBEDDER


class MemoryManager:
    """SQLite-backed memory, preferences, and execution history manager."""

    def __init__(
        self,
        db_file: Path = MEMORY_DB_FILE,
        *,
        embedder: _EmbedderProtocol | None = None,
        import_legacy: bool = True,
    ):
        self.db_file = Path(db_file)
        self.memory_file = MEMORY_FILE
        self.long_term_memory_file = LONG_TERM_MEMORY_FILE
        self.task_log_file = TASK_LOG_FILE
        self.memory_dir = MEMORY_DIR
        self.archive_dir = ARCHIVE_DIR
        self.embedder = embedder or _get_embedder()
        self._lock = threading.RLock()
        self._ensure_storage()
        self.session = self._create_fresh_memory()
        if import_legacy:
            self._import_legacy_memory_files()
        self.session = self._load_session()

    # ------------------------------------------------------------------
    # Schema / persistence
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_file), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_storage(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'memory',
                    content TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    emotion TEXT NOT NULL DEFAULT '',
                    importance REAL NOT NULL DEFAULT 0.5,
                    decay_score REAL NOT NULL DEFAULT 1.0,
                    embedding_json TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
                CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
                CREATE INDEX IF NOT EXISTS idx_memories_normalized_content ON memories(normalized_content);

                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    tasks_generated INTEGER NOT NULL DEFAULT 0,
                    tasks_executed INTEGER NOT NULL DEFAULT 0,
                    tasks_completed INTEGER NOT NULL DEFAULT 0,
                    tasks_failed INTEGER NOT NULL DEFAULT 0,
                    total_execution_time_ms INTEGER NOT NULL DEFAULT 0,
                    total_api_calls INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS execution_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    hour TEXT NOT NULL,
                    entry_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    hour TEXT NOT NULL,
                    content TEXT NOT NULL,
                    memory_id INTEGER,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS long_term_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note TEXT NOT NULL,
                    normalized_note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _import_legacy_memory_files(self) -> None:
        with self._connect() as conn:
            imported = conn.execute("SELECT value FROM metadata WHERE key = 'legacy_import_v1'").fetchone()
            if imported and str(imported["value"]) == "done":
                return

            if self.memory_file.exists():
                try:
                    self._import_legacy_daily_memory(self.memory_file.read_text(encoding="utf-8", errors="replace"))
                except Exception as error:
                    logger.warning("Failed to import legacy memory file: %s", error)

            if self.long_term_memory_file.exists():
                try:
                    self._import_legacy_long_term_memory(self.long_term_memory_file.read_text(encoding="utf-8", errors="replace"))
                except Exception as error:
                    logger.warning("Failed to import legacy long-term memory file: %s", error)

            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('legacy_import_v1', 'done')")
            conn.commit()

    def _import_legacy_daily_memory(self, text: str) -> None:
        notes = self._extract_notes(text, section_title="今日のメモ")
        summary_match = re.search(r"^ひとこと:\s*(.+)$", text, re.MULTILINE)
        summary = summary_match.group(1).strip() if summary_match else ""
        if summary and summary != "NONE":
            self.remember(summary, emotion="", importance=0.55, source="legacy_import")
        for note in notes:
            self.remember(note, emotion="", importance=0.45, source="legacy_import")

    def _import_legacy_long_term_memory(self, text: str) -> None:
        notes = self._extract_notes(text, section_title="残すこと")
        for note in notes:
            self.add_long_term_memory(note, source="legacy_import")

    def _load_session(self) -> Dict[str, Any]:
        today = date_str_local()
        session = self._create_fresh_memory()
        session["date"] = today
        session["daily_stats"] = self._load_daily_stats(today)
        session["execution_history"] = self._load_execution_history(today)
        session["today_insights"] = self._load_today_insights(today)
        session["user_preferences"] = self._load_user_preferences()
        session["memory_notes"] = self._load_recent_memories(kind="memory", limit=8)
        session["long_term_notes"] = self._load_long_term_notes()
        session["completed_tasks"] = [entry for entry in session["execution_history"] if entry.get("status") == "completed"]
        session["failed_tasks"] = [entry for entry in session["execution_history"] if entry.get("status") == "failed"]
        session["memory_text"] = self._compose_memory_text(session)
        return session

    def refresh(self) -> None:
        with self._lock:
            self.session = self._load_session()

    # ------------------------------------------------------------------
    # Core memory API
    # ------------------------------------------------------------------
    def remember(self, content: str, emotion: str = "", importance: float = 0.5, *, source: str = "manual") -> bool:
        text = self._clean_text(content)
        if not text:
            return False
        importance_value = self._clamp(importance)
        normalized_content = self._normalize_text(text)
        created_at = isoformat_local()
        embedding = self._embed_text(text, emotion=emotion)
        decay_score = self._compute_decay_score(created_at, importance_value)
        payload = {
            "kind": "memory",
            "content": text,
            "normalized_content": normalized_content,
            "emotion": self._clean_text(emotion),
            "importance": importance_value,
            "decay_score": decay_score,
            "embedding_json": json.dumps(embedding, ensure_ascii=False),
            "source": source,
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories(kind, content, normalized_content, emotion, importance, decay_score, embedding_json, source, created_at, updated_at)
                VALUES(:kind, :content, :normalized_content, :emotion, :importance, :decay_score, :embedding_json, :source, :created_at, :updated_at)
                """,
                payload,
            )
            conn.commit()
        self.session["memory_notes"] = self._load_recent_memories(kind="memory", limit=8)
        self.session["memory_text"] = self._compose_memory_text(self.session)
        return True

    def search_memories(self, query: str, top_k: int = SEARCH_RESULTS_TOP_K) -> List[Dict[str, Any]]:
        query_text = self._clean_text(query)
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, content, normalized_content, emotion, importance, decay_score, embedding_json, source, created_at, updated_at
                FROM memories
                ORDER BY datetime(created_at) DESC, id DESC
                """
            ).fetchall()

        if not rows:
            return []

        query_embedding = self._embed_text(query_text or "memory", as_query=True)
        scored: List[Dict[str, Any]] = []
        now = now_local()
        for row in rows:
            embedding = self._decode_embedding(row["embedding_json"])
            similarity = _cosine_similarity(query_embedding, embedding)
            created_at = self._parse_time(row["created_at"])
            importance = float(row["importance"])
            live_decay = self._compute_decay_score(created_at, importance, now=now)
            row_decay = float(row["decay_score"])
            combined_decay = (live_decay + row_decay) / 2.0
            score = (similarity * 0.72) + (importance * 0.18) + (combined_decay * 0.10)
            scored.append(
                {
                    "id": int(row["id"]),
                    "kind": str(row["kind"]),
                    "content": str(row["content"]),
                    "emotion": str(row["emotion"]),
                    "importance": round(importance, 6),
                    "decay_score": round(combined_decay, 6),
                    "similarity": round(similarity, 6),
                    "score": round(score, 6),
                    "source": str(row["source"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
            )

        scored.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
        results = scored[:top_k]
        self._refresh_decay_scores([item["id"] for item in results])
        return results

    def get_relevant_memory_context(self, query: str, top_k: int = SEARCH_RESULTS_TOP_K) -> str:
        memories = self.search_memories(query, top_k=top_k)
        if not memories:
            return ""
        lines = ["## 関連記憶"]
        for memory in memories:
            parts = [memory["content"]]
            suffix_parts = [
                f"emotion={memory['emotion']}" if memory["emotion"] else "",
                f"importance={memory['importance']:.2f}",
                f"decay={memory['decay_score']:.2f}",
            ]
            suffix = ", ".join(part for part in suffix_parts if part)
            if suffix:
                parts.append(f"({suffix})")
            lines.append(f"- {''.join(parts)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Compatibility / legacy-style APIs
    # ------------------------------------------------------------------
    def save_memory(self) -> bool:
        self.session["memory_text"] = self._compose_memory_text(self.session)
        return True

    def save_long_term_memory(self) -> bool:
        return True

    def add_long_term_memory(self, note: str, *, source: str = "manual") -> bool:
        cleaned_note = self._clean_text(note)
        if not cleaned_note or cleaned_note.upper() == "NONE":
            return True
        normalized = self._normalize_text(cleaned_note)
        now_text = isoformat_local()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM long_term_notes WHERE normalized_note = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if existing:
                return True
            conn.execute(
                "INSERT INTO long_term_notes(note, normalized_note, created_at) VALUES(?, ?, ?)",
                (cleaned_note, normalized, now_text),
            )
            conn.commit()
        self.session["long_term_notes"] = self._load_long_term_notes()
        self.remember(cleaned_note, emotion="", importance=0.72, source=source)
        return True

    def log_task_execution(self, task: Dict[str, Any], memory_note: str = "") -> bool:
        try:
            task_entry = {"timestamp": isoformat_local(), "hour": hour_local(), **task}
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO execution_history(date, timestamp, hour, entry_json)
                    VALUES(?, ?, ?, ?)
                    """,
                    (date_str_local(), task_entry["timestamp"], task_entry["hour"], json.dumps(task_entry, ensure_ascii=False, default=str)),
                )
                stats = self._ensure_daily_stats(date_str_local(), conn)
                stats["tasks_executed"] += 1
                if task.get("status") == "completed":
                    stats["tasks_completed"] += 1
                elif task.get("status") == "failed":
                    stats["tasks_failed"] += 1
                if "duration_ms" in task:
                    try:
                        stats["total_execution_time_ms"] += int(task["duration_ms"])
                    except Exception:
                        pass
                self._save_daily_stats_row(conn, date_str_local(), stats)
                conn.commit()

            self.session = self._load_session()
            if memory_note.strip() and memory_note.strip().upper() != "NONE":
                self.remember(memory_note, emotion="", importance=0.56, source="task_execution")
            return True
        except Exception as error:
            logger.error("Failed to log task: %s", error, exc_info=True)
            return False

    def record_api_call(self) -> None:
        with self._lock, self._connect() as conn:
            stats = self._ensure_daily_stats(date_str_local(), conn)
            stats["total_api_calls"] += 1
            self._save_daily_stats_row(conn, date_str_local(), stats)
            conn.commit()
        self.session["daily_stats"] = self._load_daily_stats(date_str_local())

    def add_insight(self, insight: str) -> bool:
        cleaned_insight = self._clean_text(insight)
        if not cleaned_insight or cleaned_insight.upper() == "NONE":
            return True
        if not self.should_store_memory_note(cleaned_insight):
            return True
        now_text = isoformat_local()
        memory = self.search_memories(cleaned_insight, top_k=1)
        memory_id = memory[0]["id"] if memory else None
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO insights(date, timestamp, hour, content, memory_id)
                VALUES(?, ?, ?, ?, ?)
                """,
                (date_str_local(), now_text, hour_local(), cleaned_insight, memory_id),
            )
            conn.commit()
        self.remember(cleaned_insight, emotion="", importance=0.58, source="insight")
        self.session["today_insights"] = self._load_today_insights(date_str_local())
        self.session["memory_notes"] = self._load_recent_memories(kind="memory", limit=8)
        self.session["memory_text"] = self._compose_memory_text(self.session)
        return True

    def should_store_memory_note(self, note: str) -> bool:
        cleaned_note = self._clean_text(note)
        if not cleaned_note or cleaned_note.upper() == "NONE":
            return False
        normalized = self._normalize_text(cleaned_note)
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM memories WHERE normalized_content = ? LIMIT 1", (normalized,)).fetchone()
            if exists:
                return False
            exists = conn.execute("SELECT 1 FROM long_term_notes WHERE normalized_note = ? LIMIT 1", (normalized,)).fetchone()
            if exists:
                return False
        return True

    def update_task_generation_count(self, count: int) -> None:
        with self._lock, self._connect() as conn:
            stats = self._ensure_daily_stats(date_str_local(), conn)
            stats["tasks_generated"] += int(count)
            self._save_daily_stats_row(conn, date_str_local(), stats)
            conn.commit()
        self.session["daily_stats"] = self._load_daily_stats(date_str_local())

    def get_execution_history(self) -> List[Dict[str, Any]]:
        self.refresh()
        return list(self.session.get("execution_history", []))

    def get_daily_stats(self) -> Dict[str, Any]:
        self.refresh()
        return dict(self.session.get("daily_stats", {}))

    def get_insights(self) -> List[Dict[str, Any]]:
        self.refresh()
        return list(self.session.get("today_insights", []))

    def add_user_preference(self, key: str, value: Any) -> bool:
        cleaned_key = self._clean_text(key)
        if not cleaned_key:
            return False
        now_text = isoformat_local()
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences(key, value_json, timestamp)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, timestamp=excluded.timestamp
                """,
                (cleaned_key, payload, now_text),
            )
            conn.commit()
        self.session["user_preferences"] = self._load_user_preferences()
        return True

    def remember_discord_voice_target(
        self,
        guild_id: str = "",
        guild_name: str = "",
        channel_id: str = "",
        channel_name: str = "",
    ) -> bool:
        guild_id = self._normalize_optional_id(guild_id)
        channel_id = self._normalize_optional_id(channel_id)
        if not guild_id and not channel_id:
            return False

        target = {
            "guild_id": guild_id,
            "guild_name": guild_name.strip(),
            "channel_id": channel_id,
            "channel_name": channel_name.strip(),
        }
        self.add_user_preference("discord_voice_target", target)
        return self.add_insight(
            f"Discord通話先を覚えた: guild={target['guild_name']}, channel={target['channel_name']}, guild_id={guild_id}, channel_id={channel_id}"
        )

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        self.refresh()
        preference = self.session.get("user_preferences", {}).get(key)
        if isinstance(preference, dict):
            return preference.get("value", default)
        return default

    def get_discord_voice_target(self) -> Dict[str, str]:
        self.refresh()
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
            target = self._extract_discord_voice_target_from_text(str(note))
            if target:
                target["source"] = "memory_note"
                return target
        for note in reversed(self.session.get("long_term_notes", [])):
            target = self._extract_discord_voice_target_from_text(str(note))
            if target:
                target["source"] = "long_term_memory"
                return target
        return {}

    def get_memory_context(self) -> str:
        self.refresh()
        stats = self.get_daily_stats()
        notes = self._load_recent_memories(kind="memory", limit=5)
        lines = [
            "## 今日の状況",
            f"- 日付: {self.session.get('date', 'Unknown')}",
            f"- 今日の自律判断数: {stats.get('tasks_generated', 0)}",
            f"- ツール実行数: {stats.get('tasks_executed', 0)}",
            f"- 成功: {stats.get('tasks_completed', 0)} / 失敗: {stats.get('tasks_failed', 0)}",
            "",
            "## ひとことメモ",
        ]
        if notes:
            lines.extend(f"- {note['content']}" for note in notes)
        else:
            lines.append("- まだ少ないので、静かに様子を見る。")
        long_term_notes = self.session.get("long_term_notes") or self._load_long_term_notes()
        if long_term_notes:
            lines.extend(["", "## 長期記憶"])
            lines.extend(f"- {note}" for note in long_term_notes[-5:])
        return "\n".join(lines)

    def get_tool_memory_context(self) -> str:
        self.refresh()
        stats = self.get_daily_stats()
        notes = self._load_recent_memories(kind="memory", limit=4)
        history = self.get_execution_history()[-3:]
        lines = [
            "## 今日の記憶",
            f"- 日付: {self.session.get('date', 'Unknown')}",
            f"- 自律判断数: {stats.get('tasks_generated', 0)}",
            f"- ツール実行数: {stats.get('tasks_executed', 0)}",
        ]
        if notes:
            lines.append("- 直近の記憶:")
            lines.extend(f"  - {note['content']}" for note in notes)
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
        try:
            if long_term_note.strip() and long_term_note.strip().upper() != "NONE":
                self.add_long_term_memory(long_term_note, source="daily_reset")
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_stats(
                        date, tasks_generated, tasks_executed, tasks_completed, tasks_failed, total_execution_time_ms, total_api_calls
                    ) VALUES(?, 0, 0, 0, 0, 0, 0)
                    """,
                    (date_str_local(),),
                )
                conn.commit()
            self.session = self._load_session()
            self._cleanup_old_archives()
            return True
        except Exception as error:
            logger.error("Failed to reset daily memory: %s", error, exc_info=True)
            return False

    def get_memory_stats(self) -> Dict[str, Any]:
        self.refresh()
        with self._connect() as conn:
            memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
            long_term_count = conn.execute("SELECT COUNT(*) AS count FROM long_term_notes").fetchone()["count"]
            insight_count = conn.execute("SELECT COUNT(*) AS count FROM insights").fetchone()["count"]
        return {
            "memory_db_size": self.db_file.stat().st_size if self.db_file.exists() else 0,
            "archive_count": len(list(self.archive_dir.glob("memory_*.md"))),
            "daily_stats": self.get_daily_stats(),
            "total_tasks_today": len(self.session.get("execution_history", [])),
            "memory_count": int(memory_count),
            "long_term_note_count": int(long_term_count),
            "insight_count": int(insight_count),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _create_fresh_memory(self) -> Dict[str, Any]:
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

    def _load_daily_stats(self, day: str) -> Dict[str, Any]:
        with self._connect() as conn:
            stats = self._ensure_daily_stats(day, conn)
        return stats

    def _ensure_daily_stats(self, day: str, conn: sqlite3.Connection) -> Dict[str, int]:
        row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (day,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO daily_stats(date, tasks_generated, tasks_executed, tasks_completed, tasks_failed, total_execution_time_ms, total_api_calls)
                VALUES(?, 0, 0, 0, 0, 0, 0)
                """,
                (day,),
            )
            return {
                "tasks_generated": 0,
                "tasks_executed": 0,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_execution_time_ms": 0,
                "total_api_calls": 0,
            }
        return {
            "tasks_generated": int(row["tasks_generated"]),
            "tasks_executed": int(row["tasks_executed"]),
            "tasks_completed": int(row["tasks_completed"]),
            "tasks_failed": int(row["tasks_failed"]),
            "total_execution_time_ms": int(row["total_execution_time_ms"]),
            "total_api_calls": int(row["total_api_calls"]),
        }

    def _save_daily_stats_row(self, conn: sqlite3.Connection, day: str, stats: Dict[str, int]) -> None:
        conn.execute(
            """
            INSERT INTO daily_stats(date, tasks_generated, tasks_executed, tasks_completed, tasks_failed, total_execution_time_ms, total_api_calls)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                tasks_generated=excluded.tasks_generated,
                tasks_executed=excluded.tasks_executed,
                tasks_completed=excluded.tasks_completed,
                tasks_failed=excluded.tasks_failed,
                total_execution_time_ms=excluded.total_execution_time_ms,
                total_api_calls=excluded.total_api_calls
            """,
            (
                day,
                int(stats["tasks_generated"]),
                int(stats["tasks_executed"]),
                int(stats["tasks_completed"]),
                int(stats["tasks_failed"]),
                int(stats["total_execution_time_ms"]),
                int(stats["total_api_calls"]),
            ),
        )

    def _load_execution_history(self, day: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT entry_json FROM execution_history WHERE date = ? ORDER BY id ASC", (day,)).fetchall()
        history: List[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["entry_json"])
            except Exception:
                continue
            if isinstance(payload, dict):
                history.append(payload)
        return history

    def _load_today_insights(self, day: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, hour, content FROM insights WHERE date = ? ORDER BY id ASC",
                (day,),
            ).fetchall()
        return [{"timestamp": row["timestamp"], "hour": row["hour"], "content": row["content"]} for row in rows]

    def _load_user_preferences(self) -> Dict[str, Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value_json, timestamp FROM user_preferences ORDER BY key ASC").fetchall()
        preferences: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            try:
                value = json.loads(row["value_json"])
            except Exception:
                value = row["value_json"]
            preferences[str(row["key"])] = {"value": value, "timestamp": row["timestamp"]}
        return preferences

    def _load_long_term_notes(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT note FROM long_term_notes ORDER BY id ASC").fetchall()
        return [str(row["note"]) for row in rows][-30:]

    def _load_recent_memories(self, *, kind: str, limit: int) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, content, emotion, importance, decay_score, source, created_at, updated_at
                FROM memories
                WHERE kind = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (kind, int(limit)),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "kind": str(row["kind"]),
                "content": str(row["content"]),
                "emotion": str(row["emotion"]),
                "importance": float(row["importance"]),
                "decay_score": float(row["decay_score"]),
                "source": str(row["source"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def _refresh_decay_scores(self, memory_ids: Sequence[int]) -> None:
        if not memory_ids:
            return
        now = now_local()
        with self._lock, self._connect() as conn:
            for memory_id in memory_ids:
                row = conn.execute("SELECT created_at, importance FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
                if row is None:
                    continue
                decay_score = self._compute_decay_score(row["created_at"], float(row["importance"]), now=now)
                conn.execute(
                    "UPDATE memories SET decay_score = ?, updated_at = ? WHERE id = ?",
                    (decay_score, isoformat_local(), int(memory_id)),
                )
            conn.commit()

    def _compose_memory_text(self, session: Dict[str, Any]) -> str:
        stats = session.get("daily_stats", {})
        recent_notes = session.get("memory_notes", [])[-5:]
        summary = (
            "今日はまだ静かに様子を見ている。"
            if stats.get("tasks_executed", 0) == 0 and not recent_notes
            else (
                f"今日は {stats.get('tasks_generated', 0)} 回の自律判断を行い、"
                f"{stats.get('tasks_executed', 0)} 回ツールを使い、"
                f"{stats.get('tasks_completed', 0)} 回成功し、"
                f"{stats.get('tasks_failed', 0)} 回失敗した。"
            )
        )
        lines = [
            f"# {session.get('agent_name', AGENT_NAME)} の今日の記憶",
            f"日付: {session.get('date', 'Unknown')}",
            f"ひとこと: {summary}",
            "",
            "## 今日のメモ",
        ]
        if recent_notes:
            lines.extend(f"- {note['content']}" for note in recent_notes)
        else:
            lines.append("- まだ特筆すべきことは少ない。")
        return "\n".join(lines).strip() + "\n"

    def _extract_notes(self, memory_text: str, *, section_title: str) -> List[str]:
        notes: List[str] = []
        in_notes_section = False
        for raw_line in memory_text.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                in_notes_section = line == f"## {section_title}"
                continue
            if in_notes_section and line.startswith("- "):
                cleaned = self._clean_text(line[2:])
                if cleaned and cleaned != "まだ長期的に残すことはない。":
                    notes.append(cleaned)
        return notes[-30:]

    def _embed_text(self, text: str, *, emotion: str = "", as_query: bool = False) -> List[float]:
        payload = self._compose_embedding_text(text, emotion=emotion, as_query=as_query)
        raw = self.embedder.encode([payload], normalize_embeddings=True)
        try:
            import numpy as np

            array = np.asarray(raw, dtype=float)
            if array.ndim == 0:
                return [float(array.item())]
            if array.ndim == 1:
                return [float(value) for value in array.tolist()]
            if array.shape[0] == 0:
                return []
            return [float(value) for value in np.asarray(array[0], dtype=float).reshape(-1).tolist()]
        except Exception:
            if isinstance(raw, Sequence) and raw:
                first_item = raw[0]
                if isinstance(first_item, (int, float)):
                    vector = raw
                else:
                    vector = first_item
            else:
                vector = raw
            return [float(value) for value in vector]

    def _compose_embedding_text(self, text: str, *, emotion: str, as_query: bool) -> str:
        prefix = "query: " if as_query else "passage: "
        if emotion.strip():
            return f"{prefix}{emotion.strip()} {text.strip()}"
        return f"{prefix}{text.strip()}"

    def _decode_embedding(self, value: str) -> List[float]:
        try:
            payload = json.loads(value)
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [float(item) for item in payload]

    def _compute_decay_score(self, created_at: str | datetime, importance: float, *, now: datetime | None = None) -> float:
        current_time = now or now_local()
        created = self._parse_time(created_at) if isinstance(created_at, str) else created_at
        if created is None:
            return 1.0
        age_hours = max((current_time - created).total_seconds() / 3600.0, 0.0)
        half_life_hours = 18.0 + (importance * 96.0)
        return _clamp_float(math.exp(-age_hours / max(half_life_hours, 1e-6)))

    def _normalize_optional_id(self, value: Any) -> str:
        text = str(value or "").strip()
        if text.lower() in {"null", "none", "undefined", "nil"}:
            return ""
        return text

    def _extract_discord_voice_target_from_text(self, text: str) -> Dict[str, str]:
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

    def _cleanup_old_archives(self) -> None:
        try:
            cutoff_date = now_local().replace(tzinfo=None) - timedelta(days=MEMORY_DELETE_DAYS)
            for archive_file in self.archive_dir.glob("memory_*.md"):
                try:
                    file_time = datetime.fromtimestamp(archive_file.stat().st_mtime)
                    if file_time < cutoff_date:
                        archive_file.unlink()
                except Exception as error:
                    logger.debug("Failed to cleanup archive %s: %s", archive_file.name, error)
        except Exception as error:
            logger.error("Error during archive cleanup: %s", error)

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = " ".join(text.strip().split()).casefold()
        normalized = re.sub(r"\d{4}-\d{2}-\d{2}(?:t|\s)\d{2}:\d{2}:\d{2}(?:\.\d+)?z?", "", normalized)
        normalized = re.sub(r"\d{2}:\d{2}(?::\d{2})?", "", normalized)
        return normalized.strip(" 。、,.")

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    @staticmethod
    def _parse_time(value: str | datetime) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(now_local().tzinfo)
        except Exception:
            return None

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))


def _normalize_vector(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm <= 0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff_]+", text.casefold())


def _clamp_float(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
