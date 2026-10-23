"""Scheduled US release loading for the isolated V2.1 paper tracker."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
EASTERN = ZoneInfo("America/New_York")
MAX_DB_AGE_HOURS = 30.0
REFRESH_SECONDS = 300.0


def release_id(event_ts_utc: pd.Timestamp) -> str:
    text = pd.Timestamp(event_ts_utc).tz_convert("UTC").isoformat()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_events(db_path: Path = EVENTS_DB) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not db_path.exists():
        return pd.DataFrame(), {"status": "missing", "path": str(db_path), "age_hours": None}
    age_hours = (datetime.now(timezone.utc).timestamp() - db_path.stat().st_mtime) / 3600.0
    status = "stale" if age_hours > MAX_DB_AGE_HOURS else "ok"
    try:
        with sqlite3.connect(str(db_path)) as conn:
            events = pd.read_sql_query(
                """
                SELECT event_date, event_name, country
                FROM economic_events
                WHERE country LIKE '%United States%' OR country='US' OR country IS NULL
                """,
                conn,
            )
    except Exception as exc:
        return pd.DataFrame(), {"status": "error", "path": str(db_path), "age_hours": age_hours, "error": str(exc)}
    events["event_ts"] = pd.to_datetime(events["event_date"], errors="coerce")
    events = events.dropna(subset=["event_ts"]).copy()
    events["event_ts"] = events["event_ts"].apply(
        lambda ts: ts.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT")
        if ts.tzinfo is None
        else ts.tz_convert(EASTERN)
    )
    events = events.dropna(subset=["event_ts"]).copy()
    events["event_ts_utc"] = events["event_ts"].dt.tz_convert("UTC")
    grouped = (
        events.groupby("event_ts_utc", as_index=False)
        .agg(
            event_ts_et=("event_ts", "first"),
            event_name=("event_name", lambda values: " | ".join(sorted({str(v) for v in values if pd.notna(v)})[:8])),
            component_count=("event_name", "count"),
        )
        .sort_values("event_ts_utc")
    )
    grouped["release_id"] = grouped["event_ts_utc"].map(release_id)
    grouped["et_date"] = grouped["event_ts_et"].dt.strftime("%Y-%m-%d")
    return grouped, {"status": status, "path": str(db_path), "age_hours": age_hours, "rows": int(len(grouped))}


class EventSchedule:
    """Thread-safe, periodically refreshed read-only event calendar."""

    def __init__(self, db_path: Path = EVENTS_DB) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._events = pd.DataFrame()
        self._status: dict[str, Any] = {"status": "not_loaded", "path": str(db_path)}
        self._loaded_at: datetime | None = None

    def refresh(self, force: bool = False) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            if not force and self._loaded_at and (now - self._loaded_at).total_seconds() < REFRESH_SECONDS:
                return
        events, status = load_events(self.db_path)
        with self._lock:
            if not events.empty or self._events.empty:
                self._events = events
            self._status = status
            self._loaded_at = now

    def snapshot(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        self.refresh()
        with self._lock:
            return self._events.copy(), dict(self._status)


__all__ = ["EVENTS_DB", "EventSchedule", "load_events", "release_id"]
