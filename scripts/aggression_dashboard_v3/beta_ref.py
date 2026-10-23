"""Reference beta for Aggression V3.

Live path: ``ensure_beta_ref`` looks up today's day tag in ``tag_beta.json``
(flow-history day OLS per tag). The superseded 10-session whole-session mean
is ``ensure_session_mean_beta_ref``.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import (
    BETA_REF_LOOKBACK_HOURS,
    BETA_REF_SESSIONS,
    BETA_REF_TOD_BUCKET_MIN,
    EASTERN,
    LIVE_STORE_DIR,
    SESSION_SLOPE_MIN_INCREMENTS,
    build_print_frame,
    completed_session_starts,
    fetch_curve_trades,
    globex_session_end,
    load_dv01_table,
    point_value_usd,
)
from scripts.aggression_dashboard_v3 import tag_beta

logger = logging.getLogger(__name__)

BETA_VERSION = "whole_session_v1"
CACHE_PATH = LIVE_STORE_DIR / "beta_ref.json"
_CACHE_LOCK = threading.Lock()
# A session counts as covered by in-memory trades if they start no later than this after its open.
WINDOW_COVER_SLACK = pd.Timedelta(hours=1)


def _trades_window_start(
    trades_window: Optional[Dict[str, pd.DataFrame]],
    symbols: Iterable[str],
) -> Optional[pd.Timestamp]:
    """Latest first print across the selected symbols' in-memory trades.

    None when any selected symbol has no in-memory prints, so every session is read from the cache.
    """
    if not trades_window:
        return None
    starts = []
    for symbol in symbols:
        df = trades_window.get(symbol)
        if df is None or df.empty or "ts" not in df.columns:
            return None
        starts.append(pd.to_datetime(df["ts"], utc=True).min())
    return max(starts) if starts else None


def _entry_identity(payload: Dict[str, Any]) -> tuple:
    return (
        payload.get("version"),
        payload.get("price_symbol"),
        tuple(payload.get("aggression_symbols") or ()),
        bool(payload.get("dv01_adjust")),
        tuple(payload.get("sessions") or ()),
        payload.get("lookback_hours"),
    )


def _identity_str(identity: tuple) -> str:
    return json.dumps(identity)


def _entries_from_disk() -> Dict[str, Dict[str, Any]]:
    raw = _load_cache()
    if not raw:
        return {}
    stored = raw.get("entries")
    if isinstance(stored, dict):
        return {key: value for key, value in stored.items() if isinstance(value, dict)}
    if raw.get("version"):
        return {_identity_str(_entry_identity(raw)): raw}
    return {}


def _usable_entry(entries: Dict[str, Dict[str, Any]], cache_id: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    want = _entry_identity(cache_id)
    for entry in entries.values():
        if not entry.get("ok") or _entry_identity(entry) != want:
            continue
        beta = entry.get("beta_ref")
        try:
            if beta is not None and float(beta) > 0:
                return entry
        except (TypeError, ValueError):
            continue
    return None


def _save_entry(payload: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        entries = _entries_from_disk()
        entries[_identity_str(_entry_identity(payload))] = payload
        _save_cache({"version": BETA_VERSION, "entries": entries})


def bucket_display_time(now: datetime) -> datetime:
    """Floor the display clock to the beta cache bucket (ET)."""
    now_et = now.astimezone(EASTERN)
    minute = (now_et.minute // BETA_REF_TOD_BUCKET_MIN) * BETA_REF_TOD_BUCKET_MIN
    return now_et.replace(minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


def display_clock_on_session(session_start: datetime, now: datetime) -> datetime:
    """Same ET clock time as `now`, placed on this Globex session."""
    now_et = now.astimezone(EASTERN)
    start_et = session_start.astimezone(EASTERN)
    if now_et.hour >= 18:
        clock_et = start_et.replace(
            hour=now_et.hour,
            minute=now_et.minute,
            second=now_et.second,
            microsecond=0,
        )
    else:
        nxt = start_et + timedelta(days=1)
        clock_et = nxt.replace(
            hour=now_et.hour,
            minute=now_et.minute,
            second=now_et.second,
            microsecond=0,
        )
    return clock_et.astimezone(timezone.utc)


def session_tod_window(session_start: datetime, now: datetime) -> Tuple[datetime, datetime]:
    """Last 3 hours ending at the display clock, clipped to this Globex session."""
    end = display_clock_on_session(session_start, now)
    start = end - timedelta(hours=BETA_REF_LOOKBACK_HOURS)
    sess_start = session_start.astimezone(timezone.utc)
    sess_end = globex_session_end(session_start)
    if start < sess_start:
        start = sess_start
    if end > sess_end:
        end = sess_end
    return start, end


def clip_frame_to_window(frame: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Keep prints in [start, end)."""
    if frame is None or frame.empty:
        return frame
    idx = frame.index
    if not isinstance(idx, pd.DatetimeIndex):
        return frame.iloc[0:0].copy()
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    work = frame.copy()
    work.index = idx
    return work.loc[(work.index >= pd.Timestamp(start)) & (work.index < pd.Timestamp(end))].copy()


def _ols_slope(delta_a: pd.Series, delta_p: pd.Series, *, min_periods: int = SESSION_SLOPE_MIN_INCREMENTS) -> Tuple[float, float, Optional[str]]:
    """OLS of ΔP on ΔA with intercept. Never regresses absolute P on absolute A."""
    sample = pd.DataFrame({"x": delta_a.astype(float), "y": delta_p.astype(float)}).dropna()
    if len(sample) < min_periods or sample["x"].nunique() < 2:
        return float("nan"), float("nan"), f"Need at least {min_periods} change-on-change increments."
    x = sample["x"].to_numpy()
    y = sample["y"].to_numpy()
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    denom = float(np.sum((x - x_mean) ** 2))
    if denom == 0:
        return float("nan"), float("nan"), "Aggression variance is zero."
    beta = float(np.sum((x - x_mean) * (y - y_mean)) / denom)
    alpha = y_mean - beta * x_mean
    return alpha, beta, None


def fit_session_slope(frame: pd.DataFrame) -> Dict[str, Any]:
    """Estimate one session change-on-change slope."""
    if frame is None or frame.empty:
        return {"ok": False, "reason": "empty session frame", "beta": None, "alpha": None, "n": 0}
    alpha, beta, reason = _ols_slope(frame.get("delta_a", pd.Series(dtype=float)), frame.get("delta_p", pd.Series(dtype=float)))
    if reason:
        return {"ok": False, "reason": reason, "beta": None, "alpha": None, "n": int(len(frame))}
    return {
        "ok": True,
        "alpha": alpha,
        "beta": beta,
        "n": int(len(frame)),
        "increments": int(frame[["delta_a", "delta_p"]].dropna().shape[0]),
    }


def beta_to_yield_bp_per_1m(beta_price: float, price_symbol: str, dv01_table: Optional[Dict[str, float]] = None) -> float:
    """Display-only conversion of the price-unit mean into yield bp per $1m."""
    table = dv01_table or load_dv01_table()
    dv01 = float(table[price_symbol])
    return -float(beta_price) * 1_000_000.0 * point_value_usd(price_symbol) / dv01


def _session_key(start: datetime) -> str:
    return start.astimezone(timezone.utc).isoformat()


def session_set_mean(slopes: Iterable[Optional[float]]) -> Tuple[Optional[float], int]:
    """Mean of the finite per-session slopes given, and how many were used."""
    values = [float(value) for value in slopes if value is not None and np.isfinite(float(value))]
    if not values:
        return None, 0
    return float(np.mean(values)), len(values)


def _load_cache() -> Dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read beta_ref cache")
        return {}


def _save_cache(payload: Dict[str, Any]) -> None:
    LIVE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compute_session_beta(
    session_start: datetime,
    *,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
    dv01_table: Dict[str, float],
    trades_by_symbol: Optional[Dict[str, pd.DataFrame]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fit one completed session's change-on-change slope; with ``now``, only its last-3-hour TOD window."""
    end = globex_session_end(session_start)
    symbols = tuple(aggression_symbols)
    trades = trades_by_symbol
    fetch_errors: Dict[str, str] = {}
    if trades is None:
        trades, fetch_errors = fetch_curve_trades(symbols, session_start, end, cache=True)
    frame, meta = build_print_frame(
        {symbol: trades.get(symbol, pd.DataFrame()) for symbol in symbols},
        price_symbol=price_symbol,
        dv01_table=dv01_table,
        dv01_adjust=dv01_adjust,
        session_start=session_start,
    )
    window_start = session_start
    window_end = end
    if now is not None:
        window_start, window_end = session_tod_window(session_start, now)
        frame = clip_frame_to_window(frame, window_start, window_end)
    fit = fit_session_slope(frame)
    fit.update({
        "session_start": _session_key(session_start),
        "session_end": end.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "trade_count": meta.get("trade_count", 0),
        "fetch_errors": fetch_errors,
    })
    return fit


def ensure_beta_ref(
    *,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
    now: Optional[datetime] = None,
    trades_window: Optional[Dict[str, pd.DataFrame]] = None,
    compute: bool = True,
) -> Dict[str, Any]:
    """Return the live reference beta: today's day tag and that tag's day-level OLS beta.

    ``compute=False`` only reads ``tag_beta.json``. ``compute=True`` first brings the table
    through the last closed session (one read-only V1 query) when it is behind.
    Cache hits come back with ``cached`` set. ``trades_window`` is unused.
    """
    symbols = tuple(aggression_symbols)
    kwargs = dict(price_symbol=price_symbol, aggression_symbols=symbols, dv01_adjust=dv01_adjust, now=now)
    payload = tag_beta.beta_for_session(**kwargs)
    cached = not payload.get("stale")
    if payload.get("stale") and compute:
        update = tag_beta.update_table(**kwargs)
        payload = tag_beta.beta_for_session(**kwargs)
        if payload.get("stale") and update.get("reason"):
            payload = {**payload, "stale": False, "reason": update["reason"]}
    beta = payload.get("beta_ref")
    through = payload.get("checked_through")
    last_end = None
    if through:
        last_end = datetime.combine(date.fromisoformat(through), time(17, 0), tzinfo=EASTERN).isoformat()
    return {
        **payload,
        "beta_display_yield_bp_per_1m": (
            beta_to_yield_bp_per_1m(beta, price_symbol, load_dv01_table()) if beta is not None else None
        ),
        "last_session_end": last_end,
        "sessions": [],
        "bucket_stale": False,
        "cached": cached,
    }


def ensure_session_mean_beta_ref(
    *,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
    now: Optional[datetime] = None,
    trades_window: Optional[Dict[str, pd.DataFrame]] = None,
    compute: bool = True,
) -> Dict[str, Any]:
    """Return the mean of the last 10 completed sessions' whole-session change-on-change slopes.

    Superseded on the live page by the tagged day beta; kept for archived-session audits.

    One value per completed-session set; it only changes at the 18:00 ET roll.
    Cache hits come back with ``cached`` set.
    """
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    starts = completed_session_starts(now_utc, n=BETA_REF_SESSIONS)
    start_keys = [_session_key(start) for start in starts]
    symbols = tuple(aggression_symbols)
    cache_id = {
        "version": BETA_VERSION,
        "price_symbol": price_symbol,
        "aggression_symbols": list(symbols),
        "dv01_adjust": bool(dv01_adjust),
        "sessions": start_keys,
        "window": "whole_session",
        "last_session_end": globex_session_end(starts[-1]).isoformat() if starts else None,
    }
    with _CACHE_LOCK:
        cached = _usable_entry(_entries_from_disk(), cache_id)
    if cached:
        return {**cached, "bucket_stale": False, "cached": True}
    if not compute:
        return {
            **cache_id,
            "ok": False,
            "reason": "Reference beta is still computing.",
            "beta_ref": None,
            "beta_display_yield_bp_per_1m": None,
            "session_rows": [],
            "computed_at": now_utc.isoformat(),
        }

    dv01_table = load_dv01_table()
    session_rows: List[Dict[str, Any]] = []
    betas: List[float] = []
    window_start = _trades_window_start(trades_window, symbols)
    for start in starts:
        scoped = None
        if window_start is not None and window_start <= pd.Timestamp(start) + WINDOW_COVER_SLACK:
            scoped = {}
            end = globex_session_end(start)
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            for symbol in symbols:
                df = trades_window.get(symbol, pd.DataFrame())
                if df is None or df.empty:
                    scoped[symbol] = pd.DataFrame()
                    continue
                ts = pd.to_datetime(df["ts"], utc=True)
                scoped[symbol] = df.loc[(ts >= start_ts) & (ts < end_ts)].copy()
        row = compute_session_beta(
            start,
            price_symbol=price_symbol,
            aggression_symbols=symbols,
            dv01_adjust=dv01_adjust,
            dv01_table=dv01_table,
            trades_by_symbol=scoped,
        )
        session_rows.append(row)
        if row.get("ok") and row.get("beta") is not None and np.isfinite(float(row["beta"])):
            betas.append(float(row["beta"]))

    if len(betas) < 1:
        payload = {
            **cache_id,
            "ok": False,
            "reason": "No completed session produced a change-on-change slope.",
            "beta_ref": None,
            "beta_display_yield_bp_per_1m": None,
            "session_rows": session_rows,
            "computed_at": now_utc.isoformat(),
        }
        _save_entry(payload)
        return payload

    beta_ref = float(np.mean(betas))
    payload = {
        **cache_id,
        "ok": True,
        "reason": None,
        "beta_ref": beta_ref,
        "beta_display_yield_bp_per_1m": beta_to_yield_bp_per_1m(beta_ref, price_symbol, dv01_table),
        "n_sessions_used": len(betas),
        "session_rows": session_rows,
        "computed_at": now_utc.isoformat(),
        "positive": beta_ref > 0,
    }
    _save_entry(payload)
    logger.info(
        "V3 beta_ref %.6e from %s whole sessions ending %s for %s",
        beta_ref,
        len(betas),
        cache_id["last_session_end"],
        price_symbol,
    )
    return payload
