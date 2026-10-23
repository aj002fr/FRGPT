#!/usr/bin/env python3
"""
Run the L1 builder for one or more event types (NFP/CPI/PPI/JOLTS).

Usage examples:
  py scripts/run_l1_builder.py --event CPI --start-date 2024-01-01 --end-date 2024-12-31
  py scripts/run_l1_builder.py --event all --since-days 21
  py scripts/run_l1_builder.py --event all --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_l1_event_features import load_event_instances, build_one_event


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def run_for_event(event_type: str, symbol: str, window_days: int, start_date: str | None, end_date: str | None, force: bool) -> tuple[int, int]:
    events = load_event_instances(event_type, start_date, end_date)
    built = 0
    skipped = 0
    for ev in events:
        before = False
        if not force:
            from scripts.l1_event_store import get_event_paths
            before = get_event_paths(event_type, ev["event_date"]).price_1m.exists()
        build_one_event(event_type, symbol, window_days, ev, force=force)
        after = True
        if not force:
            from scripts.l1_event_store import get_event_paths
            after = get_event_paths(event_type, ev["event_date"]).price_1m.exists()
        if after and (force or not before):
            built += 1
        else:
            skipped += 1
    return built, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Run L1 builder for NFP/CPI/PPI/JOLTS")
    parser.add_argument("--event", default="all", choices=["NFP", "CPI", "PPI", "JOLTS", "all"], help="Event type to build")
    parser.add_argument("--symbol", default="TY", help="Instrument symbol (default: TY)")
    parser.add_argument("--window-days", type=int, default=7, help="Days before/after event to bake (default: 7)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (optional)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (optional)")
    parser.add_argument("--since-days", type=int, default=None, help="Build events in the last N days")
    parser.add_argument("--force", action="store_true", help="Rebuild even if files exist")
    args = parser.parse_args()

    if args.since_days is not None:
        end_date = _date_str(datetime.now())
        start_date = _date_str(datetime.now() - timedelta(days=args.since_days))
    else:
        start_date = args.start_date
        end_date = args.end_date

    event_types = ["NFP", "CPI", "PPI", "JOLTS"] if args.event == "all" else [args.event]
    total_built = 0
    total_skipped = 0
    for et in event_types:
        built, skipped = run_for_event(et, args.symbol, args.window_days, start_date, end_date, args.force)
        total_built += built
        total_skipped += skipped
        print(f"[L1] {et}: built={built}, skipped={skipped}")

    print(f"[L1] Total: built={total_built}, skipped={total_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


