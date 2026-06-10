# Ellie MCP

This directory collects MCP-related integrations and their support code.

## Available Integrations

- `playwright`: browser automation via `npx @playwright/mcp`
- `pc_bridge`: local PC tool bridge for Discord and desktop actions

## Files

- `base.py`: shared process-launch helpers
- `registry.py`: integration registry used by diagnostics and the dashboard
- `playwright/`: Playwright MCP client, server launcher, and tool definitions
- `pc_bridge/`: PC bridge protocol and transport helpers
