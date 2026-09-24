"""E16 time-of-day / scheduled-event / roll regime tags (causal at event start)."""

from __future__ import annotations

import sqlite3
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, PROJECT_ROOT
from scripts.move_exhaustion.simulation.policies import _ts

EASTERN = ZoneInfo("America/New_York")
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"

OPENING_START = time(1, 0)
OPENING_END = time(3, 0)
LATE_START = time(15, 0)
LATE_END = time(17, 0)

MACRO_PRE_MIN = 15
MACRO_POST_MIN = 60
AUCTION_PRE_MIN = 30
AUCTION_POST_MIN = 90

# US coupon / TIPS auctions relevant to the TY complex.
AUCTION_NAME_EXACT = {
    "2-Year Note Auction",
    "3-Year Note Auction",
    "5-Year Note Auction",
    "7-Year Note Auction",
    "10-Year Note Auction",
    "20-Year Bond Auction",
    "30-Year Bond Auction",
    "10-Year TIPS Auction",
    "30-Year TIPS Auction",
    "5-Year TIPS Auction",
    "2-Year FRN Auction",
}

# Mutually exclusive primary label priority (first match wins).
PRIMARY_PRIORITY = (
    "scheduled_macro",
    "auction_window",
    "opening_period",
    "late_session",
    "roll_period",
    "high_volatility",
    "ordinary",
)


def load_us_high_importance_events(path: Path = EVENTS_DB) -> pd.DataFrame:
    """Read-only high-importance US releases (macro windows)."""
    if not path.exists():
        return pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    # Plain connect: UNC workspace paths break sqlite URI authority parsing.
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA query_only = ON")
        ev = pd.read_sql_query(
            """
            SELECT event_date, event_name, country, importance
            FROM economic_events
            WHERE (country LIKE '%United States%' OR country = 'US')
              AND lower(coalesce(importance, '')) = 'high'
            """,
            conn,
        )
    if ev.empty:
        return pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    ts = pd.to_datetime(ev["event_date"], errors="coerce")
    ev = ev.assign(event_ts=ts).dropna(subset=["event_ts"]).copy()
    ev["event_ts"] = ev["event_ts"].apply(
        lambda t: t.tz_localize(EASTERN) if t.tzinfo is None else t.tz_convert(EASTERN)
    )
    ev["event_ts_utc"] = ev["event_ts"].dt.tz_convert("UTC")
    ev["et_date"] = ev["event_ts"].dt.strftime("%Y-%m-%d")
    # Collapse identical timestamps (multi-component releases).
    return (
        ev.groupby("event_ts_utc", as_index=False)
        .agg(
            event_name=("event_name", lambda s: " | ".join(sorted({str(v) for v in s})[:6])),
            et_date=("et_date", "first"),
        )
        .sort_values("event_ts_utc")
        .reset_index(drop=True)
    )


def load_us_auction_events(path: Path = EVENTS_DB) -> pd.DataFrame:
    """Read-only US coupon/TIPS auction timestamps."""
    if not path.exists():
        return pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA query_only = ON")
        ev = pd.read_sql_query(
            """
            SELECT event_date, event_name, country
            FROM economic_events
            WHERE (country LIKE '%United States%' OR country = 'US')
              AND event_name IS NOT NULL
            """,
            conn,
        )
    if ev.empty:
        return pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    ev = ev[ev["event_name"].isin(AUCTION_NAME_EXACT)].copy()
    if ev.empty:
        return pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    ts = pd.to_datetime(ev["event_date"], errors="coerce")
    ev = ev.assign(event_ts=ts).dropna(subset=["event_ts"]).copy()
    ev["event_ts"] = ev["event_ts"].apply(
        lambda t: t.tz_localize(EASTERN) if t.tzinfo is None else t.tz_convert(EASTERN)
    )
    ev["event_ts_utc"] = ev["event_ts"].dt.tz_convert("UTC")
    ev["et_date"] = ev["event_ts"].dt.strftime("%Y-%m-%d")
    return ev.sort_values("event_ts_utc").reset_index(drop=True)


def is_calendar_roll_period(et_date: str) -> bool:
    """Calendar proxy for TY quarterly roll (early Mar/Jun/Sep/Dec).

    No front-month symbol on the frozen TBBO panel; this is a documented proxy,
    not a volume-confirmed roll flag.
    """
    d = pd.Timestamp(et_date)
    return bool(d.month in (3, 6, 9, 12) and 1 <= d.day <= 12)


def _in_window(
    ts_utc: pd.Timestamp,
    anchors: pd.Series,
    pre_min: int,
    post_min: int,
) -> bool:
    if anchors is None or len(anchors) == 0:
        return False
    # Restrict to same calendar day ±1 for speed
    day0 = (ts_utc - pd.Timedelta(days=1)).floor("D")
    day1 = (ts_utc + pd.Timedelta(days=1)).ceil("D")
    cand = anchors[(anchors >= day0) & (anchors <= day1)]
    if cand.empty:
        return False
    pre = pd.Timedelta(minutes=pre_min)
    post = pd.Timedelta(minutes=post_min)
    return bool(((cand >= ts_utc - pre) & (cand <= ts_utc + post)).any())


def session_regime_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    macro_events: pd.DataFrame,
    auction_events: pd.DataFrame,
    high_vol_threshold: float,
) -> dict[str, Any]:
    """Causal regime tags and base predictors at event start."""
    start = _ts(event["event_start_time"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start]
    empty = {
        "et_time": None,
        "minutes_from_session_open": None,
        "is_opening_period": False,
        "is_late_session": False,
        "is_high_volatility": False,
        "is_scheduled_macro": False,
        "is_auction_window": False,
        "is_roll_period": False,
        "is_ordinary": True,
        "primary_regime": "ordinary",
        "abs_residual_z": None,
        "abs_move_z_5m": None,
        "abs_flow_z_5m": None,
        "signed_residual_z": None,
        "abs_move_ticks_5m": None,
        "high_vol_threshold_used": float(high_vol_threshold),
    }
    if hist.empty:
        return empty

    row = hist.iloc[-1]
    et = start.tz_convert(EASTERN)
    et_t = time(int(et.hour), int(et.minute), int(et.second))
    # minutes from 01:00 ET
    open_dt = et.replace(hour=1, minute=0, second=0, microsecond=0)
    mins_open = float((et - open_dt).total_seconds() / 60.0)

    is_opening = OPENING_START <= et_t < OPENING_END
    is_late = LATE_START <= et_t < LATE_END
    is_roll = is_calendar_roll_period(str(event["et_date"]))

    macro_ts = (
        pd.to_datetime(macro_events["event_ts_utc"], utc=True)
        if len(macro_events)
        else pd.Series(dtype="datetime64[ns, UTC]")
    )
    auc_ts = (
        pd.to_datetime(auction_events["event_ts_utc"], utc=True)
        if len(auction_events)
        else pd.Series(dtype="datetime64[ns, UTC]")
    )
    is_macro = _in_window(start, macro_ts, MACRO_PRE_MIN, MACRO_POST_MIN)
    is_auction = _in_window(start, auc_ts, AUCTION_PRE_MIN, AUCTION_POST_MIN)

    move_z = pd.to_numeric(row.get("move_z_5m"), errors="coerce")
    flow_z = pd.to_numeric(row.get("flow_z_5m"), errors="coerce")
    resid_z = pd.to_numeric(row.get("residual_z"), errors="coerce")
    move_ticks = pd.to_numeric(row.get("move_ticks_5m"), errors="coerce")
    abs_move_z = float(abs(move_z)) if pd.notna(move_z) else None
    is_high_vol = bool(abs_move_z is not None and abs_move_z >= high_vol_threshold)

    flags = {
        "scheduled_macro": is_macro,
        "auction_window": is_auction,
        "opening_period": is_opening,
        "late_session": is_late,
        "roll_period": is_roll,
        "high_volatility": is_high_vol,
    }
    primary = "ordinary"
    for name in PRIMARY_PRIORITY:
        if name == "ordinary":
            continue
        if flags.get(name):
            primary = name
            break
    is_ordinary = primary == "ordinary"

    direction = int(event.get("event_direction") or np.sign(move_ticks or 0) or 1)
    signed_rz = float(direction * resid_z) if pd.notna(resid_z) else None

    return {
        "et_time": et.strftime("%H:%M"),
        "minutes_from_session_open": mins_open,
        "is_opening_period": is_opening,
        "is_late_session": is_late,
        "is_high_volatility": is_high_vol,
        "is_scheduled_macro": is_macro,
        "is_auction_window": is_auction,
        "is_roll_period": is_roll,
        "is_ordinary": is_ordinary,
        "primary_regime": primary,
        "abs_residual_z": float(abs(resid_z)) if pd.notna(resid_z) else None,
        "abs_move_z_5m": abs_move_z,
        "abs_flow_z_5m": float(abs(flow_z)) if pd.notna(flow_z) else None,
        "signed_residual_z": signed_rz,
        "abs_move_ticks_5m": float(abs(move_ticks)) if pd.notna(move_ticks) else None,
        "high_vol_threshold_used": float(high_vol_threshold),
    }


def train_high_vol_threshold(abs_move_z: pd.Series, q: float = 0.70) -> float:
    """Train-only quantile for high-volatility flag."""
    x = pd.to_numeric(abs_move_z, errors="coerce").dropna()
    if len(x) < 20:
        return 2.5
    thr = float(x.quantile(q))
    return thr if np.isfinite(thr) and thr > EPS else 2.5
