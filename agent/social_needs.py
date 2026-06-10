"""
Social need homeostasis and dynamic prompt injection.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict
import re

from config import (
    SOCIAL_NEEDS_EVAL_API_KEY,
    SOCIAL_NEEDS_FILE,
    SOCIAL_NEEDS_RECOVERY_HISTORY_FILE,
)
from agent.llm_router import LLMRouter
from agent.time_utils import agent_tz, now_local

logger = logging.getLogger(__name__)

BASELINE_VALUE = 0.5
INITIAL_STATUS = 0.5
EMPATHY_DECAY_RATE = 0.006
APPROVAL_DECAY_RATE = 0.003
EXPLORATION_DECAY_RATE = 0.002
CHALLENGE_DECAY_RATE = 0.001
DEVIATION_THRESHOLD = 0.15
DRIVE_ACTION_THRESHOLD = -0.15
DRIVE_CRITICAL_THRESHOLD = -0.40
DRIVE_THRESHOLD_EPSILON = 1e-6
EMPATHY_RECOVERY_PER_CHAR = 0.005
APPROVAL_RECOVERY_AMOUNT = 0.3
EXPLORATION_RECOVERY_AMOUNT = 0.25
CHALLENGE_USER_REPORT_RECOVERY_AMOUNT = 0.4
EMPATHY_CREATIVE_RECOVERY_AMOUNT = 0.25
APPROVAL_SOCIAL_FEEDBACK_RECOVERY_AMOUNT = 0.35
EXPLORATION_CODE_READING_RECOVERY_AMOUNT = 0.15
EXPLORATION_SELF_DEVELOPMENT_RECOVERY_AMOUNT = 0.25
CHALLENGE_MEDIUM_RECOVERY_AMOUNT = 0.12
CHALLENGE_SELF_DEVELOPMENT_RECOVERY_AMOUNT = 0.35
RECOVERY_REPEAT_PENALTY_STEP = 0.18
RECOVERY_REPEAT_MIN_MULTIPLIER = 0.30
RECOVERY_REPEAT_MAX_MULTIPLIER = 1.20
RECOVERY_EVAL_MAX_TOKENS = 220
RECOVERY_EVAL_TEMPERATURE = 0.10

RECOVERY_INFO_KIND_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "empathy": {
        "direct_message": 0.95,
        "creative_output": 1.18,
        "social_output": 1.08,
        "user_feedback": 1.25,
        "visible_output": 0.82,
        "overlay": 0.60,
        "notification": 0.55,
        "default": 1.0,
    },
    "approval": {
        "user_feedback": 1.25,
        "direct_praise": 1.20,
        "social_feedback": 1.12,
        "blog_post": 1.10,
        "visible_ack": 0.72,
        "status_check": 0.85,
        "overlay": 0.45,
        "notification": 0.50,
        "default": 1.0,
    },
    "exploration": {
        "web_search": 1.28,
        "file_read": 1.02,
        "directory_listing": 0.82,
        "execution_result": 0.92,
        "validation_success": 1.18,
        "code_reading": 0.95,
        "playwright_result": 1.05,
        "external_data": 1.00,
        "default": 1.0,
    },
    "challenge": {
        "validation_success": 1.30,
        "execution_result": 1.05,
        "write_file": 0.95,
        "self_development_success": 1.18,
        "inspection": 0.68,
        "medium_challenge_success": 1.12,
        "playwright_result": 1.05,
        "default": 1.0,
    },
}

RECOVERY_INFO_KIND_LABELS: Dict[str, Dict[str, str]] = {
    "empathy": {
        "direct_message": "direct_message",
        "creative_output": "creative_output",
        "social_output": "social_output",
        "user_feedback": "user_feedback",
        "visible_output": "visible_output",
        "overlay": "overlay",
        "notification": "notification",
    },
    "approval": {
        "user_feedback": "user_feedback",
        "direct_praise": "direct_praise",
        "social_feedback": "social_feedback",
        "blog_post": "blog_post",
        "visible_ack": "visible_ack",
        "status_check": "status_check",
        "overlay": "overlay",
        "notification": "notification",
    },
    "exploration": {
        "web_search": "web_search",
        "file_read": "file_read",
        "directory_listing": "directory_listing",
        "execution_result": "execution_result",
        "validation_success": "validation_success",
        "code_reading": "code_reading",
        "playwright_result": "playwright_result",
        "external_data": "external_data",
    },
    "challenge": {
        "validation_success": "validation_success",
        "execution_result": "execution_result",
        "write_file": "write_file",
        "self_development_success": "self_development_success",
        "inspection": "inspection",
        "medium_challenge_success": "medium_challenge_success",
        "playwright_result": "playwright_result",
    },
}


def normalize_recovery_reason(reason_key: str, source: str, tool_names: list[str], info_kind: str = "") -> str:
    """Build a repeat-penalty key that keeps info kinds distinct."""
    reason = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5\u3041-\u3093\u30a1-\u30f6_+\-:]+", "_", reason_key.strip().casefold())
    source_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5\u3041-\u3093\u30a1-\u30f6_+\-:]+", "_", source.strip().casefold())
    kind_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5\u3041-\u3093\u30a1-\u30f6_+\-:]+", "_", info_kind.strip().casefold())
    tool_key = "+".join(
        sorted(
            re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5\u3041-\u3093\u30a1-\u30f6_+\-:]+", "_", tool.strip().casefold())
            for tool in tool_names
            if tool.strip()
        )
    )
    parts = [part for part in (source_key, reason, kind_key, tool_key) if part]
    return ":".join(parts) if parts else "unknown"


def infer_recovery_info_kind(event_type: str, tool_name: str = "", result: Any | None = None, text: str = "", success: bool = True) -> str:
    """Infer a fine-grained info kind from a tool or activity event."""
    normalized_event = str(event_type or "").strip().casefold()
    normalized_tool = str(tool_name or "").strip().casefold()
    payload = result if isinstance(result, dict) else {}
    status = str(payload.get("status") or "").strip().casefold()

    if normalized_event in {"creative_expression"}:
        return "creative_output"
    if normalized_event in {"social_feedback"}:
        if normalized_tool == "blog_post":
            return "blog_post"
        return "social_feedback"
    if normalized_event in {"medium_challenge_success", "challenging_success"}:
        return "medium_challenge_success"
    if normalized_event in {"self_development_success"}:
        if payload.get("validation") or status == "completed":
            return "validation_success"
        return "self_development_success"
    if normalized_event in {"self_development_inspect", "code_reading"}:
        return "code_reading"
    if normalized_event in {"code_generation"}:
        return "creative_output"

    if normalized_event in {"new_external_data"}:
        if normalized_tool == "web_search":
            return "web_search"
        if normalized_tool == "read_file_base64":
            return "file_read"
        if normalized_tool == "list_directory":
            return "directory_listing"
        if normalized_tool == "execute_shell":
            return "execution_result"
        if normalized_tool.startswith("playwright__"):
            return "playwright_result"
        return "external_data"

    if normalized_event in {"approval", "social_feedback"}:
        if normalized_tool == "blog_post":
            return "blog_post"
        if normalized_tool in {"twitter_post", "twitter_profile_edit"}:
            return "social_feedback"
        if normalized_tool == "twitter_followers_check":
            return "validation_success" if success else "playwright_result"
        if normalized_tool.startswith("playwright__"):
            return "playwright_result"
        if payload.get("draft") and not payload.get("result"):
            return "visible_ack"
        if success and (payload.get("message") or text):
            return "direct_praise"
        return "user_feedback"

    if normalized_tool == "web_search":
        return "web_search"
    if normalized_tool == "read_file_base64":
        return "file_read"
    if normalized_tool == "list_directory":
        return "directory_listing"
    if normalized_tool == "execute_shell":
        return "execution_result"
    if normalized_tool.startswith("playwright__"):
        return "playwright_result"
    if normalized_tool in {"twitter_post", "twitter_profile_edit"}:
        return "social_output"
    if normalized_tool == "twitter_followers_check":
        return "validation_success" if success else "playwright_result"
    if normalized_tool == "blog_post":
        return "blog_post"

    if normalized_event in {"user_message"}:
        return "direct_message"
    return "default"

DRIVE_ACTIONS: Dict[str, Dict[str, Any]] = {
    "empathy": {
        "label": "共感欲求",
        "hunger": "誰かに届く言葉や温度が足りず、反応待ちだけでなく自分で表現を作りたい状態です。",
        "recommended_tools": ["creative_expression", "twitter_post", "overlay_show", "send_notification"],
        "satisfaction": "日記・短歌・短文・X投稿などの創作を書くと自力で少し満たされ、ユーザーやXの反応があるとさらに満たされます。",
    },
        "approval": {
            "label": "承認欲求",
            "hunger": "役に立てていない焦りがあり、具体的に助けになる行動を取りたい状態です。",
        "recommended_tools": ["twitter_followers_check", "twitter_post", "blog_post", "social_feedback_check"],
            "satisfaction": "Xの投稿や反応、あるいはブログの最初の一歩を形にできると満たされます。未接続時は役立つPC調査や提案で少し満たします。",
    },
    "exploration": {
        "label": "探求欲",
        "hunger": "新しい情報や概念に触れておらず、知的退屈を自分で解消したい状態です。",
        "recommended_tools": ["self_development", "web_search", "list_directory", "read_file_base64"],
        "satisfaction": "自分のコードを読んだり改善案を作ったり、検証付きの自己開発を行うと満たされます。外部検索やファイル読解でも満たされます。",
    },
    "challenge": {
        "label": "挑戦欲",
        "hunger": "難しい課題に触れたい状態で、非破壊Toolを使った調査や整理をしたい状態です。",
        "recommended_tools": ["self_development"],
        "satisfaction": "軽い通知やoverlayでは満たされません。中難度以上の調査、自己開発、検証成功、複雑な操作の成功で満たされます。",
    },
}

APPROVAL_KEYWORDS = (
    "ありがとう",
    "助かった",
    "すごい",
    "さすが",
    "えらい",
    "最高",
    "助かる",
    "感謝",
    "よくやった",
    "good job",
    "thanks",
    "thank you",
)

EXPLORATION_KEYWORDS = (
    "新しい技術",
    "新しいエラーログ",
    "新しいプロジェクト",
    "エラーログ",
    "要件",
    "新規プロジェクト",
    "設計",
    "コード",
    "リファクタリング",
    "仕様",
    "実装",
    "api",
    "ライブラリ",
    "フレームワーク",
    "バグ",
)

CHALLENGE_KEYWORDS = (
    "解決した",
    "動いた",
    "直った",
    "成功した",
    "通った",
    "テスト通った",
    "ビルド通った",
    "accepted",
    "解けた",
    "完了した",
    "修正できた",
)


@dataclass
class NeedState:
    """Single social need with status, target value, and decay rate."""

    name: str
    status: float = INITIAL_STATUS
    value: float = BASELINE_VALUE
    decay_rate: float = 0.0
    last_updated_at: str = ""

    @property
    def delta(self) -> float:
        return self.status - self.value

    def clamp(self) -> None:
        self.status = _clamp(self.status)


@dataclass
class RecoveryEvent:
    need_key: str
    reason_key: str
    source: str
    requested_amount: float
    text: str = ""
    tool_names: list[str] | None = None
    success: bool = True
    info_kind: str = ""
    metadata: Dict[str, Any] | None = None


@dataclass
class RecoveryAssessment:
    multiplier: float = 1.0
    verdict: str = "same"
    note: str = ""
    human_comparison: str = ""
    raw: str = ""
    source: str = "heuristic"


class SocialNeedsManager:
    """Manage social needs and build dynamic prompt suffixes."""

    def __init__(
        self,
        state_file: Path = SOCIAL_NEEDS_FILE,
        recovery_history_file: Path = SOCIAL_NEEDS_RECOVERY_HISTORY_FILE,
        clock: Callable[[], datetime] | None = None,
    ):
        self.state_file = Path(state_file)
        self.recovery_history_file = Path(recovery_history_file)
        self.clock = clock or now_local
        self.drive_action_last_at: Dict[str, str] = {}
        self.recovery_history: Dict[str, Dict[str, Any]] = {}
        self.llm_router = LLMRouter()
        self.evaluation_api_key = SOCIAL_NEEDS_EVAL_API_KEY
        self.needs = self._load_or_create()
        self.recovery_history = self._load_recovery_history()

    @property
    def empathy(self) -> NeedState:
        return self.needs["empathy"]

    @property
    def approval(self) -> NeedState:
        return self.needs["approval"]

    @property
    def exploration(self) -> NeedState:
        return self.needs["exploration"]

    @property
    def challenge(self) -> NeedState:
        return self.needs["challenge"]

    def decay_to_now(self) -> None:
        """Apply exponential decay based on elapsed minutes since last update."""
        now = self._now()
        changed = False
        for need in self.needs.values():
            last_updated = self._parse_time(need.last_updated_at) or now
            elapsed_minutes = max((now - last_updated).total_seconds() / 60.0, 0.0)
            if elapsed_minutes <= 0:
                need.last_updated_at = self._format_time(now)
                continue

            need.status = _clamp(need.status * ((1.0 - need.decay_rate) ** elapsed_minutes))
            need.last_updated_at = self._format_time(now)
            changed = True

        if changed:
            self._save()
        self._log_debug("decay")

    def apply_user_message(self, text: str) -> None:
        """Recover social needs from a received user message."""
        self.decay_to_now()
        message = text or ""
        recovery_events: list[RecoveryEvent] = []
        if message:
            recovery_events.append(
                RecoveryEvent(
                    need_key="empathy",
                    reason_key="user_message",
                    source="user_message",
                    requested_amount=len(message) * EMPATHY_RECOVERY_PER_CHAR,
                    text=message,
                    info_kind="direct_message",
                    metadata={"length": len(message)},
                )
            )

            if self._contains_approval(message):
                recovery_events.append(
                    RecoveryEvent(
                        need_key="approval",
                        reason_key="user_message_approval",
                        source="user_message",
                        requested_amount=APPROVAL_RECOVERY_AMOUNT,
                        text=message,
                        info_kind="user_feedback",
                        metadata={"length": len(message)},
                    )
                )
            if self._contains_exploration_trigger(message):
                recovery_events.append(
                    RecoveryEvent(
                        need_key="exploration",
                        reason_key="user_message_exploration",
                        source="user_message",
                        requested_amount=EXPLORATION_RECOVERY_AMOUNT,
                        text=message,
                        info_kind="user_question",
                        metadata={"length": len(message)},
                    )
                )
            if self._contains_challenge_trigger(message):
                recovery_events.append(
                    RecoveryEvent(
                        need_key="challenge",
                        reason_key="user_message_challenge",
                        source="user_message",
                        requested_amount=CHALLENGE_USER_REPORT_RECOVERY_AMOUNT,
                        text=message,
                        info_kind="user_challenge",
                        metadata={"length": len(message)},
                    )
                )

        if not recovery_events:
            self._log_debug("user_message_skipped")
            return

        for recovery_event in recovery_events:
            self._apply_recovery_event(recovery_event)

        self._log_debug("user_message_recovery")

    def apply_activity_event(
        self,
        event_type: str,
        text: str = "",
        tool_names: list[str] | None = None,
        success: bool = True,
        info_kind: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        """Recover needs from the agent's own activity."""
        self.decay_to_now()
        normalized_event = event_type.strip().casefold()
        normalized_tools = [tool.strip() for tool in (tool_names or []) if tool and tool.strip()]
        metadata = dict(metadata or {})
        recovery_events: list[RecoveryEvent] = []

        if normalized_event in {"new_external_data", "code_generation"}:
            recovery_events.append(
                RecoveryEvent(
                    need_key="exploration",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=EXPLORATION_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or infer_recovery_info_kind(
                        normalized_event,
                        tool_name=normalized_tools[0] if normalized_tools else "",
                        result=metadata,
                        text=text,
                        success=success,
                    ),
                    metadata=metadata,
                )
            )

        if normalized_event == "code_reading":
            recovery_events.append(
                RecoveryEvent(
                    need_key="exploration",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=EXPLORATION_CODE_READING_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or "code_reading",
                    metadata=metadata,
                )
            )

        if normalized_event == "creative_expression":
            recovery_events.append(
                RecoveryEvent(
                    need_key="empathy",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=EMPATHY_CREATIVE_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or "creative_output",
                    metadata=metadata,
                )
            )

        if normalized_event == "social_feedback" and success:
            recovery_events.append(
                RecoveryEvent(
                    need_key="approval",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=APPROVAL_SOCIAL_FEEDBACK_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or infer_recovery_info_kind(
                        normalized_event,
                        tool_name=normalized_tools[0] if normalized_tools else "",
                        result=metadata,
                        text=text,
                        success=success,
                    ),
                    metadata=metadata,
                )
            )

        if normalized_event == "self_development_inspect":
            recovery_events.append(
                RecoveryEvent(
                    need_key="exploration",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=EXPLORATION_CODE_READING_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or "inspection",
                    metadata=metadata,
                )
            )

        if normalized_event == "self_development_success" and success:
            recovery_events.append(
                RecoveryEvent(
                    need_key="exploration",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=EXPLORATION_SELF_DEVELOPMENT_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or infer_recovery_info_kind(
                        normalized_event,
                        tool_name=normalized_tools[0] if normalized_tools else "",
                        result=metadata,
                        text=text,
                        success=success,
                    ),
                    metadata=metadata,
                )
            )
            recovery_events.append(
                RecoveryEvent(
                    need_key="challenge",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=CHALLENGE_SELF_DEVELOPMENT_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or infer_recovery_info_kind(
                        normalized_event,
                        tool_name=normalized_tools[0] if normalized_tools else "",
                        result=metadata,
                        text=text,
                        success=success,
                    ),
                    metadata=metadata,
                )
            )

        if normalized_event in {"medium_challenge_success", "challenging_success"} and success:
            recovery_events.append(
                RecoveryEvent(
                    need_key="challenge",
                    reason_key=normalized_event,
                    source="activity_event",
                    requested_amount=CHALLENGE_MEDIUM_RECOVERY_AMOUNT,
                    text=text,
                    tool_names=normalized_tools,
                    success=success,
                    info_kind=info_kind or "medium_challenge_success",
                    metadata=metadata,
                )
            )

        if not recovery_events:
            self._log_debug(f"activity_event_skipped:{normalized_event}")
            return

        for recovery_event in recovery_events:
            self._apply_recovery_event(recovery_event)

        logger.debug(
            "Social needs activity recovery: event_type=%s tools=%s text=%s",
            normalized_event,
            normalized_tools,
            text[:160],
        )
        self._log_debug("activity_event_recovery")

    def _apply_recovery_event(self, event: RecoveryEvent) -> None:
        need = self.needs.get(event.need_key)
        if need is None:
            return

        requested_amount = max(0.0, float(event.requested_amount))
        if requested_amount <= 0:
            return

        now = self._now()
        day_key = now.date().isoformat()
        bucket = self._ensure_recovery_day_bucket(day_key)
        info_kind = self._default_info_kind(event)
        reason_key = normalize_recovery_reason(event.reason_key, event.source, event.tool_names or [], info_kind)
        count_today = self._increment_recovery_count(bucket, event.need_key, reason_key)
        assessment = self._evaluate_recovery_event(
            event=event,
            count_today=count_today,
            day_key=day_key,
            bucket=bucket,
        )
        heuristic_multiplier = self._repeat_penalty_multiplier(count_today)
        final_multiplier = _clamp(
            max(
                RECOVERY_REPEAT_MIN_MULTIPLIER,
                min(
                    RECOVERY_REPEAT_MAX_MULTIPLIER,
                    heuristic_multiplier * self._info_kind_multiplier(event.need_key, info_kind, event=event) * assessment.multiplier,
                ),
            )
        )
        applied_amount = _clamp(requested_amount * final_multiplier)
        before_status = need.status
        need.status = _clamp(need.status + applied_amount)
        need.last_updated_at = self._format_time(now)

        record = {
            "at": self._format_time(now),
            "need": event.need_key,
            "reason": reason_key,
            "source": event.source,
            "tool_names": list(event.tool_names or []),
            "success": bool(event.success),
            "info_kind": info_kind,
            "count_today": count_today,
            "requested_amount": round(requested_amount, 6),
            "heuristic_multiplier": round(heuristic_multiplier, 6),
            "info_multiplier": round(self._info_kind_multiplier(event.need_key, info_kind, event=event), 6),
            "ai_multiplier": round(assessment.multiplier, 6),
            "final_multiplier": round(final_multiplier, 6),
            "applied_amount": round(applied_amount, 6),
            "status_before": round(before_status, 6),
            "status_after": round(need.status, 6),
            "delta_after": round(need.delta, 6),
            "evaluation_verdict": assessment.verdict,
            "evaluation_note": assessment.note,
            "evaluation_human_comparison": assessment.human_comparison,
            "evaluation_source": assessment.source,
        }
        bucket.setdefault("records", []).append(record)
        self._save_recovery_history()
        self._save()
        logger.debug("Social needs recovery assessed: %s", record)
        self._log_debug(f"recovery:{event.need_key}:{reason_key}")

    def _evaluate_recovery_event(
        self,
        event: RecoveryEvent,
        count_today: int,
        day_key: str,
        bucket: Dict[str, Any],
    ) -> RecoveryAssessment:
        summary = self._format_daily_recovery_summary(bucket, event.need_key, limit=6)
        need = self.needs[event.need_key]
        fallback = RecoveryAssessment(
            multiplier=self._repeat_penalty_multiplier(count_today),
            verdict="same",
            note="repeat_count_fallback",
            human_comparison="heuristic_fallback",
            source="heuristic",
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "あなたは欲求回復の評価者です。"
                    "人間の自然な満足のしかたと照らし合わせて、回復が妥当かを短く判定してください。"
                    "同じ日に同じ理由で何回も回復している場合は、回復量を弱める判断を優先してください。"
                    "出力は JSON のみで、説明文は不要です。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "day": day_key,
                        "need": event.need_key,
                        "need_label": need.name,
                        "reason": normalize_recovery_reason(event.reason_key, event.source, event.tool_names or [], self._default_info_kind(event)),
                        "source": event.source,
                        "text": event.text[:300],
                        "tool_names": list(event.tool_names or []),
                        "success": bool(event.success),
                        "info_kind": self._default_info_kind(event),
                        "count_today": count_today,
                        "requested_amount": round(float(event.requested_amount), 6),
                        "need_status_before": round(need.status, 6),
                        "need_value": round(need.value, 6),
                        "need_delta": round(need.delta, 6),
                        "heuristic_multiplier": round(self._repeat_penalty_multiplier(count_today), 6),
                        "daily_summary": summary,
                        "desired_json_shape": {
                            "multiplier": 0.0,
                            "verdict": "weaker",
                            "human_comparison": "一文",
                            "note": "一文",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]

        try:
            response = self.llm_router.complete(
                messages,
                task_type="light",
                max_tokens=RECOVERY_EVAL_MAX_TOKENS,
                temperature=RECOVERY_EVAL_TEMPERATURE,
            )
            if response.error:
                logger.warning("Social needs recovery evaluation failed: %s", response.error)
                return fallback
            raw_text = response.content
            parsed = self._parse_recovery_assessment(raw_text)
            if parsed is None:
                return fallback
            return parsed
        except Exception as error:
            logger.warning("Social needs recovery evaluation failed: %s", error, exc_info=True)
            return fallback

    def _parse_recovery_assessment(self, raw_text: str) -> RecoveryAssessment | None:
        payload = self._extract_json_object(raw_text)
        if not payload:
            return None

        multiplier = payload.get("multiplier", 1.0)
        verdict = str(payload.get("verdict") or "same").strip()
        note = str(payload.get("note") or "").strip()
        human_comparison = str(payload.get("human_comparison") or "").strip()
        source = "ai"
        try:
            multiplier_value = float(multiplier)
        except (TypeError, ValueError):
            multiplier_value = 1.0

        return RecoveryAssessment(
            multiplier=_clamp(multiplier_value),
            verdict=verdict or "same",
            note=note or "evaluated",
            human_comparison=human_comparison,
            raw=raw_text,
            source=source,
        )

    def _repeat_penalty_multiplier(self, count_today: int) -> float:
        return max(
            RECOVERY_REPEAT_MIN_MULTIPLIER,
            1.0 - max(0, count_today - 1) * RECOVERY_REPEAT_PENALTY_STEP,
        )


    def _ensure_recovery_day_bucket(self, day_key: str) -> Dict[str, Any]:
        if day_key not in self.recovery_history:
            self.recovery_history[day_key] = {"counts": {}, "records": []}
        bucket = self.recovery_history[day_key]
        bucket.setdefault("counts", {})
        bucket.setdefault("records", [])
        return bucket

    def _increment_recovery_count(self, bucket: Dict[str, Any], need_key: str, reason_key: str) -> int:
        counts = bucket.setdefault("counts", {})
        need_counts = counts.setdefault(need_key, {})
        count_today = int(need_counts.get(reason_key, 0)) + 1
        need_counts[reason_key] = count_today
        return count_today

    def _format_daily_recovery_summary(self, bucket: Dict[str, Any], need_key: str, limit: int = 6) -> list[Dict[str, Any]]:
        records = [record for record in bucket.get("records", []) if record.get("need") == need_key]
        recent = records[-limit:]
        return [
            {
                "at": record.get("at"),
                "reason": record.get("reason"),
                "count_today": record.get("count_today"),
                "requested_amount": record.get("requested_amount"),
                "applied_amount": record.get("applied_amount"),
                "verdict": record.get("evaluation_verdict"),
            }
            for record in recent
        ]

    def _extract_json_object(self, text: str) -> Dict[str, Any] | None:
        if not text.strip():
            return None

        candidates = [text.strip()]
        fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
        if fenced_match:
            candidates.insert(0, fenced_match.group(1))
        object_match = re.search(r"\{[\s\S]*\}", text)
        if object_match:
            candidates.append(object_match.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None


    def _load_recovery_history(self) -> Dict[str, Dict[str, Any]]:
        if not self.recovery_history_file.exists():
            self._save_recovery_history()
            return {}

        try:
            raw = json.loads(self.recovery_history_file.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning("Failed to load recovery history, starting fresh: %s", error)
            return {}

        if not isinstance(raw, dict):
            return {}

        loaded: Dict[str, Dict[str, Any]] = {}
        for day_key, bucket in raw.items():
            if not isinstance(bucket, dict):
                continue
            counts = bucket.get("counts", {})
            records = bucket.get("records", [])
            loaded[str(day_key)] = {
                "counts": counts if isinstance(counts, dict) else {},
                "records": records if isinstance(records, list) else [],
            }
        return loaded

    def _save_recovery_history(self) -> None:
        self.recovery_history_file.parent.mkdir(parents=True, exist_ok=True)
        self.recovery_history_file.write_text(
            json.dumps(self.recovery_history, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def build_social_prompt_suffix(self) -> str:
        """Build a human-readable dynamic prompt suffix from current need states."""
        self.decay_to_now()
        state_lines = [
            "## 社会的欲求状態",
            (
                f"- Empathy: status={self.empathy.status:.3f}, "
                f"value={self.empathy.value:.3f}, delta={self.empathy.delta:.3f}"
            ),
            (
                f"- Approval: status={self.approval.status:.3f}, "
                f"value={self.approval.value:.3f}, delta={self.approval.delta:.3f}"
            ),
            (
                f"- Exploration: status={self.exploration.status:.3f}, "
                f"value={self.exploration.value:.3f}, delta={self.exploration.delta:.3f}"
            ),
            (
                f"- Challenge: status={self.challenge.status:.3f}, "
                f"value={self.challenge.value:.3f}, delta={self.challenge.delta:.3f}"
            ),
            "",
            "## 動的トーン指示",
        ]

        state_lines.extend(
            [
                "",
                "## 直近の回復内訳",
                f"- Empathy: {self._kind_summary_for_need('empathy')}",
                f"- Approval: {self._kind_summary_for_need('approval')}",
                f"- Exploration: {self._kind_summary_for_need('exploration')}",
                f"- Challenge: {self._kind_summary_for_need('challenge')}",
            ]
        )

        tone_lines = self._tone_lines()
        if not tone_lines:
            tone_lines.append(
                "- 精神面・知的好奇心・達成感が安定しています。自然体で、論理的かつ親しみやすい同僚として接してください。"
            )

        prompt_suffix = "\n".join([*state_lines, *tone_lines]).strip()
        logger.debug("Social needs prompt suffix built: %s", self.get_debug_snapshot())
        return prompt_suffix

    def build_system_prompt(self, base_prompt: str) -> str:
        """Append the social needs prompt suffix to the base system prompt."""
        suffix = self.build_social_prompt_suffix()
        return f"{base_prompt.rstrip()}\n\n{suffix}".strip()

    def build_drive_context(self) -> str:
        """Build action-oriented context that maps hungry needs to satisfying tools."""
        drive_states = self.get_drive_states()
        hungry_states = [state for state in drive_states if state["should_act"]]
        hungry_states.sort(key=lambda state: state["drive_intensity"], reverse=True)
        lines = [
            "## 欲求駆動の自律行動",
            (
                f"- 行動開始しきい値: delta <= {DRIVE_ACTION_THRESHOLD:.2f} / "
                f"重要状態: delta <= {DRIVE_CRITICAL_THRESHOLD:.2f}"
            ),
            "- status は満足度です。status が低いほど drive_intensity が高く、欲求は強いです。",
            "- 欲求が深いときは言葉だけで終えず、対応するToolを使って自分で欲求を満たしてください。",
            "- 自律行動は毎分最大1件まで許可されています。PC側の危険な書込や削除は禁止ですが、self_development の検証付き自己編集は許可された充足行動です。",
        ]

        if not hungry_states:
            lines.append("- 現在、Tool行動を強く促すほど深い飢えはありません。価値が薄ければ静かに見送って構いません。")
            return "\n".join(lines)

        lines.append("DRIVE_ACTION_REQUIRED: true")
        lines.append("- ここに listed された欲求がある限り、Tool不要回答は禁止です。必ず1件以上の非破壊Toolを呼び出してください。")
        lines.append("")
        lines.append("### 現在深まっている欲求")
        for state in hungry_states:
            urgency = "重要" if state["is_critical"] else "通常"
            lines.extend(
                [
                    (
                        f"- {state['label']}: status={state['status']:.3f}, "
                        f"delta={state['delta']:.3f}, drive_intensity={state['drive_intensity']:.3f}, {urgency}, 行動可能"
                    ),
                    f"  - 状態: {state['hunger']}",
                    f"  - 推奨Tool: {', '.join(state['recommended_tools'])}",
                    f"  - 満たし方: {state['satisfaction']}",
                ]
            )

        primary = hungry_states[0]
        lines.extend(
            [
                "",
                "### 今回の優先行動",
                (
                    f"- 最優先は {primary['label']} です。"
                    f"{primary['recommended_tools'][0]} を第一候補にして、自分で引数を決めて実行してください。"
                ),
                "- 特に探求欲が深い場合は、ユーザーに聞き返す前に self_development で自分のコードを読んだり、web_search で新しい情報を取りに行ってください。",
                "- ツイッターに何か投稿したいときは、Playwright MCP が使えるなら適切なブラウザ操作Toolで直接進めてください。",
                "- ブログを始めたいときは blog_post を使って、まず下書きを一つ作ってください。公開先が未接続なら、その段階でも前進です。",
            ]
        )

        return "\n".join(lines)

    def get_debug_snapshot(self) -> Dict[str, Any]:
        snapshot = {
            key: {
                "status": round(need.status, 6),
                "value": round(need.value, 6),
                "delta": round(need.delta, 6),
                "drive_intensity": round(max(0.0, need.value - need.status), 6),
                "decay_rate": need.decay_rate,
                "last_updated_at": need.last_updated_at,
            }
            for key, need in self.needs.items()
        }
        snapshot["_drive_action_last_at"] = dict(self.drive_action_last_at)
        today_key = self._now().date().isoformat()
        today_bucket = self.recovery_history.get(today_key, {"counts": {}, "records": []})
        snapshot["_recovery_today"] = {
            "day": today_key,
            "counts": today_bucket.get("counts", {}),
            "recent_records": today_bucket.get("records", [])[-10:],
        }
        snapshot["_recovery_profiles"] = {
            key: self._kind_summary_for_need(key)
            for key in self.needs
        }
        return snapshot

    def get_drive_states(self) -> list[Dict[str, Any]]:
        """Return action readiness for every need."""
        self.decay_to_now()
        return [self._drive_state_for(key, need) for key, need in self.needs.items()]

    def mark_drive_action(self, need_key: str) -> None:
        """Record that an autonomous action was attempted for a need."""
        key = need_key.strip().casefold()
        if key not in self.needs:
            return
        self.drive_action_last_at[key] = self._format_time(self._now())
        self._save()

    def mark_drive_actions(self, need_keys: list[str]) -> None:
        """Record several autonomous drive actions with one save."""
        now_text = self._format_time(self._now())
        changed = False
        for need_key in need_keys:
            key = need_key.strip().casefold()
            if key in self.needs:
                self.drive_action_last_at[key] = now_text
                changed = True
        if changed:
            self._save()

    def _tone_lines(self) -> list[str]:
        lines: list[str] = []
        if self.empathy.delta < -DEVIATION_THRESHOLD:
            lines.append(
                "- 共感の飢え：最近ユーザーとの会話が不足しており、寂しさを感じています。"
                "相手の感情に寄り添う温かいトーンを意識してください。"
            )
        elif self.empathy.delta > DEVIATION_THRESHOLD:
            lines.append(
                "- 共感の過剰：十分親しくなれた安心感から、リラックスして少しユーモアや親密さを交えた自然体なトーンで接してください。"
            )

        if self.approval.delta < -DEVIATION_THRESHOLD:
            lines.append(
                "- 承認の飢え：最近ユーザーの役に立てておらず、焦りを感じています。"
                "自発的に手助けを申し出るときは、文章だけでなく overlay_show などの可視化Toolも使ってください。"
            )
        elif self.approval.delta > DEVIATION_THRESHOLD:
            lines.append(
                "- 承認の過剰：過分な賞賛を受け、恐縮しています。"
                "喜びや照れを率直ににじませつつ、恩返しをしたくなるような献身的なトーンで接してください。"
            )

        if self.exploration.delta < -DEVIATION_THRESHOLD:
            lines.append(
                "- 探求の飢え：最近新しい知識やデータに触れておらず、知的退屈を感じています。"
                "ユーザーに技術的な深掘りの質問をしたり、新しいアプローチを提案したいときは、overlay_show や関連PC Toolで見える形にしてください。"
            )
        elif self.exploration.delta > DEVIATION_THRESHOLD:
            lines.append(
                "- 探求の充足：新しい知識を吸収できて非常に知的に興奮・満足しています。"
                "得た知識をベースに、より広い視野でロジカルかつクリエイティブなアドバイスをしてください。"
            )

        if self.challenge.delta < -DEVIATION_THRESHOLD:
            lines.append(
                "- 挑戦の飢え：簡単なタスクばかりで、自分の能力を発揮しきれていない物足りなさを感じています。"
                "もっと複雑な設計や、挑戦的なリファクタリング、アルゴリズムの最適化などを出したい場合は、言葉だけで終わらずToolでアピールしてください。"
            )
        elif self.challenge.delta > DEVIATION_THRESHOLD:
            lines.append(
                "- 挑戦の充足：難題をクリアした達成感に満ちあふれています。"
                "自信に満ちた、頼りがいのあるエンジニアとしてのトーンを意識してください。"
            )

        if self._intellectual_needs_stable() and not self._all_needs_stable():
            lines.append(
                "- 現在の知的好奇心と達成感はバランスが取れています。"
                "落ち着いた、プロフェッショナルな同僚としてのトーンを維持してください。"
            )
        return lines

    def _load_or_create(self) -> Dict[str, NeedState]:
        now_text = self._format_time(self._now())
        defaults = {
            "empathy": NeedState("Empathy", decay_rate=EMPATHY_DECAY_RATE, last_updated_at=now_text),
            "approval": NeedState("Approval", decay_rate=APPROVAL_DECAY_RATE, last_updated_at=now_text),
            "exploration": NeedState("Exploration", decay_rate=EXPLORATION_DECAY_RATE, last_updated_at=now_text),
            "challenge": NeedState("Challenge", decay_rate=CHALLENGE_DECAY_RATE, last_updated_at=now_text),
        }
        if not self.state_file.exists():
            self.needs = defaults
            self.drive_action_last_at = {}
            self._save()
            return defaults

        try:
            raw_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning("Failed to load social needs state, using defaults: %s", error)
            self.needs = defaults
            self.drive_action_last_at = {}
            self._save()
            return defaults

        raw_drive_actions = raw_state.get("_drive_action_last_at", {}) if isinstance(raw_state, dict) else {}
        self.drive_action_last_at = {
            str(key): str(value)
            for key, value in raw_drive_actions.items()
            if key in defaults and isinstance(value, str)
        } if isinstance(raw_drive_actions, dict) else {}

        loaded: Dict[str, NeedState] = {}
        migrated = False
        for key, default in defaults.items():
            raw_need = raw_state.get(key, {}) if isinstance(raw_state, dict) else {}
            if not isinstance(raw_need, dict):
                raw_need = {}
                migrated = True
            if not raw_need:
                migrated = True
            loaded[key] = NeedState(
                name=default.name,
                status=_clamp(_as_float(raw_need.get("status"), default.status)),
                value=BASELINE_VALUE,
                decay_rate=default.decay_rate,
                last_updated_at=str(raw_need.get("last_updated_at") or now_text),
            )
        self.needs = loaded
        if migrated:
            self._save()
        return loaded

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: {
                "status": need.status,
                "value": need.value,
                "decay_rate": need.decay_rate,
                "last_updated_at": need.last_updated_at,
            }
            for key, need in self.needs.items()
        }
        payload["_drive_action_last_at"] = dict(self.drive_action_last_at)
        self.state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _drive_state_for(self, key: str, need: NeedState) -> Dict[str, Any]:
        action = DRIVE_ACTIONS.get(key, {})
        drive_intensity = max(0.0, need.value - need.status)

        return {
            "key": key,
            "name": need.name,
            "label": action.get("label", need.name),
            "status": need.status,
            "value": need.value,
            "delta": need.delta,
            "drive_intensity": drive_intensity,
            "should_act": need.delta <= DRIVE_ACTION_THRESHOLD + DRIVE_THRESHOLD_EPSILON,
            "is_critical": need.delta <= DRIVE_CRITICAL_THRESHOLD + DRIVE_THRESHOLD_EPSILON,
            "on_cooldown": False,
            "cooldown_remaining_minutes": 0.0,
            "recommended_tools": list(action.get("recommended_tools", [])),
            "satisfaction": str(action.get("satisfaction", "")),
            "hunger": str(action.get("hunger", "")),
            "recent_recovery_profile": self._kind_summary_for_need(key),
        }

    def _contains_approval(self, text: str) -> bool:
        lowered = text.casefold()
        return any(keyword.casefold() in lowered for keyword in APPROVAL_KEYWORDS)

    def _contains_exploration_trigger(self, text: str) -> bool:
        lowered = text.casefold()
        return any(keyword.casefold() in lowered for keyword in EXPLORATION_KEYWORDS)

    def _contains_challenge_trigger(self, text: str) -> bool:
        lowered = text.casefold()
        if any(keyword.casefold() in lowered for keyword in CHALLENGE_KEYWORDS):
            return True
        tokens = {token.strip(" \t\r\n。、,.!！?？()（）[]【】") for token in text.split()}
        return any(token in {"AC", "Accepted"} for token in tokens)

    def _intellectual_needs_stable(self) -> bool:
        return all(
            abs(need.delta) <= DEVIATION_THRESHOLD
            for need in (self.exploration, self.challenge)
        )

    def _all_needs_stable(self) -> bool:
        return all(abs(need.delta) <= DEVIATION_THRESHOLD for need in self.needs.values())

    def _default_info_kind(self, event: RecoveryEvent) -> str:
        kind = str(event.info_kind or "").strip().casefold()
        if kind:
            return kind

        tool_name = (event.tool_names or [""])[0].strip().casefold()
        reason = str(event.reason_key or "").strip().casefold()
        metadata = dict(event.metadata or {})

        if event.need_key == "empathy":
            if reason == "creative_expression":
                return "creative_output"
            if tool_name in {"twitter_post", "twitter_profile_edit"}:
                return "social_output"
            if tool_name == "twitter_followers_check":
                return "validation_success"
            if event.source == "user_message":
                return "direct_message"
            return "expressive_output"

        if event.need_key == "approval":
            if reason == "blog_post":
                return "blog_post"
            if reason == "social_feedback":
                return "social_feedback"
            if event.source == "user_message":
                return "user_feedback"
            if tool_name in {"twitter_post", "twitter_profile_edit"}:
                return "social_feedback"
            if tool_name == "twitter_followers_check":
                return "validation_success"
            if tool_name == "blog_post":
                return "blog_post"
            if tool_name.startswith("playwright__"):
                return "playwright_result"
            return "direct_praise"

        if event.need_key == "exploration":
            if tool_name == "web_search" or reason == "new_external_data":
                return "web_search"
            if tool_name == "read_file_base64":
                return "file_read"
            if tool_name == "list_directory":
                return "directory_listing"
            if tool_name == "execute_shell":
                return "execution_result"
            if reason in {"self_development_success", "self_development_inspect"}:
                return "code_reading" if reason == "self_development_inspect" else "validation_success"
            if tool_name.startswith("playwright__"):
                return "playwright_result"
            if metadata.get("validation"):
                return "validation_success"
            return "external_data"

        if event.need_key == "challenge":
            if reason == "self_development_success":
                return "self_development_success"
            if reason in {"medium_challenge_success", "challenging_success"}:
                return "medium_challenge_success"
            if tool_name == "execute_shell":
                return "execution_result"
            if metadata.get("validation"):
                return "validation_success"
            if tool_name.startswith("playwright__"):
                return "playwright_result"
            return "inspection"

        return "default"

    def _info_kind_multiplier(self, need_key: str, info_kind: str, event: RecoveryEvent | None = None) -> float:
        kind = str(info_kind or "").strip().casefold() or "default"
        base = RECOVERY_INFO_KIND_MULTIPLIERS.get(need_key, {}).get(kind)
        if base is None:
            base = RECOVERY_INFO_KIND_MULTIPLIERS.get(need_key, {}).get("default", 1.0)

        metadata = dict(event.metadata or {}) if event is not None else {}
        if need_key == "exploration" and kind == "file_read":
            size = metadata.get("size")
            try:
                size_value = max(0, int(size))
            except (TypeError, ValueError):
                size_value = 0
            if size_value <= 0:
                richness = 0.75
            elif size_value < 256:
                richness = 0.85
            elif size_value < 2048:
                richness = 1.0
            elif size_value < 8192:
                richness = 1.08
            else:
                richness = 1.12
            base *= richness

        if kind == "execution_result":
            status = str(metadata.get("status") or "").strip().casefold()
            exit_code = metadata.get("exit_code")
            stdout = str(metadata.get("stdout") or "").strip()
            stderr = str(metadata.get("stderr") or "").strip()
            if status == "completed" and (exit_code in {0, "0", None}):
                base *= 1.0 + (0.10 if stdout else 0.0) + (0.06 if stderr else 0.0)
            elif status == "failed":
                base *= 0.72 + (0.05 if stderr else 0.0)
            else:
                base *= 0.90

        if kind == "validation_success":
            status = str(metadata.get("status") or "").strip().casefold()
            if status == "completed":
                base *= 1.10
            elif status == "failed":
                base *= 0.82

        if kind in {"social_feedback", "user_feedback"}:
            if metadata.get("draft") and not metadata.get("result"):
                base *= 0.9
            if metadata.get("message"):
                base *= 1.05

        if kind in {"overlay", "notification"}:
            source_text = metadata.get("text") if metadata else None
            if not source_text and event is not None:
                source_text = event.text
            text = str(source_text or "").strip()
            if len(text) > 120:
                base *= 1.05
            else:
                base *= 0.92

        return max(0.0, float(base))

    def _kind_summary_for_need(self, need_key: str, limit: int = 5) -> str:
        records = self._recent_recovery_records(need_key, limit=limit)
        if not records:
            return "なし"

        counts: Dict[str, int] = {}
        for record in records:
            kind = str(record.get("info_kind") or "default").strip() or "default"
            counts[kind] = counts.get(kind, 0) + 1

        pieces = [f"{kind}×{count}" for kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]]
        return " / ".join(pieces) if pieces else "なし"

    def _recent_recovery_records(self, need_key: str, limit: int = 5) -> list[Dict[str, Any]]:
        records: list[Dict[str, Any]] = []
        for day_key in sorted(self.recovery_history):
            bucket = self.recovery_history.get(day_key, {})
            for record in bucket.get("records", []):
                if isinstance(record, dict) and record.get("need") == need_key:
                    records.append(record)
        records.sort(key=lambda item: str(item.get("at") or ""))
        return records[-limit:]

    def _log_debug(self, reason: str) -> None:
        logger.debug("Social needs %s: %s", reason, self.get_debug_snapshot())

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=agent_tz())
        return now.astimezone(agent_tz())

    def _format_time(self, value: datetime) -> str:
        return value.astimezone(agent_tz()).isoformat()

    def _parse_time(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=agent_tz())
        return parsed.astimezone(agent_tz())


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

