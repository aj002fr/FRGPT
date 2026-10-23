#!/usr/bin/env python3
"""
Refresh data backing the unified live event dashboard.

This orchestrates the slow/off-request-path work:
1. Optional economic-events staleness check.
2. Optional Databento -> PostgreSQL TY backfill.
3. L1 event feature rebuild.
4. Live unified dashboard cache rebuild.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_EVENTS = ("NFP", "CPI", "PPI", "JOLTS")


def _run(cmd: list[str], *, check: bool = True) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    print(f"[refresh] running: {' '.join(cmd)}")
    rc = subprocess.call(cmd, cwd=str(PROJECT_ROOT), env=env)
    if check and rc != 0:
        raise RuntimeError(f"Command failed with exit code {rc}: {' '.join(cmd)}")
    return rc


def run_staleness_check() -> None:
    script = PROJECT_ROOT / "scripts" / "check_economic_events_db_staleness.py"
    if script.exists():
        _run([sys.executable, str(script)], check=False)
    else:
        print("[refresh] staleness checker not found, skipping")


def run_market_backfill(dates: list[str] | None, dry_run: bool) -> None:
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "backfill_ty_from_databento.py")]
    if dates:
        cmd.extend(["--dates", *dates])
    if dry_run:
        cmd.append("--dry-run")
    _run(cmd)


def rebuild_l1(events: list[str], force: bool, window_days: int) -> None:
    for event in events:
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_l1_event_features.py"),
            "--event",
            event,
            "--symbol",
            "TY",
            "--window-days",
            str(window_days),
            "--auto-range",
        ]
        if force:
            cmd.append("--force")
        _run(cmd)


def rebuild_dashboard_cache(events: list[str]) -> None:
    from scripts import live_unified_dashboard as lud

    for event in events:
        print(f"[refresh] rebuilding dashboard cache: {event}")
        raw = lud.load_event_data(event)
        dashboard_data = lud.build_dashboard_data(raw, event_type=event)
        lud.save_dashboard_cache(event, dashboard_data)
        dates = sorted(e.get("date") for e in dashboard_data.get("events", []) if e.get("date"))
        print(f"[refresh] {event} cache events={len(dates)} last={dates[-5:] if dates else []}")


def parse_events(value: str) -> list[str]:
    events = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not events:
        raise argparse.ArgumentTypeError("events cannot be empty")
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh unified dashboard backing data.")
    parser.add_argument("--events", type=parse_events, default=list(DEFAULT_EVENTS), help="Comma-separated event types")
    parser.add_argument("--window-days", type=int, default=7, help="L1 days before/after each event")
    parser.add_argument("--force-l1", action="store_true", help="Force rebuild existing L1 files")
    parser.add_argument("--backfill-dates", nargs="*", default=None, help="Optional TY backfill event dates")
    parser.add_argument("--dry-run-backfill", action="store_true", help="Fetch/validate backfill without inserting")
    parser.add_argument("--skip-staleness", action="store_true", help="Skip economic-events staleness check")
    parser.add_argument("--skip-backfill", action="store_true", help="Skip Databento TY backfill")
    parser.add_argument("--skip-l1", action="store_true", help="Skip L1 rebuild")
    parser.add_argument("--skip-cache", action="store_true", help="Skip dashboard cache rebuild")
    args = parser.parse_args()

    if not args.skip_staleness:
        run_staleness_check()
    if not args.skip_backfill:
        run_market_backfill(args.backfill_dates, args.dry_run_backfill)
    if not args.skip_l1:
        rebuild_l1(args.events, args.force_l1, args.window_days)
    if not args.skip_cache:
        rebuild_dashboard_cache(args.events)

    print("[refresh] complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
