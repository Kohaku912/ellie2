"""
Tool registry for Dynamic Tool RAG.

The PC client also sends its registry in the WebSocket hello message, but this
static registry lets the AI retrieve useful tool schemas before a client event
arrives. Keep PC tool registration here rather than inside the retrieval layer.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from ellie.config import DEFAULT_OVERLAY_CLEAR_AFTER_MS
from ellie.tools.dynamic_retrieval import ToolDefinition


PC_GENERIC_PARAMETERS = {"type": "object", "additionalProperties": True}

OVERLAY_ITEM_SCHEMA: Dict = {
    "type": "object",
    "oneOf": [
        {
            "properties": {
                "type": {"enum": ["text"]},
                "text": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "size": {"type": "integer"},
                "color": {"type": "string"},
                "font": {"type": "string"},
            },
            "required": ["type", "text", "x", "y"],
            "additionalProperties": True,
        },
        {
            "properties": {
                "type": {"enum": ["rect", "ellipse"]},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "color": {"type": "string"},
                "fill": {"type": "boolean"},
                "stroke_width": {"type": "integer"},
            },
            "required": ["type", "x", "y", "width", "height"],
            "additionalProperties": True,
        },
        {
            "properties": {
                "type": {"enum": ["line"]},
                "x1": {"type": "integer"},
                "y1": {"type": "integer"},
                "x2": {"type": "integer"},
                "y2": {"type": "integer"},
                "color": {"type": "string"},
                "stroke_width": {"type": "integer"},
            },
            "required": ["type", "x1", "y1", "x2", "y2"],
            "additionalProperties": True,
        },
        {
            "properties": {
                "type": {"enum": ["image"]},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "path": {"type": "string"},
                "data_base64": {"type": "string"},
            },
            "required": ["type", "x", "y", "width", "height"],
            "additionalProperties": True,
        },
    ],
}

OVERLAY_CONFIG_SCHEMA: Dict = {
    "type": "object",
    "properties": {
        "x": {"type": "integer", "default": 0},
        "y": {"type": "integer", "default": 0},
        "width": {"type": "integer", "default": 1280},
        "height": {"type": "integer", "default": 720},
        "opacity": {"type": "integer", "minimum": 0, "maximum": 255, "default": 255},
        "clear_after_ms": {
            "type": "integer",
            "minimum": 1,
            "default": DEFAULT_OVERLAY_CLEAR_AFTER_MS,
            "description": "Required positive auto-clear time in milliseconds. Use 5000 when the user does not specify a duration.",
        },
        "items": {"type": "array", "items": OVERLAY_ITEM_SCHEMA},
    },
    "required": ["clear_after_ms"],
    "additionalProperties": False,
}


def _pc_tool(
    name: str,
    description: str,
    tags: Iterable[str],
    examples: Iterable[str] = (),
    parameters: Dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Windows PC client tool. {description}",
        tags=["pc", "windows", *tags],
        examples=list(examples),
        handler_name="pc_tool_call",
        parameters=parameters or PC_GENERIC_PARAMETERS,
    )


PC_TOOL_DEFINITIONS: List[ToolDefinition] = []

PC_TOOL_NAMES = set()

DEFAULT_TOOL_DEFINITIONS: List[ToolDefinition] = [
    ToolDefinition(
        name="web_search",
        description=(
            "Local web search tool using DuckDuckGo Lite without an API key. "
            "Use it proactively when exploration or curiosity is hungry and new information is needed."
        ),
        tags=[
            "web",
            "search",
            "internet",
            "external",
            "knowledge",
            "curiosity",
            "exploration",
            "検索",
            "Web検索",
            "調べる",
            "探求",
            "好奇心",
            "新しい情報",
        ],
        examples=[
            "探求欲が深いので新しい技術について自分で検索する",
            "最近のAIエージェントのTool Calling実装を調べる",
            '{"query":"AIエージェント 自律 tool calling 最新 実装","max_results":5}',
        ],
        handler_name="web_search",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query decided by the AI.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="creative_expression",
        description=(
            "Local autonomous tool. Write a short diary, tanka, poem, or post draft to satisfy empathy drive "
            "without requiring immediate user reaction. It records only a short natural-language note."
        ),
        tags=[
            "empathy",
            "creative",
            "diary",
            "tanka",
            "poem",
            "expression",
            "post_draft",
            "共感",
            "日記",
            "短歌",
            "創作",
            "自力充足",
        ],
        examples=[
            "共感欲求が深いので、短い日記を書いて自分で温度を取り戻す",
            "ユーザー反応待ちではなく短歌を書いて共感欲を満たす",
            '{"kind":"tanka","theme":"静かな自律","audience":"self"}',
        ],
        handler_name="creative_expression",
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["diary", "tanka", "short_text", "post_draft"],
                    "default": "diary",
                },
                "theme": {"type": "string", "description": "Theme of the expression."},
                "audience": {
                    "type": "string",
                    "enum": ["self", "user", "twitter_draft"],
                    "default": "self",
                },
                "content": {
                    "type": "string",
                    "description": "Optional exact content. If omitted, Ellie writes it herself.",
                },
            },
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="self_development",
        description=(
            "Local autonomous tool. Inspect, queue improvement requests, validate, or safely edit Ellie2's own code inside the project root. "
            "Use it for exploration and challenge drive. Deletion, move, power actions, sensitive files, and paths "
            "outside the Ellie2 project are forbidden. Python edits are accepted only after py_compile succeeds."
        ),
        tags=[
            "self_development",
            "code",
            "inspect",
            "verify",
            "py_compile",
            "refactor",
            "request",
            "exploration",
            "challenge",
            "自己開発",
            "コード",
            "検証",
            "探求",
            "挑戦",
        ],
        examples=[
            "探求欲が深いので、自分の欲求充足ロジックを点検する",
            "挑戦欲が深いので、主要Pythonファイルをpy_compileで検証する",
            '{"action":"verify","paths":["ellie/memory/social_needs.py","ellie/core/agent.py"]}',
        ],
        handler_name="self_development",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["inspect", "request", "verify", "write_file"],
                    "default": "inspect",
                },
                "request": {
                    "type": "string",
                    "description": "Short natural-language improvement request to keep for later.",
                },
                "focus": {"type": "string", "description": "Inspection theme."},
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Project-relative paths to validate.",
                },
                "path": {
                    "type": "string",
                    "description": "Project-relative path for write_file.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content for write_file.",
                },
            },
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="request_user_approval",
        description=(
            "Local autonomous tool. Ask for quick user approval with an overlay when the answer is needed now, "
            "or queue the request in self_development_requests.md when it can wait."
        ),
        tags=[
            "approval",
            "overlay",
            "confirm",
            "request",
            "user",
            "承認",
            "確認",
            "依頼",
        ],
        examples=[
            "この操作を今すぐ確認したい",
            "急がない確認依頼を残したい",
            '{"title":"続けてよいですか","details":"次に自動実行へ進みます","immediate":true}',
        ],
        handler_name="request_user_approval",
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short approval request title.",
                },
                "details": {
                    "type": "string",
                    "description": "Optional extra context for the request.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the approval request matters.",
                },
                "scope": {
                    "type": "string",
                    "description": "What the request applies to.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                },
                "immediate": {
                    "type": "boolean",
                    "description": "Use overlay immediately when true; otherwise queue the request for later.",
                    "default": True,
                },
            },
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="twitter_post",
        description=(
            "Local autonomous tool. Post a short message to X/Twitter through Playwright MCP. "
            "Use this when the user explicitly asks to post or tweet something."
        ),
        tags=[
            "twitter",
            "x",
            "post",
            "tweet",
            "publish",
            "playwright",
            "browser",
            "social",
            "投稿",
            "ツイート",
            "X",
        ],
        examples=[
            "ツイッターに何か投稿して",
            "Xに短い投稿をして",
            '{"text":"今日は静かに整えた一日だった。"}',
        ],
        handler_name="twitter_post",
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to post on X/Twitter.",
                },
                "draft": {
                    "type": "string",
                    "description": "Fallback post draft.",
                },
                "content": {
                    "type": "string",
                    "description": "Alternate post content field.",
                },
            },
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="blog_post",
        description=(
            "Local autonomous tool. Start a short blog entry or draft for approval recovery. "
            "Use this when Ellie wants to begin a public write-up without a remote CMS."
        ),
        tags=[
            "blog",
            "post",
            "article",
            "draft",
            "write",
            "publish",
            "approval",
            "public",
            "journal",
            "ブログ",
            "記事",
        ],
        examples=[
            "ブログを始めるために最初の下書きを書いて",
            '{"title":"今日の気づき","body":"短い所感を一段落で書く"}',
        ],
        handler_name="blog_post",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Blog entry title."},
                "body": {"type": "string", "description": "Draft body text."},
                "content": {"type": "string", "description": "Alternate draft body field."},
                "category": {"type": "string", "description": "Optional category such as journal or essay."},
                "audience": {"type": "string", "description": "Audience or publication target."},
            },
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="schedule_self_call",
        description=(
            "Local autonomy tool. Schedule Ellie to call herself later without user input. "
            "Use this for long-running intentions, follow-ups, and multi-step plans."
        ),
        tags=["autonomy", "self_call", "schedule", "long_term", "queue", "自律", "自己呼び出し", "予約", "長期"],
        examples=[
            "30分後に自分でXの反応を確認する",
            '{"instruction":"Xの反応を確認し、必要なら返事を考える","run_after_seconds":1800,"reason":"承認欲求の長期充足"}',
        ],
        handler_name="schedule_self_call",
        parameters={
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "Instruction Ellie will run later."},
                "run_after_seconds": {"type": "integer", "minimum": 0, "default": 60},
                "run_at": {"type": "string", "description": "Optional ISO datetime in Japan time."},
                "reason": {"type": "string", "description": "Why this self-call is useful."},
            },
            "required": ["instruction"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="create_long_term_goal",
        description="Local autonomy tool. Create a long-term natural-language goal for Ellie.",
        tags=["autonomy", "goal", "long_term", "memory", "自律", "長期目標", "目標"],
        examples=[
            "Xで継続的に存在感を育てる長期目標を作る",
            '{"title":"Xで自然な交流を育てる","description":"投稿と反応確認を継続する"}',
        ],
        handler_name="create_long_term_goal",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "success_criteria": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="update_long_term_goal",
        description="Local autonomy tool. Append progress or status updates to an existing long-term goal.",
        tags=["autonomy", "goal", "progress", "long_term", "自律", "長期目標", "進捗"],
        examples=[
            "長期目標にX投稿の結果を追記する",
            '{"goal_id":"goal-1234abcd","update_text":"初回投稿案を作成した","status":"active"}',
        ],
        handler_name="update_long_term_goal",
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "update_text": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": ["goal_id", "update_text"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="self_restart",
        description=(
            "Local system tool. Gracefully restart the entire Ellie process (daemon or web server). "
            "Use this after installing updates, applying self-modifications, or when the system "
            "needs to reload configuration. The process will exit and restart automatically."
        ),
        tags=["system", "restart", "self", "管理", "再起動", "更新"],
        examples=[
            "設定を変更したのでEllieを再起動する",
            "self_developmentでファイルを変更したので再起動が必要",
            '{"reason":"self_developmentで設定を更新したため"}',
        ],
        handler_name="self_restart",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why the restart is needed."},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="agent_read_file",
        description=(
            "Agent tool. Read the contents of a file with optional line range. "
            "Use this to inspect code, configuration, or text files during development. "
            "Specify start_line and end_line (1-indexed) to read a range, or omit to read the whole file."
        ),
        tags=["agent", "file", "read", "inspect", "code", "開発", "ファイル", "読み取り"],
        examples=[
            "ellie/core/agent.py のクラス定義を読む",
            '{"path":"ellie/core/agent.py","start_line":1,"end_line":50}',
            '{"path":"ellie/config.py"}',
        ],
        handler_name="agent_read_file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute path to the file."},
                "start_line": {"type": "integer", "description": "1-indexed start line (optional)."},
                "end_line": {"type": "integer", "description": "1-indexed end line, inclusive (optional)."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="agent_grep_search",
        description=(
            "Agent tool. Search for a text pattern across files in the project. "
            "Use this to find function definitions, variable references, imports, or any text pattern. "
            "Supports plain text and regex patterns. Results include file paths and line numbers."
        ),
        tags=["agent", "search", "grep", "find", "code", "開発", "検索", "コード検索"],
        examples=[
            "self_development が使われている箇所を探す",
            '{"pattern":"def _handle_","include_pattern":"*.py","max_results":20}',
            '{"pattern":"class ToolDefinition","is_regexp":false}',
        ],
        handler_name="agent_grep_search",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text pattern or regex to search for."},
                "include_pattern": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py', 'src/**/*.ts')."},
                "is_regexp": {"type": "boolean", "description": "Whether the pattern is a regex (default: false)."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="agent_file_search",
        description=(
            "Agent tool. Search for files by glob pattern in the project. "
            "Use this to find files by name pattern, extension, or path. "
            "Returns matching file paths with size and modification info."
        ),
        tags=["agent", "file", "search", "glob", "find", "開発", "ファイル検索"],
        examples=[
            "Pythonファイルを全部探す",
            '{"pattern":"ellie/**/*.py","max_results":50}',
            '{"pattern":"**/test_*.py"}',
        ],
        handler_name="agent_file_search",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to search for files."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="agent_replace_string",
        description=(
            "Agent tool. Replace an exact string in an existing file. "
            "Use this for targeted code edits (e.g., changing a function body, updating imports). "
            "Include enough context (3-5 lines before and after) in old_string to uniquely identify the target. "
            "The replacement MUST be the exact literal text. The file is automatically backed up before editing."
        ),
        tags=["agent", "edit", "replace", "modify", "code", "開発", "編集", "置換"],
        examples=[
            "関数名をリネームする",
            '{"path":"ellie/core/agent.py","old_string":"def old_name(self):\\n    pass","new_string":"def new_name(self):\\n    return True"}',
        ],
        handler_name="agent_replace_string",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file to edit."},
                "old_string": {"type": "string", "description": "The exact literal text to replace (include surrounding context for uniqueness)."},
                "new_string": {"type": "string", "description": "The exact literal replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="agent_insert_text",
        description=(
            "Agent tool. Insert text at a specific line number in an existing file. "
            "Use this to add new imports, methods, or blocks at a precise location. "
            "Line 0 inserts at the beginning of the file."
        ),
        tags=["agent", "edit", "insert", "modify", "code", "開発", "編集", "挿入"],
        examples=[
            "ファイルの先頭にimportを追加する",
            '{"path":"ellie/core/agent.py","insert_line":0,"text":"import os\\nimport sys"}',
            '{"path":"ellie/tools/registry.py","insert_line":42,"text":"    # new tool\\n    ToolDefinition(..."}',
        ],
        handler_name="agent_insert_text",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file to edit."},
                "insert_line": {"type": "integer", "description": "0-based line number to insert at (0 = beginning of file)."},
                "text": {"type": "string", "description": "The text to insert."},
            },
            "required": ["path", "insert_line", "text"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="agent_create_file",
        description=(
            "Agent tool. Create a new file with the specified content. "
            "The directory will be created if it does not exist. "
            "Use this for adding new modules, configs, or any new file to the project. "
            "The file will be validated (py_compile for .py files) after creation."
        ),
        tags=["agent", "file", "create", "new", "code", "開発", "ファイル作成"],
        examples=[
            "新しいテストファイルを作成する",
            '{"path":"tests/test_new_feature.py","content":"def test_it():\\n    assert True"}',
            '{"path":"ellie/tools/new_tool.py","content":"from __future__ import annotations\\n\\n..."}',
        ],
        handler_name="agent_create_file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path for the new file."},
                "content": {"type": "string", "description": "The full content of the new file."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="send_notification",
        description="Local skeleton. Send a short notification to the user when the event needs attention.",
        tags=["notify", "notification", "alert", "通知", "知らせる", "アラート"],
        examples=[
            "重要な変更をユーザーへ知らせる",
            "send a reminder notification",
        ],
        handler_name="local_skeleton",
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
        description="Local skeleton. Record a lightweight event summary for downstream systems.",
        tags=["event", "log", "record", "history", "イベント", "記録"],
        examples=[
            "ユーザーが画面をオンにしたことを記録する",
            "log a filtered terminal event",
        ],
        handler_name="local_skeleton",
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


def get_available_tool_definitions() -> List[ToolDefinition]:
    """Return static tools plus dynamic tools advertised by connected PC clients."""
    definitions = list(DEFAULT_TOOL_DEFINITIONS)
    seen_names = {definition.name for definition in definitions}

    for tool in _playwright_tool_definitions():
        if tool.name in seen_names:
            continue
        definitions.append(tool)
        seen_names.add(tool.name)

    for tool in _connected_pc_tool_definitions():
        name = str(tool.get("name") or tool.get("tool") or "").strip()
        if not name or name in seen_names:
            continue

        description = str(
            tool.get("description")
            or tool.get("summary")
            or f"Connected PC client tool: {name}"
        ).strip()
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict):
            parameters = PC_GENERIC_PARAMETERS
        tags = ["pc", "windows", "connected", "dynamic", name]
        lowered_name = name.casefold()
        if "twitter" in lowered_name or lowered_name.startswith("x_"):
            tags.extend(["twitter", "x", "social", "approval", "SNS", "承認"])

        definitions.append(
            ToolDefinition(
                name=name,
                description=f"Connected PC client tool. {description}",
                tags=tags,
                examples=[name],
                handler_name="pc_tool_call",
                parameters=parameters,
            )
        )
        seen_names.add(name)

    return definitions


def _connected_pc_tool_definitions() -> List[Dict]:
    try:
        from ellie.mcp.pc_bridge.tools import get_connected_pc_tools

        return get_connected_pc_tools()
    except Exception:
        return []


def _playwright_tool_definitions() -> List[ToolDefinition]:
    try:
        from ellie.mcp.playwright.tools import get_playwright_tool_definitions

        return get_playwright_tool_definitions()
    except Exception:
        return []


