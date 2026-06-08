"""
Manual execution script for Ellie Agent
Allows running the hourly AI task generation immediately for testing
"""
import logging
import sys
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def run_manual_task():
    """Execute the hourly task generation immediately"""
    
    logger.info("=" * 70)
    logger.info("Ellie Agent - Manual Task Execution")
    logger.info("=" * 70)
    logger.info(f"Execution time: {datetime.utcnow().isoformat()}Z")
    logger.info("")
    
    try:
        # Import after logging is configured
        from agent.memory import MemoryManager
        from agent.cerebras_agent import ReActAgent
        from tasks.task_executor import TaskExecutor
        
        logger.info("✓ Modules loaded successfully")
        
        # Initialize components
        logger.info("Initializing memory manager...")
        memory = MemoryManager()
        logger.info("✓ Memory manager initialized")
        
        logger.info("Initializing Cerebras agent...")
        agent = ReActAgent(memory)
        logger.info("✓ Cerebras agent initialized")
        
        logger.info("Initializing task executor...")
        executor = TaskExecutor()
        logger.info("✓ Task executor initialized")
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("Starting hourly task generation cycle...")
        logger.info("=" * 70)
        logger.info("")
        
        # Step 1: Generate tasks using ReAct reasoning
        logger.info("【Step 1】Running ReAct loop (Think → Plan → Act → Reflect)...")
        generation_result = agent.run_hourly_task_generation()
        
        logger.info(f"Generation status: {generation_result.get('status')}")
        logger.info(f"Tasks generated: {generation_result.get('tasks_generated', 0)}")
        
        if generation_result.get('think'):
            logger.info(f"\n【Think section】:\n{generation_result.get('think')[:300]}...")
        
        if generation_result.get('plan'):
            logger.info(f"\n【Plan section】:\n{generation_result.get('plan')[:300]}...")
        
        if generation_result.get('act'):
            logger.info(f"\n【Act section】:\n{generation_result.get('act')[:300]}...")
        
        if generation_result.get('reflect'):
            logger.info(f"\n【Reflect section】:\n{generation_result.get('reflect')[:300]}...")
        
        # Step 2: Execute generated tasks
        logger.info("")
        logger.info("【Step 2】Executing generated tasks...")
        tasks = generation_result.get("tasks", [])
        execution_results = []
        
        if not tasks:
            logger.info("No tasks were generated.")
        else:
            for i, task in enumerate(tasks, 1):
                logger.info(f"\nTask {i}/{len(tasks)}: {task.get('title', 'Unknown')}")
                logger.info(f"  Type: {task.get('type', 'unknown')}")
                logger.info(f"  Description: {task.get('description', 'No description')[:100]}")
                
                # Execute task
                memory_stats = memory.get_daily_stats()
                history = memory.get_execution_history()
                result = executor.execute(task, memory_stats, history)
                execution_results.append(result)
                
                logger.info(f"  Result: {result.get('status')}")
                if result.get('output_file'):
                    logger.info(f"  Output file: {result.get('output_file')}")
                if result.get('error'):
                    logger.error(f"  Error: {result.get('error')}")
        
        # Step 3: Display results
        logger.info("")
        logger.info("=" * 70)
        logger.info("Execution Summary")
        logger.info("=" * 70)
        
        # Memory stats
        stats = memory.get_daily_stats()
        logger.info(f"\nDaily Statistics:")
        logger.info(f"  Tasks generated today: {stats.get('tasks_generated', 0)}")
        logger.info(f"  Tasks executed today: {stats.get('tasks_executed', 0)}")
        logger.info(f"  Tasks completed today: {stats.get('tasks_completed', 0)}")
        logger.info(f"  Tasks failed today: {stats.get('tasks_failed', 0)}")
        logger.info(f"  Total execution time: {stats.get('total_execution_time_ms', 0)}ms")
        logger.info(f"  Total API calls: {stats.get('total_api_calls', 0)}")
        
        # Task results
        if execution_results:
            logger.info(f"\nTask Execution Results:")
            for i, result in enumerate(execution_results, 1):
                logger.info(f"  Task {i}: {result.get('status')}")
                if result.get('duration_ms'):
                    logger.info(f"    Duration: {result.get('duration_ms')}ms")
        
        # Insights
        insights = memory.get_insights()
        if insights:
            logger.info(f"\nRecent Insights:")
            for insight in insights[-3:]:
                logger.info(f"  - {insight.get('content', 'No content')[:100]}")
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("✓ Manual task execution completed successfully!")
        logger.info("=" * 70)
        
        return 0
        
    except Exception as e:
        logger.error(f"Error during task execution: {e}", exc_info=True)
        logger.info("=" * 70)
        logger.error("✗ Manual task execution failed!")
        logger.info("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(run_manual_task())
