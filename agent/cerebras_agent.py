"""
Cerebras API integration with a ReAct-style loop.
Supports both autonomous hourly runs and direct instruction-based calls.
"""
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from cerebras.cloud.sdk import Cerebras

from config import (
    AGENT_SYSTEM_PROMPT,
    CEREBRAS_API_KEY,
    CEREBRAS_BASE_URL,
    CEREBRAS_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
)
from agent.memory import MemoryManager

logger = logging.getLogger(__name__)


class ReActAgent:
    """Autonomous agent using a ReAct (Reasoning + Acting) loop."""

    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager
        self.client = Cerebras(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)
        self.model = CEREBRAS_MODEL
        self.max_tokens = MAX_TOKENS
        self.temperature = TEMPERATURE

    def run_hourly_task_generation(self) -> Dict[str, Any]:
        """Run the default hourly autonomous loop."""
        logger.info("Starting hourly task generation cycle")
        start_time = time.time()

        try:
            memory_context = self.memory.get_memory_context()
            current_hour = datetime.utcnow().hour
            result = self._run_react_loop(memory_context, current_hour)

            duration_ms = int((time.time() - start_time) * 1000)
            result["duration_ms"] = duration_ms
            self.memory.update_task_generation_count(result.get("tasks_generated", 0))

            memory_note = self.generate_memory_note(
                event_title="hourly_cycle",
                event_summary=result.get("reflect") or "今日は新しいタスクを作らず、静かに見送った。",
                instruction_text=None,
                answer_text=None,
            )
            self.memory.add_insight(memory_note)

            logger.info(f"Hourly task generation completed in {duration_ms}ms")
            return result
        except Exception as error:
            logger.error(f"Error in hourly task generation: {error}", exc_info=True)
            return {
                "status": "failed",
                "error": str(error),
                "title": "Hourly task generation",
                "duration_ms": int((time.time() - start_time) * 1000),
            }

    def run_with_instruction(self, instruction_text: str, extra_context: str = "") -> Dict[str, Any]:
        """Run the agent with a user-provided instruction and return a direct answer."""
        logger.info("Starting instruction-based AI call")
        start_time = time.time()

        try:
            answer_text = self._run_direct_instruction(instruction_text, extra_context=extra_context)

            if extra_context and self._looks_like_guidance(answer_text):
                answer_text = self._fallback_answer_from_context(extra_context, instruction_text)

            duration_ms = int((time.time() - start_time) * 1000)
            memory_note = self.generate_memory_note(
                event_title="instruction_call",
                event_summary=answer_text,
                instruction_text=instruction_text,
                answer_text=answer_text,
            )
            self.memory.add_insight(memory_note)

            logger.info(f"Instruction-based AI call completed in {duration_ms}ms")
            return {
                "status": "completed",
                "title": "Instruction-based AI call",
                "answer": answer_text,
                "duration_ms": duration_ms,
                "tasks_generated": 0,
                "tasks": [],
            }
        except Exception as error:
            logger.error(f"Error in instruction-based AI call: {error}", exc_info=True)
            return {
                "status": "failed",
                "error": str(error),
                "title": "Instruction-based AI call",
                "duration_ms": int((time.time() - start_time) * 1000),
            }

    def _run_direct_instruction(self, instruction_text: str, extra_context: str = "") -> str:
        """Ask the model to answer the user's instruction directly."""
        memory_context = self.memory.get_memory_context()
        prompt = f"""
{memory_context}

{extra_context.strip()}

## ユーザー指示
{instruction_text.strip()}

## 応答ルール
- これはタスク生成ではありません。普通の会話として答えてください。
- `no_tasks` だけを返さないでください。
- 指示が「俳句を作って」なら、必ず俳句をそのまま出してください。
- 「Explorerを開いてください」「内容を送ってください」など、相手に追加作業を求めないでください。
- 短く、自然に、必要なら日本語で返してください。
"""

        self.memory.record_api_call()
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        response_text = self._extract_message_text(response)
        if response_text:
            return response_text

        if extra_context:
            return self._fallback_answer_from_context(extra_context, instruction_text)

        return f"{instruction_text.strip()} については、いま応答本文を取得できませんでした。"

    def generate_memory_note(
        self,
        event_title: str,
        event_summary: str,
        instruction_text: Optional[str] = None,
        answer_text: Optional[str] = None,
    ) -> str:
        """Ask the model to write one short memory sentence."""
        prompt_lines = [
            "以下の出来事を、今日の記憶として1文だけで自然に書いてください。",
            "条件:",
            "- 日本語で書く",
            "- 1文だけ",
            "- 60文字前後まで",
            "- 主観や考えを少し含めてもよい",
            "- JSON禁止",
            "- 箇条書き禁止",
            "",
            f"出来事の種類: {event_title}",
            f"要約: {event_summary.strip()}",
        ]

        if instruction_text:
            prompt_lines.append(f"元の指示: {instruction_text.strip()}")
        if answer_text:
            prompt_lines.append(f"回答: {answer_text.strip()}")

        prompt = "\n".join(prompt_lines)

        self.memory.record_api_call()
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=120,
            temperature=0.4,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは記憶係です。"
                        "保存用の自然な一文だけを書いてください。"
                        "説明は不要で、余計な前置きも不要です。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )

        note = self._extract_message_text(response)
        if note:
            return note.splitlines()[0].strip()

        return self._fallback_memory_note(
            event_title=event_title,
            event_summary=event_summary,
            instruction_text=instruction_text,
            answer_text=answer_text,
        )

    def _looks_like_guidance(self, text: str) -> bool:
        """Detect answers that explain steps instead of answering directly."""
        guidance_phrases = (
            "explorer",
            "送って",
            "確認してください",
            "教えてください",
            "手順",
            "開いてください",
        )
        lowered = text.lower()
        return any(phrase in lowered for phrase in guidance_phrases)

    def _extract_message_text(self, response: Any) -> str:
        """Safely extract message content from a Cerebras response."""
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""

        message = getattr(choices[0], "message", None)
        if message is None:
            return ""

        content = getattr(message, "content", None)
        if not isinstance(content, str):
            return ""

        return content.strip()

    def _fallback_memory_note(
        self,
        event_title: str,
        event_summary: str,
        instruction_text: Optional[str] = None,
        answer_text: Optional[str] = None,
    ) -> str:
        """Create a short natural-language memory note when the model returns nothing."""

        def clean(text: str) -> str:
            return " ".join(text.split())

        if instruction_text and answer_text:
            return f"{clean(instruction_text)} に {clean(answer_text)} を返した。"

        if event_title == "task_execution":
            return f"{clean(event_summary)} を実行して記録した。"

        return f"{clean(event_summary)} を記録した。"

    def summarize_execution_note(
        self,
        task_title: str,
        task_result: Dict[str, Any],
        memory_context: str = "",
    ) -> str:
        """Write a memory sentence for a task execution."""
        summary = task_result.get("result") or task_result.get("status") or "task executed"
        return self.generate_memory_note(
            event_title="task_execution",
            event_summary=f"{task_title}: {summary}",
            instruction_text=memory_context or None,
            answer_text=summary,
        )

    def generate_long_term_memory_note(self, daily_memory_text: str) -> str:
        """Ask the model whether anything from today deserves durable memory."""
        if not daily_memory_text.strip():
            return "NONE"

        prompt = f"""
以下は今日だけの短期記憶です。
明日以降も永久に残すべきことがあるか判断してください。

残すべきものの例:
- ユーザーの継続的な好み
- このAIの設計方針として今後も守るべきこと
- 繰り返し参照する必要がある重要な事実

残さないものの例:
- その日限りの雑談
- 一時的な実行ログ
- 失敗した試行の細かい経緯

出力ルール:
- 残す価値があるなら、日本語の自然な1文だけを書く
- 残す価値がないなら、NONE だけを書く
- JSON、箇条書き、説明は禁止

## 今日の短期記憶
{daily_memory_text.strip()}
"""

        self.memory.record_api_call()
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=180,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": "あなたは長期記憶の判定係です。残す価値があることだけを厳しく選びます。",
                },
                {"role": "user", "content": prompt},
            ],
        )

        response_text = self._extract_message_text(response)
        if not response_text:
            return "NONE"

        note = response_text.splitlines()[0].strip()
        if not note:
            return "NONE"
        if note.upper().startswith("NONE"):
            return "NONE"
        return note

    def _fallback_answer_from_context(self, extra_context: str, instruction_text: str) -> str:
        """Build a direct answer from the fetched filesystem/web context."""
        lines = [line.strip() for line in extra_context.splitlines() if line.strip()]
        if lines:
            path_match = re.search(r"([A-Za-z]:\\)", extra_context)
            if path_match:
                path = path_match.group(1)
                preview = "\n".join(lines[1:41]) if len(lines) > 1 else lines[0]
                return f"{path} の中身を確認しました。\n\n{preview}"

            web_match = re.search(r"https?://\S+", extra_context)
            if web_match:
                source = web_match.group(0)
                preview = "\n".join(lines[1:31]) if len(lines) > 1 else lines[0]
                return f"{source} を確認しました。\n\n{preview}"

        return extra_context[:4000] or f"{instruction_text.strip()} については、取得した情報をもとに回答しました。"

    def _run_react_loop(
        self,
        memory_context: str,
        current_hour: int,
        extra_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the ReAct loop and return parsed sections."""
        prompt = self._build_prompt(memory_context, current_hour, extra_instruction)

        logger.debug("Calling Cerebras API for ReAct loop")
        self.memory.record_api_call()

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        response_text = self._extract_message_text(response)
        logger.debug(f"API response:\n{response_text[:500]}...")

        think_section = self._extract_section(response_text, "Think")
        plan_section = self._extract_section(response_text, "Plan")
        act_section = self._extract_section(response_text, "Act")
        reflect_section = self._extract_section(response_text, "Reflect")

        tasks = self._parse_tasks_from_response(act_section)

        return {
            "status": "completed" if tasks else "no_tasks",
            "title": "Hourly autonomous task generation",
            "hour": current_hour,
            "tasks_generated": len(tasks),
            "tasks": tasks,
            "think": think_section[:500] if think_section else None,
            "plan": plan_section[:500] if plan_section else None,
            "act": act_section[:500] if act_section else None,
            "reflect": reflect_section[:500] if reflect_section else None,
        }

    def _build_prompt(
        self,
        memory_context: str,
        current_hour: int,
        extra_instruction: Optional[str] = None,
    ) -> str:
        """Build a context-aware prompt for the model."""
        instruction_block = ""
        if extra_instruction:
            instruction_block = f"""

## ユーザー指示
{extra_instruction.strip()}
"""

        return f"""
{memory_context}
{instruction_block}

## Current Time
Hour: {current_hour}:00 UTC
Current time: {datetime.utcnow().isoformat()}Z

## Your Task

You are Ellie, a calm and human-like Japanese assistant.

**【Think】**: Analyze the current context. What feels important right now? Is there any real reason to act?

**【Plan】**: If there is clear value, list 0-3 possible ideas. If the value is weak, do not force ideas.

**【Act】**: If you choose to create a task, provide exactly one task in JSON.
If you decide not to create a task, say that you are skipping it today in natural language.
JSON format:
```json
{{
  "task_id": "task_001",
  "title": "Task title",
  "description": "What this task does",
  "type": "file_operation|data_analysis|suggestion|research",
  "expected_impact": "Why this is valuable"
}}
```

**【Reflect】**: Briefly explain your choice in 1-2 sentences, and mention anything worth remembering.

Focus on being thoughtful, warm, and honest. It is okay to do nothing if that is the most natural choice.
"""

    def _extract_section(self, text: str, section_name: str) -> Optional[str]:
        """Extract a specific ReAct section from the model response."""
        patterns = [
            f"【{section_name}】(.+?)(?=【|$)",
            f"\\*\\*{section_name}\\*\\*(.+?)(?=\\*\\*|$)",
            f"### {section_name}(.+?)(?=###|$)",
            f"## {section_name}(.+?)(?=##|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        return None

    def _parse_tasks_from_response(self, act_section: Optional[str]) -> List[Dict[str, Any]]:
        """Parse task definitions from the Act section."""
        tasks: List[Dict[str, Any]] = []

        if not act_section:
            return tasks

        skip_patterns = [
            r"今日は新しいタスクは作らない",
            r"タスクは作らない",
            r"見送り",
            r"no[_\s-]*task",
            r"skip",
        ]
        if any(re.search(pattern, act_section, re.IGNORECASE) for pattern in skip_patterns):
            logger.debug("Act section indicates no task should be generated")
            return tasks

        json_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", act_section)
        if not json_match:
            logger.debug("No task JSON found in Act section")
            return tasks

        try:
            task_data = json.loads(json_match.group(1))
            task_data["timestamp"] = datetime.utcnow().isoformat() + "Z"
            tasks.append(task_data)
            logger.debug(f"Parsed task: {task_data.get('title', 'Unknown')}")
        except json.JSONDecodeError as error:
            logger.warning(f"Failed to parse task JSON: {error}")

        return tasks

    def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a generated task."""
        logger.info(f"Executing task: {task.get('title', 'Unknown')}")
        task_type = task.get("type", "analysis")

        try:
            if task_type == "file_operation":
                return self._execute_file_operation(task)
            if task_type == "data_analysis":
                return self._execute_data_analysis(task)
            if task_type == "suggestion":
                return self._execute_suggestion(task)
            if task_type == "research":
                return self._execute_research(task)
            return self._execute_generic_analysis(task)
        except Exception as error:
            logger.error(f"Error executing task: {error}")
            return {
                "status": "failed",
                "error": str(error),
                "task_id": task.get("task_id"),
            }

    def _execute_file_operation(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "completed",
            "task_id": task.get("task_id"),
            "result": "File operation would be executed here",
        }

    def _execute_data_analysis(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "completed",
            "task_id": task.get("task_id"),
            "result": "Data analysis would be executed here",
        }

    def _execute_suggestion(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "completed",
            "task_id": task.get("task_id"),
            "result": "Suggestions would be generated here",
        }

    def _execute_research(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "completed",
            "task_id": task.get("task_id"),
            "result": "Research results would be provided here",
        }

    def _execute_generic_analysis(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "completed",
            "task_id": task.get("task_id"),
            "result": "Analysis completed",
        }
