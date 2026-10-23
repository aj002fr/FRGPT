#!/usr/bin/env python3
"""AccessAPI live TBBO buffer for the curve aggression dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

ACCESS_API_WS = "ws://100.79.30.21:8001/ws/live/stream"
SUPPORTED_SYMBOLS = ("FV", "TU", "TY", "US")
DEFAULT_MAX_AGE_HOURS = 18


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
        self._connected = False
        self._error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start the background WebSocket thread once."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request a clean stop."""
        self._running = False

    def _thread_main(self) -> None:
        while self._running:
            try:
                asyncio.run(self._run_once())
            except Exception as exc:
                logger.exception("LiveTradeBuffer stream failed")
                with self._lock:
                    self._connected = False
                    self._error = str(exc)
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
                symbol = _symbol_from_record(data)
                if symbol not in self.symbols:
                    continue
                mid = self._record_to_mid(symbol, data)
                if mid is not None:
                    self._append_mid(symbol, mid)
                if data.get("action") != "T":
                    continue
                trade = self._record_to_trade(symbol, data)
                if trade is not None:
                    self._append_trade(symbol, trade)

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

    def _append_trade(self, symbol: str, trade: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - self.max_age
        with self._lock:
            q = self._trades[symbol]
            q.append(trade)
            while q and q[0]["ts"] < cutoff:
                q.popleft()
            self._last_tick_ts = trade["ts"]
            self._last_recv_ts = datetime.now(timezone.utc)
            self._connected = True
            self._error = None

    def _append_mid(self, symbol: str, mid: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - self.max_age
        with self._lock:
            last_sample = self._last_mid_sample_ts.get(symbol)
            if last_sample is not None and (mid["ts"] - last_sample).total_seconds() < 5:
                # Keep the latest quote fresh for status without over-growing memory.
                self._last_tick_ts = mid["ts"]
                self._last_recv_ts = datetime.now(timezone.utc)
                self._connected = True
                self._error = None
                return
            q = self._mid_prices[symbol]
            q.append(mid)
            self._last_mid_sample_ts[symbol] = mid["ts"]
            while q and q[0]["ts"] < cutoff:
                q.popleft()
            self._last_tick_ts = mid["ts"]
            self._last_recv_ts = datetime.now(timezone.utc)
            self._connected = True
            self._error = None

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
                        "source": "accessapi_live_tbbo",
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
        with self._lock:
            per_symbol_last: Dict[str, Optional[str]] = {}
            for symbol in self.symbols:
                q = self._trades.get(symbol)
                per_symbol_last[symbol] = q[-1]["ts"].isoformat() if q else None
            last_tick = self._last_tick_ts
            last_recv = self._last_recv_ts
            connected = self._connected
            error = self._error

        last_tick_age = (now - last_tick).total_seconds() if last_tick else None
        last_recv_age = (now - last_recv).total_seconds() if last_recv else None
        return {
            "source": "accessapi_live_tbbo",
            "connected": bool(connected),
            "last_tick_ts": last_tick.isoformat() if last_tick else None,
            "last_recv_ts": last_recv.isoformat() if last_recv else None,
            "last_tick_age_sec": last_tick_age,
            "last_recv_age_sec": last_recv_age,
            "per_symbol_last_ts": per_symbol_last,
            "error": error,
            "supported_symbols": list(self.symbols),
        }

