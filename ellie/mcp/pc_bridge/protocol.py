"""Message protocol for the local PC bridge."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class ToolCallMessage:
    """Normalized tool-call message payload."""

    call_id: str
    tool: str
    arguments: JsonDict


@dataclass(frozen=True)
class ToolResultMessage:
    """Normalized tool-result message payload."""

    call_id: str
    ok: bool
    result: JsonDict
