"""
Shared multi-provider LLM router for Ellie.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    class _HTTPXError(Exception):
        pass

    class _HTTPXClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def post(self, *args, **kwargs):
            raise _HTTPXError("httpx is not installed")

    httpx = type("httpx", (), {"Client": _HTTPXClient, "HTTPError": _HTTPXError})()  # type: ignore

try:
    from cerebras.cloud.sdk import Cerebras, RateLimitError  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    Cerebras = None  # type: ignore[assignment]

    class RateLimitError(Exception):
        pass

from ellie.config import (
    CEREBRAS_API_KEY,
    CEREBRAS_BASE_URL,
    CEREBRAS_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


@dataclass
class LLMResult:
    provider: str
    model: str
    content: str
    tool_calls: List[JsonDict] = field(default_factory=list)
    thinking_text: str = ""
    raw_response: Any = None
    duration_ms: int = 0
    error: str = ""
    reasoning_profile: str = ""
    reasoning_effort: str = ""


class LLMRouter:
    """Route lightweight calls to Cerebras and heavy calls to DeepSeek."""

    def __init__(self) -> None:
        self._cerebras_client: Cerebras | None = None
        self._http_client = httpx.Client(timeout=REQUEST_TIMEOUT)

    def complete(
        self,
        messages: List[JsonDict],
        *,
        task_type: str,
        max_tokens: int,
        temperature: float,
        tools: list[JsonDict] | None = None,
        tool_choice: str | JsonDict | None = None,
        audit_meta: dict | None = None,
    ) -> LLMResult:
        profile = (task_type or "light").strip().casefold()
        provider = self.provider_for(profile)
        started = time.time()

        # --- attempt the primary provider ---
        result = self._try_provider(
            provider, messages,
            max_tokens=max_tokens, temperature=temperature,
            tools=tools, tool_choice=tool_choice,
        )

        # --- if primary failed, try the other provider as fallback ---
        if result.error:
            fallback_provider = "deepseek" if provider == "cerebras" else "cerebras"
            logger.warning(
                "%s failed (%s), falling back to %s",
                provider, result.error, fallback_provider,
            )
            fallback = self._try_provider(
                fallback_provider, messages,
                max_tokens=max_tokens, temperature=temperature,
                tools=tools, tool_choice=tool_choice,
            )
            if not fallback.error:
                fallback.duration_ms = int((time.time() - started) * 1000)
                fallback.reasoning_profile = profile
                return fallback

        result.duration_ms = int((time.time() - started) * 1000)
        result.reasoning_profile = profile
        return result

    def _try_provider(
        self,
        provider: str,
        messages: List[JsonDict],
        *,
        max_tokens: int,
        temperature: float,
        tools: list[JsonDict] | None = None,
        tool_choice: str | JsonDict | None = None,
    ) -> LLMResult:
        if provider == "deepseek":
            if not DEEPSEEK_API_KEY.strip():
                return self._fallback_response(
                    messages,
                    provider="deepseek",
                    model=DEEPSEEK_MODEL,
                    error="DEEPSEEK_API_KEY is not configured",
                    reasoning_profile="fallback",
                )
            return self._complete_deepseek(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
            )
        # provider == "cerebras"
        if Cerebras is None:
            return self._fallback_response(
                messages,
                provider="cerebras",
                model=CEREBRAS_MODEL,
                error="cerebras sdk is not installed",
                reasoning_profile="fallback",
            )
        return self._complete_cerebras(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )

    def provider_for(self, task_type: str) -> str:
        return "deepseek" if task_type.strip().casefold() == "heavy" else "cerebras"

    def _complete_cerebras(
        self,
        messages: List[JsonDict],
        *,
        max_tokens: int,
        temperature: float,
        tools: list[JsonDict] | None = None,
        tool_choice: str | JsonDict | None = None,
    ) -> LLMResult:
        client = self._get_cerebras_client()
        if client is None:
            return self._fallback_response(
                messages,
                provider="cerebras",
                model=CEREBRAS_MODEL,
                error="cerebras sdk is not installed",
                reasoning_profile="light",
            )
        try:
            payload: JsonDict = {
                "model": CEREBRAS_MODEL,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice or "auto"
            response = client.chat.completions.create(**payload)
            return LLMResult(
                provider="cerebras",
                model=CEREBRAS_MODEL,
                content=_extract_message_text(response),
                tool_calls=_extract_tool_calls(response),
                raw_response=_to_jsonable(response),
            )
        except RateLimitError as error:
            return LLMResult(
                provider="cerebras",
                model=CEREBRAS_MODEL,
                content="",
                error=str(error),
            )
        except Exception as error:
            logger.warning("Cerebras request failed: %s", error)
            return self._fallback_response(
                messages,
                provider="cerebras",
                model=CEREBRAS_MODEL,
                error=str(error),
                reasoning_profile="light",
            )

    def _complete_deepseek(
        self,
        messages: List[JsonDict],
        *,
        max_tokens: int,
        temperature: float,
        tools: list[JsonDict] | None = None,
        tool_choice: str | JsonDict | None = None,
    ) -> LLMResult:
        if not DEEPSEEK_API_KEY.strip():
            return LLMResult(
                provider="deepseek",
                model=DEEPSEEK_MODEL,
                content="",
                error="DEEPSEEK_API_KEY is not configured",
                reasoning_effort=DEEPSEEK_REASONING_EFFORT,
            )

        payload: JsonDict = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": DEEPSEEK_REASONING_EFFORT,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        try:
            response = self._http_client.post(
                f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = _extract_message_text(data)
            content, thinking_text = _split_thinking(content)
            if not thinking_text:
                thinking_text = _extract_reasoning_text(data)
            return LLMResult(
                provider="deepseek",
                model=DEEPSEEK_MODEL,
                content=content,
                tool_calls=_extract_tool_calls(data),
                thinking_text=thinking_text,
                raw_response=data,
                reasoning_effort=DEEPSEEK_REASONING_EFFORT,
            )
        except httpx.HTTPError as error:
            logger.warning("DeepSeek request failed: %s", error)
            return LLMResult(
                provider="deepseek",
                model=DEEPSEEK_MODEL,
                content="",
                error=str(error),
                reasoning_effort=DEEPSEEK_REASONING_EFFORT,
            )

    def _get_cerebras_client(self) -> Cerebras | None:
        if Cerebras is None:
            return None
        if self._cerebras_client is None:
            self._cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)
        return self._cerebras_client

    def _fallback_response(
        self,
        messages: List[JsonDict],
        *,
        provider: str,
        model: str,
        error: str,
        reasoning_profile: str,
    ) -> LLMResult:
        return LLMResult(
            provider=provider,
            model=model,
            content=_fallback_chat_text(messages, provider=provider),
            error=error,
            reasoning_profile=reasoning_profile,
            reasoning_effort="fallback",
        )


def _extract_message_text(response: Any) -> str:
    choices = _get_value(response, "choices") or []
    if not choices:
        return ""
    message = _get_value(choices[0], "message")
    content = _get_value(message, "content") if message is not None else ""
    return content.strip() if isinstance(content, str) else ""


def _extract_tool_calls(response: Any) -> List[JsonDict]:
    choices = _get_value(response, "choices") or []
    if not choices:
        return []
    message = _get_value(choices[0], "message")
    raw_tool_calls = _get_value(message, "tool_calls") if message is not None else None
    if not raw_tool_calls:
        return []
    parsed: List[JsonDict] = []
    for raw_call in raw_tool_calls:
        function = _get_value(raw_call, "function")
        name = _get_value(function, "name")
        arguments = _get_value(function, "arguments") or "{}"
        if not name:
            continue
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        parsed.append(
            {
                "id": _get_value(raw_call, "id"),
                "name": str(name),
                "arguments": arguments,
            }
        )
    return parsed


def _extract_reasoning_text(response: Any) -> str:
    choices = _get_value(response, "choices") or []
    if not choices:
        return ""
    message = _get_value(choices[0], "message")
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = _get_value(message, key) if message is not None else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _split_thinking(content: str) -> tuple[str, str]:
    if not content.strip():
        return "", ""
    matches = re.findall(r"<think>([\s\S]*?)</think>", content, flags=re.IGNORECASE)
    if not matches:
        return content.strip(), ""
    thinking_text = "\n\n".join(match.strip() for match in matches if match.strip())
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    return cleaned, thinking_text


def _get_value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _fallback_chat_text(messages: List[JsonDict], provider: str) -> str:
    user_text = ""
    for message in reversed(messages):
        if str(message.get("role") or "").strip() == "user":
            user_text = str(message.get("content") or "").strip()
            if user_text:
                break
    if not user_text:
        return f"{provider} 依存が未導入で、入力から応答を作れませんでした。"
    first_line = user_text.splitlines()[0].strip()
    if len(first_line) > 120:
        first_line = first_line[:120].rstrip() + "..."
    return f"LLM 依存が未導入のため縮退応答です。入力の要点は「{first_line}」でした。"


def _to_jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=lambda obj: getattr(obj, "__dict__", str(obj)), ensure_ascii=False))
    except Exception:
        return str(value)

