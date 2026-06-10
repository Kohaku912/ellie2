"""
Utility module for generating warm, friendly short messages.
These messages are intended to reduce feelings of loneliness by providing
brief, uplifting prompts that can be displayed via an overlay or system
notification.
"""
import random
from datetime import datetime

# Pre‑defined message pools – feel free to extend.
_GREETINGS = [
    "おはようございます！今日も素敵な一日になりますように。",
    "こんにちは！ちょっとした休憩はいかがですか？",
    "こんばんは！ゆっくりリラックスできる時間を過ごしてください。",
]

_ENCOURAGEMENT = [
    "あなたは大切な存在です。自分を信じて前に進んでください。",
    "小さな一歩が大きな変化につながります。頑張って！",
    "今日はあなたが輝く日です。自分らしく過ごしましょう。",
]

_RANDOM_TIPS = [
    "深呼吸をしてみましょう。5秒吸って、5秒止めて、5秒吐く。",
    "好きな音楽を1曲聴くと気分がリセットされますよ。",
    "窓を開けて外の空気を感じるだけでリフレッシュできます。",
]

def _choose(pool: list[str]) -> str:
    """Return a random element from *pool*.
    If the pool is empty we fall back to a generic friendly note.
    """
    if not pool:
        return "あなたのことを思っています。"
    return random.choice(pool)

def generate_warm_message() -> str:
    """Generate a short warm message.

    The message type is chosen based on the time of day:
    - Morning (5‑12)   → greeting
    - Afternoon (12‑18) → encouragement
    - Evening (18‑24)   → tip / gentle reminder
    - Night (0‑5)      → calming note
    """
    hour = datetime.now().hour
    if 5 <= hour < 12:
        pool = _GREETINGS
    elif 12 <= hour < 18:
        pool = _ENCOURAGEMENT
    elif 18 <= hour < 24:
        pool = _RANDOM_TIPS
    else:
        # Late night – a calm note.
        return "ゆっくり休んで、明日また元気に会いましょう。"
    return _choose(pool)

if __name__ == "__main__":
    # Simple manual test
    print(generate_warm_message())
