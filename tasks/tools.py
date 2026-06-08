"""
Available tools for autonomous agent task execution
"""
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class FileOperationTool:
    """Tool for file creation and manipulation"""
    
    @staticmethod
    def create_file(filename: str, content: str, directory: str = "./output") -> Dict[str, Any]:
        """Create a new file with content"""
        try:
            output_dir = Path(directory)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            filepath = output_dir / filename
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"Created file: {filepath}")
            return {
                "success": True,
                "filepath": str(filepath),
                "size_bytes": len(content)
            }
        except Exception as e:
            logger.error(f"Failed to create file: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def append_to_file(filename: str, content: str, directory: str = "./output") -> Dict[str, Any]:
        """Append content to existing file"""
        try:
            output_dir = Path(directory)
            filepath = output_dir / filename
            
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write("\n" + content)
            
            logger.info(f"Appended to file: {filepath}")
            return {"success": True, "filepath": str(filepath)}
        except Exception as e:
            logger.error(f"Failed to append to file: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def read_file(filename: str, directory: str = "./output") -> Dict[str, Any]:
        """Read file content"""
        try:
            output_dir = Path(directory)
            filepath = output_dir / filename
            
            if not filepath.exists():
                return {"success": False, "error": f"File not found: {filepath}"}
            
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            logger.info(f"Read file: {filepath}")
            return {
                "success": True,
                "filepath": str(filepath),
                "content": content,
                "size_bytes": len(content)
            }
        except Exception as e:
            logger.error(f"Failed to read file: {e}")
            return {"success": False, "error": str(e)}


class DataAnalysisTool:
    """Tool for data analysis and pattern recognition"""
    
    @staticmethod
    def analyze_execution_history(history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze execution history for patterns"""
        if not history:
            return {"success": False, "error": "No execution history"}
        
        analysis = {
            "total_tasks": len(history),
            "success_rate": 0.0,
            "most_common_hour": None,
            "average_duration_ms": 0,
            "patterns": []
        }
        
        try:
            successful = sum(1 for t in history if t.get("status") == "completed")
            analysis["success_rate"] = (successful / len(history)) * 100 if history else 0
            
            if history:
                total_duration = sum(t.get("duration_ms", 0) for t in history)
                analysis["average_duration_ms"] = total_duration / len(history)
            
            # Find most common hour
            hours = [t.get("hour") for t in history if t.get("hour") is not None]
            if hours:
                analysis["most_common_hour"] = max(set(hours), key=hours.count)
            
            analysis["success"] = True
            return analysis
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def generate_daily_report(stats: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
        """Generate a daily summary report"""
        report = f"""
# Daily Agent Report - {datetime.utcnow().strftime('%Y-%m-%d')}

## Summary Statistics
- Tasks Generated: {stats.get('tasks_generated', 0)}
- Tasks Executed: {stats.get('tasks_executed', 0)}
- Tasks Completed: {stats.get('tasks_completed', 0)}
- Tasks Failed: {stats.get('tasks_failed', 0)}
- Total Execution Time: {stats.get('total_execution_time_ms', 0)}ms
- API Calls: {stats.get('total_api_calls', 0)}

## Execution Timeline
"""
        for task in history[-10:]:  # Last 10 tasks
            report += f"- Hour {task.get('hour', '?'):02d}: {task.get('title', 'Unknown')} ({task.get('status', 'unknown')})\n"
        
        return report


class SuggestionTool:
    """Tool for generating suggestions and recommendations"""
    
    @staticmethod
    def generate_improvement_suggestions(stats: Dict[str, Any]) -> List[str]:
        """Generate suggestions for improvement"""
        suggestions = []
        
        completed = stats.get('tasks_completed', 0)
        total = stats.get('tasks_executed', 0)
        
        if total > 0:
            success_rate = (completed / total) * 100
            if success_rate < 50:
                suggestions.append("⚠️ Task success rate is low. Consider simpler task generation.")
            elif success_rate > 90:
                suggestions.append("✅ High task success rate. Current task complexity is appropriate.")
        
        api_calls = stats.get('total_api_calls', 0)
        if api_calls > 10:
            suggestions.append("💡 Many API calls used today. Tasks are well-planned and executed.")
        
        total_time = stats.get('total_execution_time_ms', 0)
        if total_time > 300000:  # 5 minutes
            suggestions.append("⏱️ Total execution time is high. Consider optimizing task execution.")
        
        if not suggestions:
            suggestions.append("✨ Agent is performing well. Continue current patterns.")
        
        return suggestions
    
    @staticmethod
    def generate_user_suggestions(memory_context: str) -> List[str]:
        """Generate suggestions for the user based on agent observations"""
        suggestions = [
            "Consider scheduling important tasks during agent's active hours (9 AM - 9 PM UTC)",
            "Check daily agent reports in agent_data/logs/ for insights",
            "Review memory archives to understand agent decision patterns",
            "Update .env configuration if you want to adjust agent behavior"
        ]
        return suggestions


class LoggingTool:
    """Tool for structured logging and analytics"""
    
    @staticmethod
    def create_execution_log(task: Dict[str, Any], result: Dict[str, Any]) -> str:
        """Create a log entry for executed task"""
        timestamp = datetime.utcnow().isoformat() + "Z"
        return f"""
[{timestamp}] Task: {task.get('title', 'Unknown')}
ID: {task.get('task_id', 'unknown')}
Type: {task.get('type', 'unknown')}
Status: {result.get('status', 'unknown')}
Duration: {result.get('duration_ms', 0)}ms
Details: {result.get('result', 'No details')}
"""
    
    @staticmethod
    def aggregate_logs(log_dir: Path) -> Dict[str, Any]:
        """Aggregate all logs for analysis"""
        try:
            log_files = list(log_dir.glob("*.log"))
            total_size = sum(f.stat().st_size for f in log_files)
            
            return {
                "log_count": len(log_files),
                "total_size_bytes": total_size,
                "log_files": [str(f.name) for f in log_files]
            }
        except Exception as e:
            logger.error(f"Failed to aggregate logs: {e}")
            return {"success": False, "error": str(e)}
