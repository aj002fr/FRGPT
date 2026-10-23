#!/usr/bin/env python3
"""
Databento live overlay state (in-memory only).

Keeps last price + session high/low in memory for /api/live.
No DB writes. No historical dependencies.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass
class LiveState:
    last_price: Optional[float] = None
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    last_ts_iso: Optional[str] = None
    price_ref: Optional[float] = None
    price_ref_ts: Optional[str] = None


class DatabentoLiveState:
    def __init__(
        self,
        api_key: str,
        dataset: str,
        schema: str,
        symbol: str,
        stype_in: str = "continuous",
    ):
        self.api_key = api_key
        self.dataset = dataset
        self.schema = schema
        self.symbol = symbol
        self.stype_in = stype_in
        self.state = LiveState()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._error: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Lock reference to 3pm ET on today's date.
        # Falls back silently to first live tick if history lookup fails.
        self._initialize_reference_from_history()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def get_snapshot(self) -> dict:
        with self._lock:
            move_up_bps = None
            move_down_bps = None
            be_realized_bps = None
            if self.state.price_ref is not None and self.state.session_high is not None and self.state.session_low is not None:
                from rules.treasury_price_conversion import points_to_bps as decimal_to_bps
                move_up_bps = decimal_to_bps(self.state.session_high - self.state.price_ref)
                move_down_bps = decimal_to_bps(self.state.session_low - self.state.price_ref)
                be_realized_bps = max(move_up_bps, abs(move_down_bps))

            return {
                "timestamp_iso": self.state.last_ts_iso or datetime.now(timezone.utc).isoformat(),
                "ty_price": self.state.last_price,
                "ty_price_3pm": self.state.price_ref,
                "ty_price_3pm_timestamp": self.state.price_ref_ts,
                "high_since_3pm": self.state.session_high,
                "low_since_3pm": self.state.session_low,
                "move_up_bps": move_up_bps,
                "move_down_bps": move_down_bps,
                "be_realized_bps": be_realized_bps,
                # Provide post-event fields to keep UI stable (same values for now)
                "ty_price_830am": self.state.price_ref,
                "high_since_830am": self.state.session_high,
                "low_since_830am": self.state.session_low,
                "move_up_bps_post": move_up_bps,
                "move_down_bps_post": move_down_bps,
                "be_realized_bps_post": be_realized_bps,
                "db_file": "databento",
                "source": "databento",
                "error": self._error,
            }

    def _run(self) -> None:
        try:
            import databento as db
        except ImportError:
            self._error = "databento package not installed. Install with: pip install databento"
            return

        # Keep attempting to reconnect so /api/live does not stay stale forever
        # after transient gateway/network errors.
        while self._running:
            client = db.Live(key=self.api_key)
            try:
                client.subscribe(
                    dataset=self.dataset,
                    schema=self.schema,
                    symbols=self.symbol,
                    stype_in=self.stype_in,
                )
            except Exception as e:
                self._error = f"Databento subscribe failed: {e}"
                try:
                    client.terminate()
                except Exception:
                    pass
                time.sleep(2.0)
                continue

            try:
                for record in client:
                    if not self._running:
                        break
                    record_type = type(record).__name__
                    if record_type != "TradeMsg":
                        continue

                    price = record.price / 1e9 if hasattr(record, "price") else None
                    ts_event = record.ts_event if hasattr(record, "ts_event") else None
                    if price is None:
                        continue

                    ts_iso = datetime.fromtimestamp(ts_event / 1e9, tz=timezone.utc).isoformat() if ts_event else datetime.now(timezone.utc).isoformat()
                    with self._lock:
                        self._error = None
                        self.state.last_price = float(price)
                        self.state.last_ts_iso = ts_iso
                        if self.state.price_ref is None:
                            self.state.price_ref = float(price)
                            self.state.price_ref_ts = ts_iso
                        self.state.session_high = (
                            float(price)
                            if self.state.session_high is None
                            else max(self.state.session_high, float(price))
                        )
                        self.state.session_low = (
                            float(price)
                            if self.state.session_low is None
                            else min(self.state.session_low, float(price))
                        )
            except Exception as e:
                self._error = f"Databento stream error: {e}"
                # Fall through to terminate and reconnect.
            finally:
                try:
                    client.terminate()
                except Exception:
                    pass
            if self._running:
                time.sleep(1.0)

    def _initialize_reference_from_history(self) -> None:
        try:
            import databento as db
        except Exception:
            return

        try:
            now_et = datetime.now(ZoneInfo("America/New_York"))
            target_date = now_et.date()

            start_et = datetime.combine(target_date, datetime.strptime("15:00", "%H:%M").time(), tzinfo=ZoneInfo("America/New_York"))
            end_et = start_et + timedelta(minutes=2)
            start_utc = start_et.astimezone(timezone.utc)
            end_utc = end_et.astimezone(timezone.utc)

            client = db.Historical(key=self.api_key)
            data = client.timeseries.get_range(
                dataset=self.dataset,
                symbols=[self.symbol],
                stype_in=self.stype_in,
                schema="trades",
                start=start_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                end=end_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            df = data.to_df()
            if df is None or df.empty:
                return

            first_idx = df.index[0]
            first_row = df.iloc[0]
            px = float(first_row.get("price")) if first_row.get("price") is not None else None
            if px is None:
                return
            ts_iso = first_idx.to_pydatetime().replace(tzinfo=timezone.utc).isoformat()
            with self._lock:
                self.state.price_ref = px
                self.state.price_ref_ts = ts_iso
                self.state.session_high = px if self.state.session_high is None else max(self.state.session_high, px)
                self.state.session_low = px if self.state.session_low is None else min(self.state.session_low, px)
        except Exception as e:
            self._error = f"Databento reference lookup failed: {e}"


def _read_keys_env() -> dict:
    config_file = Path(__file__).parent.parent / "config" / "keys.env"
    if not config_file.exists():
        return {}
    values: dict[str, str] = {}
    with config_file.open("r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def get_databento_env() -> dict:
    keys_env = _read_keys_env()
    return {
        "api_key": os.environ.get("DATABENTO_API_KEY") or keys_env.get("DATABENTO_API_KEY", ""),
        "dataset": os.environ.get("DATABENTO_DATASET") or keys_env.get("DATABENTO_DATASET", "GLBX.MDP3"),
        "schema": os.environ.get("DATABENTO_SCHEMA") or keys_env.get("DATABENTO_SCHEMA", "trades"),
        "symbol": os.environ.get("DATABENTO_SYMBOL") or keys_env.get("DATABENTO_SYMBOL", "ZN.c.0"),
        "stype_in": os.environ.get("DATABENTO_STYPE_IN") or keys_env.get("DATABENTO_STYPE_IN", "continuous"),
    }


