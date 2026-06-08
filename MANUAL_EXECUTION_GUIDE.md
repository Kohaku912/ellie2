# Manual Execution Guide - Ellie AI Agent

## Overview
This guide describes how to manually execute the hourly AI task generation at any time, useful for testing and verification.

## Quick Start

### Demo Mode (Recommended for Initial Testing)
Runs the full system with mock data, no API key required:

```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python run_manual_task_demo.py
```

**What happens:**
- ✓ Generates 2 demo tasks using mock data
- ✓ Executes both tasks successfully
- ✓ Creates output files in `agent_data/task_outputs/`
- ✓ Updates memory system with execution data
- ✓ Shows complete ReAct cycle (Think → Plan → Act → Reflect)
- ✓ No API calls needed
- ⏱ Execution time: ~1 second

**Expected Output:**
```
2026-06-08 10:44:24,189 - INFO - Ellie Agent - Manual Task Execution (DEMO MODE)
2026-06-08 10:44:24,216 - INFO - Generation status: completed
2026-06-08 10:44:24,216 - INFO - Tasks generated: 2
2026-06-08 10:44:24,220 - INFO - ✓ Manual task execution completed successfully!
```

### Production Mode (Requires Valid API Key)
Runs the full system with real Cerebras API calls:

```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python run_manual_task.py
```

**Prerequisites:**
- Valid Cerebras API key in `.env` file: `CEREBRAS_API_KEY=csk-...`
- API endpoint configured: `CEREBRAS_BASE_URL=https://api.cerebras.ai`

**Expected Output:**
- Generates real tasks based on agent reasoning
- Executes tasks using live API
- Updates memory with actual execution data

## Testing API Connection

Verify your API configuration:

```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python test_api_connection.py
```

**Output:**
- ✓ Success: "API connection test PASSED"
- ✗ Failed: "API connection test FAILED" with troubleshooting tips

## File Generation

Both demo and production modes create output files in `agent_data/task_outputs/`:

**Demo mode output:**
```
suggestions_20260608_014424.md
```

**Production mode output:**
```
daily_report_20260608_014424.md
health_check_20260608_014424.md
suggestions_20260608_014424.md
```

## Background Daemon Mode

To run the agent continuously in the background (hourly execution):

```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python main.py
```

**Features:**
- Runs scheduled tasks hourly (9 AM - 9 PM UTC)
- Daily memory reset at midnight UTC
- Graceful shutdown on Ctrl+C
- Logs to `agent_data/logs/execution.log`

**Monitor execution:**
```powershell
Get-Content agent_data\logs\execution.log -Tail 20 -Wait
```

## Memory System

Generated output files and execution data are stored in:

```
agent_data/
├── memory.json                 # Current day's memory (hourly updates)
├── archive/                    # Previous days' snapshots
│   └── memory_2026-06-07.json
├── logs/
│   └── execution.log          # Execution logs
└── task_outputs/
    ├── daily_report_*.md
    ├── health_check_*.md
    └── suggestions_*.md
```

**Memory lifecycle:**
- **Hourly**: Tasks executed, memory updated with results
- **Nightly (00:00 UTC)**: Previous day archived, fresh memory created
- **Auto-cleanup**: Archives older than 30 days deleted automatically

## Troubleshooting

### Issue: "API connection test FAILED: Error code: 404"

**Cause:** Cerebras API endpoint not responding

**Solutions:**
1. Verify API key: Check `.env` has valid `CEREBRAS_API_KEY`
2. Check API status: Visit https://www.cerebras.ai/status
3. Use demo mode: Test with `run_manual_task_demo.py` instead
4. Verify internet: Ensure connection to api.cerebras.ai

### Issue: "ModuleNotFoundError"

**Cause:** Dependencies not installed

**Solution:**
```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\pip install -r requirements.txt
```

### Issue: System hangs when starting daemon

**Cause:** Scheduler waiting for tasks

**Solution:**
- Press Ctrl+C to gracefully shutdown
- Check logs: `Get-Content agent_data\logs\execution.log`
- Verify time zone settings in `.env` (AGENT_START_HOUR, AGENT_END_HOUR)

## Development Commands

```powershell
# Run full test suite
.\venv\Scripts\python test_setup.py

# Test individual components
.\venv\Scripts\python test_api_connection.py
.\venv\Scripts\python run_manual_task_demo.py
.\venv\Scripts\python run_manual_task.py

# Start background daemon (Ctrl+C to stop)
.\venv\Scripts\python main.py

# Check memory system
.\venv\Scripts\python -c "from agent.memory import MemoryManager; m = MemoryManager(); print(m.get_daily_stats())"
```

## Configuration

Edit `.env` to customize behavior:

```
# API Configuration
CEREBRAS_API_KEY=csk-...                           # Your API key
CEREBRAS_BASE_URL=https://api.cerebras.ai         # API endpoint
CEREBRAS_MODEL=claude-3-5-sonnet                   # Model name

# Agent Behavior
AGENT_TIMEZONE=UTC                                 # Timezone
AGENT_START_HOUR=9                                 # Start hour (UTC)
AGENT_END_HOUR=21                                  # End hour (UTC)
AGENT_NAME=Ellie                                   # Agent name

# Storage
MEMORY_DIR=./agent_data                            # Memory directory
LOG_DIR=./agent_data/logs                          # Log directory
ARCHIVE_DIR=./agent_data/archive                   # Archive directory

# Logging
LOG_LEVEL=INFO                                     # Log level
```

## Summary

| Mode | Command | API Key | Time | Use Case |
|------|---------|---------|------|----------|
| Demo | `run_manual_task_demo.py` | ❌ No | ~1s | Testing system without API |
| Manual | `run_manual_task.py` | ✅ Yes | ~5s | One-time task generation |
| Daemon | `main.py` | ✅ Yes | Ongoing | Continuous hourly execution |
| Test | `test_api_connection.py` | ✅ Yes | ~2s | Verify API connectivity |

## Next Steps

1. **Test Demo Mode**: `python run_manual_task_demo.py` ✓ (confirms system works)
2. **Verify API**: `python test_api_connection.py` (requires valid API key)
3. **Run Manual**: `python run_manual_task.py` (if API key works)
4. **Start Daemon**: `python main.py` (continuous background operation)

---

**Note:** Demo mode fully validates the system architecture without requiring a valid API key. Use this to verify everything works before configuring production API access.
