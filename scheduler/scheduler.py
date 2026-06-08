"""
Scheduler for Autonomous AI Agent
Manages hourly task execution and daily memory reset
"""
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import AGENT_START_HOUR, AGENT_END_HOUR, EXECUTION_LOG_FILE
from agent.memory import MemoryManager
from agent.cerebras_agent import ReActAgent
from tasks.task_executor import TaskExecutor

logger = logging.getLogger(__name__)


class AutonomousAgentScheduler:
    """
    Manages scheduled execution of autonomous agent tasks
    - Hourly task generation and execution (within working hours)
    - Daily memory reset at midnight UTC
    """
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.memory = MemoryManager()
        self.agent = ReActAgent(self.memory)
        self.executor = TaskExecutor()
        
        logger.info("Scheduler initialized")
    
    def start(self):
        """Initialize and start all scheduled jobs"""
        self._setup_jobs()
        self.scheduler.start()
        logger.info("Scheduler started")
        
        # Log startup
        self._log_startup()
    
    def stop(self):
        """Stop scheduler gracefully"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped gracefully")
    
    def _setup_jobs(self):
        """Configure all scheduled jobs"""
        
        # Hourly autonomous task loop (within working hours)
        # Format: "hour" accepts range like "9-21" for 9 AM to 9 PM
        hour_range = f"{AGENT_START_HOUR}-{AGENT_END_HOUR}"
        
        self.scheduler.add_job(
            self.autonomous_task_loop,
            CronTrigger(hour=hour_range, minute=0),
            id="hourly_autonomous_tasks",
            name="Hourly autonomous task generation and execution",
            max_instances=1  # Prevent concurrent execution
        )
        logger.info(f"Scheduled hourly task generation: {hour_range}:00")
        
        # Daily memory reset at midnight UTC
        self.scheduler.add_job(
            self.daily_memory_reset,
            CronTrigger(hour=0, minute=0),
            id="daily_memory_reset",
            name="Daily memory reset and archival",
            max_instances=1
        )
        logger.info("Scheduled daily memory reset at 00:00 UTC")
        
        # Optional: Daily summary generation at end of working day
        end_hour = AGENT_END_HOUR
        self.scheduler.add_job(
            self.generate_daily_summary,
            CronTrigger(hour=end_hour, minute=59),
            id="daily_summary",
            name="Daily summary generation",
            max_instances=1
        )
        logger.info(f"Scheduled daily summary at {end_hour}:59 UTC")
    
    def autonomous_task_loop(self):
        """
        Main hourly loop:
        1. Generate tasks using ReAct agent
        2. Execute tasks
        3. Log results to memory
        """
        logger.info("=" * 60)
        logger.info("Starting hourly autonomous task execution")
        logger.info(f"Time: {datetime.utcnow().isoformat()}Z")
        
        try:
            # Step 1: Generate tasks using ReAct reasoning
            generation_result = self.agent.run_hourly_task_generation()
            
            logger.info(f"Task generation result: {generation_result.get('status')}")
            logger.info(f"Tasks generated: {generation_result.get('tasks_generated', 0)}")
            
            # Step 2: Execute generated tasks
            tasks = generation_result.get("tasks", [])
            execution_results = []
            
            for task in tasks:
                logger.info(f"Executing task: {task.get('title', 'Unknown')}")
                
                # Execute task with current memory stats
                memory_stats = self.memory.get_daily_stats()
                history = self.memory.get_execution_history()
                
                result = self.executor.execute(task, memory_stats, history)
                execution_results.append(result)
                memory_note = self.agent.summarize_execution_note(
                    task_title=task.get("title", "Unknown"),
                    task_result=result,
                    memory_context=f"task_type={task.get('type', 'analysis')}",
                )
                self.memory.log_task_execution({
                    "task_id": task.get("task_id"),
                    "title": task.get("title", "Unknown"),
                    "type": task.get("type", "analysis"),
                    "status": result.get("status", "unknown"),
                    "duration_ms": result.get("duration_ms", 0),
                }, memory_note=memory_note)
                
                logger.info(f"Task result: {result.get('status')}")
            
            # Step 3: Log execution results
            self._log_execution(generation_result, execution_results)
            
            logger.info("Hourly task execution completed successfully")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Error in hourly task loop: {e}", exc_info=True)
            logger.info("=" * 60)
    
    def daily_memory_reset(self):
        """
        Daily reset routine at midnight:
        1. Archive current day's memory
        2. Create fresh memory for new day
        3. Clean up old archives
        """
        logger.info("=" * 60)
        logger.info("Starting daily memory reset")
        logger.info(f"Time: {datetime.utcnow().isoformat()}Z")
        
        try:
            # Get today's stats before reset
            today_stats = self.memory.get_daily_stats()
            today_date = datetime.utcnow().strftime("%Y-%m-%d")
            daily_memory_text = self.memory.session.get("memory_text", "")
            try:
                long_term_note = self.agent.generate_long_term_memory_note(daily_memory_text)
            except Exception as error:
                logger.warning(f"Long-term memory decision failed: {error}", exc_info=True)
                long_term_note = "NONE"
            
            logger.info(f"Today's statistics: {today_stats}")
            logger.info(f"Long-term memory decision: {long_term_note}")
            
            # Reset memory (archives old memory automatically)
            success = self.memory.reset_daily_memory(long_term_note=long_term_note)
            
            if success:
                logger.info(f"Memory reset successful for date: {today_date}")
            else:
                logger.error("Memory reset failed")
            
            # Reinitialize agent with fresh memory
            self.agent = ReActAgent(self.memory)
            logger.info("Agent reinitialized with fresh memory")
            
            logger.info("Daily memory reset completed")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Error during daily memory reset: {e}", exc_info=True)
            logger.info("=" * 60)
    
    def generate_daily_summary(self):
        """Generate daily summary at end of working day"""
        logger.info("=" * 60)
        logger.info("Generating daily summary")
        
        try:
            # Get all today's data
            stats = self.memory.get_daily_stats()
            history = self.memory.get_execution_history()
            insights = self.memory.get_insights()
            
            # Create summary
            summary = f"""# Daily Summary - {datetime.utcnow().strftime('%Y-%m-%d')}

## Statistics
- Tasks Generated: {stats.get('tasks_generated', 0)}
- Tasks Executed: {stats.get('tasks_executed', 0)}
- Tasks Completed: {stats.get('tasks_completed', 0)}
- Success Rate: {(stats.get('tasks_completed', 0) / max(stats.get('tasks_executed', 1), 1) * 100):.1f}%
- Total Time: {stats.get('total_execution_time_ms', 0)}ms
- API Calls: {stats.get('total_api_calls', 0)}

## Key Insights
"""
            for insight in insights[-5:]:
                summary += f"- {insight.get('content', 'No content')}\n"
            
            # Save summary
            from tasks.tools import FileOperationTool
            from config import MEMORY_DIR
            from pathlib import Path
            
            summary_file = f"summary_{datetime.utcnow().strftime('%Y%m%d')}.md"
            output_dir = Path(MEMORY_DIR) / "task_outputs"
            result = FileOperationTool.create_file(summary_file, summary, str(output_dir))
            
            if result.get("success"):
                logger.info(f"Daily summary saved to {result.get('filepath')}")
            else:
                logger.warning("Failed to save daily summary")
            
            logger.info("Daily summary generation completed")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Error generating daily summary: {e}", exc_info=True)
            logger.info("=" * 60)
    
    def _log_execution(self, generation_result, execution_results):
        """Log execution details to file"""
        try:
            with open(EXECUTION_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.utcnow().isoformat()}Z\n")
                f.write(f"Generation Status: {generation_result.get('status')}\n")
                f.write(f"Tasks Generated: {generation_result.get('tasks_generated', 0)}\n")
                f.write(f"Execution Results:\n")
                
                for result in execution_results:
                    f.write(f"  - Task {result.get('task_id')}: {result.get('status')}\n")
                    if result.get('error'):
                        f.write(f"    Error: {result.get('error')}\n")
        except Exception as e:
            logger.error(f"Failed to log execution: {e}")
    
    def _log_startup(self):
        """Log scheduler startup"""
        try:
            with open(EXECUTION_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"SCHEDULER STARTED: {datetime.utcnow().isoformat()}Z\n")
                f.write(f"Working hours: {AGENT_START_HOUR}:00 - {AGENT_END_HOUR}:00 UTC\n")
                f.write(f"Memory state: {self.memory.get_memory_stats()}\n")
        except Exception as e:
            logger.error(f"Failed to log startup: {e}")
