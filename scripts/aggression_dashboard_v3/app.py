#!/usr/bin/env python3
"""Aggression V3 — trade-to-trade flow-attribution monitor.

Manual-only on port 5053. Writes only under workspace/analysis/aggression_v3_live/.
Does not edit, write, or restart V1 / V1.2 / V1.3 / V2.
"""

from __future__ import annotations

import gzip
import logging
import queue as queue_module
import sys
import threading
import time as time_module
from collections import OrderedDict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from flask import Flask, Response, jsonify, render_template, request, stream_with_context
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Flask is required. Install with: pip install flask") from exc

import pandas as pd

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
    default_start_time,
    fetch_curve_trades,
    last_n_business_day_window,
    load_cached_trades_window,
    load_dv01_table,
    normalize_symbol,
    parse_dashboard_datetime,
    prune_trades_cache,
    globex_session_end,
    session_labels,
    trading_day_start,
)
from scripts.aggression_dashboard_v3.history_client import HistoryWorkerError, build_history_service
from scripts.aggression_dashboard_v3.history_store import HISTORY_VIEWS, write_close_snapshot
from scripts.aggression_dashboard_v3.live_buffer import LiveTradeBuffer
from scripts.aggression_dashboard_v3.live_publisher import HEARTBEAT_SEC, LivePublisher, encode_sse
from scripts.aggression_dashboard_v3.print_reconcile import reconcile_by_symbol
from scripts.aggression_dashboard_v3.panels import build_state_timeline_panel

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
TENDAY_PATH_SLOTS = 3
_TENDAY_PATH_LOCK = threading.Lock()
_TENDAY_PATHS: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()
_TENDAY_PATH_BUILDING: set[tuple] = set()

# A live selection: (price symbol, aggression chips in SYMBOL_PARENT_MAP order, DV01 adjust).
LiveKey = Tuple[str, Tuple[str, ...], bool]
DEFAULT_LIVE_KEY: LiveKey = (DEFAULT_PRICE_SYMBOL, DEFAULT_AGGRESSION_SYMBOLS, True)
MAX_EXTRA_LIVE_VIEWS = 2
LIVE_VIEW_IDLE_SEC = 600.0
LIVE_VIEW_REAP_SEC = 30.0
LIVE_VIEW_LIMIT_REASON = "Too many live views; use Historical mode."
_PUBLISHERS: Dict[LiveKey, LivePublisher] = {}
_PUBLISHER_IDLE_SINCE: Dict[LiveKey, float] = {}
_PUBLISHERS_LOCK = threading.Lock()
_REAPER_THREAD: threading.Thread | None = None


def _beta_warm_key(price_symbol: str, aggression_symbols: Tuple[str, ...], dv01_adjust: bool) -> tuple:
    return (price_symbol, tuple(aggression_symbols), bool(dv01_adjust))


def _kick_beta_ref(
    price_symbol: str,
    aggression_symbols: Tuple[str, ...],
    dv01_adjust: bool,
    trades_window: Optional[Dict[str, pd.DataFrame]] = None,
) -> Optional[threading.Thread]:
    """Compute beta_ref in the background; only a newly computed beta re-triggers a rebuild."""
    key = _beta_warm_key(price_symbol, aggression_symbols, dv01_adjust)
    with _BETA_WARM_LOCK:
        if key in _BETA_WARM_KEYS:
            return None
        _BETA_WARM_KEYS.add(key)

    def _run() -> None:
        fresh = False
        try:
            payload = ensure_beta_ref(
                price_symbol=price_symbol,
                aggression_symbols=aggression_symbols,
                dv01_adjust=dv01_adjust,
                trades_window=trades_window,
                compute=True,
            )
            fresh = bool(payload.get("ok")) and not payload.get("cached")
        except Exception:
            logger.exception("V3 background beta_ref failed")
        finally:
            with _BETA_WARM_LOCK:
                _BETA_WARM_KEYS.discard(key)
            if fresh:
                with _PUBLISHERS_LOCK:
                    publisher = _PUBLISHERS.get(key)
                if publisher is not None:
                    publisher.offer_history()

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
        frame = _TENDAY_PATHS.get(key)
        if frame is None:
            return pd.DataFrame()
        _TENDAY_PATHS.move_to_end(key)
        return frame


def _store_tenday_path(key: tuple, frame: pd.DataFrame) -> None:
    with _TENDAY_PATH_LOCK:
        _TENDAY_PATHS[key] = frame
        _TENDAY_PATHS.move_to_end(key)
        while len(_TENDAY_PATHS) > TENDAY_PATH_SLOTS:
            _TENDAY_PATHS.popitem(last=False)


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
        if key in _TENDAY_PATHS or key in _TENDAY_PATH_BUILDING:
            return
        _TENDAY_PATH_BUILDING.add(key)

    def _run() -> None:
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
                    _store_tenday_path(key, frame)
        except Exception:
            logger.exception("V3 background 10-session path failed")
        finally:
            with _TENDAY_PATH_LOCK:
                _TENDAY_PATH_BUILDING.discard(key)

    threading.Thread(target=_run, name="v3-tenday-path", daemon=True).start()


def _ensure_live_buffer() -> None:
    LIVE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_BUFFER.start()
    _ensure_backfill_thread()
    _ensure_tenday_thread()
    _kick_beta_ref(DEFAULT_PRICE_SYMBOL, DEFAULT_AGGRESSION_SYMBOLS, True)
    _ensure_publisher()
    _ensure_live_view_reaper()


class LiveViewLimitError(RuntimeError):
    """Every extra live-view slot has viewers."""


def live_key_from_args(args: Any) -> LiveKey:
    """Live selection from stream query args; unknown or missing values fall back to the default view."""
    chosen = set()
    for part in str(args.get("chips") or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            chosen.add(normalize_symbol(part))
        except ValueError:
            logger.warning("Ignoring unsupported live chip from client: %s", part)
    symbols = tuple(symbol for symbol in SYMBOL_PARENT_MAP if symbol in chosen) or DEFAULT_AGGRESSION_SYMBOLS
    try:
        price_symbol = normalize_symbol(args.get("price") or DEFAULT_PRICE_SYMBOL)
    except ValueError:
        price_symbol = DEFAULT_PRICE_SYMBOL
    dv01_adjust = str(args.get("dv01", "1")).strip().lower() not in ("0", "false", "no", "off")
    return (price_symbol, symbols, dv01_adjust)


def _describe_live_key(key: LiveKey) -> str:
    return f"{key[0]} price vs {'+'.join(key[1])} (DV01 {'on' if key[2] else 'off'})"


def _make_publisher(key: LiveKey) -> LivePublisher:
    price_symbol, symbols, dv01_adjust = key

    def assemble(start: datetime, end: datetime):
        return _live_display_trades(symbols, start, end)

    def cached_window(start: datetime, end: datetime):
        return load_cached_trades_window(symbols, start, end)

    def peek_beta(now: datetime) -> Dict[str, Any]:
        return ensure_beta_ref(
            price_symbol=price_symbol,
            aggression_symbols=symbols,
            dv01_adjust=dv01_adjust,
            now=now,
            compute=False,
        )

    def kick_beta() -> None:
        _kick_beta_ref(price_symbol, symbols, dv01_adjust, trades_window=_snapshot_tenday_trades(symbols) or None)

    def tenday_path(beta_ref: Optional[float]) -> pd.DataFrame:
        return _snapshot_tenday_path(_tenday_path_key(symbols, price_symbol, dv01_adjust, beta_ref))

    def kick_tenday(beta_ref: Optional[float], dv01_table: Dict[str, float]) -> None:
        if beta_ref is not None:
            _kick_tenday_path(symbols, price_symbol, dv01_adjust, float(beta_ref), dv01_table)

    return LivePublisher(
        assemble_trades=assemble,
        load_cached_window=cached_window,
        peek_beta=peek_beta,
        kick_beta=kick_beta,
        live_status=lambda: _live_status("live"),
        tenday_path=tenday_path,
        kick_tenday=kick_tenday,
        load_dv01=load_dv01_table,
        price_symbol=price_symbol,
        aggression_symbols=symbols,
        dv01_adjust=dv01_adjust,
    )


def _running_publishers() -> List[LivePublisher]:
    with _PUBLISHERS_LOCK:
        return list(_PUBLISHERS.values())


def _fan_out_trade(trade: Dict[str, Any]) -> None:
    for publisher in _running_publishers():
        publisher.offer_trade(trade)


def _fan_out_quote() -> None:
    for publisher in _running_publishers():
        publisher.offer_quote()


def _stop_publisher_locked(key: LiveKey) -> None:
    publisher = _PUBLISHERS.pop(key, None)
    _PUBLISHER_IDLE_SINCE.pop(key, None)
    if publisher is not None:
        publisher.stop()
        logger.info("V3 live view stopped: %s", _describe_live_key(key))


def _ensure_publisher(key: LiveKey = DEFAULT_LIVE_KEY, *, claim: bool = False) -> LivePublisher:
    """Running publisher for one live selection. The default always runs; extras are capped.

    ``claim`` is for a viewer about to subscribe: it clears the idle mark so the reaper cannot
    stop the publisher in between. When the extra cap is full, the oldest extra stops so the
    requested selection gets the slot.
    """
    with _PUBLISHERS_LOCK:
        publisher = _PUBLISHERS.get(key)
        if publisher is not None:
            if claim:
                _PUBLISHER_IDLE_SINCE.pop(key, None)
            return publisher
        if key != DEFAULT_LIVE_KEY:
            extras = [k for k in _PUBLISHERS if k != DEFAULT_LIVE_KEY]
            if len(extras) >= MAX_EXTRA_LIVE_VIEWS:
                # Insertion order: the first extra is the oldest. Drop it even if its
                # stream has not finished closing, so the selection just requested connects.
                _stop_publisher_locked(extras[0])
        publisher = _make_publisher(key)
        _PUBLISHERS[key] = publisher
        LIVE_BUFFER.set_hooks(on_trade=_fan_out_trade, on_quote=_fan_out_quote)
        publisher.start()
        logger.info("V3 live view started: %s", _describe_live_key(key))
        return publisher


def _note_viewer_left(key: LiveKey, publisher: LivePublisher) -> None:
    with _PUBLISHERS_LOCK:
        if _PUBLISHERS.get(key) is publisher and publisher.subscriber_count() == 0:
            _PUBLISHER_IDLE_SINCE[key] = time_module.monotonic()


def reap_idle_live_views(now: Optional[float] = None) -> List[LiveKey]:
    """Stop extra live views that have had no viewers for LIVE_VIEW_IDLE_SEC."""
    now = time_module.monotonic() if now is None else now
    with _PUBLISHERS_LOCK:
        expired = [
            key
            for key, since in _PUBLISHER_IDLE_SINCE.items()
            if key != DEFAULT_LIVE_KEY
            and key in _PUBLISHERS
            and _PUBLISHERS[key].subscriber_count() == 0
            and now - since >= LIVE_VIEW_IDLE_SEC
        ]
        for key in expired:
            _stop_publisher_locked(key)
    return expired


def _live_view_reaper() -> None:
    while True:
        time_module.sleep(LIVE_VIEW_REAP_SEC)
        try:
            reap_idle_live_views()
        except Exception:
            logger.exception("V3 live view reaper failed")


def _ensure_live_view_reaper() -> None:
    global _REAPER_THREAD
    if _REAPER_THREAD and _REAPER_THREAD.is_alive():
        return
    _REAPER_THREAD = threading.Thread(target=_live_view_reaper, name="v3-live-view-reaper", daemon=True)
    _REAPER_THREAD.start()


def _live_views_summary() -> List[Dict[str, Any]]:
    with _PUBLISHERS_LOCK:
        return [
            {
                "price_symbol": key[0],
                "aggression_symbols": list(key[1]),
                "dv01_adjust": key[2],
                "viewers": publisher.subscriber_count(),
                "default": key == DEFAULT_LIVE_KEY,
            }
            for key, publisher in _PUBLISHERS.items()
        ]


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


def _snapshot_session_at_close() -> None:
    """During 17:00–18:00 ET, save the merged tape of the session that just closed. No Databento call."""
    now = datetime.now(timezone.utc)
    now_et = now.astimezone(EASTERN)
    if now_et.weekday() >= 5 or not (time(17, 0) <= now_et.time() < time(18, 0)):
        return
    session_start = default_start_time(now)
    end = globex_session_end(session_start)
    if end > now:
        return
    symbols = tuple(SYMBOL_PARENT_MAP)
    trades, _errors, _state = _live_display_trades(symbols, session_start, end)
    write_close_snapshot(trades, session_start)


HISTORY, HISTORY_SCHEDULER = build_history_service(_market_open, prepare=_snapshot_session_at_close)


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
            for publisher in _running_publishers():
                publisher.offer_history()
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
    status = _live_status("live")
    status["live_views"] = _live_views_summary()
    return jsonify(status)


@app.get("/api/live/stream")
def api_live_stream() -> Response:
    key = live_key_from_args(request.args)
    try:
        publisher = _ensure_publisher(key, claim=True)
    except LiveViewLimitError as exc:
        return Response(
            encode_sse("limit", {"reason": str(exc)}),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    incoming = publisher.subscribe()

    def generate():
        try:
            yield ": connected\n" + (" " * 2048) + "\n\n"
            while True:
                try:
                    yield incoming.get(timeout=HEARTBEAT_SEC)
                except queue_module.Empty:
                    yield ": heartbeat\n\n"
        finally:
            publisher.unsubscribe(incoming)
            _note_viewer_left(key, publisher)

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
        if _parse_mode(payload) != "live":
            return jsonify({
                "success": False,
                "error": "Historical builds no longer run in the live server; use the History section (GET /api/history).",
            }), 410
        body, status_code = _cached_live(_parse_payload(payload))
        return jsonify(body), status_code
    except Exception as exc:
        logger.exception("V3 aggression API failed")
        return jsonify({"success": False, "error": str(exc)}), 400


def _history_view() -> str:
    view = str(request.args.get("view") or "curve").strip().lower()
    if view not in HISTORY_VIEWS:
        raise HistoryWorkerError(f"Unknown history view: {view}", "range")
    return view


def _history_error(exc: HistoryWorkerError) -> Tuple[Any, int]:
    return jsonify({"ok": False, "error": str(exc), "code": exc.code}), 400 if exc.code == "range" else 503


def _gzip_json_response(body: bytes, etag: str, cached: bool) -> Response:
    """Stored gzip bytes as-is when the client accepts gzip; 304 when its ETag matches."""
    if request.if_none_match.contains(etag):
        response = Response(status=304)
    elif "gzip" in (request.headers.get("Accept-Encoding") or "").lower():
        response = Response(body, mimetype="application/json")
        response.headers["Content-Encoding"] = "gzip"
    else:
        response = Response(gzip.decompress(body), mimetype="application/json")
    response.set_etag(etag)
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Vary"] = "Accept-Encoding"
    response.headers["X-History-Cache"] = "hit" if cached else "miss"
    return response


@app.get("/api/history")
def api_history() -> Any:
    try:
        view = _history_view()
        try:
            start = parse_dashboard_datetime(request.args.get("start") or "")
            end = parse_dashboard_datetime(request.args.get("end") or "")
        except ValueError as exc:
            raise HistoryWorkerError(f"Bad start or end time: {exc}", "range") from exc
        etag, body, cached = HISTORY.serve(view, start, end)
    except HistoryWorkerError as exc:
        return _history_error(exc)
    return _gzip_json_response(body, etag, cached)


@app.get("/api/history/sessions")
def api_history_sessions() -> Any:
    try:
        body = HISTORY.sessions(_history_view())
        HISTORY.warm()
    except HistoryWorkerError as exc:
        return _history_error(exc)
    return Response(body, mimetype="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/api/history/status")
def api_history_status() -> Any:
    status = HISTORY.status()
    status["scheduler"] = HISTORY_SCHEDULER.last_run
    status["market_open"] = _market_open()
    return jsonify(status)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Aggression V3 trade-to-trade monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5053)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    _ensure_live_buffer()
    HISTORY_SCHEDULER.start()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
