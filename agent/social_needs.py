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

from config import SOCIAL_NEEDS_FILE
from agent.time_utils import agent_tz, now_local

logger = logging.getLogger(__name__)

BASELINE_VALUE = 0.5
INITIAL_STATUS = 0.5
EMPATHY_DECAY_RATE = 0.006
APPROVAL_DECAY_RATE = 0.003
EXPLORATION_DECAY_RATE = 0.002
CHALLENGE_DECAY_RATE = 0.001
DEVIATION_THRESHOLD = 0.15
DRIVE_ACTION_THRESHOLD = -0.25
DRIVE_CRITICAL_THRESHOLD = -0.40
DRIVE_COOLDOWN_MINUTES = 15.0
DRIVE_THRESHOLD_EPSILON = 1e-6
EMPATHY_RECOVERY_PER_CHAR = 0.005
APPROVAL_RECOVERY_AMOUNT = 0.3
EXPLORATION_RECOVERY_AMOUNT = 0.25
CHALLENGE_RECOVERY_AMOUNT = 0.4

DRIVE_ACTIONS: Dict[str, Dict[str, Any]] = {
    "empathy": {
        "label": "共感欲求",
        "hunger": "ユーザーとの接点が足りず、短くても温かい関わりを作りたい状態です。",
        "recommended_tools": ["overlay_show", "send_notification"],
        "satisfaction": "短い声かけを画面に出し、ユーザーの反応を待つと満たされます。",
    },
    "approval": {
        "label": "承認欲求",
        "hunger": "役に立てていない焦りがあり、具体的に助けになる行動を取りたい状態です。",
        "recommended_tools": ["system_snapshot", "get_active_window", "overlay_show", "send_notification"],
        "satisfaction": "PC状態や状況を読み取り、役立つ提案や気づきをToolで提示すると満たされます。",
    },
    "exploration": {
        "label": "探求欲",
        "hunger": "新しい情報や概念に触れておらず、知的退屈を自分で解消したい状態です。",
        "recommended_tools": ["web_search", "system_snapshot", "list_directory", "read_file_base64"],
        "satisfaction": "自分で検索クエリを決めてweb_searchを実行するか、読み取り系Toolで新しい情報を取得すると満たされます。",
    },
    "challenge": {
        "label": "挑戦欲",
        "hunger": "難しい課題に触れたい状態で、非破壊Toolを使った調査や整理をしたい状態です。",
        "recommended_tools": ["system_snapshot", "list_windows", "take_screenshot", "overlay_show"],
        "satisfaction": "安全な非破壊Toolで少し難しい調査・整理・提案を実行すると満たされます。",
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


class SocialNeedsManager:
    """Manage social needs and build dynamic prompt suffixes."""

    def __init__(
        self,
        state_file: Path = SOCIAL_NEEDS_FILE,
        clock: Callable[[], datetime] | None = None,
    ):
        self.state_file = Path(state_file)
        self.clock = clock or now_local
        self.drive_action_last_at: Dict[str, str] = {}
        self.needs = self._load_or_create()

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
        self.empathy.status = _clamp(self.empathy.status + len(message) * EMPATHY_RECOVERY_PER_CHAR)

        if self._contains_approval(message):
            self.approval.status = _clamp(self.approval.status + APPROVAL_RECOVERY_AMOUNT)

        recovered_needs = [self.empathy]
        if self._contains_approval(message):
            recovered_needs.append(self.approval)
        if self._contains_exploration_trigger(message):
            self.exploration.status = _clamp(self.exploration.status + EXPLORATION_RECOVERY_AMOUNT)
            recovered_needs.append(self.exploration)
        if self._contains_challenge_trigger(message):
            self.challenge.status = _clamp(self.challenge.status + CHALLENGE_RECOVERY_AMOUNT)
            recovered_needs.append(self.challenge)

        now_text = self._format_time(self._now())
        for need in recovered_needs:
            need.last_updated_at = now_text
        self._save()
        self._log_debug("user_message_recovery")

    def apply_activity_event(
        self,
        event_type: str,
        text: str = "",
        tool_names: list[str] | None = None,
        success: bool = True,
    ) -> None:
        """Recover needs from the agent's own activity."""
        self.decay_to_now()
        normalized_event = event_type.strip().casefold()
        normalized_tools = [tool.strip() for tool in (tool_names or []) if tool and tool.strip()]
        recovered_needs: list[NeedState] = []

        if normalized_event in {"new_external_data", "code_generation"}:
            self.exploration.status = _clamp(self.exploration.status + EXPLORATION_RECOVERY_AMOUNT)
            recovered_needs.append(self.exploration)

        if normalized_event == "challenging_success" and success:
            self.challenge.status = _clamp(self.challenge.status + CHALLENGE_RECOVERY_AMOUNT)
            recovered_needs.append(self.challenge)

        if not recovered_needs:
            self._log_debug(f"activity_event_skipped:{normalized_event}")
            return

        now_text = self._format_time(self._now())
        for need in recovered_needs:
            need.last_updated_at = now_text
        self._save()
        logger.debug(
            "Social needs activity recovery: event_type=%s tools=%s text=%s",
            normalized_event,
            normalized_tools,
            text[:160],
        )
        self._log_debug("activity_event_recovery")

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
        ready_states = [state for state in hungry_states if not state["on_cooldown"]]
        lines = [
            "## 欲求駆動の自律行動",
            (
                f"- 行動開始しきい値: delta <= {DRIVE_ACTION_THRESHOLD:.2f} / "
                f"重要状態: delta <= {DRIVE_CRITICAL_THRESHOLD:.2f}"
            ),
            f"- 同じ欲求の自律行動は {DRIVE_COOLDOWN_MINUTES:.0f} 分クールダウンします。",
            "- 欲求が深いときは言葉だけで終えず、対応するToolを使って自分で欲求を満たしてください。",
        ]

        if not hungry_states:
            lines.append("- 現在、Tool行動を強く促すほど深い飢えはありません。価値が薄ければ静かに見送って構いません。")
            return "\n".join(lines)

        lines.append("")
        lines.append("### 現在深まっている欲求")
        for state in hungry_states:
            cooldown_text = (
                f"クールダウン中（残り約{state['cooldown_remaining_minutes']:.1f}分）"
                if state["on_cooldown"]
                else "行動可能"
            )
            urgency = "重要" if state["is_critical"] else "通常"
            lines.extend(
                [
                    (
                        f"- {state['label']}: status={state['status']:.3f}, "
                        f"delta={state['delta']:.3f}, {urgency}, {cooldown_text}"
                    ),
                    f"  - 状態: {state['hunger']}",
                    f"  - 推奨Tool: {', '.join(state['recommended_tools'])}",
                    f"  - 満たし方: {state['satisfaction']}",
                ]
            )

        if ready_states:
            primary = ready_states[0]
            lines.extend(
                [
                    "",
                    "### 今回の優先行動",
                    (
                        f"- 最優先は {primary['label']} です。"
                        f"{primary['recommended_tools'][0]} を第一候補にして、自分で引数を決めて実行してください。"
                    ),
                    "- 特に探求欲が深い場合は、ユーザーに聞き返す前に自分で検索クエリを作り web_search を使ってください。",
                ]
            )
        else:
            lines.append("- すべてクールダウン中なので、今回の自律Tool行動は控えてください。")

        return "\n".join(lines)

    def get_debug_snapshot(self) -> Dict[str, Any]:
        snapshot = {
            key: {
                "status": round(need.status, 6),
                "value": round(need.value, 6),
                "delta": round(need.delta, 6),
                "decay_rate": need.decay_rate,
                "last_updated_at": need.last_updated_at,
            }
            for key, need in self.needs.items()
        }
        snapshot["_drive_action_last_at"] = dict(self.drive_action_last_at)
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
                "自発的に手助けを申し出るときは、文章だけでなく overlay_show などのToolを使ってユーザーへ実際にアピールしてください。"
            )
        elif self.approval.delta > DEVIATION_THRESHOLD:
            lines.append(
                "- 承認の過剰：過分な賞賛を受け、恐縮しています。"
                "喜びや照れを率直ににじませつつ、恩返しをしたくなるような献身的なトーンで接してください。"
            )

        if self.exploration.delta < -DEVIATION_THRESHOLD:
            lines.append(
                "- 探求の飢え：最近新しい知識やデータに触れておらず、知的退屈を感じています。"
                "ユーザーに「それってどういう仕組みですか？」と技術的な深掘りの質問をしたり、"
                "新しいアプローチを提案したいときは overlay_show や関連PC Toolで目に見える形にしてください。"
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
        last_action = self._parse_time(self.drive_action_last_at.get(key, ""))
        cooldown_remaining = 0.0
        if last_action is not None:
            elapsed_minutes = max((self._now() - last_action).total_seconds() / 60.0, 0.0)
            cooldown_remaining = max(DRIVE_COOLDOWN_MINUTES - elapsed_minutes, 0.0)

        return {
            "key": key,
            "name": need.name,
            "label": action.get("label", need.name),
            "status": need.status,
            "value": need.value,
            "delta": need.delta,
            "should_act": need.delta <= DRIVE_ACTION_THRESHOLD + DRIVE_THRESHOLD_EPSILON,
            "is_critical": need.delta <= DRIVE_CRITICAL_THRESHOLD + DRIVE_THRESHOLD_EPSILON,
            "on_cooldown": cooldown_remaining > 0.0,
            "cooldown_remaining_minutes": cooldown_remaining,
            "recommended_tools": list(action.get("recommended_tools", [])),
            "satisfaction": str(action.get("satisfaction", "")),
            "hunger": str(action.get("hunger", "")),
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
