#!/usr/bin/env python3
"""AccessAPI live TBBO buffer for the curve aggression dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from scripts.databento_live_backup import DatabentoLiveBackup, TbboEventDeduplicator

logger = logging.getLogger(__name__)

ACCESS_API_WS = "ws://100.79.30.21:8001/ws/live/stream"
SUPPORTED_SYMBOLS = ("FV", "TU", "TY", "US")
DEFAULT_MAX_AGE_HOURS = 18
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
    """Parse AccessAPI ISO timestamps to UTC datetimes."""
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _symbol_from_record(data: Dict[str, Any]) -> Optional[str]:
    """Map AccessAPI record fields to dashboard symbols."""
    bloomberg = str(data.get("bloomberg_symbol") or "").upper()
    for symbol in SUPPORTED_SYMBOLS:
        if bloomberg.startswith(symbol):
            return symbol

    continuous = str(data.get("continuous_symbol") or "").upper()
    root_map = {
        "ZF": "FV",
        "ZT": "TU",
        "ZN": "TY",
        "ZB": "US",
    }
    root = continuous.split(".")[0]
    if root in root_map:
        return root_map[root]

    raw_symbol = str(data.get("symbol") or "").upper()
    for cme_root, symbol in root_map.items():
        if raw_symbol.startswith(cme_root):
            return symbol
    return None


class LiveTradeBuffer:
    """Threaded WebSocket consumer that stores recent live TBBO trades."""

    def __init__(
        self,
        *,
        ws_url: str = ACCESS_API_WS,
        symbols: Iterable[str] = SUPPORTED_SYMBOLS,
        max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
        reconnect_delay_sec: float = 2.0,
        backup_stale_sec: Optional[float] = None,
        backup_recover_sec: Optional[float] = None,
        backup_factory: Callable[..., Any] = DatabentoLiveBackup,
    ) -> None:
        self.ws_url = ws_url
        self.symbols = tuple(symbols)
        self.max_age = timedelta(hours=max_age_hours)
        self.reconnect_delay_sec = reconnect_delay_sec
        self._lock = threading.Lock()
        self._trades: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self._mid_prices: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self._last_mid_sample_ts: Dict[str, datetime] = {}
        self._last_tick_ts: Optional[datetime] = None
        self._last_recv_ts: Optional[datetime] = None
        self._primary_last_recv_ts: Optional[datetime] = None
        self._last_event_ts: Dict[str, datetime] = {}
        self._backup_cutoff: Dict[str, datetime] = {}
        self._deduplicator = TbboEventDeduplicator()
        self._connected = False
        self._error: Optional[str] = None
        self._active_source = PRIMARY_SOURCE
        self._started_at: Optional[datetime] = None
        self._primary_recovery_since: Optional[datetime] = None
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
        self._backup = backup_factory(self._on_backup_record)
        self._thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start the background WebSocket thread once."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._thread = threading.Thread(
            target=self._thread_main,
            name="aggression-v1-accessapi",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_failover,
            name="aggression-v1-feed-monitor",
            daemon=True,
        )
        self._thread.start()
        self._monitor_thread.start()

    def stop(self) -> None:
        """Request a clean stop."""
        self._running = False
        self._backup.stop()

    def _thread_main(self) -> None:
        while self._running:
            try:
                asyncio.run(self._run_once())
            except Exception as exc:
                logger.exception("LiveTradeBuffer stream failed")
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
            with self._lock:
                self._connected = False
                self._error = "websockets package is required for live mode"
            raise exc

        logger.info("Connecting to AccessAPI live TBBO stream: %s", self.ws_url)
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            with self._lock:
                self._connected = True
                self._error = None
            greeting = await ws.recv()
            logger.info("AccessAPI live stream connected: %s", str(greeting)[:200])
            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") != "record":
                    continue
                data = msg.get("data") or {}
                self._handle_record(data, PRIMARY_SOURCE)

    def _on_backup_record(self, data: Dict[str, Any]) -> None:
        self._handle_record(data, BACKUP_SOURCE)

    def _handle_record(self, data: Dict[str, Any], source: str) -> None:
        symbol = _symbol_from_record(data)
        if symbol not in self.symbols:
            return
        ts = _parse_ts(data.get("ts_event"))
        now = datetime.now(timezone.utc)
        mid = self._record_to_mid(symbol, data)
        trade = (
            self._record_to_trade(symbol, data)
            if data.get("action") == "T"
            else None
        )
        with self._lock:
            if source == PRIMARY_SOURCE:
                self._connected = True
                self._error = None
                self._primary_last_recv_ts = now
            if ts is None or (mid is None and trade is None):
                return
            if source != self._active_source:
                return
            cutoff = self._backup_cutoff.get(symbol)
            if source == BACKUP_SOURCE and cutoff and ts < cutoff:
                return
            if not self._deduplicator.accept(symbol, data, ts):
                return
            if mid is not None:
                self._append_mid_locked(symbol, mid)
            if trade is not None:
                self._append_trade_locked(symbol, trade)
            previous = self._last_event_ts.get(symbol)
            if previous is None or ts > previous:
                self._last_event_ts[symbol] = ts

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
                unhealthy_for = (now - reference).total_seconds()
                if not primary_healthy and unhealthy_for >= self.backup_stale_sec:
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
                (self._last_event_ts.get(symbol, fallback) for symbol in self.symbols),
                default=fallback,
            )
            self._primary_recovery_since = None
        logger.warning("V1 AccessAPI stale; activating Databento backup from %s", replay_from.isoformat())
        started = self._backup.start(replay_from=replay_from)
        with self._lock:
            stopped_during_start = not self._running
            if started and not stopped_during_start:
                self._active_source = BACKUP_SOURCE
        if stopped_during_start:
            self._backup.stop()

    def _recover_primary(self) -> None:
        with self._lock:
            if self._active_source != BACKUP_SOURCE:
                return
            self._active_source = PRIMARY_SOURCE
            self._primary_recovery_since = None
            self._backup_cutoff.clear()
        logger.warning("V1 AccessAPI sustained recovery; stopping Databento backup")
        self._backup.stop()

    def _record_to_mid(self, symbol: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build a BBO mid-price record when bid/ask are valid."""
        ts = _parse_ts(data.get("ts_event"))
        if ts is None:
            return None
        try:
            bid = float(data.get("bid_price"))
            ask = float(data.get("ask_price"))
        except (TypeError, ValueError):
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        return {
            "ts": ts,
            "price": (bid + ask) / 2.0,
            "bid_price": bid,
            "ask_price": ask,
            "dashboard_symbol": symbol,
        }

    def _record_to_trade(self, symbol: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ts = _parse_ts(data.get("ts_event"))
        if ts is None:
            return None
        try:
            price = float(data.get("price"))
            size = int(float(data.get("size")))
        except (TypeError, ValueError):
            return None
        if price <= 0 or size <= 0:
            return None
        side = str(data.get("side") or "N").upper()
        raw_symbol = str(data.get("symbol") or "")
        return {
            "ts": ts,
            "price": price,
            "size": size,
            "side": side,
            "instrument_id": str(data.get("instrument_id") or raw_symbol or symbol),
            "raw_symbol": raw_symbol,
            "dashboard_symbol": symbol,
        }

    def _append_trade_locked(self, symbol: str, trade: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - self.max_age
        queue = self._trades[symbol]
        queue.append(trade)
        while queue and queue[0]["ts"] < cutoff:
            queue.popleft()
        self._last_tick_ts = trade["ts"]
        self._last_recv_ts = datetime.now(timezone.utc)

    def _append_mid_locked(self, symbol: str, mid: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - self.max_age
        last_sample = self._last_mid_sample_ts.get(symbol)
        if last_sample is not None and (mid["ts"] - last_sample).total_seconds() < 5:
            self._last_tick_ts = mid["ts"]
            self._last_recv_ts = datetime.now(timezone.utc)
            return
        queue = self._mid_prices[symbol]
        queue.append(mid)
        self._last_mid_sample_ts[symbol] = mid["ts"]
        while queue and queue[0]["ts"] < cutoff:
            queue.popleft()
        self._last_tick_ts = mid["ts"]
        self._last_recv_ts = datetime.now(timezone.utc)

    def get_trades(
        self,
        symbols: Iterable[str],
        start: datetime,
        end: datetime,
    ) -> Dict[str, pd.DataFrame]:
        """Return recent live trades in the same shape as `fetch_curve_trades`."""
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        requested = tuple(symbols)
        result: Dict[str, pd.DataFrame] = {}
        with self._lock:
            snapshot = {
                symbol: list(self._trades.get(symbol, ()))
                for symbol in requested
            }
            active_source = self._active_source

        for symbol, rows in snapshot.items():
            filtered = [
                row for row in rows
                if start_utc <= row["ts"] <= end_utc
            ]
            if filtered:
                df = pd.DataFrame(filtered)
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df.sort_values("ts", inplace=True)
                df.reset_index(drop=True, inplace=True)
                raw_symbols = sorted({str(r.get("raw_symbol") or "") for r in filtered if r.get("raw_symbol")})
                df.attrs["front_contracts"] = [
                    {
                        "date": str(start_utc.date()),
                        "instrument_id": "",
                        "raw_symbol": raw_symbols[0] if raw_symbols else "",
                        "volume": int(df["size"].sum()),
                        "source": f"{active_source}_live_tbbo",
                    }
                ]
                result[symbol] = df
            else:
                result[symbol] = pd.DataFrame(columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"])
        return result

    def get_mid_series(
        self,
        symbols: Iterable[str],
        start: datetime,
        end: datetime,
    ) -> Dict[str, pd.DataFrame]:
        """Return sampled BBO mid-price series for requested symbols."""
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        requested = tuple(symbols)
        result: Dict[str, pd.DataFrame] = {}
        with self._lock:
            snapshot = {
                symbol: list(self._mid_prices.get(symbol, ()))
                for symbol in requested
            }

        for symbol, rows in snapshot.items():
            filtered = [
                row for row in rows
                if start_utc <= row["ts"] <= end_utc
            ]
            if filtered:
                df = pd.DataFrame(filtered)
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df.set_index("ts", inplace=True)
                df.sort_index(inplace=True)
                result[symbol] = df[["price"]]
            else:
                result[symbol] = pd.DataFrame(columns=["price"])
        return result

    def status(self) -> Dict[str, Any]:
        """Return freshness metadata for the dashboard banner."""
        now = datetime.now(timezone.utc)
        backup_status = self._backup.status()
        with self._lock:
            per_symbol_last: Dict[str, Optional[str]] = {}
            for symbol in self.symbols:
                q = self._trades.get(symbol)
                per_symbol_last[symbol] = q[-1]["ts"].isoformat() if q else None
            last_tick = self._last_tick_ts
            last_recv = self._last_recv_ts
            primary_connected = self._connected
            primary_error = self._error
            primary_last_recv = self._primary_last_recv_ts
            source = self._active_source

        last_tick_age = (now - last_tick).total_seconds() if last_tick else None
        last_recv_age = (now - last_recv).total_seconds() if last_recv else None
        using_backup = source == BACKUP_SOURCE
        connected = backup_status.get("connected", False) if using_backup else primary_connected
        error = backup_status.get("error") if using_backup else primary_error
        return {
            "source": source,
            "connected": bool(connected),
            "last_tick_ts": last_tick.isoformat() if last_tick else None,
            "last_recv_ts": last_recv.isoformat() if last_recv else None,
            "last_tick_age_sec": last_tick_age,
            "last_recv_age_sec": last_recv_age,
            "per_symbol_last_ts": per_symbol_last,
            "error": error,
            "supported_symbols": list(self.symbols),
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

