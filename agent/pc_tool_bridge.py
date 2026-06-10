"""
Persistent WebSocket bridge for sending tool calls to the Windows PC client.

The PC client connects once and stays connected. Tool calls are sent over that
open socket, and matching tool_result messages are routed back to the caller.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.audit_log import get_audit_logger


JsonDict = Dict[str, Any]
logger = logging.getLogger(__name__)


@dataclass
class PCToolBridgeResult:
    """Result of a single PC tool call delivery."""

    ok: bool
    tool_call: JsonDict
    tool_result: Optional[JsonDict] = None
    error: Optional[str] = None


class PersistentPCToolBridge:
    """Background WebSocket server that keeps PC clients connected."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Any = None
        self._ready = threading.Event()
        self._startup_error: Optional[str] = None
        self._clients: Dict[Any, List[JsonDict]] = {}
        self._pending: Dict[str, asyncio.Future[PCToolBridgeResult]] = {}
        self._client_event: Optional[asyncio.Event] = None
        self._send_lock: Optional[asyncio.Lock] = None
        self._state_lock = threading.Lock()
        self._remote_mode = False

    def start(self) -> None:
        """Start the persistent WebSocket server if it is not running."""
        with self._state_lock:
            if self._remote_mode:
                return
            if self._thread and self._thread.is_alive():
                return

            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._run_loop,
                name="pc-tool-bridge",
                daemon=True,
            )
            self._thread.start()

        self._ready.wait(timeout=5)
        if self._startup_error:
            if self._can_proxy_to_existing_bridge(self._startup_error):
                logger.info(
                    "PC tool bridge port is already in use; using remote bridge mode for ws://%s:%s",
                    self.host,
                    self.port,
                )
                self._remote_mode = True
                self._startup_error = None
                return
            raise RuntimeError(self._startup_error)
        self._remote_mode = False

    def stop(self) -> None:
        """Stop the WebSocket server and close all connected clients."""
        if self._remote_mode:
            return

        loop = self._loop
        if not loop or not loop.is_running():
            return

        future = asyncio.run_coroutine_threadsafe(self._stop_async(), loop)
        try:
            future.result(timeout=5)
        except Exception as error:
            logger.warning("Failed to stop PC tool bridge cleanly: %s", error)

    def send_tool_call(self, tool_call: JsonDict, timeout_seconds: int = 30) -> PCToolBridgeResult:
        """Send a tool call over an existing PC WebSocket connection."""
        normalized_call = _normalize_tool_call(tool_call)

        try:
            self.start()
        except Exception as error:
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=str(error),
            )

        if self._remote_mode:
            return self._send_tool_call_via_remote_bridge(normalized_call, timeout_seconds)

        if not self._loop or not self._loop.is_running():
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error="PC tool bridge is not running",
            )

        future = asyncio.run_coroutine_threadsafe(
            self._send_tool_call_async(normalized_call, timeout_seconds),
            self._loop,
        )
        try:
            return future.result(timeout=timeout_seconds + 2)
        except FutureTimeoutError:
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=f"Timed out waiting for PC tool_result after {timeout_seconds}s",
            )
        except Exception as error:
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=str(error),
            )

    def get_status(self) -> JsonDict:
        """Return a read-only status snapshot for dashboards."""
        if self._remote_mode:
            remote_status = self._fetch_remote_status()
            return {
                "host": self.host,
                "port": self.port,
                "started": False,
                "thread_alive": False,
                "loop_running": False,
                "server_running": False,
                "client_count": int(remote_status.get("client_count", 0)),
                "pending_call_count": int(remote_status.get("pending_call_count", 0)),
                "startup_error": self._startup_error,
                "connected_tool_count": int(remote_status.get("connected_tool_count", 0)),
                "connected_tools": remote_status.get("connected_tools", []),
                "remote_mode": True,
                "remote_status_ok": bool(remote_status.get("ok")),
                "remote_error": remote_status.get("error"),
            }

        try:
            client_tool_sets = list(self._clients.values())
            pending_count = len(self._pending)
        except RuntimeError:
            client_tool_sets = []
            pending_count = 0

        connected_tools = sorted(
            {
                str(tool.get("name") or tool.get("tool") or "").strip()
                for tools in client_tool_sets
                for tool in tools
                if isinstance(tool, dict) and str(tool.get("name") or tool.get("tool") or "").strip()
            }
        )

        return {
            "host": self.host,
            "port": self.port,
            "started": self._thread is not None,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "loop_running": bool(self._loop and self._loop.is_running()),
            "server_running": self._server is not None,
            "client_count": len(client_tool_sets),
            "pending_call_count": pending_count,
            "startup_error": self._startup_error,
            "connected_tool_count": len(connected_tools),
            "connected_tools": connected_tools[:100],
            "remote_mode": False,
        }

    def get_connected_tool_definitions(self) -> List[JsonDict]:
        """Return Tool definitions advertised by connected PC clients."""
        if self._remote_mode:
            remote_status = self._fetch_remote_status()
            return [
                {"name": name, "description": f"Connected PC client tool: {name}"}
                for name in remote_status.get("connected_tools", [])
                if isinstance(name, str) and name.strip()
            ]

        try:
            client_tool_sets = list(self._clients.values())
        except RuntimeError:
            client_tool_sets = []

        tools_by_name: Dict[str, JsonDict] = {}
        for tools in client_tool_sets:
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or tool.get("tool") or "").strip()
                if name and name not in tools_by_name:
                    tools_by_name[name] = dict(tool)
        return sorted(tools_by_name.values(), key=lambda tool: str(tool.get("name") or tool.get("tool") or ""))

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client_event = asyncio.Event()
        self._send_lock = asyncio.Lock()
        try:
            self._loop.run_until_complete(self._start_async())
            self._ready.set()
            self._loop.run_forever()
        except Exception as error:
            self._startup_error = str(error)
            self._ready.set()
        finally:
            try:
                self._loop.run_until_complete(self._shutdown_async())
            except Exception as error:
                logger.debug("PC tool bridge shutdown cleanup failed: %s", error)
            self._loop.close()

    async def _start_async(self) -> None:
        try:
            import websockets
        except ImportError as error:
            raise RuntimeError(f"websockets package is required: {error}") from error

        self._server = await websockets.serve(self._handle_client, self.host, self.port)
        logger.info("PC tool bridge listening on ws://%s:%s", self.host, self.port)

    async def _stop_async(self) -> None:
        await self._shutdown_async()
        if self._loop:
            self._loop.stop()

    async def _shutdown_async(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        clients = list(self._clients)
        for websocket in clients:
            try:
                await websocket.close()
            except Exception:
                pass
        self._clients.clear()

        for call_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(
                    PCToolBridgeResult(
                        ok=False,
                        tool_call={"call_id": call_id},
                        error="PC tool bridge stopped",
                    )
                )
        self._pending.clear()

    async def _handle_client(self, websocket: Any) -> None:
        pc_client_registered = False
        try:
            async for message in websocket:
                payload = _loads_json(message)
                if payload is None:
                    continue

                if payload.get("type") == "hello":
                    tools = payload.get("tools", [])
                    if isinstance(tools, list):
                        self._clients[websocket] = [tool for tool in tools if isinstance(tool, dict)]
                        pc_client_registered = True
                        if self._client_event:
                            self._client_event.set()
                        logger.info("PC client connected to persistent tool bridge")
                    continue

                if payload.get("type") == "controller_call":
                    await self._handle_controller_call(payload, websocket)
                    continue

                if payload.get("type") == "controller_status":
                    await self._handle_controller_status(websocket)
                    continue

                if payload.get("type") == "tool_result":
                    await self._handle_tool_result(payload, websocket)
        except Exception as error:
            if pc_client_registered:
                logger.info("PC client disconnected from tool bridge: %s", error)
        finally:
            self._clients.pop(websocket, None)
            if not self._clients and self._client_event:
                self._client_event.clear()

    async def _handle_controller_call(self, payload: JsonDict, websocket: Any) -> None:
        tool_call = payload.get("tool_call")
        if not isinstance(tool_call, dict):
            await websocket.send(
                json.dumps(
                    {
                        "type": "controller_result",
                        "call_id": "",
                        "ok": False,
                        "tool_result": None,
                        "error": "tool_call must be an object",
                    },
                    ensure_ascii=False,
                )
            )
            return

        timeout_seconds = int(payload.get("timeout_seconds") or 30)
        normalized_call = _normalize_tool_call(tool_call)
        result = await self._send_tool_call_async(normalized_call, timeout_seconds)
        await websocket.send(
            json.dumps(
                {
                    "type": "controller_result",
                    "call_id": normalized_call["call_id"],
                    "ok": result.ok,
                    "tool_result": result.tool_result,
                    "error": result.error,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_controller_status(self, websocket: Any) -> None:
        client_tool_sets = list(self._clients.values())
        connected_tools = sorted(
            {
                str(tool.get("name") or tool.get("tool") or "").strip()
                for tools in client_tool_sets
                for tool in tools
                if isinstance(tool, dict) and str(tool.get("name") or tool.get("tool") or "").strip()
            }
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "controller_status_result",
                    "ok": True,
                    "client_count": len(client_tool_sets),
                    "pending_call_count": len(self._pending),
                    "connected_tool_count": len(connected_tools),
                    "connected_tools": connected_tools[:100],
                },
                ensure_ascii=False,
            )
        )

    async def _handle_tool_result(self, payload: JsonDict, websocket: Any) -> None:
        call_id = str(payload.get("call_id") or "")
        future = self._pending.pop(call_id, None)
        if not future or future.done():
            return

        future.set_result(
            PCToolBridgeResult(
                ok=bool(payload.get("ok")),
                tool_call={"call_id": call_id},
                tool_result=payload,
                error=payload.get("error"),
            )
        )

    async def _send_tool_call_async(self, normalized_call: JsonDict, timeout_seconds: int) -> PCToolBridgeResult:
        websocket = await self._wait_for_client(timeout_seconds)
        if websocket is None:
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=f"No PC client connected after {timeout_seconds}s",
            )

        call_id = normalized_call["call_id"]
        future = self._loop.create_future() if self._loop else asyncio.get_running_loop().create_future()
        self._pending[call_id] = future

        try:
            if self._send_lock:
                async with self._send_lock:
                    await websocket.send(json.dumps(normalized_call, ensure_ascii=False))
            else:
                await websocket.send(json.dumps(normalized_call, ensure_ascii=False))
            result = await asyncio.wait_for(future, timeout=timeout_seconds)
            result.tool_call = normalized_call
            return result
        except asyncio.TimeoutError:
            self._pending.pop(call_id, None)
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=f"Timed out waiting for PC tool_result after {timeout_seconds}s",
            )
        except Exception as error:
            self._pending.pop(call_id, None)
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=str(error),
            )

    async def _wait_for_client(self, timeout_seconds: int) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            clients = list(self._clients)
            if clients:
                return clients[-1]

            remaining = max(deadline - time.monotonic(), 0.1)
            if not self._client_event:
                await asyncio.sleep(min(remaining, 0.1))
                continue

            try:
                await asyncio.wait_for(self._client_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
        return None

    def _send_tool_call_via_remote_bridge(self, normalized_call: JsonDict, timeout_seconds: int) -> PCToolBridgeResult:
        try:
            return asyncio.run(self._send_tool_call_via_remote_bridge_async(normalized_call, timeout_seconds))
        except Exception as error:
            return PCToolBridgeResult(
                ok=False,
                tool_call=normalized_call,
                error=str(error),
            )

    async def _send_tool_call_via_remote_bridge_async(
        self,
        normalized_call: JsonDict,
        timeout_seconds: int,
    ) -> PCToolBridgeResult:
        try:
            import websockets
        except ImportError as error:
            raise RuntimeError(f"websockets package is required: {error}") from error

        uri = f"ws://{self.host}:{self.port}"
        async with websockets.connect(uri) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "controller_call",
                        "tool_call": normalized_call,
                        "timeout_seconds": timeout_seconds,
                    },
                    ensure_ascii=False,
                )
            )
            raw_response = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds + 2)
            payload = _loads_json(raw_response)
            if not payload or payload.get("type") != "controller_result":
                return PCToolBridgeResult(
                    ok=False,
                    tool_call=normalized_call,
                    error="Remote bridge returned an invalid controller_result payload",
                )
            return PCToolBridgeResult(
                ok=bool(payload.get("ok")),
                tool_call=normalized_call,
                tool_result=payload.get("tool_result"),
                error=payload.get("error"),
            )

    def _fetch_remote_status(self) -> JsonDict:
        try:
            return asyncio.run(self._fetch_remote_status_async())
        except Exception as error:
            return {
                "ok": False,
                "client_count": 0,
                "pending_call_count": 0,
                "connected_tool_count": 0,
                "connected_tools": [],
                "error": str(error),
            }

    async def _fetch_remote_status_async(self) -> JsonDict:
        try:
            import websockets
        except ImportError as error:
            raise RuntimeError(f"websockets package is required: {error}") from error

        uri = f"ws://{self.host}:{self.port}"
        async with websockets.connect(uri) as websocket:
            await websocket.send(json.dumps({"type": "controller_status"}, ensure_ascii=False))
            raw_response = await asyncio.wait_for(websocket.recv(), timeout=5)
            payload = _loads_json(raw_response)
            if not payload or payload.get("type") != "controller_status_result":
                return {
                    "ok": False,
                    "client_count": 0,
                    "pending_call_count": 0,
                    "connected_tool_count": 0,
                    "connected_tools": [],
                    "error": "Remote bridge returned an invalid controller_status_result payload",
                }
            return payload

    def _can_proxy_to_existing_bridge(self, startup_error: str) -> bool:
        lowered = startup_error.lower()
        return "address already in use" in lowered or "10048" in lowered


_BRIDGE: Optional[PersistentPCToolBridge] = None
_BRIDGE_LOCK = threading.Lock()


def get_pc_tool_bridge(host: str = "127.0.0.1", port: int = 8765) -> PersistentPCToolBridge:
    """Return the process-wide persistent PC tool bridge."""
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None or _BRIDGE.host != host or _BRIDGE.port != port:
            _BRIDGE = PersistentPCToolBridge(host=host, port=port)
        return _BRIDGE


def start_pc_tool_bridge_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the persistent PC tool bridge server."""
    get_pc_tool_bridge(host, port).start()


def stop_pc_tool_bridge_server() -> None:
    """Stop the persistent PC tool bridge server."""
    bridge = _BRIDGE
    if bridge:
        bridge.stop()


def get_pc_tool_bridge_status(host: str = "127.0.0.1", port: int = 8765) -> JsonDict:
    """Return bridge status, starting the WebSocket server if it has not been created yet."""
    bridge = _BRIDGE
    if bridge is None:
        try:
            start_pc_tool_bridge_server(host, port)
        except Exception as error:
            return {
                "host": host,
                "port": port,
                "started": False,
                "thread_alive": False,
                "loop_running": False,
                "server_running": False,
                "client_count": 0,
                "pending_call_count": 0,
                "startup_error": str(error),
                "connected_tool_count": 0,
                "connected_tools": [],
                "auto_start_failed": True,
            }
        bridge = _BRIDGE
        if bridge is None:
            return {
                "host": host,
                "port": port,
                "started": False,
                "thread_alive": False,
                "loop_running": False,
                "server_running": False,
                "client_count": 0,
                "pending_call_count": 0,
                "startup_error": None,
                "connected_tool_count": 0,
                "connected_tools": [],
                "auto_start_failed": True,
            }
    return bridge.get_status()


def get_connected_pc_tools(host: str = "127.0.0.1", port: int = 8765) -> List[JsonDict]:
    """Return connected PC Tool definitions without starting the bridge."""
    bridge = _BRIDGE
    if bridge is None:
        return []
    if bridge.host != host or bridge.port != port:
        return []
    return bridge.get_connected_tool_definitions()


def get_connected_pc_tool_names(host: str = "127.0.0.1", port: int = 8765) -> List[str]:
    """Return names of connected PC Tools without starting the bridge."""
    names = []
    for tool in get_connected_pc_tools(host, port):
        name = str(tool.get("name") or tool.get("tool") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names))


def send_pc_tool_call(
    tool_call: JsonDict,
    host: str = "127.0.0.1",
    port: int = 8765,
    timeout_seconds: int = 30,
    audit_trace_id: Optional[str] = None,
    audit_parent_id: Optional[str] = None,
    audit_phase: str = "transport",
    audit_enabled: bool = True,
) -> PCToolBridgeResult:
    """Send a single PC tool call through the persistent bridge."""
    audit_logger = get_audit_logger() if audit_enabled else None
    trace_id = audit_trace_id or (audit_logger.new_id("pc-bridge") if audit_logger else "")
    start_time = time.time()
    normalized_call = _normalize_tool_call(tool_call)

    result = get_pc_tool_bridge(host, port).send_tool_call(normalized_call, timeout_seconds=timeout_seconds)
    _log_tool_call_result(
        audit_logger=audit_logger,
        trace_id=trace_id,
        parent_id=audit_parent_id,
        phase=audit_phase,
        duration_ms=int((time.time() - start_time) * 1000),
        result=result,
    )
    return result


def _log_tool_call_result(
    *,
    audit_logger: Any,
    trace_id: str,
    parent_id: Optional[str],
    phase: str,
    duration_ms: int,
    result: PCToolBridgeResult,
) -> None:
    if not audit_logger:
        return

    audit_logger.log_tool_call(
        tool_name=str(result.tool_call.get("tool") or "unknown"),
        trace_id=trace_id,
        parent_id=parent_id,
        phase=phase,
        duration_ms=duration_ms,
        status="completed" if result.ok else "failed",
        request_payload=result.tool_call,
        response_payload={
            "ok": result.ok,
            "tool_result": result.tool_result,
            "error": result.error,
        },
        error=result.error,
    )


def _normalize_tool_call(tool_call: JsonDict) -> JsonDict:
    call_id = tool_call.get("call_id") or tool_call.get("id") or f"pc-{int(time.time() * 1000)}"
    tool = tool_call.get("tool") or tool_call.get("name")
    arguments = tool_call.get("arguments") or tool_call.get("args") or {}
    if isinstance(arguments, str):
        arguments = _loads_json(arguments) or {}
    if not isinstance(arguments, dict):
        arguments = {}
    arguments = _normalize_null_like_arguments(arguments)

    return {
        "type": "tool_call",
        "call_id": str(call_id),
        "tool": str(tool or ""),
        "arguments": arguments,
    }


def normalized_tool_call_payload(tool_call: JsonDict) -> JsonDict:
    """Return a normalized call payload for logging."""
    return _normalize_tool_call(tool_call)


def _loads_json(text: Any) -> Optional[JsonDict]:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_null_like_arguments(arguments: JsonDict) -> JsonDict:
    normalized: JsonDict = {}
    for key, value in arguments.items():
        if isinstance(value, str) and value.strip().lower() in {"null", "none", "undefined", "nil"}:
            normalized[key] = None
        else:
            normalized[key] = value
    return normalized
