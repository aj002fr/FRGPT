"""CME interest-rate session clock. Sunday 18:00 ET to Monday 17:00 ET is Monday."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")
SESSION_OPEN = time(18, 0)
SESSION_CLOSE = time(17, 0)
LIVE_STALE_SECONDS = 180


def to_eastern(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        stamp = value
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=EASTERN)
        return stamp.astimezone(EASTERN)
    raw = str(value).strip()
    if len(raw) >= 19 and raw[4] == "-" and raw[10] == " ":
        try:
            naive = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            return naive.replace(tzinfo=EASTERN)
        except ValueError:
            pass
    parsed = pd.Timestamp(raw)
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(EASTERN)
    return parsed.tz_convert(EASTERN).to_pydatetime()


def market_phase(now: datetime | None = None) -> str:
    """OPEN, HALT (Mon–Thu 17:00–18:00), or CLOSED (Fri 17:00–Sun 18:00)."""
    clock = to_eastern(now) or datetime.now(EASTERN)
    weekday = clock.weekday()
    tod = clock.time()
    if weekday == 5:
        return "CLOSED"
    if weekday == 6:
        return "OPEN" if tod >= SESSION_OPEN else "CLOSED"
    if weekday == 4 and tod >= SESSION_CLOSE:
        return "CLOSED"
    if weekday <= 3 and SESSION_CLOSE <= tod < SESSION_OPEN:
        return "HALT"
    return "OPEN"


def session_day(value: Any) -> date | None:
    """Trading day named for the 17:00 ET close. Closed prints return None."""
    clock = to_eastern(value)
    if clock is None or market_phase(clock) != "OPEN":
        return None
    if clock.time() >= SESSION_OPEN:
        return (clock + timedelta(days=1)).date()
    return clock.date()


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """Half-open [prev 18:00 ET, day 17:00 ET) for a Monday–Friday session date."""
    start = datetime.combine(day - timedelta(days=1), SESSION_OPEN, tzinfo=EASTERN)
    end = datetime.combine(day, SESSION_CLOSE, tzinfo=EASTERN)
    return start, end


def current_session_day(now: datetime | None = None) -> date | None:
    clock = to_eastern(now) or datetime.now(EASTERN)
    day = session_day(clock)
    if day is not None:
        return day
    if market_phase(clock) == "HALT" and clock.weekday() <= 4:
        return clock.date()
    return None


def next_session_day(now: datetime | None = None) -> date:
    clock = to_eastern(now) or datetime.now(EASTERN)
    probe = clock
    for _ in range(96):
        day = session_day(probe)
        if day is not None:
            return day
        probe += timedelta(hours=1)
    return (clock + timedelta(days=1)).date()


PROVISIONAL_SOURCE = "databento_provisional"


def feed_status(last_ts: Any, *, now: datetime | None = None, source: str | None = None) -> dict[str, Any]:
    """LIVE only when the session is open and the last 1m bar is fresh.

    `provisional` is true when the newest bar came from the Databento backup during an
    AccessAPI outage; those minutes are replaced once AccessAPI data arrives.
    """
    clock = to_eastern(now) or datetime.now(EASTERN)
    phase = market_phase(clock)
    session = current_session_day(clock)
    last = to_eastern(last_ts)
    age_sec = None if last is None else max(0.0, (clock - last).total_seconds())
    if phase != "OPEN":
        state = phase
    elif last is None or age_sec is None or age_sec > LIVE_STALE_SECONDS:
        state = "NOT_LIVE"
    else:
        state = "LIVE"
    return {
        "state": state,
        "phase": phase,
        "session_date": None if session is None else session.isoformat(),
        "asof_et": clock.isoformat(sep=" ", timespec="minutes"),
        "last_bar_et": None if last is None else last.isoformat(sep=" ", timespec="minutes"),
        "age_sec": age_sec,
        "frequency": "1-minute completed bars from every trade print",
        "source": source,
        "provisional": source == PROVISIONAL_SOURCE,
    }
