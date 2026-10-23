"""Trading-session semantics for Move Exhaustion V2 X0.

Convention (frozen in ``config/frozen/data_store.yaml``, PROGRAM.md §1.2):

    trading date = prior day 18:00 ET -> trading date 17:00 ET

``session_bounds`` returns nominal timezone-aware UTC bounds.
``expected_session`` uses pandas-market-calendars' product-specific
``CME_InterestRate`` schedule, including year-specific full closures and
early closes. QC gate Q3 uses the actual scheduled open and close.

This module is pure (no I/O, no archive access) and is the single source the
rest of the X0 data core and QC use for session arithmetic.
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

EASTERN = ZoneInfo("America/New_York")

SESSION_START_ET = dt_time(hour=18)  # prior day
SESSION_END_ET = dt_time(hour=17)    # trading date
KIND_FULL = "full"
KIND_PARTIAL = "partial"
KIND_CLOSED = "closed"

CME_CALENDAR_NAME = "CME_InterestRate"
_CME_CALENDAR = mcal.get_calendar(CME_CALENDAR_NAME)


def _as_date(value: date | str | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def session_bounds(trading_date: date | str | pd.Timestamp) -> Tuple[datetime, datetime]:
    """UTC ``(start, end)`` of the 18:00 ET -> 17:00 ET session."""
    trading = _as_date(trading_date)
    start_et = datetime.combine(trading - timedelta(days=1), SESSION_START_ET, tzinfo=EASTERN)
    end_et = datetime.combine(trading, SESSION_END_ET, tzinfo=EASTERN)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def expected_session(
    trading_date: date | str | pd.Timestamp,
) -> Dict[str, object]:
    """Product-specific CME Interest Rate schedule for one trading date."""
    trading = _as_date(trading_date)
    nominal_start, nominal_end = session_bounds(trading)
    base: Dict[str, object] = {
        "trading_date": trading.isoformat(),
        "start_utc": nominal_start,
        "end_utc": nominal_end,
        "calendar": CME_CALENDAR_NAME,
    }
    schedule = _CME_CALENDAR.schedule(
        start_date=trading.isoformat(),
        end_date=trading.isoformat(),
    )
    if schedule.empty:
        reason = "weekend" if trading.weekday() >= 5 else "cme_calendar_closed"
        return {
            **base,
            "kind": KIND_CLOSED,
            "reason": reason,
            "expected_last_trade_utc": None,
        }
    actual_start = schedule.iloc[0]["market_open"].to_pydatetime()
    actual_end = schedule.iloc[0]["market_close"].to_pydatetime()
    partial = actual_start != nominal_start or actual_end != nominal_end
    return {
        **base,
        "start_utc": actual_start,
        "end_utc": actual_end,
        "kind": KIND_PARTIAL if partial else KIND_FULL,
        "reason": "cme_calendar_modified_hours" if partial else None,
        "expected_last_trade_utc": actual_end,
    }


def _date_range(start: date, end: date) -> Iterable[date]:
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def expected_sessions(
    start: date | str, end: date | str
) -> List[Dict[str, object]]:
    """Expected session schedule for every calendar day in [start, end]."""
    return [
        expected_session(day)
        for day in _date_range(_as_date(start), _as_date(end))
    ]


def expected_open_sessions(
    start: date | str, end: date | str
) -> List[Dict[str, object]]:
    """Sessions expected to trade (full or partial) in [start, end]."""
    return [s for s in expected_sessions(start, end) if s["kind"] != KIND_CLOSED]


def expected_open_dates(year: int) -> List[str]:
    """ISO dates of expected open sessions (full or partial) in a year."""
    return [
        str(s["trading_date"])
        for s in expected_open_sessions(date(year, 1, 1), date(year, 12, 31))
    ]


def is_open_session(trading_date: date | str) -> bool:
    """True when the trading date is an expected full or partial session."""
    return expected_session(trading_date)["kind"] != KIND_CLOSED
