"""SQLite log for V1 banner changes and V2.7 entry/exit alerts.

Writes only under workspace/analysis/aggression_v1_v27_banner_alerts/.
Fail-closed: callers must treat every public function as best-effort.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_v27_banner_alerts"
DB_PATH = DB_DIR / "banner_alerts.sqlite"
EASTERN = "America/New_York"

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS v1_banner_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    ts_et TEXT NOT NULL,
    as_of TEXT,
    state TEXT NOT NULL,
    prev_state TEXT,
    status TEXT,
    research_state TEXT,
    aggress_z REAL,
    aggress_trend TEXT,
    passive_z REAL,
    passive_trend TEXT,
    minutes_in_state INTEGER,
    shifted INTEGER,
    from_state TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_v1_banner_ts ON v1_banner_changes(ts_utc);

CREATE TABLE IF NOT EXISTS v27_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    ts_et TEXT NOT NULL,
    kind TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    direction TEXT,
    watch_state TEXT,
    exit_reason TEXT,
    open_price REAL,
    close_price REAL,
    open_residual_z REAL,
    session_ticks REAL,
    dir_pnl_trade_ticks REAL,
    minutes_held REAL,
    v1_state_at_alert TEXT,
    v1_banner_id INTEGER,
    FOREIGN KEY (v1_banner_id) REFERENCES v1_banner_changes(id)
);
CREATE INDEX IF NOT EXISTS idx_v27_alert_ts ON v27_alert_events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_v27_alert_episode ON v27_alert_events(episode_id);
"""


def _as_utc(ts: Any) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.to_pydatetime()


def _clocks(ts: Any) -> tuple[str, str]:
    utc = _as_utc(ts)
    aware = pd.Timestamp(utc)
    return aware.isoformat(), aware.tz_convert(EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def latest_v1_banner(as_of: Any = None, *, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """Latest V1 banner change at or before as_of (or the newest row)."""
    try:
        with _LOCK:
            conn = _connect(db_path)
            try:
                if as_of is None:
                    row = conn.execute(
                        "SELECT * FROM v1_banner_changes ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                else:
                    ts_utc, _ = _clocks(as_of)
                    row = conn.execute(
                        "SELECT * FROM v1_banner_changes WHERE ts_utc <= ? ORDER BY id DESC LIMIT 1",
                        (ts_utc,),
                    ).fetchone()
            finally:
                conn.close()
        return dict(row) if row is not None else None
    except Exception:
        return None


def record_v1_banner_change(context: dict[str, Any] | None, *, db_path: Path = DB_PATH) -> int | None:
    """Append a row only when the live V1 banner state changes."""
    if not context:
        return None
    state = str(context.get("state") or context.get("status") or "").strip()
    if not state:
        return None
    as_of = context.get("as_of")
    ts_utc, ts_et = _clocks(as_of)
    aggress = context.get("aggress") or {}
    passive = context.get("implied_passive") or {}
    transition = context.get("transition") or {}
    try:
        with _LOCK:
            conn = _connect(db_path)
            try:
                last = conn.execute(
                    "SELECT id, state FROM v1_banner_changes ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if last is not None and str(last["state"]) == state:
                    return None
                prev = None if last is None else str(last["state"])
                cur = conn.execute(
                    """
                    INSERT INTO v1_banner_changes (
                        ts_utc, ts_et, as_of, state, prev_state, status, research_state,
                        aggress_z, aggress_trend, passive_z, passive_trend,
                        minutes_in_state, shifted, from_state, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts_utc,
                        ts_et,
                        None if as_of is None else str(as_of),
                        state,
                        prev,
                        context.get("status"),
                        context.get("research_state"),
                        aggress.get("ticks", aggress.get("z")),
                        aggress.get("trend"),
                        passive.get("ticks", passive.get("z")),
                        passive.get("trend"),
                        transition.get("minutes_in_state"),
                        1 if transition.get("shifted") else 0,
                        transition.get("from_state"),
                        context.get("reason"),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()
    except Exception:
        return None


def record_v27_alert(
    episode: dict[str, Any] | None,
    kind: str,
    *,
    ts: Any = None,
    db_path: Path = DB_PATH,
) -> int | None:
    """Append a V2.7 ENTRY or EXIT and stamp the latest V1 banner as of that time."""
    if not episode:
        return None
    kind = str(kind or "").upper()
    if kind not in {"ENTRY", "EXIT"}:
        return None
    when = ts
    if when is None:
        when = episode.get("closed_ts") if kind == "EXIT" else episode.get("opened_ts")
    ts_utc, ts_et = _clocks(when)
    banner = latest_v1_banner(when, db_path=db_path)
    try:
        with _LOCK:
            conn = _connect(db_path)
            try:
                existing = conn.execute(
                    "SELECT id FROM v27_alert_events WHERE episode_id = ? AND kind = ?",
                    (str(episode.get("id") or ""), kind),
                ).fetchone()
                if existing is not None:
                    return int(existing["id"])
                cur = conn.execute(
                    """
                    INSERT INTO v27_alert_events (
                        ts_utc, ts_et, kind, episode_id, direction, watch_state, exit_reason,
                        open_price, close_price, open_residual_z, session_ticks,
                        dir_pnl_trade_ticks, minutes_held, v1_state_at_alert, v1_banner_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts_utc,
                        ts_et,
                        kind,
                        str(episode.get("id") or ""),
                        episode.get("direction"),
                        episode.get("state"),
                        episode.get("exit_reason") if kind == "EXIT" else None,
                        episode.get("open_price"),
                        episode.get("close_price") if kind == "EXIT" else None,
                        episode.get("open_residual_z"),
                        episode.get("session_ticks"),
                        episode.get("dir_pnl_trade_ticks") if kind == "EXIT" else None,
                        episode.get("minutes_held") if kind == "EXIT" else None,
                        None if banner is None else banner.get("state"),
                        None if banner is None else banner.get("id"),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()
    except Exception:
        return None
