"""
Test script for verifying Ellie agent setup and functionality
"""
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_imports():
    """Test that all imports work correctly"""
    logger.info("Testing imports...")
    try:
        import config
        logger.info("✓ config module imported")
        
        from agent.memory import MemoryManager
        logger.info("✓ MemoryManager imported")
        
        from agent.cerebras_agent import ReActAgent
        logger.info("✓ ReActAgent imported")
        
        from tasks.task_executor import TaskExecutor
        logger.info("✓ TaskExecutor imported")
        
        from scheduler.scheduler import AutonomousAgentScheduler
        logger.info("✓ AutonomousAgentScheduler imported")
        
        return True
    except ImportError as e:
        logger.error(f"✗ Import failed: {e}")
        return False


def test_config():
    """Test configuration loading"""
    logger.info("\nTesting configuration...")
    try:
        import config
        
        logger.info(f"  Agent name: {config.AGENT_NAME}")
        logger.info(f"  Working hours: {config.AGENT_START_HOUR}:00 - {config.AGENT_END_HOUR}:00 UTC")
        logger.info(f"  Memory directory: {config.MEMORY_DIR}")
        logger.info(f"  API key configured: {bool(config.CEREBRAS_API_KEY)}")
        
        # Check directories exist
        if config.MEMORY_DIR.exists():
            logger.info(f"  ✓ Memory directory exists: {config.MEMORY_DIR}")
        else:
            logger.warning(f"  ⚠ Memory directory doesn't exist: {config.MEMORY_DIR}")
        
        return True
    except Exception as e:
        logger.error(f"✗ Config test failed: {e}")
        return False


def test_memory_system():
    """Test memory system initialization"""
    logger.info("\nTesting memory system...")
    try:
        from agent.memory import MemoryManager
        
        memory = MemoryManager()
        logger.info("  ✓ MemoryManager initialized")
        
        # Check memory file structure
        stats = memory.get_daily_stats()
        logger.info(f"  ✓ Daily stats accessible: {len(stats)} keys")
        
        # Test adding insight
        memory.add_insight("Test insight for verification")
        insights = memory.get_insights()
        logger.info(f"  ✓ Insights recorded: {len(insights)} insights")
        
        # Test save
        memory.save_memory()
        logger.info("  ✓ Memory saved to disk")
        
        # Check memory file exists
        if memory.memory_file.exists():
            size = memory.memory_file.stat().st_size
            logger.info(f"  ✓ Memory file created: {size} bytes")
        
        return True
    except Exception as e:
        logger.error(f"✗ Memory system test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_directory_structure():
    """Test that all required directories exist"""
    logger.info("\nTesting directory structure...")
    
    required_dirs = [
        "agent",
        "scheduler",
        "tasks",
        "agent_data",
        "agent_data/archive"
    ]
    
    all_exist = True
    for dir_name in required_dirs:
        dir_path = Path(dir_name)
        if dir_path.exists():
            logger.info(f"  ✓ {dir_name}/")
        else:
            logger.warning(f"  ✗ {dir_name}/ missing")
            all_exist = False
    
    return all_exist


def main():
    """Run all tests"""
    logger.info("=" * 60)
    logger.info("Ellie Agent - Setup Verification Tests")
    logger.info("=" * 60)
    
    results = {
        "Directory structure": test_directory_structure(),
        "Configuration": test_config(),
        "Imports": test_imports(),
        "Memory system": test_memory_system(),
    }
    
    logger.info("\n" + "=" * 60)
    logger.info("Test Results Summary")
    logger.info("=" * 60)
    
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    
    logger.info("=" * 60)
    if all_passed:
        logger.info("✓ All tests passed! System is ready to run.")
        return 0
    else:
        logger.error("✗ Some tests failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
