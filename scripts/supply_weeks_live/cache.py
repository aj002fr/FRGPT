"""In-process week cache. Refreshes only the current supply week when 1m flow moves."""

from __future__ import annotations

import logging
import threading
from typing import Any

import psycopg2

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER
from scripts.aggression_flow_history_dash.cache import latest_bar_ts
from scripts.supply_weeks_live.compare import (
    all_auction_dates,
    append_live_event_week,
    build_compare_rows,
    modal_clocks,
    refresh_today_rows,
    summarize_compare,
    supply_events,
)
from scripts.supply_weeks_live.flow import fold_week_flow, summarize_windows
from scripts.supply_weeks_live.weeks import (
    attach_price,
    current_window_name,
    is_current_week,
    list_matched_weeks,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_READY_EVENT = threading.Event()
_WEEKS: list[dict[str, Any]] = []
_COMPARE_ROWS: list[dict[str, Any]] = []
_COMPARE_MODAL: dict[str, Any] = {}
_AUCTION_DATES: set | None = None
_MAX_TS: str | None = None
_READY = False
_BUILDING = False


def _pg():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def _enrich(week: dict[str, Any], conn=None) -> dict[str, Any]:
    info = week["week_info"]
    if conn is not None:
        try:
            attach_price(week, conn)
        except Exception:
            logger.exception("price attach failed for %s", week.get("week_key"))
            week["price_data"] = {"hours": [], "closes": []}
    elif "price_data" not in week:
        week["price_data"] = {"hours": [], "closes": []}
    week["flow"] = fold_week_flow(info, price_data=week.get("price_data"))
    week["is_current"] = is_current_week(info)
    week["current_window"] = current_window_name(info) if week["is_current"] else None
    return week


def _snapshot() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_WEEKS)


def _set_weeks(weeks: list[dict[str, Any]], max_ts: str | None) -> None:
    global _WEEKS, _MAX_TS, _READY
    with _LOCK:
        _WEEKS = weeks
        _MAX_TS = max_ts
        _READY = True
    _READY_EVENT.set()


def _set_compare(rows: list[dict[str, Any]], modal: dict[str, Any]) -> None:
    global _COMPARE_ROWS, _COMPARE_MODAL
    with _LOCK:
        _COMPARE_ROWS = rows
        _COMPARE_MODAL = modal


def _snapshot_compare() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with _LOCK:
        return list(_COMPARE_ROWS), dict(_COMPARE_MODAL)


def _auction_date_set():
    global _AUCTION_DATES
    with _LOCK:
        if _AUCTION_DATES is None:
            _AUCTION_DATES = all_auction_dates()
        return set(_AUCTION_DATES)


def _rebuild_compare(weeks: list[dict[str, Any]]) -> None:
    events = supply_events(weeks)
    modal = modal_clocks(events)
    rows = build_compare_rows(weeks, auction_dates=_auction_date_set(), modal=modal)
    _set_compare(rows, modal)


def _refresh_compare_today(weeks: list[dict[str, Any]]) -> None:
    rows, modal = _snapshot_compare()
    if not modal:
        modal = modal_clocks(supply_events(weeks))
    updated = refresh_today_rows(rows, weeks, auction_dates=_auction_date_set(), modal=modal)
    _set_compare(updated, modal)


def _claim_full_build(force: bool) -> str:
    global _BUILDING
    with _LOCK:
        if force or not _READY:
            if _BUILDING and not force:
                return "wait"
            _BUILDING = True
            return "build"
        return "ready"


def _finish_full_build() -> None:
    global _BUILDING
    with _LOCK:
        _BUILDING = False


def _current_needs_refresh(max_ts: str | None) -> str | None:
    with _LOCK:
        if not _READY:
            return None
        if max_ts == _MAX_TS:
            return None
        current = next((week for week in _WEEKS if week.get("is_current")), None)
        return None if current is None else str(current["week_key"])


def _full_build(max_ts: str | None) -> list[dict[str, Any]]:
    weeks = append_live_event_week(list_matched_weeks())
    conn = None
    try:
        conn = _pg()
    except Exception:
        logger.exception("postgres unavailable; price paths empty")
    try:
        for week in weeks:
            _enrich(week, conn)
            logger.info("loaded %s", week.get("week_key"))
    finally:
        if conn is not None:
            conn.close()
    _rebuild_compare(weeks)
    _set_weeks(weeks, max_ts)
    return weeks


def build_payload(
    *,
    month_end: str = "all",
    drop_tier1: bool = False,
    drop_month_end: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    max_ts = latest_bar_ts("TY")
    claim = _claim_full_build(force)
    if claim == "wait":
        _READY_EVENT.wait(timeout=600)
        weeks = _snapshot()
    elif claim == "build":
        try:
            weeks = _full_build(max_ts)
        finally:
            _finish_full_build()
            _READY_EVENT.set()
    else:
        key = _current_needs_refresh(max_ts)
        if key is not None:
            weeks = _snapshot()
            conn = None
            try:
                conn = _pg()
            except Exception:
                logger.exception("postgres unavailable on live refresh")
            try:
                for week in weeks:
                    if week["week_key"] == key:
                        _enrich(week, conn)
            finally:
                if conn is not None:
                    conn.close()
            _refresh_compare_today(weeks)
            _set_weeks(weeks, max_ts)
        else:
            weeks = _snapshot()
            for week in weeks:
                week["is_current"] = is_current_week(week["week_info"])
                week["current_window"] = current_window_name(week["week_info"]) if week["is_current"] else None
            with _LOCK:
                stale = max_ts != _MAX_TS
            if stale:
                _refresh_compare_today(weeks)
                _set_weeks(weeks, max_ts)

    current = next((week for week in weeks if week.get("is_current")), None)
    compare_rows, compare_modal = _snapshot_compare()
    return {
        "weeks": weeks,
        "windows": summarize_windows(weeks, month_end=month_end),
        "compare": summarize_compare(
            compare_rows,
            month_end=month_end,
            drop_tier1=drop_tier1,
            drop_month_end=drop_month_end,
            modal=compare_modal,
            weeks=weeks,
        ),
        "current": None
        if current is None
        else {
            "week_key": current["week_key"],
            "week_info": current["week_info"],
            "current_window": current.get("current_window"),
            "flow": current.get("flow"),
            "price_data": current.get("price_data") or {"hours": [], "closes": []},
        },
        "last_bar_ts": max_ts,
    }


def clear_cache() -> None:
    global _WEEKS, _COMPARE_ROWS, _COMPARE_MODAL, _AUCTION_DATES, _MAX_TS, _READY, _BUILDING
    with _LOCK:
        _WEEKS = []
        _COMPARE_ROWS = []
        _COMPARE_MODAL = {}
        _AUCTION_DATES = None
        _MAX_TS = None
        _READY = False
        _BUILDING = False
    _READY_EVENT.clear()
