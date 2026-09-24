from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from scripts.macro_path_navigator.constants import IRAN_HEADLINES_DB, LOCAL_STATUS_DIR
from scripts.macro_path_navigator.health import feed_status, parse_iso


class LiveDataManager:
    def __init__(self, start_price: bool = False, start_flow: bool = True) -> None:
        self.price_state = None
        self.flow_buffer = None
        self.price_error: Optional[str] = None
        self.flow_error: Optional[str] = None
        if start_price:
            self.start_price()
        if start_flow:
            self.start_flow()

    def start_price(self) -> None:
        try:
            from scripts.databento_live_state import DatabentoLiveState, get_databento_env

            env = get_databento_env()
            if not env.get("api_key"):
                self.price_error = "DATABENTO_API_KEY missing"
                return
            self.price_state = DatabentoLiveState(**env)
            self.price_state.start()
            self.price_error = None
        except Exception as exc:
            self.price_error = str(exc)

    def start_flow(self) -> None:
        try:
            from scripts.aggression_dashboard_v2.live_buffer import LiveTbboBuffer

            self.flow_buffer = LiveTbboBuffer()
            self.flow_buffer.start()
            self.flow_error = None
        except Exception as exc:
            self.flow_error = str(exc)

    def price_snapshot(self) -> Dict[str, Any]:
        if self.price_state is None:
            return {"error": self.price_error or "price stream not started", "ty_price": None}
        try:
            return self.price_state.get_snapshot()
        except Exception as exc:
            return {"error": str(exc), "ty_price": None}

    def flow_snapshot(self) -> Dict[str, Any]:
        if self.flow_buffer is None:
            return {"status": {"error": self.flow_error or "flow stream not started"}, "rows": []}
        try:
            status = self.flow_buffer.status()
            df = self.flow_buffer.snapshot(["TY"])
            rows: List[Dict[str, Any]] = []
            if not df.empty:
                tail = df.tail(1000).copy()
                for row in tail.to_dict(orient="records"):
                    ts = row.get("ts")
                    if hasattr(ts, "isoformat"):
                        row["ts"] = ts.isoformat()
                    rows.append(row)
            return {"status": status, "rows": rows}
        except Exception as exc:
            return {"status": {"error": str(exc)}, "rows": []}

    def live_session_candles(self) -> Dict[str, Any]:
        if self.flow_buffer is None:
            return {"rows": [], "mode": "none", "error": self.flow_error or "flow stream not started"}
        try:
            df = self.flow_buffer.snapshot(["TY"])
            if df.empty:
                return {"rows": [], "mode": "none"}
            df = df.copy()
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            df = df[df["ts"] >= cutoff]
            if df.empty:
                return {"rows": [], "mode": "none"}
            df.set_index("ts", inplace=True)
            candles = df.resample("1min").agg({
                "price": ["first", "max", "min", "last"],
                "size": "sum",
            })
            candles.columns = ["o", "h", "l", "c", "v"]
            candles.dropna(subset=["o", "h", "l", "c"], inplace=True)
            rows = [
                {
                    "t": ts.isoformat(),
                    "o": float(row["o"]),
                    "h": float(row["h"]),
                    "l": float(row["l"]),
                    "c": float(row["c"]),
                    "v": float(row["v"]) if pd.notna(row["v"]) else 0.0,
                }
                for ts, row in candles.iterrows()
            ]
            if len(rows) >= 2:
                return {"rows": rows, "mode": "candles", "interval": "1min"}
            line_rows = [
                {"t": row["ts"].isoformat(), "price": float(row["price"])}
                for row in df.reset_index().tail(1000).to_dict(orient="records")
            ]
            return {"rows": line_rows, "mode": "line"}
        except Exception as exc:
            return {"rows": [], "mode": "none", "error": str(exc)}

    @staticmethod
    def iran_headline_status() -> Dict[str, Any]:
        heartbeat_path = LOCAL_STATUS_DIR / "iran_headlines_stream_heartbeat.txt"
        heartbeat_ts = None
        if heartbeat_path.exists():
            try:
                raw = json.loads(heartbeat_path.read_text(encoding="utf-8"))
                heartbeat_ts = raw.get("updated_at") or raw.get("timestamp")
            except Exception:
                text = heartbeat_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                heartbeat_ts = text[0] if text else None

        latest_db_ts = None
        latest_text = None
        if IRAN_HEADLINES_DB.exists():
            try:
                with sqlite3.connect(str(IRAN_HEADLINES_DB)) as conn:
                    row = conn.execute(
                        "SELECT timestamp, text, source FROM headlines ORDER BY timestamp DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        latest_db_ts, latest_text, source = row
                        latest_text = f"{source}: {latest_text}"
            except sqlite3.Error:
                pass
        return {
            **feed_status(heartbeat_ts or latest_db_ts, stale_after=300, down_after=900),
            "latest_db_ts": latest_db_ts,
            "latest_text": latest_text,
        }

    def health_feeds(self) -> Dict[str, Dict[str, Any]]:
        price = self.price_snapshot()
        flow = self.flow_snapshot()["status"]
        return {
            "ty_price": feed_status(price.get("timestamp_iso"), stale_after=30, down_after=120, error=price.get("error")),
            "order_flow": feed_status(flow.get("last_recv_ts") or flow.get("last_tick_ts"), stale_after=30, down_after=120, error=flow.get("error")),
            "iran_headlines": self.iran_headline_status(),
        }

    def runway_payload(self) -> Dict[str, Any]:
        price = self.price_snapshot()
        flow_payload = self.flow_snapshot()
        rows = flow_payload.get("rows") or []
        current_price = price.get("ty_price")
        if current_price is None and rows:
            current_price = rows[-1].get("price")
        return {
            "price": price,
            "flow": flow_payload,
            "live_candles": self.live_session_candles(),
            "current_price": current_price,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }

