"""Playwright MCP server lifecycle helpers."""
from __future__ import annotations

from ellie.mcp.base import McpProcessSpec, launch_process
from ellie.mcp.playwright.client import get_playwright_manager


def ensure_playwright_mcp_ready() -> dict:
    """Ensure the Playwright MCP server and browser profile are ready."""
    return get_playwright_manager().ensure_ready()


def launch_playwright_mcp() -> dict:
    """Start Playwright MCP using the configured launcher."""
    return get_playwright_manager().ensure_ready()


def launch_process_for_spec(spec: McpProcessSpec):
    """Expose the shared process helper for Playwright-specific launch flows."""
    return launch_process(spec)
