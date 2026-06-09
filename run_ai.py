"""
Direct AI invocation with a user-provided instruction.

Usage:
  .\\.venv\\Scripts\\python run_ai.py --instruction "俳句を作って"
  .\\.venv\\Scripts\\python run_ai.py --file instruction.txt
  Get-Content instruction.txt | .\\.venv\\Scripts\\python run_ai.py --stdin
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from agent.instruction_runner import InstructionRunner, format_result_for_cli
from agent.logging_utils import configure_utf8_stdio

configure_utf8_stdio()
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
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
    configure_utf8_stdio()

    args = build_parser().parse_args()
    instruction_text = load_instruction(args)

    if not instruction_text:
        logger.error("Instruction text is empty.")
        return 1

    result = InstructionRunner().chat(instruction_text)
    print(format_result_for_cli(result))
    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
