"""Gap-only AccessAPI historical backfill.

Fills only uncovered intervals, clipped to Globex sessions, and never the forming minute or the
last BACKFILL_SETTLE_MINUTES completed minutes. Inserts are set-based, so a repeated fill is a
no-op. Every minute a fill touches is re-sealed from the tape, which also replaces provisional
bars (logged as revisions).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Callable, Optional

import pandas as pd

from scripts.flow_store.aggregate import bars_from_trades, minute_floor_us
from scripts.flow_store.normalize import Trade, normalize_frame
from scripts.flow_store.sessions import choose_front, previous_session, session_bounds_ns, session_for_ns
from scripts.flow_store.settings import (
    ACCESS_API_BASE,
    BACKFILL_SETTLE_MINUTES,
    BAR_SOURCE_CANONICAL,
    BAR_SOURCE_PROVISIONAL,
    MINUTE_US,
    SOURCE_HIST,
)
from scripts.flow_store.store import FlowStore

logger = logging.getLogger(__name__)

Fetch = Callable[[str, int, int], list[Trade]]


def fetch_accessapi_trades(symbol: str, start_us: int, end_us: int, *, api_base: str = ACCESS_API_BASE,
                           timeout: int = 120) -> list[Trade]:
    from scripts.aggression_flow_history_dash.backfill_accessapi import fetch_trades

    start = pd.Timestamp(int(start_us), unit="us", tz="UTC").to_pydatetime()
    end = pd.Timestamp(int(end_us), unit="us", tz="UTC").to_pydatetime()
    return normalize_frame(fetch_trades(symbol, start, end, api_base=api_base, timeout=timeout), source=SOURCE_HIST)


def settle_limit_us(now_us: int) -> int:
    """Backfill may not reach into the forming minute or the last settle minutes."""
    return minute_floor_us(now_us) - BACKFILL_SETTLE_MINUTES * MINUTE_US


def session_segments(start_us: int, end_us: int) -> list[tuple[int, int, str]]:
    """Split [start, end) into the parts that lie inside Globex sessions."""
    out: list[tuple[int, int, str]] = []
    cursor = int(start_us)
    while cursor < end_us:
        session = session_for_ns(cursor * 1000)
        if session is None:
            nxt = cursor + MINUTE_US - cursor % MINUTE_US
            cursor = min(nxt, end_us)
            continue
        _, close_ns = session_bounds_ns(session)
        stop = min(end_us, close_ns // 1000)
        out.append((cursor, stop, session.isoformat()))
        cursor = stop
    return out


def ensure_front(store: FlowStore, session: str, symbol: str, fetch: Fetch) -> Optional[str]:
    """Front contract of `session`: frozen value, else highest volume in the previous session."""
    frozen = store.front_contract(session, symbol)
    if frozen:
        return frozen
    prev = previous_session(date.fromisoformat(session))
    lo_ns, hi_ns = session_bounds_ns(prev)
    lo_us, hi_us = lo_ns // 1000, hi_ns // 1000
    if not store.gaps(symbol, lo_us, hi_us):
        volumes, method = store.contract_volumes(symbol, lo_us, hi_us), "prev_session_volume_tape"
    else:
        volumes: dict[str, int] = {}
        for trade in fetch(symbol, lo_us, hi_us):
            volumes[trade.contract] = volumes.get(trade.contract, 0) + trade.size
        method = "prev_session_volume_accessapi_hist"
    contract = choose_front(volumes)
    if contract is None:
        return None
    return store.freeze_front_contract(session, symbol, contract, method, volumes.get(contract))


def seal_minute(store: FlowStore, symbol: str, minute_us: int, *, front: str, dv01_unit: float, reason: str) -> str:
    trades = store.trades_in_range(symbol, minute_us, minute_us + MINUTE_US, contract=front)
    bar = bars_from_trades(trades, dv01_unit=dv01_unit).get(minute_us)
    result = store.write_bar(symbol, minute_us, bar, source=BAR_SOURCE_CANONICAL, reason=reason)
    store.mark_sealed(symbol, minute_us, BAR_SOURCE_CANONICAL)
    return result


def seal_provisional(store: FlowStore, symbol: str, minute_us: int, *, front: str, dv01_unit: float) -> str:
    """PROVISIONAL bar from Databento backup trades, only for a minute AccessAPI does not cover."""
    if store.minute_covered(symbol, minute_us):
        return "covered"
    trades = store.trades_in_range(symbol, minute_us, minute_us + MINUTE_US, contract=front, table="provisional_tape")
    bar = bars_from_trades(trades, dv01_unit=dv01_unit).get(minute_us)
    if bar is None:
        return "no_trades"
    result = store.write_bar(symbol, minute_us, bar, source=BAR_SOURCE_PROVISIONAL, reason="provisional")
    store.mark_sealed(symbol, minute_us, BAR_SOURCE_PROVISIONAL)
    return result


def fill_gaps(store: FlowStore, symbol: str, start_us: int, now_us: int, *, fetch: Fetch, dv01_unit: float) -> dict:
    """Backfill every uncovered part of [start_us, settle limit) for one symbol."""
    stats = {"symbol": symbol, "segments": 0, "trades_added": 0, "minutes_sealed": 0, "revised": 0}
    end_us = settle_limit_us(now_us)
    if end_us <= start_us:
        return stats
    for gap_lo, gap_hi in store.gaps(symbol, start_us, end_us):
        for lo, hi, session in session_segments(gap_lo, gap_hi):
            front = ensure_front(store, session, symbol, fetch)
            trades = fetch(symbol, lo, hi)
            added = store.insert_trades(trades, mode="set", window=(lo, hi))
            store.add_coverage(symbol, lo, hi, SOURCE_HIST)
            stats["segments"] += 1
            stats["trades_added"] += len(added)
            if front is None:
                continue
            minute = minute_floor_us(lo)
            while minute < hi:
                if store.minute_covered(symbol, minute):
                    result = seal_minute(store, symbol, minute, front=front, dv01_unit=dv01_unit, reason="backfill")
                    stats["minutes_sealed"] += 1
                    stats["revised"] += int(result in ("revised", "deleted"))
                minute += MINUTE_US
    if stats["segments"]:
        logger.info("backfill %s", stats)
    return stats
