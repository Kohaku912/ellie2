"""
Test daemon startup and scheduler initialization
"""
import logging
import sys
import time
from threading import Thread

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def test_daemon_startup():
    """Test daemon startup with scheduler initialization"""
    
    logger.info("=" * 70)
    logger.info("Testing Daemon Startup and Scheduler Initialization")
    logger.info("=" * 70)
    
    try:
        from scheduler.scheduler import AutonomousAgentScheduler
        
        logger.info("\nInitializing scheduler...")
        scheduler = AutonomousAgentScheduler()
        
        logger.info("✓ Scheduler initialized")
        
        logger.info(f"\nScheduled Jobs:")
        for job in scheduler.scheduler.get_jobs():
            logger.info(f"  - {job.name}")
            logger.info(f"    Trigger: {job.trigger}")
            logger.info(f"    Next run: {job.next_run_time}")
        
        logger.info("\nStarting scheduler...")
        scheduler.start()
        
        logger.info("✓ Scheduler started")
        logger.info("\nRunning for 5 seconds to verify background operation...")
        
        time.sleep(5)
        
        logger.info("\nScheduler is running. Active jobs:")
        for job in scheduler.scheduler.get_jobs():
            logger.info(f"  - {job.name} (Next: {job.next_run_time})")
        
        logger.info("\nShutting down scheduler...")
        scheduler.scheduler.shutdown()
        
        logger.info("✓ Scheduler shut down gracefully")
        logger.info("\n" + "=" * 70)
        logger.info("✓ Daemon startup test PASSED")
        logger.info("=" * 70)
        
        return 0
        
    except Exception as e:
        logger.error(f"✗ Daemon startup test FAILED: {e}", exc_info=True)
        logger.info("\n" + "=" * 70)
        logger.error("Troubleshooting tips:")
        logger.error("1. Check that APScheduler is installed")
        logger.error("2. Verify scheduler/scheduler.py has no syntax errors")
        logger.error("3. Check that MemoryManager and ReActAgent are available")
        logger.info("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(test_daemon_startup())
