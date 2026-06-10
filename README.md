# Ellie2

Ellie2 is a local Japanese AI agent that can run as a daemon, answer direct instructions from the command line, and expose a localhost-only web dashboard. The codebase is now organized around the `ellie/` package and the `apps/` entrypoints.

## Layout

```text
ellie2/
├─ ellie/
├─ apps/
├─ data/
├─ docs/
└─ tests/
```

## Entry Points

```powershell
.\.venv\Scripts\python apps\cli.py --instruction "俳句を作って"
.\.venv\Scripts\python apps\daemon.py
.\.venv\Scripts\python apps\web_server.py
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.template .env
```

Set `CEREBRAS_API_KEY` in `.env`.

## Runtime Data

- `data/memory/`: daily memory, long-term memory, goal files, and task logs
- `data/self/`: self-model, current self-state, self-development requests, and social needs history
- `data/runtime/`: runtime locks and other ephemeral state
- `data/logs/`: audit logs and execution logs
- `data/vendor/playwright/`: Playwright MCP browser profile and storage state

## MCP

Playwright MCP starts through `npx @playwright/mcp@latest` with its persistent profile under `data/vendor/playwright/`.

The local PC bridge runs separately from MCP and listens on `ws://127.0.0.1:8765`.

## Validation

```powershell
.\.venv\Scripts\python -m compileall -q apps ellie tests
.\.venv\Scripts\python -m unittest tests.test_tool_guardrails
.\.venv\Scripts\python apps\cli.py --instruction "俳句を作って"
```
