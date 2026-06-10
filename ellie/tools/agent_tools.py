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
