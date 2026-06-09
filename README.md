<<<<<<< HEAD
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
=======
# pc_ellie2

Windows PC client for the Ellie event-driven AI agent system.

The client keeps a WebSocket connection to the central server, sends active-window state deltas, and executes JSON Tool Calls locally.

## Run

```powershell
$env:ELLIE_WS_URL = "ws://127.0.0.1:8765/ws/pc"
$env:ELLIE_CLIENT_ID = "windows-main"
$env:ELLIE_POLL_MS = "1000"
cargo run
```

Defaults:

- `ELLIE_WS_URL=ws://127.0.0.1:8765/ws/pc`
- `ELLIE_DISCORD_SOURCE_DIR=C:\Users\kohak\programs\pc_ellie`
- `ELLIE_DISCORD_TOKEN_STORE=.\discord_tokens.json`

Discord credentials are resolved in this order:

1. Process environment
2. `pc_ellie2\.env`
3. `ELLIE_DISCORD_SOURCE_DIR\.env`

`discord_tokens.json` and `.env` are ignored by git.

## Tool Surface

The `hello` message includes a Tool registry. The registry intentionally includes both native pc_ellie2 names and pc_ellie-compatible names for the previous HTTP endpoints.

- System and hardware: `system_snapshot`, `get_processes`, `get_hardware_info`, `get_active_window`, `list_windows`
- pc_ellie-compatible system/hardware/process names: `system`, `system_os`, `system_uptime`, `system_users`, `system_battery`, `hardware_cpu`, `hardware_memory`, `hardware_disks`, `hardware_network`, `processes`, `processes_startup`, `processes_active_window`
- Window control: `focus_window`, `move_resize_window`, `show_window`, `close_window`
- Transparent overlay: `overlay_show`, `overlay_update`, `overlay_hide`, `overlay_clear`, `overlay_status`
- Execution and power: `launch_application`, `execute_shell`, `kill_process`, `shutdown`, `reboot`, `sleep`, `lock_screen`, `logout`
- pc_ellie-compatible control names: `control_execute`, `control_launch`, `control_shutdown`, `control_reboot`, `control_sleep`, `control_lock`, `control_logout`
- Input and utilities: `take_screenshot`, `get_clipboard`, `set_clipboard`, `notify`, `mouse_move`, `mouse_click`, `mouse_scroll`, `keyboard_type`, `keyboard_shortcut`, `media_key`
- pc_ellie-compatible input/utils names: `utils_screenshot`, `utils_get_clipboard`, `utils_set_clipboard`, `utils_notify`, `input_mouse_move`, `input_mouse_click`, `input_mouse_scroll`, `input_keyboard_type`, `input_keyboard_shortcut`, `input_media`
- Files: `list_directory`, `read_file_base64`, `write_file_base64`, `copy_file`, `move_file`, `rename_file`, `delete_path`
- pc_ellie-compatible file names: `files_list`, `files_download`, `files_upload`, `files_copy`, `files_move`, `files_rename`, `files_delete`
- Discord RPC: `discord_status`, `discord_connect`, `discord_disconnect`, `discord_refresh_tokens`, guild/channel/voice/activity helpers, `discord_subscribe`, `discord_unsubscribe`, `discord_command`

Destructive tools are always enabled, matching the requested plan.

## Tool Call Example
>>>>>>> d048c5d (feat: add notification, system info, screenshot, and window management tools)

```json
{
  "type": "tool_call",
<<<<<<< HEAD
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
=======
  "call_id": "shell-001",
  "tool": "execute_shell",
  "arguments": {
    "command": "Write-Output hello"
>>>>>>> d048c5d (feat: add notification, system info, screenshot, and window management tools)
  }
}
```

<<<<<<< HEAD
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
=======
OpenAI-style `function.name` / `function.arguments` messages are also accepted.

## Transparent Overlay

`overlay_show` creates a topmost transparent Win32 overlay window. It uses `WS_EX_TRANSPARENT` and `WS_EX_NOACTIVATE`, so text, images, and shapes do not receive cursor interaction; clicks pass through to the windows underneath.

Every overlay show/update request must include a positive clear time. Use `clear_after_ms` (aliases: `duration_ms`, `ttl_ms`, `erase_after_ms`). The overlay automatically disappears after that duration.

```json
{
  "type": "tool_call",
  "call_id": "overlay-001",
  "tool": "overlay_show",
  "arguments": {
    "x": 0,
    "y": 0,
    "width": 1280,
    "height": 720,
    "opacity": 230,
    "clear_after_ms": 5000,
    "items": [
      { "type": "text", "text": "hello", "x": 40, "y": 40, "size": 32, "color": "#ffffff" },
      { "type": "rect", "x": 32, "y": 96, "width": 240, "height": 120, "color": "#00ff80", "stroke_width": 3 },
      { "type": "ellipse", "x": 320, "y": 96, "width": 160, "height": 120, "color": "#ff4080" },
      { "type": "line", "x1": 32, "y1": 250, "x2": 520, "y2": 250, "color": "#80c0ff", "stroke_width": 2 },
      { "type": "image", "x": 600, "y": 40, "width": 256, "height": 256, "path": "C:\\path\\image.png" }
    ]
  }
}
```

Image items accept either `path` or `data_base64`.

## Source Layout

- `src/ws_client.rs`: WebSocket connection, reconnect, read/write tasks
- `src/tools/`: Tool registry and category implementations
- `src/platform.rs`: Win32 wrappers for windows, foreground state, and ShellExecute
- `src/discord/`: Discord IPC and token store migration
- `src/bin/smoke_harness.rs`: local WebSocket smoke test for PC tools
- `src/bin/discord_smoke.rs`: Discord IPC/token smoke test

## Native APIs

- Active window and windows: `GetForegroundWindow`, `EnumWindows`, `GetWindowTextW`, `GetWindowThreadProcessId`, `QueryFullProcessImageNameW`, `SetForegroundWindow`, `MoveWindow`, `ShowWindow`, `PostMessageW`
- Application launch: `ShellExecuteW`
- Screenshot: `screenshots` crate plus PNG encoding
- Input: `enigo`
- Clipboard: `arboard`
- System inventory: `sysinfo`, `battery`, `local-ip-address`
- Discord: Discord RPC over IPC named pipe, plus OAuth token refresh via Discord API

## Verification

```powershell
cargo fmt
cargo check
cargo test
cargo run --bin smoke_harness
cargo run --bin discord_smoke
```

`smoke_harness` starts a temporary WebSocket server and verifies Tool Calls for screenshot, clipboard, system/process/window info, shell execution, temp-file operations, launch, and kill-process.

`discord_smoke` uses the configured source `.env` and token store, migrates tokens if needed, connects to Discord IPC, and checks status and guild RPC commands. It skips cleanly if Discord prerequisites are unavailable.
>>>>>>> d048c5d (feat: add notification, system info, screenshot, and window management tools)
