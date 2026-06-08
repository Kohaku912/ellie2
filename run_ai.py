"""
Direct AI invocation with a user-provided instruction.

Usage:
  .\\venv\\Scripts\\python run_ai.py --instruction "要件を書いて..."
  .\\venv\\Scripts\\python run_ai.py --file instruction.txt
  Get-Content instruction.txt | .\\venv\\Scripts\\python run_ai.py --stdin
"""
import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call Ellie with a custom instruction.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instruction", help="Inline instruction text to send to Ellie.")
    group.add_argument("--file", help="Path to a text file containing the instruction.")
    group.add_argument("--stdin", action="store_true", help="Read the instruction from stdin.")
    return parser


def load_instruction(args: argparse.Namespace) -> str:
    if args.instruction:
        return args.instruction.strip()

    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()

    return sys.stdin.read().strip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = build_parser()
    args = parser.parse_args()
    instruction_text = load_instruction(args)

    if not instruction_text:
        logger.error("Instruction text is empty.")
        return 1

    from agent.memory import MemoryManager
    from agent.cerebras_agent import ReActAgent

    memory = MemoryManager()
    agent = ReActAgent(memory)
    result = agent.run_with_instruction(instruction_text)

    print(result.get("answer") or result.get("reflect") or result.get("act") or result.get("status"))
    if result.get("tasks"):
        print("\nTasks:")
        for task in result["tasks"]:
            print(f"- {task.get('title', 'Unknown')} ({task.get('type', 'unknown')})")

    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
