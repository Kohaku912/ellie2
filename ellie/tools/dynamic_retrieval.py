"""
Dynamic tool retrieval and AI tool-calling controller.

This module keeps tool definitions out of the model context until an event
requires them. The default vector store is an in-memory mock, but the
ToolVectorStore protocol can be implemented by Qdrant, Chroma, pgvector, etc.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import time
import base64
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from ellie.config import (
    CEREBRAS_MODEL,
    DEFAULT_OVERLAY_CLEAR_AFTER_MS,
    MAX_TOKENS,
    TEMPERATURE,
)
from ellie.autonomy.drive_system import get_drive_system
from ellie.logging.audit_log import get_audit_logger
from ellie.core.llm_router import LLMRouter
from ellie.core.prompt_builder import build_base_prompt
from ellie.memory.social_needs import SocialNeedsManager

logger = logging.getLogger(__name__)


JsonDict = Dict[str, Any]
MANDATORY_CORE_TOOL_NAMES = (
    "web_search",
    "read_file_base64",
    "self_development",
    "execute_shell",
    "overlay_show",
    "request_user_approval",
)


@dataclass(frozen=True)
class ToolDefinition:
    """JSON Schema based definition used for retrieval and LLM tool calling."""

    name: str
    description: str
    parameters: JsonDict
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    handler_name: Optional[str] = None

    def searchable_text(self) -> str:
        """Return compact text used by the vector store."""
        schema_text = json.dumps(self.parameters, ensure_ascii=False, sort_keys=True)
        return " ".join([self.name, self.description, *self.tags, *self.examples, schema_text])

    def to_openai_tool(self) -> JsonDict:
        """Convert to Chat Completion tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class RetrievedTool:
    """Tool definition with a similarity score."""

    definition: ToolDefinition
    score: float


@dataclass
class ToolCallRequest:
    """Parsed tool call requested by the model."""

    name: str
    arguments: JsonDict
    call_id: Optional[str] = None
    result: Optional[JsonDict] = None


@dataclass
class AIResponse:
    """Response returned to the event caller."""

    status: str
    content: str
    selected_tools: List[ToolDefinition]
    tool_calls: List[ToolCallRequest] = field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None
    call_id: Optional[str] = None

    def to_dict(self) -> JsonDict:
        """Return a JSON-serializable representation."""
        return {
            "status": self.status,
            "content": self.content,
            "selected_tools": [tool.to_openai_tool()["function"] for tool in self.selected_tools],
            "tool_calls": [
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "call_id": call.call_id,
                    "result": call.result,
                }
                for call in self.tool_calls
            ],
            "duration_ms": self.duration_ms,
            "error": self.error,
            "call_id": self.call_id,
        }


class ToolVectorStore(Protocol):
    """Search interface for a tool vector database."""

    def search(self, query: str, top_n: int) -> List[RetrievedTool]:
        """Return the top matching tool definitions."""


class InMemoryToolVectorStore:
    """Small mock vector store based on token cosine similarity."""

    def __init__(self, tools: Sequence[ToolDefinition]):
        self._tools = list(tools)
        self._vectors = {tool.name: self._embed(tool.searchable_text()) for tool in self._tools}

    def search(self, query: str, top_n: int) -> List[RetrievedTool]:
        if top_n <= 0:
            raise ValueError("top_n must be greater than zero")

        query_vector = self._embed(query)
        scored_tools = [
            RetrievedTool(definition=tool, score=self._cosine_similarity(query_vector, self._vectors[tool.name]))
            for tool in self._tools
        ]
        scored_tools.sort(key=lambda item: item.score, reverse=True)
        return scored_tools[:top_n]

    def _embed(self, text: str) -> Counter[str]:
        return Counter(_tokenize(text))

    def _cosine_similarity(self, left: Counter[str], right: Counter[str]) -> float:
        if not left or not right:
            return 0.0

        shared = set(left) & set(right)
        dot_product = sum(left[token] * right[token] for token in shared)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot_product / (left_norm * right_norm)


class ToolCallHandler:
    """Skeleton dispatcher for tool calls requested by the model."""

    def handle(
        self,
        tool_call: ToolCallRequest | Dict[str, Any],
        *,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
        audit_phase: str = "dispatch",
    ) -> JsonDict:
        from ellie.mcp.pc_bridge.tools import get_connected_pc_tool_names
        from ellie.tools.registry import PC_TOOL_NAMES
        audit_logger = get_audit_logger()
        start_time = time.time()
        normalized_call = self._normalize_tool_call(tool_call)

        if normalized_call.name.startswith("playwright__"):
            result = self._handle_playwright_tool_call(normalized_call.name, normalized_call.arguments)
            audit_logger.log_tool_call(
                tool_name=normalized_call.name,
                trace_id=audit_trace_id or audit_logger.new_id("tool-dispatch"),
                parent_id=audit_parent_id,
                phase=audit_phase,
                duration_ms=int((time.time() - start_time) * 1000),
                status=result.get("status", "completed"),
                request_payload={"tool_call": {"name": normalized_call.name, "arguments": normalized_call.arguments}},
                response_payload=result,
                error=result.get("error"),
            )
            return result

        handlers = {
            "web_search": self._handle_web_search,
            "read_file_base64": self._handle_read_file_base64,
            "list_directory": self._handle_list_directory,
            "execute_shell": self._handle_execute_shell,
            "creative_expression": self._handle_creative_expression,
            "blog_post": self._handle_blog_post,
            "self_development": self._handle_self_development,
            "request_user_approval": self._handle_request_user_approval,
            "social_feedback_check": self._handle_social_feedback_check,
            "twitter_followers_check": self._handle_twitter_followers_check,
            "twitter_post": self._handle_twitter_post,
            "twitter_profile_edit": self._handle_twitter_profile_edit,
            "schedule_self_call": self._handle_schedule_self_call,
            "create_long_term_goal": self._handle_create_long_term_goal,
            "update_long_term_goal": self._handle_update_long_term_goal,
            "send_notification": self._handle_send_notification,
            "record_user_event": self._handle_record_user_event,
        }

        connected_pc_tool_names = set(get_connected_pc_tool_names())
        if normalized_call.name in handlers:
            handler = handlers[normalized_call.name]
            try:
                result = handler(normalized_call.arguments)
                audit_logger.log_tool_call(
                    tool_name=normalized_call.name,
                    trace_id=audit_trace_id or audit_logger.new_id("tool-dispatch"),
                    parent_id=audit_parent_id,
                    phase=audit_phase,
                    duration_ms=int((time.time() - start_time) * 1000),
                    status=result.get("status", "completed"),
                    request_payload={"tool_call": {"name": normalized_call.name, "arguments": normalized_call.arguments}},
                    response_payload=result,
                )
                return result
            except Exception as error:
                logger.error(f"Tool call failed: {normalized_call.name}: {error}", exc_info=True)
                result = {
                    "status": "failed",
                    "tool": normalized_call.name,
                    "error": str(error),
                }
                audit_logger.log_tool_call(
                    tool_name=normalized_call.name,
                    trace_id=audit_trace_id or audit_logger.new_id("tool-dispatch"),
                    parent_id=audit_parent_id,
                    phase=audit_phase,
                    duration_ms=int((time.time() - start_time) * 1000),
                    status="failed",
                    request_payload={"tool_call": {"name": normalized_call.name, "arguments": normalized_call.arguments}},
                    response_payload=result,
                    error=str(error),
                )
                return result

        if normalized_call.name in PC_TOOL_NAMES or normalized_call.name in connected_pc_tool_names:
            result = self._handle_pc_tool_call(normalized_call.name, normalized_call.arguments)
            audit_logger.log_tool_call(
                tool_name=normalized_call.name,
                trace_id=audit_trace_id or audit_logger.new_id("tool-dispatch"),
                parent_id=audit_parent_id,
                phase=audit_phase,
                duration_ms=int((time.time() - start_time) * 1000),
                status=result.get("status", "completed"),
                request_payload={"tool_call": {"name": normalized_call.name, "arguments": normalized_call.arguments}},
                response_payload=result,
            )
            return result

        result = {
            "status": "unsupported_tool",
            "tool": normalized_call.name,
            "message": "No local handler is registered for this tool.",
        }
        audit_logger.log_tool_call(
            tool_name=normalized_call.name,
            trace_id=audit_trace_id or audit_logger.new_id("tool-dispatch"),
            parent_id=audit_parent_id,
            phase=audit_phase,
            duration_ms=int((time.time() - start_time) * 1000),
            status="unsupported_tool",
            request_payload={"tool_call": {"name": normalized_call.name, "arguments": normalized_call.arguments}},
            response_payload=result,
        )
        return result


    def _normalize_tool_call(self, tool_call: ToolCallRequest | Dict[str, Any]) -> ToolCallRequest:
        if isinstance(tool_call, ToolCallRequest):
            return tool_call
        if isinstance(tool_call, dict):
            name = str(tool_call.get("name") or tool_call.get("tool") or "").strip()
            arguments = tool_call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = _parse_arguments(arguments)
            call_id = str(tool_call.get("call_id") or tool_call.get("id") or "").strip() or None
            return ToolCallRequest(name=name, arguments=arguments, call_id=call_id)
        raise TypeError(f"Unsupported tool_call type: {type(tool_call)!r}")

    def _handle_pc_tool_call(self, tool_name: str, arguments: JsonDict) -> JsonDict:
        return {
            "status": "queued",
            "target": "pc_client",
            "tool_call": {
                "type": "tool_call",
                "tool": tool_name,
                "arguments": arguments,
            },
            "message": "PC client tool call parsed and queued for WebSocket delivery.",
        }

    def _handle_send_notification(self, arguments: JsonDict) -> JsonDict:
        title = str(arguments.get("title") or "Ellie").strip() or "Ellie"
        body = str(arguments.get("body") or arguments.get("message") or "").strip()
        display_text = f"{title}\n{body}" if body else title
        return {
            "status": "queued",
            "target": "pc_client",
            "action": "send_notification",
            "tool_call": {
                "type": "tool_call",
                "tool": "overlay_show",
                "arguments": {
                    "x": 24,
                    "y": 24,
                    "width": 640,
                    "height": 180,
                    "opacity": 230,
                    "clear_after_ms": DEFAULT_OVERLAY_CLEAR_AFTER_MS,
                    "items": [
                        {
                            "type": "rect",
                            "x": 0,
                            "y": 0,
                            "width": 640,
                            "height": 180,
                            "color": "#101820",
                            "fill": True,
                        },
                        {
                            "type": "text",
                            "text": display_text,
                            "x": 24,
                            "y": 24,
                            "size": 28,
                            "color": "#ffffff",
                        },
                    ],
                },
            },
            "message": "Notification parsed and queued for PC overlay delivery.",
        }

    def _handle_web_search(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.web_search import web_search

        query = str(arguments.get("query") or "").strip()
        max_results = arguments.get("max_results", 5)
        return web_search(query, max_results=max_results)

    def _handle_read_file_base64(self, arguments: JsonDict) -> JsonDict:
        path_text = str(arguments.get("path") or "").strip()
        if not path_text:
            return {"status": "failed", "tool": "read_file_base64", "error": "path is required"}
        target = Path(path_text)
        if not target.exists() or not target.is_file():
            return {"status": "failed", "tool": "read_file_base64", "error": "file not found", "path": path_text}
        data = target.read_bytes()
        return {
            "status": "completed",
            "tool": "read_file_base64",
            "path": str(target),
            "data_base64": base64.b64encode(data).decode("ascii"),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _handle_list_directory(self, arguments: JsonDict) -> JsonDict:
        path_text = str(arguments.get("path") or ".").strip()
        target = Path(path_text)
        if not target.exists() or not target.is_dir():
            return {"status": "failed", "tool": "list_directory", "error": "directory not found", "path": path_text}
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: item.name.casefold())[:200]:
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {
            "status": "completed",
            "tool": "list_directory",
            "path": str(target),
            "entries": entries,
        }

    def _handle_execute_shell(self, arguments: JsonDict) -> JsonDict:
        command_text = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        timeout_seconds = max(1, int(arguments.get("timeout_seconds") or 60))
        workdir = str(arguments.get("workdir") or os.getcwd()).strip() or os.getcwd()
        if not command_text:
            return {"status": "failed", "tool": "execute_shell", "error": "command is required"}
        process = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command_text,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            cwd=workdir,
        )
        ok = process.returncode == 0
        return {
            "status": "completed" if ok else "failed",
            "tool": "execute_shell",
            "command": command_text,
            "workdir": workdir,
            "exit_code": process.returncode,
            "stdout": process.stdout[-12000:],
            "stderr": process.stderr[-12000:],
        }

    def _handle_creative_expression(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import creative_expression

        return creative_expression(arguments)

    def _handle_blog_post(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import blog_post

        return blog_post(arguments)

    def _handle_self_development(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import self_development

        return self_development(arguments)

    def _handle_request_user_approval(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import request_user_approval

        return request_user_approval(arguments)

    def _handle_social_feedback_check(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import social_feedback_check

        return social_feedback_check(arguments)

    def _handle_twitter_followers_check(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import twitter_followers_check

        return twitter_followers_check(arguments)

    def _handle_twitter_post(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import twitter_post

        return twitter_post(arguments)

    def _handle_twitter_profile_edit(self, arguments: JsonDict) -> JsonDict:
        from ellie.tools.autonomous_tools import twitter_profile_edit

        return twitter_profile_edit(arguments)

    def _handle_playwright_tool_call(self, tool_name: str, arguments: JsonDict) -> JsonDict:
        from ellie.mcp.playwright.tools import call_playwright_tool

        return call_playwright_tool(tool_name, arguments)

    def _handle_schedule_self_call(self, arguments: JsonDict) -> JsonDict:
        from ellie.autonomy.runtime import enqueue_self_call

        return enqueue_self_call(
            instruction=str(arguments.get("instruction") or ""),
            run_after_seconds=int(arguments.get("run_after_seconds") or 60),
            run_at=str(arguments.get("run_at") or ""),
            reason=str(arguments.get("reason") or ""),
        )

    def _handle_create_long_term_goal(self, arguments: JsonDict) -> JsonDict:
        from ellie.autonomy.runtime import create_long_term_goal

        return create_long_term_goal(
            title=str(arguments.get("title") or ""),
            description=str(arguments.get("description") or ""),
            success_criteria=str(arguments.get("success_criteria") or ""),
        )

    def _handle_update_long_term_goal(self, arguments: JsonDict) -> JsonDict:
        from ellie.autonomy.runtime import update_long_term_goal

        return update_long_term_goal(
            goal_id=str(arguments.get("goal_id") or ""),
            update_text=str(arguments.get("update_text") or ""),
            status=str(arguments.get("status") or ""),
        )

    def _handle_record_user_event(self, arguments: JsonDict) -> JsonDict:
        return {
            "status": "recorded",
            "action": "record_user_event",
            "event_type": arguments.get("event_type", ""),
            "summary": arguments.get("summary", ""),
        }


class DynamicToolRAGController:
    """Coordinates event context, tool retrieval, AI calls, and tool parsing."""

    def __init__(
        self,
        vector_store: Optional[ToolVectorStore] = None,
        tool_handler: Optional[ToolCallHandler] = None,
        client: Optional[Any] = None,
        social_needs: Optional[SocialNeedsManager] = None,
        llm_router: Optional[LLMRouter] = None,
    ):
        if vector_store is None:
            from ellie.tools.registry import get_available_tool_definitions

            vector_store = InMemoryToolVectorStore(get_available_tool_definitions())

        self.vector_store = vector_store
        self.tool_handler = tool_handler or ToolCallHandler()
        self.client = client
        self.social_needs = social_needs
        self.llm_router = llm_router or LLMRouter()

    def retrieve_relevant_tools(self, query: str, top_n: int = 5) -> List[ToolDefinition]:
        """Search the vector store for relevant tool definitions."""
        query_text = query.strip()
        if not query_text:
            raise ValueError("query must not be empty")

        retrieved = self.vector_store.search(query_text, top_n)
        selected = [item.definition for item in retrieved]
        return self._merge_mandatory_core_tools(selected)

    def call_ai_with_dynamic_tools(
        self,
        event_context: str,
        memory_context: str = "",
        top_n: int = 5,
        audit_trace_id: Optional[str] = None,
        audit_parent_id: Optional[str] = None,
        task_type: str = "light",
    ) -> AIResponse:
        """Call the LLM with only the retrieved tools for the current event."""
        start_time = time.time()
        audit_logger = get_audit_logger()
        call_id = audit_logger.new_id("dynamic-tool-ai")
        trace_id = audit_trace_id or call_id
        try:
            retrieval_query = event_context
            if memory_context.strip():
                retrieval_query = f"{event_context.strip()}\n\n## 今日の記憶\n{memory_context.strip()}"

            selected_tools = self.retrieve_relevant_tools(retrieval_query, top_n)
            tool_schemas = [tool.to_openai_tool() for tool in selected_tools]
            messages = self._build_messages(event_context, selected_tools, memory_context=memory_context)

            response = self._call_chat_completion(messages, tool_schemas, task_type=task_type)
            if response.error:
                error_message = str(response.error or "LLM request failed")
                logger.warning("Dynamic tool call rate limited: %s", error_message)
                audit_logger.log_ai_call(
                    call_type="dynamic_tool_rag",
                    trigger="event_context",
                    trace_id=trace_id,
                    parent_id=audit_parent_id,
                    call_id=call_id,
                    model=response.model or CEREBRAS_MODEL,
                    provider=response.provider,
                    reasoning_profile=task_type,
                    reasoning_effort=response.reasoning_effort,
                    duration_ms=int((time.time() - start_time) * 1000),
                    status="failed",
                    request_payload={
                        "messages": messages,
                        "tools": tool_schemas,
                        "memory_context": memory_context,
                    },
                    response_payload={
                        "content": response.content,
                        "tool_calls": response.tool_calls,
                        "selected_tools": [tool.to_openai_tool()["function"] for tool in selected_tools],
                        "thinking_text": response.thinking_text,
                        "error": error_message,
                    },
                    error=error_message,
                )
                return AIResponse(
                    status="rate_limited",
                    content="いまAIが混雑しているので、少し時間をおいてもう一度試してください。",
                    selected_tools=selected_tools,
                    tool_calls=[],
                    duration_ms=int((time.time() - start_time) * 1000),
                    error=error_message,
                    call_id=call_id,
                )
            content = response.content
            tool_calls = self._tool_calls_from_result(response)

            if not tool_calls:
                fallback_calls = self._parse_prompt_tool_calls(content, selected_tools)
                if fallback_calls:
                    tool_calls = fallback_calls
                    content = self._strip_json_response_message(content) or content

            audit_logger.log_ai_call(
                call_type="dynamic_tool_rag",
                trigger="event_context",
                trace_id=trace_id,
                parent_id=audit_parent_id,
                call_id=call_id,
                model=response.model or CEREBRAS_MODEL,
                provider=response.provider,
                reasoning_profile=task_type,
                reasoning_effort=response.reasoning_effort,
                duration_ms=int((time.time() - start_time) * 1000),
                status="tool_call_requested" if tool_calls else "completed",
                request_payload={
                    "messages": messages,
                    "tools": tool_schemas,
                    "memory_context": memory_context,
                },
                response_payload={
                    "content": content,
                    "thinking_text": response.thinking_text,
                    "tool_calls": [
                        {
                            "name": call.name,
                            "arguments": call.arguments,
                            "call_id": call.call_id,
                        }
                        for call in tool_calls
                    ],
                    "selected_tools": [tool.to_openai_tool()["function"] for tool in selected_tools],
                },
            )

            for tool_call in tool_calls:
                tool_call.result = self.tool_handler.handle(
                    tool_call,
                    audit_trace_id=trace_id,
                    audit_parent_id=call_id,
                )
                if tool_call.call_id and isinstance(tool_call.result, dict):
                    outbound_call = tool_call.result.get("tool_call")
                    if isinstance(outbound_call, dict) and "call_id" not in outbound_call:
                        outbound_call["call_id"] = tool_call.call_id

            return AIResponse(
                status="tool_call_requested" if tool_calls else "completed",
                content=content,
                selected_tools=selected_tools,
                tool_calls=tool_calls,
                duration_ms=int((time.time() - start_time) * 1000),
                call_id=call_id,
            )
        except Exception as error:
            logger.error(f"Dynamic tool RAG call failed: {error}", exc_info=True)
            audit_logger.log_ai_call(
                call_type="dynamic_tool_rag",
                trigger="event_context",
                trace_id=trace_id,
                parent_id=audit_parent_id,
                call_id=call_id,
                model=CEREBRAS_MODEL,
                provider="cerebras",
                reasoning_profile=task_type,
                duration_ms=int((time.time() - start_time) * 1000),
                status="failed",
                request_payload={
                    "event_context": event_context,
                    "memory_context": memory_context,
                    "top_n": top_n,
                },
                response_payload={"content": "", "tool_calls": [], "selected_tools": []},
                error=str(error),
            )
            return AIResponse(
                status="failed",
                content="",
                selected_tools=[],
                duration_ms=int((time.time() - start_time) * 1000),
                error=str(error),
                call_id=call_id,
            )

    def _build_messages(
        self,
        event_context: str,
        selected_tools: Sequence[ToolDefinition],
        memory_context: str = "",
    ) -> List[JsonDict]:
        extra_tool_names = ", ".join(
            tool.name for tool in selected_tools if tool.name not in MANDATORY_CORE_TOOL_NAMES
        )
        extra_tool_names = extra_tool_names or "なし"
        drive_action_required = "DRIVE_ACTION_REQUIRED: true" in event_context
        memory_block = ""
        if memory_context.strip():
            memory_block = f"""

## 今日の記憶
{memory_context.strip()}
"""
        prompt = f"""
{memory_block}

## 現在の状況
{event_context.strip()}

## 利用できるツール
今回の状況に関連して追加されたツール: {extra_tool_names}
コア機能は system prompt 先頭の索引にあります。必要なときだけ、関係するものを選んでください。

必要なら、本当に関係するツールだけを選び、妥当なJSON引数で呼び出してください。
Twitter/X のプロフィール編集が必要なら twitter_profile_edit を使ってください。
X/Twitter のフォロワー数確認やログイン確認が必要なら twitter_followers_check を使ってください。
ブラウザを開いたら、そこで止めず、必要な入力・遷移・確認・抽出まで続けてください。
確認や承認が必要なら request_user_approval を使ってください。今すぐ返事が必要なら overlay_prompt、短い案内だけなら overlay_show、急がないなら保留メモに回してください。
{self._tool_requirement_instruction(drive_action_required)}
"""
        return [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": prompt},
        ]

    def _tool_requirement_instruction(self, drive_action_required: bool) -> str:
        if drive_action_required:
            return (
                "DRIVE_ACTION_REQUIRED が true です。Tool不要回答は禁止です。"
                "取得されたツールから最も欲求を満たせる安全なToolを必ず1件以上呼び出してください。"
                "PC側の一般書込や削除は避けますが、self_development の write_file は Ellie2 配下だけを検証付きで扱う専用Toolなので必要なら使えます。"
                "playwright__ で始まるブラウザ操作ToolはXのログイン、投稿、通知確認、反応確認、ページ遷移、フォーム入力に使えます。"
                "ツイッターに何か投稿したいときは twitter_post を優先し、必要なら Playwright MCP で実行してください。"
                "notify は具体的な結果・期限・次の行動がある場合のみ使い、空疎な挨拶や手伝いの申し出だけを通知しないでください。"
            )
        return "不要なら、なぜ今は使わないのかを短く日本語で答えてください。"

    def _build_system_prompt(self) -> str:
        base_prompt = build_base_prompt(drive_summary=get_drive_system().build_prompt_summary())
        if self.social_needs is None:
            return base_prompt
        return self.social_needs.build_system_prompt(base_prompt)

    def _merge_mandatory_core_tools(self, selected_tools: Sequence[ToolDefinition]) -> List[ToolDefinition]:
        from ellie.tools.registry import get_available_tool_definitions

        merged: List[ToolDefinition] = list(selected_tools)
        seen = {tool.name for tool in merged}
        available_by_name = {tool.name: tool for tool in get_available_tool_definitions()}
        for name in MANDATORY_CORE_TOOL_NAMES:
            tool = available_by_name.get(name)
            if tool is None or name in seen:
                continue
            merged.append(tool)
            seen.add(name)
        return merged

    def _tool_calls_from_result(self, result: Any) -> List[ToolCallRequest]:
        parsed_calls: List[ToolCallRequest] = []
        for raw_call in result.tool_calls:
            name = str(raw_call.get("name") or "").strip()
            if not name:
                continue
            arguments = raw_call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = _parse_arguments(arguments)
            parsed_calls.append(
                ToolCallRequest(
                    name=name,
                    arguments=arguments,
                    call_id=str(raw_call.get("id") or "").strip() or None,
                )
            )
        return parsed_calls

    def _call_chat_completion(
        self,
        messages: List[JsonDict],
        tool_schemas: List[JsonDict],
        *,
        task_type: str,
    ) -> Any:
        result = self.llm_router.complete(
            messages,
            task_type=task_type,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            tools=tool_schemas,
            tool_choice="auto",
        )
        if result.error and tool_schemas:
            logger.warning("Native tool calling failed, using prompt fallback: %s", result.error)
            return self.llm_router.complete(
                self._build_prompt_fallback_messages(messages, tool_schemas),
                task_type=task_type,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        return result

    def _build_prompt_fallback_messages(self, messages: List[JsonDict], tool_schemas: List[JsonDict]) -> List[JsonDict]:
        fallback_instruction = {
            "role": "system",
            "content": (
                "ツール呼び出しはJSONで表現してください。ツールが必要なときは `message` と `tool_calls` だけを返し、"
                "各 tool_call には `name` と `arguments` を入れてください。ツール不要なら通常の日本語応答だけで構いません。"
            ),
        }
        tool_context = {
            "role": "user",
            "content": "取得されたツール一覧:\n" + json.dumps(tool_schemas, ensure_ascii=False, indent=2),
        }
        return [messages[0], fallback_instruction, tool_context, *messages[1:]]

    def _parse_tool_calls(self, response: Any) -> List[ToolCallRequest]:
        choices = _get_value(response, "choices") or []
        if not choices:
            return []

        message = _get_value(choices[0], "message")
        raw_tool_calls = _get_value(message, "tool_calls") if message is not None else None
        if not raw_tool_calls:
            return []

        parsed_calls: List[ToolCallRequest] = []
        for raw_call in raw_tool_calls:
            function = _get_value(raw_call, "function")
            name = _get_value(function, "name")
            raw_arguments = _get_value(function, "arguments") or "{}"
            if not name:
                continue

            parsed_calls.append(
                ToolCallRequest(
                    name=name,
                    arguments=_parse_arguments(raw_arguments),
                    call_id=_get_value(raw_call, "id"),
                )
            )
        return parsed_calls

    def _parse_prompt_tool_calls(self, content: str, selected_tools: Sequence[ToolDefinition]) -> List[ToolCallRequest]:
        payload = _loads_json_object(content)
        if not payload:
            return []

        selected_tool_names = {tool.name for tool in selected_tools}
        raw_calls = payload.get("tool_calls", [])
        parsed_calls: List[ToolCallRequest] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                continue

            name = raw_call.get("name")
            if name not in selected_tool_names:
                continue

            arguments = raw_call.get("arguments", {})
            parsed_calls.append(
                ToolCallRequest(
                    name=name,
                    arguments=arguments if isinstance(arguments, dict) else _parse_arguments(arguments),
                )
            )
        return parsed_calls

    def _strip_json_response_message(self, content: str) -> str:
        payload = _loads_json_object(content)
        if not payload:
            return ""
        message = payload.get("message", "")
        return message if isinstance(message, str) else ""


def retrieve_relevant_tools(query: str, top_n: int) -> List[ToolDefinition]:
    """Module-level helper for dynamic tool retrieval."""
    return _get_default_controller().retrieve_relevant_tools(query, top_n)


def call_ai_with_dynamic_tools(event_context: str) -> AIResponse:
    """Module-level helper for event-driven AI calls."""
    return _get_default_controller().call_ai_with_dynamic_tools(event_context)


_DEFAULT_CONTROLLER: Optional[DynamicToolRAGController] = None


def _get_default_controller() -> DynamicToolRAGController:
    return DynamicToolRAGController()


def _tokenize(text: str) -> List[str]:
    lowered = text.lower()
    ascii_tokens = re.findall(r"[a-z0-9_]+", lowered)
    cjk_chars = re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", lowered)
    cjk_bigrams = ["".join(cjk_chars[index : index + 2]) for index in range(max(len(cjk_chars) - 1, 0))]
    return ascii_tokens + cjk_chars + cjk_bigrams


def _parse_arguments(raw_arguments: Any) -> JsonDict:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return {}

    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        logger.warning(f"Could not parse tool arguments as JSON: {raw_arguments}")
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _extract_message_content(response: Any) -> str:
    choices = _get_value(response, "choices") or []
    if not choices:
        return ""

    message = _get_value(choices[0], "message")
    content = _get_value(message, "content") if message is not None else ""
    return content.strip() if isinstance(content, str) else ""


def _get_value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _loads_json_object(text: str) -> Optional[JsonDict]:
    if not text.strip():
        return None

    candidates = [text.strip()]
    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1))

    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


