"""
Main entry point for Autonomous AI Agent
Runs as a daemon that executes autonomous tasks hourly
"""
import signal
import sys
import time
import logging
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ellie.logging.logging_utils import configure_utf8_stdio, utf8_file_handler
from ellie.config import LOG_LEVEL, LOG_DIR, AGENT_NAME
from ellie.autonomy.scheduler import AutonomousAgentScheduler

# Configure logging
LOG_DIR.mkdir(parents=True, exist_ok=True)
configure_utf8_stdio()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        utf8_file_handler(LOG_DIR / "agent.log"),
        logging.StreamHandler(sys.stdout)
    ],
    force=True,
)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global scheduler
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    if scheduler:
        scheduler.stop()
    sys.exit(0)


def main():
    """Main entry point"""
    global scheduler
    
    logger.info(f"Starting {AGENT_NAME} Autonomous AI Agent")
    logger.info("Using hybrid LLM routing: Cerebras for light tasks, DeepSeek for heavy tasks")
    
    # Initialize scheduler
    scheduler = AutonomousAgentScheduler()
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Start the scheduler
        scheduler.start()
        logger.info("Scheduler started successfully")
        
        # Keep the daemon running (cross-platform)
        while True:
            time.sleep(1)  # Sleep briefly to avoid busy-waiting
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        scheduler.stop()
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        if scheduler:
            scheduler.stop()
        raise


if __name__ == "__main__":
    main()

