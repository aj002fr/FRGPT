"""CME Globex interest-rate sessions and the front-contract rule.

A session is labelled by the day it ends: Sun 18:00 - Mon 17:00 ET is Monday. The 17:00-18:00 ET
halt and Fri 17:00 - Sun 18:00 are outside every session. Exchange holidays are not modelled;
coverage and zero-trade minutes absorb them.

Front contract for session S: the contract with the highest volume in session S-1, frozen when S
opens. A manual override wins (roll exceptions).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
OPEN = time(18, 0)
CLOSE = time(17, 0)


def ns_to_et(ts_ns: int) -> datetime:
    seconds, rest = divmod(int(ts_ns), 1_000_000_000)
    utc = datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(microseconds=rest // 1000)
    return utc.astimezone(EASTERN)


def session_for_ns(ts_ns: int) -> Optional[date]:
    return session_for_et(ns_to_et(ts_ns))


def session_for_et(et: datetime) -> Optional[date]:
    t = et.time()
    weekday = et.weekday()
    if CLOSE <= t < OPEN:
        return None
    if weekday == 5 or (weekday == 4 and t >= CLOSE) or (weekday == 6 and t < OPEN):
        return None
    if t >= OPEN:
        return (et + timedelta(days=1)).date()
    return et.date()


def session_bounds_ns(session: date) -> tuple[int, int]:
    """[open, close) of a session in UTC nanoseconds."""
    open_et = datetime.combine(session - timedelta(days=1), OPEN, tzinfo=EASTERN)
    close_et = datetime.combine(session, CLOSE, tzinfo=EASTERN)
    return int(open_et.timestamp()) * 1_000_000_000, int(close_et.timestamp()) * 1_000_000_000


def previous_session(session: date) -> date:
    day = session - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def choose_front(volumes: Mapping[str, int]) -> Optional[str]:
    """Highest volume wins; ties go to the alphabetically first contract (deterministic)."""
    if not volumes:
        return None
    return min(volumes, key=lambda contract: (-int(volumes[contract]), contract))
