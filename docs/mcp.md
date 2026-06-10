# MCP Notes

## Playwright

- Starts via `npx @playwright/mcp@latest`
- Uses `data/vendor/playwright/` for the persistent browser profile
- Tool definitions are exposed through `ellie.mcp.playwright.tools`

## PC Bridge

- Runs as a local WebSocket bridge on `ws://127.0.0.1:8765`
- Used for desktop and Discord-related tool calls
- Protocol helpers live in `ellie.mcp.pc_bridge.protocol`

## Registry

Use `ellie.mcp.registry.get_registered_mcp_entries()` to discover supported integrations at runtime.
