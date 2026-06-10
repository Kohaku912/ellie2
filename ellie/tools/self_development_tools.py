"""Self-development tool for Ellie (inspect, write_file, verify, request)."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict

from ellie.config import BASE_DIR, MEMORY_DIR, SELF_DEVELOPMENT_REQUESTS_FILE
from ellie.time_utils import isoformat_local
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

SELF_DEVELOPMENT_NOTE = MEMORY_DIR / "self_development.md"
SELF_DEVELOPMENT_REQUESTS_NOTE = SELF_DEVELOPMENT_REQUESTS_FILE
SELF_DEVELOPMENT_REQUESTS_HEADING = "## 保留中の自己改善リクエスト"
DEFAULT_SELF_DEVELOPMENT_REQUESTS_TEXT = """# Ellie の自己改善リクエスト
AI が「今すぐ実装しないほうがよい」と判断した改善依頼を、短い自然文で残すためのメモです。

## 保留中の自己改善リクエスト
- まだ保留中の依頼はありません。
"""


def self_development(arguments: JsonDict) -> JsonDict:
    """Inspect, request, or safely edit Ellie code inside the project root."""
    action = str(arguments.get("action") or "inspect").strip().casefold()
    if action in {"inspect", "plan", "read"}:
        return _self_development_inspect(arguments)
    if action in {"request", "queue_request"}:
        return _self_development_request(arguments)
    if action in {"verify", "py_compile", "validate"}:
        return _self_development_verify(arguments)
    if action in {"write_file", "edit", "replace_file"}:
        return _self_development_write(arguments)
    return {"status": "failed", "tool": "self_development", "error": f"Unsupported action: {action}"}


def _self_development_inspect(arguments: JsonDict) -> JsonDict:
    focus = str(arguments.get("focus") or "").strip()
    request_text = read_text(SELF_DEVELOPMENT_REQUESTS_NOTE)
    pending_requests = _extract_request_bullets(request_text)

    # Scan the tool registry to show existing tools
    registry_path = BASE_DIR / "ellie" / "tools" / "registry.py"
    existing_tools = []
    if registry_path.exists():
        text = registry_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('name="') and stripped.endswith('",'):
                existing_tools.append(stripped[5:-2])

    suggestions = []
    if focus:
        suggestions.append(f"「{focus}」に関連する既存の実装パターンを agent_read_file / agent_grep_search で調査してください。")
    suggestions.append("新規Tool追加の手順:")
    suggestions.append("  1. 既存の類似Toolを agent_read_file で読んでパターンを把握する")
    suggestions.append("  2. autonomous_tools.py または新規ファイルに関数を追加する")
    suggestions.append("  3. registry.py に ToolDefinition を追加する")
    suggestions.append("  4. dynamic_retrieval.py に handler を追加する")
    suggestions.append("  5. 必要に応じて runtime.py の HEAVY_CORE_TOOL_NAMES に追加する")
    suggestions.append("  6. execute_shell で py_compile を実行して検証する")
    suggestions.append("  7. execute_shell で pytest を実行して回帰テストを行う")
    suggestions.append(f"\n現在のTool一覧 ({len(existing_tools)}個): {', '.join(existing_tools[:30])}")

    append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} inspect {focus or 'general'}")
    return {
        "status": "completed", "tool": "self_development", "action": "inspect",
        "focus": focus or "general", "existing_tool_count": len(existing_tools),
        "existing_tools": existing_tools[:30], "pending_requests": pending_requests,
        "suggestions": suggestions,
        "memory_note": "自己開発としてコードベースを点検した。",
    }


def _self_development_request(arguments: JsonDict) -> JsonDict:
    title = str(arguments.get("title") or arguments.get("request") or "").strip()
    reason = str(arguments.get("reason") or "").strip()
    priority = str(arguments.get("priority") or "normal").strip().casefold()
    scope = str(arguments.get("scope") or "").strip()
    details = str(arguments.get("details") or "").strip()
    if not title and not details:
        return {"status": "failed", "tool": "self_development", "error": "title or request is required"}
    request_text = title or details
    note_parts = []
    if priority and priority != "normal":
        note_parts.append(f"[{priority}]")
    note_parts.append(request_text)
    if scope:
        note_parts.append(f"対象: {scope}")
    if reason:
        note_parts.append(f"理由: {reason}")
    if details and details != request_text:
        note_parts.append(f"補足: {details}")
    note = " / ".join(note_parts)
    appended = _append_unique_request_note(SELF_DEVELOPMENT_REQUESTS_NOTE, note, max_notes=20)
    append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} request {request_text}")
    return {
        "status": "completed", "tool": "self_development", "action": "request",
        "appended": appended, "request": request_text, "reason": reason,
        "priority": priority, "scope": scope, "details": details,
        "path": str(SELF_DEVELOPMENT_REQUESTS_NOTE),
        "memory_note": "大きめの自己改善依頼を保留メモに残した。",
    }


def _self_development_write(arguments: JsonDict) -> JsonDict:
    relative_path = str(arguments.get("path") or "").strip()
    content = arguments.get("content")
    if not relative_path:
        return {"status": "failed", "tool": "self_development", "error": "path is required"}
    if not isinstance(content, str):
        return {"status": "failed", "tool": "self_development", "error": "content must be a string"}
    target_path = resolve_project_path(relative_path)
    if target_path is None:
        return {"status": "failed", "tool": "self_development", "error": "path must stay inside the Ellie2 project root", "path": relative_path}
    if is_sensitive_path(target_path):
        return {"status": "failed", "tool": "self_development", "error": "sensitive files cannot be edited autonomously", "path": str(target_path)}
    original_bytes = target_path.read_bytes() if target_path.exists() else b""
    backup_path = write_backup(target_path, original_bytes)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    validation = validate_written_file(target_path)
    if validation.get("status") != "completed":
        if original_bytes:
            target_path.write_bytes(original_bytes)
        else:
            target_path.unlink(missing_ok=True)
        return {"status": "failed", "tool": "self_development", "action": "write_file", "path": str(target_path), "backup_path": str(backup_path), "validation": validation, "error": "validation failed; original file was restored"}
    try:
        rel = target_path.relative_to(BASE_DIR)
    except Exception:
        rel = target_path
    append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} write_file {rel}")
    return {"status": "completed", "tool": "self_development", "action": "write_file", "path": str(target_path), "backup_path": str(backup_path), "validation": validation, "memory_note": "自己開発としてプロジェクト内ファイルを編集し、検証に成功した。"}


def _self_development_verify(arguments: JsonDict) -> JsonDict:
    raw_paths = arguments.get("paths")
    if isinstance(raw_paths, list):
        path_texts = [str(p).strip() for p in raw_paths if str(p).strip()]
    else:
        path_texts = ["ellie/config.py", "ellie/core/agent.py", "ellie/tools/dynamic_retrieval.py"]
    validations = []
    for path_text in path_texts[:8]:
        target = resolve_project_path(path_text)
        if target is None or not target.exists():
            validations.append({"path": path_text, "status": "failed", "error": "missing or outside project"})
            continue
        validations.append({"path": str(target), **validate_written_file(target)})
    ok = bool(validations) and all(v.get("status") == "completed" for v in validations)
    append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} verify ok={ok} paths={len(validations)}")
    return {"status": "completed" if ok else "failed", "tool": "self_development", "action": "verify", "validations": validations,
            "memory_note": "自己開発として構文検証を行った。" if ok else "自己開発の構文検証で失敗を見つけた。"}


def _extract_request_bullets(text: str) -> list[str]:
    bullets = []
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if not cleaned.startswith("- "):
            continue
        if "まだ保留中の依頼はありません" in cleaned:
            continue
        bullets.append(cleaned[2:].strip())
    return bullets[-20:]


def _normalize_request_text(note: str) -> str:
    normalized = " ".join((note or "").strip().split()).casefold()
    normalized = re.sub(r"\s*[:：]\s*", ":", normalized)
    return normalized.strip(" 。，,.;")


def _append_unique_request_note(path: Path, note: str, max_notes: int = 20) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_text = read_text(path)
    if not current_text:
        path.write_text(DEFAULT_SELF_DEVELOPMENT_REQUESTS_TEXT, encoding="utf-8")
        current_text = read_text(path)
    normalized_note = _normalize_request_text(note)
    existing_notes = {_normalize_request_text(ex) for ex in _extract_request_bullets(current_text)}
    if normalized_note in existing_notes:
        return False
    lines = current_text.splitlines()
    heading_index = next((i for i, line in enumerate(lines) if line.strip() == SELF_DEVELOPMENT_REQUESTS_HEADING), -1)
    if heading_index < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([SELF_DEVELOPMENT_REQUESTS_HEADING, f"- {note}"])
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return True
    section_end = len(lines)
    for i in range(heading_index + 1, len(lines)):
        if lines[i].startswith("## "):
            section_end = i
            break
    section_lines = lines[heading_index + 1 : section_end]
    bullet_lines = [l for l in section_lines if l.strip().startswith("- ") and "まだ保留中の依頼はありません" not in l]
    bullet_lines.append(f"- {note}")
    bullet_lines = bullet_lines[-max_notes:]
    new_lines = [*lines[: heading_index + 1], *bullet_lines, *lines[section_end:]]
    path.write_text("\n".join(new_lines).strip() + "\n", encoding="utf-8")
    return True
