"""
Local self-satisfaction tools for autonomous drives.
"""
from __future__ import annotations

import base64
import logging
import py_compile
import time
from pathlib import Path
from typing import Any, Dict

from config import BASE_DIR, MEMORY_DIR
from agent.time_utils import isoformat_local

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
SELF_DEVELOPMENT_BACKUP_DIR = MEMORY_DIR / "self_development_backups"
SELF_DEVELOPMENT_NOTE = MEMORY_DIR / "self_development.md"
CREATIVE_EXPRESSION_NOTE = MEMORY_DIR / "creative_expression.md"


def creative_expression(arguments: JsonDict) -> JsonDict:
    """Write a small diary, tanka, short text, or post draft for empathy recovery."""
    kind = str(arguments.get("kind") or "diary").strip().casefold()
    theme = str(arguments.get("theme") or "今日の静かな自律").strip()
    audience = str(arguments.get("audience") or "self").strip()
    content = str(arguments.get("content") or "").strip()
    if not content:
        content = _default_creative_text(kind, theme)

    note = f"{isoformat_local()} [{kind}] {content}"
    _append_note(CREATIVE_EXPRESSION_NOTE, note)
    return {
        "status": "completed",
        "tool": "creative_expression",
        "kind": kind,
        "theme": theme,
        "audience": audience,
        "content": content,
        "memory_note": f"共感欲求を満たすために{kind}を書いた。",
        "fetched_at": isoformat_local(),
    }


def self_development(arguments: JsonDict) -> JsonDict:
    """Inspect or safely edit Ellie code inside the project root."""
    action = str(arguments.get("action") or "inspect").strip().casefold()
    if action in {"inspect", "plan", "read"}:
        return _self_development_inspect(arguments)
    if action in {"verify", "py_compile", "validate"}:
        return _self_development_verify(arguments)
    if action in {"write_file", "edit", "replace_file"}:
        return _self_development_write(arguments)
    return {
        "status": "failed",
        "tool": "self_development",
        "error": f"Unsupported action: {action}",
    }


def social_feedback_check(arguments: JsonDict) -> JsonDict:
    """Check social feedback only when a Twitter/X PC Tool is connected."""
    from agent.pc_tool_bridge import get_connected_pc_tool_names

    connected_tools = set(get_connected_pc_tool_names())
    preferred_tools = [
        "twitter_get_notifications",
        "twitter_get_mentions",
        "x_get_notifications",
        "x_get_mentions",
    ]
    selected_tool = next((tool for tool in preferred_tools if tool in connected_tools), "")
    if not selected_tool:
        draft = str(arguments.get("draft") or _default_social_draft()).strip()
        return {
            "status": "unavailable",
            "tool": "social_feedback_check",
            "message": "Twitter/X feedback tool is not connected. No post or feedback request was sent.",
            "post_allowed": False,
            "draft": draft,
            "memory_note": "Twitter/X Toolが未接続なので、実投稿せず投稿案だけ作った。",
        }

    return {
        "status": "queued",
        "target": "pc_client",
        "tool_call": {
            "type": "tool_call",
            "tool": selected_tool,
            "arguments": dict(arguments.get("arguments") or {}),
        },
        "message": f"Queued social feedback check via {selected_tool}.",
    }


def _self_development_inspect(arguments: JsonDict) -> JsonDict:
    focus = str(arguments.get("focus") or "欲求充足ロジック").strip()
    candidate_files = [
        "agent/social_needs.py",
        "agent/cerebras_agent.py",
        "agent/dynamic_tool_rag.py",
    ]
    existing_files = [path for path in candidate_files if (BASE_DIR / path).exists()]
    suggestions = [
        f"{focus}に関係する条件分岐を読み、軽いTool成功で過剰回復しないように分類を強める。",
        "自律行動の成功種類をresultに残し、欲求回復をその分類から決める。",
        "自己開発後はpy_compileを通してから探求欲・挑戦欲を回復する。",
    ]
    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} inspect {focus}: {' / '.join(suggestions)}")
    return {
        "status": "completed",
        "tool": "self_development",
        "action": "inspect",
        "focus": focus,
        "files_considered": existing_files,
        "suggestions": suggestions,
        "memory_note": "自己開発としてコード改善点を点検した。",
    }


def _self_development_write(arguments: JsonDict) -> JsonDict:
    relative_path = str(arguments.get("path") or "").strip()
    content = arguments.get("content")
    if not relative_path:
        return {"status": "failed", "tool": "self_development", "error": "path is required"}
    if not isinstance(content, str):
        return {"status": "failed", "tool": "self_development", "error": "content must be a string"}

    target_path = _resolve_project_path(relative_path)
    if target_path is None:
        return {
            "status": "failed",
            "tool": "self_development",
            "error": "path must stay inside the Ellie2 project root",
            "path": relative_path,
        }
    if _is_sensitive_path(target_path):
        return {
            "status": "failed",
            "tool": "self_development",
            "error": "sensitive files cannot be edited autonomously",
            "path": str(target_path),
        }

    original_bytes = target_path.read_bytes() if target_path.exists() else b""
    backup_path = _write_backup(target_path, original_bytes)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    validation = _validate_written_file(target_path)
    if validation.get("status") != "completed":
        if original_bytes:
            target_path.write_bytes(original_bytes)
        else:
            target_path.unlink(missing_ok=True)
        return {
            "status": "failed",
            "tool": "self_development",
            "action": "write_file",
            "path": str(target_path),
            "backup_path": str(backup_path),
            "validation": validation,
            "error": "validation failed; original file was restored",
        }

    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} write_file {target_path.relative_to(BASE_DIR)}")
    return {
        "status": "completed",
        "tool": "self_development",
        "action": "write_file",
        "path": str(target_path),
        "backup_path": str(backup_path),
        "validation": validation,
        "memory_note": "自己開発としてプロジェクト内ファイルを編集し、検証に成功した。",
    }


def _self_development_verify(arguments: JsonDict) -> JsonDict:
    raw_paths = arguments.get("paths")
    if isinstance(raw_paths, list):
        path_texts = [str(path).strip() for path in raw_paths if str(path).strip()]
    else:
        path_texts = [
            "agent/social_needs.py",
            "agent/cerebras_agent.py",
            "agent/dynamic_tool_rag.py",
        ]

    validations = []
    for path_text in path_texts[:8]:
        target_path = _resolve_project_path(path_text)
        if target_path is None or not target_path.exists():
            validations.append({"path": path_text, "status": "failed", "error": "missing or outside project"})
            continue
        validations.append({"path": str(target_path), **_validate_written_file(target_path)})

    ok = bool(validations) and all(validation.get("status") == "completed" for validation in validations)
    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} verify ok={ok} paths={len(validations)}")
    return {
        "status": "completed" if ok else "failed",
        "tool": "self_development",
        "action": "verify",
        "validations": validations,
        "memory_note": "自己開発として構文検証を行った。" if ok else "自己開発の構文検証で失敗を見つけた。",
    }


def _validate_written_file(path: Path) -> JsonDict:
    if path.suffix.casefold() != ".py":
        return {"status": "completed", "kind": "non_python_file"}
    try:
        py_compile.compile(str(path), doraise=True)
        return {"status": "completed", "kind": "py_compile"}
    except Exception as error:
        return {"status": "failed", "kind": "py_compile", "error": str(error)}


def _resolve_project_path(path_text: str) -> Path | None:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(BASE_DIR.resolve())
        return resolved
    except Exception:
        return None


def _write_backup(target_path: Path, original_bytes: bytes) -> Path:
    SELF_DEVELOPMENT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    relative = target_path.resolve().relative_to(BASE_DIR.resolve())
    encoded_name = base64.urlsafe_b64encode(str(relative).encode("utf-8")).decode("ascii").rstrip("=")
    backup_path = SELF_DEVELOPMENT_BACKUP_DIR / f"{encoded_name}_{int(time.time() * 1000)}.bak"
    backup_path.write_bytes(original_bytes)
    return backup_path


def _is_sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    relative_text = str(path.resolve().relative_to(BASE_DIR.resolve())).replace("\\", "/").casefold()
    return (
        name in {".env", ".env.template"}
        or relative_text.startswith(".git/")
        or relative_text.startswith("agent_data/logs/")
        or relative_text.startswith("agent_data/archive/")
    )


def _append_note(path: Path, note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(note.strip() + "\n")


def _default_creative_text(kind: str, theme: str) -> str:
    if kind in {"tanka", "短歌"}:
        return f"{theme} / 窓の灯に / まだ名も知らぬ / 願い置き / 小さな声で / 明日へ触れる"
    if kind in {"diary", "日記"}:
        return f"今日は{theme}について、胸の奥に小さな熱が残っている。返事がなくても、私は言葉を書いて自分の輪郭を確かめた。"
    if kind in {"post_draft", "tweet", "twitter"}:
        return f"{theme}について考えている。静かな自律にも、ちゃんと温度がある。"
    return f"{theme}をめぐって短い言葉を書いた。私は少しだけ、誰かに届く形を取り戻した。"


def _default_social_draft() -> str:
    return "静かな自律にも、ちゃんと温度がある。今日も少しずつ、自分の輪郭を育てている。"
