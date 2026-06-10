"""
Scheduler for Autonomous AI Agent.
Runs periodic autonomous cycles and daily memory maintenance in Japan time.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    class CronTrigger:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class BackgroundScheduler:  # type: ignore[no-redef]
        def __init__(self, timezone=None):
            self.timezone = timezone
            self.running = False
            self._jobs = []
            self._thread: threading.Thread | None = None
            self._stop_event = threading.Event()

        def add_job(self, func, trigger, id=None, name=None, max_instances=1):
            self._jobs.append(
                {
                    "func": func,
                    "trigger": trigger,
                    "id": id,
                    "name": name,
                    "max_instances": max_instances,
                    "last_run_key": "",
                }
            )

        def start(self):
            self.running = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ellie-fallback-scheduler")
            self._thread.start()

        def shutdown(self, wait: bool = True):
            self.running = False
            self._stop_event.set()
            if wait and self._thread and self._thread.is_alive():
                self._thread.join(timeout=5)

        def _run_loop(self):
            while not self._stop_event.is_set():
                now = time.localtime()
                for job in self._jobs:
                    if _should_run_job(job["trigger"], now, job["last_run_key"]):
                        job["last_run_key"] = f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d} {now.tm_hour:02d}:{now.tm_min:02d}"
                        try:
                            job["func"]()
                        except Exception:
                            logger.exception("Fallback scheduler job failed: %s", job.get("name") or job.get("id"))
                self._stop_event.wait(15)

from ellie.logging.audit_log import get_audit_logger
from ellie.autonomy.ai_activity import get_ai_activity_tracker
from ellie.autonomy.runtime import AutonomyRuntime
from ellie.core.agent import ReActAgent
from ellie.memory.memory import MemoryManager
from ellie.mcp.pc_bridge.tools import start_pc_tool_bridge_server, stop_pc_tool_bridge_server
from ellie.memory.self_model import SelfModelManager
from ellie.memory.social_needs import SocialNeedsManager
from ellie.time_utils import agent_tz, date_str_local, isoformat_local
from ellie.config import EXECUTION_LOG_FILE

logger = logging.getLogger(__name__)
AI_ACTIVITY_TRACKER = get_ai_activity_tracker()


def _should_run_job(trigger: CronTrigger, now: time.struct_time, last_run_key: str) -> bool:
    kwargs = getattr(trigger, "kwargs", {})
    minute_spec = kwargs.get("minute")
    hour_spec = kwargs.get("hour")
    if minute_spec == "*/5":
        if now.tm_min % 5 != 0:
            return False
    elif minute_spec is not None and int(minute_spec) != now.tm_min:
        return False
    if hour_spec is not None and int(hour_spec) != now.tm_hour:
        return False
    current_key = f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d} {now.tm_hour:02d}:{now.tm_min:02d}"
    return current_key != last_run_key


class AutonomousAgentScheduler:
    """Manage scheduled autonomous runs and daily maintenance."""

    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=agent_tz())
        self.memory = MemoryManager()
        self.self_model = SelfModelManager(self.memory)
        self.social_needs = SocialNeedsManager()
        self.agent = ReActAgent(self.memory, self.self_model, self.social_needs)
        self.autonomy_runtime = AutonomyRuntime(lambda: self.agent)
        logger.info("Scheduler initialized")

    def start(self):
        try:
            start_pc_tool_bridge_server()
        except Exception as error:
            logger.warning("PC tool bridge could not be started by scheduler: %s", error)
        self._setup_jobs()
        self.scheduler.start()
        self.autonomy_runtime.start()
        logger.info("Scheduler started")
        self._log_startup()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped gracefully")
        self.autonomy_runtime.stop()
        try:
            stop_pc_tool_bridge_server()
        except Exception as error:
            logger.debug("PC tool bridge stop skipped: %s", error)

    def _setup_jobs(self):
        self.scheduler.add_job(
            self.autonomous_task_loop,
            CronTrigger(minute="*/15", timezone=agent_tz()),
            id="fifteen_minute_autonomous_tasks",
            name="Fifteen-minute autonomous cycle",
            max_instances=1,
        )
        logger.info("Scheduled autonomous cycle: every 15 minutes (Asia/Tokyo)")

        self.scheduler.add_job(
            self.daily_memory_reset,
            CronTrigger(hour=0, minute=0, timezone=agent_tz()),
            id="daily_memory_reset",
            name="Daily memory reset and archival",
            max_instances=1,
        )
        logger.info("Scheduled daily memory reset at 00:00 Asia/Tokyo")

        self.scheduler.add_job(
            self.generate_daily_summary,
            CronTrigger(hour=23, minute=59, timezone=agent_tz()),
            id="daily_summary",
            name="Daily summary generation",
            max_instances=1,
        )
        logger.info("Scheduled daily summary at 23:59 Asia/Tokyo")

    def autonomous_task_loop(self):
        logger.info("=" * 60)
        if AI_ACTIVITY_TRACKER.is_active():
            logger.info("Skipping fifteen-minute autonomous cycle because AI is already running")
            logger.info("=" * 60)
            return

        logger.info("Starting fifteen-minute autonomous cycle")
        logger.info("Time: %s", isoformat_local())
        audit_logger = get_audit_logger()
        trace_id = audit_logger.new_id("fifteen-minute-loop")

        try:
            result = self.agent.run_autonomous_cycle(audit_trace_id=trace_id)
            logger.info("Autonomous cycle status: %s", result.get("status"))
            logger.info("Tool calls executed: %s", result.get("tool_calls_executed", 0))
            if result.get("answer"):
                logger.info("Cycle answer: %s", str(result.get("answer"))[:300])
            self._log_execution(result)
            logger.info("Fifteen-minute autonomous cycle completed")
            logger.info("=" * 60)
        except Exception as error:
            logger.error("Error in fifteen-minute autonomous cycle: %s", error, exc_info=True)
            logger.info("=" * 60)

    def daily_memory_reset(self):
        logger.info("=" * 60)
        logger.info("Starting daily memory reset")
        logger.info("Time: %s", isoformat_local())
        audit_logger = get_audit_logger()
        trace_id = audit_logger.new_id("daily-reset")

        try:
            daily_memory_text = self.memory.session.get("memory_text", "")
            try:
                long_term_note = self.agent.generate_long_term_memory_note(
                    daily_memory_text,
                    audit_trace_id=trace_id,
                    audit_parent_id=trace_id,
                )
            except Exception as error:
                logger.warning("Long-term memory decision failed: %s", error, exc_info=True)
                long_term_note = "NONE"

            success = self.memory.reset_daily_memory(long_term_note=long_term_note)
            if success:
                logger.info("Memory reset successful for date: %s", date_str_local())
            else:
                logger.error("Memory reset failed")

            self.self_model.reset_short_term_state()
            logger.info("Short-term self-state reset successfully")

            self.agent = ReActAgent(self.memory, self.self_model, self.social_needs)
            logger.info("Agent reinitialized with fresh memory")
            logger.info("Daily memory reset completed")
            logger.info("=" * 60)
        except Exception as error:
            logger.error("Error during daily memory reset: %s", error, exc_info=True)
            logger.info("=" * 60)

    def generate_daily_summary(self):
        logger.info("=" * 60)
        logger.info("Generating daily summary")

        try:
            stats = self.memory.get_daily_stats()
            insights = self.memory.get_insights()
            executed = stats.get("tasks_executed", 0)
            completed = stats.get("tasks_completed", 0)
            success_rate = (completed / max(executed, 1)) * 100
            summary = f"""# Daily Summary - {date_str_local()}

## Statistics
- Autonomous Runs: {stats.get('tasks_generated', 0)}
- Tool-backed Actions: {executed}
- Completed Actions: {completed}
- Success Rate: {success_rate:.1f}%
- Total Time: {stats.get('total_execution_time_ms', 0)}ms
- API Calls: {stats.get('total_api_calls', 0)}

## Key Insights
"""
            for insight in insights[-5:]:
                summary += f"- {insight.get('content', 'No content')}\n"

            output_dir = Path(self.memory.memory_dir) / "task_outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            summary_file = output_dir / f"summary_{date_str_local().replace('-', '')}.md"
            summary_file.write_text(summary, encoding="utf-8")
            logger.info("Daily summary saved to %s", summary_file)
            logger.info("Daily summary generation completed")
            logger.info("=" * 60)
        except Exception as error:
            logger.error("Error generating daily summary: %s", error, exc_info=True)
            logger.info("=" * 60)

    def _log_execution(self, result):
        try:
            with open(EXECUTION_LOG_FILE, "a", encoding="utf-8") as file_handle:
                file_handle.write(f"\n{'=' * 60}\n")
                file_handle.write(f"Timestamp: {isoformat_local()}\n")
                file_handle.write(f"Status: {result.get('status')}\n")
                file_handle.write(f"Tool Calls Executed: {result.get('tool_calls_executed', 0)}\n")
                file_handle.write(f"Answer: {result.get('answer', '')}\n")
                file_handle.write("Tool Results:\n")
                for tool_result in result.get("tool_results", []):
                    file_handle.write(
                        f"  - {tool_result.get('tool', 'unknown')}: {'ok' if tool_result.get('ok') else 'failed'}\n"
                    )
                    if tool_result.get("error"):
                        file_handle.write(f"    Error: {tool_result.get('error')}\n")
        except Exception as error:
            logger.error("Failed to log execution: %s", error)

    def _log_startup(self):
        try:
            with open(EXECUTION_LOG_FILE, "a", encoding="utf-8") as file_handle:
                file_handle.write(f"\n{'=' * 60}\n")
                file_handle.write(f"SCHEDULER STARTED: {isoformat_local()}\n")
                file_handle.write(f"Memory state: {self.memory.get_memory_stats()}\n")
        except Exception as error:
            logger.error("Failed to log startup: %s", error)

