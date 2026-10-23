#!/usr/bin/env python3
"""Flask app for Aggression V1.2 — V1 panels plus provisional RANGE-caution monitor.

Isolated copy of V1. Manual-only on port 5056. Writes only under
workspace/analysis/aggression_v12_live/. Does not modify live V1/V2/V3.
"""

from __future__ import annotations

import logging
import sys
import threading
import time as time_module
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from flask import Flask, jsonify, render_template, request
except ImportError as exc:  # pragma: no cover - import-time deployment guard
    raise SystemExit("Flask is required. Install with: pip install flask") from exc

import pandas as pd

from scripts.aggression_dashboard_v12.core import (
    AggressionRequest,
    DEFAULT_BUCKET,
    EASTERN,
    SYMBOL_PARENT_MAP,
    aggregate_curve_aggression,
    build_aggression_dataset,
    default_start_time,
    fetch_curve_trades,
    fetch_price_series,
    load_dv01_table,
    normalize_symbol,
    parse_dashboard_datetime,
    price_series_from_trades,
    prune_trades_cache,
)
from scripts.aggression_dashboard_v12.live_buffer import LiveTradeBuffer
from scripts.aggression_dashboard_v12.hf_model import (
    apply_session_model,
    fit_session_model,
    fit_tenday_model,
)
from scripts.aggression_dashboard_v12.panels import build_dashboard_payload
from scripts.aggression_dashboard_v12.caution_store import CautionStore, STORE_DIR
from scripts.aggression_dashboard_v12.range_caution_context import (
    LABEL as CAUTION_LABEL,
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    RESEARCH_CONTEXT,
    SOFT_BREAKOUT_LABEL,
    SOFT_BREAKOUT_RESEARCH,
    STRATEGY_STATUS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTradeBuffer()
CAUTION_STORE = CautionStore(STORE_DIR)
V12_PORT = 5056
BACKFILL_REFRESH_SEC = 240.0
BACKFILL_OVERLAP_SEC = 900.0
TENDAY_BUSINESS_DAYS = 10
TENDAY_REFRESH_SEC = 3600.0
_LAST_CAUTION_PANEL: pd.DataFrame | None = None
_CAUTION_LOCK = threading.Lock()
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
_BACKFILL_THREAD: threading.Thread | None = None
_LAST_CACHE_PRUNE_DATE = None
_INTRADAY_FIT_LOCK = threading.Lock()
_INTRADAY_FIT: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
_TENDAY_LOCK = threading.Lock()
_TENDAY: Dict[str, Any] = {
    "trades": {},
    "errors": {},
    "covered_start": None,
    "covered_end": None,
    "fetched_at": None,
    "fit_date": None,
    "models": {},
    "error": None,
}
_TENDAY_THREAD: threading.Thread | None = None


def _ensure_live_buffer() -> None:
    """Start live streaming once per process."""
    LIVE_BUFFER.start()
    _ensure_backfill_thread()
    _ensure_tenday_thread()


def _ensure_backfill_thread() -> None:
    """Start the slow historical backfill refresher once per process."""
    global _BACKFILL_THREAD
    if _BACKFILL_THREAD and _BACKFILL_THREAD.is_alive():
        return
    _BACKFILL_THREAD = threading.Thread(target=_backfill_refresher, daemon=True)
    _BACKFILL_THREAD.start()


def _ensure_tenday_thread() -> None:
    """Start the 10-business-day model source-data refresher once per process."""
    global _TENDAY_THREAD
    if _TENDAY_THREAD and _TENDAY_THREAD.is_alive():
        return
    _TENDAY_THREAD = threading.Thread(target=_tenday_refresher, daemon=True)
    _TENDAY_THREAD.start()


def _tenday_window(now: datetime | None = None) -> Tuple[datetime, datetime, list[str]]:
    """Return a New York 01:00 -> now window covering the last 10 business days."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    dates = pd.bdate_range(end=now_et.date(), periods=TENDAY_BUSINESS_DAYS)
    start_et = datetime.combine(dates[0].date(), time(1, 0), tzinfo=EASTERN)
    return start_et.astimezone(timezone.utc), now_et.astimezone(timezone.utc), [
        str(x.date()) for x in dates
    ]


def _tenday_refresher() -> None:
    """Fetch 10-business-day trade data once per New York date."""
    while True:
        now_et = datetime.now(timezone.utc).astimezone(EASTERN)
        with _TENDAY_LOCK:
            fit_date = _TENDAY.get("fit_date")
        if fit_date == str(now_et.date()):
            time_module.sleep(TENDAY_REFRESH_SEC)
            continue
        start, end, dates = _tenday_window(now_et)
        try:
            logger.info("10-day fair-value source refresh: %s -> %s", start, end)
            trades, errors = fetch_curve_trades(tuple(SYMBOL_PARENT_MAP), start, end, cache=True)
            with _TENDAY_LOCK:
                _TENDAY.update({
                    "trades": {symbol: df.copy() for symbol, df in trades.items()},
                    "errors": errors,
                    "covered_start": start,
                    "covered_end": end,
                    "fetched_at": datetime.now(timezone.utc),
                    "fit_date": str(now_et.date()),
                    "dates": dates,
                    "models": {},
                    "error": None,
                })
            logger.info("10-day fair-value source stored for dates %s", dates)
        except Exception as exc:
            logger.exception("10-day fair-value source refresh failed")
            with _TENDAY_LOCK:
                _TENDAY["error"] = str(exc)
        time_module.sleep(TENDAY_REFRESH_SEC)


def _backfill_refresher() -> None:
    """Refresh session historical trades in the background."""
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
            logger.info("Background backfill refresh (%s): %s -> %s", fetch_kind, start, end)
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
                        merged[symbol] = pd.DataFrame(columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"])
                        front_contracts[symbol] = existing_front.get(symbol, [])
                _BACKFILL.update({
                    "trades": merged,
                    "errors": errors,
                    "front_contracts": front_contracts,
                    "covered_start": session_start,
                    "covered_end": end,
                    "fetched_at": datetime.now(timezone.utc),
                    "error": None,
                })
            logger.info("Background backfill stored through %s (%s)", end, fetch_kind)
        except Exception as exc:
            logger.exception("Background backfill refresh failed")
            with _BACKFILL_LOCK:
                _BACKFILL["error"] = str(exc)
        time_module.sleep(BACKFILL_REFRESH_SEC)


def _market_open(now: datetime | None = None) -> bool:
    """Approximate CME Treasury futures trading hours in New York time."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    weekday = now_et.weekday()  # Monday=0, Sunday=6
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


def _parse_payload(payload: Dict[str, Any]) -> AggressionRequest:
    raw_symbols = payload.get("aggression_symbols") or list(SYMBOL_PARENT_MAP)
    if isinstance(raw_symbols, str):
        raw_symbols = [raw_symbols]
    aggression_list = []
    for symbol in raw_symbols:
        try:
            aggression_list.append(normalize_symbol(symbol))
        except ValueError:
            logger.warning("Ignoring unsupported aggression symbol from client: %s", symbol)
    aggression_symbols = tuple(dict.fromkeys(aggression_list)) or tuple(SYMBOL_PARENT_MAP)
    try:
        price_symbol = normalize_symbol(payload.get("price_symbol") or "TY")
    except ValueError:
        logger.warning("Ignoring unsupported price symbol from client: %s", payload.get("price_symbol"))
        price_symbol = "TY"
    start = parse_dashboard_datetime(payload.get("start_iso") or default_start_time().isoformat())
    end = parse_dashboard_datetime(payload.get("end_iso") or datetime.now(timezone.utc).isoformat(), default_to_now=True)
    if end <= start:
        raise ValueError("End time must be after start time.")
    bucket = str(payload.get("bucket") or DEFAULT_BUCKET)
    try:
        lookback_hours = float(payload.get("lookback_hours") or 2.0)
    except (TypeError, ValueError):
        lookback_hours = 2.0
    lookback_hours = max(lookback_hours, 0.25)
    return AggressionRequest(
        aggression_symbols=aggression_symbols,
        price_symbol=price_symbol,
        start=start,
        end=end,
        bucket=bucket,
        dv01_adjust=bool(payload.get("dv01_adjust", True)),
        lookback_hours=lookback_hours,
    )


def _parse_mode(payload: Dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "live").strip().lower()
    return "historical" if mode == "historical" else "live"


def _get_background_backfill(req: AggressionRequest) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]:
    """Read the background backfill state without triggering a Databento pull."""
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
        logger.info("Request predates background backfill; one-off fetch %s -> %s", req.start, covered_start)
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


def _merge_historical_and_live(
    historical: Dict[str, pd.DataFrame],
    live: Dict[str, pd.DataFrame],
    symbols: Tuple[str, ...],
) -> Dict[str, pd.DataFrame]:
    """Append live-tail trades after each symbol's historical max timestamp."""
    merged: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        hist_df = historical.get(symbol, pd.DataFrame()).copy()
        live_df = live.get(symbol, pd.DataFrame()).copy()
        if not hist_df.empty:
            hist_df["ts"] = pd.to_datetime(hist_df["ts"], utc=True)
            hist_max = hist_df["ts"].max()
        else:
            hist_max = None
        if not live_df.empty:
            live_df["ts"] = pd.to_datetime(live_df["ts"], utc=True)
            if hist_max is not None:
                live_df = live_df[live_df["ts"] > hist_max].copy()
        frames = [df for df in (hist_df, live_df) if not df.empty]
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            combined["ts"] = pd.to_datetime(combined["ts"], utc=True)
            subset = ["ts", "price", "size", "side", "dashboard_symbol"]
            if "instrument_id" in combined.columns:
                subset.append("instrument_id")
            combined.drop_duplicates(subset=subset, inplace=True)
            combined.sort_values("ts", inplace=True)
            combined.reset_index(drop=True, inplace=True)
            combined.attrs["front_contracts"] = (
                hist_df.attrs.get("front_contracts", []) if not hist_df.empty else []
            ) or (
                live_df.attrs.get("front_contracts", []) if not live_df.empty else []
            )
            merged[symbol] = combined
        else:
            merged[symbol] = pd.DataFrame(columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"])
    return merged


def _live_status(mode: str, historical_lag_sec: float | None = None) -> Dict[str, Any]:
    status = LIVE_BUFFER.status()
    status.update({
        "mode": mode,
        "market_open": _market_open(),
        "historical_lag_sec": historical_lag_sec,
    })
    return status


def _build_live_price_series(
    trades_by_symbol: Dict[str, pd.DataFrame],
    mid_by_symbol: Dict[str, pd.DataFrame],
    req: AggressionRequest,
) -> pd.DataFrame:
    """Build a live price line from trade backfill overlaid with BBO mid."""
    grid_start = pd.Timestamp(req.start).floor(req.bucket)
    grid_end = pd.Timestamp(req.end).floor(req.bucket)
    if grid_end < grid_start:
        return pd.DataFrame(columns=["price"])
    grid = pd.date_range(start=grid_start, end=grid_end, freq=req.bucket, tz="UTC")
    if len(grid) == 0:
        return pd.DataFrame(columns=["price"])

    trade_df = price_series_from_trades(
        trades_by_symbol.get(req.price_symbol, pd.DataFrame()),
        bucket=req.bucket,
    )
    if not trade_df.empty:
        trade_series = trade_df["price"].reindex(grid).ffill()
    else:
        trade_series = pd.Series(index=grid, dtype=float)

    mid_df = mid_by_symbol.get(req.price_symbol, pd.DataFrame())
    if not mid_df.empty:
        mid = mid_df.copy()
        if not isinstance(mid.index, pd.DatetimeIndex):
            if "ts" in mid.columns:
                mid["ts"] = pd.to_datetime(mid["ts"], utc=True)
                mid.set_index("ts", inplace=True)
            else:
                mid.index = pd.to_datetime(mid.index, utc=True)
        mid_series = mid["price"].resample(req.bucket).last().reindex(grid).ffill()
    else:
        mid_series = pd.Series(index=grid, dtype=float)

    combined = trade_series.copy()
    combined.update(mid_series.dropna())
    combined = combined.ffill().dropna()
    return pd.DataFrame({"price": combined})


def _intraday_key(req: AggressionRequest) -> Tuple[Any, ...]:
    et_date = req.end.astimezone(EASTERN).date().isoformat()
    return (
        et_date,
        req.price_symbol,
        tuple(req.aggression_symbols),
        bool(req.dv01_adjust),
        req.bucket,
    )


def _build_intraday_model(
    req: AggressionRequest,
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
) -> Dict[str, Any]:
    key = _intraday_key(req)
    with _INTRADAY_FIT_LOCK:
        cached_meta = _INTRADAY_FIT.get(key)
    if cached_meta and cached_meta.get("ok"):
        model_df, meta = apply_session_model(flow_df, price_df, cached_meta)
        return {"data": model_df, "meta": meta}

    model_df, meta = fit_session_model(flow_df, price_df)
    if meta.get("ok"):
        frozen_meta = {
            "ok": True,
            "alpha": meta["alpha"],
            "beta": meta["beta"],
            "model": meta["model"],
            "fit_ts": meta.get("fit_ts"),
            "r2": meta.get("r2"),
            "valid_points": meta.get("valid_points"),
        }
        with _INTRADAY_FIT_LOCK:
            _INTRADAY_FIT[key] = frozen_meta
    return {"data": model_df, "meta": meta}


def _empty_tenday_model(req: AggressionRequest, reason: str) -> Dict[str, Any]:
    return {
        "data": pd.DataFrame(columns=["price", "predicted", "residual", "residual_zscore", "large_residual"]),
        "meta": {
            "ok": False,
            "reason": reason,
            "model": "10-business-day pooled daily-reset OLS",
            "refit_cadence": "once_per_day",
            "request": {
                "price_symbol": req.price_symbol,
                "aggression_symbols": list(req.aggression_symbols),
                "dv01_adjust": req.dv01_adjust,
            },
        },
    }


def _filter_by_et_dates(df: pd.DataFrame, allowed_dates: set[str]) -> pd.DataFrame:
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    et_dates = idx.tz_convert(EASTERN).date
    mask = [str(value) in allowed_dates for value in et_dates]
    return df.loc[mask].copy()


def _build_tenday_model(req: AggressionRequest) -> Dict[str, Any]:
    key = (
        _TENDAY.get("fit_date"),
        req.price_symbol,
        tuple(req.aggression_symbols),
        bool(req.dv01_adjust),
        req.bucket,
    )
    with _TENDAY_LOCK:
        fit_date = _TENDAY.get("fit_date")
        error = _TENDAY.get("error")
        if not fit_date:
            return _empty_tenday_model(req, error or "10-day source data is still loading.")
        cached = (_TENDAY.get("models") or {}).get(key)
        if cached:
            return {"data": cached["data"].copy(), "meta": dict(cached["meta"])}
        trades_snapshot = {
            symbol: df.copy()
            for symbol, df in (_TENDAY.get("trades") or {}).items()
            if symbol in req.aggression_symbols
        }
        errors = dict(_TENDAY.get("errors") or {})
        covered_start = _TENDAY.get("covered_start")
        covered_end = _TENDAY.get("covered_end")
        fetched_at = _TENDAY.get("fetched_at")
        business_dates = set(_TENDAY.get("dates") or [])

    if not trades_snapshot:
        return _empty_tenday_model(req, "No 10-day source trades are available for selected symbols.")

    dv01_table = load_dv01_table()
    flow_df, flow_meta = aggregate_curve_aggression(
        trades_snapshot,
        dv01_table=dv01_table,
        bucket=req.bucket,
        dv01_adjust=req.dv01_adjust,
        reset="daily",
    )
    price_df = price_series_from_trades(
        trades_snapshot.get(req.price_symbol, pd.DataFrame()),
        bucket=req.bucket,
    )
    if business_dates:
        flow_df = _filter_by_et_dates(flow_df, business_dates)
        price_df = _filter_by_et_dates(price_df, business_dates)
    model_df, meta = fit_tenday_model(flow_df, price_df)
    meta.update({
        "request": {
            "price_symbol": req.price_symbol,
            "aggression_symbols": list(req.aggression_symbols),
            "dv01_adjust": req.dv01_adjust,
            "bucket": req.bucket,
        },
        "source": {
            "covered_start": covered_start.isoformat() if covered_start else None,
            "covered_end": covered_end.isoformat() if covered_end else None,
            "fetched_at": fetched_at.isoformat() if fetched_at else None,
            "fetch_errors": errors,
            "flow": flow_meta,
            "business_dates": sorted(business_dates),
        },
    })
    result = {"data": model_df, "meta": meta}
    with _TENDAY_LOCK:
        models = _TENDAY.setdefault("models", {})
        models[key] = {"data": model_df.copy(), "meta": dict(meta)}
    return result


def _build_fair_value_models(
    req: AggressionRequest,
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    *,
    include_tenday: bool = True,
) -> Dict[str, Any]:
    fair_value = {"intraday": _build_intraday_model(req, flow_df, price_df)}
    if include_tenday:
        fair_value["tenday"] = _build_tenday_model(req)
    else:
        fair_value["tenday"] = _empty_tenday_model(req, "10-day model is only enabled in live mode.")
    return fair_value


def _build_historical_fair_value_models(
    req: AggressionRequest,
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
) -> Dict[str, Any]:
    model_df, meta = fit_session_model(flow_df, price_df)
    return {
        "intraday": {"data": model_df, "meta": meta},
        "tenday": _empty_tenday_model(req, "10-day model is only enabled in live mode."),
    }


def _apply_lookback(df: pd.DataFrame, cutoff: pd.Timestamp | None) -> pd.DataFrame:
    """Slice a datetime-indexed frame to the visible chart window."""
    if cutoff is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    mask = idx >= cutoff
    return df.loc[mask].copy()


def _lookback_cutoff(
    req: AggressionRequest,
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
) -> pd.Timestamp | None:
    latest_values = []
    for df in (flow_df, price_df):
        if not df.empty and isinstance(df.index, pd.DatetimeIndex):
            idx = df.index
            latest = idx.max()
            if latest.tzinfo is None:
                latest = latest.tz_localize("UTC")
            latest_values.append(latest)
    if not latest_values:
        return None
    latest_ts = max(latest_values)
    return latest_ts - pd.Timedelta(hours=float(req.lookback_hours))


def _apply_display_lookback(
    req: AggressionRequest,
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    meta: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    cutoff = _lookback_cutoff(req, flow_df, price_df)
    if cutoff is None:
        return flow_df, price_df, meta

    clipped_meta = dict(meta)
    models = clipped_meta.get("fair_value_models")
    if isinstance(models, dict):
        clipped_models: Dict[str, Any] = {}
        for name, model in models.items():
            model_copy = dict(model or {})
            data = model_copy.get("data")
            if isinstance(data, pd.DataFrame):
                model_copy["data"] = _apply_lookback(data, cutoff)
            clipped_models[name] = model_copy
        clipped_meta["fair_value_models"] = clipped_models
    clipped_meta["display_lookback"] = {
        "hours": float(req.lookback_hours),
        "cutoff": cutoff.isoformat(),
    }
    return _apply_lookback(flow_df, cutoff), _apply_lookback(price_df, cutoff), clipped_meta


def build_live_aggression_dataset(req: AggressionRequest) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Build dashboard data from Historical backfill plus AccessAPI live tail."""
    _ensure_live_buffer()
    req = replace(req, end=datetime.now(timezone.utc))
    dv01_table = load_dv01_table()
    historical, fetch_errors, backfill_state = _get_background_backfill(req)
    live = LIVE_BUFFER.get_trades(req.aggression_symbols, req.start, req.end)
    live_mid = LIVE_BUFFER.get_mid_series(req.aggression_symbols, req.start, req.end)
    trades_by_symbol = _merge_historical_and_live(historical, live, req.aggression_symbols)
    flow_df, flow_meta = aggregate_curve_aggression(
        trades_by_symbol,
        dv01_table=dv01_table,
        bucket=req.bucket,
        dv01_adjust=req.dv01_adjust,
        reset=req.reset,
    )
    price_df = _build_live_price_series(trades_by_symbol, live_mid, req)
    price_source = "accessapi_bbo_mid_live + databento_trade_backfill"
    historical_lag_sec = None
    if flow_meta.get("time_end"):
        try:
            end_ts = pd.Timestamp(flow_meta["time_end"]).to_pydatetime()
            historical_lag_sec = (datetime.now(timezone.utc) - end_ts).total_seconds()
        except Exception:
            historical_lag_sec = None
    meta = {
        "request": {
            "aggression_symbols": list(req.aggression_symbols),
            "price_symbol": req.price_symbol,
            "start": req.start.isoformat(),
            "end": req.end.isoformat(),
            "bucket": req.bucket,
            "dv01_adjust": req.dv01_adjust,
            "mode": "live",
        },
        "dv01_table": dv01_table,
        "fetch_errors": fetch_errors,
        "backfill": backfill_state,
        "flow": flow_meta,
        "price_rows": int(len(price_df)),
        "price_source": price_source,
        "live_status": _live_status("live", historical_lag_sec=historical_lag_sec),
    }
    meta["fair_value_models"] = _build_fair_value_models(req, flow_df, price_df)
    return flow_df, price_df, meta


@app.get("/")
def index() -> str:
    """Render the dashboard shell."""
    symbols = list(SYMBOL_PARENT_MAP)
    return render_template(
        "index.html",
        symbols=symbols,
        dv01_table=load_dv01_table(),
        default_start=default_start_time().astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M"),
    )


def _update_caution(flow_df: pd.DataFrame, price_df: pd.DataFrame) -> dict[str, Any]:
    """Build residual panel and update isolated caution episode store."""
    global _LAST_CAUTION_PANEL
    try:
        # Prefer full session series (pre-lookback) when available via live build;
        # callers pass display-trimmed frames which is acceptable for live monitoring.
        panel = CautionStore.build_panel(flow_df, price_df)
        with _CAUTION_LOCK:
            _LAST_CAUTION_PANEL = panel
            return CAUTION_STORE.update(panel)
    except Exception as exc:
        logger.exception("V1.2 caution update failed")
        return {
            "success": False,
            "error": str(exc),
            "strategy_status": STRATEGY_STATUS,
            "label": CAUTION_LABEL,
        }


@app.get("/api/config")
def api_config() -> Any:
    """Expose static dashboard config for clients."""
    return jsonify({
        "success": True,
        "version": "V1.2",
        "port": V12_PORT,
        "strategy_status": STRATEGY_STATUS,
        "caution_label": CAUTION_LABEL,
        "soft_breakout_label": SOFT_BREAKOUT_LABEL,
        "lifo_friendly_label": LIFO_FRIENDLY_LABEL,
        "symbols": list(SYMBOL_PARENT_MAP),
        "dv01_table": load_dv01_table(),
        "default_start_iso": default_start_time().isoformat(),
        "default_bucket": DEFAULT_BUCKET,
        "research_context": dict(RESEARCH_CONTEXT),
        "soft_breakout_research": dict(SOFT_BREAKOUT_RESEARCH),
        "lifo_friendly_research": dict(LIFO_FRIENDLY_RESEARCH),
        "isolation": {
            "store": str(STORE_DIR),
            "v1_v2_v3_modified": False,
            "manual_only": True,
            "no_sod": True,
        },
    })


@app.get("/api/live_status")
def api_live_status() -> Any:
    """Expose live-feed freshness for the UI banner."""
    _ensure_live_buffer()
    return jsonify(_live_status("live"))


@app.get("/api/range_caution")
def api_range_caution() -> Any:
    """Live RANGE-caution state, episodes, and observational stats."""
    with _CAUTION_LOCK:
        panel = _LAST_CAUTION_PANEL
        if panel is None or panel.empty:
            return jsonify(CAUTION_STORE.payload())
        return jsonify(CAUTION_STORE.update(panel))


@app.get("/api/caution_log")
def api_caution_log() -> Any:
    """Append-once caution episode log."""
    scope = str(request.args.get("scope") or "today")
    rows = CAUTION_STORE.read_log(scope=scope)
    return jsonify({"success": True, "scope": scope, "rows": rows, "n": len(rows)})


@app.post("/api/aggression")
def api_aggression() -> Any:
    """Fetch data, aggregate aggression, and return five panel payloads."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        mode = _parse_mode(payload)
        req = _parse_payload(payload)
        if mode == "live":
            flow_df, price_df, meta = build_live_aggression_dataset(req)
        else:
            flow_df, price_df, meta = build_aggression_dataset(req, cache=True)
            meta["request"]["mode"] = "historical"
            meta["live_status"] = _live_status("historical")
            meta["fair_value_models"] = _build_historical_fair_value_models(req, flow_df, price_df)
        if flow_df.empty:
            error_meta = dict(meta)
            error_meta.pop("fair_value_models", None)
            return jsonify({
                "success": False,
                "error": "No trade data returned for the selected symbols/window.",
                "meta": error_meta,
            }), 404
        # Update caution on full session frames before display trim when possible.
        caution_payload = _update_caution(flow_df, price_df)
        flow_df, price_df, meta = _apply_display_lookback(req, flow_df, price_df, meta)
        response = build_dashboard_payload(flow_df, price_df, meta)
        response["range_caution"] = caution_payload
        response["version"] = "V1.2"
        response["strategy_status"] = STRATEGY_STATUS
        return jsonify(response)
    except Exception as exc:
        logger.exception("Aggression V1.2 dashboard API failed")
        return jsonify({"success": False, "error": str(exc)}), 400


def main() -> None:
    """Run the local development server (manual-only; not SOD-wired)."""
    import argparse

    parser = argparse.ArgumentParser(description="Aggression V1.2 provisional RANGE-caution monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=V12_PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_live_buffer()
    logger.info("Starting Aggression V1.2 on %s:%s (%s)", args.host, args.port, STRATEGY_STATUS)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

