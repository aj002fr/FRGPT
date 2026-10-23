#!/usr/bin/env python3
"""
Validate L1 event feature files for the unified dashboard.

Checks:
- Required files exist per event date.
- Arrow schemas contain required columns.
- Timestamps are strictly increasing.
- OHLC sanity: high >= max(open, close) and low <= min(open, close).
- meta.json required keys exist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l1_event_store import get_event_paths
from scripts.build_l1_event_features import load_event_instances


REQUIRED_META_KEYS = {"event_type", "date", "event_ts_utc", "build_version", "validation", "available_resolutions"}
REQUIRED_PRICE_COLS = {"ts_utc", "open_dec", "high_dec", "low_dec", "close_dec"}


def _require_pyarrow():
    try:
        import pyarrow as pa  # noqa: F401
        import pyarrow.ipc as ipc  # noqa: F401
    except ImportError as e:
        raise ImportError("pyarrow is required. Install with: pip install pyarrow") from e


def _read_arrow_table(path: Path):
    import pyarrow.ipc as ipc
    with path.open("rb") as f:
        return ipc.RecordBatchFileReader(f).read_all()


def _is_strictly_increasing(values: Iterable[int]) -> bool:
    it = iter(values)
    try:
        prev = next(it)
    except StopIteration:
        return True
    for v in it:
        if v <= prev:
            return False
        prev = v
    return True


def validate_one(event_type: str, date_str: str) -> list[str]:
    errors: list[str] = []
    paths = get_event_paths(event_type, date_str)

    if not paths.meta.exists():
        return [f"[{event_type} {date_str}] missing meta.json"]
    if not paths.price_1m.exists():
        errors.append(f"[{event_type} {date_str}] missing price_1m.arrow")
    if not paths.price_5m.exists():
        errors.append(f"[{event_type} {date_str}] missing price_5m.arrow")

    # Meta checks
    try:
        meta = json.loads(paths.meta.read_text())
        missing = REQUIRED_META_KEYS - set(meta.keys())
        if missing:
            errors.append(f"[{event_type} {date_str}] meta missing keys: {sorted(missing)}")
    except Exception as e:
        errors.append(f"[{event_type} {date_str}] meta.json read error: {e}")

    # Arrow checks
    for label, path in [("1m", paths.price_1m), ("5m", paths.price_5m)]:
        if not path.exists():
            continue
        try:
            table = _read_arrow_table(path)
        except Exception as e:
            errors.append(f"[{event_type} {date_str}] {label} arrow read error: {e}")
            continue
        cols = set(table.column_names)
        missing_cols = REQUIRED_PRICE_COLS - cols
        if missing_cols:
            errors.append(f"[{event_type} {date_str}] {label} missing cols: {sorted(missing_cols)}")
            continue
        ts = table.column("ts_utc").to_pylist()
        if not _is_strictly_increasing(ts):
            errors.append(f"[{event_type} {date_str}] {label} timestamps not strictly increasing")

        opens = table.column("open_dec").to_pylist()
        highs = table.column("high_dec").to_pylist()
        lows = table.column("low_dec").to_pylist()
        closes = table.column("close_dec").to_pylist()
        for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
            if o is None or h is None or l is None or c is None:
                errors.append(f"[{event_type} {date_str}] {label} row {i} has null OHLC")
                break
            if h < max(o, c) or l > min(o, c):
                errors.append(f"[{event_type} {date_str}] {label} row {i} OHLC inconsistency")
                break

    return errors


def _iter_event_dates(event_type: str, start_date: str | None, end_date: str | None) -> list[str]:
    events = load_event_instances(event_type, start_date, end_date)
    return [ev["event_date"] for ev in events]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate L1 event features")
    parser.add_argument("--event", required=True, choices=["NFP", "CPI", "PPI", "JOLTS", "all"], help="Event type")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (optional; validates single date)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (optional; range validation)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (optional; range validation)")
    args = parser.parse_args()

    _require_pyarrow()
    event_types = ["NFP", "CPI", "PPI", "JOLTS"] if args.event == "all" else [args.event]

    all_errors: list[str] = []
    for et in event_types:
        if args.date:
            dates = [args.date]
        else:
            dates = _iter_event_dates(et, args.start_date, args.end_date)
        for d in dates:
            all_errors.extend(validate_one(et, d))

    if all_errors:
        for e in all_errors:
            print(e)
        return 1
    print("[OK] L1 validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

