# Ellie2 Architecture

Ellie2 is organized around a few narrow layers:

- `ellie/core/`: the agent, LLM routing, prompt construction, and shared instruction execution
- `ellie/memory/`: daily memory, self-model, and social needs
- `ellie/tools/`: normal tools that the model can call
- `ellie/mcp/`: Playwright MCP and the local PC bridge
- `ellie/autonomy/`: self-call queue, long-term goals, and scheduler loops
- `ellie/logging/`: audit logging and UTF-8 logging helpers
- `apps/`: CLI, daemon, and web entrypoints

The `data/` directory stores runtime state and is the only supported data root.
