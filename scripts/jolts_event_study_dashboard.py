#!/usr/bin/env python3
"""
JOLTS ↔ ISM Event-Study Dashboard Generator
============================================

Generates a standalone interactive HTML dashboard comparing:
- JOLTS days following an ISM Manufacturing PMI day ("with_ism")
- JOLTS standalone days ("standalone")

Features:
- Toggle pre-window lengths: 30m / 1h / 2h / 4h
- Binning by ISM surprise (previous weekday) or JOLTS cons-std buckets
- Normalized price paths (top), click-to-candlestick modal
- Distribution plots (bottom)
- Price->ticks via rules/treasury_price_conversion.py; ticks->bps uses the
  local 10-year DV01 factor (2 ticks/bp, ~16 bps/point) via TY_TICKS_PER_BP

Usage:
    python scripts/jolts_event_study_dashboard.py
    python scripts/jolts_event_study_dashboard.py --start-date 2022-01-01 --out custom.html
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from config import settings
from rules.treasury_price_conversion import (
    points_to_ticks,
    decimal_to_bond_notation,
    TICKS_PER_POINT,
)

# 10-Year T-Note (TY/ZN) yield-bp conversion.
# DV01 ~ $63/bp and 1 point = $1,000/contract => ~2 ticks per yield bp
# (1 point = 32 ticks ~ 16 bps). The shared rules module uses 4 ticks/bp
# (~30-year bond DV01), which understates the 10-year move by ~2x, so we
# convert locally here instead of using ticks_to_bps().
TY_TICKS_PER_BP = 2.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Event name constants (DB uses lowercase 's' in JOLTs)
JOLTS_EVENT_NAME = "JOLTs Job Openings"
ISM_EVENT_NAME = "ISM Manufacturing PMI"

# Window choices in minutes
WINDOW_CHOICES = [30, 60, 120, 240]


# =============================================================================
# DATABASE HELPERS
# =============================================================================

def get_economic_events_db() -> Path:
    return settings.ECONOMIC_EVENTS_DB_PATH


def load_events_by_name(event_name: str, start_date: str, end_date: Optional[str] = None) -> pd.DataFrame:
    """Load events from economic_events.db by event name."""
    db_path = get_economic_events_db()
    if not db_path.exists():
        raise FileNotFoundError(f"Economic events DB not found: {db_path}")
    
    conn = sqlite3.connect(str(db_path))
    
    query = """
        SELECT event_date, actual, consensus, consensus_std_dev, importance
        FROM economic_events
        WHERE event_name = ?
          AND country LIKE '%United States%'
          AND event_date >= ?
    """
    params: List[Any] = [event_name, f"{start_date}T00:00:00"]
    
    if end_date:
        query += " AND event_date <= ?"
        params.append(f"{end_date}T23:59:59")
    
    query += " ORDER BY event_date ASC"
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    
    if df.empty:
        # Try to find similar event names
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT event_name FROM economic_events WHERE event_name LIKE ? LIMIT 10",
            (f"%{event_name.split()[0]}%",)
        )
        similar = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        msg = f"No events found for '{event_name}'"
        if similar:
            msg += f". Did you mean: {', '.join(similar[:5])}?"
        logger.warning(msg)
        return pd.DataFrame()
    
    # Parse event_date to datetime
    df["event_dt"] = pd.to_datetime(df["event_date"], errors="coerce")
    df["date"] = df["event_dt"].dt.strftime("%Y-%m-%d")
    df["time"] = df["event_dt"].dt.strftime("%H:%M")
    
    return df


def fetch_ohlcv_range(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """Fetch 1-min OHLCV from Postgres for the given range."""
    conn = psycopg2.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        dbname=settings.PG_DATABASE,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
        cursor_factory=RealDictCursor,
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT ts_event, open, high, low, close, volume
        FROM {settings.PG_OHLCV_TABLE}
        WHERE symbol ILIKE %s
          AND ts_event >= %s
          AND ts_event <= %s
        ORDER BY ts_event ASC
        """,
        (f"{symbol}%", start_dt, end_dt),
    )
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        return pd.DataFrame()
    
    df = pd.DataFrame(rows)
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert(ET)
    return df


# =============================================================================
# DATA PROCESSING
# =============================================================================

def parse_event_dt_et(event_date_str: str) -> datetime:
    """Parse event_date string to ET datetime."""
    s = str(event_date_str).replace(" ", "T")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def get_previous_weekday(dt: datetime) -> datetime:
    """Get previous business day (weekday only, no holiday calendar)."""
    prev = dt - timedelta(days=1)
    while prev.weekday() >= 5:  # Saturday=5, Sunday=6
        prev -= timedelta(days=1)
    return prev


def label_jolts_events(
    jolts_df: pd.DataFrame,
    ism_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Label each JOLTS event as 'with_ism' or 'standalone'.
    Also compute ism_surprise_prev for 'with_ism' events.
    """
    # Build a dict of ISM events by date
    ism_by_date: Dict[str, Dict[str, Any]] = {}
    for _, row in ism_df.iterrows():
        date_str = row["date"]
        ism_by_date[date_str] = {
            "actual": row["actual"],
            "consensus": row["consensus"],
            "surprise": (row["actual"] - row["consensus"]) if pd.notna(row["actual"]) and pd.notna(row["consensus"]) else None,
        }
    
    # Label JOLTS events
    modes: List[str] = []
    ism_surprise_prevs: List[Optional[float]] = []
    
    for _, row in jolts_df.iterrows():
        event_dt = parse_event_dt_et(row["event_date"])
        prev_weekday = get_previous_weekday(event_dt)
        prev_date_str = prev_weekday.strftime("%Y-%m-%d")
        
        if prev_date_str in ism_by_date:
            modes.append("with_ism")
            ism_surprise_prevs.append(ism_by_date[prev_date_str]["surprise"])
        else:
            modes.append("standalone")
            ism_surprise_prevs.append(None)
    
    jolts_df = jolts_df.copy()
    jolts_df["jolts_mode"] = modes
    jolts_df["ism_surprise_prev"] = ism_surprise_prevs
    
    # Compute JOLTS surprise
    jolts_df["surprise"] = jolts_df["actual"] - jolts_df["consensus"]
    
    return jolts_df


def compute_cons_std_buckets(
    df: pd.DataFrame,
    std_col: str = "consensus_std_dev",
) -> pd.DataFrame:
    """Assign Low/Medium/High buckets based on cons-std relative to median."""
    df = df.copy()
    
    valid_std = df[std_col].dropna()
    if len(valid_std) < 3:
        df["cons_std_bucket"] = "N/A"
        return df
    
    median_std = valid_std.median()
    
    def categorize(val):
        if pd.isna(val):
            return "N/A"
        if val < median_std * 0.5:
            return "Low"
        elif val <= median_std * 1.5:
            return "Medium"
        else:
            return "High"
    
    df["cons_std_bucket"] = df[std_col].apply(categorize)
    return df


def compute_window_metrics(
    jolts_df: pd.DataFrame,
    symbol: str,
    windows: List[int],
) -> Tuple[pd.DataFrame, Dict[str, List[Dict[str, Any]]]]:
    """
    For each JOLTS event and each window, compute:
    - pre_move_ticks: price change from T-W to T in ticks
    - post_move_ticks: price change from T to T+W in ticks
    - price path data for visualization
    
    Returns:
        - Updated DataFrame with window metrics columns
        - Dictionary of aligned price paths per event
    """
    max_window = max(windows)
    
    # Pre-fetch all needed OHLCV data
    min_dt = None
    max_dt = None
    for _, row in jolts_df.iterrows():
        event_dt = parse_event_dt_et(row["event_date"])
        start = event_dt - timedelta(minutes=max_window + 10)
        end = event_dt + timedelta(minutes=max_window + 10)
        if min_dt is None or start < min_dt:
            min_dt = start
        if max_dt is None or end > max_dt:
            max_dt = end
    
    logger.info(f"Fetching OHLCV for {symbol} from {min_dt} to {max_dt}")
    ohlcv_df = fetch_ohlcv_range(symbol, min_dt, max_dt)
    
    if ohlcv_df.empty:
        logger.warning("No OHLCV data found")
        return jolts_df, {}
    
    # Initialize columns for each window
    for w in windows:
        jolts_df[f"pre_move_ticks_{w}m"] = np.nan
        jolts_df[f"post_move_ticks_{w}m"] = np.nan
    
    paths_data: Dict[str, List[Dict[str, Any]]] = {}
    candles_data: Dict[str, List[Dict[str, Any]]] = {}
    
    for idx, row in jolts_df.iterrows():
        event_dt = parse_event_dt_et(row["event_date"])
        event_date = row["date"]
        
        # Get price at event time
        mask_event = (ohlcv_df["ts_event"] >= event_dt - timedelta(minutes=1)) & \
                     (ohlcv_df["ts_event"] <= event_dt + timedelta(minutes=1))
        event_prices = ohlcv_df.loc[mask_event]
        if event_prices.empty:
            continue
        
        price_at_event = float(event_prices.iloc[-1]["close"])
        
        # Compute path for max window (used for all visualizations)
        path_start = event_dt - timedelta(minutes=max_window)
        path_end = event_dt + timedelta(minutes=max_window)
        
        mask_path = (ohlcv_df["ts_event"] >= path_start) & (ohlcv_df["ts_event"] <= path_end)
        path_df = ohlcv_df.loc[mask_path].copy()
        
        if len(path_df) < 10:
            continue
        
        # Normalize to window start price
        start_price = float(path_df.iloc[0]["close"])
        
        # Build path data
        path_points: List[Dict[str, Any]] = []
        for _, prow in path_df.iterrows():
            ts = prow["ts_event"]
            rel_min = int((ts - event_dt).total_seconds() / 60)
            price = float(prow["close"])
            move_points = price - start_price
            move_ticks = points_to_ticks(move_points)
            move_bps = move_ticks / TY_TICKS_PER_BP
            
            path_points.append({
                "rel_min": rel_min,
                "ts": ts.isoformat(),
                "price": price,
                "ticks": round(move_ticks, 2),
                "bps": round(move_bps, 4),
            })
        
        paths_data[event_date] = path_points
        
        # Compute window metrics
        for w in windows:
            # Pre-window: T-W to T
            pre_start = event_dt - timedelta(minutes=w)
            mask_pre = (ohlcv_df["ts_event"] >= pre_start - timedelta(minutes=1)) & \
                       (ohlcv_df["ts_event"] <= pre_start + timedelta(minutes=1))
            pre_prices = ohlcv_df.loc[mask_pre]
            
            if not pre_prices.empty:
                price_pre = float(pre_prices.iloc[-1]["close"])
                pre_move = price_at_event - price_pre
                pre_ticks = points_to_ticks(pre_move)
                jolts_df.at[idx, f"pre_move_ticks_{w}m"] = round(pre_ticks, 2)
            
            # Post-window: T to T+W
            post_end = event_dt + timedelta(minutes=w)
            mask_post = (ohlcv_df["ts_event"] >= post_end - timedelta(minutes=1)) & \
                        (ohlcv_df["ts_event"] <= post_end + timedelta(minutes=1))
            post_prices = ohlcv_df.loc[mask_post]
            
            if not post_prices.empty:
                price_post = float(post_prices.iloc[-1]["close"])
                post_move = price_post - price_at_event
                post_ticks = points_to_ticks(post_move)
                jolts_df.at[idx, f"post_move_ticks_{w}m"] = round(post_ticks, 2)
        
        # Precompute candlesticks for modal (resample to 5-min)
        candle_start = event_dt - timedelta(days=2)
        candle_end = event_dt + timedelta(days=2)
        mask_candle = (ohlcv_df["ts_event"] >= candle_start) & (ohlcv_df["ts_event"] <= candle_end)
        candle_df = ohlcv_df.loc[mask_candle].copy()
        
        if not candle_df.empty:
            candle_df = candle_df.set_index("ts_event")
            candle_5m = candle_df.resample("5min").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()
            
            candles: List[Dict[str, Any]] = []
            for ts, crow in candle_5m.iterrows():
                candles.append({
                    "ts": ts.isoformat(),
                    "open": float(crow["open"]),
                    "high": float(crow["high"]),
                    "low": float(crow["low"]),
                    "close": float(crow["close"]),
                    "volume": float(crow["volume"]),
                })
            candles_data[event_date] = candles
    
    return jolts_df, paths_data, candles_data


def compute_distribution_stats(values: List[float], unit: str = "ticks") -> Dict[str, Any]:
    """Compute distribution statistics for a list of values."""
    if not values:
        return {"count": 0, "mean": 0, "median": 0, "p5": 0, "p95": 0, "min": 0, "max": 0}
    
    arr = np.array(values, dtype=float)
    pct = np.percentile(arr, [5, 25, 50, 75, 95])
    pos = arr[arr > 0]
    neg = arr[arr < 0]
    
    return {
        "count": int(len(arr)),
        "mean": round(float(arr.mean()), 3),
        "median": round(float(np.median(arr)), 3),
        "std": round(float(arr.std()), 3),
        "p5": round(float(pct[0]), 3),
        "p25": round(float(pct[1]), 3),
        "p50": round(float(pct[2]), 3),
        "p75": round(float(pct[3]), 3),
        "p95": round(float(pct[4]), 3),
        "min": round(float(arr.min()), 3),
        "max": round(float(arr.max()), 3),
        "rally_pct": round(len(pos) / len(arr) * 100, 1) if len(arr) > 0 else 0,
        "selloff_pct": round(len(neg) / len(arr) * 100, 1) if len(arr) > 0 else 0,
        "unit": unit,
    }


# =============================================================================
# HTML GENERATION
# =============================================================================

def generate_html(
    jolts_df: pd.DataFrame,
    paths_data: Dict[str, List[Dict[str, Any]]],
    candles_data: Dict[str, List[Dict[str, Any]]],
    windows: List[int],
    symbol: str,
    output_path: Path,
) -> None:
    """Generate the standalone HTML dashboard."""
    
    # Prepare events data for embedding
    events_list: List[Dict[str, Any]] = []
    for _, row in jolts_df.iterrows():
        event_date = row["date"]
        events_list.append({
            "date": event_date,
            "time": row["time"],
            "actual": float(row["actual"]) if pd.notna(row["actual"]) else None,
            "consensus": float(row["consensus"]) if pd.notna(row["consensus"]) else None,
            "surprise": float(row["surprise"]) if pd.notna(row["surprise"]) else None,
            "cons_std": float(row["consensus_std_dev"]) if pd.notna(row.get("consensus_std_dev")) else None,
            "cons_std_bucket": row.get("cons_std_bucket", "N/A"),
            "jolts_mode": row["jolts_mode"],
            "ism_surprise_prev": float(row["ism_surprise_prev"]) if pd.notna(row["ism_surprise_prev"]) else None,
            **{f"pre_move_ticks_{w}m": float(row[f"pre_move_ticks_{w}m"]) if pd.notna(row[f"pre_move_ticks_{w}m"]) else None for w in windows},
            **{f"post_move_ticks_{w}m": float(row[f"post_move_ticks_{w}m"]) if pd.notna(row[f"post_move_ticks_{w}m"]) else None for w in windows},
        })
    
    # Compute distribution stats for each mode/window
    distro_stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for mode in ["standalone", "with_ism", "all"]:
        distro_stats[mode] = {}
        if mode == "all":
            mode_df = jolts_df
        else:
            mode_df = jolts_df[jolts_df["jolts_mode"] == mode]
        
        for w in windows:
            col = f"pre_move_ticks_{w}m"
            values = mode_df[col].dropna().tolist()
            distro_stats[mode][f"{w}m"] = compute_distribution_stats(values, "ticks")
    
    # Metadata (ensure all values are JSON-serializable Python native types)
    meta = {
        "symbol": symbol,
        "generated_at": datetime.now().isoformat(),
        "total_events": int(len(jolts_df)),
        "with_ism_count": int(len(jolts_df[jolts_df["jolts_mode"] == "with_ism"])),
        "standalone_count": int(len(jolts_df[jolts_df["jolts_mode"] == "standalone"])),
        "windows": windows,
        "cons_std_available": bool(jolts_df["consensus_std_dev"].notna().sum() > 0),
    }
    
    # Build the embedded data blob
    data_blob = {
        "events": events_list,
        "paths": paths_data,
        "candles": candles_data,
        "distro_stats": distro_stats,
        "meta": meta,
    }
    
    html_content = _build_html_template(data_blob, symbol, windows)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    logger.info(f"Dashboard saved to: {output_path}")


def _build_html_template(data_blob: Dict[str, Any], symbol: str, windows: List[int]) -> str:
    """Build the complete HTML template."""
    
    data_json = json.dumps(data_blob, indent=None, separators=(',', ':'))
    windows_json = json.dumps(windows)
    
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JOLTS ↔ ISM Event Study Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        :root {{
            --bg-primary: #0f1419;
            --bg-secondary: #1a2332;
            --bg-tertiary: #243044;
            --text-primary: #e7e9ea;
            --text-secondary: #8b98a5;
            --accent-blue: #1d9bf0;
            --accent-green: #00ba7c;
            --accent-orange: #ff7a00;
            --accent-red: #f4212e;
            --accent-purple: #8b5cf6;
            --border-color: #2f3336;
        }}
        
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        
        body {{
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.5;
            min-height: 100vh;
        }}
        
        .container {{ max-width: 1600px; margin: 0 auto; padding: 20px; }}
        
        header {{
            text-align: center;
            padding: 30px 0;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 30px;
        }}
        
        header h1 {{
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, var(--accent-purple), var(--accent-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        header p {{ color: var(--text-secondary); font-size: 0.95rem; }}
        
        .controls {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            padding: 25px;
            background: var(--bg-secondary);
            border-radius: 12px;
            margin-bottom: 30px;
            border: 1px solid var(--border-color);
        }}
        
        .control-group {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        
        .control-group label {{
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .control-group select, .control-group input {{
            padding: 12px 16px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 1rem;
            width: 100%;
        }}
        
        .control-group select:focus, .control-group input:focus {{
            outline: none;
            border-color: var(--accent-blue);
        }}
        
        .toggle-group {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        
        .toggle-btn {{
            padding: 10px 16px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        .toggle-btn:hover {{ border-color: var(--accent-blue); }}
        .toggle-btn.active {{
            background: var(--accent-purple);
            color: white;
            border-color: var(--accent-purple);
        }}
        
        .charts-container {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 30px;
        }}
        
        .chart-panel {{
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border-color);
        }}
        
        .chart-panel h2 {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 15px;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}
        
        .badge {{
            background: var(--accent-purple);
            color: white;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        
        .badge.standalone {{ background: var(--accent-blue); }}
        .badge.with_ism {{ background: var(--accent-orange); }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 15px;
            margin-top: 15px;
            padding: 15px;
            background: var(--bg-tertiary);
            border-radius: 8px;
        }}
        
        .stat-item {{
            text-align: center;
        }}
        
        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            text-transform: uppercase;
        }}
        
        .stat-value {{
            font-size: 1.2rem;
            font-weight: 700;
            color: var(--text-primary);
        }}
        
        .event-details {{
            padding: 20px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            margin-top: 20px;
            display: none;
        }}
        
        .event-details.visible {{ display: block; }}
        
        .event-details h3 {{
            font-size: 1rem;
            margin-bottom: 15px;
            color: var(--accent-purple);
        }}
        
        /* Modal for candlestick */
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
        }}
        
        .modal.visible {{ display: flex; align-items: center; justify-content: center; }}
        
        .modal-content {{
            background: var(--bg-secondary);
            padding: 30px;
            border-radius: 16px;
            max-width: 1200px;
            width: 95%;
            max-height: 90vh;
            overflow-y: auto;
            border: 1px solid var(--border-color);
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}
        
        .modal-close {{
            font-size: 1.5rem;
            cursor: pointer;
            color: var(--text-secondary);
            background: none;
            border: none;
        }}
        
        .modal-close:hover {{ color: var(--text-primary); }}
        
        .distro-section {{
            margin-top: 40px;
        }}
        
        .distro-section h2 {{
            font-size: 1.3rem;
            margin-bottom: 20px;
            color: var(--text-primary);
        }}
        
        .distro-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
        }}
        
        .warning-banner {{
            background: var(--accent-orange);
            color: white;
            padding: 10px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }}
        
        .warning-banner.visible {{ display: block; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>JOLTS ↔ ISM Event Study</h1>
            <p>{symbol} price behavior around JOLTS releases, comparing standalone vs post-ISM days</p>
            <p style="margin-top: 8px; font-size: 0.85rem;">
                <span class="badge with_ism">With ISM: <span id="withIsmCount">--</span></span>
                <span class="badge standalone">Standalone: <span id="standaloneCount">--</span></span>
                <span class="badge">Total: <span id="totalCount">--</span></span>
            </p>
        </header>
        
        <div id="consStdWarning" class="warning-banner">
            ⚠️ JOLTS consensus std dev data not available. Cons-std bucket view is disabled.
        </div>
        
        <div class="controls">
            <div class="control-group">
                <label>JOLTS Mode</label>
                <div class="toggle-group" id="modeToggle">
                    <button class="toggle-btn active" data-mode="all">Both</button>
                    <button class="toggle-btn" data-mode="standalone">Standalone</button>
                    <button class="toggle-btn" data-mode="with_ism">With ISM</button>
                </div>
            </div>
            
            <div class="control-group">
                <label>Pre-Window</label>
                <div class="toggle-group" id="windowToggle">
                    <button class="toggle-btn" data-window="30">30m</button>
                    <button class="toggle-btn active" data-window="60">1h</button>
                    <button class="toggle-btn" data-window="120">2h</button>
                    <button class="toggle-btn" data-window="240">4h</button>
                </div>
            </div>
            
            <div class="control-group">
                <label>Unit</label>
                <div class="toggle-group" id="unitToggle">
                    <button class="toggle-btn active" data-unit="ticks">Ticks</button>
                    <button class="toggle-btn" data-unit="bps">BPS</button>
                </div>
            </div>
            
            <div class="control-group">
                <label>Binning Basis</label>
                <div class="toggle-group" id="binToggle">
                    <button class="toggle-btn active" data-bin="ism_surprise">ISM Surprise</button>
                    <button class="toggle-btn" data-bin="cons_std" id="consStdBinBtn">JOLTS Cons-Std</button>
                </div>
            </div>
            
            <div class="control-group">
                <label>Options</label>
                <div style="display: flex; align-items: center; gap: 10px; padding-top: 8px;">
                    <input type="checkbox" id="includeOutliers" checked>
                    <span style="color: var(--text-secondary);">Include outliers</span>
                </div>
            </div>
        </div>
        
        <!-- Price Paths Panel -->
        <div class="charts-container">
            <div class="chart-panel">
                <h2>
                    Normalized Price Paths
                    <span class="badge" id="pathsBadge">-- events</span>
                    <span style="margin-left: auto; font-size: 0.85rem; color: var(--text-secondary);">
                        Click a path to see event details
                    </span>
                </h2>
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-label">Mean Move</div>
                        <div class="stat-value" id="meanMove">--</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Median Move</div>
                        <div class="stat-value" id="medianMove">--</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Std Dev</div>
                        <div class="stat-value" id="stdMove">--</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Rally %</div>
                        <div class="stat-value" id="rallyPct">--</div>
                    </div>
                </div>
                <div id="pathsChart" style="height: 500px;"></div>
                <div class="event-details" id="eventDetails">
                    <h3>Selected Event Details</h3>
                    <div id="eventDetailsContent">Click on a price path to see details</div>
                </div>
            </div>
            
            <!-- (Removed) Average Event-Time Profile Panel -->
        </div>
        
        <!-- Distribution Section -->
        <div class="distro-section">
            <h2>Distribution Analysis</h2>
            <div class="distro-grid">
                <div class="chart-panel">
                    <h2>Pre-Window Return Distribution</h2>
                    <div id="distroChart" style="height: 350px;"></div>
                    <div class="stats-grid" id="distroStats"></div>
                </div>
                <div class="chart-panel">
                    <h2>Binned Analysis (<span id="binBasisLabel">ISM Surprise</span>)</h2>
                    <div id="binsChart" style="height: 350px;"></div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Candlestick Modal -->
    <div class="modal" id="candleModal">
        <div class="modal-content">
            <div class="modal-header">
                <h2 id="modalTitle">Candlestick Chart</h2>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div id="candleChart" style="height: 500px;"></div>
        </div>
    </div>
    
    <script>
        // Embedded data
        const DATA = {data_json};
        const WINDOWS = {windows_json};
        
        // State
        let currentMode = 'all';
        let currentWindow = 60;
        let currentUnit = 'ticks';
        let currentBinBasis = 'ism_surprise';
        let includeOutliers = true;
        let selectedEvent = null;
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {{
            // Update counts
            document.getElementById('totalCount').textContent = DATA.meta.total_events;
            document.getElementById('withIsmCount').textContent = DATA.meta.with_ism_count;
            document.getElementById('standaloneCount').textContent = DATA.meta.standalone_count;
            
            // Check cons-std availability
            if (!DATA.meta.cons_std_available) {{
                document.getElementById('consStdWarning').classList.add('visible');
                document.getElementById('consStdBinBtn').disabled = true;
                document.getElementById('consStdBinBtn').style.opacity = 0.5;
            }}
            
            // Setup toggle handlers
            setupToggle('modeToggle', 'mode', (val) => {{ currentMode = val; updateDashboard(); }});
            setupToggle('windowToggle', 'window', (val) => {{ currentWindow = parseInt(val); updateDashboard(); }});
            setupToggle('unitToggle', 'unit', (val) => {{ currentUnit = val; updateDashboard(); }});
            setupToggle('binToggle', 'bin', (val) => {{ currentBinBasis = val; updateDashboard(); }});
            
            document.getElementById('includeOutliers').addEventListener('change', function() {{
                includeOutliers = this.checked;
                updateDashboard();
            }});
            
            updateDashboard();
        }});
        
        function setupToggle(containerId, dataAttr, callback) {{
            const container = document.getElementById(containerId);
            const buttons = container.querySelectorAll('.toggle-btn');
            buttons.forEach(btn => {{
                btn.addEventListener('click', function() {{
                    if (this.disabled) return;
                    buttons.forEach(b => b.classList.remove('active'));
                    this.classList.add('active');
                    callback(this.dataset[dataAttr]);
                }});
            }});
        }}
        
        function updateDashboard() {{
            renderPaths();
            renderDistribution();
            renderBins();
        }}
        
        function getFilteredEvents() {{
            let events = DATA.events;
            if (currentMode !== 'all') {{
                events = events.filter(e => e.jolts_mode === currentMode);
            }}
            return events;
        }}
        
        function convertValue(ticks) {{
            if (currentUnit === 'bps') {{
                return ticks / 4;  // 4 ticks per bp
            }}
            return ticks;
        }}
        
        function formatValue(val) {{
            if (val === null || val === undefined) return '--';
            const converted = convertValue(val);
            const unit = currentUnit === 'bps' ? ' bps' : ' ticks';
            return converted.toFixed(2) + unit;
        }}
        
        function renderPaths() {{
            const events = getFilteredEvents();
            const windowKey = currentWindow + 'm';
            const moveCol = 'pre_move_ticks_' + windowKey;
            
            // Filter out events without path data
            const eventsWithPaths = events.filter(e => DATA.paths[e.date]);
            
            // Update badge
            document.getElementById('pathsBadge').textContent = eventsWithPaths.length + ' events';
            
            // Collect end-of-window moves for stats
            const endMoves = eventsWithPaths.map(e => e[moveCol]).filter(v => v !== null);
            
            // Update stats
            if (endMoves.length > 0) {{
                const mean = endMoves.reduce((a, b) => a + b, 0) / endMoves.length;
                const sorted = [...endMoves].sort((a, b) => a - b);
                const median = sorted[Math.floor(sorted.length / 2)];
                const variance = endMoves.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / endMoves.length;
                const std = Math.sqrt(variance);
                const rallies = endMoves.filter(v => v > 0).length;
                
                document.getElementById('meanMove').textContent = formatValue(mean);
                document.getElementById('medianMove').textContent = formatValue(median);
                document.getElementById('stdMove').textContent = formatValue(std);
                document.getElementById('rallyPct').textContent = (rallies / endMoves.length * 100).toFixed(1) + '%';
            }}
            
            // Build traces
            const traces = [];
            const colors = {{
                'standalone': 'rgba(29, 155, 240, 0.4)',
                'with_ism': 'rgba(255, 122, 0, 0.4)'
            }};
            
            eventsWithPaths.forEach(event => {{
                const path = DATA.paths[event.date];
                if (!path) return;
                
                // Filter to current window
                const windowPath = path.filter(p => p.rel_min >= -currentWindow && p.rel_min <= currentWindow);
                if (windowPath.length < 5) return;
                
                // Re-normalize to window start
                const startVal = windowPath.find(p => p.rel_min === -currentWindow);
                const startTicks = startVal ? startVal.ticks : windowPath[0].ticks;
                
                const x = windowPath.map(p => p.rel_min);
                const y = windowPath.map(p => convertValue(p.ticks - startTicks));
                
                traces.push({{
                    x: x,
                    y: y,
                    mode: 'lines',
                    name: event.date,
                    line: {{
                        width: 1.5,
                        color: colors[event.jolts_mode] || 'rgba(139, 92, 246, 0.4)'
                    }},
                    hoverinfo: 'text',
                    text: x.map((xv, i) => `${{event.date}}<br>T${{xv >= 0 ? '+' : ''}}${{xv}}min<br>${{formatValue(y[i] * (currentUnit === 'bps' ? 4 : 1))}}`),
                    customdata: [event.date],
                }});
            }});
            
            const layout = {{
                paper_bgcolor: '#1a2332',
                plot_bgcolor: '#1a2332',
                font: {{ color: '#e7e9ea' }},
                showlegend: false,
                xaxis: {{
                    title: 'Minutes from JOLTS Release',
                    gridcolor: '#2f3336',
                    zerolinecolor: '#ef4444',
                    zerolinewidth: 2,
                }},
                yaxis: {{
                    title: currentUnit === 'bps' ? 'Move (bps)' : 'Move (ticks)',
                    gridcolor: '#2f3336',
                    zerolinecolor: '#2f3336',
                }},
                hovermode: 'closest',
                margin: {{ t: 20, r: 20, b: 50, l: 60 }},
            }};
            
            Plotly.newPlot(
                'pathsChart',
                traces,
                layout,
                {{
                    responsive: true,
                    scrollZoom: true,
                    displayModeBar: true,
                    displaylogo: false
                }}
            );
            
            // Click handler
            document.getElementById('pathsChart').on('plotly_click', function(data) {{
                if (data.points.length > 0) {{
                    const eventDate = data.points[0].data.name;
                    showEventDetails(eventDate);
                }}
            }});
        }}
        
        function showEventDetails(eventDate) {{
            const event = DATA.events.find(e => e.date === eventDate);
            if (!event) return;
            
            selectedEvent = event;
            const details = document.getElementById('eventDetails');
            const content = document.getElementById('eventDetailsContent');
            
            const windowKey = currentWindow + 'm';
            const preMoveCol = 'pre_move_ticks_' + windowKey;
            const postMoveCol = 'post_move_ticks_' + windowKey;
            
            content.innerHTML = `
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-label">Date</div>
                        <div class="stat-value">${{event.date}}</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Time</div>
                        <div class="stat-value">${{event.time}}</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Mode</div>
                        <div class="stat-value"><span class="badge ${{event.jolts_mode}}">${{event.jolts_mode}}</span></div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Actual</div>
                        <div class="stat-value">${{event.actual !== null ? (event.actual / 1000).toFixed(0) + 'K' : '--'}}</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Surprise</div>
                        <div class="stat-value" style="color: ${{event.surprise > 0 ? 'var(--accent-green)' : 'var(--accent-red)'}}">${{event.surprise !== null ? (event.surprise / 1000).toFixed(0) + 'K' : '--'}}</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Pre-Move</div>
                        <div class="stat-value">${{formatValue(event[preMoveCol])}}</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">ISM Surp (prev)</div>
                        <div class="stat-value">${{event.ism_surprise_prev !== null ? event.ism_surprise_prev.toFixed(1) : 'N/A'}}</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label"></div>
                        <button class="toggle-btn" style="background: var(--accent-purple); color: white;" onclick="showCandlestick('${{event.date}}')">View Candlestick</button>
                    </div>
                </div>
            `;
            
            details.classList.add('visible');
        }}
        
        function showCandlestick(eventDate) {{
            const candles = DATA.candles[eventDate];
            if (!candles || candles.length === 0) {{
                alert('No candlestick data available for this event');
                return;
            }}
            
            const event = DATA.events.find(e => e.date === eventDate);
            document.getElementById('modalTitle').textContent = `${{eventDate}} - JOLTS (${{event.jolts_mode}})`;
            
            const trace = {{
                x: candles.map(c => c.ts),
                open: candles.map(c => c.open),
                high: candles.map(c => c.high),
                low: candles.map(c => c.low),
                close: candles.map(c => c.close),
                type: 'candlestick',
                increasing: {{ line: {{ color: '#00ba7c' }} }},
                decreasing: {{ line: {{ color: '#f4212e' }} }},
            }};
            
            const layout = {{
                paper_bgcolor: '#1a2332',
                plot_bgcolor: '#1a2332',
                font: {{ color: '#e7e9ea' }},
                xaxis: {{
                    gridcolor: '#2f3336',
                    rangeslider: {{ visible: false }},
                }},
                yaxis: {{
                    title: 'Price',
                    gridcolor: '#2f3336',
                }},
                margin: {{ t: 20, r: 20, b: 50, l: 60 }},
                shapes: [{{
                    type: 'line',
                    x0: eventDate + 'T10:00:00',
                    x1: eventDate + 'T10:00:00',
                    y0: 0,
                    y1: 1,
                    yref: 'paper',
                    line: {{ color: '#8b5cf6', width: 2, dash: 'dot' }},
                }}],
            }};
            
            Plotly.newPlot('candleChart', [trace], layout, {{responsive: true}});
            document.getElementById('candleModal').classList.add('visible');
        }}
        
        function closeModal() {{
            document.getElementById('candleModal').classList.remove('visible');
        }}
        
        // Close modal on escape key
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeModal();
        }});
        
        function renderDistribution() {{
            const events = getFilteredEvents();
            const windowKey = currentWindow + 'm';
            const moveCol = 'pre_move_ticks_' + windowKey;
            
            let values = events.map(e => e[moveCol]).filter(v => v !== null);
            
            // Apply outlier filter
            if (!includeOutliers && values.length > 5) {{
                const sorted = [...values].sort((a, b) => a - b);
                const p5 = sorted[Math.floor(sorted.length * 0.05)];
                const p95 = sorted[Math.floor(sorted.length * 0.95)];
                values = values.filter(v => v >= p5 && v <= p95);
            }}
            
            // Convert to current unit
            const convertedValues = values.map(v => convertValue(v));
            
            const trace = {{
                x: convertedValues,
                type: 'histogram',
                marker: {{ color: 'rgba(139, 92, 246, 0.7)' }},
                nbinsx: 20,
            }};
            
            const layout = {{
                paper_bgcolor: '#1a2332',
                plot_bgcolor: '#1a2332',
                font: {{ color: '#e7e9ea' }},
                xaxis: {{
                    title: currentUnit === 'bps' ? 'Pre-Window Move (bps)' : 'Pre-Window Move (ticks)',
                    gridcolor: '#2f3336',
                    zerolinecolor: '#ef4444',
                    zerolinewidth: 2,
                }},
                yaxis: {{
                    title: 'Count',
                    gridcolor: '#2f3336',
                }},
                bargap: 0.05,
                margin: {{ t: 20, r: 20, b: 50, l: 50 }},
            }};
            
            Plotly.newPlot('distroChart', [trace], layout, {{responsive: true}});
            
            // Update stats
            if (values.length > 0) {{
                const mean = values.reduce((a, b) => a + b, 0) / values.length;
                const sorted = [...values].sort((a, b) => a - b);
                const median = sorted[Math.floor(sorted.length / 2)];
                const p5 = sorted[Math.floor(sorted.length * 0.05)];
                const p95 = sorted[Math.floor(sorted.length * 0.95)];
                
                document.getElementById('distroStats').innerHTML = `
                    <div class="stat-item"><div class="stat-label">N</div><div class="stat-value">${{values.length}}</div></div>
                    <div class="stat-item"><div class="stat-label">Mean</div><div class="stat-value">${{formatValue(mean)}}</div></div>
                    <div class="stat-item"><div class="stat-label">Median</div><div class="stat-value">${{formatValue(median)}}</div></div>
                    <div class="stat-item"><div class="stat-label">P5</div><div class="stat-value">${{formatValue(p5)}}</div></div>
                    <div class="stat-item"><div class="stat-label">P95</div><div class="stat-value">${{formatValue(p95)}}</div></div>
                `;
            }}
        }}
        
        function renderBins() {{
            const events = getFilteredEvents();
            const windowKey = currentWindow + 'm';
            const moveCol = 'pre_move_ticks_' + windowKey;
            
            document.getElementById('binBasisLabel').textContent = 
                currentBinBasis === 'ism_surprise' ? 'ISM Surprise (prev day)' : 'JOLTS Cons-Std';
            
            // Group by bins
            let bins = {{}};
            
            if (currentBinBasis === 'ism_surprise') {{
                // Bin by ISM surprise terciles
                const withIsm = events.filter(e => e.jolts_mode === 'with_ism' && e.ism_surprise_prev !== null);
                const surprises = withIsm.map(e => e.ism_surprise_prev).sort((a, b) => a - b);
                
                if (surprises.length >= 3) {{
                    const t1 = surprises[Math.floor(surprises.length / 3)];
                    const t2 = surprises[Math.floor(surprises.length * 2 / 3)];
                    
                    const lowKey = 'Low (ISM < ' + t1.toFixed(1) + ')';
                    const highKey = 'High (ISM > ' + t2.toFixed(1) + ')';
                    bins[lowKey] = [];
                    bins['Medium'] = [];
                    bins[highKey] = [];
                    
                    withIsm.forEach(e => {{
                        const move = e[moveCol];
                        if (move === null) return;
                        if (e.ism_surprise_prev < t1) bins[lowKey].push(move);
                        else if (e.ism_surprise_prev > t2) bins[highKey].push(move);
                        else bins['Medium'].push(move);
                    }});
                }}
            }} else {{
                // Bin by cons-std bucket
                bins = {{ 'Low': [], 'Medium': [], 'High': [], 'N/A': [] }};
                events.forEach(e => {{
                    const move = e[moveCol];
                    if (move === null) return;
                    const bucket = e.cons_std_bucket || 'N/A';
                    if (bins[bucket]) bins[bucket].push(move);
                }});
            }}
            
            // Build box plot traces
            const traces = Object.entries(bins).filter(([k, v]) => v.length > 0).map(([name, values]) => ({{
                y: values.map(v => convertValue(v)),
                type: 'box',
                name: name,
                boxpoints: 'outliers',
                marker: {{ color: '#8b5cf6' }},
            }}));
            
            const layout = {{
                paper_bgcolor: '#1a2332',
                plot_bgcolor: '#1a2332',
                font: {{ color: '#e7e9ea' }},
                showlegend: false,
                yaxis: {{
                    title: currentUnit === 'bps' ? 'Pre-Move (bps)' : 'Pre-Move (ticks)',
                    gridcolor: '#2f3336',
                    zerolinecolor: '#2f3336',
                }},
                margin: {{ t: 20, r: 20, b: 80, l: 60 }},
            }};
            
            Plotly.newPlot('binsChart', traces, layout, {{responsive: true}});
        }}
    </script>
</body>
</html>
'''


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="JOLTS ↔ ISM Event Study Dashboard Generator")
    parser.add_argument("--symbol", default="TY", help="Symbol to analyze (default: TY)")
    parser.add_argument("--start-date", default="2022-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="End date (YYYY-MM-DD, default: today)")
    parser.add_argument("--windows", default="30,60,120,240", help="Pre-window lengths in minutes")
    parser.add_argument("--out", default=None, help="Output HTML path (default: workspace/scenarios/)")
    args = parser.parse_args()
    
    windows = [int(w.strip()) for w in args.windows.split(",")]
    
    # Load events
    logger.info("Loading JOLTS events...")
    jolts_df = load_events_by_name(JOLTS_EVENT_NAME, args.start_date, args.end_date)
    if jolts_df.empty:
        logger.error(f"No JOLTS events found. Check event name: '{JOLTS_EVENT_NAME}'")
        return 1
    logger.info(f"Found {len(jolts_df)} JOLTS events")
    
    logger.info("Loading ISM events...")
    ism_df = load_events_by_name(ISM_EVENT_NAME, args.start_date, args.end_date)
    logger.info(f"Found {len(ism_df)} ISM events")
    
    # Label JOLTS events
    logger.info("Labeling JOLTS events (with_ism vs standalone)...")
    jolts_df = label_jolts_events(jolts_df, ism_df)
    
    with_ism_count = len(jolts_df[jolts_df["jolts_mode"] == "with_ism"])
    standalone_count = len(jolts_df[jolts_df["jolts_mode"] == "standalone"])
    logger.info(f"  with_ism: {with_ism_count}, standalone: {standalone_count}")
    
    # Compute cons-std buckets
    logger.info("Computing cons-std buckets...")
    jolts_df = compute_cons_std_buckets(jolts_df)
    
    # Compute window metrics and paths
    logger.info("Computing window metrics and price paths...")
    jolts_df, paths_data, candles_data = compute_window_metrics(jolts_df, args.symbol, windows)
    logger.info(f"Computed paths for {len(paths_data)} events")
    
    # Generate output path
    if args.out:
        output_path = Path(args.out)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = PROJECT_ROOT / "workspace" / "scenarios" / f"jolts_event_study_dashboard_{timestamp}.html"
    
    # Generate HTML
    logger.info("Generating HTML dashboard...")
    generate_html(jolts_df, paths_data, candles_data, windows, args.symbol, output_path)
    
    logger.info("Done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

