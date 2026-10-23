#!/usr/bin/env python3
"""
Update Economic Events DB from Excel Files
-------------------------------------------
Adds consensus_std_dev column and updates consensus values from Bloomberg Excel files.

Supported Excel files (in Z:/Meini/):
- NFP.xlsx: Non Farm Payrolls + Unemployment Rate (consensus + std dev)
- CPI.xlsx: Consumer Price Index (if data available)
- Jobless Claims Test.xlsx: Initial Jobless Claims (consensus only, no std dev)

Usage:
    python scripts/update_db_from_excel.py [--dry-run]
"""

import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import argparse

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
EXCEL_FOLDER_NFP = Path("Z:/Meini")
EXCEL_FOLDER_CPI = PROJECT_ROOT / "events data"

# Excel file configurations
# Format: path -> config
EXCEL_CONFIGS = [
    # ============ NFP.xlsx (Z:/Meini) ============
    {
        "path": EXCEL_FOLDER_NFP / "NFP.xlsx",
        "engine": None,  # Default openpyxl
        "events": [
            {
                "db_event_name": "Non Farm Payrolls",
                "date_col": 0,       # Column A: NFP Date
                "consensus_col": 1,  # Column B: NFP Consensus
                "std_col": 5,        # Column F: NFP Std Dev
                "release_date_col": 6,  # Column G: Release Date
                "header_rows": 3,    # Skip first 3 header rows
                "consensus_multiplier": 1000,  # Excel shows in K, DB stores raw
            },
            {
                "db_event_name": "Unemployment Rate",
                "date_col": 2,       # Column C: UER Date
                "consensus_col": 3,  # Column D: UER Consensus
                "std_col": 8,        # Column I: UER Std Dev
                "release_date_col": 9,  # Column J: UER Release Date
                "header_rows": 3,
                "consensus_multiplier": 1,  # UER is percentage, no conversion
            },
        ]
    },
    # ============ Jobless Claims Test.xlsx (Z:/Meini) ============
    {
        "path": EXCEL_FOLDER_NFP / "Jobless Claims Test.xlsx",
        "engine": None,
        "events": [
            {
                "db_event_name": "Initial Jobless Claims",
                "date_col": 0,       # Column A: Date
                "consensus_col": 1,  # Column B: Consensus
                "std_col": None,     # No std dev column
                "release_date_col": None,
                "header_rows": 0,    # No header rows
                "consensus_multiplier": 1000,  # Excel shows in K
            },
        ]
    },
    # ============ CPI.xlsx (Z:/Meini) - Core CPI & CPI MoM ============
    {
        "path": EXCEL_FOLDER_NFP / "CPI.xlsx",
        "engine": None,
        "events": [
            {
                "db_event_name": "Core Inflation Rate MoM",  # Core CPI MoM in DB
                "date_col": 0,        # Column A: Release Date
                "consensus_col": 1,   # Column B: Core CPI MoM Consensus
                "std_col": 3,         # Column D: Core CPI MoM Std Dev
                "header_rows": 3,     # Skip 3 header rows
                "consensus_multiplier": 1,
            },
            {
                "db_event_name": "Inflation Rate MoM",  # CPI MoM in DB
                "date_col": 5,        # Column F: Release Date
                "consensus_col": 6,   # Column G: CPI MoM Consensus
                "std_col": 8,         # Column I: CPI MoM Std Dev
                "header_rows": 3,
                "consensus_multiplier": 1,
            },
        ]
    },
    # ============ cpi est.xlsb (events data folder) - Additional CPI data ============
    {
        "path": EXCEL_FOLDER_CPI / "cpi est.xlsb",
        "engine": "pyxlsb",
        "sheet_name": "CPI",
        "events": [
            {
                "db_event_name": "Inflation Rate MoM",  # CPI MoM
                "date_col": 3,        # Column D: Date (Excel serial)
                "consensus_col": 11,  # Column L: momCPI_cons
                "actual_col": 10,     # Column K: momCPI_act
                "std_col": 13,        # Column N: momCPI_cons_std
                "header_rows": 1,     # Skip 1 header row
                "consensus_multiplier": 1,
                "date_is_serial": True,  # Excel serial date
            },
            {
                "db_event_name": "Core Inflation Rate MoM",  # Core CPI MoM
                "date_col": 3,        # Column D: Date
                "consensus_col": 16,  # Column Q: momCore_cons
                "actual_col": 15,     # Column P: momCore_act
                "std_col": 18,        # Column S: momCore_cons_std
                "header_rows": 1,
                "consensus_multiplier": 1,
                "date_is_serial": True,
            },
            {
                "db_event_name": "Inflation Rate YoY",  # CPI YoY
                "date_col": 3,        # Column D: Date
                "consensus_col": 21,  # Column V: yoyCPI_cons
                "actual_col": 20,     # Column U: yoyCPI_act
                "std_col": 23,        # Column X: yoyCPI_cons_std
                "header_rows": 1,
                "consensus_multiplier": 1,
                "date_is_serial": True,
            },
            {
                "db_event_name": "Core Inflation Rate YoY",  # Core CPI YoY
                "date_col": 3,        # Column D: Date
                "consensus_col": 26,  # Column AA: yoyCore_cons
                "actual_col": 25,     # Column Z: yoyCore_act
                "std_col": 28,        # Column AC: yoyCore_cons_std
                "header_rows": 1,
                "consensus_multiplier": 1,
                "date_is_serial": True,
            },
        ]
    },
]


def add_consensus_std_dev_column(conn: sqlite3.Connection) -> bool:
    """Add consensus_std_dev column to economic_events table if not exists."""
    cursor = conn.cursor()
    
    # Check if column exists
    cursor.execute("PRAGMA table_info(economic_events)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "consensus_std_dev" in columns:
        print("[OK] Column 'consensus_std_dev' already exists")
        return False
    
    # Add column
    cursor.execute("ALTER TABLE economic_events ADD COLUMN consensus_std_dev REAL")
    conn.commit()
    print("[OK] Added 'consensus_std_dev' column to economic_events table")
    return True


def excel_serial_to_date(serial: float) -> str:
    """Convert Excel serial date to YYYY-MM-DD string."""
    # Excel serial date: days since 1899-12-30 (with Excel's leap year bug)
    from datetime import datetime, timedelta
    excel_epoch = datetime(1899, 12, 30)
    return (excel_epoch + timedelta(days=int(serial))).strftime('%Y-%m-%d')


def load_excel_data(config: Dict) -> List[Dict]:
    """
    Load event data from an Excel file based on configuration.
    
    Returns:
        List of dicts with: date, consensus, consensus_std_dev, event_name, actual
    """
    excel_path = config["path"]
    engine = config.get("engine")
    sheet_name = config.get("sheet_name", 0)
    
    if not excel_path.exists():
        print(f"  [WARN] Excel file not found: {excel_path}")
        return []
    
    try:
        if engine:
            df = pd.read_excel(excel_path, header=None, engine=engine, sheet_name=sheet_name)
        else:
            df = pd.read_excel(excel_path, header=None, sheet_name=sheet_name)
        if df.empty:
            print(f"  [WARN] Excel file is empty: {excel_path}")
            return []
    except Exception as e:
        print(f"  [WARN] Error reading Excel: {e}")
        return []
    
    all_data = []
    
    for event_config in config["events"]:
        event_name = event_config["db_event_name"]
        header_rows = event_config["header_rows"]
        date_col = event_config["date_col"]
        consensus_col = event_config["consensus_col"]
        std_col = event_config.get("std_col")
        actual_col = event_config.get("actual_col")
        multiplier = event_config.get("consensus_multiplier", 1)
        date_is_serial = event_config.get("date_is_serial", False)
        
        # Skip if columns don't exist
        max_col = max(c for c in [date_col, consensus_col, std_col, actual_col] if c is not None)
        if max_col >= len(df.columns):
            print(f"  [WARN] Not enough columns for {event_name}")
            continue
        
        # Process data rows (skip headers)
        data_rows = df.iloc[header_rows:].copy()
        
        event_data = []
        for idx, row in data_rows.iterrows():
            try:
                # Get date
                date_val = row.iloc[date_col]
                if pd.isna(date_val):
                    continue
                
                # Parse date
                if date_is_serial:
                    try:
                        date_str = excel_serial_to_date(float(date_val))
                    except:
                        continue
                elif isinstance(date_val, (datetime, pd.Timestamp)):
                    date_str = date_val.strftime('%Y-%m-%d')
                else:
                    date_str = str(date_val)[:10]
                
                # Get consensus
                consensus_val = row.iloc[consensus_col]
                if pd.isna(consensus_val):
                    consensus = None
                else:
                    consensus = float(consensus_val) * multiplier
                
                # Get actual (if column specified)
                actual = None
                if actual_col is not None:
                    actual_val = row.iloc[actual_col]
                    if not pd.isna(actual_val):
                        actual = float(actual_val) * multiplier
                
                # Get std dev (if column exists)
                std_dev = None
                if std_col is not None:
                    std_val = row.iloc[std_col]
                    if not pd.isna(std_val):
                        std_dev = float(std_val)
                
                event_data.append({
                    "date": date_str,
                    "event_name": event_name,
                    "consensus": consensus,
                    "actual": actual,
                    "consensus_std_dev": std_dev,
                })
            except Exception as e:
                continue
        
        print(f"    Loaded {len(event_data)} {event_name} records")
        all_data.extend(event_data)
    
    return all_data


def find_matching_db_event(cursor: sqlite3.Cursor, event_name: str, date_str: str) -> Optional[Dict]:
    """
    Find a matching event in the database within ±3 days of the given date.
    """
    # Try exact date match first
    cursor.execute("""
        SELECT id, event_date, consensus, consensus_std_dev
        FROM economic_events
        WHERE event_name = ? AND event_date LIKE ?
        LIMIT 1
    """, (event_name, f"{date_str}%"))
    
    row = cursor.fetchone()
    if row:
        return {"id": row[0], "event_date": row[1], "consensus": row[2], "consensus_std_dev": row[3]}
    
    # Try ±3 day proximity search
    try:
        base_date = datetime.strptime(date_str, '%Y-%m-%d')
        for delta in range(-3, 4):
            if delta == 0:
                continue
            check_date = (base_date + timedelta(days=delta)).strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT id, event_date, consensus, consensus_std_dev
                FROM economic_events
                WHERE event_name = ? AND event_date LIKE ?
                LIMIT 1
            """, (event_name, f"{check_date}%"))
            row = cursor.fetchone()
            if row:
                return {"id": row[0], "event_date": row[1], "consensus": row[2], "consensus_std_dev": row[3]}
    except:
        pass
    
    return None


def update_database(conn: sqlite3.Connection, excel_data: List[Dict], dry_run: bool = False) -> Dict:
    """
    Update database with Excel data.
    
    Returns:
        Stats dict with counts of updates
    """
    cursor = conn.cursor()
    stats = {
        "matched": 0,
        "consensus_updated": 0,
        "actual_updated": 0,
        "std_dev_added": 0,
        "not_found": 0,
    }
    
    for record in excel_data:
        event_name = record["event_name"]
        date_str = record["date"]
        excel_consensus = record.get("consensus")
        excel_actual = record.get("actual")
        excel_std_dev = record.get("consensus_std_dev")
        
        # Find matching event in DB
        db_event = find_matching_db_event(cursor, event_name, date_str)
        
        if not db_event:
            stats["not_found"] += 1
            continue
        
        stats["matched"] += 1
        event_id = db_event["id"]
        db_consensus = db_event["consensus"]
        db_std_dev = db_event["consensus_std_dev"]
        
        updates = []
        params = []
        
        # Check if consensus differs and should be updated
        if excel_consensus is not None:
            if db_consensus is None or abs(excel_consensus - db_consensus) > 0.001:
                updates.append("consensus = ?")
                params.append(excel_consensus)
                stats["consensus_updated"] += 1
                if not dry_run:
                    print(f"    {event_name} {date_str}: consensus {db_consensus} -> {excel_consensus}")
        
        # Check if actual differs and should be updated
        if excel_actual is not None:
            # Get current actual from DB
            cursor.execute("SELECT actual FROM economic_events WHERE id = ?", (event_id,))
            db_actual = cursor.fetchone()[0]
            if db_actual is None or abs(excel_actual - db_actual) > 0.001:
                updates.append("actual = ?")
                params.append(excel_actual)
                stats["actual_updated"] += 1
                if not dry_run:
                    print(f"    {event_name} {date_str}: actual {db_actual} -> {excel_actual}")
        
        # Add std dev if we have it
        if excel_std_dev is not None:
            if db_std_dev is None or abs(excel_std_dev - db_std_dev) > 0.0001:
                updates.append("consensus_std_dev = ?")
                params.append(excel_std_dev)
                stats["std_dev_added"] += 1
        
        # Execute update if we have changes
        if updates and not dry_run:
            sql = f"UPDATE economic_events SET {', '.join(updates)} WHERE id = ?"
            params.append(event_id)
            cursor.execute(sql, params)
    
    if not dry_run:
        conn.commit()
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Update economic events DB from Excel files")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying DB")
    args = parser.parse_args()
    
    print("=" * 60)
    print("Update Economic Events DB from Excel Files")
    print("=" * 60)
    
    if args.dry_run:
        print("[DRY RUN] No changes will be made\n")
    
    # Check DB exists
    if not ECONOMIC_EVENTS_DB.exists():
        print(f"[ERROR] Database not found: {ECONOMIC_EVENTS_DB}")
        return
    
    # Connect to database
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    
    # Add consensus_std_dev column if needed
    print("\n1. Checking database schema...")
    add_consensus_std_dev_column(conn)
    
    # Process each Excel file
    print("\n2. Loading data from Excel files...")
    all_excel_data = []
    
    for config in EXCEL_CONFIGS:
        excel_path = config["path"]
        print(f"\n  Processing: {excel_path.name}")
        data = load_excel_data(config)
        all_excel_data.extend(data)
    
    print(f"\n  Total records loaded: {len(all_excel_data)}")
    
    # Update database
    print("\n3. Updating database...")
    stats = update_database(conn, all_excel_data, dry_run=args.dry_run)
    
    # Print summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Events matched in DB:      {stats['matched']}")
    print(f"  Consensus values updated:  {stats['consensus_updated']}")
    print(f"  Actual values updated:     {stats['actual_updated']}")
    print(f"  Std dev values added:      {stats['std_dev_added']}")
    print(f"  Events not found in DB:    {stats['not_found']}")
    
    if args.dry_run:
        print("\n[DRY RUN] This was a dry run. Run without --dry-run to apply changes.")
    else:
        print("\n[SUCCESS] Database updated successfully!")
    
    conn.close()


if __name__ == "__main__":
    main()

