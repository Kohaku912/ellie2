"""
Tool registry for Dynamic Tool RAG.

The PC client also sends its registry in the WebSocket hello message, but this
static registry lets the AI retrieve useful tool schemas before a client event
arrives. Keep PC tool registration here rather than inside the retrieval layer.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from config import DEFAULT_OVERLAY_CLEAR_AFTER_MS
from agent.dynamic_tool_rag import ToolDefinition


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


PC_TOOL_DEFINITIONS: List[ToolDefinition] = [
    _pc_tool(
        "system_snapshot",
        "Return OS, uptime, users, battery, hardware, processes, and active window summary.",
        ["system", "hardware", "process", "window", "status", "状態", "システム", "概要"],
        ["PCの状態を確認して", "system overview", "hardware and active window summary"],
    ),
    _pc_tool(
        "get_processes",
        "Return running process list.",
        ["process", "task", "running", "pid", "プロセス", "実行中"],
        ["実行中のプロセスを見て", "list running processes"],
    ),
    _pc_tool(
        "get_hardware_info",
        "Return CPU, memory, disk, and network information.",
        ["hardware", "cpu", "memory", "disk", "network", "battery", "ハードウェア", "メモリ"],
        ["PCのスペックを確認して", "get CPU memory disk network info"],
    ),
    _pc_tool(
        "get_active_window",
        "Return current foreground window information.",
        ["window", "foreground", "active", "focus", "最前面", "アクティブウィンドウ"],
        ["今開いているウィンドウを教えて", "current active window"],
    ),
    _pc_tool(
        "list_windows",
        "Return top-level Windows desktop windows.",
        ["window", "desktop", "list", "foreground", "ウィンドウ", "一覧"],
        ["開いているウィンドウ一覧を見て", "list desktop windows"],
    ),
    _pc_tool(
        "overlay_show",
        "Show a topmost click-through transparent overlay with text, images, rectangles, ellipses, and lines. Use this for strong proactive visual appeal instead of only writing a suggestion. Every request must include positive clear_after_ms; use 5000 if unspecified.",
        ["overlay", "visual", "transparent", "click-through", "display", "appeal", "text", "image", "shape", "オーバーレイ", "表示", "透明", "アピール", "提案"],
        [
            "画面上に透明オーバーレイで声をかけて",
            "提案をオーバーレイ表示して",
            '{"x":20,"y":20,"width":520,"height":180,"opacity":230,"clear_after_ms":5000,"items":[{"type":"text","text":"Ellieです。少し手伝えそうです。","x":24,"y":24,"size":28,"color":"#ffffff"}]}',
        ],
        OVERLAY_CONFIG_SCHEMA,
    ),
    _pc_tool(
        "overlay_update",
        "Replace the click-through transparent overlay contents using the same schema as overlay_show. Every update must include positive clear_after_ms; use 5000 if unspecified.",
        ["overlay", "update", "visual", "transparent", "オーバーレイ", "更新", "表示"],
        ["オーバーレイの内容を更新して"],
        OVERLAY_CONFIG_SCHEMA,
    ),
    _pc_tool(
        "overlay_hide",
        "Hide the transparent overlay window.",
        ["overlay", "hide", "close", "オーバーレイ", "非表示", "消す"],
        ["オーバーレイを消して"],
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _pc_tool(
        "overlay_clear",
        "Show an empty transparent overlay window.",
        ["overlay", "clear", "empty", "オーバーレイ", "クリア"],
        ["オーバーレイをクリアして"],
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _pc_tool(
        "overlay_status",
        "Return overlay visibility and click-through state.",
        ["overlay", "status", "visible", "click-through", "オーバーレイ", "状態"],
        ["オーバーレイの状態を確認して"],
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _pc_tool(
        "focus_window",
        "Bring a window to foreground by hwnd.",
        ["window", "focus", "foreground", "hwnd", "前面", "フォーカス"],
        ["指定したウィンドウを前面にして", '{"hwnd":123456}'],
    ),
    _pc_tool(
        "move_resize_window",
        "Move and resize a window by hwnd.",
        ["window", "move", "resize", "position", "size", "移動", "リサイズ"],
        ["ウィンドウを移動して", '{"hwnd":123456,"x":0,"y":0,"width":1280,"height":720}'],
    ),
    _pc_tool(
        "show_window",
        "Hide, show, minimize, maximize, or restore a window.",
        ["window", "hide", "show", "minimize", "maximize", "restore", "最小化", "最大化"],
        ["ウィンドウを最小化して", '{"hwnd":123456,"command":"minimize"}'],
    ),
    _pc_tool(
        "close_window",
        "Send WM_CLOSE to a window by hwnd.",
        ["window", "close", "hwnd", "閉じる"],
        ["指定したウィンドウを閉じて", '{"hwnd":123456}'],
    ),
    _pc_tool(
        "launch_application",
        "Launch an application by alias, executable name, or path.",
        ["app", "application", "launch", "open", "notepad", "calc", "browser", "アプリ", "起動", "開く", "メモ帳", "電卓"],
        ["メモ帳を起動して", "電卓を開いて", "open notepad", '{"app_name":"notepad"}'],
        {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Application alias, executable name, or path.",
                },
            },
            "required": ["app_name"],
            "additionalProperties": True,
        },
    ),
    _pc_tool(
        "execute_shell",
        "Run a PowerShell command and return stdout/stderr/exit code.",
        ["shell", "powershell", "command", "terminal", "execute", "コマンド", "実行"],
        ["PowerShellでコマンドを実行して", '{"command":"Write-Output hello"}'],
    ),
    _pc_tool("kill_process", "Kill a process by pid or image name.", ["process", "kill", "pid", "terminate", "終了"], ['{"pid":1234}', '{"image_name":"notepad.exe"}']),
    _pc_tool("shutdown", "Shut down the PC immediately.", ["power", "shutdown", "destructive", "電源", "シャットダウン"], ["PCをシャットダウンして"]),
    _pc_tool("reboot", "Reboot the PC immediately.", ["power", "reboot", "restart", "destructive", "再起動"], ["PCを再起動して"]),
    _pc_tool("sleep", "Put the PC to sleep.", ["power", "sleep", "スリープ"], ["PCをスリープして"]),
    _pc_tool("lock_screen", "Lock the current Windows session.", ["power", "lock", "screen", "ロック"], ["画面をロックして"]),
    _pc_tool("logout", "Log out the current Windows session.", ["power", "logout", "signout", "ログアウト"], ["Windowsからログアウトして"]),
    _pc_tool(
        "take_screenshot",
        "Capture the primary display as base64 PNG.",
        ["screen", "screenshot", "display", "visual", "image", "画面", "スクリーンショット", "撮影", "キャプチャ"],
        ["画面のスクリーンショットを撮って", "take a screenshot"],
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _pc_tool("get_clipboard", "Return clipboard text.", ["clipboard", "copy", "paste", "クリップボード"], ["クリップボードを読んで"]),
    _pc_tool("set_clipboard", "Set clipboard text.", ["clipboard", "copy", "paste", "クリップボード"], ["クリップボードに文字を入れて", '{"text":"hello"}']),
    _pc_tool(
        "notify",
        "Show a desktop notification to actively appeal to the user. Use this when Ellie wants to proactively say something, suggest help, or get attention during autonomous runs instead of only writing text.",
        ["notify", "notification", "alert", "appeal", "proactive", "通知", "アピール", "知らせる", "話しかける", "提案"],
        ["自律的にユーザーへ声をかけて", "提案を通知で出して", '{"title":"Ellie","body":"少しだけ手伝えそうなことがあります。"}'],
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title."},
                "body": {"type": "string", "description": "Notification body."},
            },
            "required": ["title", "body"],
            "additionalProperties": False,
        },
    ),
    _pc_tool("mouse_move", "Move the mouse cursor.", ["mouse", "cursor", "move", "マウス", "移動"], ['{"x":100,"y":100}']),
    _pc_tool("mouse_click", "Click a mouse button.", ["mouse", "click", "button", "クリック"], ['{"button":"left"}']),
    _pc_tool("mouse_scroll", "Scroll the mouse wheel.", ["mouse", "scroll", "wheel", "スクロール"], ['{"delta_y":-5}']),
    _pc_tool("keyboard_type", "Type text via keyboard input simulation.", ["keyboard", "type", "text", "input", "入力", "キーボード"], ['{"text":"hello"}']),
    _pc_tool("keyboard_shortcut", "Press and release a key chord.", ["keyboard", "shortcut", "hotkey", "key", "ショートカット"], ['{"keys":["ctrl","c"]}']),
    _pc_tool("media_key", "Send a media key action.", ["media", "volume", "play", "pause", "音量", "再生"], ['{"key":"play_pause"}']),
    _pc_tool("list_directory", "List directory entries.", ["file", "directory", "folder", "list", "ファイル", "フォルダ", "一覧"], ['{"path":"C:\\\\Users"}']),
    _pc_tool("read_file_base64", "Read a file and return base64 bytes.", ["file", "read", "base64", "ファイル", "読む"], ['{"path":"C:\\\\temp\\\\file.txt"}']),
    _pc_tool("write_file_base64", "Write base64 bytes to a file.", ["file", "write", "base64", "ファイル", "書く"], ['{"path":"C:\\\\temp\\\\file.txt","data":"..."}']),
    _pc_tool("copy_file", "Copy a file.", ["file", "copy", "コピー"], ['{"source":"C:\\\\a.txt","destination":"C:\\\\b.txt"}']),
    _pc_tool("move_file", "Move a file or directory.", ["file", "move", "directory", "移動"], ['{"source":"C:\\\\a.txt","destination":"D:\\\\a.txt"}']),
    _pc_tool("rename_file", "Rename a file or directory within its parent.", ["file", "rename", "directory", "リネーム", "名前変更"], ['{"path":"C:\\\\a.txt","new_name":"b.txt"}']),
    _pc_tool("delete_path", "Delete a file or directory recursively.", ["file", "delete", "directory", "destructive", "削除"], ['{"path":"C:\\\\temp\\\\old"}']),
    _pc_tool("discord_status", "Return Discord IPC/token state without secrets.", ["discord", "status", "token", "状態"]),
    _pc_tool("discord_connect", "Connect and authenticate to Discord IPC.", ["discord", "connect", "ipc", "接続"]),
    _pc_tool("discord_disconnect", "Disconnect from Discord IPC.", ["discord", "disconnect", "ipc", "切断"]),
    _pc_tool("discord_refresh_tokens", "Refresh Discord OAuth tokens and store them locally.", ["discord", "token", "refresh", "oauth", "更新"]),
    _pc_tool("discord_get_guilds", "Run Discord GET_GUILDS.", ["discord", "guild", "server", "サーバー"]),
    _pc_tool("discord_get_guild", "Run Discord GET_GUILD.", ["discord", "guild", "server", "サーバー"]),
    _pc_tool(
        "discord_get_channels",
        "Run Discord GET_CHANNELS. Use this after discord_get_guilds to find voice/text channels in a guild. Requires guild_id.",
        ["discord", "channel", "voice", "guild", "server", "通話", "ボイス", "チャンネル", "サーバー"],
        ["memoサーバーの通話チャンネルを探して", '{"guild_id":"123456789"}'],
    ),
    _pc_tool("discord_get_channel", "Run Discord GET_CHANNEL.", ["discord", "channel", "チャンネル"]),
    _pc_tool("discord_get_voice_settings", "Run Discord GET_VOICE_SETTINGS.", ["discord", "voice", "settings", "音声"]),
    _pc_tool("discord_set_voice_settings", "Run Discord SET_VOICE_SETTINGS.", ["discord", "voice", "settings", "音声"]),
    _pc_tool(
        "discord_get_voice_channel",
        "Run Discord GET_SELECTED_VOICE_CHANNEL. Use this to check the currently selected voice channel.",
        ["discord", "voice", "channel", "call", "音声", "通話", "ボイス"],
    ),
    _pc_tool(
        "discord_select_voice_channel",
        "Run Discord SELECT_VOICE_CHANNEL. Join, connect to, switch, or leave a Discord voice channel. To join requests like 'memoサーバーの通話に参加して', first get guilds, find the guild, get channels, then call this with the voice channel_id. To leave/disconnect from voice, call this same tool with channel_id null. Do not invent a separate voice-leave tool.",
        ["discord", "voice", "channel", "call", "join", "leave", "select", "execute", "connect", "disconnect", "参加", "退出", "抜ける", "通話", "ボイス", "実行", "接続", "切断"],
        ["memoサーバーの通話に参加して", "memoサーバーの通話を実行して", "Discordのボイスチャンネルに入って", "通話から退出して", '{"channel_id":"123456789"}', '{"channel_id":null}'],
    ),
    _pc_tool("discord_select_text_channel", "Run Discord SELECT_TEXT_CHANNEL.", ["discord", "text", "channel", "テキスト"]),
    _pc_tool("discord_set_user_voice_settings", "Run Discord SET_USER_VOICE_SETTINGS.", ["discord", "voice", "user", "settings"]),
    _pc_tool("discord_set_activity", "Run Discord SET_ACTIVITY.", ["discord", "activity", "presence", "アクティビティ"]),
    _pc_tool("discord_send_activity_join_invite", "Run Discord SEND_ACTIVITY_JOIN_INVITE.", ["discord", "activity", "invite", "招待"]),
    _pc_tool("discord_close_activity_request", "Run Discord CLOSE_ACTIVITY_REQUEST.", ["discord", "activity", "close"]),
    _pc_tool("discord_subscribe", "Run Discord SUBSCRIBE.", ["discord", "subscribe", "event", "購読"]),
    _pc_tool("discord_unsubscribe", "Run Discord UNSUBSCRIBE.", ["discord", "unsubscribe", "event", "解除"]),
    _pc_tool("discord_command", "Send an arbitrary Discord RPC command.", ["discord", "rpc", "command", "advanced"]),
]


PC_TOOL_NAMES = {tool.name for tool in PC_TOOL_DEFINITIONS}


DEFAULT_TOOL_DEFINITIONS: List[ToolDefinition] = [
    *PC_TOOL_DEFINITIONS,
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
            "Local autonomous tool. Inspect, validate, or safely edit Ellie2's own code inside the project root. "
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
            '{"action":"verify","paths":["agent/social_needs.py","agent/cerebras_agent.py"]}',
        ],
        handler_name="self_development",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["inspect", "verify", "write_file"],
                    "default": "inspect",
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
        name="social_feedback_check",
        description=(
            "Local autonomous tool. Check Twitter/X feedback only when a dedicated PC Tool is connected. "
            "If no Twitter/X feedback Tool exists, it does not post or fetch anything and returns a draft only."
        ),
        tags=[
            "approval",
            "social",
            "feedback",
            "twitter",
            "x",
            "notifications",
            "承認",
            "反応",
            "SNS",
        ],
        examples=[
            "承認欲求が深いので、Twitter/Xの反応Toolが接続されていれば通知を確認する",
            "Twitter Toolが未接続なら実投稿せず投稿案だけ作る",
            '{"draft":"静かな自律にも、ちゃんと温度がある。"}',
        ],
        handler_name="social_feedback_check",
        parameters={
            "type": "object",
            "properties": {
                "draft": {
                    "type": "string",
                    "description": "Fallback post draft when Twitter/X tools are unavailable.",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments forwarded to the connected Twitter/X feedback PC Tool.",
                    "additionalProperties": True,
                },
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
            '{"instruction":"XMCPでXの反応を確認し、必要なら返事を考える","run_after_seconds":1800,"reason":"承認欲求の長期充足"}',
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

    for tool in _xmcp_tool_definitions():
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
        from agent.pc_tool_bridge import get_connected_pc_tools

        return get_connected_pc_tools()
    except Exception:
        return []


def _xmcp_tool_definitions() -> List[ToolDefinition]:
    try:
        from agent.mcp_client import get_xmcp_tool_definitions

        return get_xmcp_tool_definitions()
    except Exception:
        return []
