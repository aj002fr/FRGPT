"""In-process daily-row cache for the 5064 viewer. Does not write the history store."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from datetime import date

from scripts.aggression_dashboard.flow_history import DB_PATH
from scripts.aggression_flow_history_dash.daily import aggregate_daily
from scripts.aggression_flow_history_dash.session import current_session_day, next_session_day

_LOCK = threading.Lock()
_DAILY: dict[tuple[Any, ...], tuple[str | None, list[dict[str, Any]]]] = {}


def latest_bar_ts(symbol: str = "TY", *, db_path: Path | None = None) -> str | None:
    """Last 1-minute bar timestamp. Uses the (symbol, ts_utc) index. No COUNT(*)."""
    path = Path(db_path) if db_path is not None else DB_PATH
    if not path.exists():
        return None
    # Plain path open: file: URIs treat UNC hosts (\\100.95...) as URL authority.
    try:
        conn = sqlite3.connect(str(path), timeout=30)
    except sqlite3.Error:
        return None
    try:
        conn.execute("PRAGMA query_only=ON")
        wanted = str(symbol or "TY").strip().upper()
        if wanted == "CURVE":
            row = conn.execute("SELECT MAX(ts_utc) FROM v1_aggress_flow_1m").fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(ts_utc) FROM v1_aggress_flow_1m WHERE symbol = ?",
                (wanted,),
            ).fetchone()
        value = None if row is None else row[0]
        return None if value is None else str(value)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


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
