"""
Small API-key-free web search tool.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from agent.time_utils import isoformat_local

logger = logging.getLogger(__name__)

DUCKDUCKGO_LITE_URL = "https://lite.duckduckgo.com/lite/"
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
REQUEST_TIMEOUT_SECONDS = 12

JsonDict = Dict[str, Any]


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> JsonDict:
    """Search the web with DuckDuckGo Lite and return compact results."""
    normalized_query = " ".join((query or "").split())
    if not normalized_query:
        return {
            "status": "failed",
            "tool": "web_search",
            "error": "query must not be empty",
            "query": query,
            "results": [],
            "source": "duckduckgo_lite",
            "fetched_at": isoformat_local(),
        }

    result_limit = _clamp_max_results(max_results)
    try:
        html = _fetch_duckduckgo_lite(normalized_query)
        results = _parse_duckduckgo_lite_results(html, result_limit)
        return {
            "status": "completed",
            "tool": "web_search",
            "query": normalized_query,
            "results": results,
            "source": "duckduckgo_lite",
            "fetched_at": isoformat_local(),
        }
    except Exception as error:
        logger.warning("Web search failed for query=%s: %s", normalized_query, error, exc_info=True)
        return {
            "status": "failed",
            "tool": "web_search",
            "query": normalized_query,
            "results": [],
            "source": "duckduckgo_lite",
            "fetched_at": isoformat_local(),
            "error": str(error),
        }


def _fetch_duckduckgo_lite(query: str) -> str:
    url = f"{DUCKDUCKGO_LITE_URL}?{urlencode({'q': query})}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Ellie2/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def _parse_duckduckgo_lite_results(html: str, max_results: int) -> List[JsonDict]:
    parser = _DuckDuckGoLiteParser()
    parser.feed(html)
    results: List[JsonDict] = []
    for result in parser.results:
        title = _normalize_text(result.get("title", ""))
        url = _normalize_url(result.get("url", ""))
        snippet = _normalize_text(result.get("snippet", ""))
        if not title or not url:
            continue
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


class _DuckDuckGoLiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[JsonDict] = []
        self._in_result_link = False
        self._in_result_snippet = False
        self._current_href = ""
        self._current_title_parts: List[str] = []
        self._current_snippet_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        class_names = attrs_dict.get("class", "")
        if tag == "a" and "result-link" in class_names:
            self._in_result_link = True
            self._current_href = attrs_dict.get("href", "")
            self._current_title_parts = []
        elif tag == "td" and "result-snippet" in class_names:
            self._in_result_snippet = True
            self._current_snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_result_link:
            self._current_title_parts.append(data)
        elif self._in_result_snippet:
            self._current_snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_link:
            self.results.append(
                {
                    "title": "".join(self._current_title_parts),
                    "url": _decode_duckduckgo_redirect(self._current_href),
                    "snippet": "",
                }
            )
            self._in_result_link = False
            self._current_href = ""
            self._current_title_parts = []
        elif tag == "td" and self._in_result_snippet:
            if self.results:
                self.results[-1]["snippet"] = "".join(self._current_snippet_parts)
            self._in_result_snippet = False
            self._current_snippet_parts = []


def _decode_duckduckgo_redirect(href: str) -> str:
    value = unescape(href or "")
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    uddg = query.get("uddg")
    if uddg:
        return uddg[0]
    return value


def _normalize_url(url: str) -> str:
    return unescape((url or "").strip())


def _normalize_text(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", unescape(text or ""))
    return re.sub(r"\s+", " ", without_tags).strip()


def _clamp_max_results(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_RESULTS
    return max(1, min(MAX_RESULTS_LIMIT, parsed))
