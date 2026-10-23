#!/usr/bin/env python3
"""
Event Data Loader
-----------------
Centralized data loading for pre-event analysis dashboards.
Supports NFP and CPI events.

Data Sources:
- NFP: Z:/Meini/NFP.xlsx (consensus std dev in col F, FWD B/E in col P)
- CPI: Z:/Meini/CPI.xlsx (Core CPI std dev in col D, FWD B/E in col K)
- Actual/Consensus: economic_events.db
- Price data: OHLCV market data via run_query
"""

import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"

# Excel file paths for each event type
EXCEL_PATHS = {
    "NFP": Path("Z:/Meini/NFP.xlsx"),
    "CPI": Path("Z:/Meini/CPI.xlsx"),
}

# Event configuration
# Maps event type to DB event name and Excel column structure
EVENT_CONFIG = {
    "NFP": {
        "db_event_name": "Non Farm Payrolls",
        "display_name": "Non-Farm Payrolls (NFP)",
        "short_name": "NFP",
        "release_time": "08:30",  # 8:30am ET
        "unit": "K",  # Thousands
        "db_divisor": 1000,  # DB stores raw numbers, divide to get K
        # Excel column indices (0-based) - from pandas read
        "excel_date_col": 0,       # Column A: Date
        "excel_std_col": 5,        # Column F: NFP_Std (0-indexed)
        "excel_fwd_col": None,     # NFP doesn't have FWD in sheet - passed at runtime
        "excel_skip_rows": 3,
        "default_std": 25.0,
        "default_fwd": 6.5,        # Default FWD for NFP (used at runtime)
        "require_fwd_in_sheet": False,  # NFP uses runtime FWD, not historical
    },
    "CPI": {
        "db_event_name": "Core Inflation Rate MoM",
        "display_name": "Core CPI MoM",
        "short_name": "CPI",
        "release_time": "08:30",  # 8:30am ET
        "unit": "%",  # Percentage
        "db_divisor": 1,  # DB stores as percentage already
        # Excel column indices (0-based) - from CPI.xlsx structure
        # Col A (0): Date, Col B (1): Core CPI Cons, Col D (3): Core CPI Std, Col K (10): Fwd B/E
        "excel_date_col": 0,       # Column A: Date
        "excel_std_col": 3,        # Column D: Core CPI Std (0-indexed)
        "excel_fwd_col": 10,       # Column K: FWD B/E (0-indexed)
        "excel_skip_rows": 3,
        "default_std": 0.05,       # 0.05% default for CPI
        "default_fwd": 10.0,
        "require_fwd_in_sheet": True,  # CPI requires FWD from sheet
    },
    "PPI": {
        "db_event_name": "Core PPI MoM",
        "display_name": "Core PPI MoM",
        "short_name": "PPI",
        "release_time": "08:30",  # 8:30am ET
        "unit": "%",
        "db_divisor": 1,
        "excel_date_col": 0,
        "excel_std_col": None,
        "excel_fwd_col": None,
        "excel_skip_rows": 0,
        "default_std": 0.1,
        "default_fwd": 8.0,
        "require_fwd_in_sheet": False,
    },
    "JOLTS": {
        "db_event_name": "JOLTs Job Openings",
        "display_name": "JOLTS Job Openings",
        "short_name": "JOLTS",
        "release_time": "10:00",  # 10:00am ET
        "unit": "M",
        "db_divisor": 1,
        "excel_date_col": 0,
        "excel_std_col": None,
        "excel_fwd_col": None,
        "excel_skip_rows": 0,
        "default_std": 0.35,
        "default_fwd": 8.0,
        "require_fwd_in_sheet": False,
    },
}


def load_std_from_excel(event_type: str) -> Dict[str, float]:
    """
    Load consensus standard deviation from Bloomberg Excel file for specified event.
    
    Args:
        event_type: "NFP" or "CPI"
    
    Returns:
        Dict mapping date string (YYYY-MM-DD) to std dev value
    """
    config = EVENT_CONFIG.get(event_type)
    if not config:
        print(f"Warning: Unknown event type {event_type}")
        return {}
    
    excel_path = EXCEL_PATHS.get(event_type)
    if not excel_path or not excel_path.exists():
        print(f"Warning: Excel file not found at {excel_path}")
        return {}
    
    try:
        df = pd.read_excel(excel_path, header=None, skiprows=config["excel_skip_rows"])
        
        date_col = config["excel_date_col"]
        std_col = config["excel_std_col"]
        
        std_by_date = {}
        for _, row in df.iterrows():
            date_val = row.iloc[date_col] if len(row) > date_col else None
            std_val = row.iloc[std_col] if len(row) > std_col else None
            
            if pd.notna(date_val) and pd.notna(std_val):
                try:
                    std_float = float(std_val)
                    if std_float <= 0:
                        continue
                    
                    if isinstance(date_val, datetime):
                        date_str = date_val.strftime('%Y-%m-%d')
                    else:
                        date_str = str(date_val)[:10]
                    
                    std_by_date[date_str] = std_float
                except (ValueError, TypeError):
                    continue
        
        print(f"Loaded {len(std_by_date)} {event_type} std dev values from {excel_path.name}")
        return std_by_date
        
    except Exception as e:
        print(f"Error loading std from {excel_path}: {e}")
        return {}


def load_fwd_from_excel(event_type: str) -> Dict[str, float]:
    """
    Load FWD breakeven from Bloomberg Excel file for specified event.
    
    Args:
        event_type: "NFP" or "CPI"
    
    Returns:
        Dict mapping date string (YYYY-MM-DD) to FWD value in bps
    """
    config = EVENT_CONFIG.get(event_type)
    if not config:
        print(f"Warning: Unknown event type {event_type}")
        return {}
    
    # Some events (like NFP) don't have FWD in the sheet
    if config.get("excel_fwd_col") is None:
        print(f"{event_type} FWD B/E is provided at runtime (not in sheet)")
        return {}
    
    excel_path = EXCEL_PATHS.get(event_type)
    if not excel_path or not excel_path.exists():
        print(f"Warning: Excel file not found at {excel_path}")
        return {}
    
    try:
        from openpyxl import load_workbook
        wb = load_workbook(excel_path, data_only=True)
        ws = wb.active
        
        date_col_idx = config["excel_date_col"] + 1  # openpyxl is 1-indexed
        fwd_col_idx = config["excel_fwd_col"] + 1    # openpyxl is 1-indexed
        skip_rows = config["excel_skip_rows"]
        
        fwd_by_date = {}
        for row in range(skip_rows + 1, ws.max_row + 1):
            date_val = ws.cell(row=row, column=date_col_idx).value
            fwd_val = ws.cell(row=row, column=fwd_col_idx).value
            
            if date_val is not None and fwd_val is not None:
                try:
                    fwd_float = float(fwd_val)
                    if fwd_float <= 0:
                        continue
                    
                    if isinstance(date_val, datetime):
                        date_str = date_val.strftime('%Y-%m-%d')
                    else:
                        date_str = str(date_val)[:10]
                    
                    fwd_by_date[date_str] = fwd_float
                except (ValueError, TypeError):
                    continue
        
        wb.close()
        print(f"Loaded {len(fwd_by_date)} {event_type} FWD breakeven values from {excel_path.name}")
        return fwd_by_date
        
    except ImportError:
        print("Warning: openpyxl not installed, falling back to pandas")
        # Fallback to pandas
        df = pd.read_excel(excel_path, header=None, skiprows=config["excel_skip_rows"])
        
        date_col = config["excel_date_col"]
        fwd_col = config["excel_fwd_col"]
        
        if len(df.columns) > fwd_col:
            fwd_by_date = {}
            for _, row in df.iterrows():
                date_val = row.iloc[date_col] if len(row) > date_col else None
                fwd_val = row.iloc[fwd_col] if len(row) > fwd_col else None
                
                if pd.notna(date_val) and pd.notna(fwd_val):
                    try:
                        fwd_float = float(fwd_val)
                        if fwd_float <= 0:
                            continue
                        if isinstance(date_val, datetime):
                            date_str = date_val.strftime('%Y-%m-%d')
                        else:
                            date_str = str(date_val)[:10]
                        fwd_by_date[date_str] = fwd_float
                    except (ValueError, TypeError):
                        continue
            print(f"Loaded {len(fwd_by_date)} {event_type} FWD values via pandas fallback")
            return fwd_by_date
        return {}
    except Exception as e:
        print(f"Error loading FWD from {excel_path}: {e}")
        return {}


def load_events_from_db(event_type: str) -> List[Dict]:
    """
    Load actual, consensus, and consensus std dev from bbg_data (economic_events.db).
    
    Args:
        event_type: "NFP" or "CPI"
    
    Returns:
        List of dicts with: date, actual, consensus, cons_std_db
    """
    config = EVENT_CONFIG.get(event_type)
    if not config:
        print(f"Warning: Unknown event type {event_type}")
        return []
    
    if not ECONOMIC_EVENTS_DB.exists():
        print(f"Warning: Economic events DB not found at {ECONOMIC_EVENTS_DB}")
        return []
    
    conn = sqlite3.connect(ECONOMIC_EVENTS_DB)
    cursor = conn.cursor()
    
    db_event_name = config["db_event_name"]
    divisor = config["db_divisor"]
    
    cursor.execute('''
        SELECT timestamp, event_name, db_event_name, actual, consensus, consensus_std_dev
        FROM bbg_data
        WHERE (db_event_name = ? OR event_name = ?)
        AND actual IS NOT NULL
        ORDER BY timestamp ASC
    ''', (db_event_name, config.get("short_name", db_event_name)))
    
    events = []
    for row in cursor.fetchall():
        timestamp_str = row[0]
        try:
            event_dt = datetime.fromisoformat(timestamp_str.replace('Z', ''))
            date_str = event_dt.strftime('%Y-%m-%d')
        except Exception:
            date_str = timestamp_str[:10]
        
        events.append({
            'date': date_str,
            'actual': row[3],
            'consensus': row[4],
            'cons_std_db': row[5],
        })
    
    conn.close()
    print(f"Loaded {len(events)} {event_type} events from bbg_data")
    return events


def _ensure_comments_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_comments (
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            comment TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (event_type, event_date)
        )
        """
    )


def get_event_comments(event_type: str) -> List[Dict]:
    """Fetch saved comments for an event type."""
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    conn = sqlite3.connect(f"file:{ECONOMIC_EVENTS_DB}?mode=ro", uri=True)
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_comments'"
        ).fetchone()
        if not table_exists:
            return []
        cursor = conn.execute(
            """
            SELECT event_date, comment, updated_at
            FROM event_comments
            WHERE event_type = ?
            ORDER BY event_date DESC
            """,
            (event_type,),
        )
        rows = cursor.fetchall()
        return [
            {"event_date": row[0], "comment": row[1] or "", "updated_at": row[2]}
            for row in rows
        ]
    finally:
        conn.close()


def upsert_event_comment(event_type: str, event_date: str, comment: str) -> bool:
    """Insert or update a comment for an event date."""
    if not ECONOMIC_EVENTS_DB.exists():
        return False
    conn = sqlite3.connect(ECONOMIC_EVENTS_DB)
    try:
        _ensure_comments_table(conn)
        conn.execute(
            """
            INSERT INTO event_comments (event_type, event_date, comment, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_type, event_date) DO UPDATE SET
                comment = excluded.comment,
                updated_at = excluded.updated_at
            """,
            (event_type, event_date, comment, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_event_comment(event_type: str, event_date: str) -> bool:
    """Delete a comment for an event date."""
    if not ECONOMIC_EVENTS_DB.exists():
        return False
    conn = sqlite3.connect(ECONOMIC_EVENTS_DB)
    try:
        _ensure_comments_table(conn)
        conn.execute(
            "DELETE FROM event_comments WHERE event_type = ? AND event_date = ?",
            (event_type, event_date),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_event_data_combined(event_type: str) -> List[Dict]:
    """
    Combine event data from all sources (DB + Excel).
    
    Args:
        event_type: "NFP" or "CPI"
    
    Returns:
        List of dicts with: date, actual, consensus, cons_std, surprise, 
                           standardized_surprise, fwd_bps
    """
    config = EVENT_CONFIG.get(event_type)
    if not config:
        print(f"Warning: Unknown event type {event_type}")
        return []
    
    # Load from bbg_data (actual/consensus/std dev)
    events = load_events_from_db(event_type)
    
    default_fwd = config.get("default_fwd")
    
    # Merge data - only include events with ACTUAL data (no defaults)
    combined = []
    excluded_no_fwd = []
    excluded_no_std = []
    
    for event in events:
        date = event['date']
        
        # FWD breakeven (no external fallback; use default if configured)
        fwd_bps = default_fwd
        
        
        # Use std dev from bbg_data only
        cons_std = event.get('cons_std_db')
        if cons_std is None:
            excluded_no_std.append(date)
            continue
        
        # Calculate surprise metrics
        actual = event['actual']
        consensus = event['consensus']
        surprise = actual - consensus if (actual is not None and consensus is not None) else None
        std_surprise = surprise / cons_std if (surprise is not None and cons_std > 0) else None
        
        combined.append({
            'date': date,
            'actual': actual,
            'consensus': consensus,
            'cons_std': cons_std,
            'surprise': surprise,
            'standardized_surprise': std_surprise,
            'fwd_bps': fwd_bps,
        })
    
    print(f"Combined {len(combined)} {event_type} events with actual data from sheets")
    
    # Report exclusions
    if excluded_no_std:
        print(f"  Excluded {len(excluded_no_std)} events without Cons Std Dev in bbg_data")
        if len(excluded_no_std) <= 5:
            for d in excluded_no_std:
                print(f"    - {d}")
        else:
            for d in excluded_no_std[:3]:
                print(f"    - {d}")
            print(f"    ... and {len(excluded_no_std) - 3} more")
    
    return combined


def calculate_surprise_percentile(std_surprise: float, all_std_surprises: List[float]) -> float:
    """
    Calculate percentile rank of a standardized surprise value.
    
    Returns value between 0 and 1.
    """
    if not all_std_surprises or std_surprise is None:
        return 0.5
    
    count_below = sum(1 for s in all_std_surprises if s is not None and s <= std_surprise)
    return count_below / len(all_std_surprises)


def get_event_config(event_type: str) -> Optional[Dict]:
    """Get configuration for an event type."""
    return EVENT_CONFIG.get(event_type)


def get_supported_events() -> List[str]:
    """Get list of supported event types."""
    return list(EVENT_CONFIG.keys())


# Backwards compatibility - re-export NFP-specific functions
def get_nfp_data_combined() -> List[Dict]:
    """Backwards compatible: Get combined NFP data."""
    return get_event_data_combined("NFP")


def load_nfp_std_from_excel() -> Dict[str, float]:
    """Backwards compatible: Load NFP std from Excel."""
    return load_std_from_excel("NFP")


def load_nfp_fwd_from_excel() -> Dict[str, float]:
    """Backwards compatible: Load NFP FWD from Excel."""
    return load_fwd_from_excel("NFP")


# Test if run directly
if __name__ == "__main__":
    print("=" * 60)
    print("Testing Event Data Loader")
    print("=" * 60)
    
    for event_type in ["NFP", "CPI"]:
        print(f"\n{'='*40}")
        print(f"Testing {event_type}")
        print(f"{'='*40}")
        
        # Test std loader
        print(f"\n1. Testing {event_type} std dev loader...")
        std_data = load_std_from_excel(event_type)
        if std_data:
            dates = list(std_data.keys())
            print(f"   Sample dates: {dates[:3]}")
            print(f"   Sample values: {[std_data[d] for d in dates[:3]]}")
        
        # Test FWD loader
        print(f"\n2. Testing {event_type} FWD loader...")
        fwd_data = load_fwd_from_excel(event_type)
        if fwd_data:
            dates = list(fwd_data.keys())
            print(f"   Sample dates: {dates[:3]}")
            print(f"   Sample values: {[fwd_data[d] for d in dates[:3]]}")
        
        # Test DB loader
        print(f"\n3. Testing {event_type} DB loader...")
        events = load_events_from_db(event_type)
        if events:
            print(f"   First: {events[0]}")
            print(f"   Last: {events[-1]}")
        
        # Test combined
        print(f"\n4. Testing {event_type} combined loader...")
        combined = get_event_data_combined(event_type)
        if combined:
            print(f"   First: {combined[0]}")
            print(f"   Last: {combined[-1]}")

