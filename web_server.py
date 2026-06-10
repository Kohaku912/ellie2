"""Local read-only state dashboard and chat server for Ellie."""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.autonomy_runtime import AutonomyRuntime, get_autonomy_status
from agent.instruction_runner import InstructionRunner
from agent.logging_utils import configure_utf8_stdio
from agent.playwright_mcp import get_playwright_status
from agent.time_utils import agent_tz, isoformat_local
from agent.pc_tool_bridge import (
    get_pc_tool_bridge_status,
    start_pc_tool_bridge_server,
    stop_pc_tool_bridge_server,
)
from config import (
    AGENT_NAME,
    AGENT_TIMEZONE,
    CEREBRAS_MODEL,
    LOG_DIR,
    LONG_TERM_MEMORY_FILE,
    MEMORY_DIR,
    MEMORY_FILE,
    PLAYWRIGHT_DIR,
    PLAYWRIGHT_MCP_AUTO_INSTALL,
    PLAYWRIGHT_MCP_BROWSER,
    PLAYWRIGHT_MCP_ENABLED,
    PLAYWRIGHT_MCP_HEADLESS,
    PLAYWRIGHT_MCP_HOST,
    PLAYWRIGHT_MCP_PORT,
    PLAYWRIGHT_MCP_SERVER_URL,
    PLAYWRIGHT_STORAGE_STATE_FILE,
    PLAYWRIGHT_USER_DATA_DIR,
    SELF_FILE,
    SELF_STATE_FILE,
    SOCIAL_NEEDS_FILE,
    WEB_HOST,
    WEB_PORT,
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
STATE_FILE_MAX_CHARS = 20000
AUDIT_STATE_MAX_CHARS = 12000

app = FastAPI(title=f"{AGENT_NAME} Web Dashboard")
_RUNNER: Optional[InstructionRunner] = None
_AUTONOMY_RUNTIME: Optional[AutonomyRuntime] = None
_CHAT_LOCK = threading.Lock()
_STARTUP_BRIDGE_ERROR: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


@app.on_event("startup")
def _startup() -> None:
    global _STARTUP_BRIDGE_ERROR, _AUTONOMY_RUNTIME
    try:
        start_pc_tool_bridge_server()
        _STARTUP_BRIDGE_ERROR = None
    except Exception as error:
        _STARTUP_BRIDGE_ERROR = str(error)
        logger.warning("PC tool bridge could not be started for web dashboard: %s", error)
    _AUTONOMY_RUNTIME = AutonomyRuntime(lambda: _get_runner().agent)
    _AUTONOMY_RUNTIME.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    if _AUTONOMY_RUNTIME:
        _AUTONOMY_RUNTIME.stop()
    stop_pc_tool_bridge_server()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    _ensure_local_request(request)
    return HTMLResponse(INDEX_HTML)


@app.get("/api/state")
def api_state(request: Request) -> JsonDict:
    _ensure_local_request(request)
    return build_state_snapshot()


@app.get("/api/logs/audit")
def api_audit_log(
    request: Request,
    chars: int = Query(default=40000, ge=1000, le=200000),
) -> JsonDict:
    _ensure_local_request(request)
    return _read_latest_audit_log(chars, include_entries=True)


@app.post("/api/chat")
def api_chat(request: Request, payload: ChatRequest) -> JsonDict:
    _ensure_local_request(request)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is empty")

    with _CHAT_LOCK:
        runner = _get_runner()
        return runner.chat(message)


def build_state_snapshot() -> JsonDict:
    """Build a safe read-only snapshot of the agent state."""
    audit_log = _read_latest_audit_log(AUDIT_STATE_MAX_CHARS)
    bridge_status = get_pc_tool_bridge_status()
    return {
        "generated_at": isoformat_local(),
        "config": _safe_config_snapshot(),
        "files": {
            "memory": _read_text_file(MEMORY_FILE),
            "long_term_memory": _read_text_file(LONG_TERM_MEMORY_FILE),
            "self": _read_text_file(SELF_FILE),
            "state": _read_text_file(SELF_STATE_FILE),
            "social_needs": _read_json_file(SOCIAL_NEEDS_FILE),
            "long_term_goals": _read_text_file(MEMORY_DIR / "long_term_goals.md"),
        },
        "pc_bridge": {
            **bridge_status,
            "startup_error": _STARTUP_BRIDGE_ERROR or bridge_status.get("startup_error"),
        },
        "playwright": get_playwright_status(),
        "autonomy": get_autonomy_status(),
        "tools": _tool_registry_snapshot(),
        "logs": {
            "latest_audit": audit_log,
            "files": _log_file_overview(),
        },
    }


def _get_runner() -> InstructionRunner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = InstructionRunner()
    return _RUNNER


def _ensure_local_request(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if client_host not in LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="This dashboard only accepts localhost connections.")


def _safe_config_snapshot() -> JsonDict:
    return {
        "agent_name": AGENT_NAME,
        "agent_timezone": AGENT_TIMEZONE,
        "cerebras_model": CEREBRAS_MODEL,
        "web": {
            "host": WEB_HOST,
            "port": WEB_PORT,
            "localhost_only": True,
        },
        "paths": {
            "memory_dir": str(MEMORY_DIR),
            "log_dir": str(LOG_DIR),
        },
        "playwright": {
            "enabled": PLAYWRIGHT_MCP_ENABLED,
            "auto_install": PLAYWRIGHT_MCP_AUTO_INSTALL,
            "server_url": PLAYWRIGHT_MCP_SERVER_URL,
            "host": PLAYWRIGHT_MCP_HOST,
            "port": PLAYWRIGHT_MCP_PORT,
            "browser": PLAYWRIGHT_MCP_BROWSER,
            "headless": PLAYWRIGHT_MCP_HEADLESS,
            "install_dir": str(PLAYWRIGHT_DIR),
            "user_data_dir": str(PLAYWRIGHT_USER_DATA_DIR),
            "storage_state_file": str(PLAYWRIGHT_STORAGE_STATE_FILE),
        },
    }


def _read_text_file(path: Path, max_chars: int = STATE_FILE_MAX_CHARS) -> JsonDict:
    info = _file_info(path)
    if not path.exists():
        return {**info, "content": "", "truncated": False}

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as error:
        return {**info, "content": "", "truncated": False, "error": str(error)}

    truncated = len(text) > max_chars
    content = text[-max_chars:] if truncated else text
    return {**info, "content": content, "truncated": truncated}


def _read_json_file(path: Path) -> JsonDict:
    info = _file_info(path)
    if not path.exists():
        return {**info, "data": None, "content": "", "truncated": False}

    text_data = _read_text_file(path)
    try:
        data = json.loads(str(text_data.get("content") or "{}"))
    except json.JSONDecodeError as error:
        data = None
        text_data["error"] = str(error)
    return {**text_data, "data": data}


def _file_info(path: Path) -> JsonDict:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "modified_at": None,
        }
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=agent_tz()).isoformat(),
    }

def _read_latest_audit_log(max_chars: int, include_entries: bool = False) -> JsonDict:
    latest = _latest_audit_path()
    if latest is None:
        empty = {
            "path": None,
            "exists": False,
            "size_bytes": 0,
            "modified_at": None,
            "content": "",
            "truncated": False,
        }
        if include_entries:
            empty["entries"] = []
        return empty

    payload = _read_text_file(latest, max_chars=max_chars)
    if include_entries:
        payload["entries"] = _parse_audit_entries(str(payload.get("content") or ""))
    return payload


def _latest_audit_path() -> Optional[Path]:
    candidates = sorted(
        LOG_DIR.glob("ai_audit_*.md"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _log_file_overview() -> list[JsonDict]:
    if not LOG_DIR.exists():
        return []

    files = sorted(
        [path for path in LOG_DIR.iterdir() if path.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return [_file_info(path) for path in files[:20]]


def _tool_registry_snapshot() -> JsonDict:
    from agent.tool_registry import PC_TOOL_DEFINITIONS, get_available_tool_definitions

    def summarize(tool: Any) -> JsonDict:
        parameters = tool.parameters if isinstance(tool.parameters, dict) else {}
        return {
            "name": tool.name,
            "description": tool.description,
            "tags": list(tool.tags),
            "handler_name": tool.handler_name,
            "required_arguments": parameters.get("required", []),
        }

    available_tools = get_available_tool_definitions()
    return {
        "total_count": len(available_tools),
        "pc_tool_count": len(PC_TOOL_DEFINITIONS),
        "playwright_tool_count": len([tool for tool in available_tools if tool.name.startswith("playwright__")]),
        "tools": [summarize(tool) for tool in available_tools],
    }


def _parse_audit_entries(content: str) -> list[JsonDict]:
    entries: list[JsonDict] = []
    if not content.strip():
        return entries

    sections = [section.strip() for section in re.split(r"(?m)^---\s*$", content) if section.strip()]
    for section in sections:
        lines = [line.rstrip() for line in section.splitlines() if line.strip()]
        if not lines:
            continue

        first_line = lines[0]
        if not (first_line.startswith("AI Call") or first_line.startswith("Tool Call")):
            continue

        entry: JsonDict = {
            "title": first_line,
            "timestamp": "",
            "kind": "",
            "status": "",
            "duration_ms": "",
            "raw": section,
        }
        if first_line.startswith("AI Call"):
            entry["kind"] = "ai"
        elif first_line.startswith("Tool Call"):
            entry["kind"] = "tool"

        for line in lines[1:]:
            if not line.startswith("- "):
                continue
            key, _, value = line[2:].partition(":")
            normalized_key = key.strip().replace(" ", "_")
            normalized_value = value.strip().strip("`")
            entry[normalized_key] = normalized_value
            if normalized_key == "timestamp":
                entry["timestamp"] = normalized_value
            elif normalized_key == "status":
                entry["status"] = normalized_value
            elif normalized_key == "duration_ms":
                entry["duration_ms"] = normalized_value

        entries.append(entry)

    return entries


def _loopback_host(host: str) -> str:
    if host in {"127.0.0.1", "localhost", "::1"}:
        return host
    logger.warning("WEB_HOST=%s is not loopback; forcing 127.0.0.1 for safety.", host)
    return "127.0.0.1"


INDEX_HTML = r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ellie Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #10131a;
      --panel: #171b24;
      --panel-2: #202634;
      --text: #edf1f7;
      --muted: #9aa7bd;
      --accent: #8cc8ff;
      --good: #82e6a3;
      --warn: #ffd27d;
      --bad: #ff8e8e;
      --border: #2c3445;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at top left, #1c2940 0, var(--bg) 34rem);
      color: var(--text);
      font-family: "Segoe UI", "Yu Gothic UI", system-ui, sans-serif;
    }
    header {
      padding: 24px clamp(16px, 4vw, 40px);
      display: flex;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border);
      backdrop-filter: blur(8px);
    }
    h1 { margin: 0; font-size: 24px; }
    .subtitle { color: var(--muted); margin-top: 6px; font-size: 13px; }
    button {
      background: var(--accent);
      color: #07111e;
      border: none;
      border-radius: 10px;
      padding: 10px 14px;
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled { opacity: .55; cursor: wait; }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 18px;
      padding: 18px clamp(16px, 4vw, 40px) 40px;
    }
    section, .card {
      background: color-mix(in srgb, var(--panel) 94%, transparent);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 18px 50px #0004;
    }
    .chat { display: flex; flex-direction: column; min-height: 72vh; }
    .chat-log {
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow: auto;
      flex: 1;
    }
    .bubble {
      padding: 12px 14px;
      border-radius: 14px;
      white-space: pre-wrap;
      line-height: 1.55;
      border: 1px solid var(--border);
    }
    .user { background: #28405d; align-self: flex-end; max-width: 92%; }
    .ai { background: var(--panel-2); align-self: flex-start; max-width: 92%; }
    .meta { color: var(--muted); font-size: 12px; margin-top: 6px; }
    form {
      display: grid;
      gap: 10px;
      padding: 14px;
      border-top: 1px solid var(--border);
    }
    textarea {
      width: 100%;
      min-height: 92px;
      resize: vertical;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #0d1118;
      color: var(--text);
      padding: 12px;
      font: inherit;
    }
    .dashboard {
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .card { padding: 14px; overflow: hidden; }
    .card h2 {
      margin: 0 0 10px;
      font-size: 15px;
      letter-spacing: .02em;
    }
    .status-line {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 8px;
    }
    .pill {
      display: inline-flex;
      gap: 6px;
      align-items: center;
      background: #101722;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 5px 9px;
      color: var(--muted);
      font-size: 12px;
    }
    .pill.good { color: var(--good); }
    .pill.warn { color: var(--warn); }
    .pill.bad { color: var(--bad); }
    pre {
      margin: 0;
      max-height: 320px;
      overflow: auto;
      padding: 12px;
      background: #0d1118;
      border: 1px solid var(--border);
      border-radius: 12px;
      color: #dbe7ff;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.5;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .tools {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      max-height: 150px;
      overflow: auto;
    }
    .tool {
      padding: 5px 8px;
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--muted);
      background: #101722;
      font-size: 12px;
    }
    .audit-list {
      display: grid;
      gap: 10px;
    }
    .audit-entry {
      border: 1px solid var(--border);
      border-radius: 12px;
      background: #0d1118;
      overflow: hidden;
    }
    .audit-entry summary {
      list-style: none;
      cursor: pointer;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      padding: 12px;
    }
    .audit-entry summary::-webkit-details-marker {
      display: none;
    }
    .audit-entry summary:hover {
      background: #111827;
    }
    .audit-title {
      font-weight: 700;
      color: var(--text);
    }
    .audit-time {
      color: var(--accent);
      font-size: 12px;
    }
    .audit-meta {
      color: var(--muted);
      font-size: 12px;
    }
    .audit-body {
      padding: 0 12px 12px;
    }
    @media (max-width: 960px) {
      main { grid-template-columns: 1fr; }
      .chat { min-height: 58vh; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Ellie Dashboard</h1>
      <div class="subtitle">読み取り専用の状態ビュー + ローカルAIチャット</div>
    </div>
    <button id="refresh">状態を更新</button>
  </header>
  <main>
    <section class="chat">
      <div id="chatLog" class="chat-log">
        <div class="bubble ai">こんにちは。ここからEllieへ直接話しかけられます。状態パネルは読み取り専用です。</div>
      </div>
      <form id="chatForm">
        <textarea id="message" placeholder="例: 俳句を作って"></textarea>
        <button id="send" type="submit">送信</button>
      </form>
    </section>
    <div class="dashboard">
      <div class="card">
        <h2>概要</h2>
        <div id="overview" class="status-line"></div>
        <pre id="config"></pre>
      </div>
      <div class="grid">
        <div class="card">
          <h2>PC Bridge</h2>
          <pre id="pcBridge"></pre>
        </div>
        <div class="card">
          <h2>社会的欲求</h2>
          <pre id="socialNeeds"></pre>
        </div>
      </div>
      <div class="card">
        <h2>Tool Registry</h2>
        <div id="toolSummary" class="status-line"></div>
        <div id="tools" class="tools"></div>
      </div>
      <div class="grid">
        <div class="card">
          <h2>今日の記憶</h2>
          <pre id="memory"></pre>
        </div>
        <div class="card">
          <h2>長期記憶</h2>
          <pre id="longTermMemory"></pre>
        </div>
      </div>
      <div class="grid">
        <div class="card">
          <h2>自己モデル</h2>
          <pre id="selfModel"></pre>
        </div>
        <div class="card">
          <h2>現在の自己状態</h2>
          <pre id="selfState"></pre>
        </div>
      </div>
      <div class="card">
        <h2>最新監査ログ</h2>
        <div id="auditLogMeta" class="meta"></div>
        <div id="auditLogList" class="audit-list"></div>
      </div>
    </div>
  </main>
  <script>
    const state = { busy: false };
    const el = (id) => document.getElementById(id);
    const stringify = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
    const setPre = (id, value) => { el(id).textContent = stringify(value ?? ""); };
    const addBubble = (kind, text, meta = "") => {
      const bubble = document.createElement("div");
      bubble.className = `bubble ${kind}`;
      bubble.textContent = text;
      if (meta) {
        const metaLine = document.createElement("div");
        metaLine.className = "meta";
        metaLine.textContent = meta;
        bubble.appendChild(metaLine);
      }
      el("chatLog").appendChild(bubble);
      el("chatLog").scrollTop = el("chatLog").scrollHeight;
    };
    const pill = (text, tone = "") => `<span class="pill ${tone}">${text}</span>`;

    async function refreshState() {
      const [stateResponse, auditResponse] = await Promise.all([
        fetch("/api/state"),
        fetch("/api/logs/audit?chars=80000")
      ]);
      if (!stateResponse.ok) throw new Error(await stateResponse.text());
      if (!auditResponse.ok) throw new Error(await auditResponse.text());
      const data = await stateResponse.json();
      const auditData = await auditResponse.json();
      const bridge = data.pc_bridge || {};
      const autonomy = data.autonomy || {};
      const tools = data.tools || {};
      el("overview").innerHTML = [
        pill(`generated: ${data.generated_at || "-"}`),
        pill(`PC clients: ${bridge.client_count || 0}`, bridge.client_count ? "good" : "warn"),
        pill(`autonomy: ${autonomy.started ? "on" : "standby"}`, autonomy.owns_lock ? "good" : "warn"),
        pill(`self queue: ${autonomy.pending_count || 0}`),
        pill(`tools: ${tools.total_count || 0}`),
        pill(`model: ${(data.config || {}).cerebras_model || "-"}`)
      ].join("");
      setPre("config", data.config);
      setPre("pcBridge", bridge);
      setPre("socialNeeds", ((data.files || {}).social_needs || {}).data ?? ((data.files || {}).social_needs || {}).content);
      setPre("memory", ((data.files || {}).memory || {}).content);
      setPre(
        "longTermMemory",
        [
          ((data.files || {}).long_term_memory || {}).content || "",
          ((data.files || {}).long_term_goals || {}).content ? "\n\n--- 長期目標 ---\n" + ((data.files || {}).long_term_goals || {}).content : ""
        ].join("")
      );
      setPre("selfModel", ((data.files || {}).self || {}).content);
      setPre("selfState", ((data.files || {}).state || {}).content);
      el("toolSummary").innerHTML = [
        pill(`total: ${tools.total_count || 0}`),
        pill(`PC: ${tools.pc_tool_count || 0}`)
      ].join("");
      el("tools").innerHTML = (tools.tools || [])
        .map((tool) => `<span class="tool" title="${escapeHtml(tool.description || "")}">${escapeHtml(tool.name || "")}</span>`)
        .join("");
      renderAuditLog(auditData);
    }

    function escapeHtml(text) {
      return String(text).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#039;"
      }[char]));
    }

    function renderAuditLog(auditData) {
      const entries = auditData.entries || [];
      const meta = [];
      if (auditData.modified_at) meta.push(`updated ${auditData.modified_at}`);
      if (auditData.path) meta.push(auditData.path);
      if (auditData.truncated) meta.push("truncated");
      el("auditLogMeta").textContent = meta.join(" / ");

      if (!entries.length) {
        el("auditLogList").innerHTML = `<pre>${escapeHtml(auditData.content || "No audit log yet.")}</pre>`;
        return;
      }

      el("auditLogList").innerHTML = entries.map((entry, index) => {
        const title = escapeHtml(entry.title || entry.kind || "log");
        const timestamp = escapeHtml(entry.timestamp || "unknown time");
        const status = escapeHtml(entry.status || "unknown");
        const duration = escapeHtml(entry.duration_ms ? `${entry.duration_ms}ms` : "");
        const detail = escapeHtml(entry.raw || "");
        return `
          <details class="audit-entry"${index === 0 ? " open" : ""}>
            <summary>
              <span class="audit-title">${title}</span>
              <span class="audit-time">${timestamp}</span>
              <span class="audit-meta">${status}</span>
              <span class="audit-meta">${duration}</span>
            </summary>
            <div class="audit-body">
              <pre>${detail}</pre>
            </div>
          </details>
        `;
      }).join("");
    }

    el("refresh").addEventListener("click", () => refreshState().catch((error) => addBubble("ai", `状態更新エラー: ${error.message}`)));
    el("chatForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = el("message").value.trim();
      if (!message || state.busy) return;
      state.busy = true;
      el("send").disabled = true;
      el("message").value = "";
      addBubble("user", message);
      const started = performance.now();
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));
        const elapsed = Math.round(performance.now() - started);
        addBubble("ai", data.answer || data.error || data.status || "", `${data.status || "unknown"} / ${data.duration_ms ?? elapsed}ms / trace ${data.trace_id || "-"}`);
        await refreshState();
      } catch (error) {
        addBubble("ai", `チャットエラー: ${error.message}`);
      } finally {
        state.busy = false;
        el("send").disabled = false;
        el("message").focus();
      }
    });

    refreshState().catch((error) => addBubble("ai", `状態更新エラー: ${error.message}`));
  </script>
</body>
</html>
"""


def main() -> int:
    configure_utf8_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    host = _loopback_host(WEB_HOST)
    logger.info("Starting Ellie web dashboard at http://%s:%s", host, WEB_PORT)
    uvicorn.run(app, host=host, port=WEB_PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
