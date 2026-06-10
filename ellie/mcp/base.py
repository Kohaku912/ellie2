"""Shared helpers for MCP-style integrations."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpProcessSpec:
    """Describe how to launch a long-lived MCP process."""

    command: Sequence[str]
    cwd: Path | None = None


def launch_process(spec: McpProcessSpec) -> subprocess.Popen[str]:
    """Start a subprocess and return the live handle."""
    logger.info("Launching MCP process: %s", " ".join(spec.command))
    return subprocess.Popen(
        list(spec.command),
        cwd=spec.cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
