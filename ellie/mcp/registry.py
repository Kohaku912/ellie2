"""Registry of available MCP integrations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class McpEntry:
    name: str
    description: str
    status_getter: Callable[[], dict]


def get_registered_mcp_entries() -> List[McpEntry]:
    """Return the integrations that are available in this build."""
    from ellie.mcp.playwright.tools import get_playwright_status

    return [
        McpEntry(
            name="playwright",
            description="Browser automation via Playwright MCP.",
            status_getter=get_playwright_status,
        ),
    ]
