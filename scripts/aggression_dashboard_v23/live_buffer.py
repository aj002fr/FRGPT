from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Iterable, Optional

import pandas as pd


ACCESS_API_WS = "ws://100.79.30.21:8001/ws/live/stream"
ACCESS_API_RECENT = "http://100.79.30.21:8001/live/tbbo/recent"
SYMBOLS = ("FV", "TU", "TY", "US")
MAX_AGE_HOURS = 18


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _dashboard_symbol(data: Dict[str, Any]) -> Optional[str]:
    bloomberg = str(data.get("bloomberg_symbol") or "").upper()
    for symbol in SYMBOLS:
        if bloomberg.startswith(symbol):
            return symbol
    raw = str(data.get("symbol") or "").upper()
    root_map = {"ZF": "FV", "ZT": "TU", "ZN": "TY", "ZB": "US"}
    for root, symbol in root_map.items():
        if raw.startswith(root):
            return symbol
    return None


class LiveTbboBuffer:
    def __init__(self, ws_url: str = ACCESS_API_WS, max_age_hours: int = MAX_AGE_HOURS) -> None:
        self.ws_url = ws_url
        self.max_age = timedelta(hours=max_age_hours)
        self._rows: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._error: str | None = None
        self._last_tick_ts: datetime | None = None
        self._last_recv_ts: datetime | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.seed_recent()
        self._running = True
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def seed_recent(self, n: int = 5000) -> None:
        query = urllib.parse.urlencode({"symbols": ",".join(SYMBOLS), "n": n})
        try:
            with urllib.request.urlopen(f"{ACCESS_API_RECENT}?{query}", timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            with self._lock:
                self._error = f"recent seed failed: {exc}"
            return
        for data in payload.get("data") or []:
            symbol = _dashboard_symbol(data)
            if symbol is None:
                continue
            row = self._record(symbol, data)
            if row:
                self._append(symbol, row)

    def _thread_main(self) -> None:
        while self._running:
            try:
                asyncio.run(self._run_once())
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._error = str(exc)
            if self._running:
                time.sleep(2.0)

    async def _run_once(self) -> None:
        import websockets

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            with self._lock:
                self._connected = True
                self._error = None
            await ws.recv()
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
                symbol = _dashboard_symbol(data)
                if symbol is None:
                    continue
                row = self._record(symbol, data)
                if row:
                    self._append(symbol, row)

    def _record(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any] | None:
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

    def _append(self, symbol: str, row: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - self.max_age
        with self._lock:
            q = self._rows[symbol]
            q.append(row)
            while q and q[0]["ts"] < cutoff:
                q.popleft()
            self._last_tick_ts = row["ts"]
            self._last_recv_ts = datetime.now(timezone.utc)
            self._connected = True
            self._error = None

    def snapshot(self, symbols: Iterable[str] = SYMBOLS) -> pd.DataFrame:
        with self._lock:
            rows = [row for symbol in symbols for row in list(self._rows.get(symbol, ()))]
        if not rows:
            return pd.DataFrame(columns=["ts", "dashboard_symbol", "price", "size", "side", "bid_price", "ask_price", "bid_size", "ask_size"])
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df.sort_values("ts", inplace=True)
        return df

    def status(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._lock:
            return {
                "connected": self._connected,
                "error": self._error,
                "last_tick_ts": self._last_tick_ts.isoformat() if self._last_tick_ts else None,
                "last_recv_ts": self._last_recv_ts.isoformat() if self._last_recv_ts else None,
                "last_tick_age_sec": (now - self._last_tick_ts).total_seconds() if self._last_tick_ts else None,
                "last_recv_age_sec": (now - self._last_recv_ts).total_seconds() if self._last_recv_ts else None,
                "rows": sum(len(q) for q in self._rows.values()),
            }
