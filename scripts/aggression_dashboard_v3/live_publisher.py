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
    WINDOW_MIN,
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
    BANNER_HOVER_FIELDS,
    BANNER_SERIES_MIN_GAP,
    BUYSELL_DISPLAY_HALFLIFE,
    READY_STATE_COLORS,
    STATE_HEADLINE,
    _to_et_iso,
    build_compact_dashboard_payload,
    build_state_timeline_panel,
    collapse_ready_state_runs,
    last_panel_row,
    slim_banner_series,
)
from scripts.totalaggress_vs_price_chart import classify_aggressor as _classify_aggressor

logger = logging.getLogger(__name__)

LOOKBACK_HOURS = 2.0
HEARTBEAT_SEC = 15.0
GAP_NS = int(GAP_SEC * 1_000_000_000)
WINDOW_NS = int(WINDOW_MIN * 60 * 1_000_000_000)
NO_EARLIER_PRINT = "No print yet at the 15-minute-earlier endpoint since the session or configuration reset."
SESSION_RESET = "Banner window reset after a session boundary."
SUBSCRIBER_QUEUE_MAX = 128
REBUILD_MIN_INTERVAL_SEC = 60.0
WARMING_REBUILD_MIN_INTERVAL_SEC = 5.0
CATCH_UP_BATCH = 2000
REPLAY_MAX = 20_000
TIP_SLACK_MIN = 1.0
PREFIX_TOLERANCE = 0.5
SERIES_MARGIN = pd.Timedelta(minutes=2 * WINDOW_MIN)
EMPTY_WINDOW_REASON = "No print yet at the 15-minute-earlier endpoint since the session or configuration reset."


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


_CHUNK_ENCODER = json.JSONEncoder(separators=(",", ":"), default=_json_default)
JSON_CHUNK_ITEMS = 2000


def encode_json_chunked(value: Any) -> str:
    """Same text as ``json.dumps(value, separators=(',', ':'), default=_json_default)``.

    Dicts with string keys and long lists are encoded piece by piece, so no single C-level
    encode holds the GIL for seconds and the trade thread keeps emitting during a rebuild.
    """
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ",".join(
            f"{_CHUNK_ENCODER.encode(key)}:{encode_json_chunked(item)}" for key, item in value.items()
        ) + "}"
    if isinstance(value, list) and len(value) > JSON_CHUNK_ITEMS:
        parts = []
        for offset in range(0, len(value), JSON_CHUNK_ITEMS):
            text = _CHUNK_ENCODER.encode(value[offset:offset + JSON_CHUNK_ITEMS])
            parts.append(text[1:-1])
        return "[" + ",".join(parts) + "]"
    return _CHUNK_ENCODER.encode(value)


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
    earlier = as_of_ts - pd.Timedelta(minutes=WINDOW_MIN)
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
    if state == "WARMING" and "15-minute-earlier" in reason:
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


def _hover_row(point: Dict[str, Any]) -> Dict[str, Any]:
    banner = point.get("banner") or {}
    row = {key: banner.get(key) for key in BANNER_HOVER_FIELDS if banner.get(key) is not None}
    row["x"] = point["x"]
    return row


def _wire_point(point: Dict[str, Any]) -> Dict[str, Any]:
    """A print as sent: the hover banner rides along only when it is due (state change or 5 s)."""
    out = {key: value for key, value in point.items() if key not in ("banner", "hover_due")}
    if point.get("hover_due") and point.get("banner"):
        out["banner"] = _hover_row(point)
    return out


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
    """Frame rows from `margin` before the display start: enough for 15-minute endpoints."""
    if frame is None or frame.empty or display is None or display.empty:
        return frame
    return frame.loc[frame.index >= display.index.min() - margin]


def _tape_arrays(frame: pd.DataFrame) -> Tuple[List[int], List[Optional[float]], List[Optional[float]], List[str], Optional[int]]:
    if frame is None or frame.empty:
        return [], [], [], [], None
    idx = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
    ts = [int(v) for v in idx.asi8]
    prices = pd.to_numeric(frame["price"], errors="coerce").tolist() if "price" in frame.columns else [None] * len(ts)
    aggs = pd.to_numeric(frame["total_aggress"], errors="coerce").tolist() if "total_aggress" in frame.columns else [None] * len(ts)
    px = [_finite_num(v) for v in prices]
    ag = [_finite_num(v) for v in aggs]
    if "session_id" in frame.columns:
        sid = ["" if v is None else str(v) for v in frame["session_id"].tolist()]
    else:
        sid = [""] * len(ts)
    gap = None
    prev = None
    for ts_ns in ts:
        if prev is not None and ts_ns - prev > GAP_NS:
            gap = ts_ns
        prev = ts_ns
    return ts, px, ag, sid, gap


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
        self._rebuild_mutex = threading.Lock()
        self._last_rebuild_mono: Optional[float] = None
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
        self._gap_ns: Optional[int] = None
        self._pending_rows: List[Dict[str, Any]] = []
        self._recent_points: Deque[Dict[str, Any]] = deque(maxlen=50_000)
        self._snapshot_through_ns = 0
        self._session_prints = 0
        self._dv01: Optional[Dict[str, float]] = None
        self._beta_bucket: Optional[str] = None
        self._beta_peek_mono = 0.0
        self.resyncs = 0
        self.last_stream_lag_sec: Optional[float] = None
        self._hover_state: Optional[str] = None
        self._hover_ns: Optional[int] = None

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
            with self._subs_lock:
                self._subs.append(q)
            for message in self._opening_messages():
                self._put(q, message)
        return q

    def _opening_messages(self) -> List[str]:
        """What a new (or resynced) subscriber needs: the picture, then every print since it."""
        with self._lock:
            opening = self._snapshot_bytes
            through = self._snapshot_through_ns
            replay_points = [item["point"] for item in self._recent_points if int(item["ts_ns"]) > through][-REPLAY_MAX:]
            if opening is None:
                if self._snapshot is not None:
                    opening = encode_sse("snapshot", self._snapshot)
                elif self._loading is not None:
                    opening = encode_sse("loading", self._loading)
                else:
                    opening = encode_sse("loading", {"reason": "Waiting for the live picture."})
            messages = [opening]
            if replay_points:
                messages.append(encode_sse("trade", {
                    "prints": [_wire_point(point) for point in replay_points],
                    "flow_dominance": replay_points[-1].get("banner"),
                    "live_status": self.live_status(),
                    "server_ts": time.time() * 1000.0,
                }))
            return messages

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._subs_lock:
            if q in self._subs:
                self._subs.remove(q)

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
        """Coalesce history offers: triggers during the minimum interval fold into one rebuild."""
        while self._running:
            if not self._rebuild_event.wait(timeout=1.0):
                continue
            if not self._running:
                return
            wait = self._rebuild_wait_sec()
            if wait > 0:
                time.sleep(min(wait, 1.0))
                continue
            self._rebuild_event.clear()
            try:
                self.rebuild()
            except Exception:
                logger.exception("V3 live rebuild failed")

    def _rebuild_wait_sec(self) -> float:
        if self._last_rebuild_mono is None:
            return 0.0
        with self._lock:
            has_snapshot = self._snapshot is not None
        interval = REBUILD_MIN_INTERVAL_SEC if has_snapshot else WARMING_REBUILD_MIN_INTERVAL_SEC
        return interval - (time.monotonic() - self._last_rebuild_mono)

    def rebuild(self) -> Optional[Dict[str, Any]]:
        """Assemble the shared snapshot. Empty-tape WARMING is not stored."""
        with self._rebuild_mutex:
            self._last_rebuild_mono = time.monotonic()
            started = time.perf_counter()
            response = self._rebuild()
            logger.info(
                "V3 live rebuild %.2fs (%s)",
                time.perf_counter() - started,
                "snapshot" if response else "warming",
            )
            return response

    def _rebuild(self) -> Optional[Dict[str, Any]]:
        """Build the snapshot from the session open.

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
        if not beta_payload.get("ok") or beta_payload.get("bucket_stale"):
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
                "reason": banner.get("reason") or EMPTY_WINDOW_REASON,
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
                "lookback_hours": beta_payload.get("lookback_hours"),
                "tod_bucket": beta_payload.get("tod_bucket"),
                "reason": beta_payload.get("reason"),
                "version": beta_payload.get("version"),
            },
            "live_status": live_status,
            "label": "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE",
        }
        response = build_compact_dashboard_payload(display, display_path, tenday_frame, meta)
        series = build_banner_series(
            recent,
            beta_payload,
            price_symbol=self.price_symbol,
            as_of_index=display.index if not display.empty else None,
        )
        response["flow_dominance"] = banner
        response["flow_dominance_series"] = slim_banner_series(series)
        response["panels"]["p1_states"] = build_state_timeline_panel(series)
        prefix = _aggress_prefix(display)
        event = self._revision_event(prefix, beta_payload)
        if event is None and tail_shift > PREFIX_TOLERANCE:
            event = "history"
        self._store_picture(response, frame, display, session_path, beta_payload, prefix=prefix, event=event)
        self._catch_up_live(display.index.max() if not display.empty else lookback_start)
        return response

    def apply_trades(self, trades: List[Dict[str, Any]]) -> Optional[str]:
        """Apply prints in trade order. One message, every print, no wall-clock wait.

        Never rebuilds on this thread; a missing picture only requests one. The trade message is
        queued under the lock so it cannot overtake or trail a rebuild's history message.
        """
        with self._lock:
            pending = self._snapshot is None
        if pending:
            self.offer_history()
        live_status = self.live_status()
        with self._lock:
            prints: List[Dict[str, Any]] = []
            banner = None
            for trade in trades:
                point = self._apply_one(trade)
                if point is None:
                    continue
                prints.append(point)
                banner = point.get("banner")
            if not prints:
                return None
            if self._snapshot is None and frame_has_window_print(self._frame):
                self.offer_history()
                return None
            strip = self._strip_edge(prints[-1])
            server_ts = time.time()
            if self._ts:
                self.last_stream_lag_sec = max(0.0, server_ts - self._ts[-1] / 1e9)
            payload = {
                "prints": [_wire_point(point) for point in prints],
                "flow_dominance": banner,
                "strip": strip,
                "live_status": live_status,
                "server_ts": server_ts * 1000.0,
                "stream_lag_sec": self.last_stream_lag_sec,
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
            for offset in range(0, len(pending), CATCH_UP_BATCH):
                self.apply_trades(pending[offset:offset + CATCH_UP_BATCH])

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
                    if point.get("hover_due") and point.get("banner"):
                        series.append(_hover_row(point))
            if strip:
                states = panels.setdefault("p1_states", {"data": []})
                runs = states.setdefault("data", [])
                action = strip.get("action")
                run = strip.get("run")
                if action == "extend" and runs and run:
                    runs[-1] = dict(run)
                elif action == "open" and run:
                    runs.append(dict(run))
                elif action == "close" and runs and run:
                    runs[-1] = dict(run)
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
        self._remember(key)
        state = banner.get("state") or banner.get("status")
        hover_due = (
            state != self._hover_state
            or self._hover_ns is None
            or ts_ns - self._hover_ns >= int(BANNER_SERIES_MIN_GAP.value)
        )
        if hover_due:
            self._hover_state = state
            self._hover_ns = ts_ns
        point = {
            "x": x,
            "price": price,
            "total_aggress": total_aggress,
            "buysell_ratio": buysell,
            "buy_aggress": buy,
            "sell_aggress": -abs(sell),
            "implied_price": implied,
            "banner": banner,
            "hover_due": hover_due,
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
        if self._beta.get("ok"):
            return self._beta
        self._beta = payload
        return payload

    def _last_price(self) -> Optional[float]:
        for value in reversed(self._px):
            if value is not None and math.isfinite(value):
                return float(value)
        return None

    def _push_tape(self, ts_ns: int, price: Any, aggress: Any, session_id: str) -> None:
        if self._ts and int(ts_ns) - self._ts[-1] > GAP_NS:
            self._gap_ns = int(ts_ns)
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
            reason = NO_EARLIER_PRINT
        else:
            now_i = len(self._ts) - 1
            prev_i = bisect.bisect_right(self._ts, self._ts[now_i] - WINDOW_NS) - 1
            p_now = self._px[now_i]
            a_now = self._ag[now_i]
            if prev_i < 0 or (self._gap_ns is not None and self._ts[prev_i] < self._gap_ns):
                reason = NO_EARLIER_PRINT
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

    def _prepare_picture(
        self,
        response: Dict[str, Any],
        frame: pd.DataFrame,
        display: pd.DataFrame,
        beta_payload: Dict[str, Any],
        prefix: Tuple[Tuple[int, float], ...],
    ) -> Dict[str, Any]:
        """Everything a rebuild swap needs that does not read live state. Runs off the lock."""
        last_p2 = last_panel_row((response.get("panels") or {}).get("p2") or {})
        anchor = None
        if frame is not None and not frame.empty and {"price", "total_aggress"} <= set(frame.columns):
            priced = frame.loc[frame["price"].notna()]
            if not priced.empty:
                first = priced.iloc[0]
                anchor = (float(first["price"]), float(first["total_aggress"]))
        data = encode_json_chunked(response)
        through_ns = 0
        if frame is not None and not frame.empty:
            stamp = pd.Timestamp(frame.index.max())
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            through_ns = int(stamp.value)
        buysell_ewm = None
        buysell_ts = None
        if last_p2:
            buysell_ewm = last_p2.get("buysell_ratio")
            try:
                buysell_ts = (
                    pd.Timestamp(str(last_p2.get("x")).replace(" ", "T"))
                    .tz_localize("America/New_York")
                    .tz_convert("UTC")
                )
            except Exception:
                buysell_ts = display.index.max() if not display.empty else None
        seen: Deque[Tuple[int, str, int, float]] = deque(maxlen=self._seen.maxlen)
        seen_set: set[Tuple[int, str, int, float]] = set()
        if not display.empty:
            tail = display.tail(seen.maxlen or 8192)
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
                key = (int(ts_ns), str(symbol), int(round(float(size))), round(float(price), 6))
                if key in seen_set:
                    continue
                if len(seen) == seen.maxlen:
                    seen_set.discard(seen.popleft())
                seen.append(key)
                seen_set.add(key)
        beta_bucket = None
        if beta_payload.get("ok"):
            beta_bucket = bucket_display_time(
                pd.to_datetime(through_ns, utc=True).to_pydatetime() if through_ns else datetime.now(timezone.utc)
            ).isoformat()
        return {
            "data": data,
            "encoded": f"event: snapshot\ndata: {data}\n\n",
            "through_ns": through_ns,
            "tape": _tape_arrays(display),
            "runs": list(((response.get("panels") or {}).get("p1_states") or {}).get("data") or []),
            "prefix": prefix,
            "anchor": anchor,
            "buysell": (buysell_ewm, buysell_ts),
            "seen": seen,
            "seen_set": seen_set,
            "beta_bucket": beta_bucket,
        }

    def _store_picture(
        self,
        response: Dict[str, Any],
        frame: pd.DataFrame,
        display: pd.DataFrame,
        session_path: pd.DataFrame,
        beta_payload: Dict[str, Any],
        *,
        prefix: Optional[Tuple[Tuple[int, float], ...]] = None,
        event: Optional[str] = None,
    ) -> None:
        """Swap a rebuilt picture in. The critical section only assigns and replays newer prints.

        Existing subscribers get the same bytes as the opening snapshot, then one trade message
        with the prints that arrived during the rebuild: what a new subscriber receives.
        """
        prepared = self._prepare_picture(
            response,
            frame,
            display,
            beta_payload,
            prefix if prefix is not None else _aggress_prefix(display),
        )
        through_ns = prepared["through_ns"]
        live_status = (response.get("meta") or {}).get("live_status") or {}
        with self._lock:
            kept_rows = [row for row in self._pending_rows if int(row["ts_ns"]) > through_ns]
            kept_seen = [key for key in self._seen if key[0] > through_ns]
            kept_points = [item["point"] for item in self._recent_points if int(item["ts_ns"]) > through_ns]
            self._snapshot = response
            self._snapshot_bytes = prepared["encoded"]
            self._snapshot_through_ns = through_ns
            self._loading = None
            self._frame = frame
            self._ts, self._px, self._ag, self._sid, self._gap_ns = prepared["tape"]
            kept_shift = self._continue_kept(kept_rows, kept_points, prepared["anchor"], beta_payload)
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
            self._runs = prepared["runs"]
            self._prefix = prepared["prefix"]
            self._path_anchor = prepared["anchor"]
            self._buysell_ewm, self._buysell_ts = prepared["buysell"]
            self._seen = prepared["seen"]
            self._seen_set = prepared["seen_set"]
            for key in kept_seen:
                self._remember(key)
            if kept_shift > PREFIX_TOLERANCE:
                event = event or "history"
            if prepared["beta_bucket"] is not None:
                self._beta_bucket = prepared["beta_bucket"]
            if kept_points:
                self._update_snapshot_tip(kept_points, kept_points[-1].get("banner"), live_status, {})
            if event:
                opening = prepared["encoded"] if event == "snapshot" else f"event: {event}\ndata: {prepared['data']}\n\n"
                self._broadcast_raw(opening)
                if kept_points:
                    self._broadcast_raw(encode_sse("trade", {
                        "prints": [_wire_point(point) for point in kept_points],
                        "flow_dominance": kept_points[-1].get("banner"),
                        "live_status": live_status,
                    }))

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

    def _revision_event(self, new_prefix: Tuple[Tuple[int, float], ...], beta_payload: Dict[str, Any]) -> Optional[str]:
        if self._snapshot is None:
            return "snapshot"
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
        banner = last_print.get("banner") or {}
        x = last_print.get("x")
        state = str(banner.get("state") or "")
        ready = banner.get("status") == "READY" and state in READY_STATE_COLORS
        if ready:
            if self._runs and self._runs[-1].get("state") == state:
                self._runs[-1]["x1"] = x
                return {"action": "extend", "run": dict(self._runs[-1])}
            run = {
                "x0": x,
                "x1": x,
                "state": state,
                "headline": banner.get("headline") or STATE_HEADLINE.get(state, state.replace("_", " ")),
                "color": READY_STATE_COLORS[state],
            }
            self._runs.append(run)
            return {"action": "open", "run": dict(run)}
        if self._runs:
            self._runs[-1]["x1"] = x
            return {"action": "close", "run": dict(self._runs[-1])}
        return {"action": "gap"}

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
            self._resync(q)

    def _resync(self, q: queue.Queue[str]) -> None:
        """A subscriber that cannot keep up gets the current picture again instead of gapped deltas.

        The opening and replay already contain the message that overflowed the queue.
        """
        with self._lock:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            self.resyncs += 1
            for message in self._opening_messages():
                try:
                    q.put_nowait(message)
                except queue.Full:
                    break
