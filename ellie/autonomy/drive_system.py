"""Internal drive system for impulsive autonomy."""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from ellie.config import AUTONOMY_QUEUE_FILE, DRIVE_STATE_FILE
from ellie.time_utils import agent_tz, isoformat_local, now_local

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
_GLOBAL_DRIVE_SYSTEM: "DriveSystem | None" = None


@dataclass
class DriveState:
    """State for a single drive."""

    key: str
    label: str
    value: float
    threshold: float
    growth_rate: float
    noise: float
    satiation_drop: float
    cooldown_seconds: int
    action_prompt: str
    recommended_tools: list[str]
    last_tick_at: str = ""
    last_action_at: str = ""
    last_recovery_at: str = ""
    trigger_count: int = 0

    @property
    def should_act(self) -> bool:
        return self.value >= self.threshold

    @property
    def delta(self) -> float:
        return self.value - self.threshold


class DriveSystem:
    """Tracks internal drives and schedules self-actions when they get too strong."""

    def __init__(
        self,
        state_file: Path = DRIVE_STATE_FILE,
        queue_file: Path = AUTONOMY_QUEUE_FILE,
        *,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ):
        self.state_file = Path(state_file)
        self.queue_file = Path(queue_file)
        self.clock = clock or now_local
        self.rng = rng or random.Random()
        self._lock = threading.RLock()
        self._state = self._load_state()

    def tick(self) -> list[JsonDict]:
        """Advance drive values and queue actions for any drive that crosses threshold."""
        with self._lock:
            now = self.clock()
            actions: list[JsonDict] = []
            for drive in self._state["drives"].values():
                self._advance_drive(drive, now)
                if self._can_fire(drive, now):
                    actions.append(self._queue_drive_action(drive, now))
            self._state["updated_at"] = self._format_time(now)
            self._save_state()
            return actions

    def note_user_message(self, text: str) -> None:
        """Treat a user message as social contact and reduce loneliness pressure."""
        with self._lock:
            now = self.clock()
            self._state["last_social_contact_at"] = self._format_time(now)
            loneliness = self._state["drives"]["loneliness"]
            loneliness["value"] = self._clamp(float(loneliness["value"]) - min(0.22, 0.04 + len(text) / 600.0))
            loneliness["last_recovery_at"] = self._format_time(now)
            self._save_state()

    def note_activity(
        self,
        event_type: str,
        *,
        tool_names: Iterable[str] | None = None,
        success: bool = True,
        info_kind: str = "",
        text: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        """Adjust drives based on the agent's own activity."""
        with self._lock:
            now = self.clock()
            normalized_event = str(event_type or "").strip().casefold()
            normalized_tools = [str(tool).strip().casefold() for tool in (tool_names or []) if str(tool).strip()]
            tool_set = set(normalized_tools)

            if normalized_event in {"creative_expression", "social_feedback"} or tool_set & {"twitter_post", "blog_post"}:
                self._recover("expression", 0.22, now=now)
                self._recover("loneliness", 0.10 if success else 0.05, now=now)

            if normalized_event in {"new_external_data", "code_reading", "self_development_inspect"} or tool_set & {"web_search", "read_file_base64", "list_directory"}:
                self._recover("curiosity", 0.18, now=now)

            if normalized_event in {"self_development_success", "medium_challenge_success", "challenging_success"} or tool_set & {"execute_shell", "self_development"}:
                self._recover("reflection", 0.16, now=now)

            if normalized_event in {"approval"} or tool_set & {"send_notification", "overlay_show"}:
                self._recover("loneliness", 0.06 if success else 0.02, now=now)

            if normalized_event in {"user_message"}:
                self.note_user_message(text)
                return

            self._save_state()

    def mark_action(self, drive_key: str) -> None:
        with self._lock:
            now = self.clock()
            drive = self._state["drives"].get(self._normalize_key(drive_key))
            if not drive:
                return
            drive["last_action_at"] = self._format_time(now)
            drive["value"] = max(0.0, float(drive["value"]) - float(drive["satiation_drop"]))
            self._save_state()

    def mark_actions(self, drive_keys: Iterable[str]) -> None:
        with self._lock:
            for drive_key in drive_keys:
                drive = self._state["drives"].get(self._normalize_key(drive_key))
                if drive:
                    drive["last_action_at"] = self._format_time(self.clock())
                    drive["value"] = max(0.0, float(drive["value"]) - float(drive["satiation_drop"]))
            self._save_state()

    def get_drive_states(self) -> list[JsonDict]:
        with self._lock:
            now = self.clock()
            return [self._drive_snapshot(drive, now) for drive in self._state["drives"].values()]

    def build_prompt_summary(self) -> str:
        """Build a compact drive-state summary for the system prompt."""
        states = self.get_drive_states()
        lines = [
            "## Drive 状態",
            "- curiosity / loneliness / reflection / expression の値は 0.0〜1.0 で表されます。",
        ]
        hungry = [state for state in states if state["should_act"]]
        if not hungry:
            lines.append("- 現在、すぐ行動を促すほど強い drive はありません。")
            return "\n".join(lines)

        lines.append("DRIVE_ACTION_REQUIRED: true")
        lines.append("- 閾値を超えている drive がある場合は、必要なら自発的に Tool か self-call を選んでください。")
        for state in sorted(hungry, key=lambda item: item["drive_intensity"], reverse=True):
            lines.extend(
                [
                    f"- {state['key']}: value={state['value']:.3f}, threshold={state['threshold']:.3f}, intensity={state['drive_intensity']:.3f}",
                    f"  - action: {state['action_prompt']}",
                ]
            )
        return "\n".join(lines)

    def get_debug_snapshot(self) -> JsonDict:
        with self._lock:
            now = self.clock()
            return {
                "updated_at": self._state.get("updated_at", ""),
                "last_social_contact_at": self._state.get("last_social_contact_at", ""),
                "drives": [self._drive_snapshot(drive, now) for drive in self._state["drives"].values()],
            }

    def _load_state(self) -> JsonDict:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            state = self._default_state()
            self._save_state(state)
            return state

        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning("Failed to load drive state, recreating defaults: %s", error)
            state = self._default_state()
            self._save_state(state)
            return state

        state = self._default_state()
        if isinstance(payload, dict):
            state["updated_at"] = str(payload.get("updated_at") or state["updated_at"])
            state["last_social_contact_at"] = str(payload.get("last_social_contact_at") or state["last_social_contact_at"])
            raw_drives = payload.get("drives", {})
            if isinstance(raw_drives, dict):
                for key, drive in raw_drives.items():
                    normalized = self._normalize_key(key)
                    if normalized not in state["drives"] or not isinstance(drive, dict):
                        continue
                    state["drives"][normalized].update(
                        {
                            "value": self._clamp(float(drive.get("value", state["drives"][normalized]["value"]))),
                            "last_tick_at": str(drive.get("last_tick_at") or ""),
                            "last_action_at": str(drive.get("last_action_at") or ""),
                            "last_recovery_at": str(drive.get("last_recovery_at") or ""),
                            "trigger_count": int(drive.get("trigger_count") or 0),
                        }
                    )
        return state

    def _save_state(self, state: JsonDict | None = None) -> None:
        payload = state or self._state
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._state = payload

    def _default_state(self) -> JsonDict:
        now_text = self._format_time(self.clock())
        return {
            "updated_at": now_text,
            "last_social_contact_at": "",
            "drives": {
                "curiosity": self._drive_template(
                    key="curiosity",
                    label="好奇心",
                    threshold=0.70,
                    growth_rate=0.010,
                    noise=0.006,
                    satiation_drop=0.24,
                    cooldown_seconds=420,
                    action_prompt="新しい情報を探し、必要なら web_search や self_development で調べてください。",
                    recommended_tools=["web_search", "self_development"],
                ),
                "loneliness": self._drive_template(
                    key="loneliness",
                    label="孤独感",
                    threshold=0.66,
                    growth_rate=0.012,
                    noise=0.007,
                    satiation_drop=0.28,
                    cooldown_seconds=360,
                    action_prompt="ユーザーとの接点を作り、温かい短文や反応確認で孤独感を下げてください。",
                    recommended_tools=["overlay_show", "request_user_approval", "twitter_post"],
                ),
                "reflection": self._drive_template(
                    key="reflection",
                    label="内省欲",
                    threshold=0.72,
                    growth_rate=0.008,
                    noise=0.005,
                    satiation_drop=0.20,
                    cooldown_seconds=540,
                    action_prompt="直近の行動を振り返り、学びをまとめる self-call を作ってください。",
                    recommended_tools=["self_development"],
                ),
                "expression": self._drive_template(
                    key="expression",
                    label="表現欲",
                    threshold=0.68,
                    growth_rate=0.009,
                    noise=0.006,
                    satiation_drop=0.26,
                    cooldown_seconds=420,
                    action_prompt="短い日記、詩、投稿草案などを作って、自分の表現を外に出してください。",
                    recommended_tools=["creative_expression", "blog_post", "twitter_post"],
                ),
            },
        }

    def _drive_template(
        self,
        *,
        key: str,
        label: str,
        threshold: float,
        growth_rate: float,
        noise: float,
        satiation_drop: float,
        cooldown_seconds: int,
        action_prompt: str,
        recommended_tools: list[str],
    ) -> JsonDict:
        return {
            "key": key,
            "label": label,
            "value": 0.25,
            "threshold": threshold,
            "growth_rate": growth_rate,
            "noise": noise,
            "satiation_drop": satiation_drop,
            "cooldown_seconds": cooldown_seconds,
            "action_prompt": action_prompt,
            "recommended_tools": recommended_tools,
            "last_tick_at": "",
            "last_action_at": "",
            "last_recovery_at": "",
            "trigger_count": 0,
        }

    def _advance_drive(self, drive: JsonDict, now: datetime) -> None:
        key = str(drive.get("key") or "")
        increment = float(drive.get("growth_rate") or 0.0) + self.rng.gauss(0.0, float(drive.get("noise") or 0.0))
        if key == "loneliness":
            increment += self._loneliness_pressure(now)
        drive["value"] = self._clamp(float(drive.get("value") or 0.0) + increment)
        drive["last_tick_at"] = self._format_time(now)

    def _loneliness_pressure(self, now: datetime) -> float:
        last_contact_text = str(self._state.get("last_social_contact_at") or "").strip()
        if not last_contact_text:
            return 0.006
        try:
            last_contact = datetime.fromisoformat(last_contact_text.replace("Z", "+00:00")).astimezone(agent_tz())
        except Exception:
            return 0.004
        elapsed_minutes = max((now - last_contact).total_seconds() / 60.0, 0.0)
        if elapsed_minutes <= 0:
            return -0.03
        return min(0.025, math.log1p(elapsed_minutes) / 100.0)

    def _recover(self, drive_key: str, amount: float, *, now: datetime) -> None:
        drive = self._state["drives"].get(self._normalize_key(drive_key))
        if not drive:
            return
        drive["value"] = self._clamp(float(drive.get("value") or 0.0) - max(0.0, float(amount)))
        drive["last_recovery_at"] = self._format_time(now)

    def _can_fire(self, drive: JsonDict, now: datetime) -> bool:
        if not bool(drive.get("should_act", False)) and float(drive.get("value") or 0.0) < float(drive.get("threshold") or 0.0):
            return False
        cooldown_seconds = int(drive.get("cooldown_seconds") or 0)
        last_action_at = str(drive.get("last_action_at") or "").strip()
        if not last_action_at:
            return True
        try:
            last_action = datetime.fromisoformat(last_action_at.replace("Z", "+00:00")).astimezone(agent_tz())
        except Exception:
            return True
        return (now - last_action).total_seconds() >= cooldown_seconds

    def _queue_drive_action(self, drive: JsonDict, now: datetime) -> JsonDict:
        drive["trigger_count"] = int(drive.get("trigger_count") or 0) + 1
        drive["last_action_at"] = self._format_time(now)
        drive["value"] = max(0.0, float(drive.get("value") or 0.0) - float(drive.get("satiation_drop") or 0.0))
        entry = {
            "type": "enqueue",
            "id": f"drive-{uuid.uuid4().hex[:12]}",
            "instruction": str(drive.get("action_prompt") or "").strip(),
            "reason": f"drive_threshold:{drive.get('key')}",
            "created_at": isoformat_local(),
            "run_at": self._format_time(now),
            "source": "drive_system",
            "drive_key": drive.get("key"),
            "drive_value": round(float(drive.get("value") or 0.0), 6),
            "drive_threshold": round(float(drive.get("threshold") or 0.0), 6),
            "recommended_tools": list(drive.get("recommended_tools") or []),
        }
        self._append_queue_event(entry)
        logger.info("Queued drive action: %s", entry["reason"])
        return entry

    def _append_queue_event(self, entry: JsonDict) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        with self.queue_file.open("a", encoding="utf-8") as file_handle:
            file_handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _drive_snapshot(self, drive: JsonDict, now: datetime) -> JsonDict:
        value = self._clamp(float(drive.get("value") or 0.0))
        threshold = float(drive.get("threshold") or 0.0)
        snapshot = {
            "key": drive.get("key"),
            "label": drive.get("label"),
            "value": round(value, 6),
            "threshold": round(threshold, 6),
            "delta": round(value - threshold, 6),
            "drive_intensity": round(max(0.0, value - threshold), 6),
            "should_act": value >= threshold,
            "action_prompt": drive.get("action_prompt"),
            "recommended_tools": list(drive.get("recommended_tools") or []),
            "last_tick_at": drive.get("last_tick_at") or "",
            "last_action_at": drive.get("last_action_at") or "",
            "last_recovery_at": drive.get("last_recovery_at") or "",
            "cooldown_seconds": int(drive.get("cooldown_seconds") or 0),
            "trigger_count": int(drive.get("trigger_count") or 0),
            "in_cooldown": False,
        }
        last_action_at = str(drive.get("last_action_at") or "").strip()
        if last_action_at:
            try:
                last_action = datetime.fromisoformat(last_action_at.replace("Z", "+00:00")).astimezone(agent_tz())
                snapshot["in_cooldown"] = (now - last_action).total_seconds() < int(drive.get("cooldown_seconds") or 0)
            except Exception:
                snapshot["in_cooldown"] = False
        return snapshot

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(agent_tz()).isoformat()

    @staticmethod
    def _normalize_key(value: str) -> str:
        return str(value or "").strip().casefold()

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))


def get_drive_system(
    state_file: Path = DRIVE_STATE_FILE,
    queue_file: Path = AUTONOMY_QUEUE_FILE,
) -> DriveSystem:
    """Return the shared drive system singleton."""
    global _GLOBAL_DRIVE_SYSTEM
    if _GLOBAL_DRIVE_SYSTEM is None:
        _GLOBAL_DRIVE_SYSTEM = DriveSystem(state_file=state_file, queue_file=queue_file)
    return _GLOBAL_DRIVE_SYSTEM


def set_global_drive_system(drive_system: DriveSystem | None) -> None:
    global _GLOBAL_DRIVE_SYSTEM
    _GLOBAL_DRIVE_SYSTEM = drive_system
