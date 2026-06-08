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
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence

from config import (
    AGENT_SYSTEM_PROMPT,
    CEREBRAS_API_KEY,
    CEREBRAS_BASE_URL,
    CEREBRAS_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
)

logger = logging.getLogger(__name__)


JsonDict = Dict[str, Any]


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

    def handle(self, tool_call: ToolCallRequest) -> JsonDict:
        handlers = {
            "capture_screenshot": self._handle_capture_screenshot,
            "launch_application": self._handle_launch_application,
            "send_notification": self._handle_send_notification,
            "record_user_event": self._handle_record_user_event,
        }

        handler = handlers.get(tool_call.name)
        if handler is None:
            return {
                "status": "unsupported_tool",
                "tool": tool_call.name,
                "message": "No local handler is registered for this tool.",
            }

        try:
            return handler(tool_call.arguments)
        except Exception as error:
            logger.error(f"Tool call failed: {tool_call.name}: {error}", exc_info=True)
            return {
                "status": "failed",
                "tool": tool_call.name,
                "error": str(error),
            }

    def _handle_capture_screenshot(self, arguments: JsonDict) -> JsonDict:
        return {
            "status": "queued",
            "action": "capture_screenshot",
            "screen_id": arguments.get("screen_id", "primary"),
            "reason": arguments.get("reason", ""),
            "message": "Screenshot capture request parsed and queued for the device layer.",
        }

    def _handle_launch_application(self, arguments: JsonDict) -> JsonDict:
        return {
            "status": "queued",
            "action": "launch_application",
            "app_name": arguments.get("app_name", ""),
            "path": arguments.get("path", ""),
            "arguments": arguments.get("arguments", []),
            "message": "Application launch request parsed and queued for the device layer.",
        }

    def _handle_send_notification(self, arguments: JsonDict) -> JsonDict:
        return {
            "status": "queued",
            "action": "send_notification",
            "title": arguments.get("title", ""),
            "message": arguments.get("message", ""),
        }

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
    ):
        self.vector_store = vector_store or InMemoryToolVectorStore(DEFAULT_TOOL_DEFINITIONS)
        self.tool_handler = tool_handler or ToolCallHandler()
        self.client = client

    def retrieve_relevant_tools(self, query: str, top_n: int = 5) -> List[ToolDefinition]:
        """Search the vector store for relevant tool definitions."""
        query_text = query.strip()
        if not query_text:
            raise ValueError("query must not be empty")

        retrieved = self.vector_store.search(query_text, top_n)
        return [item.definition for item in retrieved]

    def call_ai_with_dynamic_tools(self, event_context: str, top_n: int = 5) -> AIResponse:
        """Call the LLM with only the retrieved tools for the current event."""
        start_time = time.time()
        try:
            selected_tools = self.retrieve_relevant_tools(event_context, top_n)
            tool_schemas = [tool.to_openai_tool() for tool in selected_tools]
            messages = self._build_messages(event_context, selected_tools)

            response = self._call_chat_completion(messages, tool_schemas)
            content = _extract_message_content(response)
            tool_calls = self._parse_tool_calls(response)

            if not tool_calls:
                fallback_calls = self._parse_prompt_tool_calls(content, selected_tools)
                if fallback_calls:
                    tool_calls = fallback_calls
                    content = self._strip_json_response_message(content) or content

            for tool_call in tool_calls:
                tool_call.result = self.tool_handler.handle(tool_call)

            return AIResponse(
                status="tool_call_requested" if tool_calls else "completed",
                content=content,
                selected_tools=selected_tools,
                tool_calls=tool_calls,
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as error:
            logger.error(f"Dynamic tool RAG call failed: {error}", exc_info=True)
            return AIResponse(
                status="failed",
                content="",
                selected_tools=[],
                duration_ms=int((time.time() - start_time) * 1000),
                error=str(error),
            )

    def _build_messages(self, event_context: str, selected_tools: Sequence[ToolDefinition]) -> List[JsonDict]:
        tool_names = ", ".join(tool.name for tool in selected_tools)
        prompt = f"""
## Event Context
{event_context.strip()}

## Dynamic Tool Retrieval
Only these tools were retrieved for this event: {tool_names}

If a tool is needed, request exactly the relevant tool call with valid JSON arguments.
If no tool is needed, answer briefly with the reason.
"""
        return [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    def _call_chat_completion(self, messages: List[JsonDict], tool_schemas: List[JsonDict]) -> Any:
        client = self._get_client()
        try:
            return client.chat.completions.create(
                model=CEREBRAS_MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=messages,
                tools=tool_schemas,
                tool_choice="auto",
            )
        except Exception as error:
            error_text = str(error).lower()
            if not any(keyword in error_text for keyword in ("tool", "tools", "tool_choice")):
                raise

            logger.warning(f"Native tool calling not supported by this client, using prompt fallback: {error}")
            return client.chat.completions.create(
                model=CEREBRAS_MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=self._build_prompt_fallback_messages(messages, tool_schemas),
            )

    def _get_client(self) -> Any:
        if self.client is None:
            from cerebras.cloud.sdk import Cerebras

            self.client = Cerebras(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)
        return self.client

    def _build_prompt_fallback_messages(self, messages: List[JsonDict], tool_schemas: List[JsonDict]) -> List[JsonDict]:
        fallback_instruction = {
            "role": "system",
            "content": (
                "Tool calling is represented as JSON. When a tool is needed, return only JSON with "
                "`message` and `tool_calls`, where each call has `name` and `arguments`."
            ),
        }
        tool_context = {
            "role": "user",
            "content": "Available retrieved tools:\n" + json.dumps(tool_schemas, ensure_ascii=False, indent=2),
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
    return DEFAULT_CONTROLLER.retrieve_relevant_tools(query, top_n)


def call_ai_with_dynamic_tools(event_context: str) -> AIResponse:
    """Module-level helper for event-driven AI calls."""
    return DEFAULT_CONTROLLER.call_ai_with_dynamic_tools(event_context)


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


DEFAULT_TOOL_DEFINITIONS: List[ToolDefinition] = [
    ToolDefinition(
        name="capture_screenshot",
        description="Capture a screenshot from the user's device when visual state is needed.",
        tags=["screen", "screenshot", "display", "visual", "画面", "スクリーンショット", "撮影"],
        examples=[
            "ユーザーがスマホの画面をオンにした",
            "画面の状態を確認したい",
            "take a screenshot when the display changes",
        ],
        handler_name="capture_screenshot",
        parameters={
            "type": "object",
            "properties": {
                "screen_id": {
                    "type": "string",
                    "description": "Target screen identifier. Use primary when unknown.",
                    "default": "primary",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason why the screenshot is useful.",
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="launch_application",
        description="Launch an application or foreground a known app on the user's device.",
        tags=["app", "application", "launch", "open", "アプリ", "起動", "開く"],
        examples=[
            "ユーザーが音楽を聴きたそうなのでアプリを起動する",
            "open the browser app",
            "アプリ起動が必要なイベント",
        ],
        handler_name="launch_application",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Human-readable application name.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional executable path when available.",
                },
                "arguments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional launch arguments.",
                    "default": [],
                },
            },
            "required": ["app_name"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="send_notification",
        description="Send a short notification to the user when the event needs attention.",
        tags=["notify", "notification", "alert", "通知", "知らせる", "アラート"],
        examples=[
            "重要な変更をユーザーへ知らせる",
            "send a reminder notification",
        ],
        handler_name="send_notification",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title."},
                "message": {"type": "string", "description": "Notification body."},
            },
            "required": ["title", "message"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="record_user_event",
        description="Record a lightweight event summary for downstream systems.",
        tags=["event", "log", "record", "history", "イベント", "記録"],
        examples=[
            "ユーザーがスマホの画面をオンにしたことを記録する",
            "log a filtered terminal event",
        ],
        handler_name="record_user_event",
        parameters={
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "description": "Machine-readable event type."},
                "summary": {"type": "string", "description": "Short natural-language summary."},
            },
            "required": ["event_type", "summary"],
            "additionalProperties": False,
        },
    ),
]


DEFAULT_CONTROLLER = DynamicToolRAGController()
