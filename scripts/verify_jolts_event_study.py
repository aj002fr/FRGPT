#!/usr/bin/env python3
"""
Verify JOLTS Event Study Dashboard Data
========================================

Sanity-checks event coverage, mode distribution, and data availability
for the JOLTS event study dashboard.

Usage:
    python scripts/verify_jolts_event_study.py
    python scripts/verify_jolts_event_study.py --start-date 2020-01-01
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings

ET = ZoneInfo("America/New_York")

# Event name constants (must match the dashboard script)
JOLTS_EVENT_NAME = "JOLTs Job Openings"  # Note: lowercase 's' in JOLTs
ISM_EVENT_NAME = "ISM Manufacturing PMI"


def get_db_path() -> Path:
    return settings.ECONOMIC_EVENTS_DB_PATH


def check_event_exists(event_name: str, start_date: str) -> dict:
    """Check if events exist for a given event name."""
    db_path = get_db_path()
    if not db_path.exists():
        return {"error": f"DB not found: {db_path}"}
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Count events
    cur.execute(
        """
        SELECT COUNT(*) FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' AND event_date >= ?
        """,
        (event_name, f"{start_date}T00:00:00"),
    )
    count = cur.fetchone()[0]
    
    # Get date range
    cur.execute(
        """
        SELECT MIN(event_date), MAX(event_date) FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' AND event_date >= ?
        """,
        (event_name, f"{start_date}T00:00:00"),
    )
    min_date, max_date = cur.fetchone()
    
    # Check if actual values exist
    cur.execute(
        """
        SELECT COUNT(*) FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' 
          AND event_date >= ? AND actual IS NOT NULL
        """,
        (event_name, f"{start_date}T00:00:00"),
    )
    with_actual = cur.fetchone()[0]
    
    # Check if consensus_std_dev exists
    cur.execute(
        """
        SELECT COUNT(*) FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' 
          AND event_date >= ? AND consensus_std_dev IS NOT NULL
        """,
        (event_name, f"{start_date}T00:00:00"),
    )
    with_std = cur.fetchone()[0]
    
    conn.close()
    
    return {
        "event_name": event_name,
        "count": count,
        "with_actual": with_actual,
        "with_consensus_std_dev": with_std,
        "min_date": min_date,
        "max_date": max_date,
    }


def check_mode_distribution(start_date: str) -> dict:
    """Check the distribution of JOLTS events by mode (with_ism vs standalone)."""
    db_path = get_db_path()
    if not db_path.exists():
        return {"error": "DB not found"}
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Get JOLTS events
    cur.execute(
        """
        SELECT event_date FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' AND event_date >= ?
        ORDER BY event_date ASC
        """,
        (JOLTS_EVENT_NAME, f"{start_date}T00:00:00"),
    )
    jolts_dates = [row[0] for row in cur.fetchall()]
    
    # Get ISM events as a set of dates
    cur.execute(
        """
        SELECT event_date FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' AND event_date >= ?
        """,
        (ISM_EVENT_NAME, f"{start_date}T00:00:00"),
    )
    ism_dates_raw = [row[0] for row in cur.fetchall()]
    conn.close()
    
    # Parse ISM dates
    ism_date_set = set()
    for date_str in ism_dates_raw:
        try:
            dt = datetime.fromisoformat(date_str.replace(" ", "T"))
            ism_date_set.add(dt.strftime("%Y-%m-%d"))
        except:
            pass
    
    # Classify JOLTS events
    with_ism = 0
    standalone = 0
    
    for jolts_date_str in jolts_dates:
        try:
            dt = datetime.fromisoformat(jolts_date_str.replace(" ", "T"))
            # Get previous weekday
            prev = dt - timedelta(days=1)
            while prev.weekday() >= 5:
                prev -= timedelta(days=1)
            prev_date_str = prev.strftime("%Y-%m-%d")
            
            if prev_date_str in ism_date_set:
                with_ism += 1
            else:
                standalone += 1
        except:
            standalone += 1
    
    return {
        "total_jolts": len(jolts_dates),
        "with_ism": with_ism,
        "standalone": standalone,
        "with_ism_pct": round(with_ism / len(jolts_dates) * 100, 1) if jolts_dates else 0,
    }


def check_timestamp_ordering(event_name: str, start_date: str) -> dict:
    """Check if event timestamps are properly ordered."""
    db_path = get_db_path()
    if not db_path.exists():
        return {"error": "DB not found"}
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    cur.execute(
        """
        SELECT event_date FROM economic_events
        WHERE event_name = ? AND country LIKE '%United States%' AND event_date >= ?
        ORDER BY event_date ASC
        """,
        (event_name, f"{start_date}T00:00:00"),
    )
    dates = [row[0] for row in cur.fetchall()]
    conn.close()
    
    # Check ordering
    out_of_order = 0
    for i in range(1, len(dates)):
        if dates[i] < dates[i - 1]:
            out_of_order += 1
    
    return {
        "event_name": event_name,
        "total": len(dates),
        "out_of_order": out_of_order,
        "ordered": out_of_order == 0,
    }


def check_similar_events(event_name: str) -> list:
    """Find similar event names in the database."""
    db_path = get_db_path()
    if not db_path.exists():
        return []
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Get first word
    first_word = event_name.split()[0]
    cur.execute(
        """
        SELECT DISTINCT event_name FROM economic_events
        WHERE event_name LIKE ? AND country LIKE '%United States%'
        LIMIT 10
        """,
        (f"%{first_word}%",),
    )
    similar = [row[0] for row in cur.fetchall()]
    conn.close()
    
    return similar


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify JOLTS event study data")
    parser.add_argument("--start-date", default="2022-01-01", help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    print("=" * 60)
    print("JOLTS Event Study Data Verification")
    print("=" * 60)
    print(f"Start date: {args.start_date}")
    print(f"DB path: {get_db_path()}")
    print()
    
    # Check JOLTS events
    print("[1] Checking JOLTS events...")
    jolts_info = check_event_exists(JOLTS_EVENT_NAME, args.start_date)
    if "error" in jolts_info:
        print(f"  [ERROR] {jolts_info['error']}")
        return 2
    
    print(f"  Event: {jolts_info['event_name']}")
    print(f"  Count: {jolts_info['count']}")
    print(f"  With actual: {jolts_info['with_actual']}")
    print(f"  With consensus_std_dev: {jolts_info['with_consensus_std_dev']}")
    print(f"  Date range: {jolts_info['min_date']} to {jolts_info['max_date']}")
    
    if jolts_info["count"] == 0:
        print(f"  [WARN] No JOLTS events found!")
        similar = check_similar_events(JOLTS_EVENT_NAME)
        if similar:
            print(f"  Did you mean: {', '.join(similar[:5])}")
        return 1
    print("  [OK]")
    print()
    
    # Check ISM events
    print("[2] Checking ISM events...")
    ism_info = check_event_exists(ISM_EVENT_NAME, args.start_date)
    if "error" in ism_info:
        print(f"  [ERROR] {ism_info['error']}")
        return 2
    
    print(f"  Event: {ism_info['event_name']}")
    print(f"  Count: {ism_info['count']}")
    print(f"  With actual: {ism_info['with_actual']}")
    print(f"  Date range: {ism_info['min_date']} to {ism_info['max_date']}")
    
    if ism_info["count"] == 0:
        print(f"  [WARN] No ISM events found!")
        similar = check_similar_events(ISM_EVENT_NAME)
        if similar:
            print(f"  Did you mean: {', '.join(similar[:5])}")
    else:
        print("  [OK]")
    print()
    
    # Check mode distribution
    print("[3] Checking mode distribution...")
    mode_info = check_mode_distribution(args.start_date)
    if "error" in mode_info:
        print(f"  [ERROR] {mode_info['error']}")
    else:
        print(f"  Total JOLTS: {mode_info['total_jolts']}")
        print(f"  With ISM (prev day): {mode_info['with_ism']} ({mode_info['with_ism_pct']}%)")
        print(f"  Standalone: {mode_info['standalone']}")
        
        if mode_info["with_ism"] == 0:
            print("  [WARN] No JOLTS events follow ISM - check event names/dates")
        else:
            print("  [OK]")
    print()
    
    # Check timestamp ordering
    print("[4] Checking timestamp ordering...")
    jolts_order = check_timestamp_ordering(JOLTS_EVENT_NAME, args.start_date)
    ism_order = check_timestamp_ordering(ISM_EVENT_NAME, args.start_date)
    
    if jolts_order.get("ordered") and ism_order.get("ordered"):
        print("  JOLTS: ordered [OK]")
        print("  ISM: ordered [OK]")
        print("  [OK]")
    else:
        if not jolts_order.get("ordered"):
            print(f"  JOLTS: {jolts_order['out_of_order']} out of order")
        if not ism_order.get("ordered"):
            print(f"  ISM: {ism_order['out_of_order']} out of order")
        print("  [WARN] Some timestamps out of order")
    print()
    
    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    if jolts_info["count"] > 0 and mode_info.get("with_ism", 0) > 0:
        print("[PASS] Data looks good for JOLTS event study dashboard")
        print(f"  Ready to run: python scripts/jolts_event_study_dashboard.py --start-date {args.start_date}")
        return 0
    else:
        print("[WARN] Some data may be missing - review warnings above")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

