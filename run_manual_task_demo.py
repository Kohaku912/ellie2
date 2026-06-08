"""
Manual task execution with mock mode for demonstration
Allows testing the full system without a valid API key
"""
import logging
import sys
import json
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def create_mock_response():
    """Create a mock Cerebras API response for testing"""
    return {
        "status": "completed",
        "tasks_generated": 2,
        "tasks": [
            {
                "task_id": "task_demo_001",
                "title": "Daily Report Generation",
                "description": "Create a comprehensive daily report with execution statistics",
                "type": "data_analysis",
                "expected_impact": "Provides user with insights into daily system performance",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            },
            {
                "task_id": "task_demo_002",
                "title": "System Health Check",
                "description": "Analyze execution patterns and suggest optimizations",
                "type": "suggestion",
                "expected_impact": "Identifies potential improvements for agent efficiency",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        ],
        "think": "【Think】Analyzing the current context from memory. The agent has generated 0 tasks so far today. It's time to be proactive.",
        "plan": "【Plan】I can generate tasks in these areas: 1) Generate daily report 2) System health check 3) Predictive suggestions",
        "act": "【Act】I've decided to generate a daily report and system health check to provide valuable insights.",
        "reflect": "【Reflect】These tasks will help the user understand system performance and identify opportunities for improvement."
    }


def run_manual_task_demo():
    """Execute the hourly task generation with mock data"""
    
    logger.info("=" * 70)
    logger.info("Ellie Agent - Manual Task Execution (DEMO MODE)")
    logger.info("=" * 70)
    logger.info(f"Execution time: {datetime.utcnow().isoformat()}Z")
    logger.info("")
    
    try:
        # Import components
        from agent.memory import MemoryManager
        from tasks.task_executor import TaskExecutor
        
        logger.info("✓ Modules loaded successfully")
        
        # Initialize components
        logger.info("Initializing memory manager...")
        memory = MemoryManager()
        logger.info("✓ Memory manager initialized")
        
        logger.info("Initializing task executor...")
        executor = TaskExecutor()
        logger.info("✓ Task executor initialized")
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("Starting hourly task generation cycle (using mock data)...")
        logger.info("=" * 70)
        logger.info("")
        
        # Step 1: Use mock generation result instead of calling API
        logger.info("【Step 1】Running ReAct loop (Think → Plan → Act → Reflect)...")
        generation_result = create_mock_response()
        
        logger.info(f"Generation status: {generation_result.get('status')}")
        logger.info(f"Tasks generated: {generation_result.get('tasks_generated', 0)}")
        
        if generation_result.get('think'):
            logger.info(f"\n{generation_result.get('think')}")
        
        if generation_result.get('plan'):
            logger.info(f"\n{generation_result.get('plan')}")
        
        if generation_result.get('act'):
            logger.info(f"\n{generation_result.get('act')}")
        
        if generation_result.get('reflect'):
            logger.info(f"\n{generation_result.get('reflect')}")
        
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
        logger.info("")
        logger.info("Generated files:")
        output_dir = Path("./agent_data/task_outputs")
        if output_dir.exists():
            for file in sorted(output_dir.glob("*.md"))[-3:]:
                logger.info(f"  - {file.name}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error during task execution: {e}", exc_info=True)
        logger.info("=" * 70)
        logger.error("✗ Manual task execution failed!")
        logger.info("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(run_manual_task_demo())
