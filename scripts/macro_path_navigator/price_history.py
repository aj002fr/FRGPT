from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

import pandas as pd

from src.servers.distro_analytics.db_reader import get_ohlcv_from_db, get_pg_connection

WINDOW_TO_DELTA = {
    "1D": timedelta(days=1),
    "5D": timedelta(days=5),
    "1M": timedelta(days=31),
    "3M": timedelta(days=92),
    "6M": timedelta(days=183),
    "1Y": timedelta(days=366),
}

WINDOW_TO_INTERVAL = {
    "1D": "1min",
    "5D": "5min",
    "1M": "30min",
    "3M": "1h",
    "6M": "1D",
    "1Y": "1D",
    "ALL": "1W",
}


def _coverage_end(symbol_pattern: str = "TY%") -> pd.Timestamp:
    conn = get_pg_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(ts_event) FROM ohlcv_1m WHERE symbol ILIKE %s", (symbol_pattern,))
        value = cur.fetchone()[0]
    finally:
        conn.close()
    if value is None:
        raise ValueError(f"No OHLCV coverage found for {symbol_pattern}")
    return pd.Timestamp(value).tz_convert("UTC") if pd.Timestamp(value).tzinfo else pd.Timestamp(value, tz="UTC")


def _resample(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if interval in ("1min", "1T"):
        out = df.copy()
    else:
        out = df.resample(interval).agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        })
        out.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    return out


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "t": pd.Timestamp(ts).isoformat(),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
            "v": float(row["Volume"]) if pd.notna(row["Volume"]) else 0.0,
        })
    return rows


def load_history(window: str = "1M", interval: Optional[str] = None, symbol_pattern: str = "TY%") -> Dict[str, Any]:
    window = (window or "1M").upper()
    interval = interval or WINDOW_TO_INTERVAL.get(window, "30min")
    end_ts = _coverage_end(symbol_pattern)
    if window == "ALL":
        start_text = None
    else:
        delta = WINDOW_TO_DELTA.get(window, WINDOW_TO_DELTA["1M"])
        start_text = (end_ts - delta).strftime("%Y-%m-%d %H:%M:%S")
    end_text = end_ts.strftime("%Y-%m-%d %H:%M:%S")

    df = get_ohlcv_from_db(symbol_pattern, start_date=start_text, end_date=end_text)
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    else:
        df.index = pd.to_datetime(df.index).tz_convert("UTC")
    candles = _resample(df, interval)
    return {
        "window": window,
        "interval": interval,
        "coverage_end": end_ts.isoformat(),
        "rows": _records(candles),
        "row_count": int(len(candles)),
    }

