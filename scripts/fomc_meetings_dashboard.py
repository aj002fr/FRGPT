#!/usr/bin/env python
"""
FOMC Meetings Analysis Dashboard
---------------------------------
Generates an interactive HTML dashboard showing TY price paths around
FOMC press conferences with:
- Filter pills: Month-end (last 7 days of month), Hawkish, Dovish
- Normalized price paths in bps (T-60m to T+180m)
- Click-modal with candlestick chart + event annotations + web summary
- Distribution plots for return windows

Usage:
    python scripts/fomc_meetings_dashboard.py
    python scripts/fomc_meetings_dashboard.py --force-refresh  # Re-fetch web sentiments
    python scripts/fomc_meetings_dashboard.py --output custom.html
"""

import argparse
import json
import logging
import sqlite3
import sys
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
from rules.treasury_price_conversion import (
    points_to_bps,
    decimal_to_bond_notation,
    BPS_PER_POINT,
)
from src.core.fomc_sentiment_cache import (
    batch_get_or_fetch_sentiments,
    get_all_cached_sentiments,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "scenarios"
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
ET = ZoneInfo("America/New_York")

# Price filter to exclude bad ticks
TY_PRICE_MIN = 80
TY_PRICE_MAX = 150

# Default time windows (minutes relative to press conference start)
PATH_START_OFFSET = -300  # T-5h (support full range for distribution windows)
PATH_END_OFFSET = 300     # T+5h

# Return windows for distributions
RETURN_WINDOWS = {
    "pre_60m": (-60, 0),
    "during_60m": (0, 60),
    "during_120m": (0, 120),
    "during_180m": (0, 180),
}

# Candlestick window (days around event)
CANDLE_DAYS_BEFORE = 2
CANDLE_DAYS_AFTER = 2


def get_db_connection():
    """Get PostgreSQL connection."""
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD
    )


def fetch_fomc_events() -> List[Dict[str, Any]]:
    """Fetch all Fed Press Conference events from economic_events.db."""
    if not ECONOMIC_EVENTS_DB.exists():
        logger.error(f"Economic events DB not found: {ECONOMIC_EVENTS_DB}")
        return []
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("""
        SELECT event_name, event_date, importance, actual, consensus, previous
        FROM economic_events
        WHERE event_name = 'Fed Press Conference'
          AND country LIKE '%United States%'
          AND event_date < datetime('now')
          AND event_date >= '2022-01-01'
        ORDER BY event_date DESC
    """)
    
    events = []
    for row in cur.fetchall():
        events.append({
            "event_name": row["event_name"],
            "event_date": row["event_date"],
            "importance": row["importance"] or "high",
            "actual": row["actual"],
            "consensus": row["consensus"],
            "previous": row["previous"],
        })
    
    conn.close()
    logger.info(f"Found {len(events)} Fed Press Conference events")
    return events


def parse_event_datetime(event_date_str: str) -> Optional[datetime]:
    """Parse event_date string to datetime in ET."""
    try:
        # Handle both with and without time
        if "T" in event_date_str or " " in event_date_str:
            dt_str = event_date_str.replace("T", " ")
            if len(dt_str) > 19:
                dt_str = dt_str[:19]  # Truncate any timezone suffix
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        else:
            # Date only - assume 2:30 PM ET (typical press conference time)
            dt = datetime.strptime(event_date_str, "%Y-%m-%d")
            dt = dt.replace(hour=14, minute=30)
        
        return dt.replace(tzinfo=ET)
    except Exception as e:
        logger.warning(f"Failed to parse event date {event_date_str}: {e}")
        return None


def is_month_end(dt: datetime) -> bool:
    """Check if date is in the last 7 calendar days of its month."""
    _, last_day = monthrange(dt.year, dt.month)
    return dt.day > last_day - 7


def fetch_price_data_for_window(
    anchor_dt: datetime,
    start_offset_min: int,
    end_offset_min: int,
    conn,
) -> Dict[str, Any]:
    """
    Fetch 1-minute TY price data for a time window around anchor.
    
    Args:
        anchor_dt: Press conference start time (ET)
        start_offset_min: Minutes before anchor (negative) or after (positive)
        end_offset_min: Minutes after anchor
        conn: PostgreSQL connection
    
    Returns:
        Dict with timestamps, opens, highs, lows, closes, volumes
    """
    cur = conn.cursor()
    
    # Convert to UTC for query (Postgres stores in UTC)
    anchor_utc = anchor_dt.astimezone(ZoneInfo("UTC"))
    query_start = anchor_utc + timedelta(minutes=start_offset_min)
    query_end = anchor_utc + timedelta(minutes=end_offset_min)
    
    cur.execute("""
        SELECT ts_event, open, high, low, close, volume
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event < %s
          AND close >= %s AND close <= %s
        ORDER BY ts_event
    """, (query_start, query_end, TY_PRICE_MIN, TY_PRICE_MAX))
    
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": [], "volumes": []}
    
    return {
        "timestamps": [row[0].isoformat() for row in rows],
        "opens": [float(row[1]) for row in rows],
        "highs": [float(row[2]) for row in rows],
        "lows": [float(row[3]) for row in rows],
        "closes": [float(row[4]) for row in rows],
        "volumes": [int(row[5]) if row[5] else 0 for row in rows],
    }


def fetch_candlestick_data(
    anchor_dt: datetime,
    days_before: int,
    days_after: int,
    conn,
) -> Dict[str, Any]:
    """Fetch price data for candlestick modal (±N days)."""
    cur = conn.cursor()
    
    anchor_utc = anchor_dt.astimezone(ZoneInfo("UTC"))
    query_start = anchor_utc - timedelta(days=days_before)
    query_end = anchor_utc + timedelta(days=days_after + 1)
    
    cur.execute("""
        SELECT ts_event, open, high, low, close, volume
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event < %s
          AND close >= %s AND close <= %s
        ORDER BY ts_event
    """, (query_start, query_end, TY_PRICE_MIN, TY_PRICE_MAX))
    
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": [], "volumes": []}
    
    return {
        "timestamps": [row[0].isoformat() for row in rows],
        "opens": [float(row[1]) for row in rows],
        "highs": [float(row[2]) for row in rows],
        "lows": [float(row[3]) for row in rows],
        "closes": [float(row[4]) for row in rows],
        "volumes": [int(row[5]) if row[5] else 0 for row in rows],
    }


def fetch_economic_events_for_date(date_str: str) -> List[Dict[str, Any]]:
    """Fetch economic events from SQLite for a given date."""
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    cur = conn.cursor()
    
    cur.execute("""
        SELECT event_name, event_date, importance, actual, consensus, previous
        FROM economic_events
        WHERE date(event_date) = ?
          AND country LIKE '%United States%'
        ORDER BY event_date
    """, (date_str,))
    
    rows = cur.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        events.append({
            "name": row[0],
            "timestamp": row[1],
            "importance": row[2] or "medium",
            "actual": row[3],
            "consensus": row[4],
            "previous": row[5],
        })
    
    return events


def compute_normalized_path(
    price_data: Dict[str, Any],
    anchor_dt: datetime,
) -> Dict[str, Any]:
    """
    Compute normalized price path in bps relative to T-60m price.
    
    Returns dict with:
    - minutes_from_anchor: list of minute offsets from anchor
    - bps_changes: list of bps changes from reference price
    - ref_price: reference price at T-60m
    """
    closes = price_data.get("closes", [])
    timestamps = price_data.get("timestamps", [])
    
    if not closes or not timestamps:
        return {"minutes_from_anchor": [], "bps_changes": [], "ref_price": None}
    
    # Reference price is first close (should be at T-60m)
    ref_price = closes[0]
    
    # Convert timestamps to minutes from anchor
    anchor_utc = anchor_dt.astimezone(ZoneInfo("UTC"))
    minutes_from_anchor = []
    for ts_str in timestamps:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        delta_minutes = (ts - anchor_utc).total_seconds() / 60
        minutes_from_anchor.append(delta_minutes)
    
    # Compute bps changes using treasury_price_conversion
    bps_changes = [points_to_bps(c - ref_price) for c in closes]
    
    return {
        "minutes_from_anchor": minutes_from_anchor,
        "bps_changes": bps_changes,
        "ref_price": ref_price,
    }


def compute_return_windows(
    price_data: Dict[str, Any],
    anchor_dt: datetime,
) -> Dict[str, Optional[float]]:
    """
    Compute returns (bps) for each defined window.
    
    Returns dict mapping window_name -> return in bps (or None if no data)
    """
    closes = price_data.get("closes", [])
    timestamps = price_data.get("timestamps", [])
    
    if not closes or not timestamps:
        return {name: None for name in RETURN_WINDOWS}
    
    # Build minute -> close mapping
    anchor_utc = anchor_dt.astimezone(ZoneInfo("UTC"))
    minute_to_close = {}
    for i, ts_str in enumerate(timestamps):
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        delta_minutes = int((ts - anchor_utc).total_seconds() / 60)
        minute_to_close[delta_minutes] = closes[i]
    
    results = {}
    for window_name, (start_min, end_min) in RETURN_WINDOWS.items():
        # Find closest available minute to start and end
        start_price = None
        end_price = None
        
        # Search for start price (prefer exact, then within ±2 min)
        for offset in range(3):
            if start_min + offset in minute_to_close:
                start_price = minute_to_close[start_min + offset]
                break
            if start_min - offset in minute_to_close:
                start_price = minute_to_close[start_min - offset]
                break
        
        # Search for end price
        for offset in range(3):
            if end_min - offset in minute_to_close:
                end_price = minute_to_close[end_min - offset]
                break
            if end_min + offset in minute_to_close:
                end_price = minute_to_close[end_min + offset]
                break
        
        if start_price is not None and end_price is not None:
            results[window_name] = round(points_to_bps(end_price - start_price), 2)
        else:
            results[window_name] = None
    
    return results


def compute_stats(values: List[float]) -> Dict[str, float]:
    """Compute distribution statistics."""
    import numpy as np
    
    if not values:
        return {}
    
    arr = np.array(values)
    return {
        "mean": round(float(np.mean(arr)), 2),
        "std": round(float(np.std(arr)), 2),
        "min": round(float(np.min(arr)), 2),
        "max": round(float(np.max(arr)), 2),
        "p5": round(float(np.percentile(arr, 5)), 2),
        "p25": round(float(np.percentile(arr, 25)), 2),
        "p50": round(float(np.percentile(arr, 50)), 2),
        "p75": round(float(np.percentile(arr, 75)), 2),
        "p95": round(float(np.percentile(arr, 95)), 2),
        "n": len(values),
    }


def generate_html(
    events_data: List[Dict[str, Any]],
    return_distributions: Dict[str, List[float]],
    return_stats: Dict[str, Dict[str, float]],
) -> str:
    """Generate the HTML dashboard."""
    
    events_json = json.dumps(events_data, indent=2)
    return_distros_json = json.dumps(return_distributions, indent=2)
    return_stats_json = json.dumps(return_stats, indent=2)
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FOMC Meetings Analysis - TY Price Paths</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap');
        
        :root {{
            --bg-primary: #0a0f1a;
            --bg-secondary: #111827;
            --bg-tertiary: #1f2937;
            --bg-card: #1a1f2e;
            --text-primary: #f9fafb;
            --text-secondary: #9ca3af;
            --text-muted: #6b7280;
            --border-color: #374151;
            --accent-teal: #14b8a6;
            --accent-cyan: #22d3ee;
            --accent-green: #22c55e;
            --accent-orange: #f97316;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --accent-purple: #a855f7;
            --accent-amber: #fbbf24;
        }}
        
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            background-image: 
                radial-gradient(ellipse at top left, rgba(20, 184, 166, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at bottom right, rgba(168, 85, 247, 0.06) 0%, transparent 50%);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
        }}
        
        .container {{ max-width: 1900px; margin: 0 auto; padding: 30px; }}
        
        header {{
            margin-bottom: 35px;
            padding-bottom: 25px;
            border-bottom: 1px solid var(--border-color);
        }}
        
        h1 {{
            font-size: 2.2rem;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .subtitle {{ color: var(--text-secondary); font-size: 1rem; }}
        
        .filters {{
            display: flex;
            gap: 12px;
            margin: 20px 0;
            flex-wrap: wrap;
        }}
        
        .filter-pill {{
            padding: 10px 20px;
            border-radius: 25px;
            border: 1px solid var(--border-color);
            background: var(--bg-secondary);
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s;
            font-size: 0.9rem;
            font-weight: 500;
        }}
        
        .filter-pill:hover {{
            border-color: var(--accent-teal);
            color: var(--text-primary);
        }}
        
        .filter-pill.active {{
            background: var(--accent-teal);
            border-color: var(--accent-teal);
            color: var(--bg-primary);
        }}
        
        .filter-pill.hawkish.active {{
            background: var(--accent-red);
            border-color: var(--accent-red);
        }}
        
        .filter-pill.dovish.active {{
            background: var(--accent-green);
            border-color: var(--accent-green);
        }}
        
        .stats-bar {{
            display: flex;
            gap: 20px;
            margin-top: 20px;
            flex-wrap: wrap;
        }}
        
        .stat-item {{
            background: var(--bg-secondary);
            padding: 14px 22px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
        }}
        
        .stat-label {{ color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 4px; }}
        .stat-value {{ color: var(--accent-teal); font-size: 1.3rem; font-weight: 600; }}
        
        .section {{
            background: var(--bg-secondary);
            border-radius: 14px;
            padding: 25px;
            margin-bottom: 30px;
            border: 1px solid var(--border-color);
        }}
        
        .section-title {{
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 20px;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .section-title::before {{
            content: '';
            display: inline-block;
            width: 4px;
            height: 20px;
            background: linear-gradient(180deg, var(--accent-blue), var(--accent-purple));
            border-radius: 2px;
        }}
        
        #price-chart {{ width: 100%; height: 550px; }}
        
        .events-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }}
        
        .event-card {{
            background: var(--bg-card);
            border-radius: 10px;
            padding: 18px;
            border: 1px solid var(--border-color);
            cursor: pointer;
            transition: all 0.25s ease;
            position: relative;
        }}
        
        .event-card:hover {{
            border-color: var(--accent-teal);
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(20, 184, 166, 0.15);
        }}
        
        .event-card.selected {{
            border-color: var(--accent-cyan);
            background: rgba(20, 184, 166, 0.08);
        }}
        
        .event-card.hidden {{ display: none; }}
        
        .event-date {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--accent-amber);
            margin-bottom: 6px;
        }}
        
        .sentiment-badge {{
            display: inline-block;
            font-size: 0.75rem;
            padding: 3px 10px;
            border-radius: 12px;
            margin-left: 8px;
            font-weight: 600;
        }}
        
        .sentiment-badge.hawkish {{
            background: rgba(239, 68, 68, 0.2);
            color: var(--accent-red);
        }}
        
        .sentiment-badge.dovish {{
            background: rgba(34, 197, 94, 0.2);
            color: var(--accent-green);
        }}
        
        .sentiment-badge.neutral {{
            background: rgba(156, 163, 175, 0.2);
            color: var(--text-secondary);
        }}
        
        .month-end-badge {{
            display: inline-block;
            font-size: 0.7rem;
            padding: 2px 8px;
            border-radius: 10px;
            background: rgba(251, 191, 36, 0.2);
            color: var(--accent-amber);
            margin-left: 6px;
        }}
        
        .event-summary {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-top: 8px;
            line-height: 1.5;
            max-height: 60px;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        
        .distro-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
        }}
        
        @media (max-width: 1200px) {{
            .distro-grid {{ grid-template-columns: 1fr; }}
        }}
        
        .distro-item {{
            display: flex;
            gap: 16px;
            align-items: flex-start;
        }}
        
        .distro-chart {{ height: 320px; flex: 1; }}
        
        .distro-single {{
            display: flex;
            gap: 25px;
            align-items: flex-start;
        }}
        
        .distro-chart-large {{
            flex: 1;
            height: 400px;
        }}
        
        .stats-box {{
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            min-width: 160px;
            font-size: 0.85rem;
        }}
        
        .stats-box h4 {{
            margin: 0 0 12px 0;
            color: #a5b4fc;
            font-size: 0.9rem;
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 8px;
        }}
        
        .stats-row {{
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
        }}
        
        .stats-row .label {{ color: var(--text-secondary); }}
        .stats-row .value {{ font-weight: 500; font-family: 'JetBrains Mono', monospace; }}
        
        /* Modal */
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.85);
            backdrop-filter: blur(4px);
        }}
        
        .modal.active {{ display: flex; align-items: center; justify-content: center; }}
        
        .modal-content {{
            background: var(--bg-secondary);
            border-radius: 16px;
            width: 95%;
            max-width: 1400px;
            max-height: 90vh;
            overflow: hidden;
            border: 1px solid var(--border-color);
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            padding: 20px 25px;
            border-bottom: 1px solid var(--border-color);
            background: var(--bg-tertiary);
        }}
        
        .modal-title {{ font-size: 1.3rem; font-weight: 600; }}
        
        .modal-summary {{
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-top: 12px;
            padding: 12px 15px;
            background: var(--bg-card);
            border-radius: 8px;
            border-left: 3px solid var(--accent-blue);
            line-height: 1.6;
        }}
        
        .modal-citations {{
            margin-top: 8px;
            font-size: 0.8rem;
        }}
        
        .modal-citations a {{
            color: var(--accent-cyan);
            text-decoration: none;
        }}
        
        .modal-citations a:hover {{ text-decoration: underline; }}
        
        .modal-close {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 2rem;
            cursor: pointer;
            padding: 0 10px;
        }}
        
        .modal-close:hover {{ color: var(--accent-red); }}
        
        .modal-expand {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px 12px;
            border-radius: 6px;
            margin-right: 10px;
            transition: all 0.2s;
        }}
        
        .modal-expand:hover {{
            background: var(--accent-teal);
            color: var(--bg-primary);
            border-color: var(--accent-teal);
        }}
        
        .modal-body {{ padding: 20px 25px; overflow-y: auto; max-height: calc(90vh - 200px); }}
        
        #modal-candlestick {{ width: 100%; height: 450px; transition: height 0.3s; }}
        
        .modal-content.expanded {{
            width: 100%;
            max-width: 100%;
            height: 100vh;
            max-height: 100vh;
            border-radius: 0;
        }}
        
        .modal-content.expanded .modal-body {{
            max-height: calc(100vh - 180px);
        }}
        
        .modal-content.expanded #modal-candlestick {{
            height: calc(100vh - 380px);
        }}
        
        .events-list {{
            margin-top: 20px;
            padding: 15px;
            background: var(--bg-card);
            border-radius: 10px;
        }}
        
        .events-list-title {{
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 12px;
            color: var(--accent-cyan);
        }}
        
        .econ-event {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.85rem;
        }}
        
        .econ-event:last-child {{ border-bottom: none; }}
        
        .importance-high {{ border-left: 3px solid var(--accent-red); padding-left: 10px; }}
        .importance-medium {{ border-left: 3px solid var(--accent-orange); padding-left: 10px; }}
        
        .window-controls {{
            background: var(--bg-tertiary);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            border: 1px solid var(--border-color);
        }}
        
        .window-row {{
            display: flex;
            gap: 30px;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }}
        
        .window-row:last-of-type {{ margin-bottom: 0; }}
        
        .window-input-group {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1;
            min-width: 280px;
        }}
        
        .window-input-group label {{
            font-weight: 600;
            color: var(--accent-cyan);
            min-width: 80px;
        }}
        
        .window-input-group span {{
            color: var(--text-secondary);
            font-family: 'JetBrains Mono', monospace;
        }}
        
        .time-select {{
            padding: 8px 12px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            background: var(--bg-card);
            color: var(--text-primary);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            cursor: pointer;
            min-width: 90px;
        }}
        
        .time-select:focus {{
            outline: none;
            border-color: var(--accent-teal);
        }}
        
        .t-reference {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 15px;
            padding: 10px 15px;
            background: rgba(20, 184, 166, 0.1);
            border-radius: 8px;
            border-left: 3px solid var(--accent-teal);
        }}
        
        .t-label {{
            font-weight: 600;
            color: var(--accent-teal);
        }}
        
        .t-time {{
            color: var(--text-muted);
            font-size: 0.85rem;
        }}
        
        .update-btn {{
            margin-top: 15px;
            padding: 12px 30px;
            background: linear-gradient(135deg, var(--accent-teal), var(--accent-cyan));
            border: none;
            border-radius: 8px;
            color: var(--bg-primary);
            font-weight: 600;
            font-size: 0.95rem;
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        .update-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(20, 184, 166, 0.4);
        }}
        
        .max-move-controls {{
            display: flex;
            align-items: center;
            gap: 20px;
            padding: 15px 20px;
            background: var(--bg-tertiary);
            border-radius: 10px;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }}
        
        .max-move-controls label {{
            font-weight: 600;
            color: var(--accent-cyan);
        }}
        
        .max-move-controls select {{
            padding: 8px 15px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            background: var(--bg-card);
            color: var(--text-primary);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            cursor: pointer;
        }}
        
        .max-move-controls select:focus {{
            outline: none;
            border-color: var(--accent-teal);
        }}
        
        .max-move-stats {{
            display: flex;
            gap: 25px;
            margin-left: auto;
            flex-wrap: wrap;
        }}
        
        .max-stat {{
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
        }}
        
        .max-stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .max-stat-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .max-stat-value.positive {{ color: var(--accent-green); }}
        .max-stat-value.negative {{ color: var(--accent-red); }}
        
        .generated-info {{
            color: var(--text-muted);
            font-size: 0.8rem;
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid var(--border-color);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>FOMC Press Conferences Analysis</h1>
            <p class="subtitle">TY Treasury Futures price paths around Fed press conferences | T = Press Conference Start (typically 2:30 PM ET)</p>
            
            <div class="filters">
                <button class="filter-pill active" data-filter="all">All Meetings</button>
                <button class="filter-pill" data-filter="month-end">Month-End Only</button>
                <button class="filter-pill hawkish" data-filter="hawkish">Hawkish</button>
                <button class="filter-pill dovish" data-filter="dovish">Dovish</button>
            </div>
            
            <div class="stats-bar">
                <div class="stat-item">
                    <div class="stat-label">Showing</div>
                    <div class="stat-value" id="showing-count">-</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Date Range</div>
                    <div class="stat-value" id="date-range">-</div>
                </div>
            </div>
        </header>
        
        <div class="section">
            <div class="section-title">Normalized Price Paths (bps from T-60m)</div>
            <div class="max-move-controls">
                <label>Max Move Window:</label>
                <select id="max-move-window" onchange="updateMaxMoveStats()">
                    <option value="1">T to T+1m</option>
                    <option value="5">T to T+5m</option>
                    <option value="15">T to T+15m</option>
                    <option value="30">T to T+30m</option>
                    <option value="60" selected>T to T+60m</option>
                    <option value="120">T to T+120m</option>
                </select>
                <div class="max-move-stats" id="max-move-stats">
                    <div class="max-stat">
                        <span class="max-stat-label">Mean Max Up</span>
                        <span class="max-stat-value positive" id="mean-max-up">-</span>
                    </div>
                    <div class="max-stat">
                        <span class="max-stat-label">Mean Max Down</span>
                        <span class="max-stat-value negative" id="mean-max-down">-</span>
                    </div>
                    <div class="max-stat">
                        <span class="max-stat-label">P5 / P95</span>
                        <span class="max-stat-value" id="max-percentiles">-</span>
                    </div>
                    <div class="max-stat">
                        <span class="max-stat-label">Median</span>
                        <span class="max-stat-value" id="max-median">-</span>
                    </div>
                </div>
            </div>
            <div id="price-chart"></div>
        </div>
        
        <div class="section">
            <div class="section-title">FOMC Meetings (Click to view details)</div>
            <div class="events-grid" id="events-grid"></div>
        </div>
        
        <div class="section">
            <div class="section-title">Return Distributions by Window</div>
            
            <div class="window-controls">
                <div class="t-reference">
                    <span class="t-label">T = Press Conference Start</span>
                    <span class="t-time">(typically 2:30 PM ET)</span>
                </div>
                <div class="window-row">
                    <div class="window-input-group">
                        <label>Return Window:</label>
                        <select id="w1-start" class="time-select">
                            <option value="-300">T-5h</option>
                            <option value="-240">T-4h</option>
                            <option value="-180">T-3h</option>
                            <option value="-120">T-2h</option>
                            <option value="-60">T-1h</option>
                            <option value="-30">T-30m</option>
                            <option value="-15">T-15m</option>
                            <option value="0" selected>T</option>
                            <option value="15">T+15m</option>
                            <option value="30">T+30m</option>
                            <option value="60">T+1h</option>
                            <option value="120">T+2h</option>
                            <option value="180">T+3h</option>
                            <option value="240">T+4h</option>
                            <option value="300">T+5h</option>
                        </select>
                        <span>to</span>
                        <select id="w1-end" class="time-select">
                            <option value="-300">T-5h</option>
                            <option value="-240">T-4h</option>
                            <option value="-180">T-3h</option>
                            <option value="-120">T-2h</option>
                            <option value="-60">T-1h</option>
                            <option value="-30">T-30m</option>
                            <option value="-15">T-15m</option>
                            <option value="0">T</option>
                            <option value="15">T+15m</option>
                            <option value="30">T+30m</option>
                            <option value="60">T+1h</option>
                            <option value="120" selected>T+2h</option>
                            <option value="180">T+3h</option>
                            <option value="240">T+4h</option>
                            <option value="300">T+5h</option>
                        </select>
                        <button class="update-btn" onclick="updateDistributions()" style="margin-left: 15px; margin-top: 0;">Update</button>
                    </div>
                </div>
            </div>
            
            <div class="distro-single">
                <div id="distro-w1" class="distro-chart-large"></div>
                <div id="stats-w1" class="stats-box"></div>
            </div>
        </div>
        
        <p class="generated-info">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | TY Treasury Futures 1-min data | Sentiment via Perplexity</p>
    </div>
    
    <!-- Modal -->
    <div class="modal" id="detail-modal">
        <div class="modal-content" id="modal-content">
            <div class="modal-header">
                <div style="flex: 1;">
                    <div class="modal-title" id="modal-title">FOMC Meeting Details</div>
                    <div class="modal-summary" id="modal-summary"></div>
                    <div class="modal-citations" id="modal-citations"></div>
                </div>
                <div style="display: flex; align-items: flex-start;">
                    <button class="modal-expand" id="expand-btn" onclick="toggleExpand()" title="Expand/Collapse">⛶</button>
                    <button class="modal-close" onclick="closeModal()">&times;</button>
                </div>
            </div>
            <div class="modal-body">
                <div id="modal-candlestick"></div>
                <div class="events-list">
                    <div class="events-list-title">Economic Events on This Day</div>
                    <div id="econ-events-container"></div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        const eventsData = {events_json};
        const returnDistros = {return_distros_json};
        const returnStats = {return_stats_json};
        
        const colors = [
            '#14b8a6', '#22d3ee', '#a855f7', '#f97316', '#22c55e',
            '#3b82f6', '#ef4444', '#fbbf24', '#ec4899', '#8b5cf6'
        ];
        
        let currentFilter = 'all';
        
        // Filter logic
        function matchesFilter(event, filter) {{
            if (filter === 'all') return true;
            if (filter === 'month-end') return event.is_month_end;
            if (filter === 'hawkish') return event.sentiment_label === 'hawkish';
            if (filter === 'dovish') return event.sentiment_label === 'dovish';
            return true;
        }}
        
        function getFilteredEvents() {{
            return eventsData.filter(e => matchesFilter(e, currentFilter) && e.has_data);
        }}
        
        function updateStats() {{
            const filtered = getFilteredEvents();
            document.getElementById('showing-count').textContent = filtered.length + ' / ' + eventsData.filter(e => e.has_data).length;
            
            if (filtered.length > 0) {{
                const dates = filtered.map(e => e.date).sort();
                document.getElementById('date-range').textContent = dates[0] + ' - ' + dates[dates.length - 1];
            }}
            
            // Update max move stats
            updateMaxMoveStats();
        }}
        
        // Compute max move (up and down) within a window for an event
        // Caps window at 5 PM ET (17:00) on the event day
        function computeMaxMoves(event, windowMinutes) {{
            const path = event.normalized_path;
            if (!path || !path.minutes_from_anchor || path.minutes_from_anchor.length === 0) return null;
            
            const minutes = path.minutes_from_anchor;
            const bpsChanges = path.bps_changes;
            
            // Cap windowMinutes at 5 PM ET
            let effectiveWindow = windowMinutes;
            if (event.anchor_time) {{
                const anchorDate = new Date(event.anchor_time);
                const anchorHour = anchorDate.getHours();
                const anchorMinute = anchorDate.getMinutes();
                const minutesTo5PM = (17 - anchorHour) * 60 - anchorMinute;
                if (effectiveWindow > minutesTo5PM) {{
                    effectiveWindow = minutesTo5PM;
                }}
            }}
            
            // Find index of T=0 (conference start)
            let t0Idx = -1;
            let t0Dist = Infinity;
            for (let i = 0; i < minutes.length; i++) {{
                const dist = Math.abs(minutes[i]);
                if (dist < t0Dist) {{
                    t0Dist = dist;
                    t0Idx = i;
                }}
            }}
            if (t0Idx === -1) return null;
            
            const refBps = bpsChanges[t0Idx];  // BPS at T=0
            
            // Find max up and max down within window
            let maxUp = 0;
            let maxDown = 0;
            
            for (let i = t0Idx; i < minutes.length; i++) {{
                if (minutes[i] > effectiveWindow) break;
                const delta = bpsChanges[i] - refBps;
                if (delta > maxUp) maxUp = delta;
                if (delta < maxDown) maxDown = delta;
            }}
            
            return {{ maxUp, maxDown }};
        }}
        
        // Update max move statistics display
        function updateMaxMoveStats() {{
            const windowMinutes = parseInt(document.getElementById('max-move-window').value) || 60;
            const filtered = getFilteredEvents();
            
            const maxUps = [];
            const maxDowns = [];
            
            filtered.forEach(event => {{
                const moves = computeMaxMoves(event, windowMinutes);
                if (moves) {{
                    maxUps.push(moves.maxUp);
                    maxDowns.push(moves.maxDown);
                }}
            }});
            
            if (maxUps.length === 0) {{
                document.getElementById('mean-max-up').textContent = '-';
                document.getElementById('mean-max-down').textContent = '-';
                document.getElementById('max-percentiles').textContent = '-';
                document.getElementById('max-median').textContent = '-';
                return;
            }}
            
            // Compute stats
            const meanUp = maxUps.reduce((a, b) => a + b, 0) / maxUps.length;
            const meanDown = maxDowns.reduce((a, b) => a + b, 0) / maxDowns.length;
            
            const sortedUps = [...maxUps].sort((a, b) => a - b);
            const sortedDowns = [...maxDowns].sort((a, b) => a - b);
            
            const percentile = (arr, p) => {{
                const idx = (p / 100) * (arr.length - 1);
                const lower = Math.floor(idx);
                const upper = Math.ceil(idx);
                if (lower === upper) return arr[lower];
                return arr[lower] + (idx - lower) * (arr[upper] - arr[lower]);
            }};
            
            const p5Up = percentile(sortedUps, 5);
            const p95Up = percentile(sortedUps, 95);
            const medianUp = percentile(sortedUps, 50);
            const p5Down = percentile(sortedDowns, 5);
            const p95Down = percentile(sortedDowns, 95);
            const medianDown = percentile(sortedDowns, 50);
            
            document.getElementById('mean-max-up').textContent = '+' + meanUp.toFixed(1) + ' bps';
            document.getElementById('mean-max-down').textContent = meanDown.toFixed(1) + ' bps';
            document.getElementById('max-percentiles').textContent = 
                'Up: ' + p5Up.toFixed(1) + ' / +' + p95Up.toFixed(1) + ' | Down: ' + p5Down.toFixed(1) + ' / ' + p95Down.toFixed(1);
            document.getElementById('max-median').textContent = 
                'Up: +' + medianUp.toFixed(1) + ' | Down: ' + medianDown.toFixed(1);
        }}
        
        // Render event cards
        function renderEventCards() {{
            const grid = document.getElementById('events-grid');
            grid.innerHTML = '';
            
            eventsData.forEach((event, idx) => {{
                if (!event.has_data) return;
                
                const card = document.createElement('div');
                card.className = 'event-card' + (matchesFilter(event, currentFilter) ? '' : ' hidden');
                card.dataset.idx = idx;
                
                const sentimentBadge = event.sentiment_label && event.sentiment_label !== 'unknown'
                    ? `<span class="sentiment-badge ${{event.sentiment_label}}">${{event.sentiment_label.toUpperCase()}}</span>`
                    : '';
                
                const monthEndBadge = event.is_month_end
                    ? '<span class="month-end-badge">Month-End</span>'
                    : '';
                
                const summaryText = event.sentiment_summary
                    ? `<div class="event-summary">${{event.sentiment_summary.substring(0, 150)}}...</div>`
                    : '';
                
                card.innerHTML = `
                    <div class="event-date">${{event.date}}${{sentimentBadge}}${{monthEndBadge}}</div>
                    ${{summaryText}}
                `;
                
                card.onclick = () => selectEvent(idx);
                grid.appendChild(card);
            }});
        }}
        
        // Plot all paths
        function plotAllPaths() {{
            const filtered = getFilteredEvents();
            
            const traces = filtered.map((event, idx) => {{
                const path = event.normalized_path;
                if (!path || !path.bps_changes || path.bps_changes.length === 0) return null;
                
                return {{
                    x: path.minutes_from_anchor,
                    y: path.bps_changes,
                    type: 'scatter',
                    mode: 'lines',
                    name: `${{event.date}} (${{event.sentiment_label || 'N/A'}})`,
                    opacity: 0.7,
                    line: {{ width: 2, color: colors[idx % colors.length] }},
                    hovertemplate: `<b>${{event.date}}</b><br>` +
                                   `Time: T%{{x:+.0f}}m<br>` +
                                   `Change: %{{y:.1f}} bps<extra></extra>`
                }};
            }}).filter(t => t !== null);
            
            // Add vertical line at T=0 (press conference start)
            const shapes = [{{
                type: 'line',
                x0: 0, x1: 0,
                y0: 0, y1: 1,
                yref: 'paper',
                line: {{ color: '#fbbf24', width: 2, dash: 'dash' }}
            }}];
            
            const annotations = [{{
                x: 0,
                y: 1.02,
                yref: 'paper',
                text: 'Press Conference Start',
                showarrow: false,
                font: {{ size: 11, color: '#fbbf24' }}
            }}];
            
            Plotly.newPlot('price-chart', traces, {{
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                xaxis: {{
                    title: 'Minutes from Press Conference Start',
                    gridcolor: '#374151',
                    zerolinecolor: '#4b5563',
                    range: [-60, 180],  // Default view: T-1h to T+3h (full data: T-5h to T+5h)
                    rangeslider: {{ visible: true, bgcolor: '#1f2937', thickness: 0.08 }}
                }},
                yaxis: {{
                    title: 'Price Change (bps from T-60m)',
                    gridcolor: '#374151',
                    zerolinecolor: '#ef4444',
                    zeroline: true
                }},
                shapes: shapes,
                annotations: annotations,
                showlegend: true,
                legend: {{
                    orientation: 'v',
                    x: 1.02, y: 1,
                    xanchor: 'left',
                    bgcolor: 'rgba(31, 41, 55, 0.95)',
                    bordercolor: '#4b5563',
                    borderwidth: 1,
                    font: {{ size: 10 }}
                }},
                margin: {{ t: 40, r: 220, b: 100, l: 70 }},
                hovermode: 'closest'
            }}, {{ scrollZoom: true, displaylogo: false }});
        }}
        
        // Filter button handlers
        document.querySelectorAll('.filter-pill').forEach(btn => {{
            btn.addEventListener('click', () => {{
                document.querySelectorAll('.filter-pill').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentFilter = btn.dataset.filter;
                
                // Update card visibility
                document.querySelectorAll('.event-card').forEach(card => {{
                    const idx = parseInt(card.dataset.idx);
                    const event = eventsData[idx];
                    card.classList.toggle('hidden', !matchesFilter(event, currentFilter));
                }});
                
                // Replot paths and distributions
                plotAllPaths();
                updateStats();
                updateDistributions();
            }});
        }});
        
        // Modal
        function selectEvent(idx) {{
            const event = eventsData[idx];
            
            document.getElementById('modal-title').textContent = `${{event.date}} - FOMC Press Conference`;
            
            // Summary
            const summaryEl = document.getElementById('modal-summary');
            if (event.sentiment_summary) {{
                const label = event.sentiment_label ? `<strong style="color: ${{event.sentiment_label === 'hawkish' ? '#ef4444' : event.sentiment_label === 'dovish' ? '#22c55e' : '#9ca3af'}}">${{event.sentiment_label.toUpperCase()}}</strong>: ` : '';
                summaryEl.innerHTML = label + event.sentiment_summary;
                summaryEl.style.display = 'block';
            }} else {{
                summaryEl.style.display = 'none';
            }}
            
            // Citations
            const citationsEl = document.getElementById('modal-citations');
            if (event.citations && event.citations.length > 0) {{
                citationsEl.innerHTML = 'Sources: ' + event.citations.map(c => 
                    `<a href="${{c.url}}" target="_blank">${{c.title || c.url}}</a>`
                ).join(', ');
                citationsEl.style.display = 'block';
            }} else {{
                citationsEl.style.display = 'none';
            }}
            
            // Candlestick
            plotCandlestick(event);
            
            // Economic events
            renderEconomicEvents(event.economic_events || []);
            
            document.getElementById('detail-modal').classList.add('active');
        }}
        
        function decimalToBondNotation(price) {{
            const points = Math.floor(price);
            const ticks = (price - points) * 32;
            if (ticks % 1 === 0.5) {{
                return `${{points}}'${{Math.floor(ticks).toString().padStart(2, '0')}}5`;
            }}
            return `${{points}}'${{Math.round(ticks).toString().padStart(2, '0')}}`;
        }}
        
        function plotCandlestick(event) {{
            const prices = event.candlestick_data;
            if (!prices || !prices.closes || prices.closes.length === 0) return;
            
            // Resample to 5-min candles
            const candleData = resampleToCandles(prices, 5);
            
            const allPrices = [...candleData.opens, ...candleData.highs, ...candleData.lows, ...candleData.closes];
            const minPrice = Math.min(...allPrices);
            const maxPrice = Math.max(...allPrices);
            const priceRange = maxPrice - minPrice;
            
            // Dynamic tick step based on price range to avoid crowding
            // 0.5 = 16 ticks (half point), 1.0 = 32 ticks (full point)
            let tickStep = 0.5;  // Default: 16 ticks (half point)
            if (priceRange > 3) tickStep = 1.0;  // Full point for large ranges
            if (priceRange < 1) tickStep = 0.25;  // 8 ticks for small ranges
            
            const startTick = Math.floor(minPrice / tickStep) * tickStep;
            const endTick = Math.ceil(maxPrice / tickStep) * tickStep;
            
            const tickVals = [];
            const tickText = [];
            for (let p = startTick; p <= endTick; p += tickStep) {{
                tickVals.push(p);
                tickText.push(decimalToBondNotation(p));
            }}
            
            const trace = {{
                x: candleData.timestamps,
                open: candleData.opens,
                high: candleData.highs,
                low: candleData.lows,
                close: candleData.closes,
                type: 'candlestick',
                increasing: {{ line: {{ color: '#22c55e' }}, fillcolor: '#22c55e' }},
                decreasing: {{ line: {{ color: '#ef4444' }}, fillcolor: '#ef4444' }}
            }};
            
            // Mark press conference time
            const shapes = [];
            const annotations = [];
            
            if (event.anchor_time) {{
                shapes.push({{
                    type: 'line',
                    x0: event.anchor_time,
                    x1: event.anchor_time,
                    y0: 0, y1: 1,
                    yref: 'paper',
                    line: {{ color: '#fbbf24', width: 2, dash: 'dash' }}
                }});
                annotations.push({{
                    x: event.anchor_time,
                    y: 0.98,
                    yref: 'paper',
                    text: 'Press Conf Start',
                    showarrow: false,
                    font: {{ size: 10, color: '#fbbf24' }},
                    xanchor: 'left',
                    xshift: 5
                }});
            }}
            
            Plotly.newPlot('modal-candlestick', [trace], {{
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                xaxis: {{
                    title: 'Time (±2 days)',
                    gridcolor: '#374151',
                    type: 'date',
                    tickformat: '%b %d %H:%M',
                    rangeslider: {{ visible: true, bgcolor: '#1f2937', thickness: 0.1 }}
                }},
                yaxis: {{
                    title: 'Price (32nds)',
                    gridcolor: '#374151',
                    tickmode: 'array',
                    tickvals: tickVals,
                    ticktext: tickText
                }},
                shapes: shapes,
                annotations: annotations,
                margin: {{ t: 40, r: 60, b: 100, l: 70 }}
            }}, {{ scrollZoom: true, displaylogo: false }});
        }}
        
        function resampleToCandles(prices, minutes) {{
            const result = {{ timestamps: [], opens: [], highs: [], lows: [], closes: [] }};
            for (let i = 0; i < prices.timestamps.length; i += minutes) {{
                const end = Math.min(i + minutes, prices.timestamps.length);
                const slice = {{
                    opens: prices.opens.slice(i, end),
                    highs: prices.highs.slice(i, end),
                    lows: prices.lows.slice(i, end),
                    closes: prices.closes.slice(i, end)
                }};
                if (slice.opens.length > 0) {{
                    result.timestamps.push(prices.timestamps[i]);
                    result.opens.push(slice.opens[0]);
                    result.highs.push(Math.max(...slice.highs));
                    result.lows.push(Math.min(...slice.lows));
                    result.closes.push(slice.closes[slice.closes.length - 1]);
                }}
            }}
            return result;
        }}
        
        function renderEconomicEvents(events) {{
            const container = document.getElementById('econ-events-container');
            if (!events || events.length === 0) {{
                container.innerHTML = '<div style="color: #6b7280;">No other economic events recorded</div>';
                return;
            }}
            container.innerHTML = events.map(ev => {{
                const time = new Date(ev.timestamp).toLocaleTimeString('en-US', {{ hour: '2-digit', minute: '2-digit' }});
                const values = [];
                if (ev.actual !== null) values.push(`A: ${{ev.actual}}`);
                if (ev.consensus !== null) values.push(`C: ${{ev.consensus}}`);
                return `
                    <div class="econ-event importance-${{ev.importance}}">
                        <span>${{ev.name}}</span>
                        <span style="color: #6b7280;">${{time}}</span>
                        <span style="color: #22c55e;">${{values.join(' | ') || '-'}}</span>
                    </div>
                `;
            }}).join('');
        }}
        
        function closeModal() {{
            document.getElementById('detail-modal').classList.remove('active');
            document.getElementById('modal-content').classList.remove('expanded');
        }}
        
        function toggleExpand() {{
            const modalContent = document.getElementById('modal-content');
            const isExpanded = modalContent.classList.toggle('expanded');
            document.getElementById('expand-btn').textContent = isExpanded ? '⛶' : '⛶';
            document.getElementById('expand-btn').title = isExpanded ? 'Collapse' : 'Expand to fullscreen';
            
            // Resize the Plotly chart after transition
            setTimeout(() => {{
                Plotly.Plots.resize('modal-candlestick');
            }}, 350);
        }}
        
        document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});
        document.getElementById('detail-modal').addEventListener('click', e => {{
            if (e.target.classList.contains('modal')) closeModal();
        }});
        
        // BPS_PER_POINT constant from treasury_price_conversion.py
        const BPS_PER_POINT = {BPS_PER_POINT};
        
        // Compute return for a specific window from normalized path data
        // Caps end time at 5 PM ET (17:00) on the event day
        function computeReturnForWindow(event, startMin, endMin) {{
            const path = event.normalized_path;
            if (!path || !path.minutes_from_anchor || path.minutes_from_anchor.length === 0) return null;
            
            const minutes = path.minutes_from_anchor;
            const bpsChanges = path.bps_changes;
            
            // Cap endMin at 5 PM ET (market close)
            // anchor_time is the press conference start (typically 2:30 PM ET = 14:30)
            // 5 PM = 17:00, so max offset = (17:00 - anchor_hour) * 60 - anchor_minutes
            if (event.anchor_time) {{
                const anchorDate = new Date(event.anchor_time);
                const anchorHour = anchorDate.getHours();
                const anchorMinute = anchorDate.getMinutes();
                // Minutes from anchor to 5 PM (17:00)
                const minutesTo5PM = (17 - anchorHour) * 60 - anchorMinute;
                if (endMin > minutesTo5PM) {{
                    endMin = minutesTo5PM;
                }}
            }}
            
            // Find closest minute to startMin
            let startIdx = -1;
            let startDist = Infinity;
            for (let i = 0; i < minutes.length; i++) {{
                const dist = Math.abs(minutes[i] - startMin);
                if (dist < startDist && dist <= 3) {{
                    startDist = dist;
                    startIdx = i;
                }}
            }}
            
            // Find closest minute to endMin
            let endIdx = -1;
            let endDist = Infinity;
            for (let i = 0; i < minutes.length; i++) {{
                const dist = Math.abs(minutes[i] - endMin);
                if (dist < endDist && dist <= 3) {{
                    endDist = dist;
                    endIdx = i;
                }}
            }}
            
            if (startIdx === -1 || endIdx === -1) return null;
            
            // Return is the difference in bps values
            return bpsChanges[endIdx] - bpsChanges[startIdx];
        }}
        
        // Compute statistics for an array
        function computeStats(data) {{
            if (!data || data.length === 0) return null;
            const sorted = [...data].sort((a, b) => a - b);
            const n = sorted.length;
            const sum = sorted.reduce((a, b) => a + b, 0);
            const mean = sum / n;
            const variance = sorted.reduce((s, v) => s + Math.pow(v - mean, 2), 0) / n;
            const std = Math.sqrt(variance);
            
            const percentile = (arr, p) => {{
                const idx = (p / 100) * (arr.length - 1);
                const lower = Math.floor(idx);
                const upper = Math.ceil(idx);
                if (lower === upper) return arr[lower];
                return arr[lower] + (idx - lower) * (arr[upper] - arr[lower]);
            }};
            
            // Compute what percentile 0 is at (what % of values are below 0)
            const countBelowZero = sorted.filter(v => v < 0).length;
            const zeroPercentile = (countBelowZero / n) * 100;
            
            return {{
                mean: mean,
                std: std,
                min: sorted[0],
                max: sorted[n - 1],
                p5: percentile(sorted, 5),
                p25: percentile(sorted, 25),
                p50: percentile(sorted, 50),
                p75: percentile(sorted, 75),
                p95: percentile(sorted, 95),
                n: n,
                zeroPercentile: zeroPercentile
            }};
        }}
        
        // Get current window configurations from inputs
        function getWindowConfigs() {{
            return [
                {{ id: 'w1', start: parseInt(document.getElementById('w1-start').value) || 0, end: parseInt(document.getElementById('w1-end').value) || 120 }}
            ];
        }}
        
        // Update distributions based on current filter and window configs
        function updateDistributions() {{
            const filtered = getFilteredEvents();
            const windowConfigs = getWindowConfigs();
            
            windowConfigs.forEach(wc => {{
                const returns = [];
                filtered.forEach(event => {{
                    const ret = computeReturnForWindow(event, wc.start, wc.end);
                    if (ret !== null) returns.push(ret);
                }});
                
                const title = `T${{wc.start >= 0 ? '+' : ''}}${{wc.start}}m to T${{wc.end >= 0 ? '+' : ''}}${{wc.end}}m`;
                const stats = computeStats(returns);
                
                plotSingleDistribution(wc.id, returns, stats, title);
            }});
        }}
        
        // Plot a single distribution
        function plotSingleDistribution(containerId, data, stats, title) {{
            const chartEl = document.getElementById(`distro-${{containerId}}`);
            const statsBox = document.getElementById(`stats-${{containerId}}`);
            
            if (data.length === 0) {{
                chartEl.innerHTML = '<div style="color: #6b7280; text-align: center; padding: 50px;">No data for this window</div>';
                statsBox.innerHTML = `<h4>${{title}}</h4><div style="color: #6b7280;">No data</div>`;
                return;
            }}
            
            const trace = {{
                x: data,
                type: 'histogram',
                marker: {{ color: 'rgba(59, 130, 246, 0.6)', line: {{ color: '#3b82f6', width: 1 }} }},
                nbinsx: 12,
                histnorm: 'probability density'
            }};
            
            const kde = computeKDE(data, 40);
            const kdeTrace = {{
                x: kde.x,
                y: kde.y,
                type: 'scatter',
                mode: 'lines',
                line: {{ color: '#22d3ee', width: 2 }}
            }};
            
            const shapes = [];
            if (stats) {{
                shapes.push({{ type: 'line', x0: stats.mean, x1: stats.mean, y0: 0, y1: 1, yref: 'paper', line: {{ color: '#fbbf24', width: 2, dash: 'dash' }} }});
                shapes.push({{ type: 'line', x0: 0, x1: 0, y0: 0, y1: 1, yref: 'paper', line: {{ color: '#ef4444', width: 1 }} }});
            }}
            
            Plotly.newPlot(`distro-${{containerId}}`, [trace, kdeTrace], {{
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                title: {{ text: title, font: {{ size: 13 }} }},
                xaxis: {{ title: 'Return (bps)', gridcolor: '#374151', zerolinecolor: '#ef4444' }},
                yaxis: {{ title: 'Density', gridcolor: '#374151' }},
                shapes: shapes,
                showlegend: false,
                margin: {{ t: 45, r: 15, b: 45, l: 50 }}
            }}, {{ displaylogo: false }});
            
            if (statsBox && stats) {{
                // Determine color for zero percentile (green if mostly positive, red if mostly negative)
                const zeroColor = stats.zeroPercentile > 50 ? '#ef4444' : '#22c55e';
                const zeroLabel = stats.zeroPercentile > 50 
                    ? `${{(100 - stats.zeroPercentile).toFixed(0)}}% positive`
                    : `${{stats.zeroPercentile.toFixed(0)}}% negative`;
                
                statsBox.innerHTML = `
                    <h4>${{title}}</h4>
                    <div class="stats-row"><span class="label">Mean</span><span class="value" style="color: #fbbf24;">${{stats.mean.toFixed(1)}} bps</span></div>
                    <div class="stats-row"><span class="label">Median</span><span class="value" style="color: #22d3ee;">${{stats.p50.toFixed(1)}} bps</span></div>
                    <div class="stats-row"><span class="label">Std</span><span class="value">${{stats.std.toFixed(1)}}</span></div>
                    <div class="stats-row"><span class="label">P5</span><span class="value" style="color: #ef4444;">${{stats.p5.toFixed(1)}}</span></div>
                    <div class="stats-row"><span class="label">P95</span><span class="value" style="color: #22c55e;">${{stats.p95.toFixed(1)}}</span></div>
                    <div class="stats-row" style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #374151;">
                        <span class="label">0 bps @ P</span>
                        <span class="value" style="color: ${{zeroColor}}; font-weight: 700;">${{stats.zeroPercentile.toFixed(0)}}</span>
                    </div>
                    <div style="font-size: 0.75rem; color: #6b7280; margin-top: 2px;">(${{zeroLabel}})</div>
                    <div class="stats-row" style="margin-top: 8px; color: #6b7280; font-size: 0.8rem;"><span>n = ${{stats.n}}</span></div>
                `;
            }}
        }}
        
        // Legacy function for initial load
        function plotDistributions() {{
            updateDistributions();
        }}
        
        function computeKDE(data, numPoints) {{
            if (!data || data.length === 0) return {{ x: [], y: [] }};
            const min = Math.min(...data);
            const max = Math.max(...data);
            const range = max - min || 1;
            const std = standardDeviation(data);
            const bandwidth = 1.06 * std * Math.pow(data.length, -0.2);
            
            const x = [];
            const y = [];
            const step = range / numPoints;
            
            for (let i = 0; i <= numPoints; i++) {{
                const xi = min + i * step;
                x.push(xi);
                let density = 0;
                for (const d of data) {{
                    density += Math.exp(-0.5 * Math.pow((xi - d) / bandwidth, 2)) / Math.sqrt(2 * Math.PI);
                }}
                y.push(density / (data.length * bandwidth));
            }}
            return {{ x, y }};
        }}
        
        function standardDeviation(arr) {{
            if (!arr.length) return 0;
            const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
            return Math.sqrt(arr.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / arr.length);
        }}
        
        // Initialize
        renderEventCards();
        plotAllPaths();
        updateStats();
        plotDistributions();
    </script>
</body>
</html>'''
    
    return html


def main():
    parser = argparse.ArgumentParser(description="Generate FOMC Meetings Analysis Dashboard")
    parser.add_argument("--output", type=str, default=None, help="Output HTML file path")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh web sentiments")
    args = parser.parse_args()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path(args.output) if args.output else OUTPUT_DIR / f"fomc_meetings_dashboard_{timestamp}.html"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    logger.info("Generating FOMC Meetings Analysis Dashboard")
    
    # Fetch FOMC events
    fomc_events = fetch_fomc_events()
    if not fomc_events:
        logger.error("No FOMC events found")
        return
    
    # Get dates for sentiment lookup
    dates = []
    for ev in fomc_events:
        dt = parse_event_datetime(ev["event_date"])
        if dt:
            dates.append(dt.strftime("%Y-%m-%d"))
    
    # Fetch sentiments (with caching)
    logger.info(f"Fetching sentiments for {len(dates)} dates...")
    
    def progress_cb(current, total, date):
        cached_sentiments = get_all_cached_sentiments()
        is_cached = date in cached_sentiments
        status = "cached" if is_cached and not args.force_refresh else "fetching"
        logger.info(f"  [{current}/{total}] {date} ({status})")
    
    sentiments = batch_get_or_fetch_sentiments(
        dates,
        force_refresh=args.force_refresh,
        progress_callback=progress_cb,
    )
    
    # Connect to price database
    conn = get_db_connection()
    
    # Process each event
    events_data = []
    return_distributions = {name: [] for name in RETURN_WINDOWS}
    
    for ev in fomc_events:
        anchor_dt = parse_event_datetime(ev["event_date"])
        if not anchor_dt:
            continue
        
        date_str = anchor_dt.strftime("%Y-%m-%d")
        logger.info(f"Processing: {date_str}")
        
        # Fetch price data for path
        price_data = fetch_price_data_for_window(
            anchor_dt, PATH_START_OFFSET, PATH_END_OFFSET, conn
        )
        
        has_data = len(price_data.get("closes", [])) >= 10
        
        # Normalized path
        normalized_path = compute_normalized_path(price_data, anchor_dt) if has_data else {}
        
        # Return windows
        return_windows = compute_return_windows(price_data, anchor_dt) if has_data else {}
        
        # Add to distributions
        for name, value in return_windows.items():
            if value is not None:
                return_distributions[name].append(value)
        
        # Candlestick data
        candlestick_data = fetch_candlestick_data(
            anchor_dt, CANDLE_DAYS_BEFORE, CANDLE_DAYS_AFTER, conn
        ) if has_data else {}
        
        # Economic events on this date
        econ_events = fetch_economic_events_for_date(date_str)
        
        # Sentiment
        sentiment = sentiments.get(date_str, {})
        
        events_data.append({
            "date": date_str,
            "anchor_time": anchor_dt.isoformat(),
            "is_month_end": is_month_end(anchor_dt),
            "sentiment_label": sentiment.get("label", "unknown"),
            "sentiment_summary": sentiment.get("summary", ""),
            "sentiment_confidence": sentiment.get("confidence", 0),
            "citations": sentiment.get("citations", []),
            "has_data": has_data,
            "normalized_path": normalized_path,
            "return_windows": return_windows,
            "candlestick_data": candlestick_data,
            "economic_events": econ_events,
        })
        
        if has_data:
            logger.info(f"  -> {len(price_data['closes'])} bars, sentiment: {sentiment.get('label', 'N/A')}")
        else:
            logger.warning(f"  -> No price data")
    
    conn.close()
    
    # Compute distribution stats
    return_stats = {name: compute_stats(vals) for name, vals in return_distributions.items()}
    
    logger.info(f"Processed {len(events_data)} events, {sum(1 for e in events_data if e['has_data'])} with data")
    
    # Generate HTML
    html = generate_html(events_data, return_distributions, return_stats)
    
    # Write output
    output_file.write_text(html, encoding="utf-8")
    logger.info(f"Dashboard saved to: {output_file}")
    logger.info(f"Open: file:///{output_file.as_posix()}")


if __name__ == "__main__":
    main()

