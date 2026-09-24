#!/usr/bin/env python3
"""
PPI Post-Event Dashboard Generator
-----------------------------------
Generates a standalone interactive HTML dashboard for PPI Core MoM analysis.

Features:
- Top panel: Actual, Consensus, Cons Std Dev, FWD breakeven inputs
- Surprise percentile bucketed boxplot with current bucket highlighted
- Normalized price paths with zoom/pan re-normalization
- Click-to-candlestick modal with ±2 days (expandable)
- Distribution explorer with fine-grained time presets
- Stats in bps with 0 bps percentile rank

Usage:
    python scripts/ppi_postevent_dashboard.py
    python scripts/ppi_postevent_dashboard.py --actual 0.3 --consensus 0.2 --cons-std 0.1 --fwd-be 8.0
    python scripts/ppi_postevent_dashboard.py --serve  # Start live server
"""

import argparse
import csv
import json
import sqlite3
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rules.treasury_price_conversion import points_to_bps

# Configuration
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "scenarios"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PPI_WEEKS_CSV = PROJECT_ROOT / "ppi_weeks_results.csv"


def load_fwd_be_from_csv() -> Dict[str, float]:
    """Load FWD breakeven values from ppi_weeks_results.csv.
    
    Uses IMPLIED_VOL from BF (before event) rows as the forward breakeven.
    BF row date is typically the day before the event, so we map to the next day.
    Also looks at AF rows for events where BF is not available.
    
    Returns dict mapping event_date (YYYY-MM-DD) -> FWD_BE value.
    """
    fwd_be_map = {}
    bf_rows = {}  # date -> implied_vol from BF rows
    af_dates = []  # list of AF row dates
    
    if not PPI_WEEKS_CSV.exists():
        print(f"Warning: CSV not found at {PPI_WEEKS_CSV}")
        return fwd_be_map
    
    with open(PPI_WEEKS_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # First pass: collect BF rows with IMPLIED_VOL and AF dates
    for row in rows:
        if row.get('EVENT') != 'PPI':
            continue
        date_str = row.get('DATE', '').strip()
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str, '%m/%d/%Y')
        except ValueError:
            continue
        
        if row.get('TIME') == 'BF':
            impl_vol_str = row.get('IMPLIED_VOL', '').strip()
            if impl_vol_str:
                try:
                    bf_rows[dt.strftime('%Y-%m-%d')] = float(impl_vol_str)
                except ValueError:
                    pass
        elif row.get('TIME') == 'AF':
            af_dates.append(dt.strftime('%Y-%m-%d'))
    
    # Second pass: match BF implied vol to AF event dates
    # BF date is typically 1 day before AF date
    for af_date in af_dates:
        af_dt = datetime.strptime(af_date, '%Y-%m-%d')
        # Check day before
        bf_date = (af_dt - timedelta(days=1)).strftime('%Y-%m-%d')
        if bf_date in bf_rows:
            fwd_be_map[af_date] = bf_rows[bf_date]
        # Also check 2-3 days before (for weekends)
        elif (af_dt - timedelta(days=3)).strftime('%Y-%m-%d') in bf_rows:
            fwd_be_map[af_date] = bf_rows[(af_dt - timedelta(days=3)).strftime('%Y-%m-%d')]
    
    print(f"Loaded {len(fwd_be_map)} FWD_BE values from CSV (using IMPLIED_VOL from BF rows)")
    return fwd_be_map

# PPI Configuration
PPI_CONFIG = {
    "db_event_name": "Core PPI MoM",
    "display_name": "Core PPI MoM",
    "short_name": "PPI",
    "release_time": "08:30",
    "unit": "%",
    "default_std": 0.1,
    "default_fwd": 8.0,
    "post_event_end_time": "17:00",
}


def load_ppi_events() -> List[Dict]:
    """Load historical PPI events from economic_events.db."""
    if not ECONOMIC_EVENTS_DB.exists():
        print(f"Warning: Database not found at {ECONOMIC_EVENTS_DB}")
        return []
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT event_date, actual, consensus, previous
        FROM economic_events
        WHERE event_name = ?
          AND country LIKE '%United States%'
          AND actual IS NOT NULL
          AND consensus IS NOT NULL
          AND event_date >= '2021-01-01'
        ORDER BY event_date DESC
    """, (PPI_CONFIG["db_event_name"],))
    
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        event_date = row['event_date']
        if 'T' in str(event_date):
            date_str = str(event_date).split('T')[0]
        else:
            date_str = str(event_date)[:10]
        
        events.append({
            'date': date_str,
            'actual': float(row['actual']) if row['actual'] else None,
            'consensus': float(row['consensus']) if row['consensus'] else None,
            'previous': float(row['previous']) if row['previous'] else None,
        })
    
    print(f"Loaded {len(events)} PPI events from database")
    return events


def compute_surprise_percentiles(events: List[Dict], cons_std: float) -> List[Dict]:
    """Compute standardized surprise and percentile for each event."""
    # Calculate standardized surprises
    std_surprises = []
    for evt in events:
        if evt['actual'] is not None and evt['consensus'] is not None:
            surprise = evt['actual'] - evt['consensus']
            std_surprise = surprise / cons_std if cons_std > 0 else 0
            evt['surprise'] = surprise
            evt['standardized_surprise'] = std_surprise
            std_surprises.append(std_surprise)
        else:
            evt['surprise'] = None
            evt['standardized_surprise'] = None
    
    # Compute percentile ranks
    sorted_surprises = sorted([s for s in std_surprises if s is not None])
    n = len(sorted_surprises)
    
    for evt in events:
        if evt['standardized_surprise'] is not None and n > 0:
            count_below = sum(1 for s in sorted_surprises if s < evt['standardized_surprise'])
            evt['surp_pct'] = count_below / n
        else:
            evt['surp_pct'] = None
    
    return events


def fetch_postevent_prices(event_date: str) -> Optional[List[Dict]]:
    """Fetch post-event prices from 8:30am to 5pm on event day."""
    try:
        from src.servers.marketdata.run_query import run_query
        
        # Query prices from 8:30am to 5pm
        result = run_query(
            template="custom",
            columns=["timestamp", "open", "high", "low", "close"],
            params={
                "conditions": (
                    f"symbol = 'TY' AND "
                    f"timestamp >= '{event_date} 08:30:00' AND "
                    f"timestamp <= '{event_date} 17:00:00' AND "
                    f"close >= 80 AND close <= 150"
                )
            },
            limit=600
        )
        
        rows = result.get("data") or result.get("rows") or []
        if not rows:
            return None
        
        prices = []
        for row in rows:
            ts = row.get('timestamp')
            close = row.get('close')
            if ts and close:
                prices.append({
                    'timestamp': str(ts),
                    'open': float(row.get('open', close)),
                    'high': float(row.get('high', close)),
                    'low': float(row.get('low', close)),
                    'close': float(close),
                })
        
        prices.sort(key=lambda x: x['timestamp'])
        return prices if prices else None
        
    except Exception as e:
        print(f"  Error fetching prices for {event_date}: {e}")
        return None


def fetch_candlestick_data(event_date: str, days_around: int = 2) -> Optional[List[Dict]]:
    """Fetch 5-minute candlestick data around the event date."""
    try:
        from src.servers.marketdata.run_query import run_query
        
        event_dt = datetime.strptime(event_date, "%Y-%m-%d")
        start_dt = event_dt - timedelta(days=days_around)
        end_dt = event_dt + timedelta(days=days_around)
        
        result = run_query(
            template="custom",
            columns=["timestamp", "open", "high", "low", "close", "volume"],
            params={
                "conditions": (
                    f"symbol = 'TY' AND "
                    f"timestamp >= '{start_dt.strftime('%Y-%m-%d')} 00:00:00' AND "
                    f"timestamp <= '{end_dt.strftime('%Y-%m-%d')} 23:59:59' AND "
                    f"close >= 80 AND close <= 150"
                )
            },
            limit=5000
        )
        
        rows = result.get("data") or result.get("rows") or []
        if not rows:
            return None
        
        # Aggregate to 5-minute bars (sorted by timestamp to preserve open/close)
        from collections import defaultdict
        bars = defaultdict(lambda: {'open': None, 'high': -999, 'low': 999, 'close': None, 'volume': 0})
        
        parsed_rows = []
        for row in rows:
            ts = row.get('timestamp')
            if not ts:
                continue
            ts_str = str(ts)
            if '+' in ts_str or ts_str.endswith('Z'):
                ts_str = ts_str.split('+')[0].split('-05:00')[0].split('-04:00')[0].rstrip('Z')
            try:
                if 'T' in ts_str:
                    dt = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
                else:
                    dt = datetime.strptime(ts_str[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            parsed_rows.append((dt, row))
        
        parsed_rows.sort(key=lambda x: x[0])
        
        for dt, row in parsed_rows:
            bucket = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
            bucket_key = bucket.isoformat()
            
            bar = bars[bucket_key]
            if bar['open'] is None:
                bar['open'] = float(row['open'])
            bar['high'] = max(bar['high'], float(row['high']))
            bar['low'] = min(bar['low'], float(row['low']))
            # Close should be last value in the sorted bucket
            bar['close'] = float(row['close'])
            bar['volume'] += float(row.get('volume', 0))
        
        candles = []
        for ts_key in sorted(bars.keys()):
            bar = bars[ts_key]
            candles.append({
                'ts': ts_key,
                'open': bar['open'],
                'high': bar['high'],
                'low': bar['low'],
                'close': bar['close'],
                'volume': bar['volume'],
            })
        
        return candles if candles else None
        
    except Exception as e:
        print(f"  Error fetching candlestick for {event_date}: {e}")
        return None


def get_other_events_on_date(event_date: str) -> List[Dict]:
    """Get other economic events on the same date."""
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT event_name, importance, actual, consensus, event_date
        FROM economic_events
        WHERE DATE(event_date) = ?
          AND event_name != ?
          AND country LIKE '%United States%'
          AND (importance = 'high' OR importance = 'medium')
        ORDER BY event_date ASC
    """, (event_date, PPI_CONFIG["db_event_name"]))
    
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        event_time = "08:30"
        if row['event_date'] and 'T' in str(row['event_date']):
            event_time = str(row['event_date']).split('T')[1][:5]
        
        events.append({
            'name': row['event_name'],
            'importance': row['importance'],
            'actual': row['actual'],
            'consensus': row['consensus'],
            'time': event_time,
        })
    
    return events


def build_dashboard_data(events: List[Dict], cons_std: float, fwd_be: float) -> Dict:
    """Build enriched event data for the dashboard."""
    enriched = []
    
    print(f"Processing {len(events)} PPI events...")
    
    for i, event in enumerate(events):
        if i % 10 == 0:
            print(f"  [{i+1}/{len(events)}] {event['date']}...")
        
        # Fetch post-event prices
        prices = fetch_postevent_prices(event['date'])
        if not prices or len(prices) < 5:
            continue
        
        # Normalize prices to bps from 8:30am
        ref_price = prices[0]['open']  # Use open of first bar as reference
        base_date = datetime(2000, 1, 1)  # Same as JS BASE_DATE
        event_date_dt = datetime.strptime(event['date'], '%Y-%m-%d')
        normalized = []
        for p in prices:
            move_bps = points_to_bps(p['close'] - ref_price)
            # Parse timestamp and normalize to BASE_DATE
            ts_str = str(p['timestamp'])
            # Handle various timestamp formats
            if '+' in ts_str or ts_str.endswith('Z'):
                # Remove timezone suffix
                ts_str = ts_str.split('+')[0].split('-05:00')[0].split('-04:00')[0].rstrip('Z')
            if 'T' in ts_str:
                ts_dt = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
            elif ' ' in ts_str:
                ts_dt = datetime.strptime(ts_str[:19], '%Y-%m-%d %H:%M:%S')
            else:
                continue  # Skip invalid timestamp
            # Normalize: keep only time-of-day, apply to BASE_DATE
            normalized_dt = base_date.replace(
                hour=ts_dt.hour, minute=ts_dt.minute, second=ts_dt.second
            )
            ts_ms = int(normalized_dt.timestamp() * 1000)
            normalized.append({
                'ts': ts_ms,
                'bps': round(move_bps, 4),
                'open': p['open'],
                'high': p['high'],
                'low': p['low'],
                'close': p['close'],
            })
        
        # Get FWD breakeven for this event (from CSV or default)
        event_fwd_be = event.get('fwd_be', fwd_be)
        
        # Calculate B/E multiplier: max(open-low, high-open) / fwd_be for full window
        first_open = prices[0]['open']
        high_price = max(p['high'] for p in prices)
        low_price = min(p['low'] for p in prices)
        up_move = abs(high_price - first_open)
        down_move = abs(first_open - low_price)
        max_move = max(up_move, down_move)
        move_bps = points_to_bps(max_move)
        be_multiplier = move_bps / event_fwd_be if event_fwd_be > 0 else 0
        
        # Fetch candlestick data
        candles = fetch_candlestick_data(event['date'], days_around=5)
        
        # Get other events on same day
        other_events = get_other_events_on_date(event['date'])
        
        enriched.append({
            'date': event['date'],
            'actual': event['actual'],
            'consensus': event['consensus'],
            'surprise': event.get('surprise'),
            'standardized_surprise': event.get('standardized_surprise'),
            'surp_pct': event.get('surp_pct'),
            'cons_std': cons_std,
            'fwd_be': event_fwd_be,  # From CSV or default
            'post_be': round(be_multiplier, 3),
            'postevent_prices': normalized,
            'candlestick_5m': candles or [],
            'other_events': other_events,
            'event_time': f"{event['date']}T08:30:00",
        })
    
    print(f"  Enriched {len(enriched)} events with price data")
    
    return {
        'events': enriched,
        'cons_std': cons_std,
        'fwd_be': fwd_be,
        'event_type': 'PPI',
        'display_name': PPI_CONFIG['display_name'],
        'unit': PPI_CONFIG['unit'],
        'generated_at': datetime.now().isoformat(),
    }


def generate_html(data: Dict, user_actual: Optional[float], user_consensus: Optional[float],
                  user_cons_std: float, user_fwd_be: float) -> str:
    """Generate the complete HTML dashboard."""
    
    events_json = json.dumps(data['events'], default=str)
    
    # Calculate user's surprise percentile if actual provided
    user_surp_pct = 50  # Default to middle
    if user_actual is not None and user_consensus is not None:
        user_surprise = user_actual - user_consensus
        user_std_surprise = user_surprise / user_cons_std if user_cons_std > 0 else 0
        
        # Count how many historical surprises are below user's
        all_std_surprises = [e.get('standardized_surprise') for e in data['events'] 
                           if e.get('standardized_surprise') is not None]
        if all_std_surprises:
            count_below = sum(1 for s in all_std_surprises if s < user_std_surprise)
            user_surp_pct = (count_below / len(all_std_surprises)) * 100
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PPI Core MoM Post-Event Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        :root {{
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --text-primary: #f0f6fc;
            --text-secondary: #8b949e;
            --border-color: #30363d;
            --accent-purple: #9b59b6;
            --accent-green: #22c55e;
            --accent-red: #ef4444;
            --accent-yellow: #fbbf24;
        }}
        
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 20px;
        }}
        
        .dashboard-header {{
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }}
        
        .dashboard-header h1 {{
            font-size: 2rem;
            margin-bottom: 10px;
            color: var(--accent-purple);
        }}
        
        .dashboard-header .subtitle {{
            color: var(--text-secondary);
            font-size: 0.95rem;
        }}
        
        .input-panel {{
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 30px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            align-items: end;
        }}
        
        .input-group {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        
        .input-group label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .input-group input {{
            padding: 12px 16px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 1rem;
        }}
        
        .input-group input:focus {{
            outline: none;
            border-color: var(--accent-purple);
        }}
        
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        .btn-primary {{
            background: var(--accent-purple);
            color: white;
        }}
        
        .btn-primary:hover {{
            opacity: 0.9;
            transform: translateY(-1px);
        }}
        
        .charts-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .chart-panel {{
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
        }}
        
        .chart-panel h2 {{
            font-size: 1.1rem;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .badge {{
            background: var(--accent-purple);
            color: white;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: normal;
        }}
        
        .stats-row {{
            display: flex;
            gap: 20px;
            margin-bottom: 15px;
        }}
        
        .stat-item {{
            background: var(--bg-tertiary);
            padding: 10px 15px;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        
        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        
        .stat-value {{
            font-size: 1.1rem;
            font-weight: 600;
        }}
        
        .bucket-info {{
            background: var(--bg-tertiary);
            border-left: 3px solid var(--accent-green);
            padding: 12px 15px;
            margin-top: 15px;
            border-radius: 0 8px 8px 0;
            font-size: 0.9rem;
        }}
        
        .distro-section {{
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            margin-top: 30px;
        }}
        
        .distro-presets {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 15px;
            align-items: center;
        }}
        
        .distro-presets span {{
            color: var(--text-secondary);
            margin-right: 10px;
        }}
        
        .preset-btn {{
            padding: 6px 12px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        .preset-btn:hover {{
            background: var(--bg-primary);
            border-color: var(--accent-purple);
        }}
        
        .preset-btn.active {{
            background: var(--accent-purple);
            color: white;
            border-color: var(--accent-purple);
        }}
        
        .distro-content {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
        }}
        
        .distro-stats {{
            background: var(--bg-tertiary);
            border-radius: 10px;
            padding: 20px;
        }}
        
        .distro-stats h4 {{
            color: var(--accent-purple);
            margin-bottom: 15px;
        }}
        
        .distro-stat-row {{
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
        }}
        
        .event-details {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 15px;
            margin-top: 15px;
            display: none;
        }}
        
        .event-details.visible {{
            display: block;
        }}
        
        .event-details h3 {{
            color: var(--accent-purple);
            margin-bottom: 10px;
        }}
        
        /* Modal */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }}
        
        .modal-overlay.visible {{
            display: flex;
        }}
        
        .modal-content {{
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 25px;
            width: 90%;
            max-width: 1400px;
            max-height: 90vh;
            overflow-y: auto;
            resize: both;
            overflow: auto;
            min-width: 600px;
            min-height: 400px;
        }}
        
        .modal-content.expanded {{
            width: 98%;
            max-width: 98%;
            height: 95vh;
            max-height: 95vh;
        }}
        
        .expand-btn {{
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            font-size: 18px;
            cursor: pointer;
            padding: 5px 12px;
            border-radius: 6px;
            transition: all 0.2s;
        }}
        
        .expand-btn:hover {{
            background: var(--accent-purple);
            border-color: var(--accent-purple);
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}
        
        .modal-close {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
        }}
        
        .modal-close:hover {{
            color: var(--text-primary);
        }}
        
        .duration-slider {{
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 20px;
        }}
        
        .duration-slider input {{
            flex: 1;
        }}
        
        .other-events {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 15px;
            margin-top: 15px;
        }}
        
        .other-events h4 {{
            color: var(--accent-yellow);
            margin-bottom: 10px;
            font-size: 0.9rem;
        }}
        
        @media (max-width: 1200px) {{
            .charts-grid {{
                grid-template-columns: 1fr;
            }}
            .distro-content {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="dashboard-header">
        <h1>📊 PPI Core MoM Post-Event Dashboard</h1>
        <p class="subtitle">Event Time → 5:00 PM ET | B/E Multiplier by Surprise Percentile</p>
    </div>
    
    <!-- Input Panel -->
    <div class="input-panel">
        <div class="input-group">
            <label>Actual (%)</label>
            <input type="number" id="actualInput" value="{user_actual or ''}" step="0.01" placeholder="e.g., 0.3">
        </div>
        <div class="input-group">
            <label>Consensus (%)</label>
            <input type="number" id="consensusInput" value="{user_consensus or ''}" step="0.01" placeholder="e.g., 0.2">
        </div>
        <div class="input-group">
            <label>Consensus Std Dev (%)</label>
            <input type="number" id="consStdInput" value="{user_cons_std}" step="0.01" min="0.01" max="0.5">
        </div>
        <div class="input-group">
            <label>FWD Breakeven (bps)</label>
            <input type="number" id="fwdBeInput" value="{user_fwd_be}" step="0.5" min="1" max="20">
        </div>
        <div class="input-group">
            <label>Bucket Width (%)</label>
            <input type="number" id="bucketWidthInput" value="25" step="5" min="10" max="50">
        </div>
        <div class="input-group" style="display: flex; flex-direction: column; gap: 8px;">
            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                <input type="checkbox" id="removeOutliersCheckbox" style="width: 18px; height: 18px; cursor: pointer;">
                <span>Remove Outliers (IQR)</span>
            </label>
        </div>
        <div class="input-group">
            <label>&nbsp;</label>
            <button class="btn btn-primary" onclick="updateDashboard()">Update Analysis</button>
        </div>
    </div>
    
    <!-- Time Window Slider -->
    <div class="input-row" style="margin-bottom: 20px; background: var(--bg-secondary); padding: 20px; border-radius: 12px;">
        <div style="display: flex; align-items: center; gap: 20px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 300px;">
                <label style="color: var(--text-secondary); margin-bottom: 8px; display: block;">
                    <strong>Analysis Window:</strong> 8:30 AM → <span id="windowEndLabel">5:00 PM</span>
                </label>
                <input type="range" id="windowEndSlider" min="30" max="510" value="510" step="15" 
                       style="width: 100%; cursor: pointer;"
                       oninput="updateWindowEnd()">
                <div style="display: flex; justify-content: space-between; color: var(--text-secondary); font-size: 11px; margin-top: 4px;">
                    <span>+30m</span>
                    <span>+2h</span>
                    <span>+4h</span>
                    <span>+6h</span>
                    <span>+8.5h (5pm)</span>
                </div>
            </div>
            <div style="text-align: center; padding: 10px 20px; background: var(--bg-tertiary); border-radius: 8px;">
                <div style="color: var(--text-secondary); font-size: 11px;">Window Duration</div>
                <div id="windowDuration" style="font-size: 24px; font-weight: bold; color: var(--accent-purple);">8h 30m</div>
            </div>
        </div>
    </div>
    
    <!-- Charts -->
    <div class="charts-grid">
        <div class="chart-panel">
            <h2>
                B/E Multiplier by Surprise Percentile
                <span class="badge" id="boxplotBadge">All Events</span>
            </h2>
            <div id="boxplotChart" style="height: 450px;"></div>
            <div class="bucket-info" id="bucketInfo"></div>
        </div>
        
        <div class="chart-panel">
            <h2>
                Post-Event Price Paths (8:30am → 5pm)
                <span class="badge" id="pathsBadge">0 events</span>
            </h2>
            <div class="distro-presets" style="margin-bottom: 10px;">
                <span style="font-weight: 500;">Window:</span>
                <select id="pathsFromSelect" onchange="updatePathsWindow()"
                        style="padding: 6px 10px; border-radius: 6px; background: var(--bg-tertiary);
                               color: var(--text-primary); border: 1px solid var(--border-color); font-size: 12px;">
                    <option value="0" selected>t (8:30)</option>
                    <option value="5">t+5m</option>
                    <option value="10">t+10m</option>
                    <option value="15">t+15m</option>
                    <option value="20">t+20m</option>
                    <option value="30">t+30m</option>
                    <option value="45">t+45m</option>
                    <option value="60">t+1h</option>
                    <option value="75">t+1h15m</option>
                    <option value="90">t+1h30m</option>
                    <option value="120">t+2h</option>
                    <option value="150">t+2h30m</option>
                    <option value="180">t+3h</option>
                    <option value="210">t+3h30m</option>
                    <option value="240">t+4h</option>
                    <option value="270">t+4h30m</option>
                    <option value="300">t+5h</option>
                    <option value="330">t+5h30m</option>
                    <option value="360">t+6h</option>
                    <option value="390">t+6h30m</option>
                    <option value="420">t+7h</option>
                    <option value="450">t+7h30m</option>
                </select>
                <span style="color: var(--text-secondary);">→</span>
                <select id="pathsToSelect" onchange="updatePathsWindow()"
                        style="padding: 6px 10px; border-radius: 6px; background: var(--bg-tertiary);
                               color: var(--text-primary); border: 1px solid var(--border-color); font-size: 12px;">
                    <option value="0">t (8:30)</option>
                    <option value="5">t+5m</option>
                    <option value="10">t+10m</option>
                    <option value="15">t+15m</option>
                    <option value="20">t+20m</option>
                    <option value="30">t+30m</option>
                    <option value="45">t+45m</option>
                    <option value="60">t+1h</option>
                    <option value="75">t+1h15m</option>
                    <option value="90">t+1h30m</option>
                    <option value="120">t+2h</option>
                    <option value="150">t+2h30m</option>
                    <option value="180">t+3h</option>
                    <option value="210">t+3h30m</option>
                    <option value="240">t+4h</option>
                    <option value="270">t+4h30m</option>
                    <option value="300">t+5h</option>
                    <option value="330">t+5h30m</option>
                    <option value="360">t+6h</option>
                    <option value="390">t+6h30m</option>
                    <option value="420">t+7h</option>
                    <option value="450">t+7h30m</option>
                    <option value="480">t+8h</option>
                    <option value="510" selected>t+8h30m (5pm)</option>
                </select>
            </div>
            <div class="stats-row">
                <div class="stat-item">
                    <span class="stat-label">Mean</span>
                    <span class="stat-value" id="meanMove">-- bps</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Median</span>
                    <span class="stat-value" id="medianMove">-- bps</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Min / Max</span>
                    <span class="stat-value" id="minMaxMove">-- / --</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">P10 / P90</span>
                    <span class="stat-value" id="p10p90Move">-- / --</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">P25 / P75</span>
                    <span class="stat-value" id="p25p75Move">-- / --</span>
                </div>
            </div>
            <div id="pathsChart" style="height: 450px;"></div>
            <div class="event-details" id="eventDetails">
                <h3>Selected Event Details</h3>
                <p style="color: var(--text-secondary);">Click on a price path to see details</p>
            </div>
        </div>
    </div>
    
    <!-- Distribution Explorer -->
    <div class="distro-section">
        <h2>
            Window Distribution Explorer
            <span class="badge" style="background: #8b5cf6;">Return Distribution</span>
            <span class="badge" id="distroBadge" style="background: var(--accent-yellow); color: #000;">t → t+30m</span>
        </h2>
        
        <div class="distro-presets" style="display: flex; gap: 20px; align-items: center;">
            <div style="display: flex; align-items: center; gap: 10px;">
                <span style="font-weight: 500;">From:</span>
                <select id="distroFromSelect" onchange="setDistroFrom(parseInt(this.value))" 
                        style="padding: 8px 12px; border-radius: 6px; background: var(--bg-tertiary); 
                               color: var(--text-primary); border: 1px solid var(--border-color); 
                               font-size: 14px; cursor: pointer; min-width: 100px;">
                    <option value="-1440">t - 24h</option>
                    <option value="-720">t - 12h</option>
                    <option value="-480">t - 8h</option>
                    <option value="-240">t - 4h</option>
                    <option value="-120">t - 2h</option>
                    <option value="-60">t - 1h</option>
                    <option value="-30">t - 30m</option>
                    <option value="-15">t - 15m</option>
                    <option value="-5">t - 5m</option>
                    <option value="0" selected>t (event)</option>
                </select>
            </div>
            <div style="font-size: 20px; color: var(--accent-purple);">→</div>
            <div style="display: flex; align-items: center; gap: 10px;">
                <span style="font-weight: 500;">To:</span>
                <select id="distroToSelect" onchange="setDistroTo(parseInt(this.value))"
                        style="padding: 8px 12px; border-radius: 6px; background: var(--bg-tertiary); 
                               color: var(--text-primary); border: 1px solid var(--border-color); 
                               font-size: 14px; cursor: pointer; min-width: 100px;">
                    <option value="0">t (event)</option>
                    <option value="5">t + 5m</option>
                    <option value="15">t + 15m</option>
                    <option value="30" selected>t + 30m</option>
                    <option value="60">t + 1h</option>
                    <option value="120">t + 2h</option>
                    <option value="240">t + 4h</option>
                    <option value="360">t + 6h</option>
                    <option value="510">t + 8.5h (5pm)</option>
                </select>
            </div>
        </div>
        
        <div class="distro-content">
            <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 15px;">
                <div id="distroChart" style="height: 350px;"></div>
            </div>
            <div class="distro-stats">
                <h4>Window Statistics</h4>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">N:</span>
                    <span id="distroN">--</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">Mean:</span>
                    <span id="distroMean">-- bps</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">Std Dev:</span>
                    <span id="distroStd">-- bps</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">Median:</span>
                    <span id="distroMedian">-- bps</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">P10 / P90:</span>
                    <span id="distroP10P90">-- / --</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">P25 / P75:</span>
                    <span id="distroP25P75">-- / --</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">P5 / P95:</span>
                    <span id="distroP5P95">-- / --</span>
                </div>
                <div class="distro-stat-row">
                    <span style="color: var(--text-secondary);">Min / Max:</span>
                    <span id="distroMinMax">-- / --</span>
                </div>
                <div class="distro-stat-row" style="margin-top: 15px; padding-top: 15px; border-top: 1px solid var(--border-color);">
                    <span style="color: var(--accent-green);">0 bps Percentile:</span>
                    <span id="distroZeroPct" style="color: var(--accent-green); font-weight: bold;">--%</span>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Candlestick Modal -->
    <div class="modal-overlay" id="candleModal">
        <div class="modal-content" id="modalContent">
            <div class="modal-header">
                <h2>Candlestick Chart - <span id="modalDate"></span></h2>
                <div style="display: flex; gap: 10px;">
                    <button class="expand-btn" id="expandBtn" onclick="toggleModalExpand()" title="Expand/Collapse">⛶</button>
                    <button class="modal-close" onclick="closeModal()">&times;</button>
                </div>
            </div>
            <div class="duration-slider">
                <label>Days around event:</label>
                <input type="range" id="daysSlider" min="1" max="10" value="2" oninput="updateCandlestick()">
                <span id="daysDisplay">±2 days</span>
            </div>
            <div id="candleChart" style="height: 500px;"></div>
            <div class="other-events" id="otherEvents">
                <h4>📅 Other Events on This Day</h4>
                <div id="otherEventsList"></div>
            </div>
        </div>
    </div>
    
    <script>
        // Data
        const events = {events_json};
        const BASE_DATE = new Date('2000-01-01T00:00:00');
        
        // State
        let distroFromMin = 0;
        let distroToMin = 30;
        let selectedEvent = null;
        let rawSeries = [];
        let pathsFilteredCount = null;
        let currentWindow = {{ start: null, end: null }};
        let windowEndMinutes = 510;  // Minutes after 8:30am (default: 510 = 5:00pm)
        
        // Dark layout for Plotly
        const darkLayout = {{
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: {{ color: '#f0f6fc' }},
            xaxis: {{ gridcolor: '#30363d', zerolinecolor: '#30363d' }},
            yaxis: {{ gridcolor: '#30363d', zerolinecolor: '#30363d' }},
            margin: {{ t: 40, r: 20, b: 50, l: 60 }},
        }};
        
        // Conversion
        function decimalToBps(points) {{
            return points * 256;  // 32 ticks/point * 8 bps/point
        }}
        
        // Update window end time
        function updateWindowEnd() {{
            windowEndMinutes = parseInt(document.getElementById('windowEndSlider').value);
            
            // Format label
            const hours = Math.floor(windowEndMinutes / 60);
            const mins = windowEndMinutes % 60;
            const endHour = 8 + hours + (30 + mins) / 60;
            const endHourInt = Math.floor(8 + hours + Math.floor((30 + mins) / 60));
            const endMins = (30 + mins) % 60;
            const ampm = endHourInt >= 12 ? 'PM' : 'AM';
            const displayHour = endHourInt > 12 ? endHourInt - 12 : endHourInt;
            
            document.getElementById('windowEndLabel').textContent = 
                `${{displayHour}}:${{endMins.toString().padStart(2, '0')}} ${{ampm}}`;
            
            const durationHours = Math.floor(windowEndMinutes / 60);
            const durationMins = windowEndMinutes % 60;
            document.getElementById('windowDuration').textContent = 
                durationMins > 0 ? `${{durationHours}}h ${{durationMins}}m` : `${{durationHours}}h`;
            
            // Recalculate boxplot and stats
            updateDashboard();
        }}
        
        // Filter outliers using IQR method
        function getOutlierBounds(values) {{
            if (values.length < 4) return null;
            const sorted = [...values].sort((a, b) => a - b);
            const n = sorted.length;
            const q1 = sorted[Math.floor(n * 0.25)];
            const q3 = sorted[Math.floor(n * 0.75)];
            const iqr = q3 - q1;
            return {{
                lower: q1 - 1.5 * iqr,
                upper: q3 + 1.5 * iqr
            }};
        }}

        function filterOutliers(values) {{
            const bounds = getOutlierBounds(values);
            if (!bounds) return values;
            return values.filter(v => v >= bounds.lower && v <= bounds.upper);
        }}
        
        // Calculate B/E multiplier for an event in the current window
        function calcBeMultiplier(event) {{
            const prices = event.postevent_prices;
            if (!prices || prices.length === 0) return null;
            
            // Get window bounds
            const eventTimeHours = 8.5;  // 8:30am in hours
            const windowEndHours = eventTimeHours + windowEndMinutes / 60;
            const windowEndMs = BASE_DATE.getTime() + windowEndHours * 60 * 60 * 1000;
            
            // Filter prices within window
            const windowPrices = prices.filter(p => p.ts <= windowEndMs);
            if (windowPrices.length === 0) return null;
            
            // Get OHLC for window
            const firstOpen = windowPrices[0].open;
            const highPrice = Math.max(...windowPrices.map(p => p.high));
            const lowPrice = Math.min(...windowPrices.map(p => p.low));
            
            // B/E multiplier = max(open-low, high-open) / fwd_be in bps
            const upMove = Math.abs(highPrice - firstOpen);
            const downMove = Math.abs(firstOpen - lowPrice);
            const maxMove = Math.max(upMove, downMove);
            const moveBps = maxMove * 8;  // decimal_to_bps
            
            const fwdBe = event.fwd_be || {user_fwd_be};  // Use event's fwd_be or default
            return fwdBe > 0 ? moveBps / fwdBe : 0;
        }}
        
        // Update dashboard
        function updateDashboard() {{
            const bucketWidth = parseFloat(document.getElementById('bucketWidthInput').value);
            const userActual = parseFloat(document.getElementById('actualInput').value);
            const userConsensus = parseFloat(document.getElementById('consensusInput').value);
            const consStd = parseFloat(document.getElementById('consStdInput').value);
            const fwdBe = parseFloat(document.getElementById('fwdBeInput').value);
            const removeOutliers = document.getElementById('removeOutliersCheckbox').checked;
            
            // Calculate user's surprise percentile
            let userSurpPct = 50;
            if (!isNaN(userActual) && !isNaN(userConsensus)) {{
                const userStdSurprise = (userActual - userConsensus) / consStd;
                const allStdSurprises = events
                    .filter(e => e.standardized_surprise != null)
                    .map(e => e.standardized_surprise);
                if (allStdSurprises.length > 0) {{
                    const countBelow = allStdSurprises.filter(s => s < userStdSurprise).length;
                    userSurpPct = (countBelow / allStdSurprises.length) * 100;
                }}
            }}
            
            // Create buckets
            const buckets = [];
            for (let i = 0; i < 100; i += bucketWidth) {{
                buckets.push({{
                    min: i,
                    max: Math.min(i + bucketWidth, 100),
                    label: `P${{i}}-${{Math.min(i + bucketWidth, 100)}}`
                }});
            }}
            
            // Find user's bucket
            const userBucketIdx = buckets.findIndex(b => userSurpPct >= b.min && userSurpPct < b.max);
            
            // Build boxplot data
            const boxplotData = [];
            const annotations = [];
            
            buckets.forEach((bucket, idx) => {{
                const bucketEvents = events.filter(e => {{
                    const pct = (e.surp_pct || 0) * 100;
                    return pct >= bucket.min && pct < bucket.max;
                }});
                
                let beValues = bucketEvents
                    .map(e => calcBeMultiplier(e))
                    .filter(v => v != null && v > 0);
                
                // Apply outlier filter if checked
                if (removeOutliers && beValues.length >= 4) {{
                    beValues = filterOutliers(beValues);
                }}
                
                const isUserBucket = idx === userBucketIdx;
                const hasSmallSample = beValues.length > 0 && beValues.length < 3;
                
                if (hasSmallSample) {{
                    annotations.push({{
                        x: bucket.label,
                        y: Math.max(...beValues) * 1.1,
                        text: `⚠️ n=${{beValues.length}}`,
                        showarrow: false,
                        font: {{ color: '#fbbf24', size: 10 }},
                    }});
                }}
                
                if (beValues.length > 0) {{
                    const color = isUserBucket ? '#22c55e' : '#9b59b6';
                    boxplotData.push({{
                        type: 'box',
                        y: beValues,
                        name: isUserBucket ? `${{bucket.label}} ★` : bucket.label,
                        marker: {{ color: color }},
                        line: {{ color: color, width: isUserBucket ? 3 : 1 }},
                        fillcolor: isUserBucket ? 'rgba(34, 197, 94, 0.3)' : 'rgba(155, 89, 182, 0.3)',
                    }});
                }}
            }});
            
            // Update bucket info
            if (userBucketIdx >= 0) {{
                const bucket = buckets[userBucketIdx];
                const bucketEvents = events.filter(e => {{
                    const pct = (e.surp_pct || 0) * 100;
                    return pct >= bucket.min && pct < bucket.max;
                }});
                let beValues = bucketEvents.map(e => calcBeMultiplier(e)).filter(v => v != null && v > 0);
                
                // Apply outlier filter if checked
                if (removeOutliers && beValues.length >= 4) {{
                    beValues = filterOutliers(beValues);
                }}
                
                if (beValues.length > 0) {{
                    const mean = (beValues.reduce((a,b) => a+b, 0) / beValues.length).toFixed(2);
                    const sorted = [...beValues].sort((a,b) => a-b);
                    const median = sorted[Math.floor(sorted.length/2)].toFixed(2);
                    const min = sorted[0].toFixed(2);
                    const max = sorted[sorted.length - 1].toFixed(2);
                    const warning = beValues.length < 3 ? ' <span style="color:#fbbf24;">⚠️ Small sample</span>' : '';
                    document.getElementById('bucketInfo').innerHTML = `
                        <strong style="color:#22c55e;">Your Bucket: ${{bucket.label}}</strong> (n=${{beValues.length}}${{warning}})<br>
                        Mean: ${{mean}}x | Median: ${{median}}x | Range: ${{min}}x - ${{max}}x
                    `;
                }} else {{
                    document.getElementById('bucketInfo').innerHTML = `
                        <strong style="color:#22c55e;">Your Bucket: ${{bucket.label}}</strong> — No data
                    `;
                }}
            }}
            
            // Render boxplot
            Plotly.newPlot('boxplotChart', boxplotData, {{
                ...darkLayout,
                title: '',
                yaxis: {{ ...darkLayout.yaxis, title: 'B/E Multiplier' }},
                showlegend: false,
                annotations: annotations,
            }}, {{ responsive: true }});
            
            // Render paths
            updatePaths();
            
            // Update distro
            updateDistro();
        }}
        
        function updatePathsWindow() {{
            const fromMin = parseInt(document.getElementById('pathsFromSelect').value);
            let toMin = parseInt(document.getElementById('pathsToSelect').value);
            if (toMin < fromMin) {{
                toMin = fromMin;
                document.getElementById('pathsToSelect').value = toMin;
            }}

            const eventTimeHours = 8.5;
            const startMs = BASE_DATE.getTime() + (eventTimeHours + fromMin/60) * 60 * 60 * 1000;
            const endMs = BASE_DATE.getTime() + (eventTimeHours + toMin/60) * 60 * 60 * 1000;
            currentWindow.start = startMs;
            currentWindow.end = endMs;

            if (rawSeries.length > 0) {{
                const removeOutliers = document.getElementById('removeOutliersCheckbox').checked;
                const newTraces = buildTraces(startMs, endMs, removeOutliers);
                Plotly.react('pathsChart', newTraces, {{
                    ...darkLayout,
                    title: '',
                    xaxis: {{
                        ...darkLayout.xaxis,
                        title: 'Time of Day',
                        range: [startMs, endMs],
                        type: 'date',
                        tickformat: '%H:%M',
                    }},
                    yaxis: {{ ...darkLayout.yaxis, title: 'Move (bps)' }},
                    showlegend: false,
                    hovermode: 'closest',
                }}).then(() => {{
                    const chart = document.getElementById('pathsChart');
                    bindPathsEvents(chart);
                }});
                const count = pathsFilteredCount != null ? pathsFilteredCount : rawSeries.length;
                document.getElementById('pathsBadge').textContent = `${{count}} events`;
            }}
        }}

        function updatePaths() {{
            rawSeries = [];
            const palette = ['#9b59b6', '#1d9bf0', '#22c55e', '#f97316', '#f43f5e',
                            '#14b8a6', '#eab308', '#8b5cf6', '#0ea5e9', '#f59e0b'];
            
            // Get bucket parameters to filter events
            const bucketWidth = parseFloat(document.getElementById('bucketWidthInput').value);
            const userActual = parseFloat(document.getElementById('actualInput').value);
            const userConsensus = parseFloat(document.getElementById('consensusInput').value);
            const consStd = parseFloat(document.getElementById('consStdInput').value);
            
            // Calculate user's surprise percentile
            let userSurpPct = 50;
            if (!isNaN(userActual) && !isNaN(userConsensus)) {{
                const userStdSurprise = (userActual - userConsensus) / consStd;
                const allStdSurprises = events
                    .filter(e => e.standardized_surprise != null)
                    .map(e => e.standardized_surprise);
                if (allStdSurprises.length > 0) {{
                    const countBelow = allStdSurprises.filter(s => s < userStdSurprise).length;
                    userSurpPct = (countBelow / allStdSurprises.length) * 100;
                }}
            }}
            
            // Calculate bucket bounds
            const bucketMin = Math.floor(userSurpPct / bucketWidth) * bucketWidth;
            const bucketMax = Math.min(bucketMin + bucketWidth, 100);
            
            // Only include events in the user's bucket
            events.forEach((event, idx) => {{
                if (!event.postevent_prices || event.postevent_prices.length === 0) return;
                
                // Filter by bucket
                const evtPct = (event.surp_pct || 0) * 100;
                if (evtPct < bucketMin || evtPct >= bucketMax) return;  // Skip events outside bucket
                
                const timestamps = event.postevent_prices.map(p => {{
                    const d = new Date(p.ts);
                    const mapped = new Date(BASE_DATE.getTime());
                    mapped.setHours(d.getHours(), d.getMinutes(), d.getSeconds(), 0);
                    return mapped.getTime();
                }});
                
                rawSeries.push({{
                    date: event.date,
                    timestamps,
                    bps: event.postevent_prices.map(p => p.bps),
                    event: event,
                }});
            }});
            
            if (rawSeries.length === 0) {{
                document.getElementById('pathsChart').innerHTML = '<p style="text-align:center; color:#8b949e; padding:60px;">No price data available</p>';
                return;
            }}
            
            // Set window from UI selectors
            const fromMin = parseInt(document.getElementById('pathsFromSelect').value);
            const toMin = parseInt(document.getElementById('pathsToSelect').value);
            const eventTimeHours = 8.5;
            currentWindow.start = BASE_DATE.getTime() + (eventTimeHours + fromMin/60) * 60 * 60 * 1000;
            currentWindow.end = BASE_DATE.getTime() + (eventTimeHours + toMin/60) * 60 * 60 * 1000;
            
            const removeOutliers = document.getElementById('removeOutliersCheckbox').checked;
            const traces = buildTraces(currentWindow.start, currentWindow.end, removeOutliers);
            
            const layout = {{
                ...darkLayout,
                title: '',
                xaxis: {{
                    ...darkLayout.xaxis,
                    title: 'Time of Day',
                    range: [currentWindow.start, currentWindow.end],
                    type: 'date',
                    tickformat: '%H:%M',
                }},
                yaxis: {{ ...darkLayout.yaxis, title: 'Move (bps)' }},
                showlegend: false,
                hovermode: 'closest',
            }};
            
            Plotly.newPlot('pathsChart', traces, layout, {{ responsive: true, scrollZoom: true }}).then(() => {{
                const chart = document.getElementById('pathsChart');
                bindPathsEvents(chart, layout);
                
                chart.on('plotly_relayout', (evt) => {{
                    if (evt['xaxis.range[0]'] != null && evt['xaxis.range[1]'] != null) {{
                        const start = Date.parse(evt['xaxis.range[0]']);
                        const end = Date.parse(evt['xaxis.range[1]']);
                        if (!isNaN(start) && !isNaN(end)) {{
                            currentWindow.start = start;
                            currentWindow.end = end;
                            const removeOutliers = document.getElementById('removeOutliersCheckbox').checked;
                            const newTraces = buildTraces(start, end, removeOutliers);
                            Plotly.react('pathsChart', newTraces, {{
                                ...layout,
                                xaxis: {{ ...layout.xaxis, range: [start, end] }},
                            }}).then(() => {{
                                bindPathsEvents(chart, layout);
                            }});
                        }}
                    }}
                }});
            }});
            
            const count = pathsFilteredCount != null ? pathsFilteredCount : rawSeries.length;
            document.getElementById('pathsBadge').textContent = `${{count}} events in P${{bucketMin}}-${{bucketMax}}`;
        }}
        
        function getWindowMoves(windowStart, windowEnd, removeOutliers = false) {{
            const seriesMoves = [];
            rawSeries.forEach((series, idx) => {{
                const base = interpolateAt(series.timestamps, series.bps, windowStart);
                const endVal = interpolateAt(series.timestamps, series.bps, windowEnd) - base;
                seriesMoves.push({{
                    idx,
                    endVal,
                    base,
                }});
            }});

            let filtered = seriesMoves;
            if (removeOutliers && seriesMoves.length >= 4) {{
                const bounds = getOutlierBounds(seriesMoves.map(s => s.endVal));
                if (bounds) {{
                    filtered = seriesMoves.filter(s => s.endVal >= bounds.lower && s.endVal <= bounds.upper);
                }}
            }}

            return {{
                moves: filtered.map(s => s.endVal),
                indices: filtered.map(s => s.idx),
            }};
        }}

        function buildTraces(windowStart, windowEnd, removeOutliers = false) {{
            const palette = ['#9b59b6', '#1d9bf0', '#22c55e', '#f97316', '#f43f5e',
                            '#14b8a6', '#eab308', '#8b5cf6', '#0ea5e9', '#f59e0b'];
            const traces = [];
            const windowMoves = getWindowMoves(windowStart, windowEnd, removeOutliers);
            const indexSet = new Set(windowMoves.indices);

            rawSeries.forEach((series, idx) => {{
                if (!indexSet.has(idx)) return;
                const base = interpolateAt(series.timestamps, series.bps, windowStart);
                const normalized = series.bps.map(v => v - base);
                traces.push({{
                    x: series.timestamps.map(t => new Date(t)),
                    y: normalized,
                    type: 'scatter',
                    mode: 'lines',
                    name: series.date,
                    line: {{ width: 1, color: palette[idx % palette.length] }},
                    hoverinfo: 'name+y',
                    customdata: series.timestamps.map(() => idx),
                }});
            }});

            pathsFilteredCount = windowMoves.moves.length;
            
            // Update stats
            const endMoves = windowMoves.moves;
            if (endMoves.length > 0) {{
                const mean = (endMoves.reduce((a,b) => a+b, 0) / endMoves.length).toFixed(1);
                const sorted = [...endMoves].sort((a,b) => a-b);
                const median = sorted[Math.floor(sorted.length/2)].toFixed(1);
                const min = sorted[0].toFixed(1);
                const max = sorted[sorted.length - 1].toFixed(1);
                const p10 = sorted[Math.floor(sorted.length * 0.10)].toFixed(1);
                const p90 = sorted[Math.min(Math.floor(sorted.length * 0.90), sorted.length - 1)].toFixed(1);
                const p25 = sorted[Math.floor(sorted.length * 0.25)].toFixed(1);
                const p75 = sorted[Math.min(Math.floor(sorted.length * 0.75), sorted.length - 1)].toFixed(1);
                document.getElementById('meanMove').textContent = `${{mean}} bps`;
                document.getElementById('medianMove').textContent = `${{median}} bps`;
                document.getElementById('minMaxMove').textContent = `${{min}} / ${{max}} bps`;
                document.getElementById('p10p90Move').textContent = `${{p10}} / ${{p90}} bps`;
                document.getElementById('p25p75Move').textContent = `${{p25}} / ${{p75}} bps`;
            }} else {{
                document.getElementById('meanMove').textContent = '-- bps';
                document.getElementById('medianMove').textContent = '-- bps';
                document.getElementById('minMaxMove').textContent = '-- / --';
                document.getElementById('p10p90Move').textContent = '-- / --';
                document.getElementById('p25p75Move').textContent = '-- / --';
            }}
            
            return traces;
        }}

        function bindPathsEvents(chart, layout) {{
            chart.removeAllListeners('plotly_click');
            chart.on('plotly_click', (clickData) => {{
                if (!clickData.points || clickData.points.length === 0) return;
                const rawIdx = clickData.points[0].customdata;
                if (rawIdx != null && rawSeries[rawIdx]) {{
                    showEventDetails(rawSeries[rawIdx].event);
                    openModal();
                }}
            }});
        }}
        
        function interpolateAt(times, values, targetTime) {{
            if (!times.length) return 0;
            if (targetTime <= times[0]) return values[0];
            if (targetTime >= times[times.length - 1]) return values[values.length - 1];
            for (let i = 0; i < times.length - 1; i++) {{
                if (targetTime >= times[i] && targetTime <= times[i + 1]) {{
                    const t = (targetTime - times[i]) / (times[i + 1] - times[i] || 1);
                    return values[i] + (values[i + 1] - values[i]) * t;
                }}
            }}
            return values[values.length - 1];
        }}
        
        function showEventDetails(event) {{
            selectedEvent = event;
            const details = document.getElementById('eventDetails');
            const eventTimeHours = 8.5;  // 8:30am
            const baseMs = BASE_DATE.getTime() + eventTimeHours * 60 * 60 * 1000;
            const series = event.postevent_prices || [];
            let windowRows = '';
            if (series.length > 0) {{
                const timestamps = series.map(p => p.ts);
                const bpsValues = series.map(p => p.bps);
                const windows = [
                    {{ label: 't+5m', min: 5 }},
                    {{ label: 't+10m', min: 10 }},
                    {{ label: 't+15m', min: 15 }},
                    {{ label: 't+30m', min: 30 }},
                    {{ label: 't+1h', min: 60 }},
                    {{ label: 't+2h', min: 120 }},
                    {{ label: 't+4h', min: 240 }},
                    {{ label: 't+8.5h', min: 510 }},
                ];
                const startBps = interpolateAt(timestamps, bpsValues, baseMs);
                windowRows = windows.map(w => {{
                    const endMs = baseMs + w.min * 60 * 1000;
                    const endBps = interpolateAt(timestamps, bpsValues, endMs);
                    const move = endBps - startBps;
                    return `
                        <tr>
                            <td style="padding: 4px 6px; color: var(--text-secondary);">${{w.label}}</td>
                            <td style="padding: 4px 6px; text-align: right;">${{move.toFixed(2)}} bps</td>
                        </tr>
                    `;
                }}).join('');
            }} else {{
                windowRows = `
                    <tr>
                        <td colspan="2" style="padding: 6px; color: var(--text-secondary); text-align: center;">
                            No price series
                        </td>
                    </tr>
                `;
            }}
            details.innerHTML = `
                <h3>${{event.date}}</h3>
                <p><strong>Actual:</strong> ${{event.actual?.toFixed(2) || 'N/A'}}%</p>
                <p><strong>Consensus:</strong> ${{event.consensus?.toFixed(2) || 'N/A'}}%</p>
                <p><strong>Surprise:</strong> ${{event.surprise?.toFixed(2) || 'N/A'}}%</p>
                <p><strong>Surprise Percentile:</strong> ${{event.surp_pct != null ? (event.surp_pct * 100).toFixed(1) : 'N/A'}}%</p>
                <p><strong>FWD B/E:</strong> ${{event.fwd_be?.toFixed(2) || 'N/A'}} bps</p>
                <p><strong>B/E Multiplier (window):</strong> ${{calcBeMultiplier(event)?.toFixed(2) || 'N/A'}}x</p>
                <div style="margin-top: 10px;">
                    <strong>Window Moves (bps)</strong>
                    <table style="width: 100%; margin-top: 6px; border-collapse: collapse;">
                        <tbody>
                            ${{windowRows}}
                        </tbody>
                    </table>
                </div>
                <button class="btn btn-primary" onclick="openModal()" style="margin-top: 15px; width: 100%;">
                    View Candlestick Chart
                </button>
            `;
            details.classList.add('visible');
        }}
        
        // Modal functions
        function openModal() {{
            if (!selectedEvent) return;
            document.getElementById('modalDate').textContent = selectedEvent.date;
            document.getElementById('candleModal').classList.add('visible');
            updateCandlestick();
        }}
        
        function closeModal() {{
            document.getElementById('candleModal').classList.remove('visible');
            document.getElementById('modalContent').classList.remove('expanded');
        }}
        
        function toggleModalExpand() {{
            const modal = document.getElementById('modalContent');
            modal.classList.toggle('expanded');
            // Resize chart after expand/collapse
            setTimeout(() => {{
                Plotly.Plots.resize('candleChart');
            }}, 100);
        }}
        
        function updateCandlestick() {{
            if (!selectedEvent) return;
            
            const days = parseInt(document.getElementById('daysSlider').value);
            document.getElementById('daysDisplay').textContent = `±${{days}} day${{days === 1 ? '' : 's'}}`;
            
            const candles = selectedEvent.candlestick_5m || [];
            if (candles.length === 0) {{
                document.getElementById('candleChart').innerHTML = '<p style="text-align:center; color:#8b949e; padding:60px;">No candlestick data available</p>';
                return;
            }}
            
            // Filter by days around event
            const eventDate = new Date(selectedEvent.date + 'T08:30:00');
            const startDate = new Date(eventDate.getTime() - days * 24 * 60 * 60 * 1000);
            const endDate = new Date(eventDate.getTime() + days * 24 * 60 * 60 * 1000);
            
            const filtered = candles.filter(c => {{
                const t = new Date(c.ts);
                return t >= startDate && t <= endDate;
            }});
            
            if (filtered.length === 0) {{
                document.getElementById('candleChart').innerHTML = '<p style="text-align:center; color:#8b949e; padding:60px;">No data in this range</p>';
                return;
            }}
            
            const trace = {{
                x: filtered.map(c => c.ts),
                open: filtered.map(c => c.open),
                high: filtered.map(c => c.high),
                low: filtered.map(c => c.low),
                close: filtered.map(c => c.close),
                type: 'candlestick',
                increasing: {{ line: {{ color: '#22c55e' }} }},
                decreasing: {{ line: {{ color: '#ef4444' }} }},
            }};
            
            const layout = {{
                ...darkLayout,
                title: '',
                xaxis: {{ ...darkLayout.xaxis, rangeslider: {{ visible: false }} }},
                yaxis: {{ ...darkLayout.yaxis, title: 'Price' }},
                shapes: [{{
                    type: 'line',
                    x0: selectedEvent.date + 'T08:30:00',
                    x1: selectedEvent.date + 'T08:30:00',
                    y0: 0,
                    y1: 1,
                    yref: 'paper',
                    line: {{ color: '#9b59b6', width: 2, dash: 'dash' }},
                }}],
                annotations: [{{
                    x: selectedEvent.date + 'T08:30:00',
                    y: 1.02,
                    yref: 'paper',
                    text: 'PPI Release',
                    showarrow: false,
                    font: {{ color: '#9b59b6', size: 11 }},
                }}],
            }};
            
            Plotly.newPlot('candleChart', [trace], layout, {{ responsive: true, scrollZoom: true }});
            
            // Show other events
            const others = selectedEvent.other_events || [];
            if (others.length > 0) {{
                const list = others.map(e => `
                    <div style="padding: 6px 0; border-bottom: 1px solid var(--border-color);">
                        <strong>${{e.name}}</strong> @ ${{e.time}}<br>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">
                            Actual: ${{e.actual || 'N/A'}} | Cons: ${{e.consensus || 'N/A'}}
                        </span>
                    </div>
                `).join('');
                document.getElementById('otherEventsList').innerHTML = list;
            }} else {{
                document.getElementById('otherEventsList').innerHTML = '<p style="color: var(--text-secondary);">No other high/medium events on this day</p>';
            }}
        }}
        
        // Distribution explorer
        function setDistroFrom(minutes) {{
            distroFromMin = minutes;
            document.getElementById('distroFromSelect').value = minutes;
            updateDistro();
        }}
        
        function setDistroTo(minutes) {{
            distroToMin = minutes;
            document.getElementById('distroToSelect').value = minutes;
            updateDistro();
        }}
        
        function formatDistroLabel(mins) {{
            if (mins === 0) return 't';
            const sign = mins >= 0 ? '+' : '';
            if (Math.abs(mins) >= 60) return `t${{sign}}${{mins/60}}h`;
            return `t${{sign}}${{mins}}m`;
        }}
        
        function updateDistro() {{
            document.getElementById('distroBadge').textContent = 
                `${{formatDistroLabel(distroFromMin)}} → ${{formatDistroLabel(distroToMin)}}`;
            
            const eventTimeHours = 8.5;  // 8:30am
            const fromMs = BASE_DATE.getTime() + (eventTimeHours + distroFromMin/60) * 60 * 60 * 1000;
            const toMs = BASE_DATE.getTime() + (eventTimeHours + distroToMin/60) * 60 * 60 * 1000;
            
            // Apply outlier filter if checked
            const removeOutliers = document.getElementById('removeOutliersCheckbox').checked;
            const moves = getWindowMoves(fromMs, toMs, removeOutliers).moves;
            
            renderDistro(moves);
        }}
        
        function renderDistro(values) {{
            if (!values || values.length === 0) {{
                document.getElementById('distroChart').innerHTML = '<p style="text-align:center; color:#8b949e; padding:60px;">No data for selected window</p>';
                return;
            }}
            
            const sorted = [...values].sort((a,b) => a-b);
            const n = sorted.length;
            const mean = values.reduce((a,b) => a+b, 0) / n;
            const median = n % 2 === 0 ? (sorted[n/2-1] + sorted[n/2]) / 2 : sorted[Math.floor(n/2)];
            const variance = values.reduce((s,v) => s + (v-mean)**2, 0) / (n-1 || 1);
            const std = Math.sqrt(variance);
            const min = sorted[0];
            const max = sorted[n-1];
            const p5 = sorted[Math.floor(n * 0.05)];
            const p10 = sorted[Math.floor(n * 0.10)];
            const p25 = sorted[Math.floor(n * 0.25)];
            const p75 = sorted[Math.min(Math.floor(n * 0.75), n-1)];
            const p90 = sorted[Math.min(Math.floor(n * 0.90), n-1)];
            const p95 = sorted[Math.min(Math.floor(n * 0.95), n-1)];
            
            // Percentile of zero
            let zeroPct = 0;
            for (let i = 0; i < n; i++) {{
                if (sorted[i] >= 0) {{ zeroPct = (i / n) * 100; break; }}
                if (i === n-1) zeroPct = 100;
            }}
            
            // Update stats
            document.getElementById('distroN').textContent = n;
            document.getElementById('distroMean').textContent = `${{mean.toFixed(2)}} bps`;
            document.getElementById('distroStd').textContent = `${{std.toFixed(2)}} bps`;
            document.getElementById('distroMedian').textContent = `${{median.toFixed(2)}} bps`;
            document.getElementById('distroP10P90').textContent = `${{p10.toFixed(1)}} / ${{p90.toFixed(1)}} bps`;
            document.getElementById('distroP25P75').textContent = `${{p25.toFixed(1)}} / ${{p75.toFixed(1)}} bps`;
            document.getElementById('distroP5P95').textContent = `${{p5.toFixed(1)}} / ${{p95.toFixed(1)}} bps`;
            document.getElementById('distroMinMax').textContent = `${{min.toFixed(1)}} / ${{max.toFixed(1)}} bps`;
            document.getElementById('distroZeroPct').textContent = `${{zeroPct.toFixed(1)}}%`;
            
            // Histogram (DistroDashboard style: skyblue, opacity 0.6)
            const histTrace = {{
                x: values,
                type: 'histogram',
                name: 'Histogram',
                histnorm: 'probability density',
                opacity: 0.6,
                marker: {{ color: 'skyblue' }},
                nbinsx: Math.min(20, Math.max(5, Math.floor(n/2))),
                hovertemplate: '%{{x:.2f}} bps<br>Density: %{{y:.4f}}<extra></extra>'
            }};
            
            // KDE (DistroDashboard style: darkblue, width 2)
            const bandwidth = 1.06 * std * Math.pow(n, -0.2);
            const xRange = max - min || 1;
            const xPadding = xRange * 0.15;
            const xGrid = [];
            const yGrid = [];
            for (let i = 0; i < 300; i++) {{
                const x = min - xPadding + (xRange + 2*xPadding) * i / 299;
                xGrid.push(x);
                let sum = 0;
                for (const v of values) {{
                    const z = (x - v) / bandwidth;
                    sum += Math.exp(-0.5*z*z) / Math.sqrt(2*Math.PI);
                }}
                yGrid.push(sum / (n * bandwidth));
            }}
            
            const kdeTrace = {{
                x: xGrid,
                y: yGrid,
                type: 'scatter',
                mode: 'lines',
                name: 'KDE',
                line: {{ color: 'darkblue', width: 2 }},
                hoverinfo: 'skip'
            }};
            
            // Stats box annotation (DistroDashboard style)
            const statsBox = 
                `N: ${{n}}<br>` +
                `Mean: ${{mean.toFixed(2)}} bps<br>` +
                `Std: ${{std.toFixed(2)}} bps<br>` +
                `Min: ${{min.toFixed(2)}}<br>` +
                `P5: ${{p5.toFixed(2)}}<br>` +
                `P25: ${{p25.toFixed(2)}}<br>` +
                `Median: ${{median.toFixed(2)}}<br>` +
                `P75: ${{p75.toFixed(2)}}<br>` +
                `P95: ${{p95.toFixed(2)}}<br>` +
                `Max: ${{max.toFixed(2)}}<br>` +
                `<b style="color:#22c55e;">0 bps @ P${{zeroPct.toFixed(0)}}</b>`;
            
            // DistroDashboard layout (light theme with stats box)
            const distroLayout = {{
                title: {{ text: `Returns: ${{formatDistroLabel(distroFromMin)}} → ${{formatDistroLabel(distroToMin)}}`, font: {{ size: 14, color: '#333' }} }},
                xaxis: {{ 
                    title: {{ text: 'Move (bps)', font: {{ size: 12 }} }},
                    gridcolor: '#e0e0e0',
                    zerolinecolor: '#ef4444',
                    zerolinewidth: 2,
                    zeroline: true
                }},
                yaxis: {{ 
                    title: {{ text: 'Density', font: {{ size: 12 }} }},
                    gridcolor: '#e0e0e0'
                }},
                barmode: 'overlay',
                height: 380,
                margin: {{ t: 40, r: 30, b: 50, l: 55 }},
                paper_bgcolor: 'white',
                plot_bgcolor: 'white',
                showlegend: false,
                annotations: [
                    {{
                        xref: 'paper', yref: 'paper',
                        x: 0.98, y: 0.98,
                        xanchor: 'right', yanchor: 'top',
                        text: statsBox,
                        showarrow: false,
                        align: 'left',
                        font: {{ size: 10, color: 'black' }},
                        bgcolor: 'white',
                        bordercolor: 'black',
                        borderwidth: 1,
                        opacity: 0.9
                    }}
                ]
            }};
            
            Plotly.newPlot('distroChart', [histTrace, kdeTrace], distroLayout, {{ responsive: true, displayModeBar: false }});
        }}
        
        // Initialize
        updateDashboard();
    </script>
</body>
</html>
'''
    return html


def main():
    parser = argparse.ArgumentParser(description="PPI Post-Event Dashboard Generator")
    parser.add_argument("--actual", type=float, help="User's actual PPI value (%)")
    parser.add_argument("--consensus", type=float, help="User's consensus value (%)")
    parser.add_argument("--cons-std", type=float, default=0.1, help="Consensus std dev (default: 0.1%%)")
    parser.add_argument("--fwd-be", type=float, default=8.0, help="FWD breakeven in bps (default: 8.0)")
    parser.add_argument("--serve", action="store_true", help="Start live server after generating")
    parser.add_argument("--output", type=str, help="Output HTML file path")
    args = parser.parse_args()
    
    print("=" * 60)
    print("  PPI Core MoM Post-Event Dashboard Generator")
    print("=" * 60)
    
    # Load events
    events = load_ppi_events()
    if not events:
        print("No PPI events found in database. Exiting.")
        return
    
    # Load FWD breakevens from CSV
    fwd_be_map = load_fwd_be_from_csv()
    
    # Merge FWD_BE into events
    for evt in events:
        evt['fwd_be'] = fwd_be_map.get(evt['date'], args.fwd_be)
    
    # Compute surprise percentiles
    events = compute_surprise_percentiles(events, args.cons_std)
    
    # Build dashboard data
    data = build_dashboard_data(events, args.cons_std, args.fwd_be)
    
    # Generate HTML
    html = generate_html(data, args.actual, args.consensus, args.cons_std, args.fwd_be)
    
    # Write to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = OUTPUT_DIR / f"ppi_postevent_dashboard_{timestamp}.html"
    
    output_path.write_text(html, encoding="utf-8")
    print(f"\nDashboard saved to: {output_path}")
    
    # Open in browser
    webbrowser.open(f"file://{output_path.resolve()}")
    print("Opening in browser...")
    
    if args.serve:
        print("\n[--serve not implemented for standalone HTML]")
        print("The dashboard is fully self-contained and works offline.")


if __name__ == "__main__":
    main()

