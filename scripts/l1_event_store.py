#!/usr/bin/env python3
"""
L1 Event Store helpers.

Reads precomputed per-event feature files from disk:
  YYYY-MM-DD.meta.json
  YYYY-MM-DD.price_1m.arrow
  YYYY-MM-DD.price_5m.arrow
  YYYY-MM-DD.stats.arrow (optional)

Contract:
- Request handlers may only *read* these files and *slice* them.
- No candle aggregation in request handlers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


EventType = Literal["NFP", "CPI", "PPI", "JOLTS"]
Resolution = Literal["1m", "5m"]


@dataclass(frozen=True)
class L1Paths:
    meta: Path
    price_1m: Path
    price_5m: Path
    stats: Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_l1_root() -> Path:
    """Root directory for L1 event feature files."""
    env = os.environ.get("L1_EVENT_FEATURES_DIR")
    if env:
        return Path(env)
    return _project_root() / "data" / "event_features"


def get_event_paths(event_type: str, event_date: str) -> L1Paths:
    """Return expected L1 file paths for an event type and YYYY-MM-DD date."""
    et = event_type.upper()
    base = get_l1_root() / et / event_date
    return L1Paths(
        meta=base.with_suffix(".meta.json"),
        price_1m=base.with_suffix(".price_1m.arrow"),
        price_5m=base.with_suffix(".price_5m.arrow"),
        stats=base.with_suffix(".stats.arrow"),
    )


def load_event_meta(event_type: str, event_date: str) -> dict[str, Any]:
    paths = get_event_paths(event_type, event_date)
    with open(paths.meta, "r", encoding="utf-8") as f:
        return json.load(f)


def _require_pyarrow():
    try:
        import pyarrow as pa  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "pyarrow is required to read L1 Arrow files. Install with: pip install pyarrow"
        ) from e


def load_price_table(event_type: str, event_date: str, resolution: Resolution):
    """
    Load the full Arrow table for this event/resolution.
    Caller should slice/filter as needed.
    """
    _require_pyarrow()
    import pyarrow as pa
    import pyarrow.ipc as ipc

    paths = get_event_paths(event_type, event_date)
    file_path = paths.price_1m if resolution == "1m" else paths.price_5m
    with pa.memory_map(str(file_path), "r") as source:
        reader = ipc.RecordBatchFileReader(source)
        return reader.read_all()


def slice_by_days_around(table, event_ts_et_ns: int, days_around: int):
    """
    Slice a price table to +/- days_around around the event timestamp.

    Requires a `ts_et_ns` int64 column.
    """
    _require_pyarrow()
    import pyarrow.compute as pc

    ns_per_day = 24 * 60 * 60 * 1_000_000_000
    lo = event_ts_et_ns - (days_around * ns_per_day)
    hi = event_ts_et_ns + (days_around * ns_per_day)

    ts_col = table.column("ts_et_ns")
    mask = pc.and_(pc.greater_equal(ts_col, lo), pc.less_equal(ts_col, hi))
    return table.filter(mask)


def slice_by_ts_range(table, start_ts_et_ns: int, end_ts_et_ns: int):
    """
    Slice a price table to an absolute ET timestamp range (ns).

    Requires a `ts_et_ns` int64 column.
    """
    _require_pyarrow()
    import pyarrow.compute as pc

    ts_col = table.column("ts_et_ns")
    mask = pc.and_(pc.greater_equal(ts_col, start_ts_et_ns), pc.less_equal(ts_col, end_ts_et_ns))
    return table.filter(mask)


def table_to_candles_payload(table) -> list[dict[str, Any]]:
    """Convert an Arrow table to the candlestick JSON shape expected by the dashboard."""
    cols = {name: table.column(name).to_pylist() for name in table.schema.names}
    out: list[dict[str, Any]] = []

    ts_utc = cols.get("ts_utc")
    open_dec = cols.get("open_dec") or cols.get("open")
    high_dec = cols.get("high_dec") or cols.get("high")
    low_dec = cols.get("low_dec") or cols.get("low")
    close_dec = cols.get("close_dec") or cols.get("close")
    volume = cols.get("volume")

    for i in range(table.num_rows):
        out.append(
            {
                "ts_utc": int(ts_utc[i]) if ts_utc is not None else None,
                "open_dec": float(open_dec[i]),
                "high_dec": float(high_dec[i]),
                "low_dec": float(low_dec[i]),
                "close_dec": float(close_dec[i]),
                "volume": float(volume[i]) if volume is not None else None,
            }
        )
    return out


