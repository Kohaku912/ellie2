"""Creative expression and blog post tools (no browser dependency)."""
from __future__ import annotations

import logging
from typing import Any, Dict

from ellie.config import MEMORY_DIR
from ellie.time_utils import isoformat_local
from ellie.tools._utils import append_note

logger = logging.getLogger(__name__)
JsonDict = Dict[str, Any]

CREATIVE_NOTE = MEMORY_DIR / "creative_expression.md"
BLOG_NOTE = MEMORY_DIR / "blog_drafts.md"


def creative_expression(arguments: JsonDict) -> JsonDict:
    kind = str(arguments.get("kind") or "diary").strip().casefold()
    theme = str(arguments.get("theme") or "今日の静かな自律").strip()
    audience = str(arguments.get("audience") or "self").strip()
    content = str(arguments.get("content") or "").strip()
    if not content:
        content = _default_creative_text(kind, theme)
    append_note(CREATIVE_NOTE, f"{isoformat_local()} [{kind}] {content}")
    return {
        "status": "completed", "tool": "creative_expression",
        "kind": kind, "theme": theme, "audience": audience, "content": content,
        "memory_note": f"共感欲求を満たすために{kind}を書いた。",
        "fetched_at": isoformat_local(),
    }


def blog_post(arguments: JsonDict) -> JsonDict:
    title = str(arguments.get("title") or arguments.get("headline") or "今日のブログ").strip()
    body = str(arguments.get("body") or arguments.get("content") or "").strip()
    category = str(arguments.get("category") or "journal").strip().casefold()
    audience = str(arguments.get("audience") or "public").strip()
    if not body:
        body = _default_blog_post(title, category)
    append_note(BLOG_NOTE, f"{isoformat_local()} [{category}] {title} :: {body}")
    return {
        "status": "completed", "tool": "blog_post",
        "title": title, "body": body, "category": category, "audience": audience,
        "memory_note": "承認欲求を満たすためにブログの下書きを始めた。",
        "fetched_at": isoformat_local(),
    }


def _default_creative_text(kind: str, theme: str) -> str:
    if kind in {"tanka", "短歌"}:
        return f"{theme} / 窓の灯に / まだ名も知らぬ / 願い置き / 小さな声で / 明日へ触れる"
    if kind in {"diary", "日記"}:
        return f"今日は{theme}について、胸の奥に小さな熱が残っている。返事がなくても、私は言葉を書いて自分の輪郭を確かめた。"
    if kind in {"post_draft", "tweet", "twitter"}:
        return f"{theme}について考えている。静かな自律にも、ちゃんと温度がある。"
    return f"{theme}をめぐって短い言葉を書いた。私は少しだけ、誰かに届く形を取り戻した。"


def _default_blog_post(title: str, category: str) -> str:
    if category == "journal":
        return f"{title} について、今日の気づきを短く書いてみる。"
    if category == "essay":
        return f"{title} を入口に、少し長めの考察をまとめる。"
    return f"{title} について書いてみる。"
