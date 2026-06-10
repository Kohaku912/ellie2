"""
ToolRAG search tool — searches the tool registry via vector similarity.

This is the single entry point for discovering tools. Given a natural-language
query, it returns matching ToolDefinition objects (name, description,
parameters) from the full registry, including low-frequency tools and
PC Bridge / connected-client tools.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ellie.tools.dynamic_retrieval import (
    InMemoryToolVectorStore,
    ToolDefinition,
)
from ellie.tools.registry import get_available_tool_definitions

logger = logging.getLogger(__name__)
JsonDict = Dict[str, Any]


def search_tools(query: str, top_n: int = 8) -> List[ToolDefinition]:
    """Search the tool registry for tools relevant to *query*."""
    all_tools = get_available_tool_definitions()
    store = InMemoryToolVectorStore(all_tools)
    retrieved = store.search(query, top_n)
    return [item.definition for item in retrieved]


def search_tools_as_json(query: str, top_n: int = 8) -> JsonDict:
    """Return search_tools() results as a JSON-serialisable dict."""
    try:
        results = search_tools(query, top_n)
        return {
            "status": "completed",
            "query": query,
            "total": len(results),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "tags": t.tags,
                    "parameters": t.parameters,
                }
                for t in results
            ],
        }
    except Exception as error:
        logger.warning("search_tools failed: %s", error, exc_info=True)
        return {"status": "failed", "error": str(error), "query": query}


def search_tools_handler(arguments: JsonDict) -> JsonDict:
    """Handler called from ToolCallHandler."""
    query = str(arguments.get("query") or "").strip()
    top_n = max(1, min(30, int(arguments.get("top_n", 8))))
    if not query:
        return {"status": "failed", "tool": "search_tools", "error": "query is required"}
    return search_tools_as_json(query, top_n)
