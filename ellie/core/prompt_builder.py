"""Prompt assembly helpers for Ellie."""
from __future__ import annotations

from ellie.config import AGENT_SYSTEM_PROMPT, TOOL_CAPABILITY_INDEX


def build_base_prompt(drive_summary: str = "") -> str:
    """Build the shared base prompt for agent-facing calls."""
    parts = [TOOL_CAPABILITY_INDEX.strip(), AGENT_SYSTEM_PROMPT.strip()]
    if drive_summary.strip():
        parts.insert(1, drive_summary.strip())
    return "\n\n".join(part for part in parts if part).strip()


def build_autonomy_prompt(extra_instructions: str = "", drive_summary: str = "") -> str:
    """Build an autonomy prompt with optional extra instructions."""
    base_prompt = build_base_prompt(drive_summary=drive_summary)
    if extra_instructions.strip():
        return f"{base_prompt}\n\n{extra_instructions.strip()}"
    return base_prompt
