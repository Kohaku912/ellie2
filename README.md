# Ellie2

Ellie2 is a local Japanese AI agent that can run as a daemon or answer a direct instruction from the command line. It keeps today's memory, long-term memory, a lightweight self-model, human-readable audit logs, and a persistent WebSocket bridge for PC-side tools.

## Current Features

- Direct instruction execution with `run_ai.py`
- Daemon execution with `main.py`
- Dynamic Tool Retrieval for selecting only relevant Tool schemas
- Persistent PC Tool WebSocket bridge
- Discord / PC tool operation through the bridge
- Today's memory and selected long-term memory
- Separate self-model files for stable identity and current self-state
- Social need homeostasis with dynamic prompt injection
- XMCP / X(Twitter) MCP tool integration when credentials are configured
- Long-running self-call queue for autonomous follow-ups and goals
- Local Web dashboard with read-only state view and AI chat
- Markdown audit logs for AI calls and Tool calls

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.template .env
```

Set `CEREBRAS_API_KEY` in `.env`.

## Direct Instruction

```powershell
.\.venv\Scripts\python run_ai.py --instruction "俳句を作って"
```

You can also pass a file or stdin:

```powershell
.\.venv\Scripts\python run_ai.py --file instruction.txt
Get-Content instruction.txt | .\.venv\Scripts\python run_ai.py --stdin
```

## Daemon

```powershell
.\.venv\Scripts\python main.py
```

The daemon starts the scheduler, keeps the PC Tool WebSocket bridge available, runs periodic autonomous checks, and resets daily memory.

It also starts the autonomy runtime, which processes `agent_data/autonomy_queue.jsonl` so Ellie can schedule future self-calls.

## Web Dashboard

```powershell
.\.venv\Scripts\python web_server.py
```

Open `http://127.0.0.1:8080`. The dashboard is localhost-only, shows read-only agent state, and lets you chat with Ellie through the same instruction path as `run_ai.py`.

Optional environment variables:

```text
WEB_HOST=127.0.0.1
WEB_PORT=8080
```

The Web server also starts the autonomy runtime. If `main.py` is already running, `agent_data/runtime/autonomy.lock` prevents a second worker from processing the same queue.

## XMCP / X MCP

XMCP uses the official `xdevplatform/xmcp` server. Configure the X credentials in `agent_data/vendor/xmcp/.env`:

```text
X_OAUTH_CONSUMER_KEY=...
X_OAUTH_CONSUMER_SECRET=...
X_BEARER_TOKEN=...
```

With `XMCP_ENABLED=true`, Ellie auto-installs/starts XMCP when credentials exist, exposes tools as `xmcp__...`, and allows all XMCP tool calls in direct and autonomous runs.

## PC Tool Bridge

The PC-side server/client should connect to the local WebSocket bridge at:

```text
ws://127.0.0.1:8765
```

Ellie sends messages shaped like:

```json
{
  "type": "tool_call",
  "call_id": "example-call-id",
  "tool": "example_tool",
  "arguments": {}
}
```

The PC side should respond with:

```json
{
  "type": "tool_result",
  "call_id": "example-call-id",
  "ok": true,
  "result": {
    "data": {}
  }
}
```

## Important Files

- `main.py`: daemon entrypoint
- `run_ai.py`: direct instruction entrypoint
- `web_server.py`: local dashboard and chat server
- `config.py`: environment and file path configuration
- `agent/cerebras_agent.py`: AI call orchestration
- `agent/instruction_runner.py`: shared direct-instruction runner for CLI and Web chat
- `agent/dynamic_tool_rag.py`: Dynamic Tool Retrieval and Tool Calling layer
- `agent/tool_registry.py`: available Tool schemas
- `agent/pc_tool_bridge.py`: persistent PC Tool bridge
- `agent/memory.py`: today's memory and long-term memory
- `agent/self_model.py`: self-model and current self-state
- `agent/social_needs.py`: social need state and dynamic prompt injection
- `agent/mcp_client.py`: XMCP install/start and HTTP MCP tool client
- `agent/autonomy_runtime.py`: self-call queue and long-term goals
- `agent/audit_log.py`: human-readable audit logging
- `scheduler/scheduler.py`: periodic jobs

## Runtime Data

- `agent_data/memory.md`: today's memory
- `agent_data/long_term_memory.md`: selected durable memory
- `agent_data/self.md`: stable self-model
- `agent_data/state.md`: current self-state
- `agent_data/social_needs.json`: internal social need state
- `agent_data/autonomy_queue.jsonl`: scheduled self-calls
- `agent_data/long_term_goals.md`: long-term autonomous goals
- `agent_data/logs/`: audit and runtime logs
- `agent_data/archive/`: archived daily memory

`agent_data/logs/`, `agent_data/archive/`, `agent_data/social_needs.json`, and generated task outputs are ignored by Git.

## Validation

```powershell
.\.venv\Scripts\python -m compileall -q main.py run_ai.py web_server.py config.py agent scheduler tasks
.\.venv\Scripts\python -m py_compile web_server.py agent\instruction_runner.py
.\.venv\Scripts\python run_ai.py --instruction "俳句を作って"
```
