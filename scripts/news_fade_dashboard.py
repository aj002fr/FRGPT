#!/usr/bin/env python3
"""
News & Tweet Fade Dashboard
============================

Builds a standalone event-study dashboard for X macro stream messages:
- measures the initial TY reaction after each headline/tweet
- filters to messages with a meaningful move
- quantifies how much of that move fades by later horizons
- renders distributions, account breakdowns, and click-through price paths

Usage:
    python scripts/news_fade_dashboard.py
    python scripts/news_fade_dashboard.py --start-date 2026-02-09 --move-threshold-bps 2
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from rules.treasury_price_conversion import decimal_to_bond_notation, points_to_bps
from src.servers.distro_analytics.db_reader import get_ohlcv_from_db


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")

DEFAULT_X_MACRO_DB = Path.home() / ".market_data" / "x_macro_stream.db"
DEFAULT_FADE_MINUTES = [30, 60]
DEFAULT_PATH_PRE_MINUTES = 10
FULL_FADE_PCT = 95.0
DEFAULT_TOPIC = "iran_israel_lebanon"
TOPIC_TITLES = {
    "none": "News & Tweet Fade Dashboard",
    DEFAULT_TOPIC: "Iran-US / Israel-Lebanon News Fade",
}
IRAN_ISRAEL_KEYWORDS = [
    "iran",
    "iranian",
    "tehran",
    "irgc",
    "khamenei",
    "ayatollah",
    "hormuz",
    "israel",
    "israeli",
    "idf",
    "netanyahu",
    "jerusalem",
    "lebanon",
    "lebanese",
    "hezbollah",
    "beirut",
    "nasrallah",
    "houthi",
]
IRAN_ISRAEL_STRICT_TERMS = [
    "strike",
    "airstrike",
    "attack",
    "missile",
    "drone",
    "rocket",
    "war",
    "retaliat",
    "killed",
    "kill",
    "ceasefire",
    "truce",
    "bomb",
    "shell",
    "idf",
]


@dataclass(frozen=True)
class DashboardConfig:
    x_macro_db: Path
    symbol: str
    start_date: Optional[str]
    end_date: Optional[str]
    accounts: Optional[List[str]]
    topic: str
    keywords: List[str]
    strict_terms: List[str]
    label: str
    dedupe_seconds: int
    path_pre_mins: int
    initial_mins: int
    lag_mins: int
    move_threshold_bps: float
    fade_mins: List[int]
    max_paths: int
    output: Optional[Path]


def _iso_utc(value: pd.Timestamp) -> str:
    return value.tz_convert("UTC").isoformat()


def _parse_date_arg(value: Optional[str], end_of_day: bool = False) -> Optional[pd.Timestamp]:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        if len(value) <= 10 and end_of_day:
            ts = ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(text: Any, max_chars: int = 420) -> str:
    raw = "" if text is None else str(text)
    cleaned = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "..."


def _normalize_terms(terms: Sequence[str]) -> List[str]:
    return [str(term).strip().lower() for term in terms if str(term).strip()]


def _text_matches_any(text: Any, terms: Sequence[str]) -> bool:
    haystack = str(text or "").lower()
    return any(term in haystack for term in terms)


def _compute_stats(values: Sequence[Optional[float]]) -> Dict[str, Optional[float]]:
    arr = np.array([float(v) for v in values if v is not None and not pd.isna(v)], dtype=float)
    if len(arr) == 0:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p0": None,
            "p1": None,
            "p5": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p95": None,
            "p99": None,
            "p100": None,
            "p_rally": None,
            "p_selloff": None,
            "expected_rally": None,
            "expected_selloff": None,
            "pct_zero": None,
        }

    pct = np.percentile(arr, [0, 1, 5, 25, 50, 75, 95, 99, 100])
    pos = arr[arr > 0]
    neg = arr[arr < 0]
    p_rally = len(pos) / len(arr)
    p_selloff = len(neg) / len(arr)
    expected_rally = p_rally * (pos.mean() if len(pos) else 0.0)
    expected_selloff = p_selloff * (neg.mean() if len(neg) else 0.0)
    below = int(np.sum(arr < 0))
    equal = int(np.sum(arr == 0))
    return {
        "count": int(len(arr)),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p0": float(pct[0]),
        "p1": float(pct[1]),
        "p5": float(pct[2]),
        "p25": float(pct[3]),
        "p50": float(pct[4]),
        "p75": float(pct[5]),
        "p95": float(pct[6]),
        "p99": float(pct[7]),
        "p100": float(pct[8]),
        "p_rally": float(p_rally),
        "p_selloff": float(p_selloff),
        "expected_rally": float(expected_rally),
        "expected_selloff": float(expected_selloff),
        "pct_zero": float((below + 0.5 * equal) / len(arr) * 100.0),
    }


def _median(values: Iterable[Optional[float]]) -> Optional[float]:
    arr = [float(v) for v in values if v is not None and not pd.isna(v)]
    if not arr:
        return None
    return float(np.median(np.array(arr, dtype=float)))


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    arr = [float(v) for v in values if v is not None and not pd.isna(v)]
    if not arr:
        return None
    return float(np.mean(np.array(arr, dtype=float)))


def _rate(count: int, total: int) -> Optional[float]:
    return float(count / total * 100.0) if total else None


def _fade_severity_for_events(events: List[Dict[str, Any]], fade_mins: Sequence[int]) -> Dict[str, Dict[str, Any]]:
    reactions = [event for event in events if event.get("is_reaction")]
    severity: Dict[str, Dict[str, Any]] = {}
    for minutes in fade_mins:
        eligible = [
            event
            for event in reactions
            if event.get(f"fade_{minutes}m_pct") is not None
        ]
        total = len(eligible)
        ge_50 = sum(1 for event in eligible if event.get(f"fade_ge_50_{minutes}m"))
        ge_80 = sum(1 for event in eligible if event.get(f"fade_ge_80_{minutes}m"))
        full = sum(1 for event in eligible if event.get(f"fade_full_{minutes}m"))
        severity[f"{minutes}m"] = {
            "reaction_count": total,
            "ge_50_count": ge_50,
            "ge_50_pct": _rate(ge_50, total),
            "ge_80_count": ge_80,
            "ge_80_pct": _rate(ge_80, total),
            "full_count": full,
            "full_pct": _rate(full, total),
            "full_threshold_pct": FULL_FADE_PCT,
        }
    return severity


def load_news_events(config: DashboardConfig) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """Load X macro stream messages from tweet_labels."""
    db_path = config.x_macro_db
    if not db_path.exists():
        raise FileNotFoundError(f"X macro SQLite DB not found: {db_path}")

    start_ts = _parse_date_arg(config.start_date)
    end_ts = _parse_date_arg(config.end_date, end_of_day=True)
    params: List[Any] = []
    where = ["created_at IS NOT NULL"]

    if start_ts is not None:
        where.append("created_at >= ?")
        params.append(start_ts.isoformat())
    if end_ts is not None:
        where.append("created_at <= ?")
        params.append(end_ts.isoformat())
    if config.label != "all":
        if config.label == "pending":
            where.append("label IS NULL")
        else:
            where.append("label = ?")
            params.append(config.label)

    sql = f"""
        SELECT
            tweet_id,
            username,
            text,
            tweet_url,
            label,
            score,
            created_at,
            telegram_message_id,
            forwarded_message_id
        FROM tweet_labels
        WHERE {' AND '.join(where)}
        ORDER BY created_at ASC
    """

    with sqlite3.connect(str(db_path)) as conn:
        df = pd.read_sql_query(sql, conn, params=params)

    raw_count = int(len(df))
    if df.empty:
        return df, {
            "raw_count": raw_count,
            "topic_filtered": 0,
            "strict_count": 0,
            "account_filtered": 0,
            "deduped": 0,
        }

    df["created_at_utc"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["created_at_utc"]).copy()
    df["username"] = df["username"].fillna("unknown").astype(str)
    df["account_key"] = df["username"].str.lower()
    df["text"] = df["text"].fillna("").astype(str)

    topic_filtered = 0
    if config.topic != "none":
        before = len(df)
        keywords = _normalize_terms(config.keywords)
        strict_terms = _normalize_terms(config.strict_terms)
        df["is_topic_match"] = df["text"].map(lambda text: _text_matches_any(text, keywords))
        df["is_strict"] = df["text"].map(lambda text: _text_matches_any(text, strict_terms))
        df = df[df["is_topic_match"]].copy()
        topic_filtered = before - len(df)
    else:
        df["is_topic_match"] = True
        df["is_strict"] = True

    strict_count = int(df["is_strict"].sum()) if not df.empty else 0

    account_filtered = 0
    if config.accounts:
        wanted = {account.lower().lstrip("@") for account in config.accounts}
        before = len(df)
        df = df[df["account_key"].isin(wanted)].copy()
        account_filtered = before - len(df)

    deduped = 0
    if config.dedupe_seconds > 0 and not df.empty:
        keep_indices: List[int] = []
        last_by_account: Dict[str, pd.Timestamp] = {}
        for idx, row in df.sort_values(["account_key", "created_at_utc"]).iterrows():
            key = str(row["account_key"])
            ts = row["created_at_utc"]
            last_ts = last_by_account.get(key)
            if last_ts is not None and (ts - last_ts).total_seconds() <= config.dedupe_seconds:
                deduped += 1
                continue
            keep_indices.append(idx)
            last_by_account[key] = ts
        df = df.loc[keep_indices].sort_values("created_at_utc").reset_index(drop=True)
    else:
        df = df.sort_values("created_at_utc").reset_index(drop=True)

    strict_count = int(df["is_strict"].sum()) if "is_strict" in df.columns and not df.empty else 0
    return df, {
        "raw_count": raw_count,
        "topic_filtered": topic_filtered,
        "strict_count": strict_count,
        "account_filtered": account_filtered,
        "deduped": deduped,
    }


def load_ohlcv_for_events(events_df: pd.DataFrame, config: DashboardConfig) -> pd.DataFrame:
    """Bulk-load OHLCV once for the full event date span."""
    if events_df.empty:
        return pd.DataFrame()

    max_post = max(max(config.fade_mins), config.initial_mins + config.lag_mins)
    start_ts = events_df["created_at_utc"].min() - pd.Timedelta(minutes=config.path_pre_mins + 5)
    end_ts = events_df["created_at_utc"].max() + pd.Timedelta(minutes=max_post + 5)
    symbol_pattern = f"{config.symbol}%"

    logger.info(
        "Loading %s OHLCV from %s to %s",
        symbol_pattern,
        _iso_utc(start_ts),
        _iso_utc(end_ts),
    )
    ohlcv = get_ohlcv_from_db(
        symbol_pattern=symbol_pattern,
        start_date=start_ts.to_pydatetime(),
        end_date=end_ts.to_pydatetime(),
    )
    if ohlcv.empty:
        return ohlcv

    ohlcv = ohlcv.copy()
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)
    ohlcv = ohlcv.sort_index()
    ohlcv = ohlcv[~ohlcv.index.duplicated(keep="last")]
    return ohlcv


def _row_at_or_before(df: pd.DataFrame, ts: pd.Timestamp) -> Optional[pd.Series]:
    if df.empty:
        return None
    pos = df.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    return df.iloc[int(pos)]


def _close_at_or_before(df: pd.DataFrame, ts: pd.Timestamp) -> Optional[float]:
    row = _row_at_or_before(df, ts)
    if row is None:
        return None
    return _safe_float(row.get("Close"))


def _first_timestamp_for_extreme(window: pd.DataFrame, col: str, value: float) -> pd.Timestamp:
    matches = window.index[np.isclose(window[col].astype(float), value)]
    if len(matches) == 0:
        return window.index[0]
    return pd.Timestamp(matches[0])


def compute_event_metrics(
    event: pd.Series,
    ohlcv: pd.DataFrame,
    config: DashboardConfig,
) -> Optional[Dict[str, Any]]:
    """Compute initial reaction and fade metrics for a single event."""
    event_ts = pd.Timestamp(event["created_at_utc"]).tz_convert("UTC")
    path_start = event_ts - pd.Timedelta(minutes=config.path_pre_mins)
    path_end = event_ts + pd.Timedelta(minutes=max(config.fade_mins))
    path_df = ohlcv.loc[(ohlcv.index >= path_start) & (ohlcv.index <= path_end)]
    if len(path_df) < 3:
        return None

    p0 = _close_at_or_before(ohlcv, event_ts)
    if p0 is None or not math.isfinite(p0):
        return None

    initial_start = event_ts - pd.Timedelta(minutes=2)
    initial_end = event_ts + pd.Timedelta(minutes=config.lag_mins + config.initial_mins)
    initial_df = ohlcv.loc[(ohlcv.index >= initial_start) & (ohlcv.index <= initial_end)]
    if len(initial_df) < 2:
        return None

    max_high = float(initial_df["High"].max())
    min_low = float(initial_df["Low"].min())
    up_bps = points_to_bps(max_high - p0)
    down_bps = points_to_bps(min_low - p0)
    if abs(up_bps) >= abs(down_bps):
        peak_bps = float(up_bps)
        peak_price = max_high
        peak_ts = _first_timestamp_for_extreme(initial_df, "High", max_high)
        direction = 1
    else:
        peak_bps = float(down_bps)
        peak_price = min_low
        peak_ts = _first_timestamp_for_extreme(initial_df, "Low", min_low)
        direction = -1

    # The onset proxy is the first close that moves at least 25% of the threshold
    # in the final reaction direction within the alignment window.
    onset_threshold = max(0.25 * config.move_threshold_bps, 0.25)
    align_df = ohlcv.loc[
        (ohlcv.index >= initial_start)
        & (ohlcv.index <= event_ts + pd.Timedelta(minutes=config.lag_mins))
    ]
    onset_ts = None
    for ts, row in align_df.iterrows():
        close = _safe_float(row.get("Close"))
        if close is None:
            continue
        move_bps = points_to_bps(close - p0) * direction
        if move_bps >= onset_threshold:
            onset_ts = pd.Timestamp(ts)
            break

    fixed_marks: Dict[str, Optional[float]] = {}
    for mark in (1, 3, 5):
        close = _close_at_or_before(ohlcv, event_ts + pd.Timedelta(minutes=mark))
        fixed_marks[f"ret_{mark}m_bps"] = points_to_bps(close - p0) if close is not None else None

    fade_metrics: Dict[str, Any] = {}
    for minutes in config.fade_mins:
        close = _close_at_or_before(ohlcv, event_ts + pd.Timedelta(minutes=minutes))
        if close is None:
            net_bps = None
            fade_bps = None
            fade_pct = None
            persistence_pct = None
            full_fade = None
        else:
            net_bps = float(points_to_bps(close - p0))
            if direction >= 0:
                fade_bps = max(0.0, peak_bps - net_bps)
            else:
                fade_bps = max(0.0, net_bps - peak_bps)
            denom = abs(peak_bps) if peak_bps else None
            fade_pct = float(fade_bps / denom * 100.0) if denom else None
            persistence_pct = float(100.0 - fade_pct) if fade_pct is not None else None
            full_fade = bool(fade_pct is not None and fade_pct >= FULL_FADE_PCT)

        fade_metrics[f"net_{minutes}m_bps"] = net_bps
        fade_metrics[f"fade_{minutes}m_bps"] = fade_bps
        fade_metrics[f"fade_{minutes}m_pct"] = fade_pct
        fade_metrics[f"persistence_{minutes}m_pct"] = persistence_pct
        fade_metrics[f"full_fade_{minutes}m"] = full_fade
        fade_metrics[f"fade_ge_50_{minutes}m"] = bool(fade_pct is not None and fade_pct > 50.0)
        fade_metrics[f"fade_ge_80_{minutes}m"] = bool(fade_pct is not None and fade_pct > 80.0)
        fade_metrics[f"fade_full_{minutes}m"] = bool(fade_pct is not None and fade_pct >= FULL_FADE_PCT)

    path_points = []
    for ts, row in path_df.iterrows():
        close = _safe_float(row.get("Close"))
        if close is None:
            continue
        ts_utc = pd.Timestamp(ts).tz_convert("UTC")
        path_points.append(
            {
                "t": _iso_utc(ts_utc),
                "minute": round((ts_utc - event_ts).total_seconds() / 60.0, 2),
                "close": close,
                "move_bps": round(float(points_to_bps(close - p0)), 4),
            }
        )

    created_et = event_ts.tz_convert(ET)
    is_reaction = abs(peak_bps) >= config.move_threshold_bps
    return {
        "tweet_id": str(event.get("tweet_id", "")),
        "username": str(event.get("username", "unknown")),
        "account_key": str(event.get("account_key", "unknown")),
        "is_strict": bool(event.get("is_strict", False)),
        "is_topic_match": bool(event.get("is_topic_match", True)),
        "text": _clean_text(event.get("text")),
        "tweet_url": event.get("tweet_url") if pd.notna(event.get("tweet_url")) else None,
        "label": event.get("label") if pd.notna(event.get("label")) else None,
        "score": _safe_float(event.get("score")),
        "created_at_utc": _iso_utc(event_ts),
        "created_at_et": created_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "date_et": created_et.strftime("%Y-%m-%d"),
        "time_et": created_et.strftime("%H:%M:%S"),
        "p0": float(p0),
        "p0_32nds": decimal_to_bond_notation(float(p0)),
        "peak_bps": round(float(peak_bps), 4),
        "peak_abs_bps": round(abs(float(peak_bps)), 4),
        "peak_price": round(float(peak_price), 6),
        "peak_price_32nds": decimal_to_bond_notation(float(peak_price)),
        "peak_ts_utc": _iso_utc(peak_ts),
        "time_to_peak_min": round((peak_ts - event_ts).total_seconds() / 60.0, 2),
        "direction": "up" if direction > 0 else "down",
        "direction_sign": direction,
        "onset_ts_utc": _iso_utc(onset_ts) if onset_ts is not None else None,
        "onset_delay_min": round((onset_ts - event_ts).total_seconds() / 60.0, 2) if onset_ts is not None else None,
        "is_reaction": bool(is_reaction),
        "path": path_points if is_reaction else [],
        **fixed_marks,
        **fade_metrics,
    }


def compute_all_metrics(
    events_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    config: DashboardConfig,
) -> List[Dict[str, Any]]:
    metrics: List[Dict[str, Any]] = []
    skipped = 0
    for _, event in events_df.iterrows():
        row = compute_event_metrics(event, ohlcv, config)
        if row is None:
            skipped += 1
            continue
        metrics.append(row)
    logger.info("Computed metrics for %s events; skipped %s without enough OHLCV", len(metrics), skipped)
    return metrics


def build_account_stats(events: List[Dict[str, Any]], fade_minutes: int) -> List[Dict[str, Any]]:
    by_account: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        by_account.setdefault(str(event["username"]), []).append(event)

    rows: List[Dict[str, Any]] = []
    for username, account_events in by_account.items():
        reactions = [event for event in account_events if event.get("is_reaction")]
        rows.append(
            {
                "username": username,
                "message_count": len(account_events),
                "reaction_count": len(reactions),
                "reaction_rate_pct": (len(reactions) / len(account_events) * 100.0) if account_events else None,
                "median_peak_abs_bps": _median(event.get("peak_abs_bps") for event in reactions),
                "mean_peak_abs_bps": _mean(event.get("peak_abs_bps") for event in reactions),
                "median_fade_pct": _median(event.get(f"fade_{fade_minutes}m_pct") for event in reactions),
                "median_fade_bps": _median(event.get(f"fade_{fade_minutes}m_bps") for event in reactions),
                "median_persistence_pct": _median(event.get(f"persistence_{fade_minutes}m_pct") for event in reactions),
                "fade_ge_50_rate_pct": (
                    sum(1 for event in reactions if event.get(f"fade_ge_50_{fade_minutes}m"))
                    / len(reactions)
                    * 100.0
                )
                if reactions
                else None,
                "full_fade_rate_pct": (
                    sum(1 for event in reactions if event.get(f"fade_full_{fade_minutes}m"))
                    / len(reactions)
                    * 100.0
                )
                if reactions
                else None,
            }
        )

    rows.sort(key=lambda row: (row["reaction_count"], row["message_count"]), reverse=True)
    return rows


def build_dashboard_data(
    events: List[Dict[str, Any]],
    load_meta: Dict[str, int],
    ohlcv: pd.DataFrame,
    config: DashboardConfig,
) -> Dict[str, Any]:
    reactions = [event for event in events if event.get("is_reaction")]
    primary_fade = config.fade_mins[0]

    stats: Dict[str, Any] = {
        "all_peak_abs_bps": _compute_stats([event.get("peak_abs_bps") for event in events]),
        "reaction_peak_bps": _compute_stats([event.get("peak_bps") for event in reactions]),
        "reaction_peak_abs_bps": _compute_stats([event.get("peak_abs_bps") for event in reactions]),
        "time_to_peak_min": _compute_stats([event.get("time_to_peak_min") for event in reactions]),
    }
    for minutes in config.fade_mins:
        stats[f"net_{minutes}m_bps"] = _compute_stats([event.get(f"net_{minutes}m_bps") for event in reactions])
        stats[f"fade_{minutes}m_bps"] = _compute_stats([event.get(f"fade_{minutes}m_bps") for event in reactions])
        stats[f"fade_{minutes}m_pct"] = _compute_stats([event.get(f"fade_{minutes}m_pct") for event in reactions])
        stats[f"persistence_{minutes}m_pct"] = _compute_stats(
            [event.get(f"persistence_{minutes}m_pct") for event in reactions]
        )

    fade_severity = _fade_severity_for_events(reactions, config.fade_mins)
    primary_severity = fade_severity.get(f"{primary_fade}m", {})

    summary = {
        "message_count": len(events),
        "reaction_count": len(reactions),
        "reaction_rate_pct": (len(reactions) / len(events) * 100.0) if events else None,
        "median_peak_abs_bps": stats["reaction_peak_abs_bps"]["median"],
        "median_fade_pct": stats[f"fade_{primary_fade}m_pct"]["median"],
        "median_fade_bps": stats[f"fade_{primary_fade}m_bps"]["median"],
        "median_persistence_pct": stats[f"persistence_{primary_fade}m_pct"]["median"],
        "fade_ge_50_rate_pct": primary_severity.get("ge_50_pct"),
        "fade_ge_80_rate_pct": primary_severity.get("ge_80_pct"),
        "full_fade_rate_pct": primary_severity.get("full_pct"),
    }

    # Keep the embedded payload reasonable while preserving the most important
    # click-through paths first.
    reactions_sorted = sorted(reactions, key=lambda row: row.get("peak_abs_bps") or 0, reverse=True)
    reaction_ids_with_paths = {
        event["tweet_id"]
        for event in reactions_sorted[: config.max_paths]
    }
    display_events: List[Dict[str, Any]] = []
    for event in sorted(events, key=lambda row: (row.get("is_reaction", False), row.get("peak_abs_bps") or 0), reverse=True):
        row = dict(event)
        row["path_embedded"] = bool(row.get("tweet_id") in reaction_ids_with_paths and row.get("path"))
        if not row["path_embedded"]:
            row["path"] = []
        display_events.append(row)

    return {
        "meta": {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "symbol": config.symbol,
            "x_macro_db": str(config.x_macro_db),
            "start_date": config.start_date,
            "end_date": config.end_date,
            "accounts": config.accounts,
            "topic": config.topic,
            "topic_title": TOPIC_TITLES.get(config.topic, config.topic.replace("_", " ").title()),
            "keywords": config.keywords,
            "strict_terms": config.strict_terms,
            "full_fade_pct": FULL_FADE_PCT,
            "label": config.label,
            "dedupe_seconds": config.dedupe_seconds,
            "path_pre_mins": config.path_pre_mins,
            "initial_mins": config.initial_mins,
            "lag_mins": config.lag_mins,
            "move_threshold_bps": config.move_threshold_bps,
            "fade_mins": config.fade_mins,
            "max_paths": config.max_paths,
            "load_meta": load_meta,
            "ohlcv_rows": int(len(ohlcv)),
            "primary_fade_mins": primary_fade,
            "path_event_count": len(reaction_ids_with_paths),
        },
        "summary": summary,
        "stats": stats,
        "fade_severity": fade_severity,
        "account_stats": build_account_stats(events, primary_fade),
        "events": display_events,
    }


def _plotly_include() -> str:
    asset = PROJECT_ROOT / "workspace" / "scenarios" / "_assets" / "plotly-2.27.0.min.js"
    if asset.exists():
        return f"<script>{asset.read_text(encoding='utf-8')}</script>"
    return '<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>'


def _build_html(data: Dict[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":"), allow_nan=False)
    plotly_include = _plotly_include()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{data.get("meta", {}).get("topic_title", "News & Tweet Fade Dashboard")}</title>
  {plotly_include}
  <style>
    :root {{
      --bg: #0f1419;
      --panel: #17212f;
      --panel2: #1f2c3d;
      --text: #e7e9ea;
      --muted: #8b98a5;
      --border: #2f3a48;
      --blue: #1d9bf0;
      --green: #00ba7c;
      --red: #f4212e;
      --orange: #ff9f1c;
      --purple: #8b5cf6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 22px 28px 12px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, #17212f 0%, #0f1419 100%);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .subtitle {{ color: var(--muted); line-height: 1.45; max-width: 1100px; }}
    main {{ padding: 20px 28px 36px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
    }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ margin-top: 8px; font-size: 25px; font-weight: 700; }}
    .note {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(430px, 1fr));
      gap: 16px;
      margin: 18px 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      min-width: 0;
    }}
    .panel h2 {{ margin: 0 0 10px; font-size: 17px; }}
    .chart {{ width: 100%; height: 360px; }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 8px 0 14px;
      align-items: center;
    }}
    select, input {{
      background: var(--panel2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .06em;
      cursor: pointer;
      white-space: nowrap;
    }}
    tr:hover {{ background: rgba(255,255,255,.04); }}
    .event-row {{ cursor: pointer; }}
    .pill {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--panel2);
      border: 1px solid var(--border);
      font-size: 12px;
      color: var(--muted);
    }}
    .up {{ color: var(--green); }}
    .down {{ color: var(--red); }}
    .muted {{ color: var(--muted); }}
    .table-wrap {{ overflow: auto; max-height: 620px; border: 1px solid var(--border); border-radius: 10px; }}
    .event-text {{ max-width: 560px; min-width: 280px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    footer {{ color: var(--muted); padding: 0 28px 24px; font-size: 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>{data.get("meta", {}).get("topic_title", "News & Tweet Fade Dashboard")}</h1>
    <div class="subtitle">
      TY reaction study for X macro stream messages. A reaction event is any message whose initial peak excursion clears the configured threshold; fade is retracement from that peak to the selected later horizon. Use the view selector to compare broad conflict headlines with stricter attack/war headlines.
    </div>
  </header>
  <main>
    <section class="controls">
      <label class="muted">View
        <select id="viewSelect">
          <option value="broad">Broad conflict headlines</option>
          <option value="strict">War-only / attacks</option>
        </select>
      </label>
      <span class="pill" id="viewCount"></span>
    </section>
    <section class="cards" id="summaryCards"></section>

    <section class="grid">
      <div class="panel">
        <h2>Initial Reaction Distribution</h2>
        <div id="peakChart" class="chart"></div>
      </div>
      <div class="panel">
        <h2>Fade Distribution</h2>
        <div class="controls">
          <label class="muted">Fade horizon
            <select id="fadeSelect"></select>
          </label>
        </div>
        <div id="fadeChart" class="chart"></div>
      </div>
      <div class="panel">
        <h2>Initial Move vs Fade</h2>
        <div id="scatterChart" class="chart"></div>
      </div>
      <div class="panel">
        <h2>Time To Peak</h2>
        <div id="timeChart" class="chart"></div>
      </div>
    </section>

    <section class="panel">
      <h2>Fade Severity</h2>
      <div class="table-wrap" style="max-height: none;">
        <table id="severityTable"></table>
      </div>
    </section>

    <section class="panel">
      <h2>Account Breakdown</h2>
      <div class="table-wrap">
        <table id="accountTable"></table>
      </div>
    </section>

    <section class="panel" style="margin-top: 18px;">
      <h2>Reaction Events</h2>
      <div class="controls">
        <input id="eventSearch" placeholder="Filter account/text..." style="min-width: 280px;">
        <label class="muted">Account
          <select id="accountFilter"></select>
        </label>
        <span class="pill" id="eventCount"></span>
      </div>
      <div id="pathChart" class="chart" style="height: 420px;"></div>
      <div class="table-wrap">
        <table id="eventTable"></table>
      </div>
    </section>
  </main>
  <footer id="footer"></footer>

  <script>
    const DATA = {payload};
    const PLOT_LAYOUT = {{
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: {{ color: '#e7e9ea' }},
      margin: {{ l: 54, r: 22, t: 16, b: 45 }},
      xaxis: {{ gridcolor: '#2f3a48', zerolinecolor: '#8b98a5' }},
      yaxis: {{ gridcolor: '#2f3a48', zerolinecolor: '#8b98a5' }},
      showlegend: false
    }};

    let currentFade = DATA.meta.primary_fade_mins;
    let activeView = 'broad';
    let sortState = {{ table: 'peak_abs_bps', dir: -1 }};
    const allEvents = DATA.events;

    function fmt(value, digits = 1, suffix = '') {{
      if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a';
      return Number(value).toFixed(digits) + suffix;
    }}
    function signedClass(value) {{ return Number(value) >= 0 ? 'up' : 'down'; }}
    function pct(value) {{ return fmt(value, 1, '%'); }}
    function activeEvents() {{
      return activeView === 'strict' ? allEvents.filter(e => e.is_strict) : allEvents;
    }}
    function activeReactions() {{
      return activeEvents().filter(e => e.is_reaction);
    }}
    function rate(count, total) {{
      return total ? (count / total * 100.0) : null;
    }}
    function median(nums) {{
      const arr = nums.filter(v => v !== null && v !== undefined && Number.isFinite(Number(v))).map(Number).sort((a, b) => a - b);
      if (!arr.length) return null;
      const mid = Math.floor(arr.length / 2);
      return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
    }}
    function severityFor(events, minutes) {{
      const reactions = events.filter(e => e.is_reaction);
      const eligible = reactions.filter(e => e[`fade_${{minutes}}m_pct`] !== null && e[`fade_${{minutes}}m_pct`] !== undefined);
      const total = eligible.length;
      const ge50 = eligible.filter(e => e[`fade_ge_50_${{minutes}}m`]).length;
      const ge80 = eligible.filter(e => e[`fade_ge_80_${{minutes}}m`]).length;
      const full = eligible.filter(e => e[`fade_full_${{minutes}}m`]).length;
      return {{
        reaction_count: total,
        ge_50_count: ge50,
        ge_50_pct: rate(ge50, total),
        ge_80_count: ge80,
        ge_80_pct: rate(ge80, total),
        full_count: full,
        full_pct: rate(full, total)
      }};
    }}
    function summaryFor(events) {{
      const reactions = events.filter(e => e.is_reaction);
      const sev = severityFor(events, currentFade);
      return {{
        message_count: events.length,
        reaction_count: reactions.length,
        reaction_rate_pct: rate(reactions.length, events.length),
        median_peak_abs_bps: median(reactions.map(e => e.peak_abs_bps)),
        median_fade_pct: median(reactions.map(e => e[`fade_${{currentFade}}m_pct`])),
        median_fade_bps: median(reactions.map(e => e[`fade_${{currentFade}}m_bps`])),
        median_persistence_pct: median(reactions.map(e => e[`persistence_${{currentFade}}m_pct`])),
        fade_ge_50_rate_pct: sev.ge_50_pct,
        fade_ge_80_rate_pct: sev.ge_80_pct,
        full_fade_rate_pct: sev.full_pct
      }};
    }}

    function renderSummary() {{
      const s = summaryFor(activeEvents());
      const noMinMove = Number(DATA.meta.move_threshold_bps) === 0;
      const eventLabel = noMinMove ? 'Events With Market Data' : 'Reaction Events';
      const eventNote = noMinMove ? 'no minimum move' : `${{pct(s.reaction_rate_pct)}} reaction rate`;
      const cards = [
        ['Messages', fmt(s.message_count, 0), activeView === 'strict' ? 'war-only subset' : `${{fmt(DATA.meta.load_meta.deduped || 0, 0)}} deduped`],
        [eventLabel, fmt(s.reaction_count, 0), eventNote],
        ['Median Initial Move', fmt(s.median_peak_abs_bps, 2, ' bps'), noMinMove ? 'all measured events' : `threshold ${{DATA.meta.move_threshold_bps}} bps`],
        [`Median Fade ${{currentFade}}m`, pct(s.median_fade_pct), `${{fmt(s.median_fade_bps, 2, ' bps')}} retraced`],
        ['Median Persistence', pct(s.median_persistence_pct), '100% means no fade'],
        [`Faded >50% ${{currentFade}}m`, pct(s.fade_ge_50_rate_pct), 'more than half retraced'],
        [`Faded >80% ${{currentFade}}m`, pct(s.fade_ge_80_rate_pct), 'deep retracement'],
        [`Full Fade ${{currentFade}}m`, pct(s.full_fade_rate_pct), `>=${{DATA.meta.full_fade_pct}}% retraced`]
      ];
      document.getElementById('summaryCards').innerHTML = cards.map(([label, value, note]) => `
        <div class="card"><div class="label">${{label}}</div><div class="value">${{value}}</div><div class="note">${{note}}</div></div>
      `).join('');
      document.getElementById('viewCount').textContent = `${{s.message_count}} messages | ${{s.reaction_count}} ${{noMinMove ? 'with market data' : 'reactions'}}`;
    }}

    function values(field) {{
      return activeReactions().map(e => e[field]).filter(v => v !== null && v !== undefined && Number.isFinite(Number(v)));
    }}

    function renderCharts() {{
      const reactions = activeReactions();
      Plotly.newPlot('peakChart', [{{
        type: 'histogram',
        x: values('peak_bps'),
        marker: {{ color: '#1d9bf0' }},
        nbinsx: 60,
        hovertemplate: 'Peak reaction: %{{x:.2f}} bps<br>Count: %{{y}}<extra></extra>'
      }}], {{ ...PLOT_LAYOUT, xaxis: {{ ...PLOT_LAYOUT.xaxis, title: 'Signed initial peak reaction (bps)' }}, yaxis: {{ ...PLOT_LAYOUT.yaxis, title: 'Events' }} }}, {{ responsive: true }});

      renderFadeChart();

      Plotly.newPlot('scatterChart', [{{
        type: 'scattergl',
        mode: 'markers',
        x: values('peak_abs_bps'),
        y: reactions.map(e => e[`fade_${{currentFade}}m_pct`]),
        text: reactions.map(e => `${{e.username}}<br>${{e.created_at_et}}<br>${{e.text}}`),
        marker: {{ color: reactions.map(e => e.direction === 'up' ? '#00ba7c' : '#f4212e'), size: 7, opacity: .72 }},
        hovertemplate: '%{{text}}<br>Peak: %{{x:.2f}} bps<br>Fade: %{{y:.1f}}%<extra></extra>'
      }}], {{ ...PLOT_LAYOUT, xaxis: {{ ...PLOT_LAYOUT.xaxis, title: 'Abs initial peak (bps)' }}, yaxis: {{ ...PLOT_LAYOUT.yaxis, title: `Fade ${{currentFade}}m (%)` }}, showlegend: false }}, {{ responsive: true }});

      Plotly.newPlot('timeChart', [{{
        type: 'histogram',
        x: values('time_to_peak_min'),
        marker: {{ color: '#8b5cf6' }},
        nbinsx: 40,
        hovertemplate: 'Time to peak: %{{x:.1f}} min<br>Count: %{{y}}<extra></extra>'
      }}], {{ ...PLOT_LAYOUT, xaxis: {{ ...PLOT_LAYOUT.xaxis, title: 'Minutes from message to peak' }}, yaxis: {{ ...PLOT_LAYOUT.yaxis, title: 'Events' }} }}, {{ responsive: true }});
    }}

    function renderFadeChart() {{
      Plotly.newPlot('fadeChart', [{{
        type: 'histogram',
        x: values(`fade_${{currentFade}}m_pct`),
        marker: {{ color: '#ff9f1c' }},
        nbinsx: 60,
        hovertemplate: 'Fade: %{{x:.1f}}%<br>Count: %{{y}}<extra></extra>'
      }}], {{ ...PLOT_LAYOUT, xaxis: {{ ...PLOT_LAYOUT.xaxis, title: `Fade by t+${{currentFade}}m (%)` }}, yaxis: {{ ...PLOT_LAYOUT.yaxis, title: 'Events' }} }}, {{ responsive: true }});
    }}

    function renderSeverityTable() {{
      const events = activeEvents();
      const rows = DATA.meta.fade_mins.map(m => [m, severityFor(events, m)]);
      document.getElementById('severityTable').innerHTML = `
        <thead><tr>
          <th>Horizon</th><th>Reactions</th><th>Faded >50%</th><th>Faded >80%</th><th>Full fade (&gt;=${{DATA.meta.full_fade_pct}}%)</th>
        </tr></thead>
        <tbody>${{rows.map(([m, s]) => `
          <tr>
            <td>t+${{m}}m</td>
            <td>${{fmt(s.reaction_count, 0)}}</td>
            <td>${{fmt(s.ge_50_count, 0)}} (${{pct(s.ge_50_pct)}})</td>
            <td>${{fmt(s.ge_80_count, 0)}} (${{pct(s.ge_80_pct)}})</td>
            <td>${{fmt(s.full_count, 0)}} (${{pct(s.full_pct)}})</td>
          </tr>`).join('')}}
        </tbody>`;
    }}

    function accountRows() {{
      const rowsByAccount = new Map();
      for (const e of activeEvents()) {{
        if (!rowsByAccount.has(e.username)) rowsByAccount.set(e.username, []);
        rowsByAccount.get(e.username).push(e);
      }}
      const rows = [];
      for (const [username, events] of rowsByAccount.entries()) {{
        const reactions = events.filter(e => e.is_reaction);
        const ge50 = reactions.filter(e => e[`fade_ge_50_${{currentFade}}m`]).length;
        const full = reactions.filter(e => e[`fade_full_${{currentFade}}m`]).length;
        rows.push({{
          username,
          message_count: events.length,
          reaction_count: reactions.length,
          reaction_rate_pct: rate(reactions.length, events.length),
          median_peak_abs_bps: median(reactions.map(e => e.peak_abs_bps)),
          median_fade_pct: median(reactions.map(e => e[`fade_${{currentFade}}m_pct`])),
          median_persistence_pct: median(reactions.map(e => e[`persistence_${{currentFade}}m_pct`])),
          fade_ge_50_rate_pct: rate(ge50, reactions.length),
          full_fade_rate_pct: rate(full, reactions.length)
        }});
      }}
      return rows.sort((a, b) => (b.reaction_count - a.reaction_count) || (b.message_count - a.message_count));
    }}

    function renderAccountTable() {{
      const rows = accountRows().slice(0, 80);
      document.getElementById('accountTable').innerHTML = `
        <thead><tr>
          <th>Account</th><th>Messages</th><th>Reactions</th><th>Reaction Rate</th>
          <th>Median Peak</th><th>Median Fade</th><th>Median Persistence</th><th>>50% Fade</th><th>Full Fade</th>
        </tr></thead>
        <tbody>${{rows.map(r => `
          <tr>
            <td>${{r.username}}</td>
            <td>${{fmt(r.message_count, 0)}}</td>
            <td>${{fmt(r.reaction_count, 0)}}</td>
            <td>${{pct(r.reaction_rate_pct)}}</td>
            <td>${{fmt(r.median_peak_abs_bps, 2, ' bps')}}</td>
            <td>${{pct(r.median_fade_pct)}}</td>
            <td>${{pct(r.median_persistence_pct)}}</td>
            <td>${{pct(r.fade_ge_50_rate_pct)}}</td>
            <td>${{pct(r.full_fade_rate_pct)}}</td>
          </tr>`).join('')}}
        </tbody>`;
    }}

    function renderFilters() {{
      const viewSel = document.getElementById('viewSelect');
      viewSel.value = activeView;
      viewSel.addEventListener('change', () => {{
        activeView = viewSel.value;
        renderAll();
      }});

      const fadeSel = document.getElementById('fadeSelect');
      fadeSel.innerHTML = DATA.meta.fade_mins.map(m => `<option value="${{m}}">${{m}}m</option>`).join('');
      fadeSel.value = String(currentFade);
      fadeSel.addEventListener('change', () => {{
        currentFade = Number(fadeSel.value);
        renderAll();
      }});

      const accounts = Array.from(new Set(allEvents.filter(e => e.is_reaction).map(e => e.username))).sort();
      document.getElementById('accountFilter').innerHTML = '<option value="">All accounts</option>' + accounts.map(a => `<option value="${{a}}">${{a}}</option>`).join('');
      document.getElementById('accountFilter').addEventListener('change', renderEventTable);
      document.getElementById('eventSearch').addEventListener('input', renderEventTable);
    }}

    function filteredEvents() {{
      const q = document.getElementById('eventSearch').value.toLowerCase().trim();
      const account = document.getElementById('accountFilter').value;
      let rows = activeReactions();
      if (account) rows = rows.filter(e => e.username === account);
      if (q) rows = rows.filter(e => (`${{e.username}} ${{e.text}} ${{e.created_at_et}}`).toLowerCase().includes(q));
      rows = rows.slice().sort((a, b) => {{
        const av = a[sortState.table];
        const bv = b[sortState.table];
        if (av === bv) return 0;
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        return av > bv ? sortState.dir : -sortState.dir;
      }});
      return rows;
    }}

    function setSort(field) {{
      if (sortState.table === field) sortState.dir *= -1;
      else sortState = {{ table: field, dir: -1 }};
      renderEventTable();
    }}

    function renderEventTable() {{
      const rows = filteredEvents();
      document.getElementById('eventCount').textContent = `${{rows.length}} ${{Number(DATA.meta.move_threshold_bps) === 0 ? 'events with market data' : 'reaction events'}}`;
      const limited = rows.slice(0, 500);
      document.getElementById('eventTable').innerHTML = `
        <thead><tr>
          <th onclick="setSort('created_at_utc')">Time ET</th>
          <th onclick="setSort('username')">Account</th>
          <th onclick="setSort('peak_abs_bps')">Peak</th>
          <th onclick="setSort('time_to_peak_min')">TTP</th>
          <th onclick="setSort('fade_${{currentFade}}m_pct')">Fade ${{currentFade}}m</th>
          <th onclick="setSort('persistence_${{currentFade}}m_pct')">Persistence</th>
          <th>Text</th>
        </tr></thead>
        <tbody>${{limited.map((e, i) => `
          <tr class="event-row" onclick="renderPath('${{e.tweet_id}}')">
            <td><span class="muted">${{e.created_at_et}}</span></td>
            <td>${{e.username}}</td>
            <td class="${{signedClass(e.peak_bps)}}">${{fmt(e.peak_bps, 2, ' bps')}}</td>
            <td>${{fmt(e.time_to_peak_min, 1, 'm')}}</td>
            <td>${{pct(e[`fade_${{currentFade}}m_pct`])}}</td>
            <td>${{pct(e[`persistence_${{currentFade}}m_pct`])}}</td>
            <td class="event-text">${{e.tweet_url ? `<a href="${{e.tweet_url}}" target="_blank">${{e.text}}</a>` : e.text}}</td>
          </tr>`).join('')}}
        </tbody>`;
      if (limited.length) renderPath(limited[0].tweet_id);
    }}

    function renderPath(tweetId) {{
      const e = activeReactions().find(row => row.tweet_id === tweetId) || allEvents.find(row => row.tweet_id === tweetId);
      if (!e || !e.path || !e.path.length) {{
        Plotly.newPlot('pathChart', [], {{
          ...PLOT_LAYOUT,
          title: {{ text: 'Path not embedded for this event (scalar metrics are still included). Increase --max-paths to embed more paths.', x: 0.02, font: {{ size: 14 }} }},
          xaxis: {{ ...PLOT_LAYOUT.xaxis, title: 'Minutes from message' }},
          yaxis: {{ ...PLOT_LAYOUT.yaxis, title: 'TY move from message (bps)' }}
        }}, {{ responsive: true }});
        return;
      }}
      const x = e.path.map(p => p.minute);
      const y = e.path.map(p => p.move_bps);
      const trace = {{
        type: 'scatter',
        mode: 'lines',
        x, y,
        line: {{ color: e.direction === 'up' ? '#00ba7c' : '#f4212e', width: 2 }},
        hovertemplate: 'Minute: %{{x:.1f}}<br>Move: %{{y:.2f}} bps<extra></extra>'
      }};
      const shapes = [
        {{ type: 'line', x0: 0, x1: 0, y0: Math.min(...y), y1: Math.max(...y), line: {{ color: '#8b98a5', dash: 'dot' }} }},
        {{ type: 'line', x0: Math.min(...x), x1: Math.max(...x), y0: 0, y1: 0, line: {{ color: '#8b98a5', dash: 'dot' }} }},
        {{ type: 'line', x0: currentFade, x1: currentFade, y0: Math.min(...y), y1: Math.max(...y), line: {{ color: '#ff9f1c', dash: 'dash' }} }}
      ];
      Plotly.newPlot('pathChart', [trace], {{
        ...PLOT_LAYOUT,
        title: {{ text: `${{e.username}} - ${{e.created_at_et}}<br><span style="font-size:12px;color:#8b98a5">${{e.text}}</span>`, x: 0.02, font: {{ size: 14 }} }},
        xaxis: {{ ...PLOT_LAYOUT.xaxis, title: 'Minutes from message' }},
        yaxis: {{ ...PLOT_LAYOUT.yaxis, title: 'TY move from message (bps)' }},
        shapes
      }}, {{ responsive: true }});
    }}

    function renderFooter() {{
      document.getElementById('footer').textContent =
        `Generated ${{DATA.meta.generated_at}} | DB: ${{DATA.meta.x_macro_db}} | OHLCV rows: ${{DATA.meta.ohlcv_rows}} | ` +
        `topic=${{DATA.meta.topic}} | initial=${{DATA.meta.initial_mins}}m, lag=${{DATA.meta.lag_mins}}m, threshold=${{DATA.meta.move_threshold_bps}} bps, max_paths=${{DATA.meta.max_paths}}, dedupe=${{DATA.meta.dedupe_seconds}}s`;
    }}

    function renderAll() {{
      renderSummary();
      renderCharts();
      renderSeverityTable();
      renderAccountTable();
      renderEventTable();
    }}

    renderFilters();
    renderAll();
    renderFooter();
  </script>
</body>
</html>
"""


def write_dashboard(data: Dict[str, Any], output: Optional[Path]) -> Path:
    if output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = PROJECT_ROOT / "workspace" / "scenarios"
        out_dir.mkdir(parents=True, exist_ok=True)
        topic = str(data.get("meta", {}).get("topic") or "all")
        output = out_dir / f"news_fade_dashboard_{topic}_{ts}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_build_html(data), encoding="utf-8")
    return output


def parse_accounts(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    accounts = [part.strip().lstrip("@") for part in value.split(",") if part.strip()]
    return accounts or None


def parse_csv_terms(value: Optional[str], default: Sequence[str]) -> List[str]:
    if value is None:
        return list(default)
    terms = [part.strip().lower() for part in value.split(",") if part.strip()]
    return terms


def parse_fade_minutes(value: str) -> List[int]:
    minutes = sorted({int(part.strip()) for part in value.split(",") if part.strip()})
    if not minutes or any(item <= 0 for item in minutes):
        raise argparse.ArgumentTypeError("fade minutes must be a comma-separated list of positive integers")
    return minutes


def parse_args() -> DashboardConfig:
    parser = argparse.ArgumentParser(description="Build the News & Tweet Fade Dashboard.")
    parser.add_argument(
        "--x-macro-db",
        type=Path,
        default=Path(os.getenv("X_MACRO_DB_PATH", str(DEFAULT_X_MACRO_DB))),
        help="Path to x_macro_stream.db (default: X_MACRO_DB_PATH or ~/.market_data/x_macro_stream.db)",
    )
    parser.add_argument("--symbol", default="TY", help="OHLCV symbol prefix to query (default: TY)")
    parser.add_argument("--start-date", help="Start date/time for messages (UTC; YYYY-MM-DD ok)")
    parser.add_argument("--end-date", help="End date/time for messages (UTC; YYYY-MM-DD ok)")
    parser.add_argument("--accounts", help="Comma-separated X accounts to include (without @)")
    parser.add_argument(
        "--topic",
        choices=["none", DEFAULT_TOPIC],
        default=DEFAULT_TOPIC,
        help="Topic filter to apply before market alignment (default: iran_israel_lebanon)",
    )
    parser.add_argument(
        "--keywords",
        help="Comma-separated broad topic keywords; overrides the default Iran/Israel/Lebanon entity list",
    )
    parser.add_argument(
        "--strict-terms",
        help="Comma-separated strict conflict terms; overrides the default war-only term list",
    )
    parser.add_argument(
        "--label",
        choices=["all", "pending", "approved", "rejected"],
        default="all",
        help="Message label filter from tweet_labels (default: all)",
    )
    parser.add_argument(
        "--dedupe-seconds",
        type=int,
        default=60,
        help="Collapse same-account messages within this many seconds; 0 disables (default: 60)",
    )
    parser.add_argument("--path-pre-mins", type=int, default=DEFAULT_PATH_PRE_MINUTES)
    parser.add_argument("--initial-mins", type=int, default=5)
    parser.add_argument("--lag-mins", type=int, default=3)
    parser.add_argument("--move-threshold-bps", type=float, default=2.0)
    parser.add_argument("--fade-mins", type=parse_fade_minutes, default=DEFAULT_FADE_MINUTES)
    parser.add_argument(
        "--max-paths",
        type=int,
        default=600,
        help="Maximum number of event path arrays to embed in HTML; scalar metrics remain for all events (default: 600)",
    )
    parser.add_argument("--out", type=Path, help="Output HTML path")
    args = parser.parse_args()

    if args.dedupe_seconds < 0:
        parser.error("--dedupe-seconds must be >= 0")
    if args.path_pre_mins < 0:
        parser.error("--path-pre-mins must be >= 0")
    if args.initial_mins <= 0:
        parser.error("--initial-mins must be > 0")
    if args.lag_mins < 0:
        parser.error("--lag-mins must be >= 0")
    if args.move_threshold_bps < 0:
        parser.error("--move-threshold-bps must be >= 0")
    if args.max_paths < 0:
        parser.error("--max-paths must be >= 0")

    return DashboardConfig(
        x_macro_db=args.x_macro_db,
        symbol=args.symbol.upper().strip(),
        start_date=args.start_date,
        end_date=args.end_date,
        accounts=parse_accounts(args.accounts),
        topic=args.topic,
        keywords=parse_csv_terms(args.keywords, IRAN_ISRAEL_KEYWORDS),
        strict_terms=parse_csv_terms(args.strict_terms, IRAN_ISRAEL_STRICT_TERMS),
        label=args.label,
        dedupe_seconds=args.dedupe_seconds,
        path_pre_mins=args.path_pre_mins,
        initial_mins=args.initial_mins,
        lag_mins=args.lag_mins,
        move_threshold_bps=args.move_threshold_bps,
        fade_mins=args.fade_mins,
        max_paths=args.max_paths,
        output=args.out,
    )


def main() -> None:
    config = parse_args()
    logger.info("Loading messages from %s", config.x_macro_db)
    events_df, load_meta = load_news_events(config)
    if events_df.empty:
        raise SystemExit("No messages found for the selected filters.")
    logger.info("Loaded %s messages after filters/dedupe", len(events_df))

    ohlcv = load_ohlcv_for_events(events_df, config)
    if ohlcv.empty:
        raise SystemExit("No OHLCV rows found for the selected message range.")

    events = compute_all_metrics(events_df, ohlcv, config)
    load_meta["metric_skipped_no_ohlcv"] = int(len(events_df) - len(events))
    load_meta["metric_event_count"] = int(len(events))
    if not events:
        raise SystemExit("No events had enough OHLCV to compute metrics.")

    data = build_dashboard_data(events, load_meta, ohlcv, config)
    output = write_dashboard(data, config.output)

    summary = data["summary"]
    logger.info("Dashboard written to %s", output)
    logger.info(
        "Reaction events: %s/%s (%.1f%%); median peak %.2f bps; median fade %.1f%%",
        summary["reaction_count"],
        summary["message_count"],
        summary["reaction_rate_pct"] or 0.0,
        summary["median_peak_abs_bps"] or 0.0,
        summary["median_fade_pct"] or 0.0,
    )
    print(output)


if __name__ == "__main__":
    main()
