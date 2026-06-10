"""Agent-level file operation and search tools (no browser dependency)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict

from ellie.config import BASE_DIR
from ellie.tools._utils import (
    append_note,
    is_sensitive_path,
    read_text,
    resolve_project_path,
    validate_written_file,
    write_backup,
)

logger = logging.getLogger(__name__)
JsonDict = Dict[str, Any]

SELF_DEVELOPMENT_NOTE = BASE_DIR / "agent_data" / "self_development.md"


def agent_read_file(arguments: JsonDict) -> JsonDict:
    path_text = str(arguments.get("path") or "").strip()
    if not path_text:
        return {"status": "failed", "tool": "agent_read_file", "error": "path is required"}
    target = resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_read_file", "error": "path is outside project root", "path": path_text}
    if not target.exists() or not target.is_file():
        return {"status": "failed", "tool": "agent_read_file", "error": "file not found", "path": str(target)}
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as error:
        return {"status": "failed", "tool": "agent_read_file", "error": str(error), "path": str(target)}
    start_line = max(1, int(arguments.get("start_line") or 1))
    end_line = int(arguments.get("end_line") or len(lines))
    end_line = min(end_line, len(lines))
    if start_line > len(lines):
        return {"status": "failed", "tool": "agent_read_file", "error": f"start_line {start_line} exceeds file length {len(lines)}"}
    selected = lines[start_line - 1 : end_line]
    return {
        "status": "completed", "tool": "agent_read_file",
        "path": str(target), "total_lines": len(lines),
        "start_line": start_line, "end_line": end_line,
        "content": "".join(selected),
    }


def agent_grep_search(arguments: JsonDict) -> JsonDict:
    pattern = str(arguments.get("pattern") or "").strip()
    if not pattern:
        return {"status": "failed", "tool": "agent_grep_search", "error": "pattern is required"}
    include_pattern = str(arguments.get("include_pattern") or "**/*").strip()
    is_regexp = bool(arguments.get("is_regexp", False))
    max_results = max(1, min(100, int(arguments.get("max_results") or 30)))
    flags = 0 if is_regexp else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags) if is_regexp else None
    except re.error as error:
        return {"status": "failed", "tool": "agent_grep_search", "error": f"invalid regex: {error}"}
    results = []
    base = BASE_DIR.resolve()
    try:
        for file_path in base.rglob(include_pattern):
            if not file_path.is_file() or is_sensitive_path(file_path):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                for line_index, line in enumerate(text.splitlines(), 1):
                    match = compiled.search(line) if compiled else pattern.casefold() in line.casefold()
                    if match:
                        results.append({"path": str(file_path.relative_to(base)), "line": line_index, "text": line.strip()[:200]})
                        if len(results) >= max_results:
                            break
            except (UnicodeDecodeError, PermissionError):
                continue
            if len(results) >= max_results:
                break
    except Exception as error:
        return {"status": "failed", "tool": "agent_grep_search", "error": str(error), "results": results[:max_results]}
    return {"status": "completed", "tool": "agent_grep_search", "pattern": pattern, "is_regexp": is_regexp, "total_matches": len(results), "results": results[:max_results]}


def agent_file_search(arguments: JsonDict) -> JsonDict:
    glob_pattern = str(arguments.get("pattern") or "").strip()
    if not glob_pattern:
        return {"status": "failed", "tool": "agent_file_search", "error": "pattern is required"}
    max_results = max(1, min(200, int(arguments.get("max_results") or 50)))
    results = []
    base = BASE_DIR.resolve()
    try:
        for file_path in base.rglob(glob_pattern):
            try:
                rel = file_path.relative_to(base)
            except ValueError:
                continue
            if ".git" in rel.parts:
                continue
            info = {"path": str(rel), "is_dir": file_path.is_dir()}
            if file_path.is_file():
                try:
                    info["size"] = file_path.stat().st_size
                except OSError:
                    info["size"] = 0
            results.append(info)
            if len(results) >= max_results:
                break
    except Exception as error:
        return {"status": "failed", "tool": "agent_file_search", "error": str(error), "results": results}
    return {"status": "completed", "tool": "agent_file_search", "pattern": glob_pattern, "total": len(results), "results": results}


def agent_replace_string(arguments: JsonDict) -> JsonDict:
    path_text = str(arguments.get("path") or "").strip()
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    if not path_text:
        return {"status": "failed", "tool": "agent_replace_string", "error": "path is required"}
    if not isinstance(old_string, str) or not old_string.strip():
        return {"status": "failed", "tool": "agent_replace_string", "error": "old_string is required"}
    if not isinstance(new_string, str):
        return {"status": "failed", "tool": "agent_replace_string", "error": "new_string is required"}
    target = resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_replace_string", "error": "path is outside project root"}
    if not target.exists():
        return {"status": "failed", "tool": "agent_replace_string", "error": "file not found", "path": path_text}
    if is_sensitive_path(target):
        return {"status": "failed", "tool": "agent_replace_string", "error": "cannot edit sensitive files"}
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as error:
        return {"status": "failed", "tool": "agent_replace_string", "error": f"cannot read file: {error}"}
    if old_string not in content:
        return {"status": "failed", "tool": "agent_replace_string", "error": "old_string not found in file (exact match required)", "path": path_text}
    if content.count(old_string) > 1:
        return {"status": "failed", "tool": "agent_replace_string", "error": f"old_string appears {content.count(old_string)} times; add more context", "path": path_text}
    original_bytes = target.read_bytes()
    backup_path = write_backup(target, original_bytes)
    new_content = content.replace(old_string, new_string, 1)
    try:
        target.write_text(new_content, encoding="utf-8")
    except Exception as error:
        target.write_bytes(original_bytes)
        return {"status": "failed", "tool": "agent_replace_string", "error": f"write failed, restored: {error}"}
    validation = validate_written_file(target)
    if validation.get("status") != "completed" and target.suffix.casefold() == ".py":
        target.write_bytes(original_bytes)
        return {"status": "failed", "tool": "agent_replace_string", "error": "validation failed; original restored", "path": path_text, "validation": validation, "backup_path": str(backup_path)}
    return {"status": "completed", "tool": "agent_replace_string", "path": path_text, "backup_path": str(backup_path), "validation": validation}


def agent_insert_text(arguments: JsonDict) -> JsonDict:
    path_text = str(arguments.get("path") or "").strip()
    insert_line = int(arguments.get("insert_line") or 0)
    text = arguments.get("text")
    if not path_text:
        return {"status": "failed", "tool": "agent_insert_text", "error": "path is required"}
    if not isinstance(text, str) or not text.strip():
        return {"status": "failed", "tool": "agent_insert_text", "error": "text is required"}
    target = resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_insert_text", "error": "path is outside project root"}
    if not target.exists():
        return {"status": "failed", "tool": "agent_insert_text", "error": "file not found", "path": path_text}
    if is_sensitive_path(target):
        return {"status": "failed", "tool": "agent_insert_text", "error": "cannot edit sensitive files"}
    original_bytes = target.read_bytes()
    backup_path = write_backup(target, original_bytes)
    try:
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as error:
        return {"status": "failed", "tool": "agent_insert_text", "error": f"cannot read file: {error}"}
    insert_line = max(0, min(insert_line, len(lines)))
    text_to_insert = text if text.endswith("\n") else text + "\n"
    lines.insert(insert_line, text_to_insert)
    try:
        target.write_text("".join(lines), encoding="utf-8")
    except Exception as error:
        target.write_bytes(original_bytes)
        return {"status": "failed", "tool": "agent_insert_text", "error": f"write failed, restored: {error}"}
    validation = validate_written_file(target)
    if validation.get("status") != "completed" and target.suffix.casefold() == ".py":
        target.write_bytes(original_bytes)
        return {"status": "failed", "tool": "agent_insert_text", "error": "validation failed; original restored", "path": path_text, "validation": validation, "backup_path": str(backup_path)}
    return {"status": "completed", "tool": "agent_insert_text", "path": path_text, "insert_line": insert_line, "backup_path": str(backup_path), "validation": validation}


def agent_create_file(arguments: JsonDict) -> JsonDict:
    path_text = str(arguments.get("path") or "").strip()
    content = arguments.get("content")
    if not path_text:
        return {"status": "failed", "tool": "agent_create_file", "error": "path is required"}
    if not isinstance(content, str):
        return {"status": "failed", "tool": "agent_create_file", "error": "content must be a string"}
    target = resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_create_file", "error": "path is outside project root"}
    if target.exists():
        return {"status": "failed", "tool": "agent_create_file", "error": "file already exists", "path": path_text}
    if is_sensitive_path(target):
        return {"status": "failed", "tool": "agent_create_file", "error": "cannot create sensitive files"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as error:
        return {"status": "failed", "tool": "agent_create_file", "error": str(error), "path": path_text}
    validation = validate_written_file(target)
    if validation.get("status") != "completed" and target.suffix.casefold() == ".py":
        target.unlink(missing_ok=True)
        return {"status": "failed", "tool": "agent_create_file", "error": "validation failed; file removed", "path": path_text, "validation": validation}
    return {"status": "completed", "tool": "agent_create_file", "path": path_text, "validation": validation}


# ── Memory Operation Tools ──


def agent_add_memory(arguments: JsonDict) -> JsonDict:
    """Add a new memory/insight to the AI's persistent memory store."""
    text = str(arguments.get("text") or "").strip()
    importance = float(arguments.get("importance", 0.5))
    category = str(arguments.get("category", "general")).strip()
    is_core = bool(arguments.get("is_core", False))
    if not text:
        return {"status": "failed", "tool": "agent_add_memory", "error": "text is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        mid = mgr.remember(text, emotion="", importance=importance, source="agent", category=category, is_core=is_core)
        if mid:
            mgr.add_insight(text)
        return {"status": "completed" if mid else "failed", "tool": "agent_add_memory", "memory_id": mid, "memory": text, "importance": importance, "category": category, "is_core": is_core}
    except Exception as error:
        return {"status": "failed", "tool": "agent_add_memory", "error": str(error)}


def agent_search_memory(arguments: JsonDict) -> JsonDict:
    """Search the AI's persistent memory store for relevant entries."""
    query = str(arguments.get("query") or "").strip()
    top_k = max(1, min(20, int(arguments.get("top_k", 5))))
    if not query:
        return {"status": "failed", "tool": "agent_search_memory", "error": "query is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        results = mgr.search_memories(query, top_k=top_k)
        context = mgr.get_relevant_memory_context(query, top_k=top_k)
        return {
            "status": "completed", "tool": "agent_search_memory",
            "query": query, "top_k": top_k,
            "results": results[:top_k],
            "context": context,
        }
    except Exception as error:
        return {"status": "failed", "tool": "agent_search_memory", "error": str(error)}


def agent_add_working_memory(arguments: JsonDict) -> JsonDict:
    """Add a short-term working memory item. Use for temporary context."""
    text = str(arguments.get("text") or "").strip()
    importance = float(arguments.get("importance", 0.5))
    ttl = max(60, int(arguments.get("ttl_seconds", 3600)))
    if not text:
        return {"status": "failed", "tool": "agent_add_working_memory", "error": "text is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        wid = mgr.add_working_memory(text, importance=importance, ttl_seconds=ttl)
        return {"status": "completed" if wid else "failed", "tool": "agent_add_working_memory", "id": wid}
    except Exception as error:
        return {"status": "failed", "tool": "agent_add_working_memory", "error": str(error)}


def agent_get_working_memory(arguments: JsonDict) -> JsonDict:
    """Return active working memory items."""
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        items = mgr.get_working_memory()
        return {"status": "completed", "tool": "agent_get_working_memory", "items": items, "count": len(items)}
    except Exception as error:
        return {"status": "failed", "tool": "agent_get_working_memory", "error": str(error)}


def agent_create_episode(arguments: JsonDict) -> JsonDict:
    """Bundle several memories into an episode (a themed group)."""
    title = str(arguments.get("title") or "").strip()
    memory_ids = arguments.get("memory_ids", [])
    if not title or not memory_ids:
        return {"status": "failed", "tool": "agent_create_episode", "error": "title and memory_ids are required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        eid = mgr.create_episode(title, memory_ids)
        return {"status": "completed" if eid else "failed", "tool": "agent_create_episode", "episode_id": eid, "title": title}
    except Exception as error:
        return {"status": "failed", "tool": "agent_create_episode", "error": str(error)}


def agent_search_episodes(arguments: JsonDict) -> JsonDict:
    """Search episodes by semantic similarity."""
    query = str(arguments.get("query") or "").strip()
    top_k = max(1, min(20, int(arguments.get("top_k", 5))))
    if not query:
        return {"status": "failed", "tool": "agent_search_episodes", "error": "query is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        results = mgr.search_episodes(query, top_k=top_k)
        return {"status": "completed", "tool": "agent_search_episodes", "results": results, "count": len(results)}
    except Exception as error:
        return {"status": "failed", "tool": "agent_search_episodes", "error": str(error)}


def agent_get_episode(arguments: JsonDict) -> JsonDict:
    """Get a specific episode with its member memories."""
    episode_id = int(arguments.get("episode_id", 0))
    if not episode_id:
        return {"status": "failed", "tool": "agent_get_episode", "error": "episode_id is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        episode = mgr.get_episode(episode_id)
        return {"status": "completed" if episode else "failed", "tool": "agent_get_episode", "episode": episode}
    except Exception as error:
        return {"status": "failed", "tool": "agent_get_episode", "error": str(error)}


def agent_link_memories(arguments: JsonDict) -> JsonDict:
    """Create a directed link between two memories (e.g. 'causes', 'associated')."""
    source_id = int(arguments.get("source_id", 0))
    target_id = int(arguments.get("target_id", 0))
    relation = str(arguments.get("relation", "associated")).strip()
    if not source_id or not target_id:
        return {"status": "failed", "tool": "agent_link_memories", "error": "source_id and target_id are required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        ok = mgr.link_memories(source_id, target_id, relation=relation)
        return {"status": "completed" if ok else "failed", "tool": "agent_link_memories", "source_id": source_id, "target_id": target_id, "relation": relation}
    except Exception as error:
        return {"status": "failed", "tool": "agent_link_memories", "error": str(error)}


def agent_get_related_memories(arguments: JsonDict) -> JsonDict:
    """Get memories linked to/from a given memory."""
    memory_id = int(arguments.get("memory_id", 0))
    if not memory_id:
        return {"status": "failed", "tool": "agent_get_related_memories", "error": "memory_id is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        related = mgr.get_related_memories(memory_id)
        return {"status": "completed", "tool": "agent_get_related_memories", "related": related, "count": len(related)}
    except Exception as error:
        return {"status": "failed", "tool": "agent_get_related_memories", "error": str(error)}


def agent_consolidate_memories(arguments: JsonDict) -> JsonDict:
    """Create consolidated summaries from high-importance linked memories."""
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        summaries = mgr.consolidate_memories()
        return {"status": "completed", "tool": "agent_consolidate_memories", "summaries": summaries, "count": len(summaries)}
    except Exception as error:
        return {"status": "failed", "tool": "agent_consolidate_memories", "error": str(error)}


def agent_get_memory_stats(arguments: JsonDict) -> JsonDict:
    """Return aggregate memory statistics."""
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        stats = mgr.get_memory_stats()
        return {"status": "completed", "tool": "agent_get_memory_stats", "stats": stats}
    except Exception as error:
        return {"status": "failed", "tool": "agent_get_memory_stats", "error": str(error)}


def agent_set_core_memory(arguments: JsonDict) -> JsonDict:
    """Mark/unmark a memory as a core identity memory."""
    memory_id = int(arguments.get("memory_id", 0))
    is_core = bool(arguments.get("is_core", True))
    if not memory_id:
        return {"status": "failed", "tool": "agent_set_core_memory", "error": "memory_id is required"}
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        ok = mgr.set_core_memory(memory_id, is_core)
        return {"status": "completed" if ok else "failed", "tool": "agent_set_core_memory", "memory_id": memory_id, "is_core": is_core}
    except Exception as error:
        return {"status": "failed", "tool": "agent_set_core_memory", "error": str(error)}


def agent_get_core_memories(arguments: JsonDict) -> JsonDict:
    """Return all core identity memories."""
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        memories = mgr.get_core_memories()
        return {"status": "completed", "tool": "agent_get_core_memories", "memories": memories, "count": len(memories)}
    except Exception as error:
        return {"status": "failed", "tool": "agent_get_core_memories", "error": str(error)}


def agent_list_recent_memories(arguments: JsonDict) -> JsonDict:
    """Return recent memories ordered by creation time."""
    limit = max(1, min(50, int(arguments.get("limit", 15))))
    try:
        from ellie.memory.memory import MemoryManager
        mgr = MemoryManager()
        memories = mgr.list_recent_memories(limit=limit)
        return {"status": "completed", "tool": "agent_list_recent_memories", "memories": memories, "count": len(memories)}
    except Exception as error:
        return {"status": "failed", "tool": "agent_list_recent_memories", "error": str(error)}
