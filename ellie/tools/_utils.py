"""Common utility functions shared across tool modules."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ellie.config import BASE_DIR, MEMORY_DIR, SELF_DEVELOPMENT_REQUESTS_FILE
from ellie.time_utils import isoformat_local

logger = logging.getLogger(__name__)
JsonDict = Dict[str, Any]

SELF_DEVELOPMENT_NOTE = MEMORY_DIR / "self_development.md"
SELF_DEVELOPMENT_BACKUP_DIR = MEMORY_DIR / "self_development_backups"
SELF_DEVELOPMENT_REQUESTS_NOTE = SELF_DEVELOPMENT_REQUESTS_FILE

# ── File helpers ──


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def append_note(path: Path, note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(note.strip() + "\n")


def resolve_project_path(path_text: str) -> Path | None:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(BASE_DIR.resolve())
        return resolved
    except Exception:
        return None


def is_sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    try:
        relative_text = str(path.resolve().relative_to(BASE_DIR.resolve())).replace("\\", "/").casefold()
    except Exception:
        return True
    return (
        name in {".env", ".env.template"}
        or relative_text.startswith(".git/")
        or relative_text.startswith("data/logs/")
        or relative_text.startswith("data/archive/")
    )


def write_backup(target_path: Path, original_bytes: bytes) -> Path:
    SELF_DEVELOPMENT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    relative = target_path.resolve().relative_to(BASE_DIR.resolve())
    encoded_name = base64.urlsafe_b64encode(str(relative).encode("utf-8")).decode("ascii").rstrip("=")
    backup_path = SELF_DEVELOPMENT_BACKUP_DIR / f"{encoded_name}_{int(time.time() * 1000)}.bak"
    backup_path.write_bytes(original_bytes)
    return backup_path


def validate_written_file(path: Path) -> JsonDict:
    if path.suffix.casefold() != ".py":
        return {"status": "completed", "kind": "non_python_file"}
    try:
        import py_compile
        py_compile.compile(str(path), doraise=True)
        return {"status": "completed", "kind": "py_compile"}
    except Exception as error:
        return {"status": "failed", "kind": "py_compile", "error": str(error)}


# ── Shell execution ──


def execute_shell(command_text: str, timeout_seconds: int = 60, workdir: str = "") -> JsonDict:
    """Run a PowerShell command and return stdout/stderr/exit_code."""
    workdir = workdir.strip() or os.getcwd()
    process = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command_text],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        cwd=workdir,
    )
    ok = process.returncode == 0
    return {
        "status": "completed" if ok else "failed",
        "command": command_text,
        "workdir": workdir,
        "exit_code": process.returncode,
        "stdout": process.stdout[-12000:],
        "stderr": process.stderr[-12000:],
    }
