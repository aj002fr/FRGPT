"""V2.2-local TBBO buffer with direct-Databento automatic failover."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, Iterable, Optional

import pandas as pd

from scripts.databento_live_backup import DatabentoLiveBackup, TbboEventDeduplicator

logger = logging.getLogger(__name__)

ACCESS_API_WS = "ws://100.79.30.21:8001/ws/live/stream"
ACCESS_API_RECENT = "http://100.79.30.21:8001/live/tbbo/recent"
SYMBOLS = ("FV", "TU", "TY", "US")
MAX_AGE_HOURS = 18
PRIMARY_SOURCE = "accessapi"
BACKUP_SOURCE = "databento_backup"
DEFAULT_BACKUP_STALE_SEC = 30.0
DEFAULT_BACKUP_RECOVER_SEC = 60.0


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            result = value
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _dashboard_symbol(data: Dict[str, Any]) -> Optional[str]:
    bloomberg = str(data.get("bloomberg_symbol") or "").upper()
    for symbol in SYMBOLS:
        if bloomberg.startswith(symbol):
            return symbol
    root_map = {"ZF": "FV", "ZT": "TU", "ZN": "TY", "ZB": "US"}
    continuous = str(data.get("continuous_symbol") or "").upper().split(".")[0]
    if continuous in root_map:
        return root_map[continuous]
    raw = str(data.get("symbol") or "").upper()
    for root, symbol in root_map.items():
        if raw.startswith(root):
            return symbol
    return None


class LiveTbboBuffer:
    """V2.2-owned AccessAPI buffer with an on-demand Databento backup."""

    def __init__(
        self,
        ws_url: str = ACCESS_API_WS,
        *,
        recent_url: str = ACCESS_API_RECENT,
        max_age_hours: int = MAX_AGE_HOURS,
        reconnect_delay_sec: float = 2.0,
        backup_stale_sec: Optional[float] = None,
        backup_recover_sec: Optional[float] = None,
        backup_factory: Callable[..., Any] = DatabentoLiveBackup,
    ) -> None:
        self.ws_url = ws_url
        self.recent_url = recent_url
        self.max_age = timedelta(hours=max_age_hours)
        self.reconnect_delay_sec = reconnect_delay_sec
        self.backup_stale_sec = (
            float(backup_stale_sec)
            if backup_stale_sec is not None and backup_stale_sec > 0
            else _positive_float_env("AGG_BACKUP_STALE_SEC", DEFAULT_BACKUP_STALE_SEC)
        )
        self.backup_recover_sec = (
            float(backup_recover_sec)
            if backup_recover_sec is not None and backup_recover_sec > 0
            else _positive_float_env("AGG_BACKUP_RECOVER_SEC", DEFAULT_BACKUP_RECOVER_SEC)
        )
        self._rows: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._error: Optional[str] = None
        self._last_tick_ts: Optional[datetime] = None
        self._last_recv_ts: Optional[datetime] = None
        self._primary_last_recv_ts: Optional[datetime] = None
        self._last_event_ts: Dict[str, datetime] = {}
        self._backup_cutoff: Dict[str, datetime] = {}
        self._deduplicator = TbboEventDeduplicator()
        self._active_source = PRIMARY_SOURCE
        self._started_at: Optional[datetime] = None
        self._primary_recovery_since: Optional[datetime] = None
        self._backup = backup_factory(self._on_backup_record)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self.seed_recent()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="aggression-v22-accessapi",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_failover,
            name="aggression-v22-feed-monitor",
            daemon=True,
        )
        self._thread.start()
        self._monitor_thread.start()

    def stop(self) -> None:
        self._running = False
        self._backup.stop()

    def seed_recent(self, n: int = 5000) -> None:
        query = urllib.parse.urlencode({"symbols": ",".join(SYMBOLS), "n": n})
        try:
            with urllib.request.urlopen(f"{self.recent_url}?{query}", timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            with self._lock:
                self._error = f"recent seed failed: {exc}"
            return
        rows = list(payload.get("data") or [])
        rows.sort(
            key=lambda data: _parse_ts(data.get("ts_recv") or data.get("ts_event"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        for data in rows:
            self._handle_record(data, PRIMARY_SOURCE, mark_primary_health=False)

    def _thread_main(self) -> None:
        while self._running:
            try:
                asyncio.run(self._run_once())
            except Exception as exc:
                logger.exception("V2.2 AccessAPI stream failed")
                with self._lock:
                    self._connected = False
                    self._error = str(exc)
            else:
                with self._lock:
                    self._connected = False
            if self._running:
                time.sleep(self.reconnect_delay_sec)

    async def _run_once(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets package is required for V2.2 live mode") from exc

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            with self._lock:
                self._connected = True
                self._error = None
            await ws.recv()
            async for raw in ws:
                if not self._running:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if message.get("type") != "record":
                    continue
                self._handle_record(message.get("data") or {}, PRIMARY_SOURCE)

    def _on_backup_record(self, data: Dict[str, Any]) -> None:
        self._handle_record(data, BACKUP_SOURCE)

    def _handle_record(
        self,
        data: Dict[str, Any],
        source: str,
        *,
        mark_primary_health: bool = True,
    ) -> None:
        symbol = _dashboard_symbol(data)
        if symbol is None:
            return
        ts = _parse_ts(data.get("ts_event"))
        now = datetime.now(timezone.utc)
        row = self._record(symbol, data)
        with self._lock:
            if source == PRIMARY_SOURCE and mark_primary_health:
                self._connected = True
                self._error = None
                self._primary_last_recv_ts = now
            if ts is None or row is None:
                return
            if source != self._active_source:
                return
            cutoff = self._backup_cutoff.get(symbol)
            if source == BACKUP_SOURCE and cutoff and ts < cutoff:
                return
            if not self._deduplicator.accept(symbol, data, ts):
                return
            self._append_locked(symbol, row)
            previous = self._last_event_ts.get(symbol)
            if previous is None or row["ts"] > previous:
                self._last_event_ts[symbol] = row["ts"]

    def _monitor_failover(self) -> None:
        interval = min(1.0, max(0.05, self.backup_stale_sec / 2.0))
        while self._running:
            now = datetime.now(timezone.utc)
            with self._lock:
                source = self._active_source
                connected = self._connected
                primary_last = self._primary_last_recv_ts
                started_at = self._started_at or now
                recovery_since = self._primary_recovery_since
            primary_healthy = bool(
                connected
                and primary_last
                and (now - primary_last).total_seconds() <= self.backup_stale_sec
            )
            if source == PRIMARY_SOURCE:
                reference = primary_last or started_at
                if (
                    not primary_healthy
                    and (now - reference).total_seconds() >= self.backup_stale_sec
                ):
                    self._activate_backup(now)
            elif primary_healthy:
                if recovery_since is None:
                    with self._lock:
                        self._primary_recovery_since = now
                elif (now - recovery_since).total_seconds() >= self.backup_recover_sec:
                    self._recover_primary()
            elif recovery_since is not None:
                with self._lock:
                    self._primary_recovery_since = None
            time.sleep(interval)

    def _activate_backup(self, now: datetime) -> None:
        with self._lock:
            if not self._running or self._active_source == BACKUP_SOURCE:
                return
            self._backup_cutoff = dict(self._last_event_ts)
            fallback = self._started_at or now - timedelta(seconds=self.backup_stale_sec)
            replay_from = min(
                (self._last_event_ts.get(symbol, fallback) for symbol in SYMBOLS),
                default=fallback,
            )
            self._active_source = BACKUP_SOURCE
            self._primary_recovery_since = None
        logger.warning("V2.2 AccessAPI stale; activating Databento backup from %s", replay_from.isoformat())
        started = self._backup.start(replay_from=replay_from)
        with self._lock:
            stopped_during_start = not self._running
            if not started and not stopped_during_start:
                self._active_source = PRIMARY_SOURCE
        if stopped_during_start:
            self._backup.stop()

    def _recover_primary(self) -> None:
        with self._lock:
            if self._active_source != BACKUP_SOURCE:
                return
            self._active_source = PRIMARY_SOURCE
            self._primary_recovery_since = None
            self._backup_cutoff.clear()
        logger.warning("V2.2 AccessAPI sustained recovery; stopping Databento backup")
        self._backup.stop()

    def _record(self, symbol: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if data.get("action") != "T":
            return None
        ts = _parse_ts(data.get("ts_event"))
        if ts is None:
            return None
        try:
            return {
                "ts": ts,
                "dashboard_symbol": symbol,
                "raw_symbol": str(data.get("symbol") or ""),
                "price": float(data.get("price")),
                "size": int(float(data.get("size"))),
                "side": str(data.get("side") or "N").upper(),
                "bid_price": float(data.get("bid_price")),
                "ask_price": float(data.get("ask_price")),
                "bid_size": int(float(data.get("bid_size"))),
                "ask_size": int(float(data.get("ask_size"))),
            }
        except (TypeError, ValueError):
            return None

    def _append_locked(self, symbol: str, row: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - self.max_age
        queue = self._rows[symbol]
        queue.append(row)
        while queue and queue[0]["ts"] < cutoff:
            queue.popleft()
        self._last_tick_ts = row["ts"]
        self._last_recv_ts = datetime.now(timezone.utc)

    def snapshot(self, symbols: Iterable[str] = SYMBOLS) -> pd.DataFrame:
        with self._lock:
            rows = [row for symbol in symbols for row in list(self._rows.get(symbol, ()))]
        if not rows:
            return pd.DataFrame(
                columns=[
                    "ts", "dashboard_symbol", "price", "size", "side",
                    "bid_price", "ask_price", "bid_size", "ask_size",
                ]
            )
        frame = pd.DataFrame(rows)
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        frame.sort_values("ts", inplace=True)
        return frame

    def status(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        backup_status = self._backup.status()
        with self._lock:
            source = self._active_source
            primary_connected = self._connected
            primary_error = self._error
            primary_last_recv = self._primary_last_recv_ts
            last_tick = self._last_tick_ts
            last_recv = self._last_recv_ts
            row_count = sum(len(queue) for queue in self._rows.values())
        using_backup = source == BACKUP_SOURCE
        connected = backup_status.get("connected", False) if using_backup else primary_connected
        error = backup_status.get("error") if using_backup else primary_error
        return {
            "source": source,
            "connected": bool(connected),
            "error": error,
            "last_tick_ts": last_tick.isoformat() if last_tick else None,
            "last_recv_ts": last_recv.isoformat() if last_recv else None,
            "last_tick_age_sec": (
                (now - last_tick).total_seconds() if last_tick else None
            ),
            "last_recv_age_sec": (
                (now - last_recv).total_seconds() if last_recv else None
            ),
            "rows": row_count,
            "failover": {
                "stale_after_sec": self.backup_stale_sec,
                "recover_after_sec": self.backup_recover_sec,
                "primary": {
                    "connected": bool(primary_connected),
                    "last_recv_ts": (
                        primary_last_recv.isoformat() if primary_last_recv else None
                    ),
                    "error": primary_error,
                },
                "backup": backup_status,
            },
        }


__all__ = ["LiveTbboBuffer"]
