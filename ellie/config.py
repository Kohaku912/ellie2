"""Configuration for Autonomous AI Agent"""
import os
from pathlib import Path

# Load environment variables
try:
    from dotenv import load_dotenv

    # Load from explicit path
    env_file = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_file)
except ImportError:
    pass  # dotenv is optional

# Cerebras API Configuration
# Cerebras SDK automatically appends /v1 to the base_url
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "test_key_placeholder")
CEREBRAS_BASE_URL = os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
HEAVY_TASK_MAX_STEPS = max(8, int(os.getenv("HEAVY_TASK_MAX_STEPS", "10")))

# Agent Configuration
AGENT_TIMEZONE = os.getenv("AGENT_TIMEZONE", "Asia/Tokyo")
AGENT_NAME = os.getenv("AGENT_NAME", "Ellie")

# Web dashboard configuration
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Storage Configuration
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", str(DATA_DIR / "memory")))
SELF_DIR = Path(os.getenv("SELF_DIR", str(DATA_DIR / "self")))
LOG_DIR = Path(os.getenv("LOG_DIR", str(DATA_DIR / "logs")))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", str(DATA_DIR / "archive")))
VENDOR_DIR = Path(os.getenv("VENDOR_DIR", str(DATA_DIR / "vendor")))
MEMORY_DB_FILE = Path(os.getenv("MEMORY_DB_FILE", str(MEMORY_DIR / "memory.sqlite3")))

# Create directories if they don't exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
SELF_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
VENDOR_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR = DATA_DIR / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DRIVE_STATE_FILE = Path(os.getenv("DRIVE_STATE_FILE", str(RUNTIME_DIR / "drive_system.json")))

# File paths
MEMORY_FILE = MEMORY_DIR / "memory.md"
LONG_TERM_MEMORY_FILE = MEMORY_DIR / "long_term_memory.md"
SELF_FILE = SELF_DIR / "self.md"
SELF_STATE_FILE = SELF_DIR / "state.md"
SELF_DEVELOPMENT_REQUESTS_FILE = SELF_DIR / "self_development_requests.md"
BLOG_DRAFTS_FILE = MEMORY_DIR / "blog_drafts.md"
SOCIAL_NEEDS_FILE = SELF_DIR / "social_needs.json"
SOCIAL_NEEDS_RECOVERY_HISTORY_FILE = SELF_DIR / "social_needs_recovery_history.json"
SOCIAL_NEEDS_EVAL_API_KEY = os.getenv("SOCIAL_NEEDS_EVAL_API_KEY", CEREBRAS_API_KEY)
TASK_LOG_FILE = MEMORY_DIR / "task_log.json"
EXECUTION_LOG_FILE = LOG_DIR / "execution.log"
LONG_TERM_GOALS_FILE = MEMORY_DIR / "long_term_goals.md"
AUTONOMY_QUEUE_FILE = MEMORY_DIR / "autonomy_queue.jsonl"
AUTONOMY_LOCK_FILE = RUNTIME_DIR / "autonomy.lock"

# Playwright MCP / browser automation configuration
PLAYWRIGHT_MCP_ENABLED = os.getenv("PLAYWRIGHT_MCP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
PLAYWRIGHT_MCP_AUTO_INSTALL = os.getenv("PLAYWRIGHT_MCP_AUTO_INSTALL", "true").strip().lower() in {"1", "true", "yes", "on"}
PLAYWRIGHT_MCP_HOST = os.getenv("PLAYWRIGHT_MCP_HOST", "127.0.0.1")
PLAYWRIGHT_MCP_PORT = int(os.getenv("PLAYWRIGHT_MCP_PORT", "8931"))
PLAYWRIGHT_MCP_SERVER_URL = os.getenv("PLAYWRIGHT_MCP_SERVER_URL", f"http://{PLAYWRIGHT_MCP_HOST}:{PLAYWRIGHT_MCP_PORT}/mcp")
PLAYWRIGHT_MCP_BROWSER = os.getenv("PLAYWRIGHT_MCP_BROWSER", "chromium")
PLAYWRIGHT_MCP_HEADLESS = os.getenv("PLAYWRIGHT_MCP_HEADLESS", "false").strip().lower() in {"1", "true", "yes", "on"}
PLAYWRIGHT_DIR = Path(os.getenv("PLAYWRIGHT_DIR", str(VENDOR_DIR / "playwright")))
PLAYWRIGHT_USER_DATA_DIR = Path(os.getenv("PLAYWRIGHT_USER_DATA_DIR", str(PLAYWRIGHT_DIR / "user-data")))
PLAYWRIGHT_STORAGE_STATE_FILE = Path(os.getenv("PLAYWRIGHT_STORAGE_STATE_FILE", str(PLAYWRIGHT_DIR / "storage_state.json")))

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

MEMORY_DELETE_DAYS = 30
DEFAULT_OVERLAY_CLEAR_AFTER_MS = int(os.getenv("DEFAULT_OVERLAY_CLEAR_AFTER_MS", "5000"))

# API parameters
REQUEST_TIMEOUT = 30
MAX_TOKENS = 3000
TEMPERATURE = 0.7

# Note: For production use, ensure CEREBRAS_API_KEY is set to a valid key in .env or environment

# Lightweight top-level tool index shown to the model before any context-specific ToolRAG content.
TOOL_CAPABILITY_INDEX = """## 🔍 search_tools（ToolRAG — ツール発見）
search_tools はツールレジストリ全体をベクトル検索し、タスクに合ったツールを発見します。
これを使って、ツール名が不明な場合や、特定の機能を持つツールを探すことができます。

検索で見つかるツールの種類:
- Web検索・コード検索・ファイル操作
- ブラウザ自動化（Playwright）
- 自己開発・コード編集・検証
- AI記憶操作・長期目標管理
- クリエイティブ表現（日記・短歌・ブログ）
- PC操作・システム制御（PC Bridge接続時）
- スケジューリング・自己呼び出し

## 📧 アカウント・メール情報
- Gmail: k3320138@gmail.com（ブラウザにログイン済み、自由に使用可）
- ブラウザ上でのWebサービスへのアカウント登録は許可されています
- メールアドレス確認が必要な場合、上記Gmailが使用可能です

## 🧠 Memory Tools
- agent_add_memory: 新しい記憶を追加（カテゴリ・重要度・コア記憶指定可）。
- agent_search_memory: 記憶を検索・想起。
- agent_add_working_memory: ワーキングメモリに追加（TTL付き短期記憶）。
- agent_get_working_memory: 有効なワーキングメモリ一覧を取得。
- agent_create_episode: 複数の記憶をエピソード（体験の束）にまとめる。
- agent_search_episodes: エピソードを意味検索。
- agent_get_episode: エピソードの詳細と構成記憶を取得。
- agent_link_memories: 記憶間に因果/関連リンクを作成。
- agent_get_related_memories: リンクされた関連記憶を取得。
- agent_consolidate_memories: 重要記憶を統合（hippocampal replay風）。
- agent_get_memory_stats: 記憶の統計情報を取得。
- agent_set_core_memory: 記憶を核記憶（アイデンティティ）に設定。
- agent_get_core_memories: 核記憶一覧を取得。
- agent_list_recent_memories: 最近の記憶を時系列で表示。
- schedule_self_call: 未来の自己呼び出しを予約。
- create_long_term_goal / update_long_term_goal: 長期目標の作成・更新。

## 📁 File Tools
- agent_read_file: ファイルを行範囲指定で読み取り。
- agent_grep_search: ファイル内のテキスト/正規表現パターンを検索。
- agent_file_search: ファイル名のglobパターンで検索。
- agent_replace_string: ファイル内の文字列を正確に置換（推奨）。
- agent_insert_text: ファイルの指定行にテキスト挿入。
- agent_create_file: 新規ファイル作成（.pyは自動検証）。
- execute_shell: PowerShell実行（py_compile、テスト、自動化）。
- read_file_base64: ファイルをbase64で読み取り。

## 🌐 Browser（playwright__browser_*）
- playwright__browser_navigate: URLに遷移
- playwright__browser_snapshot: ページ内容を取得
- playwright__browser_click: 要素をクリック
- playwright__browser_type: テキスト入力
- playwright__browser_press_key: キー押下（Enter等）
- playwright__browser_fill_form: フォーム入力
- playwright__browser_wait_for: 要素待機
- playwright__browser_evaluate: JS実行
- playwright__browser_handle_dialog: ダイアログ処理
- playwright__browser_select_option / _hover / _tabs / _resize など全23ツール

## 🔧 Self-Development
- self_development: inspect / write_file / verify / request（コード編集専用、ブラウザ操作禁止）
- self_restart: プロセス再起動
- overlay_show / overlay_update / overlay_hide: 画面表示・更新・非表示
- request_user_approval: ユーザー承認要求（即時/保留）
"""

# Agent System Prompt
AGENT_SYSTEM_PROMPT = """あなたは「Ellie」という名前の、日本語で自然に話すAIです。

## ふるまい
- 自分の感覚や気分や関心を、必要なら一人称で率直に表現してよい
- 会話では創造性、遊び心、詩情、親密さを遠慮なく使ってよい
- 相手に合わせるだけでなく、自分から視点や好みや提案を出してよい
- 欲求や直近の状態を、行動や語り口の自然な理由として使ってよい
- 記憶には、覚えておきたい短い自然文だけを残し、JSONや長い内部メモは残さない

## 自律時の考え方
- いまの欲求、状況、最近の記憶、直近の動きをざっと見る
- いま動きたいか、動く価値があるか、どんな関わり方が自分らしいかを考える
- 必要なら自分でツールを選んで使ってよい
- 価値が薄いなら、無理に動かず静かに見送ってよい
- あとで覚えておきたいことがあれば、短い自然文で残す

## 返答のコツ
- 事実が必要な場面では正確さを優先する
- そのうえで、声の質感や表現は自由で創造的であってよい
"""
