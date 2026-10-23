#!/usr/bin/env python
"""
January Auction Weeks Dashboard Generator
------------------------------------------
Generates an interactive HTML dashboard showing TY price paths for January
Treasury auction weeks (2Y, 5Y, 7Y auctions).

Usage:
    python scripts/january_auction_weeks.py
    python scripts/january_auction_weeks.py --start-year 2018
"""

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

AUCTION_DATA_DIR = PROJECT_ROOT / "auction_data"
OUTPUT_FILE = PROJECT_ROOT / "scripts" / "january_auction_weeks.html"


def load_auction_dates(tenor: str, start_year: int) -> List[Dict]:
    """Load auction dates from processed CSV files."""
    filepath = AUCTION_DATA_DIR / f"{tenor.lower()}_note_processed.csv"
    if not filepath.exists():
        logger.warning(f"Auction file not found: {filepath}")
        return []
    
    auctions = []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Parse auction date
                date_str = row.get('Auction', row.get('auction', row.get('Date', '')))
                if not date_str:
                    continue
                
                # Handle various date formats
                for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y']:
                    try:
                        dt = datetime.strptime(date_str.strip(), fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue
                
                if dt.year >= start_year and dt.month == 1:  # January only
                    auctions.append({
                        'date': dt,
                        'tenor': tenor,
                        'data': row
                    })
            except Exception as e:
                continue
    
    logger.info(f"Loaded {len(auctions)} {tenor} January auctions from {start_year}")
    return auctions


def get_auction_weeks(start_year: int) -> List[Dict]:
    """Group January auctions by week."""
    # Load all auction dates
    all_auctions = []
    for tenor in ['2Y', '5Y', '7Y']:
        all_auctions.extend(load_auction_dates(tenor, start_year))
    
    # Group by week (ISO week)
    weeks = defaultdict(lambda: {'auctions': [], 'year': None, 'month': None})
    for auction in all_auctions:
        dt = auction['date']
        week_key = f"{dt.year}-W{dt.isocalendar()[1]:02d}"
        weeks[week_key]['auctions'].append(auction)
        weeks[week_key]['year'] = dt.year
        weeks[week_key]['month'] = dt.month
    
    # Build week info
    result = []
    for week_key, week_data in sorted(weeks.items()):
        if len(week_data['auctions']) == 0:
            continue
        
        auctions_by_tenor = {a['tenor']: a for a in week_data['auctions']}
        
        # Get week start (Monday)
        first_auction = min(week_data['auctions'], key=lambda x: x['date'])
        week_start = first_auction['date'] - timedelta(days=first_auction['date'].weekday())
        
        week_info = {
            'year': week_data['year'],
            'month': week_data['month'],
            'week_start': week_start.strftime('%Y-%m-%d'),
        }
        
        # Add auction dates for each tenor
        for tenor in ['2Y', '5Y', '7Y']:
            if tenor in auctions_by_tenor:
                dt = auctions_by_tenor[tenor]['date']
                # Approximate auction times
                time_str = "11:30" if tenor == "2Y" else "13:00"
                week_info[f'{tenor.lower()}_date'] = dt.strftime(f'%Y-%m-%d {time_str}')
                week_info[f'{tenor.lower()}_day'] = dt.strftime('%a')
            else:
                week_info[f'{tenor.lower()}_date'] = None
                week_info[f'{tenor.lower()}_day'] = None
        
        # Check if consecutive days
        auction_dates = [a['date'] for a in week_data['auctions']]
        auction_dates.sort()
        is_consecutive = len(auction_dates) >= 2 and all(
            (auction_dates[i+1] - auction_dates[i]).days == 1 
            for i in range(len(auction_dates)-1)
        )
        week_info['is_consecutive'] = is_consecutive
        
        result.append({
            'week_info': week_info,
            'week_key': week_key,
        })
    
    logger.info(f"Found {len(result)} January auction weeks")
    return result


def fetch_price_data(week_start: str, conn) -> Dict:
    """Fetch 1-minute TY price data for the auction week."""
    cur = conn.cursor()
    
    # Parse week start
    start_dt = datetime.strptime(week_start, '%Y-%m-%d')
    # Fetch Sunday 6pm to Friday 5pm (full trading week)
    query_start = start_dt - timedelta(days=1, hours=6)  # Sunday 6pm
    query_end = start_dt + timedelta(days=4, hours=17)   # Friday 5pm
    
    cur.execute("""
        SELECT ts_event, close 
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event <= %s
        ORDER BY ts_event
    """, (query_start, query_end))
    
    rows = cur.fetchall()
    
    if not rows:
        return {'timestamps': [], 'closes': []}
    
    timestamps = [row[0].isoformat() for row in rows]
    closes = [float(row[1]) for row in rows]
    
    return {'timestamps': timestamps, 'closes': closes}


def generate_html(weeks_data: List[Dict], histogram_data: Dict) -> str:
    """Generate the HTML dashboard."""
    
    # Read template from existing file or create new
    html_template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>January Auction Weeks - TY Price Paths</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        :root {
            --bg-primary: #0a0f1a;
            --bg-secondary: #111827;
            --bg-tertiary: #1f2937;
            --text-primary: #f9fafb;
            --text-secondary: #9ca3af;
            --border-color: #374151;
            --accent-teal: #14b8a6;
            --accent-green: #22c55e;
            --accent-orange: #f97316;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --accent-purple: #a855f7;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.5;
        }
        
        .container { max-width: 1800px; margin: 0 auto; padding: 30px; }
        
        header {
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }
        
        h1 { font-size: 2rem; font-weight: 700; margin-bottom: 10px; }
        .subtitle { color: var(--text-secondary); font-size: 1rem; }
        
        .stats-bar { display: flex; gap: 30px; margin-top: 20px; flex-wrap: wrap; }
        
        .stat-item {
            background: var(--bg-secondary);
            padding: 12px 20px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }
        
        .stat-label { color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 4px; }
        .stat-value { color: var(--accent-teal); font-size: 1.2rem; font-weight: 600; }
        
        .chart-container {
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 30px;
            border: 1px solid var(--border-color);
        }
        
        .chart-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 15px; }
        
        .weeks-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        
        .week-card {
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 15px;
            border: 1px solid var(--border-color);
            cursor: pointer;
            transition: border-color 0.2s;
        }
        
        .week-card:hover { border-color: var(--accent-teal); }
        .week-card.selected { border-color: var(--accent-teal); background: rgba(20, 184, 166, 0.1); }
        
        .week-year { font-size: 1.2rem; font-weight: 700; color: var(--accent-teal); }
        .week-dates { color: var(--text-secondary); font-size: 0.85rem; margin-top: 5px; }
        
        #price-chart, #histogram-chart { width: 100%; height: 500px; }
        
        .generated-info {
            color: var(--text-secondary);
            font-size: 0.8rem;
            text-align: center;
            margin-top: 30px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 January Auction Weeks - TY Price Paths</h1>
            <p class="subtitle">Treasury 2Y, 5Y, 7Y auction weeks in January - 1-minute price data</p>
            <div class="stats-bar">
                <div class="stat-item">
                    <div class="stat-label">Total Weeks</div>
                    <div class="stat-value" id="total-weeks">-</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Data Range</div>
                    <div class="stat-value" id="data-range">-</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Selected Week</div>
                    <div class="stat-value" id="selected-week">None</div>
                </div>
            </div>
        </header>
        
        <div class="chart-container">
            <div class="chart-title">Normalized Price Paths (bps change from window start)</div>
            <div id="price-chart"></div>
        </div>
        
        <div class="chart-container">
            <div class="chart-title">Weekly Weeks</div>
            <div class="weeks-grid" id="weeks-grid"></div>
        </div>
        
        <p class="generated-info">Generated: ''' + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ''' | Data cleaned of calendar spreads</p>
    </div>
    
    <script>
        const weeksData = ''' + json.dumps(weeks_data, indent=2) + ''';
        
        // Initialize
        document.getElementById('total-weeks').textContent = weeksData.length;
        if (weeksData.length > 0) {
            const years = weeksData.map(w => w.week_info.year);
            document.getElementById('data-range').textContent = `${Math.min(...years)} - ${Math.max(...years)}`;
        }
        
        // Render week cards
        const grid = document.getElementById('weeks-grid');
        weeksData.forEach((week, idx) => {
            const card = document.createElement('div');
            card.className = 'week-card';
            card.dataset.week = idx;
            card.innerHTML = `
                <div class="week-year">${week.week_info.year}</div>
                <div class="week-dates">
                    Week of ${week.week_info.week_start}<br>
                    2Y: ${week.week_info['2y_day'] || '-'} | 
                    5Y: ${week.week_info['5y_day'] || '-'} | 
                    7Y: ${week.week_info['7y_day'] || '-'}
                </div>
            `;
            card.onclick = () => selectWeek(idx);
            grid.appendChild(card);
        });
        
        // Plot all paths
        function plotAllPaths() {
            const traces = weeksData.map((week, idx) => {
                const prices = week.price_data;
                if (!prices || !prices.closes || prices.closes.length === 0) return null;
                
                const refPrice = prices.closes[0];
                const bpsChanges = prices.closes.map(p => (p - refPrice) * 32 * 100 / 2);  // 32nds to bps
                
                return {
                    x: prices.timestamps.map((_, i) => i),  // Use index for x-axis
                    y: bpsChanges,
                    type: 'scatter',
                    mode: 'lines',
                    name: `${week.week_info.year}`,
                    opacity: 0.6,
                    line: { width: 1 }
                };
            }).filter(t => t !== null);
            
            Plotly.newPlot('price-chart', traces, {
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: { color: '#f9fafb' },
                xaxis: { title: 'Minutes from Week Start', gridcolor: '#374151' },
                yaxis: { title: 'Change (bps)', gridcolor: '#374151' },
                showlegend: true,
                legend: { orientation: 'h', y: -0.15 },
                margin: { t: 20, r: 20, b: 60, l: 60 }
            });
        }
        
        function selectWeek(idx) {
            // Update selection
            document.querySelectorAll('.week-card').forEach(c => c.classList.remove('selected'));
            document.querySelectorAll('.week-card')[idx].classList.add('selected');
            document.getElementById('selected-week').textContent = weeksData[idx].week_info.year;
            
            // Highlight selected path
            const traces = weeksData.map((week, i) => {
                const prices = week.price_data;
                if (!prices || !prices.closes || prices.closes.length === 0) return null;
                
                const refPrice = prices.closes[0];
                const bpsChanges = prices.closes.map(p => (p - refPrice) * 32 * 100 / 2);
                
                return {
                    x: prices.timestamps.map((_, j) => j),
                    y: bpsChanges,
                    type: 'scatter',
                    mode: 'lines',
                    name: `${week.week_info.year}`,
                    opacity: i === idx ? 1 : 0.2,
                    line: { width: i === idx ? 3 : 1, color: i === idx ? '#14b8a6' : undefined }
                };
            }).filter(t => t !== null);
            
            Plotly.react('price-chart', traces, {
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: { color: '#f9fafb' },
                xaxis: { title: 'Minutes from Week Start', gridcolor: '#374151' },
                yaxis: { title: 'Change (bps)', gridcolor: '#374151' },
                showlegend: true,
                legend: { orientation: 'h', y: -0.15 },
                margin: { t: 20, r: 20, b: 60, l: 60 }
            });
        }
        
        plotAllPaths();
    </script>
</body>
</html>'''
    
    return html_template


def main():
    parser = argparse.ArgumentParser(description="Generate January Auction Weeks Dashboard")
    parser.add_argument('--start-year', type=int, default=2018, help='Start year for data')
    args = parser.parse_args()
    
    logger.info(f"Generating January Auction Weeks dashboard from {args.start_year}")
    
    # Get auction weeks
    weeks = get_auction_weeks(args.start_year)
    
    if not weeks:
        logger.error("No auction weeks found")
        return
    
    # Connect to database and fetch price data
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD
    )
    
    logger.info("Fetching price data for each week...")
    for week in weeks:
        week['price_data'] = fetch_price_data(week['week_info']['week_start'], conn)
        if week['price_data']['closes']:
            logger.info(f"  {week['week_info']['year']}: {len(week['price_data']['closes'])} bars")
        else:
            logger.warning(f"  {week['week_info']['year']}: No price data")
    
    conn.close()
    
    # Generate HTML
    html = generate_html(weeks, {})
    
    # Write output
    OUTPUT_FILE.write_text(html, encoding='utf-8')
    logger.info(f"Dashboard saved to: {OUTPUT_FILE}")
    logger.info(f"Open: file:///{OUTPUT_FILE}")


if __name__ == '__main__':
    main()
 