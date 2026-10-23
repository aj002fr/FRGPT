#!/usr/bin/env python3
"""
NFP Data Loader
---------------
Centralized data loading for NFP analysis dashboards.

Data Sources:
- NFP_STD, UER_STD: Z:/Meini/NFP.xlsx (Bloomberg analyst forecast dispersion)
- NFP actual, consensus, dates: economic_events.db
- Price data: OHLCV market data via run_query
"""

import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
BLOOMBERG_EXCEL_PATH = Path("Z:/Meini/NFP.xlsx")
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"


def load_nfp_std_from_excel() -> Dict[str, float]:
    """
    Load NFP consensus standard deviation from Bloomberg Excel file.
    
    Returns:
        Dict mapping date string (YYYY-MM-DD) to NFP_STD value
    """
    if not BLOOMBERG_EXCEL_PATH.exists():
        print(f"Warning: Bloomberg Excel not found at {BLOOMBERG_EXCEL_PATH}")
        return {}
    
    # Read Excel with custom parsing (skip header rows)
    df = pd.read_excel(BLOOMBERG_EXCEL_PATH, header=None, skiprows=3)
    df.columns = ['NFP_Date', 'NFP_Cons', 'UER_Date', 'UER_Cons', 
                  'NFP_Std_Date', 'NFP_Std', 'NFP_Release', 
                  'UER_Std_Date', 'UER_Std', 'UER_Release']
    
    # Get rows with valid NFP_STD
    nfp_data = df[['NFP_Release', 'NFP_Std']].dropna(subset=['NFP_Std'])
    
    # Create date -> std mapping
    std_by_date = {}
    for _, row in nfp_data.iterrows():
        if pd.notna(row['NFP_Release']):
            if isinstance(row['NFP_Release'], datetime):
                date_str = row['NFP_Release'].strftime('%Y-%m-%d')
            else:
                date_str = str(row['NFP_Release'])[:10]
            std_by_date[date_str] = float(row['NFP_Std'])
    
    print(f"Loaded {len(std_by_date)} NFP std dev values from Bloomberg Excel")
    return std_by_date


def load_fwd_from_excel() -> Dict[str, float]:
    """
    Load FWD breakeven from Column P (16) of Bloomberg Excel file.
    
    Returns:
        Dict mapping date string (YYYY-MM-DD) to FWD value in bps
    """
    if not BLOOMBERG_EXCEL_PATH.exists():
        print(f"Warning: Bloomberg Excel not found at {BLOOMBERG_EXCEL_PATH}")
        return {}
    
    try:
        from openpyxl import load_workbook
        wb = load_workbook(BLOOMBERG_EXCEL_PATH, data_only=True)
        ws = wb.active
        
        fwd_by_date = {}
        for row in range(4, ws.max_row + 1):  # Skip header rows (start at row 4)
            date_val = ws.cell(row=row, column=1).value  # Column A = NFP_Date
            fwd_val = ws.cell(row=row, column=16).value   # Column P = FWD
            
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
        print(f"Loaded {len(fwd_by_date)} FWD breakeven values from Bloomberg Excel Column P")
        return fwd_by_date
        
    except ImportError:
        print("Warning: openpyxl not installed, falling back to pandas")
        # Fallback to pandas if openpyxl not available
        df = pd.read_excel(BLOOMBERG_EXCEL_PATH, header=None, skiprows=3)
        if len(df.columns) >= 16:
            fwd_by_date = {}
            for _, row in df.iterrows():
                date_val = row.iloc[0]
                fwd_val = row.iloc[15]  # Column P (0-indexed = 15)
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
            print(f"Loaded {len(fwd_by_date)} FWD values via pandas fallback")
            return fwd_by_date
        return {}
    except Exception as e:
        print(f"Error loading FWD from Excel: {e}")
        return {}


def load_uer_std_from_excel() -> Dict[str, float]:
    """
    Load Unemployment Rate consensus standard deviation from Bloomberg Excel.
    
    Returns:
        Dict mapping date string (YYYY-MM-DD) to UER_STD value
    """
    if not BLOOMBERG_EXCEL_PATH.exists():
        return {}
    
    df = pd.read_excel(BLOOMBERG_EXCEL_PATH, header=None, skiprows=3)
    df.columns = ['NFP_Date', 'NFP_Cons', 'UER_Date', 'UER_Cons', 
                  'NFP_Std_Date', 'NFP_Std', 'NFP_Release', 
                  'UER_Std_Date', 'UER_Std', 'UER_Release']
    
    # Get rows with valid UER_STD
    uer_data = df[['UER_Release', 'UER_Std']].dropna(subset=['UER_Std'])
    
    std_by_date = {}
    for _, row in uer_data.iterrows():
        if pd.notna(row['UER_Release']):
            if isinstance(row['UER_Release'], datetime):
                date_str = row['UER_Release'].strftime('%Y-%m-%d')
            else:
                date_str = str(row['UER_Release'])[:10]
            std_by_date[date_str] = float(row['UER_Std'])
    
    print(f"Loaded {len(std_by_date)} UER std dev values from Bloomberg Excel")
    return std_by_date


def load_nfp_from_economic_events() -> List[Dict]:
    """
    Load NFP actual, consensus, and std dev values from bbg_data.
    
    Returns:
        List of dicts with: date, actual, consensus, consensus_std_dev
    """
    if not ECONOMIC_EVENTS_DB.exists():
        print(f"Warning: Economic events DB not found at {ECONOMIC_EVENTS_DB}")
        return []
    
    conn = sqlite3.connect(ECONOMIC_EVENTS_DB)
    cursor = conn.cursor()
    
    # Query headline NFP data from bbg_data
    cursor.execute('''
        SELECT timestamp, event_name, db_event_name, actual, consensus, consensus_std_dev
        FROM bbg_data
        WHERE (db_event_name = 'Non Farm Payrolls' OR event_name = 'NFP')
        AND actual IS NOT NULL
        ORDER BY timestamp ASC
    ''')
    
    events = []
    for row in cursor.fetchall():
        event_date_str = row[0]
        try:
            event_dt = datetime.fromisoformat(event_date_str.replace('Z', ''))
            date_str = event_dt.strftime('%Y-%m-%d')
        except:
            date_str = event_date_str[:10]
        
        events.append({
            'date': date_str,
            'nfp_actual': row[3],
            'nfp_cons': row[4],
            'nfp_cons_std_db': row[5],
        })
    
    conn.close()
    print(f"Loaded {len(events)} NFP events from bbg_data")
    return events


def load_uer_from_economic_events() -> Dict[str, Dict]:
    """
    Load Unemployment Rate actual, consensus, and std dev values from bbg_data.
    
    Returns:
        Dict mapping date string to {uer_actual, uer_cons, uer_cons_std_db}
    """
    if not ECONOMIC_EVENTS_DB.exists():
        return {}
    
    conn = sqlite3.connect(ECONOMIC_EVENTS_DB)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT timestamp, event_name, db_event_name, actual, consensus, consensus_std_dev
        FROM bbg_data
        WHERE (db_event_name = 'Unemployment Rate' OR event_name = 'Unemployment Rate')
        AND actual IS NOT NULL
        ORDER BY timestamp ASC
    ''')
    
    uer_by_date = {}
    for row in cursor.fetchall():
        event_date_str = row[0]
        try:
            event_dt = datetime.fromisoformat(event_date_str.replace('Z', ''))
            date_str = event_dt.strftime('%Y-%m-%d')
        except:
            date_str = event_date_str[:10]
        
        uer_by_date[date_str] = {
            'uer_actual': row[3] if row[3] else None,
            'uer_cons': row[4] if row[4] else None,
            'uer_cons_std_db': row[5] if row[5] else None,
        }
    
    conn.close()
    print(f"Loaded {len(uer_by_date)} UER events from bbg_data")
    return uer_by_date


def get_nfp_data_combined() -> List[Dict]:
    """
    Combine NFP and UER data from all sources.
    
    Returns:
        List of dicts with: date, nfp_actual, nfp_cons, cons_std, surprise, standardized_surprise,
                           uer_actual, uer_cons, uer_std, uer_surprise, uer_std_surprise, fwd_bps
    """
    # Load from economic events DB
    nfp_events = load_nfp_from_economic_events()
    
    # Load FWD breakeven from Bloomberg Excel Column P
    fwd_by_date = load_fwd_from_excel()
    
    # Load UER actual/consensus
    uer_by_date = load_uer_from_economic_events()
    
    # Merge data
    combined = []
    for event in nfp_events:
        date = event['date']
        
        # NFP std dev (bbg_data only)
        cons_std = event.get('nfp_cons_std_db')
        if cons_std is None:
            print(f"  Warning: No NFP std dev found for {date}, skipping event")
            continue
        
        # UER std dev (bbg_data only)
        uer_std = uer_by_date.get(date, {}).get('uer_cons_std_db')
        if uer_std is None:
            print(f"  Warning: No UER std dev found for {date}, keeping NFP event without UER z-score")
        
        # UER actual/consensus
        uer_data = uer_by_date.get(date, {})
        uer_actual = uer_data.get('uer_actual')
        uer_cons = uer_data.get('uer_cons')
        
        # Calculate NFP surprise metrics
        nfp_actual = event['nfp_actual']
        nfp_cons = event['nfp_cons']
        nfp_surprise = nfp_actual - nfp_cons if (nfp_actual is not None and nfp_cons is not None) else None
        nfp_std_surprise = nfp_surprise / cons_std if (nfp_surprise is not None and cons_std > 0) else None
        
        # Calculate UER surprise metrics
        uer_surprise = uer_actual - uer_cons if (uer_actual is not None and uer_cons is not None) else None
        uer_std_surprise = uer_surprise / uer_std if (
            uer_surprise is not None and uer_std is not None and uer_std > 0
        ) else None
        
        # Get FWD breakeven (with date proximity search fallback)
        fwd_bps = fwd_by_date.get(date)
        if fwd_bps is None:
            event_dt = datetime.strptime(date, '%Y-%m-%d')
            for delta in range(-3, 4):
                check_date = (event_dt + timedelta(days=delta)).strftime('%Y-%m-%d')
                if check_date in fwd_by_date:
                    fwd_bps = fwd_by_date[check_date]
                    break
        if fwd_bps is None:
            fwd_bps = 15.0  # Default fallback
        
        combined.append({
            'date': date,
            # NFP fields
            'nfp_actual': nfp_actual,
            'nfp_cons': nfp_cons,
            'cons_std': cons_std,
            'surprise': nfp_surprise,
            'standardized_surprise': nfp_std_surprise,
            # UER fields
            'uer_actual': uer_actual,
            'uer_cons': uer_cons,
            'uer_std': uer_std,
            'uer_surprise': uer_surprise,
            'uer_std_surprise': uer_std_surprise,
            # FWD breakeven
            'fwd_bps': fwd_bps,
        })
    
    print(f"Combined {len(combined)} NFP events with std dev data")
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


# Test if run directly
if __name__ == "__main__":
    print("=" * 60)
    print("Testing NFP Data Loader")
    print("=" * 60)
    
    # Test Bloomberg Excel
    print("\n1. Testing Bloomberg Excel loader...")
    nfp_std = load_nfp_std_from_excel()
    print(f"   Sample dates: {list(nfp_std.keys())[:5]}")
    print(f"   Sample values: {list(nfp_std.values())[:5]}")
    
    # Test economic events
    print("\n2. Testing economic events loader...")
    nfp_events = load_nfp_from_economic_events()
    if nfp_events:
        print(f"   First event: {nfp_events[0]}")
        print(f"   Last event: {nfp_events[-1]}")
    
    # Test combined
    print("\n3. Testing combined loader...")
    combined = get_nfp_data_combined()
    if combined:
        print(f"   First: {combined[0]}")
        print(f"   Last: {combined[-1]}")

