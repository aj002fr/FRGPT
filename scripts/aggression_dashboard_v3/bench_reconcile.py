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
    parser.add_argument("--now-utc", default="15:30", help="end of the window")
    parser.add_argument("--hours", type=float, default=4.5, help="window length (the fixture starts 11:00 UTC)")
    parser.add_argument("--with-reference", action="store_true", help="also time the bisect reference matcher")
    args = parser.parse_args()

    now = pd.Timestamp(f"{args.day} {args.now_utc}", tz="UTC")
    start = now - pd.Timedelta(hours=args.hours)
    totals = {"reference_s": 0.0, "merge_ms": 0.0}
    for symbol in ("TY", "FV", "TU", "US"):
        hist = pr._as_trades(pd.read_parquet(args.fixture / f"hist_{symbol}.parquet"))
        live = pr._as_trades(pd.read_parquet(args.fixture / f"live_{symbol}.parquet"))
        hw = _window(hist, start, now - pd.Timedelta(minutes=20))
        lv = _window(live, start, now)
        merge_ms, merged = _ms(lambda: pr.reconcile_symbol(None, live=lv, historical=hw, symbol=symbol))
        line = f"{symbol}: live {len(lv):,} hist {len(hw):,} -> {len(merged):,} rows | live-first merge {merge_ms:7.1f} ms"
        if args.with_reference:
            started = time.perf_counter()
            pr._matched_incoming_reference(lv, hw, symbol)
            ref_s = time.perf_counter() - started
            totals["reference_s"] += ref_s
            line += f" | bisect reference match {ref_s:6.2f} s"
        print(line, flush=True)
        totals["merge_ms"] += merge_ms
    summary = f"all symbols: live-first merge {totals['merge_ms']:.1f} ms"
    if args.with_reference:
        summary += f", bisect reference match {totals['reference_s']:.2f} s"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
