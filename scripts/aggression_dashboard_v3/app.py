#!/usr/bin/env python3
"""Aggression V3 — trade-to-trade six-state monitor.

Manual-only on port 5053. Writes only under workspace/analysis/aggression_v3_live/.
Does not edit, write, or restart V1 / V1.2 / V1.3 / V2.
"""

from __future__ import annotations

import logging
import queue as queue_module
import sys
import threading
import time as time_module
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from flask import Flask, Response, jsonify, render_template, request, stream_with_context
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Flask is required. Install with: pip install flask") from exc

import pandas as pd

from scripts.aggression_dashboard_v3.banner import build_banner, build_banner_series
from scripts.aggression_dashboard_v3.beta_ref import ensure_beta_ref
from scripts.aggression_dashboard_v3.core import (
    AggressionRequest,
    DEFAULT_AGGRESSION_SYMBOLS,
    DEFAULT_PRICE_SYMBOL,
    EASTERN,
    LIVE_STORE_DIR,
    SYMBOL_PARENT_MAP,
    apply_beta_ref_path,
    build_print_frame,
    clip_print_lookback,
    default_start_time,
    fetch_curve_trades,
    last_n_business_day_window,
    load_cached_trades_window,
    load_dv01_table,
    normalize_symbol,
    parse_dashboard_datetime,
    prune_trades_cache,
    session_labels,
    trading_day_start,
)
from scripts.aggression_dashboard_v3.live_buffer import LiveTradeBuffer
from scripts.aggression_dashboard_v3.live_publisher import HEARTBEAT_SEC, LivePublisher
from scripts.aggression_dashboard_v3.print_reconcile import reconcile_by_symbol
from scripts.aggression_dashboard_v3.panels import build_dashboard_payload, build_state_timeline_panel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTradeBuffer()
BACKFILL_REFRESH_SEC = 240.0
BACKFILL_OVERLAP_SEC = 900.0
TENDAY_REFRESH_SEC = 3600.0
LIVE_RESULT_CACHE_TTL_SEC = 1.0
STATUS_LABEL = "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE"

_BACKFILL_LOCK = threading.Lock()
_BACKFILL: Dict[str, Any] = {
    "trades": {},
    "errors": {},
    "front_contracts": {},
    "covered_start": None,
    "covered_end": None,
    "fetched_at": None,
    "error": None,
}
_TENDAY_LOCK = threading.Lock()
_TENDAY: Dict[str, Any] = {
    "trades": {},
    "errors": {},
    "covered_start": None,
    "covered_end": None,
    "fetched_at": None,
    "fit_date": None,
    "session_start": None,
    "error": None,
}
_LIVE_REBUILD_LOCK = threading.Lock()
_LIVE_RESULT_CACHE: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
_BACKFILL_THREAD: threading.Thread | None = None
_TENDAY_THREAD: threading.Thread | None = None
_LAST_CACHE_PRUNE_DATE = None
_BETA_WARM_LOCK = threading.Lock()
_BETA_WARM_KEYS: set[tuple] = set()
_BETA_HELD_KEYS: set[tuple] = set()
_TENDAY_PATH_LOCK = threading.Lock()
_TENDAY_PATH: Dict[str, Any] = {"key": None, "frame": pd.DataFrame(), "building": None}
_PUBLISHER: LivePublisher | None = None
_PUBLISHER_LOCK = threading.Lock()


def _beta_warm_key(price_symbol: str, aggression_symbols: Tuple[str, ...], dv01_adjust: bool) -> tuple:
    return (price_symbol, tuple(aggression_symbols), bool(dv01_adjust))


def _kick_beta_ref(
    price_symbol: str,
    aggression_symbols: Tuple[str, ...],
    dv01_adjust: bool,
    trades_window: Optional[Dict[str, pd.DataFrame]] = None,
) -> Optional[threading.Thread]:
    """Compute beta_ref once per key per process, then hold it until restart."""
    key = _beta_warm_key(price_symbol, aggression_symbols, dv01_adjust)
    with _BETA_WARM_LOCK:
        if key in _BETA_WARM_KEYS:
            return None
        _BETA_WARM_KEYS.add(key)
        refresh_stale = key not in _BETA_HELD_KEYS

    def _run() -> None:
        fresh = False
        try:
            payload = ensure_beta_ref(
                price_symbol=price_symbol,
                aggression_symbols=aggression_symbols,
                dv01_adjust=dv01_adjust,
                trades_window=trades_window,
                compute=True,
                refresh_stale=refresh_stale,
            )
            fresh = bool(payload.get("ok")) and not payload.get("bucket_stale")
        except Exception:
            logger.exception("V3 background beta_ref failed")
        finally:
            with _BETA_WARM_LOCK:
                _BETA_WARM_KEYS.discard(key)
                if fresh:
                    _BETA_HELD_KEYS.add(key)
            if fresh and _PUBLISHER is not None:
                _PUBLISHER.offer_history()

    thread = threading.Thread(target=_run, name="v3-beta-ref", daemon=True)
    thread.start()
    return thread


def _tenday_path_key(
    aggression_symbols: Tuple[str, ...],
    price_symbol: str,
    dv01_adjust: bool,
    beta_ref: Optional[float],
) -> tuple:
    beta_part = None if beta_ref is None else round(float(beta_ref), 16)
    return (tuple(aggression_symbols), price_symbol, bool(dv01_adjust), beta_part)


def _snapshot_tenday_path(key: tuple) -> pd.DataFrame:
    """Shared 10-session path frame. Callers must not modify it in place."""
    with _TENDAY_PATH_LOCK:
        if _TENDAY_PATH.get("key") != key:
            return pd.DataFrame()
        frame = _TENDAY_PATH.get("frame")
        return frame if frame is not None else pd.DataFrame()


def _kick_tenday_path(
    aggression_symbols: Tuple[str, ...],
    price_symbol: str,
    dv01_adjust: bool,
    beta_ref: Optional[float],
    dv01_table: Dict[str, float],
) -> None:
    if beta_ref is None:
        return
    key = _tenday_path_key(aggression_symbols, price_symbol, dv01_adjust, beta_ref)
    with _TENDAY_PATH_LOCK:
        if _TENDAY_PATH.get("key") == key:
            return
        if _TENDAY_PATH.get("building") == key:
            return
        _TENDAY_PATH["building"] = key

    def _run() -> None:
        published = False
        try:
            trades = _snapshot_tenday_trades(aggression_symbols)
            if trades:
                frame, _ = build_print_frame(
                    trades,
                    price_symbol=price_symbol,
                    dv01_table=dv01_table,
                    dv01_adjust=dv01_adjust,
                )
                if not frame.empty:
                    frame = _tag_sessions(frame)
                    frame["total_aggress"] = frame.groupby("session_id")["delta_a"].cumsum()
                    frame = apply_beta_ref_path(frame, float(beta_ref), session_col="session_id")
                    with _TENDAY_PATH_LOCK:
                        _TENDAY_PATH.update({"key": key, "frame": frame, "building": None})
                    published = True
        except Exception:
            logger.exception("V3 background 10-session path failed")
        finally:
            if not published:
                with _TENDAY_PATH_LOCK:
                    if _TENDAY_PATH.get("building") == key:
                        _TENDAY_PATH["building"] = None

    threading.Thread(target=_run, name="v3-tenday-path", daemon=True).start()


def _ensure_live_buffer() -> None:
    LIVE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_BUFFER.start()
    _ensure_backfill_thread()
    _ensure_tenday_thread()
    _kick_beta_ref(DEFAULT_PRICE_SYMBOL, DEFAULT_AGGRESSION_SYMBOLS, True)
    _ensure_publisher()


def _publisher_assemble(start: datetime, end: datetime):
    return _live_display_trades(DEFAULT_AGGRESSION_SYMBOLS, start, end)


def _publisher_cached_window(start: datetime, end: datetime):
    return load_cached_trades_window(DEFAULT_AGGRESSION_SYMBOLS, start, end)


def _publisher_peek_beta(now: datetime) -> Dict[str, Any]:
    return ensure_beta_ref(
        price_symbol=DEFAULT_PRICE_SYMBOL,
        aggression_symbols=DEFAULT_AGGRESSION_SYMBOLS,
        dv01_adjust=True,
        now=now,
        trades_window=_snapshot_tenday_trades(DEFAULT_AGGRESSION_SYMBOLS) or None,
        compute=False,
    )


def _publisher_kick_beta() -> None:
    _kick_beta_ref(
        DEFAULT_PRICE_SYMBOL,
        DEFAULT_AGGRESSION_SYMBOLS,
        True,
        trades_window=_snapshot_tenday_trades(DEFAULT_AGGRESSION_SYMBOLS) or None,
    )


def _publisher_tenday_path(beta_ref: Optional[float]) -> pd.DataFrame:
    key = _tenday_path_key(DEFAULT_AGGRESSION_SYMBOLS, DEFAULT_PRICE_SYMBOL, True, beta_ref)
    return _snapshot_tenday_path(key)


def _publisher_kick_tenday(beta_ref: Optional[float], dv01_table: Dict[str, float]) -> None:
    if beta_ref is None:
        return
    _kick_tenday_path(
        DEFAULT_AGGRESSION_SYMBOLS,
        DEFAULT_PRICE_SYMBOL,
        True,
        float(beta_ref),
        dv01_table,
    )


def _ensure_publisher() -> LivePublisher:
    global _PUBLISHER
    with _PUBLISHER_LOCK:
        if _PUBLISHER is None:
            _PUBLISHER = LivePublisher(
                assemble_trades=_publisher_assemble,
                load_cached_window=_publisher_cached_window,
                peek_beta=_publisher_peek_beta,
                kick_beta=_publisher_kick_beta,
                live_status=lambda: _live_status("live"),
                tenday_path=_publisher_tenday_path,
                kick_tenday=_publisher_kick_tenday,
                load_dv01=load_dv01_table,
            )
            LIVE_BUFFER.set_hooks(on_trade=_PUBLISHER.offer_trade, on_quote=_PUBLISHER.offer_quote)
            _PUBLISHER.start()
        return _PUBLISHER


def _ensure_backfill_thread() -> None:
    global _BACKFILL_THREAD
    if _BACKFILL_THREAD and _BACKFILL_THREAD.is_alive():
        return
    _BACKFILL_THREAD = threading.Thread(target=_backfill_refresher, daemon=True)
    _BACKFILL_THREAD.start()


def _ensure_tenday_thread() -> None:
    global _TENDAY_THREAD
    if _TENDAY_THREAD and _TENDAY_THREAD.is_alive():
        return
    _TENDAY_THREAD = threading.Thread(target=_tenday_refresher, daemon=True)
    _TENDAY_THREAD.start()


def _market_open(now: datetime | None = None) -> bool:
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    weekday = now_et.weekday()
    t = now_et.time()
    if weekday == 5:
        return False
    if weekday == 6:
        return t >= time(18, 0)
    if weekday == 4 and t >= time(17, 0):
        return False
    if time(17, 0) <= t < time(18, 0):
        return False
    return True


def _live_status(mode: str, historical_lag_sec: float | None = None) -> Dict[str, Any]:
    status = LIVE_BUFFER.status()
    status.update({
        "mode": mode,
        "market_open": _market_open(),
        "historical_lag_sec": historical_lag_sec,
        "label": STATUS_LABEL,
    })
    return status


def _as_utc_ts(value: datetime) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _copy_trade_window(
    source: Dict[str, pd.DataFrame],
    symbols: Tuple[str, ...],
    start: datetime,
    end: datetime,
) -> Dict[str, pd.DataFrame]:
    """Copy only prints in [start, end] so the live request never clones the full session."""
    start_ts = _as_utc_ts(start)
    end_ts = _as_utc_ts(end)
    out: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = source.get(symbol)
        if df is None or df.empty:
            out[symbol] = pd.DataFrame()
            continue
        ts = pd.to_datetime(df["ts"], utc=True)
        out[symbol] = df.loc[(ts >= start_ts) & (ts <= end_ts)].copy()
    return out


def _peek_backfill_window(
    symbols: Tuple[str, ...],
    start: datetime,
    end: datetime,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]:
    with _BACKFILL_LOCK:
        raw = dict(_BACKFILL.get("trades") or {})
        front_contracts = dict(_BACKFILL.get("front_contracts") or {})
        errors = dict(_BACKFILL.get("errors") or {})
        state = {
            "covered_start": _BACKFILL.get("covered_start").isoformat() if _BACKFILL.get("covered_start") else None,
            "covered_end": _BACKFILL.get("covered_end").isoformat() if _BACKFILL.get("covered_end") else None,
            "fetched_at": _BACKFILL.get("fetched_at").isoformat() if _BACKFILL.get("fetched_at") else None,
            "error": _BACKFILL.get("error"),
        }
    window = _copy_trade_window(raw, symbols, start, end)
    for symbol, df in window.items():
        if not df.empty:
            df.attrs["front_contracts"] = front_contracts.get(symbol, [])
    return window, errors, state


def _live_display_trades(
    symbols: Tuple[str, ...],
    start: datetime,
    end: datetime,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]:
    """Tape for [start, end]: live prints first, then Databento fills the holes."""
    historical, errors, state = _peek_backfill_window(symbols, start, end)
    live = LIVE_BUFFER.get_trades(symbols, start, end)
    trades = _merge_historical_and_live(historical, live, symbols)
    for symbol, df in trades.items():
        if df is None or df.empty:
            continue
        source = historical.get(symbol)
        if source is None or source.empty:
            source = live.get(symbol)
        df.attrs["front_contracts"] = source.attrs.get("front_contracts", []) if source is not None else []
    return trades, errors, state


def _merge_historical_and_live(
    historical: Dict[str, pd.DataFrame],
    live: Dict[str, pd.DataFrame],
    symbols: Tuple[str, ...],
) -> Dict[str, pd.DataFrame]:
    """Hole-only combine: keep live rows, add unmatched hist prints."""
    return reconcile_by_symbol({}, symbols, live=live, historical=historical)


def _get_background_backfill(req: AggressionRequest) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]:
    with _BACKFILL_LOCK:
        covered_start = _BACKFILL.get("covered_start")
        covered_end = _BACKFILL.get("covered_end")
        fetched_at = _BACKFILL.get("fetched_at")
        error = _BACKFILL.get("error")
        trades_snapshot = {
            symbol: df.copy()
            for symbol, df in (_BACKFILL.get("trades") or {}).items()
            if symbol in req.aggression_symbols
        }
        front_contracts = dict(_BACKFILL.get("front_contracts") or {})
        errors = dict(_BACKFILL.get("errors") or {})
    for symbol, df in trades_snapshot.items():
        if not df.empty:
            df.attrs["front_contracts"] = front_contracts.get(symbol, [])
    state = {
        "covered_start": covered_start.isoformat() if covered_start else None,
        "covered_end": covered_end.isoformat() if covered_end else None,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "error": error,
    }
    if covered_start is not None and req.start < covered_start:
        trades, extra_errors = fetch_curve_trades(req.aggression_symbols, req.start, covered_start, cache=True)
        for symbol, df in trades.items():
            frames = [x for x in (df, trades_snapshot.get(symbol, pd.DataFrame())) if not x.empty]
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined["ts"] = pd.to_datetime(combined["ts"], utc=True)
                combined.sort_values("ts", inplace=True)
                combined.drop_duplicates(subset=["ts", "price", "size", "side", "dashboard_symbol"], inplace=True)
                combined.reset_index(drop=True, inplace=True)
                trades_snapshot[symbol] = combined
        errors.update(extra_errors)
    return trades_snapshot, errors, state


def _backfill_covered_end(
    merged: Dict[str, pd.DataFrame],
    previous: Optional[datetime],
) -> Optional[datetime]:
    """Earliest of the symbols' last fetched prints, so the next fetch restarts inside Databento's range."""
    ends = [
        pd.Timestamp(df["ts"].max()).to_pydatetime()
        for df in merged.values()
        if df is not None and not df.empty and "ts" in df.columns
    ]
    return min(ends) if ends else previous


def _backfill_refresher() -> None:
    global _LAST_CACHE_PRUNE_DATE
    while True:
        now_et = datetime.now(timezone.utc).astimezone(EASTERN)
        if _LAST_CACHE_PRUNE_DATE != now_et.date():
            prune_trades_cache()
            _LAST_CACHE_PRUNE_DATE = now_et.date()
        session_start = default_start_time()
        end = datetime.now(timezone.utc)
        with _BACKFILL_LOCK:
            covered_end = _BACKFILL.get("covered_end")
        if covered_end is not None and covered_end >= session_start:
            start = max(session_start, covered_end - timedelta(seconds=BACKFILL_OVERLAP_SEC))
            fetch_kind = "incremental"
        else:
            start = session_start
            fetch_kind = "full"
        try:
            logger.info("V3 backfill refresh (%s): %s -> %s", fetch_kind, start, end)
            new_trades, errors = fetch_curve_trades(tuple(SYMBOL_PARENT_MAP), start, end, cache=True)
            with _BACKFILL_LOCK:
                existing = _BACKFILL.get("trades") or {}
                existing_front = _BACKFILL.get("front_contracts") or {}
                merged: Dict[str, pd.DataFrame] = {}
                front_contracts: Dict[str, Any] = {}
                for symbol in tuple(SYMBOL_PARENT_MAP):
                    old_df = existing.get(symbol, pd.DataFrame()).copy()
                    new_df = new_trades.get(symbol, pd.DataFrame()).copy()
                    frames = [df for df in (old_df, new_df) if not df.empty]
                    if frames:
                        df = pd.concat(frames, ignore_index=True)
                        df["ts"] = pd.to_datetime(df["ts"], utc=True)
                        df = df[df["ts"] >= session_start].copy()
                        subset = ["ts", "price", "size", "side", "dashboard_symbol"]
                        if "instrument_id" in df.columns:
                            subset.append("instrument_id")
                        df.drop_duplicates(subset=subset, inplace=True)
                        df.sort_values("ts", inplace=True)
                        df.reset_index(drop=True, inplace=True)
                        new_front = new_df.attrs.get("front_contracts", []) if not new_df.empty else []
                        front = new_front or existing_front.get(symbol, []) or old_df.attrs.get("front_contracts", [])
                        df.attrs["front_contracts"] = front
                        merged[symbol] = df
                        front_contracts[symbol] = front
                    else:
                        merged[symbol] = pd.DataFrame(
                            columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"]
                        )
                        front_contracts[symbol] = existing_front.get(symbol, [])
                _BACKFILL.update({
                    "trades": merged,
                    "errors": errors,
                    "front_contracts": front_contracts,
                    "covered_start": session_start,
                    "covered_end": _backfill_covered_end(merged, covered_end),
                    "fetched_at": datetime.now(timezone.utc),
                    "error": None,
                })
            if _PUBLISHER is not None:
                _PUBLISHER.offer_history()
        except Exception as exc:
            logger.exception("V3 backfill refresh failed")
            with _BACKFILL_LOCK:
                _BACKFILL["error"] = str(exc)
        time_module.sleep(BACKFILL_REFRESH_SEC)


def _tenday_refresher() -> None:
    while True:
        now = datetime.now(timezone.utc)
        session_start = default_start_time(now)
        now_et = now.astimezone(EASTERN)
        with _TENDAY_LOCK:
            fit_date = _TENDAY.get("fit_date")
            stored_session = _TENDAY.get("session_start")
        if fit_date == str(now_et.date()) and stored_session == session_start:
            time_module.sleep(TENDAY_REFRESH_SEC)
            continue
        start, end, dates = last_n_business_day_window(now)
        try:
            logger.info("V3 10-session source refresh: %s -> %s", start, end)
            trades, errors = fetch_curve_trades(tuple(SYMBOL_PARENT_MAP), start, end, cache=True)
            with _TENDAY_LOCK:
                _TENDAY.update({
                    "trades": trades,
                    "errors": errors,
                    "covered_start": start,
                    "covered_end": end,
                    "fetched_at": datetime.now(timezone.utc),
                    "fit_date": str(now_et.date()),
                    "session_start": session_start,
                    "dates": dates,
                    "error": None,
                })
        except Exception as exc:
            logger.exception("V3 10-session source refresh failed")
            with _TENDAY_LOCK:
                _TENDAY["error"] = str(exc)
        time_module.sleep(TENDAY_REFRESH_SEC)


def _snapshot_tenday_trades(symbols: Tuple[str, ...]) -> Dict[str, pd.DataFrame]:
    """Shared 10-session trade frames. Callers must not modify them in place."""
    with _TENDAY_LOCK:
        return {
            symbol: df
            for symbol, df in (_TENDAY.get("trades") or {}).items()
            if symbol in symbols
        }


def _tag_sessions(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    et = pd.DatetimeIndex(out.index)
    if et.tz is None:
        et = et.tz_localize("UTC")
    et = et.tz_convert(EASTERN)
    start_dates = et.normalize()
    start_dates = start_dates.where(et.hour >= 18, start_dates - pd.Timedelta(days=1))
    out["session_id"] = session_labels(pd.DatetimeIndex(start_dates))
    return out


def _parse_payload(payload: Dict[str, Any]) -> AggressionRequest:
    raw_symbols = payload.get("aggression_symbols") or list(DEFAULT_AGGRESSION_SYMBOLS)
    if isinstance(raw_symbols, str):
        raw_symbols = [raw_symbols]
    aggression_list = []
    for symbol in raw_symbols:
        try:
            aggression_list.append(normalize_symbol(symbol))
        except ValueError:
            logger.warning("Ignoring unsupported aggression symbol from client: %s", symbol)
    aggression_symbols = tuple(dict.fromkeys(aggression_list)) or DEFAULT_AGGRESSION_SYMBOLS
    try:
        price_symbol = normalize_symbol(payload.get("price_symbol") or DEFAULT_PRICE_SYMBOL)
    except ValueError:
        price_symbol = DEFAULT_PRICE_SYMBOL
    start = parse_dashboard_datetime(payload.get("start_iso") or default_start_time().isoformat())
    end = parse_dashboard_datetime(payload.get("end_iso") or datetime.now(timezone.utc).isoformat(), default_to_now=True)
    if end <= start:
        raise ValueError("End time must be after start time.")
    try:
        lookback_hours = float(payload.get("lookback_hours") or 2.0)
    except (TypeError, ValueError):
        lookback_hours = 2.0
    return AggressionRequest(
        aggression_symbols=aggression_symbols,
        price_symbol=price_symbol,
        start=start,
        end=end,
        dv01_adjust=bool(payload.get("dv01_adjust", True)),
        lookback_hours=max(lookback_hours, 0.25),
    )


def _parse_mode(payload: Dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "live").strip().lower()
    return "historical" if mode == "historical" else "live"


def _build_response(req: AggressionRequest, mode: str) -> Tuple[Dict[str, Any], int]:
    dv01_table = load_dv01_table()
    session_start = default_start_time(req.end if mode == "historical" else None)
    if mode == "live":
        req = replace(req, end=datetime.now(timezone.utc), start=session_start)
        _ensure_live_buffer()
        lookback_start = req.end - timedelta(hours=req.lookback_hours)
        built = time_module.monotonic()
        trades, fetch_errors, backfill_state = _live_display_trades(
            req.aggression_symbols,
            lookback_start,
            req.end,
        )
        live_status = _live_status("live")
        logger.info(
            "V3 live lookback %.2fh assembled in %.2fs",
            req.lookback_hours,
            time_module.monotonic() - built,
        )
        frame_start = lookback_start
    else:
        trades, fetch_errors = fetch_curve_trades(req.aggression_symbols, req.start, req.end, cache=True)
        backfill_state = {}
        live_status = _live_status("historical")
        frame_start = req.start

    frame, flow_meta = build_print_frame(
        trades,
        price_symbol=req.price_symbol,
        dv01_table=dv01_table,
        dv01_adjust=req.dv01_adjust,
        session_start=frame_start,
    )
    if frame.empty:
        return {
            "success": False,
            "error": "No trade data returned for the selected symbols/window.",
            "meta": {"request": {"mode": mode}, "flow": flow_meta, "live_status": live_status, "fetch_errors": fetch_errors},
            "flow_dominance": build_banner(
                frame,
                {},
                price_symbol=req.price_symbol,
                as_of=req.end,
                live_connected=live_status.get("connected") if mode == "live" else True,
            ),
            "flow_dominance_series": [],
            "panels": {"p1_states": build_state_timeline_panel([])},
        }, 404

    tenday_trades = _snapshot_tenday_trades(req.aggression_symbols)
    beta_payload = ensure_beta_ref(
        price_symbol=req.price_symbol,
        aggression_symbols=req.aggression_symbols,
        dv01_adjust=req.dv01_adjust,
        now=req.end,
        trades_window=tenday_trades or None,
        compute=False,
    )
    if not beta_payload.get("ok") or beta_payload.get("bucket_stale"):
        _kick_beta_ref(
            req.price_symbol,
            req.aggression_symbols,
            req.dv01_adjust,
            trades_window=tenday_trades or None,
        )
    beta_ref = beta_payload.get("beta_ref")
    path_beta = float(beta_ref) if beta_ref and beta_payload.get("ok") else 0.0
    session_path = apply_beta_ref_path(frame, path_beta)

    tenday_key = _tenday_path_key(req.aggression_symbols, req.price_symbol, req.dv01_adjust, beta_ref if beta_payload.get("ok") else None)
    tenday_frame = _snapshot_tenday_path(tenday_key)
    if tenday_frame.empty and beta_ref and beta_payload.get("ok"):
        _kick_tenday_path(
            req.aggression_symbols,
            req.price_symbol,
            req.dv01_adjust,
            float(beta_ref),
            dv01_table,
        )

    banner = build_banner(
        frame,
        beta_payload,
        price_symbol=req.price_symbol,
        as_of=req.end,
        live_connected=live_status.get("connected") if mode == "live" else True,
    )

    display = clip_print_lookback(frame, req.lookback_hours)
    display_path = clip_print_lookback(session_path, req.lookback_hours)
    meta = {
        "request": {
            "aggression_symbols": list(req.aggression_symbols),
            "price_symbol": req.price_symbol,
            "start": req.start.isoformat(),
            "end": req.end.isoformat(),
            "dv01_adjust": req.dv01_adjust,
            "lookback_hours": req.lookback_hours,
            "mode": mode,
            "clock": "trade-to-trade",
        },
        "dv01_table": dv01_table,
        "fetch_errors": fetch_errors,
        "backfill": backfill_state,
        "flow": flow_meta,
        "beta_ref": {
            "ok": beta_payload.get("ok"),
            "beta_ref": beta_payload.get("beta_ref"),
            "beta_display_yield_bp_per_1m": beta_payload.get("beta_display_yield_bp_per_1m"),
            "n_sessions_used": beta_payload.get("n_sessions_used"),
            "sessions": beta_payload.get("sessions"),
            "lookback_hours": beta_payload.get("lookback_hours"),
            "tod_bucket": beta_payload.get("tod_bucket"),
            "reason": beta_payload.get("reason"),
            "version": beta_payload.get("version"),
        },
        "live_status": live_status,
        "label": STATUS_LABEL,
    }
    response = build_dashboard_payload(display, display_path, tenday_frame, meta)
    response["flow_dominance"] = banner
    response["flow_dominance_series"] = build_banner_series(
        frame,
        beta_payload,
        price_symbol=req.price_symbol,
        as_of_index=display.index if not display.empty else None,
    )
    response["panels"]["p1_states"] = build_state_timeline_panel(response["flow_dominance_series"])
    return response, 200


def _cached_live(req: AggressionRequest) -> Tuple[Dict[str, Any], int]:
    """Live HTTP reads the shared picture. It does not rebuild per request."""
    _ensure_live_buffer()
    snapshot = _ensure_publisher().last_snapshot()
    if snapshot and snapshot.get("success"):
        return snapshot, 200
    banner = (snapshot or {}).get("flow_dominance") if snapshot else None
    return {
        "success": False,
        "error": (snapshot or {}).get("reason") or "Live view is streamed at GET /api/live/stream.",
        "flow_dominance": banner,
        "flow_dominance_series": [],
        "panels": {"p1_states": build_state_timeline_panel([])},
        "meta": {"request": {"mode": "live", "clock": "trade-to-trade"}, "live_status": _live_status("live")},
    }, 202


@app.after_request
def _no_cache_live_assets(response: Response) -> Response:
    path = request.path or ""
    if path.endswith("/dashboard.js") or path == "/api/live/stream":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        symbols=list(SYMBOL_PARENT_MAP),
        default_aggression_symbols=list(DEFAULT_AGGRESSION_SYMBOLS),
        dv01_table=load_dv01_table(),
        default_start=default_start_time().astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M"),
        status_label=STATUS_LABEL,
    )


@app.get("/api/config")
def api_config() -> Any:
    return jsonify({
        "symbols": list(SYMBOL_PARENT_MAP),
        "default_aggression_symbols": list(DEFAULT_AGGRESSION_SYMBOLS),
        "default_price_symbol": DEFAULT_PRICE_SYMBOL,
        "dv01_table": load_dv01_table(),
        "default_start_iso": default_start_time().isoformat(),
        "label": STATUS_LABEL,
    })


@app.get("/api/live_status")
def api_live_status() -> Any:
    _ensure_live_buffer()
    status = dict(_live_status("live"))
    publisher = _PUBLISHER
    if publisher is not None:
        status["stream_lag_sec"] = publisher.last_stream_lag_sec
        status["stream_resyncs"] = publisher.resyncs
        with publisher._subs_lock:
            status["stream_subscribers"] = len(publisher._subs)
    return jsonify(status)


@app.get("/api/live/stream")
def api_live_stream() -> Response:
    publisher = _ensure_publisher()
    incoming = publisher.subscribe()

    def generate():
        try:
            yield "retry: 5000\n: connected\n" + (" " * 2048) + "\n\n"
            while True:
                try:
                    yield incoming.get(timeout=HEARTBEAT_SEC)
                except queue_module.Empty:
                    yield ": heartbeat\n\n"
        finally:
            publisher.unsubscribe(incoming)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/aggression")
def api_aggression() -> Any:
    try:
        payload = request.get_json(force=True, silent=False) or {}
        mode = _parse_mode(payload)
        req = _parse_payload(payload)
        if mode == "live":
            body, status_code = _cached_live(req)
        else:
            body, status_code = _build_response(req, "historical")
        return jsonify(body), status_code
    except Exception as exc:
        logger.exception("V3 aggression API failed")
        return jsonify({"success": False, "error": str(exc)}), 400


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Aggression V3 trade-to-trade monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5053)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    _ensure_live_buffer()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
