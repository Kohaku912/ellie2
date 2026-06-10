"""
Logging helpers for Windows-safe UTF-8 output.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def configure_utf8_stdio() -> None:
    """Make stdout/stderr tolerant of Japanese text on Windows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def utf8_file_handler(path: str | Path) -> logging.FileHandler:
    """Create a UTF-8 file handler that never crashes on unencodable text."""
    return logging.FileHandler(path, encoding="utf-8", errors="replace")
