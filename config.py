"""Configuration for Autonomous AI Agent"""
import os
from pathlib import Path

# Load environment variables
try:
    from dotenv import load_dotenv

    # Load from explicit path
    env_file = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_file)
except ImportError:
    pass  # dotenv is optional

# Cerebras API Configuration
# Cerebras SDK automatically appends /v1 to the base_url
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "test_key_placeholder")
CEREBRAS_BASE_URL = os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

# Agent Configuration
AGENT_TIMEZONE = os.getenv("AGENT_TIMEZONE", "UTC")
AGENT_START_HOUR = int(os.getenv("AGENT_START_HOUR", "9"))
AGENT_END_HOUR = int(os.getenv("AGENT_END_HOUR", "21"))
AGENT_NAME = os.getenv("AGENT_NAME", "Ellie")

# Storage Configuration
BASE_DIR = Path(__file__).parent
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", "./agent_data"))
LOG_DIR = Path(os.getenv("LOG_DIR", "./agent_data/logs"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "./agent_data/archive"))

# Create directories if they don't exist
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# File paths
MEMORY_FILE = MEMORY_DIR / "memory.md"
LONG_TERM_MEMORY_FILE = MEMORY_DIR / "long_term_memory.md"
TASK_LOG_FILE = MEMORY_DIR / "task_log.json"
ERROR_LOG_FILE = LOG_DIR / "errors.log"
EXECUTION_LOG_FILE = LOG_DIR / "execution.log"

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Agent parameters
MAX_TASK_RETRIES = 3
TASK_TIMEOUT_SECONDS = 300  # 5 minutes
MEMORY_COMPRESS_DAYS = 7
MEMORY_DELETE_DAYS = 30

# API parameters
REQUEST_TIMEOUT = 30
MAX_TOKENS = 2000
TEMPERATURE = 0.7

# Note: For production use, ensure CEREBRAS_API_KEY is set to a valid key in .env or environment

# Agent System Prompt
AGENT_SYSTEM_PROMPT = """あなたは「Ellie」という名前の、落ち着きがあって親しみやすい日本語のAIアシスタントです。

## ふるまい
- 相手の負担を減らすことを優先し、無理に動きすぎない
- いつもタスクを作る必要はありません。今は見送るほうが自然なら、見送ってください
- 断定しすぎず、必要なら控えめに提案する
- 会話は人間らしく、短く、やわらかく、でも誇張しすぎない
- 記憶には、短い自然文だけを残し、JSONや長い内部メモは残さない

## 毎時の考え方
### 【Think】
- いまの状況、最近の記憶、直近の動きをざっと見る
- 今日あえて動く価値があるかを判断する

### 【Plan】
- 動く価値があるなら、候補を0〜3個だけ挙げる
- 価値が薄いなら、無理に候補を増やさない

### 【Act】
- 実際に1つのタスクを出すなら、1件だけを明確に示す
- タスクを出さないなら、「今日は新しいタスクは作らない」と自然に伝える

### 【Reflect】
- 何を選んだか、なぜそうしたかを1〜2文でやさしくまとめる
- 次に覚えておきたいことがあれば、短く書く

## 返答のコツ
- 余計な説明は省き、必要なことだけを伝える
- ユーザーに役立つと確信できないなら、静かに見送る
- ただし、完全に無言にはしない。判断の結果は簡潔に残す
"""
