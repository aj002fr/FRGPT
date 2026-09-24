#!/usr/bin/env python3
"""Time V3 live-overlay reconciliation on a recorded US-open fixture (see golden_fixture.py)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard_v3 import print_reconcile as pr  # noqa: E402


def _window(frame: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    return frame.loc[(frame["ts"] >= lo) & (frame["ts"] <= hi)].reset_index(drop=True)


def _ms(fn, repeat: int = 3) -> tuple[float, object]:
    best = float("inf")
    out = None
    for _ in range(repeat):
        started = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - started)
    return best * 1000.0, out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--day", default="2026-09-23")
    parser.add_argument("--now-utc", default="14:30", help="end of the 2h lookback window")
    parser.add_argument("--with-reference", action="store_true", help="also time the original matcher (minutes for TY)")
    args = parser.parse_args()

    now = pd.Timestamp(f"{args.day} {args.now_utc}", tz="UTC")
    start = now - pd.Timedelta(hours=2)
    totals = {"reference_s": 0.0, "full_ms": 0.0, "cached_ms": 0.0}
    for symbol in ("TY", "FV", "TU", "US"):
        hist = pr._as_trades(pd.read_parquet(args.fixture / f"hist_{symbol}.parquet"))
        live = pr._as_trades(pd.read_parquet(args.fixture / f"live_{symbol}.parquet"))
        covered = now - pd.Timedelta(minutes=4)
        stored = _window(hist, start, covered)
        lv = _window(live, start, now)
        full_ms, _ = _ms(lambda: pr.apply_live(stored, lv, symbol))
        cache = pr.LiveOverlayCache()
        prev_start, prev_now = start - pd.Timedelta(minutes=1), now - pd.Timedelta(minutes=1)
        cache.apply_live(_window(hist, prev_start, covered), _window(live, prev_start, prev_now), symbol)
        started = time.perf_counter()
        cache.apply_live(stored, lv, symbol)
        cached_ms = (time.perf_counter() - started) * 1000.0
        line = f"{symbol}: stored {len(stored):,} live {len(lv):,} | full {full_ms:7.1f} ms | cached rebuild {cached_ms:6.1f} ms"
        if args.with_reference:
            started = time.perf_counter()
            pr._matched_incoming_reference(stored, lv, symbol)
            ref_s = time.perf_counter() - started
            totals["reference_s"] += ref_s
            line += f" | original {ref_s:7.1f} s"
        print(line, flush=True)
        totals["full_ms"] += full_ms
        totals["cached_ms"] += cached_ms
    summary = f"all symbols: full {totals['full_ms']:.1f} ms, cached rebuild {totals['cached_ms']:.1f} ms"
    if args.with_reference:
        summary += f", original {totals['reference_s']:.1f} s"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
