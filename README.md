# Ellie - Autonomous AI Agent with Cerebras API

## Overview

Ellie is a self-aware autonomous AI agent that runs 24/7 as a daemon. Every hour (9 AM - 9 PM UTC), it:
1. **Thinks** - Analyzes the current context using the ReAct reasoning pattern
2. **Plans** - Proposes task ideas only when they feel genuinely useful
3. **Acts** - Executes one selected task, or intentionally does nothing
4. **Reflects** - Records short insights and learning to external memory

The agent has **external memory** stored as a small natural-language note file that:
- Persists throughout the day with brief updates
- Automatically resets at midnight UTC
- Archives previous days' notes
- Stores only minimal summaries and recent observations

## Features

✨ **Autonomous Decision Making**: Uses ReAct (Reasoning + Acting) pattern for transparent thinking before action

🧠 **External Memory**: Daily memory system with hourly updates and automatic nightly reset

⏰ **Scheduled Execution**: Hourly task generation and execution (9 AM - 9 PM UTC, configurable)

🛠️ **Task Execution**: 
- File operations (create, read, analyze files)
- Data analysis and reporting
- Suggestion generation
- Research and information gathering

🧩 **Dynamic Tool Retrieval**: Retrieves only relevant tool schemas for an event before calling the AI

📊 **Daily Analytics**: Automatic generation of daily reports with execution statistics

🔧 **Easy Configuration**: `.env` file for API keys and settings

## Project Structure

```
ellie2/
├── agent/                    # Core reasoning engine
│   ├── cerebras_agent.py    # ReAct agent with Cerebras API
│   ├── dynamic_tool_rag.py   # Event-driven dynamic Tool RAG layer
│   ├── memory.py            # Multi-layer memory management
│   └── __init__.py
├── scheduler/               # Execution scheduling
│   ├── scheduler.py         # APScheduler configuration
│   └── __init__.py
├── tasks/                   # Task execution
│   ├── task_executor.py     # Execute agent-decided tasks
│   ├── tools.py             # Available tools
│   └── __init__.py
├── agent_data/              # Persistent storage
│   ├── memory.json          # Today's memory (updated hourly)
│   ├── task_log.json        # Task execution log
│   ├── archive/             # Previous days' memories
│   ├── logs/                # Execution and error logs
│   └── task_outputs/        # Generated files
├── main.py                  # Entry point (daemon)
├── config.py                # Configuration management
├── requirements.txt         # Python dependencies
├── .env                     # API keys and settings
├── .env.template            # Configuration template
└── test_setup.py            # Setup verification tests
```

## Getting Started

### 1. Prerequisites

- Python 3.9+
- Cerebras API key (from https://www.cerebras.ai)

### 2. Installation

```bash
# Clone or navigate to project
cd c:\Users\kohak\programs\ellie2

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy template and edit
copy .env.template .env

# Edit .env with your settings:
# - Set CEREBRAS_API_KEY to your actual API key
# - Adjust AGENT_START_HOUR and AGENT_END_HOUR if needed
# - Customize AGENT_NAME if desired
```

### 4. Verify Setup

```bash
# Run verification tests
.\venv\Scripts\python test_setup.py

# Expected output: ✓ All tests passed! System is ready to run.
```

### 5. Run the Agent

```bash
# Start as foreground daemon
.\venv\Scripts\python main.py

# Or run in background (use Ctrl+C to stop)
```

### 6. Call Ellie with an instruction

```powershell
# Inline instruction
.\venv\Scripts\python run_ai.py --instruction "このフォルダの構成を簡単に要約して"

# Read from file
.\venv\Scripts\python run_ai.py --file instruction.txt

# Read from stdin
Get-Content instruction.txt | .\venv\Scripts\python run_ai.py --stdin
```

## Event-Driven Dynamic Tool RAG

`agent/dynamic_tool_rag.py` implements the backend control layer for event-driven tool calling.

- `ToolDefinition`: JSON Schema based tool metadata passed to the LLM only after retrieval
- `InMemoryToolVectorStore`: mock vector database using lightweight token cosine similarity
- `retrieve_relevant_tools(query, top_n)`: searches the tool store for the current event context
- `call_ai_with_dynamic_tools(event_context)`: retrieves tools, calls Chat Completion, parses tool calls, and returns handler results
- `ToolCallHandler`: skeleton dispatcher for actions such as screenshot capture and app launch

Example:

```python
from agent.dynamic_tool_rag import call_ai_with_dynamic_tools, retrieve_relevant_tools

tools = retrieve_relevant_tools("ユーザーがスマホの画面をオンにした", top_n=3)
print([tool.name for tool in tools])

response = call_ai_with_dynamic_tools("ユーザーがスマホの画面をオンにした")
print(response.to_dict())
```

## Memory Structure

The agent keeps a short natural-language memory file (`agent_data/memory.md`) with:
- one-line summary of the day
- a few recent notes

It also keeps durable notes in `agent_data/long_term_memory.md`. At the daily reset, Ellie asks the AI to judge whether anything from the day is worth keeping permanently. Only that selected sentence is copied into long-term memory; ordinary daily notes are archived and later cleaned up.

Example:

```md
# Ellie の今日の記憶
日付: 2026-06-08
ひとこと: 今日は新しいタスクを作らず、静かに見送った。

## 今日のメモ
- ユーザーは毎回タスクを出さなくてよい。
- 記憶は短い自然文だけにする。
```

### Memory Lifecycle

1. **00:00 UTC**: AI judges whether anything should become long-term memory, then previous day's memory is archived
2. **Hourly (9-21 UTC)**: Memory updated with short natural-language notes
3. **30+ days**: Old archives automatically cleaned up

## Configuration Options

Edit `.env` to customize:

```env
# API Configuration
CEREBRAS_API_KEY=your_key_here           # Your Cerebras API key
CEREBRAS_BASE_URL=https://api.cerebras.ai/v1
CEREBRAS_MODEL=claude-3-5-sonnet

# Agent Behavior
AGENT_NAME=Ellie                         # Agent's name
AGENT_TIMEZONE=UTC                       # Timezone for scheduling
AGENT_START_HOUR=9                       # Start hour (UTC)
AGENT_END_HOUR=21                        # End hour (UTC)

# Storage
MEMORY_DIR=./agent_data
LOG_DIR=./agent_data/logs
ARCHIVE_DIR=./agent_data/archive

# Logging
LOG_LEVEL=INFO                           # DEBUG, INFO, WARNING, ERROR
```

## Usage Examples

### Running the daemon

```bash
# Foreground mode (shows all logs)
.\venv\Scripts\python main.py

# Background mode (using Windows Task Scheduler - see below)
```

### Monitoring execution

```bash
# Watch logs in real-time
Get-Content -Path agent_data/logs/execution.log -Wait

# Check memory state
Get-Content agent_data/memory.md
```

### Accessing task outputs

```bash
# Find generated files
dir agent_data/task_outputs/

# View daily report
Get-Content agent_data/task_outputs/daily_report_YYYYMMDD.md
```

## Setting Up Persistent Execution

### Option 1: Windows Task Scheduler

```powershell
# Create a task to run at startup
$action = New-ScheduledTaskAction `
  -Execute "C:\Users\kohak\programs\ellie2\venv\Scripts\python.exe" `
  -Argument "main.py" `
  -WorkingDirectory "C:\Users\kohak\programs\ellie2"

$trigger = New-ScheduledTaskTrigger -AtStartup

Register-ScheduledTask `
  -TaskName "Ellie AI Agent" `
  -Action $action `
  -Trigger $trigger `
  -RunLevel Highest `
  -User "System"
```

### Option 2: Background Job

```powershell
# In a PowerShell terminal, create a background job
Start-Job -FilePath C:\Users\kohak\programs\ellie2\start_agent.ps1
```

Create `start_agent.ps1`:
```powershell
Set-Location C:\Users\kohak\programs\ellie2
.\venv\Scripts\python main.py
```

## API Integration

### Cerebras API Structure

The agent uses Cerebras API (Claude-compatible endpoint) for reasoning:

```python
# In config.py
CEREBRAS_API_KEY = "your_api_key"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_MODEL = "claude-3-5-sonnet"
```

### System Prompt

The agent receives a system prompt that:
1. Establishes its identity as "Ellie"
2. Defines the ReAct reasoning process
3. Outlines available tools
4. Sets behavioral guidelines

See `config.py` for the full system prompt.

## Troubleshooting

### Issue: "CEREBRAS_API_KEY not set"

**Solution**: Make sure `.env` file is in the project root and contains your API key:
```bash
cat .env | findstr CEREBRAS_API_KEY
```

### Issue: "No module named 'anthropic'"

**Solution**: Ensure virtual environment is activated:
```bash
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Issue: Tasks not executing

**Solution**: Check logs:
```bash
Get-Content agent_data/logs/execution.log -Tail 20
Get-Content agent_data/logs/errors.log -Tail 20
```

### Issue: Memory not persisting between runs

**Solution**: Verify `agent_data/` directory exists and memory file is being created:
```bash
dir agent_data/
Get-Content agent_data/memory.md
```

## Performance Metrics

Typical hourly execution:
- **API call time**: 2-5 seconds
- **Task execution time**: 1-3 seconds  
- **Memory footprint**: ~50-100 MB
- **Total hourly time**: ~5-10 seconds

The agent runs only 12 hours daily (9-21 UTC), making it lightweight and efficient.

## Architecture Highlights

### ReAct Pattern

```
【Think】         → Analyze context and past actions
   ↓
【Plan】         → Generate 1-3 task options
   ↓
【Act】          → Execute selected task (file I/O, analysis, etc.)
   ↓
【Reflect】      → Record results and insights to memory
```

### Memory Layers

1. **Immediate**: In-process (current execution context)
2. **Session**: Today's JSON file (persisted hourly)
3. **Archive**: Previous days' snapshots (cleanup after 30 days)
4. **Summary**: Compressed old memories (for context)

### Task Types

- **file_operation**: Create, read, modify files
- **data_analysis**: Analyze execution data, generate reports
- **suggestion**: Generate improvement suggestions
- **research**: Information gathering and research
- **generic**: Analysis and thinking tasks

## Future Enhancements

- [ ] Web search integration
- [ ] Database support
- [ ] Multi-user support
- [ ] Dashboard/Web UI
- [ ] Slack integration
- [ ] Advanced long-term learning (beyond 30 days)
- [ ] Parallel task execution
- [ ] Custom tool registration API

## Support

For issues or questions:
1. Check logs in `agent_data/logs/`
2. Review memory file in `agent_data/memory.md`
3. Run `test_setup.py` to verify installation
4. Check Cerebras API status and usage

## License

This project is for personal use. Ensure you comply with Cerebras API terms of service.

## Notes

- Agent runs in UTC timezone (configurable)
- All timestamps are in ISO 8601 format with Z suffix
- Memory is reset daily at 00:00 UTC (configurable)
- Agent respects working hours (9-21 UTC default)
- Task execution is sequential (one per hour)
- API calls are logged for cost tracking
