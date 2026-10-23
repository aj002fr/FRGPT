"""In-process daily-row cache for the 5064 viewer. Does not write the history store."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from datetime import date

from scripts.aggression_dashboard.flow_history import DB_PATH, latest_bar
from scripts.aggression_flow_history_dash.daily import aggregate_daily
from scripts.aggression_flow_history_dash.session import current_session_day, feed_status, next_session_day

_LOCK = threading.Lock()
_DAILY: dict[tuple[Any, ...], tuple[str | None, list[dict[str, Any]]]] = {}


def latest_bar_ts(symbol: str = "TY", *, db_path: Path | None = None) -> str | None:
    """Last 1-minute bar timestamp. Uses the (symbol, ts_utc) index. No COUNT(*)."""
    return latest_bar(symbol, db_path=Path(db_path) if db_path is not None else DB_PATH)[0]


def latest_bar_source(symbol: str = "TY", *, db_path: Path | None = None) -> str | None:
    """Source of the newest bar (`databento_provisional` means the feed is provisional)."""
    return latest_bar(symbol, db_path=Path(db_path) if db_path is not None else DB_PATH)[1]


def feed_for(symbol: str = "TY", *, db_path: Path | None = None) -> dict[str, Any]:
    """feed_status of the newest bar, including whether it is PROVISIONAL."""
    ts, source = latest_bar(symbol, db_path=Path(db_path) if db_path is not None else DB_PATH)
    return feed_status(ts, source=source)


def _active_session() -> date:
    return current_session_day() or next_session_day()


def _fold_kwargs(symbol: str, db_path=None, events_db_path=None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"symbol": symbol}
    if db_path is not None:
        kwargs["db_path"] = db_path
    if events_db_path is not None:
        kwargs["events_db_path"] = events_db_path
    return kwargs


def cached_history(
    symbol: str = "TY",
    *,
    db_path=None,
    events_db_path=None,
) -> list[dict[str, Any]]:
    """Closed session days. Rebuild when the Globex session date rolls."""
    session_key = _active_session().isoformat()
    key = (str(symbol).upper(), str(db_path), str(events_db_path))
    with _LOCK:
        hit = _DAILY.get(key)
        if hit is not None and hit[0] == session_key:
            return list(hit[1])
    rows = aggregate_daily(**_fold_kwargs(symbol, db_path, events_db_path))
    hist = [row for row in rows if str(row.get("date") or "") != session_key]
    with _LOCK:
        _DAILY[key] = (session_key, hist)
    return list(hist)


def cached_daily(
    symbol: str = "TY",
    *,
    db_path=None,
    events_db_path=None,
) -> list[dict[str, Any]]:
    """Closed days from cache plus a fresh fold of the current session."""
    session = _active_session()
    hist = cached_history(symbol, db_path=db_path, events_db_path=events_db_path)
    today = aggregate_daily(
        start=session,
        end=session,
        **_fold_kwargs(symbol, db_path, events_db_path),
    )
    return hist + today


def clear_daily_cache() -> None:
    with _LOCK:
        _DAILY.clear()
