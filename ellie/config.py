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
HEAVY_TASK_MAX_STEPS = max(20, int(os.getenv("HEAVY_TASK_MAX_STEPS", "20")))

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
TOOL_CAPABILITY_INDEX = """## コア機能索引
- web_search: 外部情報を調べる。
- read_file_base64: ファイル内容を確認する。
- self_development: このプロジェクトの調査・編集・検証・保留依頼を扱う。
- execute_shell: PowerShell を実行して検証や自動化を行う。
- playwright__*: ブラウザを直接操作する。開いて読むだけで終わらず、必要な操作を続ける。
- twitter_followers_check: X/Twitter に開いてログイン確認とフォロワー数の確認を行う。
- overlay_show / overlay_update / overlay_hide: 即時に見せたい短い案内や確認を画面へ出す。
- request_user_approval: すぐ返事が必要なら overlay、急がない依頼は self_development_requests.md に残す。
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
