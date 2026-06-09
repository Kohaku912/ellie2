"""HTTP MCP client and manager for X/Twitter XMCP."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.audit_log import get_audit_logger
from agent.dynamic_tool_rag import ToolDefinition
from agent.time_utils import isoformat_local
from config import (
    LOG_DIR,
    XMCP_AUTO_INSTALL,
    XMCP_DIR,
    XMCP_ENABLED,
    XMCP_REPO_URL,
    XMCP_SERVER_URL,
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
XMCP_TOOL_PREFIX = "xmcp__"
XMCP_REQUIRED_ENV = (
    "X_OAUTH_CONSUMER_KEY",
    "X_OAUTH_CONSUMER_SECRET",
    "X_BEARER_TOKEN",
)
_XMCP_MANAGER: "XmcpManager | None" = None
_XMCP_CLIENT: "XmcpClient | None" = None


class XmcpManager:
    """Install and start the official X/Twitter XMCP server when configured."""

    def __init__(
        self,
        repo_url: str = XMCP_REPO_URL,
        install_dir: Path = XMCP_DIR,
        server_url: str = XMCP_SERVER_URL,
        enabled: bool = XMCP_ENABLED,
        auto_install: bool = XMCP_AUTO_INSTALL,
    ):
        self.repo_url = repo_url
        self.install_dir = Path(install_dir)
        self.server_url = server_url
        self.enabled = enabled
        self.auto_install = auto_install
        self._process: subprocess.Popen[str] | None = None
        self._last_status: JsonDict = {}
        self._last_audited_status_key = ""

    def ensure_ready(self) -> JsonDict:
        """Ensure XMCP is installed and reachable; never raise for normal setup issues."""
        if not self.enabled:
            return self._remember({"ok": False, "enabled": False, "status": "disabled"})

        env_status = self._env_status()
        if not env_status["ok"]:
            return self._remember(
                {
                    "ok": False,
                    "enabled": True,
                    "status": "missing_credentials",
                    **env_status,
                    "server_url": self.server_url,
                }
            )

        if _probe_mcp_endpoint(self.server_url):
            return self._remember(self._base_status(ok=True, status="connected"))

        if not self.auto_install:
            return self._remember(
                self._base_status(
                    ok=False,
                    status="not_running",
                    error="XMCP_AUTO_INSTALL is false and the MCP endpoint is not reachable.",
                )
            )

        install_status = self._ensure_installed()
        if not install_status.get("ok"):
            return self._remember({**self._base_status(ok=False, status="install_failed"), **install_status})

        start_status = self._start_server()
        if not start_status.get("ok"):
            return self._remember({**self._base_status(ok=False, status="start_failed"), **start_status})

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _probe_mcp_endpoint(self.server_url):
                return self._remember(self._base_status(ok=True, status="started"))
            time.sleep(1)

        return self._remember(
            self._base_status(
                ok=False,
                status="startup_timeout",
                error="XMCP process started but the MCP endpoint did not become reachable.",
            )
        )

    def get_status(self) -> JsonDict:
        """Return the latest known XMCP state."""
        status = dict(self._last_status) if self._last_status else self.ensure_ready()
        status["process_running"] = bool(self._process and self._process.poll() is None)
        return status

    def _ensure_installed(self) -> JsonDict:
        if (self.install_dir / "server.py").exists():
            return {"ok": True, "install_dir": str(self.install_dir), "installed": True}

        self.install_dir.parent.mkdir(parents=True, exist_ok=True)
        if not (self.install_dir / ".git").exists():
            clone_result = _run_command(
                ["git", "clone", self.repo_url, str(self.install_dir)],
                cwd=self.install_dir.parent,
                timeout_seconds=180,
            )
            if not clone_result["ok"]:
                self._audit_manager("clone", clone_result)
                return clone_result

        venv_python = self._venv_python()
        if not venv_python.exists():
            venv_result = _run_command(
                [sys.executable, "-m", "venv", str(self.install_dir / ".venv")],
                cwd=self.install_dir,
                timeout_seconds=180,
            )
            if not venv_result["ok"]:
                self._audit_manager("venv", venv_result)
                return venv_result

        requirements_file = self.install_dir / "requirements.txt"
        if requirements_file.exists():
            pip_result = _run_command(
                [str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)],
                cwd=self.install_dir,
                timeout_seconds=300,
            )
            if not pip_result["ok"]:
                self._audit_manager("pip_install", pip_result)
                return pip_result

        status = {"ok": True, "install_dir": str(self.install_dir), "installed": True}
        self._audit_manager("install", status)
        return status

    def _start_server(self) -> JsonDict:
        if self._process and self._process.poll() is None:
            return {"ok": True, "already_running": True}

        server_file = self.install_dir / "server.py"
        if not server_file.exists():
            return {"ok": False, "error": f"server.py not found in {self.install_dir}"}

        log_path = LOG_DIR / "xmcp_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8", errors="replace")
        env = os.environ.copy()
        env.update(_read_env_file(self.install_dir / ".env"))
        env.setdefault("MCP_HOST", "127.0.0.1")
        env.setdefault("MCP_PORT", "8000")
        self._process = subprocess.Popen(
            [str(self._venv_python()), str(server_file)],
            cwd=self.install_dir,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        status = {
            "ok": True,
            "pid": self._process.pid,
            "log_path": str(log_path),
            "server_url": self.server_url,
        }
        self._audit_manager("start", status)
        return status

    def _env_status(self) -> JsonDict:
        env_file = self.install_dir / ".env"
        env_values = {**os.environ, **_read_env_file(env_file)}
        missing = [key for key in XMCP_REQUIRED_ENV if not str(env_values.get(key) or "").strip()]
        return {
            "ok": not missing,
            "env_file": str(env_file),
            "missing_env": missing,
        }

    def _base_status(self, ok: bool, status: str, error: str = "") -> JsonDict:
        return {
            "ok": ok,
            "enabled": self.enabled,
            "status": status,
            "server_url": self.server_url,
            "install_dir": str(self.install_dir),
            "auto_install": self.auto_install,
            "error": error,
        }

    def _venv_python(self) -> Path:
        if os.name == "nt":
            return self.install_dir / ".venv" / "Scripts" / "python.exe"
        return self.install_dir / ".venv" / "bin" / "python"

    def _remember(self, status: JsonDict) -> JsonDict:
        status = {**status, "checked_at": isoformat_local()}
        self._last_status = status
        status_key = f"{status.get('status')}|{status.get('error')}|{','.join(status.get('missing_env', []) or [])}"
        if status_key != self._last_audited_status_key:
            self._last_audited_status_key = status_key
            self._audit_manager("status", status)
        return status

    def _audit_manager(self, phase: str, payload: JsonDict) -> None:
        try:
            audit_logger = get_audit_logger()
            audit_logger.log_tool_call(
                tool_name="xmcp_manager",
                trace_id=audit_logger.new_id("xmcp-manager"),
                phase=phase,
                status="completed" if payload.get("ok") else "failed",
                request_payload={
                    "repo_url": self.repo_url,
                    "install_dir": str(self.install_dir),
                    "server_url": self.server_url,
                },
                response_payload=payload,
                error=str(payload.get("error") or "") or None,
            )
        except Exception:
            logger.debug("Failed to audit XMCP manager event", exc_info=True)


class XmcpClient:
    """Small JSON-RPC over HTTP MCP client for XMCP."""

    def __init__(self, manager: XmcpManager | None = None, server_url: str = XMCP_SERVER_URL):
        self.manager = manager or get_xmcp_manager()
        self.server_url = server_url
        self._request_id = 0
        self._initialized = False
        self._session_id = ""
        self._tool_cache: list[ToolDefinition] = []
        self._tool_cache_at = 0.0

    def list_tools(self) -> list[ToolDefinition]:
        ready = self.manager.ensure_ready()
        if not ready.get("ok"):
            return []
        if self._tool_cache and time.monotonic() - self._tool_cache_at < 60:
            return list(self._tool_cache)

        start = time.time()
        tools: list[ToolDefinition] = []
        try:
            self._initialize()
            payload = self._rpc("tools/list", {})
            for raw_tool in payload.get("tools", []):
                if not isinstance(raw_tool, dict):
                    continue
                definition = _raw_mcp_tool_to_definition(raw_tool)
                if definition is not None:
                    tools.append(definition)
            self._tool_cache = tools
            self._tool_cache_at = time.monotonic()
            self._audit_tool("xmcp_tools_list", {}, {"tool_count": len(tools)}, start, "completed")
            return list(tools)
        except Exception as error:
            self._audit_tool("xmcp_tools_list", {}, {"error": str(error)}, start, "failed", error=str(error))
            logger.warning("Failed to list XMCP tools: %s", error)
            return []

    def call_tool(self, name: str, arguments: JsonDict) -> JsonDict:
        original_name = strip_xmcp_prefix(name)
        start = time.time()
        try:
            ready = self.manager.ensure_ready()
            if not ready.get("ok"):
                result = {"status": "unavailable", "tool": name, "xmcp_status": ready}
                self._audit_tool(name, arguments, result, start, "unavailable")
                return result
            self._initialize()
            payload = self._rpc(
                "tools/call",
                {
                    "name": original_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                },
            )
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
        try:
            self._notify("notifications/initialized", {})
        except Exception:
            logger.debug("XMCP initialized notification failed", exc_info=True)
        self._initialized = True

    def _rpc(self, method: str, params: JsonDict) -> JsonDict:
        self._request_id += 1
        request_payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        response_payload, headers = _http_json_rpc(self.server_url, request_payload, self._session_id)
        session_id = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        if "error" in response_payload:
            raise RuntimeError(response_payload["error"])
        result = response_payload.get("result", {})
        return result if isinstance(result, dict) else {"value": result}

    def _notify(self, method: str, params: JsonDict) -> None:
        request_payload = {"jsonrpc": "2.0", "method": method, "params": params}
        _http_json_rpc(self.server_url, request_payload, self._session_id)

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
            trace_id=audit_logger.new_id("xmcp"),
            phase="xmcp_http",
            duration_ms=int((time.time() - start_time) * 1000),
            status=status,
            request_payload={"tool": tool_name, "arguments": arguments},
            response_payload=result,
            error=error,
        )


def get_xmcp_manager() -> XmcpManager:
    global _XMCP_MANAGER
    if _XMCP_MANAGER is None:
        _XMCP_MANAGER = XmcpManager()
    return _XMCP_MANAGER


def get_xmcp_client() -> XmcpClient:
    global _XMCP_CLIENT
    if _XMCP_CLIENT is None:
        _XMCP_CLIENT = XmcpClient(get_xmcp_manager())
    return _XMCP_CLIENT


def get_xmcp_status() -> JsonDict:
    return get_xmcp_manager().get_status()


def get_xmcp_tool_definitions() -> list[ToolDefinition]:
    return get_xmcp_client().list_tools()


def call_xmcp_tool(name: str, arguments: JsonDict) -> JsonDict:
    return get_xmcp_client().call_tool(name, arguments)


def is_xmcp_tool_name(name: str) -> bool:
    return name.startswith(XMCP_TOOL_PREFIX)


def strip_xmcp_prefix(name: str) -> str:
    return name[len(XMCP_TOOL_PREFIX) :] if name.startswith(XMCP_TOOL_PREFIX) else name


def _raw_mcp_tool_to_definition(raw_tool: JsonDict) -> Optional[ToolDefinition]:
    name = str(raw_tool.get("name") or "").strip()
    if not name:
        return None
    description = str(raw_tool.get("description") or f"XMCP X/Twitter tool: {name}").strip()
    parameters = raw_tool.get("inputSchema") or raw_tool.get("input_schema") or raw_tool.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "additionalProperties": True}
    return ToolDefinition(
        name=f"{XMCP_TOOL_PREFIX}{name}",
        description=f"XMCP X/Twitter MCP tool. 全権限で利用可能。{description}",
        tags=["xmcp", "x", "twitter", "mcp", "social", "all_allowed", name],
        examples=[f"{XMCP_TOOL_PREFIX}{name}"],
        handler_name="xmcp",
        parameters=parameters,
    )


def _probe_mcp_endpoint(server_url: str) -> bool:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}, "clientInfo": {"name": "ellie2-probe"}}}
        response, _ = _http_json_rpc(server_url, payload, "", timeout_seconds=3)
        return "result" in response or "error" in response
    except Exception:
        return False


def _http_json_rpc(server_url: str, payload: JsonDict, session_id: str = "", timeout_seconds: int = 30) -> tuple[JsonDict, JsonDict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(server_url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw_text = response.read().decode("utf-8", errors="replace")
        response_headers = {key: value for key, value in response.headers.items()}
    return _parse_mcp_http_response(raw_text), response_headers


def _parse_mcp_http_response(raw_text: str) -> JsonDict:
    text = raw_text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            value = line[5:].strip()
            if value and value != "[DONE]":
                data_lines.append(value)
    for candidate in reversed(data_lines):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("MCP HTTP response was not JSON or parseable SSE data.")


def _run_command(command: list[str], cwd: Path, timeout_seconds: int) -> JsonDict:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        return {
            "ok": completed.returncode == 0,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except Exception as error:
        return {"ok": False, "command": command, "error": str(error)}


def _read_env_file(path: Path) -> JsonDict:
    if not path.exists():
        return {}
    values: JsonDict = {}
    try:
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        logger.debug("Failed to read XMCP env file: %s", path, exc_info=True)
    return values
