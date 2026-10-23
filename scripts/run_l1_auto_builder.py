#!/usr/bin/env python3
"""
Auto L1 builder for unified dashboard (missing-only).

Runs the L1 builder for NFP/CPI using the full available Postgres date range.
Designed for daily scheduling (idempotent).

Usage:
  py scripts/run_l1_auto_builder.py --events NFP,CPI
  py scripts/run_l1_auto_builder.py --events CPI --symbol TY --window-days 7
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_builder(event_type: str, symbol: str, window_days: int) -> int:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_l1_event_features.py"),
        "--event",
        event_type,
        "--symbol",
        symbol,
        "--window-days",
        str(window_days),
        "--auto-range",
    ]
    print(f"[L1-AUTO] Running: {' '.join(cmd)}")
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-build missing L1 event features")
    parser.add_argument("--events", default="NFP,CPI", help="Comma-separated list (default: NFP,CPI)")
    parser.add_argument("--symbol", default="TY", help="Symbol in ohlcv_1m (default: TY)")
    parser.add_argument("--window-days", type=int, default=7, help="Days before/after to bake (default: 7)")
    args = parser.parse_args()

    events = [e.strip().upper() for e in args.events.split(",") if e.strip()]
    if not events:
        print("ERROR: No events specified.")
        return 2

    code = 0
    for ev in events:
        rc = run_builder(ev, args.symbol, args.window_days)
        if rc != 0:
            code = rc
    return code


if __name__ == "__main__":
    raise SystemExit(main())






