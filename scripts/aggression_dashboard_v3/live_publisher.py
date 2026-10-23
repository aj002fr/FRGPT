"""One live picture, trade-clock deltas, same bytes to every subscriber."""

from __future__ import annotations

import bisect
import json
import logging
import math
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    BANNER_SERIES_MAX_POINTS,
    GAP_SEC,
    NO_EARLIER_PRINT_REASON,
    OVERNIGHT_WINDOW_MIN,
    earlier_print_reason,
    window_minutes,
    banner_from_endpoints,
    build_banner,
    build_banner_series,
)
from scripts.aggression_dashboard_v3.beta_ref import bucket_display_time
from scripts.aggression_dashboard_v3.core import (
    DEFAULT_AGGRESSION_SYMBOLS,
    DEFAULT_PRICE_SYMBOL,
    TICK_PLOT_MAX_POINTS,
    apply_beta_ref_path,
    build_print_frame,
    clip_print_lookback,
    default_start_time,
)
from scripts.aggression_dashboard_v3.panels import (
    BUYSELL_DISPLAY_HALFLIFE,
    _to_et_iso,
    advance_runs,
    apply_strip_edge,
    build_dashboard_payload,
    build_state_timeline_panel,
    state_run,
)
from scripts.totalaggress_vs_price_chart import classify_aggressor as _classify_aggressor

logger = logging.getLogger(__name__)

LOOKBACK_HOURS = 2.0
HEARTBEAT_SEC = 15.0
GAP_NS = int(GAP_SEC * 1_000_000_000)
OVERNIGHT_GAP_NS = int(OVERNIGHT_WINDOW_MIN * 60 * 1_000_000_000)
SESSION_RESET = "Banner window reset after a session boundary."
SUBSCRIBER_QUEUE_MAX = 128
TIP_SLACK_MIN = 1.0
PREFIX_TOLERANCE = 0.5
SERIES_MARGIN = pd.Timedelta(minutes=2 * OVERNIGHT_WINDOW_MIN)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"unserializable {type(value)!r}")


def encode_sse(event: str, payload: Any) -> str:
    """Encode one SSE event. Call once so every subscriber gets the same bytes."""
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'), default=_json_default)}\n\n"


def encode_heartbeat() -> str:
    return ": heartbeat\n\n"


def frame_has_window_print(frame: pd.DataFrame, as_of: Optional[pd.Timestamp] = None) -> bool:
    if frame is None or frame.empty:
        return False
    idx = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
    as_of_ts = pd.Timestamp(as_of if as_of is not None else idx.max())
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize("UTC")
    else:
        as_of_ts = as_of_ts.tz_convert("UTC")
    earlier = as_of_ts - pd.Timedelta(minutes=window_minutes(as_of_ts))
    now_hits = idx[idx <= as_of_ts]
    prev_hits = idx[idx <= earlier]
    if len(now_hits) == 0 or len(prev_hits) == 0:
        return False
    return pd.Timestamp(now_hits[-1]) != pd.Timestamp(prev_hits[-1])


def empty_tape_warming(banner: Optional[Dict[str, Any]], frame: Optional[pd.DataFrame]) -> bool:
    if frame_has_window_print(frame):
        return False
    payload = banner or {}
    state = str(payload.get("state") or "")
    reason = str(payload.get("reason") or "")
    if state == "WARMING" and "minute-earlier endpoint" in reason:
        return True
    if payload.get("status") == "UNAVAILABLE" and "No last-trade print frame" in reason:
        return True
    return frame is None or frame.empty


def _session_id(ts: pd.Timestamp) -> str:
    value = pd.Timestamp(ts)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    et = value.tz_convert("America/New_York")
    start = et.normalize()
    if et.hour < 18:
        start = start - pd.Timedelta(days=1)
    return start.strftime("%Y-%m-%dT18:00")


def _aggress_prefix(frame: pd.DataFrame) -> Tuple[Tuple[int, float], ...]:
    if frame is None or frame.empty or "total_aggress" not in frame.columns:
        return tuple()
    idx = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
    values = pd.to_numeric(frame["total_aggress"], errors="coerce").to_numpy(dtype=float)
    return tuple((int(ts), round(float(val), 6) if math.isfinite(val) else None) for ts, val in zip(idx.asi8, values))


def _finite_num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cap_list(items: List[Any], limit: int) -> None:
    extra = len(items) - int(limit)
    if extra > 0:
        del items[:extra]


def _keep_newer_tail(rebuilt: pd.DataFrame, live: pd.DataFrame) -> Tuple[pd.DataFrame, float]:
    """A history rebuild must not erase prints already drawn past its right edge.

    Also returns how far re-accumulating the tail moved its TotalAggress.
    """
    if live is None or live.empty:
        return rebuilt, 0.0
    if rebuilt is None or rebuilt.empty:
        return live, 0.0
    live_idx = pd.DatetimeIndex(pd.to_datetime(live.index, utc=True))
    rebuilt_idx = pd.DatetimeIndex(pd.to_datetime(rebuilt.index, utc=True))
    if live_idx.max() <= rebuilt_idx.max():
        return rebuilt, 0.0
    tail = live.loc[live_idx > rebuilt_idx.max()].copy()
    if tail.empty:
        return rebuilt, 0.0
    before = pd.to_numeric(tail["total_aggress"], errors="coerce") if "total_aggress" in tail.columns else None
    tail = _continue_session_sum(rebuilt, tail)
    shift = 0.0
    if before is not None:
        moved = (pd.to_numeric(tail["total_aggress"], errors="coerce") - before).abs()
        shift = float(moved.max()) if moved.notna().any() else 0.0
    return pd.concat([rebuilt, tail]).sort_index(), shift


def _continue_session_sum(rebuilt: pd.DataFrame, tail: pd.DataFrame) -> pd.DataFrame:
    """Re-accumulate tail TotalAggress from the rebuilt frame's last print, per session."""
    if "delta_a" not in tail.columns or "total_aggress" not in rebuilt.columns:
        return tail
    last_total = _finite_num(rebuilt["total_aggress"].iloc[-1])
    if last_total is None:
        return tail
    last_session = rebuilt["session_id"].iloc[-1] if "session_id" in rebuilt.columns else None
    sessions = (
        tail["session_id"].to_numpy()
        if "session_id" in tail.columns
        else np.full(len(tail), last_session, dtype=object)
    )
    deltas = pd.to_numeric(tail["delta_a"], errors="coerce").fillna(0.0)
    running = deltas.groupby(sessions, dropna=False).cumsum().to_numpy(dtype=float)
    tail["total_aggress"] = running + np.where(sessions == last_session, last_total, 0.0)
    return tail


def _clip_with_margin(frame: pd.DataFrame, display: pd.DataFrame, margin: pd.Timedelta) -> pd.DataFrame:
    """Frame rows from `margin` before the display start: enough for banner-window endpoints."""
    if frame is None or frame.empty or display is None or display.empty:
        return frame
    return frame.loc[frame.index >= display.index.min() - margin]


def _prefix_changed(
    old: Tuple[Tuple[int, float], ...],
    new: Tuple[Tuple[int, float], ...],
) -> bool:
    new_map = dict(new)
    for ts, value in dict(old).items():
        if ts not in new_map:
            continue
        other = new_map[ts]
        if value is None or other is None:
            if value is not other:
                return True
            continue
        if abs(other - value) > PREFIX_TOLERANCE:
            return True
    return False


class LivePublisher:
    """Owns the default live view. One rebuild, trade-clock deltas, N subscribers."""

    def __init__(
        self,
        *,
        assemble_trades: Callable[[datetime, datetime], Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]],
        load_cached_window: Callable[[datetime, datetime], Dict[str, pd.DataFrame]],
        peek_beta: Callable[[datetime], Dict[str, Any]],
        kick_beta: Callable[[], None],
        live_status: Callable[[], Dict[str, Any]],
        tenday_path: Callable[[Optional[float]], pd.DataFrame],
        kick_tenday: Callable[[Optional[float], Dict[str, float]], None],
        load_dv01: Callable[[], Dict[str, float]],
        price_symbol: str = DEFAULT_PRICE_SYMBOL,
        aggression_symbols: Tuple[str, ...] = DEFAULT_AGGRESSION_SYMBOLS,
        dv01_adjust: bool = True,
        lookback_hours: float = LOOKBACK_HOURS,
    ) -> None:
        self.assemble_trades = assemble_trades
        self.load_cached_window = load_cached_window
        self.peek_beta = peek_beta
        self.kick_beta = kick_beta
        self.live_status = live_status
        self.tenday_path = tenday_path
        self.kick_tenday = kick_tenday
        self.load_dv01 = load_dv01
        self.price_symbol = price_symbol
        self.aggression_symbols = tuple(aggression_symbols)
        self.dv01_adjust = bool(dv01_adjust)
        self.lookback_hours = float(lookback_hours)
        self._inbox: queue.Queue[Tuple[str, Any]] = queue.Queue()
        self._subs: List[queue.Queue[str]] = []
        self._subs_lock = threading.Lock()
        self._lock = threading.RLock()
        self._rebuild_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rebuild_thread: Optional[threading.Thread] = None
        self._running = False
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_bytes: Optional[str] = None
        self._loading: Optional[Dict[str, Any]] = None
        self._frame = pd.DataFrame()
        self._display = pd.DataFrame()
        self._path = pd.DataFrame()
        self._beta: Dict[str, Any] = {}
        self._beta_ref: Optional[float] = None
        self._runs: List[Dict[str, Any]] = []
        self._group_runs: List[Dict[str, Any]] = []
        self._runs_open: Dict[str, bool] = {"state": False, "group": False}
        self._prefix: Tuple[Tuple[int, float], ...] = tuple()
        self._buysell_ewm: Optional[float] = None
        self._buysell_ts: Optional[pd.Timestamp] = None
        self._path_anchor: Optional[Tuple[float, float]] = None
        self._seen: Deque[Tuple[int, str, int, float]] = deque(maxlen=8192)
        self._seen_set: set[Tuple[int, str, int, float]] = set()
        self._ts: List[int] = []
        self._px: List[Optional[float]] = []
        self._ag: List[Optional[float]] = []
        self._sid: List[str] = []
        self._gap5_ns: Optional[int] = None
        self._gap15_ns: Optional[int] = None
        self._pending_rows: List[Dict[str, Any]] = []
        self._recent_points: Deque[Dict[str, Any]] = deque(maxlen=50_000)
        self._snapshot_through_ns = 0
        self._session_prints = 0
        self._dv01: Optional[Dict[str, float]] = None
        self._beta_bucket: Optional[str] = None
        self._beta_peek_mono = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="v3-live-publisher", daemon=True)
        self._rebuild_thread = threading.Thread(target=self._rebuild_loop, name="v3-live-rebuild", daemon=True)
        self._thread.start()
        self._rebuild_thread.start()
        self.offer_history()

    def stop(self) -> None:
        self._running = False
        self._rebuild_event.set()
        self._inbox.put(("stop", None))

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            opening = self._snapshot_bytes
            through = self._snapshot_through_ns
            replay_points = [item["point"] for item in self._recent_points if int(item["ts_ns"]) > through]
            loading = None if opening else self._loading
            snap = None if opening else self._snapshot
            with self._subs_lock:
                self._subs.append(q)
            if opening is None:
                if snap is not None:
                    opening = encode_sse("snapshot", snap)
                elif loading is not None:
                    opening = encode_sse("loading", loading)
                else:
                    opening = encode_sse("loading", {"reason": "Waiting for the live picture."})
            self._put(q, opening)
            if replay_points:
                self._put(q, encode_sse("trade", {
                    "prints": replay_points,
                    "flow_dominance": replay_points[-1].get("banner"),
                    "live_status": self.live_status(),
                }))
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._subs_lock:
            if q in self._subs:
                self._subs.remove(q)

    def subscriber_count(self) -> int:
        with self._subs_lock:
            return len(self._subs)

    def offer_trade(self, trade: Dict[str, Any]) -> None:
        self._inbox.put(("trade", trade))

    def offer_quote(self) -> None:
        self._inbox.put(("quote", None))

    def offer_history(self) -> None:
        self._rebuild_event.set()

    def last_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot
            return self._loading

    def _opening_bytes(self) -> Optional[str]:
        with self._lock:
            if self._snapshot_bytes:
                return self._snapshot_bytes
            snap = self._snapshot
            loading = self._loading
        if snap is not None:
            return encode_sse("snapshot", snap)
        if loading is not None:
            return encode_sse("loading", loading)
        return encode_sse("loading", {"reason": "Waiting for the live picture."})

    def _loop(self) -> None:
        while self._running:
            try:
                first = self._inbox.get(timeout=HEARTBEAT_SEC)
            except queue.Empty:
                self._broadcast_raw(encode_heartbeat())
                self._broadcast("status", self.live_status())
                continue
            batch = [first]
            while True:
                try:
                    batch.append(self._inbox.get_nowait())
                except queue.Empty:
                    break
            if any(kind == "stop" for kind, _ in batch):
                return
            trades = [item for kind, item in batch if kind == "trade" and item]
            quotes = any(kind == "quote" for kind, _ in batch)
            try:
                if trades:
                    self.apply_trades(trades)
                elif quotes:
                    status = self.live_status()
                    self._touch_snapshot_status(status)
                    self._broadcast("status", status)
            except Exception:
                logger.exception("V3 live publisher failed")

    def _rebuild_loop(self) -> None:
        while self._running:
            if not self._rebuild_event.wait(timeout=1.0):
                continue
            self._rebuild_event.clear()
            if not self._running:
                return
            try:
                self.rebuild()
            except Exception:
                logger.exception("V3 live rebuild failed")

    def rebuild(self) -> Optional[Dict[str, Any]]:
        """Assemble the shared snapshot from the session open. Empty-tape WARMING is not stored.

        TotalAggress and the implied path run from 18:00 ET; lookback only clips the charts.
        """
        now = datetime.now(timezone.utc)
        session_start = default_start_time(now)
        lookback_start = now - timedelta(hours=self.lookback_hours)
        trades, errors, backfill_state = self.assemble_trades(session_start, now)
        frame, flow_meta = self._frame_from_trades(trades, session_start)
        if not frame_has_window_print(frame, pd.Timestamp(now)):
            cached = self.load_cached_window(session_start, now)
            merged = dict(trades)
            for symbol, extra in cached.items():
                existing = merged.get(symbol)
                if extra is None or extra.empty:
                    continue
                if existing is None or existing.empty:
                    merged[symbol] = extra
                else:
                    both = pd.concat([existing, extra], ignore_index=True)
                    both["ts"] = pd.to_datetime(both["ts"], utc=True)
                    both.sort_values("ts", inplace=True)
                    both.drop_duplicates(
                        subset=["ts", "price", "size", "side", "dashboard_symbol"],
                        inplace=True,
                    )
                    both.reset_index(drop=True, inplace=True)
                    merged[symbol] = both
            trades = merged
            frame, flow_meta = self._frame_from_trades(trades, session_start)
        dv01_table = self.load_dv01()
        beta_payload = self.peek_beta(now)
        if beta_payload.get("stale", not beta_payload.get("ok")) or beta_payload.get("bucket_stale"):
            self.kick_beta()
        live_status = self.live_status()
        with self._lock:
            live_tail = self._pending_frame()
        frame, tail_shift = _keep_newer_tail(frame, live_tail)
        display = clip_print_lookback(frame, self.lookback_hours)
        recent = _clip_with_margin(frame, display, SERIES_MARGIN)
        banner_as_of = frame.index.max() if frame is not None and not frame.empty else now
        banner = build_banner(
            recent,
            beta_payload,
            price_symbol=self.price_symbol,
            as_of=banner_as_of,
            live_connected=live_status.get("connected"),
        )
        if empty_tape_warming(banner, frame):
            loading = {
                "reason": banner.get("reason") or NO_EARLIER_PRINT_REASON,
                "flow_dominance": banner,
                "live_status": live_status,
            }
            with self._lock:
                self._loading = loading
                if self._snapshot is None:
                    self._broadcast("loading", loading)
            return None

        beta_ref = beta_payload.get("beta_ref") if beta_payload.get("ok") else None
        path_beta = float(beta_ref) if beta_ref else 0.0
        session_path = apply_beta_ref_path(frame, path_beta)
        tenday_frame = self.tenday_path(float(beta_ref) if beta_ref else None)
        if tenday_frame.empty and beta_ref:
            self.kick_tenday(float(beta_ref), dv01_table)
        display_path = clip_print_lookback(session_path, self.lookback_hours)
        meta = {
            "request": {
                "aggression_symbols": list(self.aggression_symbols),
                "price_symbol": self.price_symbol,
                "start": session_start.isoformat(),
                "end": now.isoformat(),
                "dv01_adjust": self.dv01_adjust,
                "lookback_hours": self.lookback_hours,
                "mode": "live",
                "clock": "trade-to-trade",
            },
            "dv01_table": dv01_table,
            "fetch_errors": errors,
            "backfill": backfill_state,
            "flow": flow_meta,
            "beta_ref": {
                "ok": beta_payload.get("ok"),
                "beta_ref": beta_payload.get("beta_ref"),
                "beta_display_yield_bp_per_1m": beta_payload.get("beta_display_yield_bp_per_1m"),
                "n_sessions_used": beta_payload.get("n_sessions_used"),
                "sessions": beta_payload.get("sessions"),
                "window": beta_payload.get("window"),
                "last_session_end": beta_payload.get("last_session_end"),
                "reason": beta_payload.get("reason"),
                "version": beta_payload.get("version"),
                "tag": beta_payload.get("tag"),
                "tag_detail": beta_payload.get("tag_detail"),
                "session_date": beta_payload.get("session_date"),
                "slope_bps_per_mm": beta_payload.get("slope_bps_per_mm"),
                "last_tag_day": beta_payload.get("last_tag_day"),
            },
            "live_status": live_status,
            "label": "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE",
        }
        response = build_dashboard_payload(display, display_path, tenday_frame, meta)
        series = build_banner_series(
            recent,
            beta_payload,
            price_symbol=self.price_symbol,
            as_of_index=display.index if not display.empty else None,
        )
        response["flow_dominance"] = banner
        response["flow_dominance_series"] = series
        response["panels"]["p1_states"] = build_state_timeline_panel(series)
        event = self._revision_event(display, beta_payload)
        kept_shift = self._store_picture(response, frame, display, session_path, beta_payload)
        if event is None and max(tail_shift, kept_shift) > PREFIX_TOLERANCE:
            event = "history"
        if event:
            self._broadcast(event, response)
        self._catch_up_live(display.index.max() if not display.empty else lookback_start)
        return response

    def apply_trades(self, trades: List[Dict[str, Any]]) -> Optional[str]:
        """Apply prints in trade order. One message, every print, no wall-clock wait."""
        with self._lock:
            if self._snapshot is None:
                pending = True
            else:
                pending = False
        if pending:
            self.rebuild()
        prints: List[Dict[str, Any]] = []
        banner = None
        with self._lock:
            for trade in trades:
                point = self._apply_one(trade)
                if point is None:
                    continue
                prints.append(point)
                banner = point.get("banner")
        if not prints:
            return None
        if self._snapshot is None and frame_has_window_print(self._frame):
            self.rebuild()
            return None
        live_status = self.live_status()
        strip = self._strip_edge(prints[-1])
        payload = {
            "prints": prints,
            "flow_dominance": banner,
            "strip": strip,
            "live_status": live_status,
            "lookback_start": _to_et_iso(datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)),
            "meta": {
                "flow": {
                    "trade_count": self._session_prints,
                    "total_aggress_final": prints[-1].get("total_aggress"),
                }
            },
        }
        encoded = encode_sse("trade", payload)
        self._update_snapshot_tip(prints, banner, live_status, strip)
        self._broadcast_raw(encoded)
        return encoded

    def _catch_up_live(self, since: datetime | pd.Timestamp) -> None:
        """Apply live prints newer than the rebuilt frame without waiting for the next trade."""
        start = pd.Timestamp(since)
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        else:
            start = start.tz_convert("UTC")
        now = datetime.now(timezone.utc)
        fresh, _errors, _state = self.assemble_trades(start.to_pydatetime(), now)
        pending: List[Dict[str, Any]] = []
        for symbol, df in fresh.items():
            if df is None or df.empty:
                continue
            work = df.copy()
            work["ts"] = pd.to_datetime(work["ts"], utc=True)
            work = work.loc[work["ts"] > start]
            for row in work.itertuples(index=False):
                pending.append({
                    "ts": row.ts,
                    "price": row.price,
                    "size": row.size,
                    "side": row.side,
                    "dashboard_symbol": symbol,
                })
        if pending:
            pending.sort(key=lambda item: pd.Timestamp(item["ts"]))
            self.apply_trades(pending)

    def _touch_snapshot_status(self, live_status: Dict[str, Any]) -> None:
        with self._lock:
            if self._snapshot is None:
                return
            meta = self._snapshot.setdefault("meta", {})
            meta["live_status"] = live_status

    def _update_snapshot_tip(
        self,
        prints: List[Dict[str, Any]],
        banner: Optional[Dict[str, Any]],
        live_status: Dict[str, Any],
        strip: Dict[str, Any],
    ) -> None:
        with self._lock:
            snap = self._snapshot
            if snap is None:
                return
            panels = snap.setdefault("panels", {})
            field_map = {
                "p1": ("total_aggress", "price"),
                "p2": ("buysell_ratio", "price"),
                "p3": ("buy_aggress", "sell_aggress", "total_aggress", "price"),
                "p4": ("price", "implied_price"),
            }
            for key, fields in field_map.items():
                panel = panels.get(key)
                if not panel:
                    continue
                data = panel.setdefault("data", [])
                for point in prints:
                    data.append({"x": point["x"], **{field: point.get(field) for field in fields}})
            if banner is not None:
                snap["flow_dominance"] = banner
                series = snap.setdefault("flow_dominance_series", [])
                for point in prints:
                    hover = point.get("hover")
                    if hover:
                        series.append(hover)
            if strip:
                states = panels.setdefault("p1_states", {"data": [], "group_data": []})
                apply_strip_edge(states.setdefault("data", []), strip.get("state"))
                apply_strip_edge(states.setdefault("group_data", []), strip.get("group"))
            meta = snap.setdefault("meta", {})
            meta["live_status"] = live_status
            flow = meta.setdefault("flow", {})
            if prints:
                flow["time_end"] = prints[-1].get("x")
                flow["trade_count"] = self._session_prints
                flow["total_aggress_final"] = prints[-1].get("total_aggress")
            for key in ("p1", "p2", "p3", "p4"):
                panel = panels.get(key) or {}
                _cap_list(panel.get("data") or [], TICK_PLOT_MAX_POINTS)
            _cap_list(snap.get("flow_dominance_series") or [], BANNER_SERIES_MAX_POINTS)

    def _apply_one(self, trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = str(trade.get("dashboard_symbol") or "")
        if symbol and symbol not in self.aggression_symbols:
            return None
        ts = pd.Timestamp(trade["ts"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        key = self._trade_key(trade, ts)
        if key in self._seen_set:
            return None
        ts_ns = int(ts.value)
        if self._ts and ts_ns < self._ts[-1] - 1_000_000:
            return None
        dv01_table = self._dv01_table()
        sign = _classify_aggressor(trade.get("side"))
        unit = float(dv01_table.get(symbol, 1.0)) if self.dv01_adjust else 1.0
        size = float(trade.get("size") or 0)
        buy = size * unit if sign == 1 else 0.0
        sell = size * unit if sign == -1 else 0.0
        delta_a = buy - sell
        last_price = self._last_price()
        last_aggress = float(self._ag[-1]) if self._ag and self._ag[-1] is not None else 0.0
        last_session = self._sid[-1] if self._sid else None
        price = float(trade["price"]) if symbol == self.price_symbol else last_price
        session_id = _session_id(ts)
        if last_session is not None and last_session != session_id:
            last_aggress = 0.0
            self._path_anchor = None
            self._session_prints = 0
        self._session_prints += 1
        total_aggress = last_aggress + delta_a
        denom = buy + sell
        raw = (100.0 * delta_a / denom) if denom else float("nan")
        row = {
            "ts_ns": ts_ns,
            "dashboard_symbol": symbol,
            "sign": sign,
            "size": size,
            "buy_aggress": buy,
            "sell_aggress": sell,
            "delta_a": delta_a,
            "price": price,
            "delta_p": (price - last_price) if price is not None and last_price is not None else 0.0,
            "total_aggress": total_aggress,
            "buysell_raw": raw,
            "buysell_ratio": raw,
            "session_id": session_id,
        }
        self._push_tape(ts_ns, price, total_aggress, session_id)
        self._pending_rows.append(row)
        beta_payload = self._beta_for(ts)
        beta_ref = beta_payload.get("beta_ref") if beta_payload.get("ok") else None
        banner = self._banner_for(ts, beta_payload)
        buysell = self._update_buysell_ewm(ts, raw)
        implied = self._implied_price(total_aggress, price, beta_ref)
        x = _to_et_iso(ts)
        hover = dict(banner)
        hover["x"] = x
        self._remember(key)
        point = {
            "x": x,
            "price": price,
            "total_aggress": total_aggress,
            "buysell_ratio": buysell,
            "buy_aggress": buy,
            "sell_aggress": -abs(sell),
            "implied_price": implied,
            "banner": banner,
            "hover": hover,
        }
        self._recent_points.append({"ts_ns": ts_ns, "point": point})
        return point

    def _dv01_table(self) -> Dict[str, float]:
        if self._dv01 is None:
            self._dv01 = self.load_dv01()
        return self._dv01

    def _beta_for(self, ts: pd.Timestamp) -> Dict[str, Any]:
        """Use the in-memory beta. Read the cache only when the 5-minute bucket changes."""
        bucket = bucket_display_time(ts.to_pydatetime()).isoformat()
        if self._beta.get("ok") and self._beta_bucket == bucket:
            return self._beta
        now_m = time.monotonic()
        if self._beta and not self._beta.get("ok") and (now_m - self._beta_peek_mono) < 5:
            return self._beta
        payload = self.peek_beta(ts.to_pydatetime())
        self._beta_peek_mono = now_m
        if payload.get("ok"):
            ref = payload.get("beta_ref")
            if ref and self._beta_ref is not None and float(ref) != float(self._beta_ref):
                self.offer_history()
            self._beta = payload
            self._beta_bucket = bucket
            if ref:
                self._beta_ref = float(ref)
            return payload
        if self._beta.get("ok") and self._beta.get("session_date") == payload.get("session_date"):
            return self._beta
        self._beta = payload
        return payload

    def _last_price(self) -> Optional[float]:
        for value in reversed(self._px):
            if value is not None and math.isfinite(value):
                return float(value)
        return None

    def _push_tape(self, ts_ns: int, price: Any, aggress: Any, session_id: str) -> None:
        if self._ts:
            delta = int(ts_ns) - self._ts[-1]
            if delta > GAP_NS:
                self._gap5_ns = int(ts_ns)
            if delta > OVERNIGHT_GAP_NS:
                self._gap15_ns = int(ts_ns)
        self._ts.append(int(ts_ns))
        try:
            px = float(price) if price is not None and math.isfinite(float(price)) else None
        except (TypeError, ValueError):
            px = None
        try:
            ag = float(aggress) if aggress is not None and math.isfinite(float(aggress)) else None
        except (TypeError, ValueError):
            ag = None
        self._px.append(px)
        self._ag.append(ag)
        self._sid.append(session_id)
        if len(self._ts) % 5000 == 0:
            self._trim_tape(int(ts_ns))

    def _trim_tape(self, now_ns: int) -> None:
        cutoff = now_ns - int(self.lookback_hours * 3600 * 1_000_000_000)
        cut = bisect.bisect_left(self._ts, cutoff)
        if cut > 0:
            del self._ts[:cut]
            del self._px[:cut]
            del self._ag[:cut]
            del self._sid[:cut]
        self._pending_rows = [row for row in self._pending_rows if int(row["ts_ns"]) >= cutoff]

    def _index_tape(self, frame: pd.DataFrame) -> None:
        if frame is None or frame.empty:
            self._ts, self._px, self._ag, self._sid = [], [], [], []
            self._gap5_ns = None
            self._gap15_ns = None
            return
        idx = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
        self._ts = [int(v) for v in idx.asi8]
        prices = pd.to_numeric(frame["price"], errors="coerce").tolist() if "price" in frame.columns else [None] * len(self._ts)
        aggs = pd.to_numeric(frame["total_aggress"], errors="coerce").tolist() if "total_aggress" in frame.columns else [None] * len(self._ts)
        self._px = [_finite_num(v) for v in prices]
        self._ag = [_finite_num(v) for v in aggs]
        if "session_id" in frame.columns:
            self._sid = ["" if v is None else str(v) for v in frame["session_id"].tolist()]
        else:
            self._sid = [""] * len(self._ts)
        gap5 = gap15 = None
        prev = None
        for ts_ns in self._ts:
            if prev is not None and ts_ns - prev > GAP_NS:
                gap5 = ts_ns
            if prev is not None and ts_ns - prev > OVERNIGHT_GAP_NS:
                gap15 = ts_ns
            prev = ts_ns
        self._gap5_ns = gap5
        self._gap15_ns = gap15

    def _pending_frame(self) -> pd.DataFrame:
        rows = self._pending_rows
        if not rows:
            return pd.DataFrame()
        idx = pd.to_datetime([int(row["ts_ns"]) for row in rows], utc=True, unit="ns")
        columns = [key for key in rows[0] if key != "ts_ns"]
        data = {key: [row.get(key) for row in rows] for key in columns}
        return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name="print_ts"))

    def _banner_for(self, ts: pd.Timestamp, beta_payload: Dict[str, Any]) -> Dict[str, Any]:
        reason = None
        p_now = p_prev = a_now = a_prev = None
        if not self._ts:
            reason = earlier_print_reason(window_minutes(ts))
        else:
            now_i = len(self._ts) - 1
            end_ts = pd.Timestamp(self._ts[now_i], unit="ns", tz="UTC")
            minutes = window_minutes(end_ts)
            window_ns = int(minutes * 60 * 1_000_000_000)
            prev_i = bisect.bisect_right(self._ts, self._ts[now_i] - window_ns) - 1
            gap_ns = self._gap15_ns if minutes == OVERNIGHT_WINDOW_MIN else self._gap5_ns
            p_now = self._px[now_i]
            a_now = self._ag[now_i]
            if prev_i < 0 or (gap_ns is not None and self._ts[prev_i] < gap_ns):
                reason = earlier_print_reason(minutes)
            else:
                p_prev = self._px[prev_i]
                a_prev = self._ag[prev_i]
                if self._sid[now_i] and self._sid[prev_i] and self._sid[now_i] != self._sid[prev_i]:
                    reason = SESSION_RESET
        return banner_from_endpoints(
            price_symbol=self.price_symbol,
            as_of=ts,
            beta_payload=beta_payload,
            live_connected=True,
            p_now=p_now,
            p_prev=p_prev,
            a_now=a_now,
            a_prev=a_prev,
            window_reason=reason,
        )

    def _frame_from_trades(
        self,
        trades: Dict[str, pd.DataFrame],
        frame_start: datetime,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        return build_print_frame(
            trades,
            price_symbol=self.price_symbol,
            dv01_table=self.load_dv01(),
            dv01_adjust=self.dv01_adjust,
            session_start=frame_start,
        )

    def _store_picture(
        self,
        response: Dict[str, Any],
        frame: pd.DataFrame,
        display: pd.DataFrame,
        session_path: pd.DataFrame,
        beta_payload: Dict[str, Any],
    ) -> float:
        """Install the rebuilt picture. Returns how far prints applied meanwhile were moved."""
        p2 = ((response.get("panels") or {}).get("p2") or {}).get("data") or []
        last_p2 = p2[-1] if p2 else None
        anchor = None
        if frame is not None and not frame.empty and {"price", "total_aggress"} <= set(frame.columns):
            priced = frame.loc[frame["price"].notna()]
            if not priced.empty:
                first = priced.iloc[0]
                anchor = (float(first["price"]), float(first["total_aggress"]))
        encoded = encode_sse("snapshot", response)
        through_ns = 0
        if frame is not None and not frame.empty:
            stamp = pd.Timestamp(frame.index.max())
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            through_ns = int(stamp.value)
        with self._lock:
            kept_rows = [row for row in self._pending_rows if int(row["ts_ns"]) > through_ns]
            kept_seen = [key for key in self._seen if key[0] > through_ns]
            kept_points = [item["point"] for item in self._recent_points if int(item["ts_ns"]) > through_ns]
            self._snapshot = response
            self._snapshot_bytes = encoded
            self._snapshot_through_ns = through_ns
            self._loading = None
            self._frame = frame
            self._index_tape(display)
            kept_shift = self._continue_kept(kept_rows, kept_points, anchor, beta_payload)
            for row in kept_rows:
                self._push_tape(
                    int(row["ts_ns"]),
                    row.get("price"),
                    row.get("total_aggress"),
                    str(row.get("session_id") or ""),
                )
            self._pending_rows = kept_rows
            self._session_prints = int(len(frame)) + len(kept_rows) if frame is not None else len(kept_rows)
            self._display = display
            self._path = session_path
            self._beta = beta_payload
            self._beta_ref = float(beta_payload["beta_ref"]) if beta_payload.get("ok") and beta_payload.get("beta_ref") else None
            states_panel = (response.get("panels") or {}).get("p1_states") or {}
            self._runs = list(states_panel.get("data") or [])
            self._group_runs = list(states_panel.get("group_data") or [])
            series = response.get("flow_dominance_series") or []
            last_ready = bool(series) and state_run(series[-1], "state", series[-1].get("x")) is not None
            self._runs_open = {"state": last_ready and bool(self._runs), "group": last_ready and bool(self._group_runs)}
            self._prefix = _aggress_prefix(display)
            self._path_anchor = anchor
            if last_p2:
                self._buysell_ewm = last_p2.get("buysell_ratio")
                try:
                    self._buysell_ts = (
                        pd.Timestamp(str(last_p2.get("x")).replace(" ", "T"))
                        .tz_localize("America/New_York")
                        .tz_convert("UTC")
                    )
                except Exception:
                    self._buysell_ts = display.index.max() if not display.empty else None
            else:
                self._buysell_ewm = None
                self._buysell_ts = None
            self._seen.clear()
            self._seen_set.clear()
            if not display.empty:
                tail = display.tail(self._seen.maxlen or 8192)
                idx = pd.DatetimeIndex(pd.to_datetime(tail.index, utc=True))
                symbols = (
                    tail["dashboard_symbol"].astype(str).tolist()
                    if "dashboard_symbol" in tail.columns else [""] * len(tail)
                )
                sizes = (
                    pd.to_numeric(tail["size"], errors="coerce").fillna(0).tolist()
                    if "size" in tail.columns else [0] * len(tail)
                )
                prices = pd.to_numeric(tail["price"], errors="coerce").fillna(0).tolist() if "price" in tail.columns else [0] * len(tail)
                for ts_ns, symbol, size, price in zip(idx.asi8, symbols, sizes, prices):
                    self._remember((
                        int(ts_ns),
                        str(symbol),
                        int(round(float(size))),
                        round(float(price), 6),
                    ))
            for key in kept_seen:
                self._remember(key)
            if beta_payload.get("ok"):
                bucket = bucket_display_time(
                    pd.to_datetime(through_ns, utc=True).to_pydatetime() if through_ns else datetime.now(timezone.utc)
                )
                self._beta_bucket = bucket.isoformat()
            if kept_points:
                self._update_snapshot_tip(
                    kept_points,
                    kept_points[-1].get("banner"),
                    (response.get("meta") or {}).get("live_status") or {},
                    {},
                )
        return kept_shift

    def _continue_kept(
        self,
        rows: List[Dict[str, Any]],
        points: List[Dict[str, Any]],
        anchor: Optional[Tuple[float, float]],
        beta_payload: Dict[str, Any],
    ) -> float:
        """Re-accumulate prints applied during the rebuild onto the rebuilt tape's sum."""
        if not rows or not self._ag or self._ag[-1] is None:
            return 0.0
        running = float(self._ag[-1])
        session = self._sid[-1] if self._sid else ""
        totals: List[float] = []
        shift = 0.0
        for row in rows:
            row_session = str(row.get("session_id") or "")
            if row_session != session:
                running = 0.0
                session = row_session
            running += float(row.get("delta_a") or 0.0)
            previous = _finite_num(row.get("total_aggress"))
            if previous is not None:
                shift = max(shift, abs(running - previous))
            row["total_aggress"] = running
            totals.append(running)
        if len(points) == len(rows):
            beta_ref = _finite_num(beta_payload.get("beta_ref")) if beta_payload.get("ok") else None
            for point, total in zip(points, totals):
                point["total_aggress"] = total
                if anchor is not None and beta_ref is not None:
                    point["implied_price"] = anchor[0] + beta_ref * (total - anchor[1])
        return shift

    def _revision_event(self, display: pd.DataFrame, beta_payload: Dict[str, Any]) -> Optional[str]:
        if self._snapshot is None:
            return "snapshot"
        new_prefix = _aggress_prefix(display)
        if self._prefix and _prefix_changed(self._prefix, new_prefix):
            return "history"
        incoming = beta_payload.get("beta_ref") if beta_payload.get("ok") else None
        if (
            incoming is not None
            and self._beta_ref is not None
            and float(incoming) != float(self._beta_ref)
        ):
            return "beta"
        return None

    def _update_buysell_ewm(self, ts: pd.Timestamp, raw: float) -> Optional[float]:
        if raw is None or not math.isfinite(float(raw)):
            return self._buysell_ewm
        if self._buysell_ewm is None or self._buysell_ts is None:
            self._buysell_ewm = float(raw)
            self._buysell_ts = ts
            return self._buysell_ewm
        prev = pd.Timestamp(self._buysell_ts)
        if prev.tzinfo is None:
            prev = prev.tz_localize("UTC")
        dt = (ts - prev).total_seconds()
        half = BUYSELL_DISPLAY_HALFLIFE.total_seconds()
        if dt <= 0:
            return self._buysell_ewm
        if half <= 0:
            self._buysell_ewm = float(raw)
        else:
            decay = 0.5 ** (dt / half)
            self._buysell_ewm = decay * float(self._buysell_ewm) + (1.0 - decay) * float(raw)
        self._buysell_ts = ts
        return self._buysell_ewm

    def _implied_price(self, total_aggress: float, price: Optional[float], beta_ref: Any) -> Optional[float]:
        if beta_ref is None or not math.isfinite(float(beta_ref)):
            return None
        if self._path_anchor is None and price is not None:
            self._path_anchor = (float(price), float(total_aggress))
        if self._path_anchor is None:
            return None
        p1, a1 = self._path_anchor
        return p1 + float(beta_ref) * (float(total_aggress) - a1)

    def _strip_edge(self, last_print: Dict[str, Any]) -> Dict[str, Any]:
        """Edges for both strip rows: STATE2 under "state", STATE under "group"."""
        banner = last_print.get("banner") or {}
        x = last_print.get("x")
        edges: Dict[str, Any] = {}
        for key, runs in (("state", self._runs), ("group", self._group_runs)):
            edges[key], self._runs_open[key] = advance_runs(
                runs, state_run(banner, key, x), x, self._runs_open[key]
            )
        return edges

    def _trade_key(self, trade: Dict[str, Any], ts: pd.Timestamp) -> Tuple[int, str, int, float]:
        return (
            int(ts.value),
            str(trade.get("dashboard_symbol") or ""),
            int(round(float(trade.get("size") or 0))),
            round(float(trade.get("price") or 0), 6),
        )

    def _remember(self, key: Tuple[int, str, int, float]) -> None:
        if key in self._seen_set:
            return
        if len(self._seen) == self._seen.maxlen:
            old = self._seen.popleft()
            self._seen_set.discard(old)
        self._seen.append(key)
        self._seen_set.add(key)

    def _broadcast(self, event: str, payload: Any) -> str:
        encoded = encode_sse(event, payload)
        self._broadcast_raw(encoded)
        return encoded

    def _broadcast_raw(self, encoded: str) -> None:
        with self._subs_lock:
            subscribers = list(self._subs)
        for q in subscribers:
            self._put(q, encoded)

    def _put(self, q: queue.Queue[str], encoded: str) -> None:
        try:
            q.put_nowait(encoded)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(encoded)
            except queue.Full:
                pass
