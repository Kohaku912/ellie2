"""Shared timezone-aware helpers for Ellie."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ellie.config import AGENT_TIMEZONE


def agent_tz() -> tzinfo:
    try:
        return ZoneInfo(AGENT_TIMEZONE)
    except ZoneInfoNotFoundError:
        if AGENT_TIMEZONE == "Asia/Tokyo":
            return timezone(timedelta(hours=9), name="JST")
        raise


def now_local() -> datetime:
    return datetime.now(agent_tz())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_local() -> str:
    return now_local().isoformat()


def date_str_local() -> str:
    return now_local().strftime("%Y-%m-%d")


def compact_timestamp_local() -> str:
    return now_local().strftime("%Y%m%d_%H%M%S")


def hour_local() -> int:
    return now_local().hour

