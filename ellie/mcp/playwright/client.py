"""Playwright MCP client and manager for browser automation."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from shutil import which
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ellie.logging.audit_log import get_audit_logger
from ellie.tools.dynamic_retrieval import ToolDefinition
from ellie.time_utils import isoformat_local
from ellie.config import (
    LOG_DIR,
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
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
PLAYWRIGHT_TOOL_PREFIX = "playwright__"
_PLAYWRIGHT_MANAGER: "PlaywrightMcpManager | None" = None
_PLAYWRIGHT_CLIENT: "PlaywrightMcpClient | None" = None


class PlaywrightMcpManager:
    """Install/start the official Playwright MCP server and keep a persistent profile."""

    def __init__(
        self,
        enabled: bool = PLAYWRIGHT_MCP_ENABLED,
        auto_install: bool = PLAYWRIGHT_MCP_AUTO_INSTALL,
        host: str = PLAYWRIGHT_MCP_HOST,
        port: int = PLAYWRIGHT_MCP_PORT,
        server_url: str = PLAYWRIGHT_MCP_SERVER_URL,
        browser: str = PLAYWRIGHT_MCP_BROWSER,
        headless: bool = PLAYWRIGHT_MCP_HEADLESS,
        user_data_dir: Path = PLAYWRIGHT_USER_DATA_DIR,
        storage_state_file: Path = PLAYWRIGHT_STORAGE_STATE_FILE,
    ):
        self.enabled = enabled
        self.auto_install = auto_install
        self.host = host
        self.port = port
        self.server_url = server_url
        self.browser = browser
        self.headless = headless
        self.user_data_dir = Path(user_data_dir)
        self.storage_state_file = Path(storage_state_file)
        self._process: subprocess.Popen[str] | None = None
        self._last_status: JsonDict = {}
        self._last_audited_status_key = ""

    def ensure_ready(self) -> JsonDict:
        if not self.enabled:
            return self._remember({"ok": False, "enabled": False, "status": "disabled"})

        if not self.auto_install:
            return self._remember(
                self._base_status(ok=False, status="not_running", error="PLAYWRIGHT_MCP_AUTO_INSTALL is false and the Playwright MCP process is not running.")
            )

        start_status = self._start_server()
        if not start_status.get("ok"):
            return self._remember({**self._base_status(ok=False, status="start_failed"), **start_status})

        return self._remember(self._base_status(ok=True, status="started"))

    def get_status(self) -> JsonDict:
        if self._process and self._process.poll() is None:
            status = dict(self._last_status) if self._last_status else self._base_status(ok=True, status="running")
        else:
            status = dict(self._last_status) if self._last_status else self.ensure_ready()
        status["process_running"] = bool(self._process and self._process.poll() is None)
        return status

    def _start_server(self) -> JsonDict:
        if self._process and self._process.poll() is None:
            return {"ok": True, "already_running": True}

        PLAYWRIGHT_DIR.mkdir(parents=True, exist_ok=True)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_file.parent.mkdir(parents=True, exist_ok=True)

        log_path = LOG_DIR / "playwright_mcp.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8", errors="replace")
        launcher = _resolve_npx_launcher()
        if launcher is None:
            log_handle.close()
            return {
                "ok": False,
                "error": "npx が見つかりません。Node.js / npm をインストールして PATH に追加するか、PLAYWRIGHT_MCP_ENABLED=false にしてください。",
            }

        browser_install_status = self._ensure_browser_installed(launcher)
        if not browser_install_status.get("ok"):
            log_handle.close()
            return {
                "ok": False,
                "error": browser_install_status.get("error", "Failed to install Playwright browser."),
                "browser_install": browser_install_status,
            }

        command = [
            launcher,
            "-y",
            "@playwright/mcp@latest",
            "--browser",
            self.browser,
            "--user-data-dir",
            str(self.user_data_dir),
        ]
        if self.headless:
            command.append("--headless")
        if self.storage_state_file.exists():
            command.extend(["--storage-state", str(self.storage_state_file)])

        env = os.environ.copy()
        env.setdefault("PLAYWRIGHT_MCP_INIT_SCRIPT", "")
        try:
            self._process = subprocess.Popen(
                command,
                cwd=PLAYWRIGHT_DIR,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_handle,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log_handle.close()
        except FileNotFoundError as error:
            log_handle.close()
            return {
                "ok": False,
                "error": f"Playwright MCP を起動できませんでした: {error}",
                "launcher": launcher,
                "command": command,
            }
        status = {
            "ok": True,
            "pid": self._process.pid,
            "log_path": str(log_path),
            "server_url": self.server_url,
            "command": command,
        }
        self._audit_manager("start", status)
        return status

    def _ensure_browser_installed(self, launcher: str) -> JsonDict:
        browser_name = "chrome-for-testing"
        marker = PLAYWRIGHT_DIR / ".browser_installed"
        if marker.exists():
            cached_text = ""
            try:
                cached_text = marker.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                cached_text = ""
            if cached_text and browser_name in cached_text and "failed" not in cached_text.casefold():
                return {"ok": True, "cached": True, "browser": browser_name}

        command = [
            launcher,
            "-y",
            "@playwright/mcp",
            "install-browser",
            browser_name,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=PLAYWRIGHT_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=900,
            )
        except FileNotFoundError as error:
            return {"ok": False, "error": f"Browser install launcher not found: {error}", "command": command}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Playwright browser installation timed out.", "command": command}

        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"Playwright browser installation failed: {result.stderr or result.stdout or 'unknown error'}",
                "command": command,
                "returncode": result.returncode,
            }

        try:
            marker.write_text(f"browser={browser_name}\ninstalled_at={isoformat_local()}\n", encoding="utf-8")
        except Exception:
            logger.debug("Failed to write browser installation marker", exc_info=True)
        return {"ok": True, "command": command, "returncode": result.returncode, "browser": browser_name}

    def _base_status(self, ok: bool, status: str, error: str = "") -> JsonDict:
        return {
            "ok": ok,
            "enabled": self.enabled,
            "status": status,
            "server_url": self.server_url,
            "host": self.host,
            "port": self.port,
            "browser": self.browser,
            "headless": self.headless,
            "user_data_dir": str(self.user_data_dir),
            "storage_state_file": str(self.storage_state_file),
            "auto_install": self.auto_install,
            "error": error,
        }

    def _remember(self, status: JsonDict) -> JsonDict:
        status = {**status, "checked_at": isoformat_local()}
        self._last_status = status
        status_key = f"{status.get('status')}|{status.get('error')}|{status.get('server_url')}"
        if status_key != self._last_audited_status_key:
            self._last_audited_status_key = status_key
            self._audit_manager("status", status)
        return status

    def _audit_manager(self, phase: str, payload: JsonDict) -> None:
        try:
            audit_logger = get_audit_logger()
            audit_logger.log_tool_call(
                tool_name="playwright_mcp_manager",
                trace_id=audit_logger.new_id("playwright-mcp-manager"),
                phase=phase,
                status="completed" if payload.get("ok") else "failed",
                request_payload={
                    "server_url": self.server_url,
                    "browser": self.browser,
                    "host": self.host,
                    "port": self.port,
                },
                response_payload=payload,
                error=str(payload.get("error") or "") or None,
            )
        except Exception:
            logger.debug("Failed to audit Playwright MCP manager event", exc_info=True)


class PlaywrightMcpClient:
    """MCP JSON-RPC client for the Playwright browser automation server."""

    def __init__(self, manager: PlaywrightMcpManager | None = None, server_url: str = PLAYWRIGHT_MCP_SERVER_URL):
        self.manager = manager or get_playwright_manager()
        self.server_url = server_url
        self._request_id = 0
        self._initialized = False
        self._session_id = ""
        self._tool_cache: list[ToolDefinition] = []
        self._tool_cache_at = 0.0
        self._rpc_lock = threading.Lock()

    def list_tools(self) -> list[ToolDefinition]:
        ready = self.manager.ensure_ready()
        if not ready.get("ok"):
            return []
        if self._tool_cache and time.monotonic() - self._tool_cache_at < 60:
            return list(self._tool_cache)

        start = time.time()
        try:
            self._initialize()
            payload = self._rpc("tools/list", {})
            tools: list[ToolDefinition] = []
            for raw_tool in payload.get("tools", []):
                if not isinstance(raw_tool, dict):
                    continue
                definition = _raw_playwright_tool_to_definition(raw_tool)
                if definition is not None:
                    tools.append(definition)
            self._tool_cache = tools
            self._tool_cache_at = time.monotonic()
            self._audit_tool("playwright_tools_list", {}, {"tool_count": len(tools)}, start, "completed")
            return list(tools)
        except Exception as error:
            self._audit_tool("playwright_tools_list", {}, {"error": str(error)}, start, "failed", error=str(error))
            logger.warning("Failed to list Playwright MCP tools: %s", error)
            return []

    def call_tool(self, name: str, arguments: JsonDict) -> JsonDict:
        original_name = strip_playwright_prefix(name)
        start = time.time()
        try:
            ready = self.manager.ensure_ready()
            if not ready.get("ok"):
                result = {"status": "unavailable", "tool": name, "playwright_status": ready}
                self._audit_tool(name, arguments, result, start, "unavailable")
                return result

            self._initialize()
            payload = self._rpc("tools/call", {"name": original_name, "arguments": arguments if isinstance(arguments, dict) else {}})
            result = {
                "status": "completed",
                "tool": name,
                "original_tool": original_name,
                "result": payload,
                "called_at": isoformat_local(),
            }
            self._audit_tool(name, arguments, result, start, "completed")
            return result
        except Exception as error:
            result = {"status": "failed", "tool": name, "original_tool": original_name, "error": str(error)}
            self._audit_tool(name, arguments, result, start, "failed", error=str(error))
            return result

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ellie2", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized", {})
        self._initialized = True

    def _rpc(self, method: str, params: JsonDict) -> JsonDict:
        with self._rpc_lock:
            process = self._ensure_process()
            self._request_id += 1
            request_id = self._request_id
            request_payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            self._write_message(process, request_payload)
            while True:
                message = self._read_message(process)
                if message is None:
                    raise RuntimeError("Playwright MCP process ended unexpectedly.")
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise RuntimeError(message["error"])
                result = message.get("result", {})
                return result if isinstance(result, dict) else {"value": result}

    def _notify(self, method: str, params: JsonDict) -> None:
        with self._rpc_lock:
            process = self._ensure_process()
            payload = {"jsonrpc": "2.0", "method": method, "params": params}
            self._write_message(process, payload)

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self.manager._process and self.manager._process.poll() is None:
            return self.manager._process

        ready = self.manager.ensure_ready()
        if not ready.get("ok") or not self.manager._process:
            raise RuntimeError(str(ready.get("error") or "Playwright MCP is not available."))
        return self.manager._process

    def _write_message(self, process: subprocess.Popen[str], payload: JsonDict) -> None:
        if process.stdin is None:
            raise RuntimeError("Playwright MCP stdin is not available.")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_message(self, process: subprocess.Popen[str]) -> JsonDict | None:
        if process.stdout is None:
            raise RuntimeError("Playwright MCP stdout is not available.")
        while True:
            raw_line = process.stdout.readline()
            if raw_line == "":
                return None
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Ignoring non-JSON Playwright MCP stdout line: %s", line)
                continue
            if isinstance(message, dict):
                return message
            logger.debug("Ignoring non-object Playwright MCP message: %s", message)
            continue

    def _audit_tool(
        self,
        tool_name: str,
        arguments: JsonDict,
        result: JsonDict,
        start_time: float,
        status: str,
        error: str | None = None,
    ) -> None:
        audit_logger = get_audit_logger()
        audit_logger.log_tool_call(
            tool_name=tool_name,
            trace_id=audit_logger.new_id("playwright"),
            phase="playwright_http",
            duration_ms=int((time.time() - start_time) * 1000),
            status=status,
            request_payload={"tool": tool_name, "arguments": arguments},
            response_payload=result,
            error=error,
        )


def get_playwright_manager() -> PlaywrightMcpManager:
    global _PLAYWRIGHT_MANAGER
    if _PLAYWRIGHT_MANAGER is None:
        _PLAYWRIGHT_MANAGER = PlaywrightMcpManager()
    return _PLAYWRIGHT_MANAGER


def get_playwright_client() -> PlaywrightMcpClient:
    global _PLAYWRIGHT_CLIENT
    if _PLAYWRIGHT_CLIENT is None:
        _PLAYWRIGHT_CLIENT = PlaywrightMcpClient(get_playwright_manager())
    return _PLAYWRIGHT_CLIENT


def get_playwright_status() -> JsonDict:
    return get_playwright_manager().get_status()


def get_playwright_tool_definitions() -> list[ToolDefinition]:
    return get_playwright_client().list_tools()


def call_playwright_tool(name: str, arguments: JsonDict) -> JsonDict:
    return get_playwright_client().call_tool(name, arguments)


def is_playwright_tool_name(name: str) -> bool:
    return name.startswith(PLAYWRIGHT_TOOL_PREFIX)


def strip_playwright_prefix(name: str) -> str:
    return name[len(PLAYWRIGHT_TOOL_PREFIX) :] if name.startswith(PLAYWRIGHT_TOOL_PREFIX) else name


def _raw_playwright_tool_to_definition(raw_tool: JsonDict) -> Optional[ToolDefinition]:
    name = str(raw_tool.get("name") or "").strip()
    if not name:
        return None
    description = str(raw_tool.get("description") or f"Playwright browser tool: {name}").strip()
    parameters = raw_tool.get("inputSchema") or raw_tool.get("input_schema") or raw_tool.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "additionalProperties": True}
    lowered_name = name.casefold()
    tags = ["playwright", "browser", "web", name]
    if any(token in lowered_name for token in ("login", "auth", "post", "tweet", "mention", "reply", "x_", "twitter")):
        tags.extend(["twitter", "x", "social", "login", "post", "reaction"])
    return ToolDefinition(
        name=f"{PLAYWRIGHT_TOOL_PREFIX}{name}",
        description=f"Playwright MCP browser automation tool. {description}",
        tags=tags,
        examples=[f"{PLAYWRIGHT_TOOL_PREFIX}{name}"],
        handler_name="playwright_mcp",
        parameters=parameters,
    )


def _resolve_npx_launcher() -> Optional[str]:
    candidates = [
        which("npx"),
        which("npx.cmd"),
        which("npx.exe"),
        str(Path(os.environ.get("APPDATA", "")) / "npm" / "npx.cmd"),
        str(Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "npx.cmd"),
        str(Path(os.environ.get("ProgramFiles(x86)", "")) / "nodejs" / "npx.cmd"),
        str(Path(os.environ.get("LocalAppData", "")) / "Programs" / "nodejs" / "npx.cmd"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None

