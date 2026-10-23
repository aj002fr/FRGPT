#!/usr/bin/env python3
"""Flask app for the interactive curve aggression dashboard."""

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

from scripts.aggression_dashboard.core import (
    AggressionRequest,
    DEFAULT_BUCKET,
    EASTERN,
    SYMBOL_PARENT_MAP,
    aggregate_curve_aggression,
    aggregate_tick_aggression,
    attach_last_traded_price,
    build_aggression_dataset,
    combine_symbol_minute_bars,
    default_start_time,
    fetch_curve_trades,
    fetch_price_series,
    load_dv01_table,
    normalize_symbol,
    parse_dashboard_datetime,
    price_series_from_trades,
    prune_trades_cache,
    printed_minute_bars,
    trades_to_symbol_minute_bars,
    upsert_minute_bars,
)
from scripts.aggression_dashboard.live_buffer import LiveTradeBuffer
from scripts.aggression_dashboard.hf_model import (
    apply_session_model,
    fit_session_model,
    fit_tenday_model,
)
from scripts.aggression_dashboard.flow_dominance_context import (
    build_flow_dominance_context,
    build_flow_dominance_context_and_series,
    clip_flow_dominance_series,
)
from scripts.aggression_v1_v27_banner_alerts.store import record_v1_banner_change
from scripts.aggression_dashboard.flow_history import (
    backfill_from_trade_cache,
    history_status,
    load_completed_symbol_bars,
    persist_completed_bars,
    query_flow_history,
)
from scripts.aggression_dashboard.panels import build_dashboard_payload

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
TENDAY_BUSINESS_DAYS = 10
TENDAY_REFRESH_SEC = 3600.0
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
    "bars": {},
    "errors": {},
    "covered_start": None,
    "covered_end": None,
    "fetched_at": None,
    "fit_date": None,
    "models": {},
    "error": None,
}
_TENDAY_THREAD: threading.Thread | None = None
_LIVE_REBUILD_LOCK = threading.Lock()
_LIVE_REBUILDS: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
LIVE_RESULT_CACHE_TTL_SEC = 1.0
_LIVE_RESULT_CACHE: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
LIVE_PUBLISH_SEC = 1.0
BACKFILL_TICK_TAIL_SEC = 1200.0
P1_AXIS_DEADBAND = 0.02
_P1_AXIS_LOCK = threading.Lock()
_P1_AXIS_LAST: Dict[Tuple[Any, ...], list] = {}
_SESSION_1M_LOCK = threading.Lock()
_SESSION_1M: Dict[str, Any] = {
    "session_start": None,
    "rebuild_from": None,
    "refold_tail": False,
    "bars": {},
    "per_symbol": {},
    "front_contracts": {},
    "ready": False,
    "error": None,
    "updated_at": None,
}
_PUBLISH_THREAD: threading.Thread | None = None
_FLOW_HISTORY_THREAD: threading.Thread | None = None
_FLOW_HISTORY_LOCK = threading.Lock()
_FLOW_HISTORY_WATERMARK = None
_SESSION_TICKS_LOCK = threading.Lock()
_SESSION_TICKS: Dict[str, Any] = {
    "session_start": None,
    "trades": {},
}


def _ensure_live_buffer() -> None:
    """Start live streaming once per process."""
    LIVE_BUFFER.start()
    _ensure_backfill_thread()
    _ensure_tenday_thread()
    _ensure_publish_thread()
    _ensure_flow_history_thread()


def _ensure_flow_history_thread() -> None:
    """Seed durable buy/sell history from the existing trade cache once."""
    global _FLOW_HISTORY_THREAD
    if _FLOW_HISTORY_THREAD and _FLOW_HISTORY_THREAD.is_alive():
        return
    _FLOW_HISTORY_THREAD = threading.Thread(target=_flow_history_backfill, daemon=True)
    _FLOW_HISTORY_THREAD.start()


def _flow_history_backfill() -> None:
    """Build missing history from parquet cache, then fold in-memory live bars.

    Ten-day Databento bars stay in memory as model input only; persisting them would
    overwrite minutes written live from AccessAPI.
    """
    try:
        stats = backfill_from_trade_cache()
        logger.info("V1 flow history cache backfill %s", stats)
    except Exception:
        logger.exception("V1 flow history cache backfill failed")
    try:
        with _SESSION_1M_LOCK:
            session_bars = {
                symbol: df.copy()
                for symbol, df in (_SESSION_1M.get("bars") or {}).items()
            }
            forming = _SESSION_1M.get("rebuild_from")
        if session_bars:
            persist_completed_bars(session_bars, before=forming, source="live")
    except Exception:
        logger.exception("V1 flow history in-memory seed failed")


def _ensure_publish_thread() -> None:
    """Start the 1-minute session publisher once per process."""
    global _PUBLISH_THREAD
    if _PUBLISH_THREAD and _PUBLISH_THREAD.is_alive():
        return
    _PUBLISH_THREAD = threading.Thread(target=_live_publisher, daemon=True)
    _PUBLISH_THREAD.start()


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
    """Return a trading-day window covering the last 10 business days."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    dates = pd.bdate_range(end=now_et.date(), periods=TENDAY_BUSINESS_DAYS)
    start_et = datetime.combine(dates[0].date(), time(18, 0), tzinfo=EASTERN) - timedelta(days=1)
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
            dv01_table = load_dv01_table()
            bars = {}
            for symbol, df in trades.items():
                bar, _stats = trades_to_symbol_minute_bars(
                    df,
                    symbol,
                    dv01_unit=float(dv01_table.get(symbol, 1.0)),
                    bucket=DEFAULT_BUCKET,
                )
                bars[symbol] = bar
            with _TENDAY_LOCK:
                _TENDAY.update({
                    "trades": {},
                    "bars": bars,
                    "errors": errors,
                    "covered_start": start,
                    "covered_end": end,
                    "fetched_at": datetime.now(timezone.utc),
                    "fit_date": str(now_et.date()),
                    "dates": dates,
                    "models": {},
                    "error": None,
                })
            logger.info("10-day fair-value 1m bars stored in memory for dates %s; raw ticks dropped", dates)
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
            with _SESSION_1M_LOCK:
                _SESSION_1M["refold_tail"] = True
        except Exception as exc:
            logger.exception("Background backfill refresh failed")
            with _BACKFILL_LOCK:
                _BACKFILL["error"] = str(exc)
        time_module.sleep(BACKFILL_REFRESH_SEC)


def _trim_backfill_tick_tail(session_start: datetime) -> None:
    """Keep only a short backfill tail after ticks have been folded into 1m bars."""
    cutoff = max(
        session_start,
        datetime.now(timezone.utc) - timedelta(seconds=BACKFILL_TICK_TAIL_SEC),
    )
    with _BACKFILL_LOCK:
        trimmed: Dict[str, pd.DataFrame] = {}
        for symbol, df in (_BACKFILL.get("trades") or {}).items():
            if df is None or df.empty:
                trimmed[symbol] = df if df is not None else pd.DataFrame()
                continue
            ts = pd.to_datetime(df["ts"], utc=True)
            kept = df.loc[ts >= cutoff].copy()
            kept.attrs["front_contracts"] = df.attrs.get("front_contracts", [])
            trimmed[symbol] = kept
        _BACKFILL["trades"] = trimmed


def _reset_session_minute_store(session_start: datetime) -> None:
    global _FLOW_HISTORY_WATERMARK
    _SESSION_1M.update({
        "session_start": session_start,
        "rebuild_from": session_start,
        "refold_tail": False,
        "bars": {},
        "per_symbol": {},
        "front_contracts": {},
        "ready": False,
        "error": None,
        "updated_at": None,
    })
    with _FLOW_HISTORY_LOCK:
        _FLOW_HISTORY_WATERMARK = None
    with _SESSION_TICKS_LOCK:
        _SESSION_TICKS.update({"session_start": session_start, "trades": {}})


def _append_session_ticks(merged: Dict[str, pd.DataFrame], session_start: datetime) -> None:
    """Append prints into the session tick tape. Dedupes; does not drop older prints."""
    start_ts = pd.Timestamp(session_start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    else:
        start_ts = start_ts.tz_convert("UTC")
    with _SESSION_TICKS_LOCK:
        if _SESSION_TICKS.get("session_start") != session_start:
            _SESSION_TICKS.update({"session_start": session_start, "trades": {}})
        stored = dict(_SESSION_TICKS.get("trades") or {})
        for symbol, incoming in merged.items():
            if incoming is None or incoming.empty:
                continue
            new = incoming.copy()
            new["ts"] = pd.to_datetime(new["ts"], utc=True)
            old = stored.get(symbol, pd.DataFrame())
            frames = [df for df in (old, new) if df is not None and not df.empty]
            if not frames:
                continue
            combined = pd.concat(frames, ignore_index=True)
            combined["ts"] = pd.to_datetime(combined["ts"], utc=True)
            subset = [col for col in ("ts", "price", "size", "side", "dashboard_symbol") if col in combined.columns]
            combined.drop_duplicates(subset=subset, inplace=True)
            combined = combined.loc[combined["ts"] >= start_ts].copy()
            combined.sort_values("ts", inplace=True)
            combined.reset_index(drop=True, inplace=True)
            stored[symbol] = combined
        _SESSION_TICKS["trades"] = stored


def _snapshot_session_ticks(symbols: Tuple[str, ...]) -> Dict[str, pd.DataFrame]:
    with _SESSION_TICKS_LOCK:
        return {
            symbol: df.copy()
            for symbol, df in (_SESSION_TICKS.get("trades") or {}).items()
            if symbol in symbols
        }


def _tick_flow_for_request(
    req: AggressionRequest,
    trades_by_symbol: Dict[str, pd.DataFrame] | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Session print cumsum plus last traded price of the selected price symbol."""
    if trades_by_symbol is None:
        keys = tuple(dict.fromkeys([*req.aggression_symbols, req.price_symbol]))
        trades_by_symbol = _snapshot_session_ticks(keys)
    aggress = {
        symbol: trades_by_symbol.get(symbol, pd.DataFrame())
        for symbol in req.aggression_symbols
    }
    tick_df, tick_meta = aggregate_tick_aggression(
        aggress,
        dv01_table=load_dv01_table(),
        dv01_adjust=req.dv01_adjust,
    )
    return attach_last_traded_price(
        tick_df,
        trades_by_symbol.get(req.price_symbol, pd.DataFrame()),
    ), tick_meta


def _trades_on_or_after(df: pd.DataFrame, start: datetime) -> pd.DataFrame:
    """Keep prints at or after start and preserve front-contract attrs."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    start_ts = pd.Timestamp(start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    else:
        start_ts = start_ts.tz_convert("UTC")
    out = out.loc[out["ts"] >= start_ts].copy()
    out.attrs["front_contracts"] = df.attrs.get("front_contracts", [])
    return out


def _heal_session_bars_from_history(
    bars: Dict[str, pd.DataFrame],
    *,
    session_start: datetime,
    forming_start: datetime,
    symbols: Tuple[str, ...],
) -> Dict[str, pd.DataFrame]:
    """Fill in-memory holes from completed sqlite bars. Does not overwrite prints."""
    try:
        stored = load_completed_symbol_bars(symbols, start=session_start, end=forming_start)
    except Exception:
        logger.exception("V1 session-bar history heal failed")
        return bars
    healed = dict(bars)
    for symbol in symbols:
        hist = stored.get(symbol)
        if hist is None or hist.empty:
            continue
        current = healed.get(symbol, pd.DataFrame())
        if current is None or current.empty:
            healed[symbol] = hist.copy()
            continue
        current = current.copy()
        current.index = pd.to_datetime(current.index, utc=True)
        missing = hist.loc[~hist.index.isin(current.index)]
        fill = missing
        if "trade_count" in current.columns:
            zeros = current.index[current["trade_count"].fillna(0) <= 0]
            better = hist.loc[hist.index.isin(zeros) & (hist["trade_count"] > 0)]
            if not better.empty:
                fill = pd.concat([fill, better])
        if fill.empty:
            continue
        healed[symbol] = upsert_minute_bars(current, printed_minute_bars(fill))
    return healed


def _ingest_session_minute_bars() -> None:
    """Fold new session prints into the in-memory 1-minute store."""
    session_start = default_start_time()
    end = datetime.now(timezone.utc)
    forming_start = pd.Timestamp(end).tz_convert("UTC").floor("min").to_pydatetime()
    with _SESSION_1M_LOCK:
        if _SESSION_1M.get("session_start") != session_start:
            _reset_session_minute_store(session_start)
        rebuild_from = _SESSION_1M.get("rebuild_from") or session_start
        existing_bars = {
            symbol: df.copy()
            for symbol, df in (_SESSION_1M.get("bars") or {}).items()
        }
        existing_front = dict(_SESSION_1M.get("front_contracts") or {})
        existing_per_symbol = dict(_SESSION_1M.get("per_symbol") or {})
        have_bars = any(not df.empty for df in existing_bars.values())
        need_full = rebuild_from == session_start or not have_bars
        need_tail = bool(_SESSION_1M.pop("refold_tail", False))

    if need_full:
        tick_start = session_start
    elif need_tail:
        tick_start = max(session_start, forming_start - timedelta(seconds=BACKFILL_OVERLAP_SEC))
    else:
        tick_start = forming_start

    req = AggressionRequest(
        aggression_symbols=tuple(SYMBOL_PARENT_MAP),
        price_symbol="TY",
        start=tick_start,
        end=end,
    )
    historical, _errors, _state = _get_background_backfill(req)
    historical = {
        symbol: _trades_on_or_after(df, tick_start)
        for symbol, df in historical.items()
    }
    live = LIVE_BUFFER.get_trades(req.aggression_symbols, tick_start, end)
    merged = _merge_historical_and_live(historical, live, req.aggression_symbols)
    if need_full or need_tail:
        full_req = replace(req, start=session_start)
        raw_hist, _, _ = _get_background_backfill(full_req)
        live_all = LIVE_BUFFER.get_trades(req.aggression_symbols, session_start, end)
        _append_session_ticks(
            _merge_historical_and_live(raw_hist, live_all, req.aggression_symbols),
            session_start,
        )
    else:
        _append_session_ticks(merged, session_start)
    dv01_table = load_dv01_table()
    bars: Dict[str, pd.DataFrame] = {}
    per_symbol: Dict[str, Any] = {}
    front_contracts: Dict[str, Any] = dict(existing_front)
    for symbol in tuple(SYMBOL_PARENT_MAP):
        incoming, stats = trades_to_symbol_minute_bars(
            merged.get(symbol, pd.DataFrame()),
            symbol,
            dv01_unit=float(dv01_table.get(symbol, 1.0)),
            bucket=DEFAULT_BUCKET,
        )
        incoming = printed_minute_bars(incoming)
        if not incoming.empty and not need_full:
            start_ts = pd.Timestamp(tick_start)
            if start_ts.tzinfo is None:
                start_ts = start_ts.tz_localize("UTC")
            else:
                start_ts = start_ts.tz_convert("UTC")
            incoming = incoming.loc[incoming.index >= start_ts]
        bars[symbol] = upsert_minute_bars(existing_bars.get(symbol, pd.DataFrame()), incoming)
        if not incoming.empty:
            per_symbol[symbol] = {
                **stats,
                "trade_count": int(bars[symbol]["trade_count"].sum()) if not bars[symbol].empty else 0,
                "front_contracts": stats.get("front_contracts") or existing_front.get(symbol, []),
            }
            front_contracts[symbol] = per_symbol[symbol]["front_contracts"]
        else:
            old = existing_per_symbol.get(symbol, {"trade_count": 0})
            per_symbol[symbol] = old
            if not bars[symbol].empty and "trade_count" in bars[symbol].columns:
                per_symbol[symbol] = {
                    **old,
                    "trade_count": int(bars[symbol]["trade_count"].sum()),
                }
    bars = _heal_session_bars_from_history(
        bars,
        session_start=session_start,
        forming_start=forming_start,
        symbols=tuple(SYMBOL_PARENT_MAP),
    )
    with _SESSION_1M_LOCK:
        if _SESSION_1M.get("session_start") != session_start:
            return
        _SESSION_1M.update({
            "bars": bars,
            "per_symbol": per_symbol,
            "front_contracts": front_contracts,
            "rebuild_from": forming_start,
            "ready": any(not df.empty for df in bars.values()),
            "error": None,
            "updated_at": end,
        })
    _persist_session_flow_history(bars, forming_start)
    if any(not df.empty for df in bars.values()) and not need_full:
        _trim_backfill_tick_tail(session_start)


def _persist_session_flow_history(bars: Dict[str, Any], forming_start: datetime) -> None:
    """Append newly completed session minutes to the durable buy/sell store."""
    global _FLOW_HISTORY_WATERMARK
    with _FLOW_HISTORY_LOCK:
        if _FLOW_HISTORY_WATERMARK == forming_start:
            return
        after = _FLOW_HISTORY_WATERMARK
        _FLOW_HISTORY_WATERMARK = forming_start
    persist_completed_bars(bars, before=forming_start, after=after, source="live")


def _live_publisher() -> None:
    """Refresh session 1-minute bars once per second for all live tabs."""
    while True:
        try:
            _ingest_session_minute_bars()
        except Exception:
            logger.exception("Live 1-minute publisher failed")
            with _SESSION_1M_LOCK:
                _SESSION_1M["error"] = "publisher_failed"
        time_module.sleep(LIVE_PUBLISH_SEC)


def _session_bars_ready() -> bool:
    with _SESSION_1M_LOCK:
        return bool(_SESSION_1M.get("ready"))


def _dataset_from_session_bars(req: AggressionRequest) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]] | None:
    """Build live flow/price from stored 1-minute bars. None if the store cannot serve this request."""
    if req.bucket != DEFAULT_BUCKET:
        return None
    session_start = default_start_time()
    if abs((req.start - session_start).total_seconds()) > 60:
        return None
    with _SESSION_1M_LOCK:
        if not _SESSION_1M.get("ready"):
            return None
        bars = {
            symbol: df.copy()
            for symbol, df in (_SESSION_1M.get("bars") or {}).items()
            if symbol in req.aggression_symbols
        }
        per_symbol = {
            symbol: dict(stats)
            for symbol, stats in (_SESSION_1M.get("per_symbol") or {}).items()
            if symbol in req.aggression_symbols
        }
        stored_start = _SESSION_1M.get("session_start")
    if stored_start != session_start or not bars:
        return None
    flow_df, flow_meta = combine_symbol_minute_bars(
        bars,
        dv01_adjust=req.dv01_adjust,
        reset=req.reset,
        per_symbol=per_symbol,
        bucket=req.bucket,
    )
    tick_df, tick_meta = _tick_flow_for_request(req)
    req_end = datetime.now(timezone.utc)
    live_mid = LIVE_BUFFER.get_mid_series((req.price_symbol,), req.start, req_end)
    price_src = bars.get(req.price_symbol, pd.DataFrame())
    trade_price = (
        pd.DataFrame({"price": price_src["trade_price"]})
        if not price_src.empty and "trade_price" in price_src.columns
        else pd.DataFrame(columns=["price"])
    )
    price_df = _overlay_live_mids(trade_price, live_mid.get(req.price_symbol, pd.DataFrame()), req)
    if flow_meta.get("time_end"):
        try:
            end_ts = pd.Timestamp(flow_meta["time_end"]).to_pydatetime()
            historical_lag_sec = (datetime.now(timezone.utc) - end_ts).total_seconds()
        except Exception:
            historical_lag_sec = None
    else:
        historical_lag_sec = None
    live_status = _live_status("live", historical_lag_sec=historical_lag_sec)
    meta = {
        "request": {
            "aggression_symbols": list(req.aggression_symbols),
            "price_symbol": req.price_symbol,
            "start": req.start.isoformat(),
            "end": req_end.isoformat(),
            "bucket": req.bucket,
            "dv01_adjust": req.dv01_adjust,
            "mode": "live",
        },
        "dv01_table": load_dv01_table(),
        "fetch_errors": {},
        "backfill": {"source": "session_1m_bars"},
        "flow": {**flow_meta, **({
            "total_aggress_final": tick_meta.get("total_aggress_final", flow_meta.get("total_aggress_final")),
            "formula": tick_meta.get("formula", flow_meta.get("formula")),
            "bucket_interval": "print",
        } if tick_meta.get("trade_count") else {})},
        "tick_flow": tick_df,
        "tick_flow_meta": tick_meta,
        "price_rows": int(len(price_df)),
        "price_source": f"{live_status.get('source', 'accessapi')}_tbbo_live + session_1m_bars",
        "live_status": live_status,
    }
    meta["fair_value_models"] = _build_fair_value_models(req, flow_df, price_df)
    return flow_df, price_df, meta


def _overlay_live_mids(
    trade_price: pd.DataFrame,
    mid_df: pd.DataFrame,
    req: AggressionRequest,
) -> pd.DataFrame:
    """Keep the 1-minute trade-price path and overlay recent BBO mids."""
    if trade_price.empty and (mid_df is None or mid_df.empty):
        return pd.DataFrame(columns=["price"])
    series = trade_price["price"] if not trade_price.empty else pd.Series(dtype=float)
    if mid_df is not None and not mid_df.empty:
        mid = mid_df.copy()
        if not isinstance(mid.index, pd.DatetimeIndex):
            if "ts" in mid.columns:
                mid["ts"] = pd.to_datetime(mid["ts"], utc=True)
                mid.set_index("ts", inplace=True)
            else:
                mid.index = pd.to_datetime(mid.index, utc=True)
        mid_series = mid["price"].resample(req.bucket).last()
        series = series.combine_first(mid_series)
        series.update(mid_series.dropna())
    series = series.sort_index().ffill().dropna()
    return pd.DataFrame({"price": series})


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
        bars_snapshot = {
            symbol: df.copy()
            for symbol, df in (_TENDAY.get("bars") or {}).items()
            if symbol in req.aggression_symbols
        }
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

    if bars_snapshot:
        flow_df, flow_meta = combine_symbol_minute_bars(
            bars_snapshot,
            dv01_adjust=req.dv01_adjust,
            reset="daily",
            bucket=req.bucket,
        )
        price_src = bars_snapshot.get(req.price_symbol, pd.DataFrame())
        if price_src.empty or "trade_price" not in price_src.columns:
            price_df = pd.DataFrame(columns=["price"])
        else:
            price_df = price_src[["trade_price"]].rename(columns={"trade_price": "price"})
    elif trades_snapshot:
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
    else:
        return _empty_tenday_model(req, "No 10-day source trades are available for selected symbols.")
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
    extra_df: pd.DataFrame | None = None,
) -> pd.Timestamp | None:
    latest_values = []
    for df in (flow_df, price_df, extra_df):
        if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
            continue
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
    tick_df = meta.get("tick_flow") if isinstance(meta.get("tick_flow"), pd.DataFrame) else None
    cutoff = _lookback_cutoff(req, flow_df, price_df, tick_df)
    if cutoff is None:
        return flow_df, price_df, meta

    clipped_meta = dict(meta)
    if isinstance(tick_df, pd.DataFrame) and not tick_df.empty:
        clipped_meta["tick_flow"] = _apply_lookback(tick_df, cutoff)
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


def _flow_dominance_payloads(meta: Dict[str, Any]) -> Tuple[Dict[str, Any], list]:
    """Build the live banner and full-history hover series without blocking charts."""
    models = meta.get("fair_value_models") or {}
    intraday = models.get("intraday") or {}
    request_meta = meta.get("request") or {}
    as_of = request_meta.get("end")
    try:
        context, series = build_flow_dominance_context_and_series(
            intraday.get("data", pd.DataFrame()),
            intraday.get("meta") or {},
            as_of=as_of,
        )
        live_status = meta.get("live_status") or {}
        if request_meta.get("mode") == "live" and live_status.get("connected") is False:
            context = dict(context)
            context.update(
                status="UNAVAILABLE",
                state="UNAVAILABLE",
                reason="Live feed is disconnected; the prior decomposition is not current.",
            )
        if request_meta.get("mode") == "live":
            try:
                record_v1_banner_change(context)
            except Exception:
                pass
        return context, series
    except Exception:
        logger.exception("Flow-dominance context calculation failed")
        unavailable = build_flow_dominance_context(pd.DataFrame(), {}, as_of=as_of)
        unavailable["reason"] = "Flow-dominance context calculation failed."
        return unavailable, []


def _latest_flow_dominance(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Build a fail-closed banner payload without affecting chart delivery."""
    context, _ = _flow_dominance_payloads(meta)
    return context


def _attach_flow_dominance(
    response: Dict[str, Any],
    context: Dict[str, Any],
    series: list,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Add the current banner plus the lookback-clipped hover series."""
    cutoff = None
    lookback = meta.get("display_lookback") or {}
    if lookback.get("cutoff"):
        cutoff = lookback["cutoff"]
    response["flow_dominance"] = context
    response["flow_dominance_series"] = clip_flow_dominance_series(series, cutoff)
    return response


def build_live_aggression_dataset(req: AggressionRequest) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Build data from the 1-minute session store, or tick merge as fallback."""
    _ensure_live_buffer()
    req = replace(req, end=datetime.now(timezone.utc))
    from_bars = _dataset_from_session_bars(req)
    if from_bars is not None:
        return from_bars
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
    tick_df, tick_meta = _tick_flow_for_request(req, trades_by_symbol)
    _append_session_ticks(trades_by_symbol, default_start_time())
    price_df = _build_live_price_series(trades_by_symbol, live_mid, req)
    historical_lag_sec = None
    if flow_meta.get("time_end"):
        try:
            end_ts = pd.Timestamp(flow_meta["time_end"]).to_pydatetime()
            historical_lag_sec = (datetime.now(timezone.utc) - end_ts).total_seconds()
        except Exception:
            historical_lag_sec = None
    live_status = _live_status("live", historical_lag_sec=historical_lag_sec)
    price_source = f"{live_status.get('source', 'accessapi')}_tbbo_live + databento_trade_backfill"
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
        "flow": {**flow_meta, **({
            "total_aggress_final": tick_meta.get("total_aggress_final", flow_meta.get("total_aggress_final")),
            "formula": tick_meta.get("formula", flow_meta.get("formula")),
            "bucket_interval": "print",
        } if tick_meta.get("trade_count") else {})},
        "tick_flow": tick_df,
        "tick_flow_meta": tick_meta,
        "price_rows": int(len(price_df)),
        "price_source": price_source,
        "live_status": live_status,
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


@app.get("/api/config")
def api_config() -> Any:
    """Expose static dashboard config for clients."""
    return jsonify({
        "symbols": list(SYMBOL_PARENT_MAP),
        "dv01_table": load_dv01_table(),
        "default_start_iso": default_start_time().isoformat(),
        "default_bucket": DEFAULT_BUCKET,
    })


@app.get("/api/live_status")
def api_live_status() -> Any:
    """Expose live-feed freshness for the UI banner."""
    _ensure_live_buffer()
    return jsonify(_live_status("live"))


@app.get("/api/flow_history")
def api_flow_history() -> Any:
    """Return durable 1-minute aggress buy/sell volume (contracts and $DV01)."""
    symbol = str(request.args.get("symbol") or "TY").strip().upper()
    if symbol not in {*SYMBOL_PARENT_MAP, "CURVE"}:
        return jsonify({"success": False, "error": f"Unsupported symbol '{symbol}'."}), 400
    start_raw = request.args.get("start")
    end_raw = request.args.get("end")
    try:
        start = parse_dashboard_datetime(start_raw) if start_raw else None
        end = parse_dashboard_datetime(end_raw, default_to_now=True) if end_raw else None
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    rows = query_flow_history(symbol, start=start, end=end)
    return jsonify({
        "success": True,
        "symbol": symbol,
        "rows": rows,
        "count": len(rows),
        "fields": {
            "buy_contracts": "aggress buy volume in contracts (took the ask)",
            "sell_contracts": "aggress sell volume in contracts (hit the bid)",
            "buy_flow": "aggress buy $DV01",
            "sell_flow": "aggress sell $DV01",
        },
    })


@app.get("/api/flow_history/status")
def api_flow_history_status() -> Any:
    """Coverage for the durable V1 buy/sell volume store."""
    return jsonify({"success": True, **history_status()})


def _live_rebuild_key(req: AggressionRequest) -> Tuple[Any, ...]:
    """Identify live rebuilds that can share one in-flight pandas pass."""
    return (
        tuple(req.aggression_symbols),
        req.price_symbol,
        req.start.isoformat(),
        req.bucket,
        bool(req.dv01_adjust),
        float(req.lookback_hours),
        req.reset,
    )


def _stabilize_p1_axis(response: Dict[str, Any], key: Tuple[Any, ...]) -> Dict[str, Any]:
    """Hold panel 1 aggress range unless min/max move by more than the deadband."""
    panel = (response.get("panels") or {}).get("p1") or {}
    align = dict(panel.get("axis_align") or {})
    new_range = align.get("aggress_range")
    if not new_range or len(new_range) != 2:
        return response
    with _P1_AXIS_LOCK:
        last = _P1_AXIS_LAST.get(key)
        if last:
            span = float(last[1]) - float(last[0])
            if span > 0:
                lo_move = abs(float(new_range[0]) - float(last[0])) / span
                hi_move = abs(float(new_range[1]) - float(last[1])) / span
                if lo_move < P1_AXIS_DEADBAND and hi_move < P1_AXIS_DEADBAND:
                    align["aggress_range"] = last
                    panel["axis_align"] = align
                    response["panels"] = dict(response.get("panels") or {})
                    response["panels"]["p1"] = dict(panel)
                    return response
        _P1_AXIS_LAST[key] = [float(new_range[0]), float(new_range[1])]
    return response


def _build_live_aggression_http(req: AggressionRequest) -> Tuple[Dict[str, Any], int]:
    """Build one live dashboard response body and HTTP status."""
    flow_df, price_df, meta = build_live_aggression_dataset(req)
    if flow_df.empty:
        error_meta = dict(meta)
        error_meta.pop("fair_value_models", None)
        return {
            "success": False,
            "error": "No trade data returned for the selected symbols/window.",
            "meta": error_meta,
        }, 404
    flow_dominance, flow_series = _flow_dominance_payloads(meta)
    flow_df, price_df, meta = _apply_display_lookback(req, flow_df, price_df, meta)
    response = build_dashboard_payload(flow_df, price_df, meta)
    response = _stabilize_p1_axis(response, _live_rebuild_key(req))
    return _attach_flow_dominance(response, flow_dominance, flow_series, meta), 200


def _cached_live_aggression_http(req: AggressionRequest) -> Tuple[Dict[str, Any], int]:
    """Serve a fresh live payload so extra Tailscale tabs share one rebuild."""
    key = _live_rebuild_key(req)
    now = time_module.monotonic()
    with _LIVE_REBUILD_LOCK:
        cached = _LIVE_RESULT_CACHE.get(key)
        if cached is not None and (now - float(cached["ts"])) < LIVE_RESULT_CACHE_TTL_SEC:
            return cached["result"]
    result = _coalesced_live_aggression_http(req)
    with _LIVE_REBUILD_LOCK:
        _LIVE_RESULT_CACHE[key] = {
            "result": result,
            "ts": time_module.monotonic(),
        }
    return result


def _coalesced_live_aggression_http(req: AggressionRequest) -> Tuple[Dict[str, Any], int]:
    """Reuse an in-flight live rebuild for identical request payloads."""
    key = _live_rebuild_key(req)
    owner = False
    with _LIVE_REBUILD_LOCK:
        inflight = _LIVE_REBUILDS.get(key)
        if inflight is None:
            inflight = {"event": threading.Event(), "result": None, "error": None}
            _LIVE_REBUILDS[key] = inflight
            owner = True
    if not owner:
        inflight["event"].wait()
        if inflight["error"] is not None:
            raise inflight["error"]
        return inflight["result"]
    try:
        inflight["result"] = _build_live_aggression_http(req)
        return inflight["result"]
    except Exception as exc:
        inflight["error"] = exc
        raise
    finally:
        inflight["event"].set()
        with _LIVE_REBUILD_LOCK:
            if _LIVE_REBUILDS.get(key) is inflight:
                del _LIVE_REBUILDS[key]


@app.post("/api/aggression")
def api_aggression() -> Any:
    """Fetch data, aggregate aggression, and return five panel payloads."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        mode = _parse_mode(payload)
        req = _parse_payload(payload)
        if mode == "live":
            body, status_code = _cached_live_aggression_http(req)
            return jsonify(body), status_code
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
        flow_dominance, flow_series = _flow_dominance_payloads(meta)
        flow_df, price_df, meta = _apply_display_lookback(req, flow_df, price_df, meta)
        response = build_dashboard_payload(flow_df, price_df, meta)
        return jsonify(_attach_flow_dominance(response, flow_dominance, flow_series, meta))
    except Exception as exc:
        logger.exception("Aggression dashboard API failed")
        return jsonify({"success": False, "error": str(exc)}), 400


def main() -> None:
    """Run the local development server."""
    import argparse

    parser = argparse.ArgumentParser(description="Curve aggression dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    _ensure_live_buffer()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

