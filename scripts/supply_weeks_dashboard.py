#!/usr/bin/env python
"""
2Y/5Y/7Y Supply Weeks Dashboard Generator.

Builds a standalone TY dashboard for weeks containing the standard 2Y, 5Y,
and 7Y Treasury auctions. Auction anchors come from the processed auction CSVs,
and market data comes from PostgreSQL ohlcv_1m.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER
from rules.treasury_price_conversion import points_to_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

AUCTION_DATA_DIR = PROJECT_ROOT / "auction_data"
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "scripts" / "supply_weeks_dashboard.html"
DEFAULT_DASHBOARDS_OUTPUT_FILE = PROJECT_ROOT / "dashboards" / "supply_weeks_dashboard.html"

VALID_TY_MIN = 80.0
VALID_TY_MAX = 150.0
TENORS = ("2Y", "5Y", "7Y")
AUCTION_TIMES = {"2Y": "11:30", "5Y": "13:00", "7Y": "13:00"}


def parse_auction_date(value: str) -> Optional[datetime]:
    """Parse auction date strings from processed CSVs."""
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_money(value: str) -> Optional[float]:
    """Parse a money string like '$69,000,000,000'."""
    raw = (value or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_auction_dates(tenor: str, start_year: int) -> List[Dict]:
    """Load standard auction dates for one tenor from its processed CSV."""
    filepath = AUCTION_DATA_DIR / f"{tenor.lower()}_note_processed.csv"
    if not filepath.exists():
        raise FileNotFoundError(f"Missing auction file: {filepath}")

    auctions: List[Dict] = []
    with filepath.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt = parse_auction_date(row.get("Auction", ""))
            if dt is None or dt.year < start_year:
                continue
            size = parse_money(row.get("Size", ""))
            if size is None:
                continue
            time_str = AUCTION_TIMES[tenor]
            hh, mm = [int(part) for part in time_str.split(":")]
            auctions.append(
                {
                    "tenor": tenor,
                    "date": dt.replace(hour=hh, minute=mm, second=0, microsecond=0),
                    "size": size,
                    "data": row,
                }
            )

    auctions.sort(key=lambda item: item["date"])
    logger.info("Loaded %d %s auctions from %s", len(auctions), tenor, filepath.name)
    return auctions


def is_month_end_supply_week(seven_dt: datetime) -> bool:
    """Return true when the 7Y auction lands in the last 7 calendar days."""
    last_day = calendar.monthrange(seven_dt.year, seven_dt.month)[1]
    return seven_dt.day >= last_day - 6


def build_auction_weeks(start_year: int, min_size: float) -> List[Dict]:
    """Build week records containing 2Y, 5Y, and 7Y auctions."""
    by_week: Dict[Tuple[int, int], Dict[str, Dict]] = defaultdict(dict)
    for tenor in TENORS:
        for auction in load_auction_dates(tenor, start_year=start_year):
            dt = auction["date"]
            iso = dt.isocalendar()
            slot = by_week[(iso[0], iso[1])]
            prev = slot.get(tenor)
            if prev is None:
                slot[tenor] = auction
                continue
            # FRNs/TIPS can appear in the same ISO week as the standard note
            # auction. Use the largest issue in the week to keep the standard
            # 2Y/5Y/7Y cycle while preserving older years with smaller sizes.
            prev_preferred = prev["size"] >= min_size
            auction_preferred = auction["size"] >= min_size
            if auction_preferred and not prev_preferred:
                slot[tenor] = auction
            elif auction_preferred == prev_preferred and auction["size"] > prev["size"]:
                slot[tenor] = auction

    weeks: List[Dict] = []
    for (iso_year, iso_week), tenors in sorted(by_week.items()):
        if not all(tenor in tenors for tenor in TENORS):
            continue

        dates = [tenors[tenor]["date"] for tenor in TENORS]
        dates_only = sorted({d.date() for d in dates})
        first_dt = min(dates)
        week_start = first_dt - timedelta(days=first_dt.weekday())
        seven_dt = tenors["7Y"]["date"]
        is_consecutive = len(dates_only) >= 2 and all(
            (dates_only[i + 1] - dates_only[i]).days == 1
            for i in range(len(dates_only) - 1)
        )

        week_info: Dict = {
            "year": seven_dt.year,
            "month": seven_dt.month,
            "month_name": seven_dt.strftime("%b"),
            "week_start": week_start.strftime("%Y-%m-%d"),
            "is_january": seven_dt.month == 1,
            "is_month_end": is_month_end_supply_week(seven_dt),
            "is_consecutive": is_consecutive,
        }
        for tenor in TENORS:
            key = tenor.lower()
            dt = tenors[tenor]["date"]
            week_info[f"{key}_date"] = dt.strftime("%Y-%m-%d %H:%M")
            week_info[f"{key}_day"] = dt.strftime("%a")
            week_info[f"{key}_size"] = tenors[tenor]["size"]

        weeks.append(
            {
                "week_key": f"{iso_year}-W{iso_week:02d}",
                "week_info": week_info,
            }
        )

    logger.info("Constructed %d matched 2Y/5Y/7Y supply weeks", len(weeks))
    return weeks


def aggregate_to_5m(rows: List[Tuple[datetime, float, float, float, float]]) -> Dict[str, List]:
    """Aggregate 1m OHLC rows into 5m candles."""
    out = {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": []}
    bucket_ts: Optional[datetime] = None
    o = h = l = c = None

    def flush() -> None:
        if bucket_ts is None:
            return
        out["timestamps"].append(bucket_ts.isoformat())
        out["opens"].append(float(o))
        out["highs"].append(float(h))
        out["lows"].append(float(l))
        out["closes"].append(float(c))

    for ts, opn, high, low, close in rows:
        minute_bucket = (ts.minute // 5) * 5
        bts = ts.replace(minute=minute_bucket, second=0, microsecond=0)
        if bucket_ts is None:
            bucket_ts = bts
            o, h, l, c = opn, high, low, close
            continue
        if bts != bucket_ts:
            flush()
            bucket_ts = bts
            o, h, l, c = opn, high, low, close
            continue
        h = max(h, high)
        l = min(l, low)
        c = close

    flush()
    return out


def trim_candles_to_days(candles_5m: Dict[str, List], keep_days: set[str]) -> Dict[str, List]:
    """Keep only 5m candles whose calendar date is one of the auction days."""
    out = {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": []}
    ts = candles_5m.get("timestamps", [])
    opens = candles_5m.get("opens", [])
    highs = candles_5m.get("highs", [])
    lows = candles_5m.get("lows", [])
    closes = candles_5m.get("closes", [])
    for i, ts_val in enumerate(ts):
        if str(ts_val)[:10] not in keep_days:
            continue
        if i >= len(opens) or i >= len(highs) or i >= len(lows) or i >= len(closes):
            continue
        out["timestamps"].append(ts_val)
        out["opens"].append(opens[i])
        out["highs"].append(highs[i])
        out["lows"].append(lows[i])
        out["closes"].append(closes[i])
    return out


def fetch_market_data_for_week(week_start: str, conn) -> Dict:
    """Fetch TY market data for Sunday 6pm through Friday 5pm."""
    cur = conn.cursor()
    start_dt = datetime.strptime(week_start, "%Y-%m-%d")
    query_start = start_dt - timedelta(days=1, hours=6)
    query_end = start_dt + timedelta(days=4, hours=17)

    cur.execute(
        """
        SELECT ts_event, open, high, low, close
        FROM ohlcv_1m
        WHERE symbol = 'TY'
          AND ts_event >= %s
          AND ts_event <= %s
          AND close >= %s
          AND close <= %s
        ORDER BY ts_event
        """,
        (query_start, query_end, VALID_TY_MIN, VALID_TY_MAX),
    )
    rows = cur.fetchall()
    if not rows:
        return {
            "price_data": {"hours": [], "closes": []},
            "candles_5m": {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": []},
        }

    first_ts = rows[0][0]
    if getattr(first_ts, "tzinfo", None) is not None:
        query_start_aligned = query_start.replace(tzinfo=first_ts.tzinfo)
    else:
        query_start_aligned = query_start

    closes = [float(r[4]) for r in rows]
    hours = [round((r[0] - query_start_aligned).total_seconds() / 3600.0, 4) for r in rows]
    return {
        "price_data": {"hours": hours, "closes": closes},
        "candles_5m": aggregate_to_5m(rows),
    }


def enrich_weeks_with_market_data(weeks: List[Dict]) -> None:
    """Add price_data and trimmed candles_5m to each week in place."""
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )
    try:
        for i, week in enumerate(weeks, start=1):
            info = week["week_info"]
            market = fetch_market_data_for_week(info["week_start"], conn)
            keep_days = {
                str(info.get("2y_date", ""))[:10],
                str(info.get("5y_date", ""))[:10],
                str(info.get("7y_date", ""))[:10],
            }
            keep_days = {day for day in keep_days if day}
            market["candles_5m"] = trim_candles_to_days(market["candles_5m"], keep_days)
            week["price_data"] = market["price_data"]
            week["candles_5m"] = market["candles_5m"]
            logger.info(
                "  [%d/%d] %s -> %d points",
                i,
                len(weeks),
                week["week_key"],
                len(week["price_data"]["closes"]),
            )
    finally:
        conn.close()


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>2Y/5Y/7Y Supply Weeks - TY Price Paths</title>
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
      --accent-blue: #3b82f6;
      --accent-red: #ef4444;
      --accent-amber: #f59e0b;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      line-height: 1.45;
    }
    .container { max-width: 1800px; margin: 0 auto; padding: 28px; }
    header { margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border-color); }
    h1 { font-size: 1.9rem; margin-bottom: 8px; }
    .subtitle { color: var(--text-secondary); font-size: 0.98rem; }
    .stats-bar { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px; align-items: stretch; }
    .stat-item {
      background: var(--bg-secondary);
      padding: 10px 16px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
    }
    .stat-label { color: var(--text-secondary); font-size: 0.82rem; }
    .stat-value { color: var(--accent-teal); font-size: 1.15rem; font-weight: 600; margin-top: 2px; }
    .chart-container {
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 18px;
      margin-bottom: 22px;
    }
    .chart-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .chart-title { font-size: 1.08rem; font-weight: 600; }
    .badge {
      background: var(--accent-teal);
      color: #00110f;
      border-radius: 10px;
      padding: 3px 10px;
      font-size: 0.78rem;
      font-weight: 600;
    }
    .badge-orange { background: var(--accent-orange); color: #111827; }
    .hint-bar {
      color: var(--text-secondary);
      background: var(--bg-tertiary);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 0.85rem;
      margin-bottom: 10px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .weeks-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(255px, 1fr));
      gap: 12px;
    }
    .week-card {
      position: relative;
      background: var(--bg-tertiary);
      border: 2px solid var(--border-color);
      border-radius: 8px;
      padding: 10px;
      cursor: pointer;
      transition: border-color 0.2s ease, background 0.2s ease;
    }
    .week-card:hover { border-color: var(--accent-teal); }
    .week-card.selected { border-color: var(--accent-teal); background: rgba(20, 184, 166, 0.12); }
    .week-card.january { border-color: var(--accent-orange); background: rgba(249, 115, 22, 0.10); }
    .week-card.january.selected { background: rgba(249, 115, 22, 0.18); }
    .week-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
    .week-year { color: var(--accent-teal); font-weight: 700; font-size: 1.06rem; }
    .week-card.january .week-year { color: var(--accent-orange); }
    .chips { display: flex; gap: 5px; flex-wrap: wrap; justify-content: flex-end; }
    .chip {
      display: inline-block;
      border-radius: 10px;
      padding: 2px 8px;
      font-size: 0.72rem;
      background: rgba(59, 130, 246, 0.2);
      color: #bfdbfe;
      white-space: nowrap;
    }
    .chip.month-end { background: rgba(20, 184, 166, 0.2); color: #5eead4; }
    .chip.early { background: rgba(245, 158, 11, 0.18); color: #fcd34d; }
    .chip.jan { background: rgba(249, 115, 22, 0.22); color: #fdba74; }
    .week-dates { color: var(--text-secondary); font-size: 0.82rem; margin-top: 6px; line-height: 1.45; }
    .btn {
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 8px 14px;
      background: var(--bg-tertiary);
      color: var(--text-primary);
      cursor: pointer;
      font-size: 0.88rem;
    }
    .btn:hover { border-color: var(--accent-teal); }
    .btn[disabled] { opacity: 0.45; cursor: not-allowed; }
    #price-chart { width: 100%; height: 560px; }
    .distro-controls {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      margin-bottom: 14px;
      align-items: center;
    }
    .control-group { display: flex; align-items: center; gap: 8px; }
    .control-group label { font-size: 0.9rem; color: var(--text-secondary); }
    .control-group input[type="checkbox"] { accent-color: var(--accent-teal); }
    .control-group select {
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      color: var(--text-primary);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 0.9rem;
    }
    .distro-grid {
      display: grid;
      grid-template-columns: 1fr 300px;
      gap: 14px;
    }
    #distro-chart { width: 100%; height: 420px; }
    .distro-stats {
      background: var(--bg-tertiary);
      border-radius: 8px;
      padding: 10px;
      border: 1px solid var(--border-color);
    }
    .distro-stats h4 { margin-bottom: 8px; font-size: 0.95rem; }
    .stat-row { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 0.86rem; }
    .stat-row .label { color: var(--text-secondary); }
    .stat-row .value { color: var(--text-primary); }
    .positive { color: var(--accent-green) !important; }
    .negative { color: var(--accent-red) !important; }
    .modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      z-index: 9999;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .modal.visible { display: flex; }
    .modal-content {
      width: min(1200px, 96vw);
      max-height: 90vh;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      overflow: hidden;
    }
    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-color);
      font-size: 1rem;
      font-weight: 600;
    }
    .modal-close {
      border: none;
      background: transparent;
      color: var(--text-secondary);
      font-size: 1.4rem;
      cursor: pointer;
      padding: 2px 8px;
    }
    .modal-close:hover { color: var(--text-primary); }
    .modal-body { padding: 12px; }
    #candlestick-chart { width: 100%; height: 560px; }
    .generated-info {
      color: var(--text-secondary);
      text-align: center;
      font-size: 0.8rem;
      margin-top: 18px;
    }
    @media (max-width: 900px) {
      .distro-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>2Y/5Y/7Y Supply Weeks Analysis</h1>
      <p class="subtitle">TY price behavior around standard 2Y, 5Y and 7Y Treasury auction weeks. Month-end = 7Y auction in the last 7 calendar days of the month.</p>
      <div class="stats-bar">
        <div class="stat-item"><div class="stat-label">Total Supply Weeks</div><div class="stat-value" id="total-weeks">-</div></div>
        <div class="stat-item"><div class="stat-label">January Weeks</div><div class="stat-value" id="january-weeks" style="color: var(--accent-orange);">-</div></div>
        <div class="stat-item"><div class="stat-label">Month-End Weeks</div><div class="stat-value" id="month-end-weeks">-</div></div>
        <div class="stat-item"><div class="stat-label">Data Range</div><div class="stat-value" id="data-range">-</div></div>
        <div class="stat-item"><div class="stat-label">Selected Week</div><div class="stat-value" id="selected-label">None</div></div>
        <div class="stat-item"><div class="stat-label">Window</div><div class="stat-value" id="window-range">Full Week</div></div>
      </div>
    </header>

    <div class="chart-container">
      <div class="chart-header">
        <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
          <span class="chart-title">Normalized TY Price Paths (bps from visible window start)</span>
          <span class="badge" id="paths-badge">0 paths</span>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <select id="type-filter" onchange="onTypeFilterChange()" class="btn" style="padding:8px 10px;">
            <option value="all" selected>Supply Weeks: All</option>
            <option value="month_end">Month-End Only</option>
            <option value="non_month_end">Non-Month-End</option>
            <option value="january">January Only</option>
            <option value="january_month_end">January & Month-End</option>
          </select>
          <button class="btn" onclick="resetZoom()">Reset Zoom</button>
        </div>
      </div>
      <div class="hint-bar">
        <span>Zoom/pan to re-normalize all traces.</span>
        <span>|</span>
        <span>Click a path or card, then open Candlestick Detail.</span>
        <span>|</span>
        <span>The supply-week filter applies to paths, cards and distribution stats.</span>
      </div>
      <div id="price-chart"></div>
    </div>

    <div class="chart-container">
      <div class="chart-header">
        <span class="chart-title">Supply Weeks</span>
        <button class="btn" id="btn-candle" onclick="openCandlestickModal()" disabled>Candlestick Detail</button>
      </div>
      <div class="weeks-grid" id="weeks-grid"></div>
    </div>

    <div class="chart-container">
      <div class="chart-header">
        <div style="display:flex; align-items:center; gap:10px;">
          <span class="chart-title">Return Distribution Analysis</span>
          <span class="badge" id="distro-badge">0 samples</span>
        </div>
      </div>
      <div class="distro-controls">
        <div class="control-group">
          <label>Window:</label>
          <select id="distro-window" onchange="updateDistribution()">
            <option value="full">Full Week</option>
            <option value="pre_2y">Pre-2Y</option>
            <option value="2y_to_5y" selected>2Y to 5Y</option>
            <option value="5y_to_7y">5Y to 7Y</option>
            <option value="post_7y">Post-7Y</option>
            <option value="auction_day_2y">2Y Auction Day (+/-6h)</option>
            <option value="auction_day_5y">5Y Auction Day (+/-6h)</option>
            <option value="auction_day_7y">7Y Auction Day (+/-6h)</option>
          </select>
        </div>
        <div class="control-group">
          <label>From Year:</label>
          <select id="distro-year" onchange="updateDistribution()"></select>
        </div>
        <div class="control-group">
          <label>Measure:</label>
          <select id="distro-measure" onchange="updateDistribution()">
            <option value="max_move" selected>Max Move</option>
            <option value="end_move">End-to-End Move</option>
            <option value="max_up">Max Up Move</option>
            <option value="max_down">Max Down Move</option>
            <option value="range">High-Low Range</option>
          </select>
        </div>
        <div class="control-group">
          <input id="annotate-january" type="checkbox" checked onchange="updateDistribution()" />
          <label for="annotate-january">Annotate January</label>
        </div>
        <div class="control-group">
          <input id="annotate-month-end" type="checkbox" checked onchange="updateDistribution()" />
          <label for="annotate-month-end">Annotate Month-End</label>
        </div>
      </div>
      <div class="hint-bar">
        <span>Histogram and stats are computed from selected year onward and the active supply-week filter.</span>
        <span>|</span>
        <span style="color:#fdba74;">When enabled, orange vertical lines mark January weeks.</span>
        <span>|</span>
        <span style="color:#5eead4;">When enabled, teal vertical lines mark month-end weeks.</span>
      </div>
      <div class="distro-grid">
        <div id="distro-chart"></div>
        <div class="distro-stats">
          <h4>Filtered Weeks Stats (bps)</h4>
          <div class="stat-row"><span class="label">N</span><span class="value" id="stat-n">-</span></div>
          <div class="stat-row"><span class="label">Mean</span><span class="value" id="stat-mean">-</span></div>
          <div class="stat-row"><span class="label">Median</span><span class="value" id="stat-median">-</span></div>
          <div class="stat-row"><span class="label">Std Dev</span><span class="value" id="stat-std">-</span></div>
          <div class="stat-row"><span class="label">Min</span><span class="value" id="stat-min">-</span></div>
          <div class="stat-row"><span class="label">P5</span><span class="value" id="stat-p5">-</span></div>
          <div class="stat-row"><span class="label">P25</span><span class="value" id="stat-p25">-</span></div>
          <div class="stat-row"><span class="label">P75</span><span class="value" id="stat-p75">-</span></div>
          <div class="stat-row"><span class="label">P95</span><span class="value" id="stat-p95">-</span></div>
          <div class="stat-row"><span class="label">Max</span><span class="value" id="stat-max">-</span></div>
          <div class="stat-row"><span class="label">0 Pctl</span><span class="value" id="stat-zero-pct">-</span></div>
          <div class="stat-row"><span class="label">Expected Rally</span><span class="value" id="stat-exp-rally">-</span></div>
          <div class="stat-row"><span class="label">Expected Selloff</span><span class="value" id="stat-exp-selloff">-</span></div>
        </div>
      </div>
    </div>

    <p class="generated-info">Generated: __GENERATED_AT__ | Source: auction_data 2Y/5Y/7Y processed CSVs | TY range filter: [__VALID_TY_MIN__, __VALID_TY_MAX__]</p>
  </div>

  <div class="modal" id="candlestick-modal">
    <div class="modal-content">
      <div class="modal-header">
        <span id="modal-title">Candlestick Detail</span>
        <button class="modal-close" onclick="closeCandlestickModal()">&times;</button>
      </div>
      <div class="modal-body"><div id="candlestick-chart"></div></div>
    </div>
  </div>

  <script>
    const weeksData = __WEEKS_DATA__;
    const BPS_PER_POINT = __BPS_PER_POINT__;
    let lastClickedWeek = null;
    let currentWindowStart = null;
    let currentWindowEnd = null;

    function pointsToBps(points) {
      return points * BPS_PER_POINT;
    }

    function hoursToTimeStr(hours) {
      if (hours === null || hours === undefined) return "N/A";
      const day = Math.floor(hours / 24);
      const h = Math.floor(hours % 24);
      const m = Math.round((hours - Math.floor(hours)) * 60);
      const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"];
      return `${dayNames[Math.max(0, Math.min(dayNames.length - 1, day))]} ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    }

    function parseAuctionHours(week) {
      const info = week.week_info;
      const out = {};
      ["2y", "5y", "7y"].forEach((tenor) => {
        const dateStr = info[`${tenor}_date`];
        if (!dateStr) return;
        const parts = dateStr.split(" ");
        const datePart = parts[0];
        const timePart = parts[1] || "13:00";
        const hm = timePart.split(":");
        const hh = Number(hm[0] || 0);
        const mm = Number(hm[1] || 0);
        const weekStart = new Date(info.week_start);
        const aucDate = new Date(datePart);
        const dayOffset = Math.round((aucDate - weekStart) / (24 * 60 * 60 * 1000));
        const hours = 6 + (dayOffset * 24) + hh + (mm / 60.0);
        out[tenor] = hours;
      });
      return out;
    }

    function getWindowBounds(week, windowType) {
      const auc = parseAuctionHours(week);
      const auc2 = auc["2y"] ?? 41.5;
      const auc5 = auc["5y"] ?? 61.0;
      const auc7 = auc["7y"] ?? 85.0;
      switch (windowType) {
        case "full": return { start: 0, end: 119 };
        case "pre_2y": return { start: 0, end: auc2 };
        case "2y_to_5y": return { start: auc2, end: auc5 };
        case "5y_to_7y": return { start: auc5, end: auc7 };
        case "post_7y": return { start: auc7, end: 119 };
        case "auction_day_2y": return { start: auc2 - 6, end: auc2 + 6 };
        case "auction_day_5y": return { start: auc5 - 6, end: auc5 + 6 };
        case "auction_day_7y": return { start: auc7 - 6, end: auc7 + 6 };
        default: return { start: 0, end: 119 };
      }
    }

    function matchesTypeFilter(week, filterValue) {
      const info = week.week_info || {};
      if (filterValue === "all") return true;
      if (filterValue === "month_end") return !!info.is_month_end;
      if (filterValue === "non_month_end") return !info.is_month_end;
      if (filterValue === "january") return !!info.is_january;
      if (filterValue === "january_month_end") return !!info.is_january && !!info.is_month_end;
      return true;
    }

    function activeFilterValue() {
      return document.getElementById("type-filter").value;
    }

    function onTypeFilterChange() {
      renderWeekCards();
      renderPaths();
      updateDistribution();
    }

    function renderWeekCards() {
      const grid = document.getElementById("weeks-grid");
      grid.innerHTML = "";
      const filterValue = activeFilterValue();
      let selectedStillVisible = false;
      weeksData.forEach((week, idx) => {
        if (!matchesTypeFilter(week, filterValue)) return;
        const info = week.week_info;
        const card = document.createElement("div");
        const selClass = lastClickedWeek === idx ? "selected" : "";
        if (selClass) selectedStillVisible = true;
        card.className = `week-card ${selClass} ${info.is_january ? "january" : ""}`;
        const monthChip = info.is_month_end
          ? `<span class="chip month-end">Month-End</span>`
          : `<span class="chip early">Early-Cycle</span>`;
        const januaryChip = info.is_january ? `<span class="chip jan">January</span>` : "";
        card.innerHTML = `
          <div class="week-top">
            <div class="week-year">${info.year} ${info.month_name}</div>
            <div class="chips">${januaryChip}${monthChip}</div>
          </div>
          <div class="week-dates">
            Week of ${info.week_start}<br/>
            2Y: ${info["2y_day"] || "-"} (${info["2y_date"] || "-"})<br/>
            5Y: ${info["5y_day"] || "-"} (${info["5y_date"] || "-"})<br/>
            7Y: ${info["7y_day"] || "-"} (${info["7y_date"] || "-"})
          </div>
        `;
        card.onclick = () => {
          lastClickedWeek = idx;
          document.getElementById("selected-label").textContent = `${info.year}-${info.month_name}`;
          document.getElementById("btn-candle").disabled = false;
          renderWeekCards();
          renderPaths();
        };
        grid.appendChild(card);
      });
      if (!selectedStillVisible) {
        lastClickedWeek = null;
        document.getElementById("selected-label").textContent = "None";
        document.getElementById("btn-candle").disabled = true;
      }
    }

    function resetZoom() {
      currentWindowStart = null;
      currentWindowEnd = null;
      renderPaths();
    }

    function renderPaths() {
      const colors = [
        "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
        "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
        "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000"
      ];
      const traces = [];
      let count = 0;
      const filterValue = activeFilterValue();
      weeksData.forEach((week, idx) => {
        if (!matchesTypeFilter(week, filterValue)) return;
        const pd = week.price_data;
        if (!pd || !pd.hours || !pd.closes || pd.hours.length < 2) return;
        let arr = pd.hours.map((h, i) => ({ h, c: pd.closes[i] }));
        if (currentWindowStart !== null && currentWindowEnd !== null) {
          arr = arr.filter(d => d.h >= currentWindowStart && d.h <= currentWindowEnd);
        }
        if (arr.length < 2) return;
        const ref = arr[0].c;
        const x = arr.map(d => d.h / 24.0);
        const y = arr.map(d => pointsToBps(d.c - ref));
        const selected = idx === lastClickedWeek;
        const baseColor = week.week_info.is_january ? "#f97316" : (week.week_info.is_month_end ? "#14b8a6" : colors[count % colors.length]);
        traces.push({
          x,
          y,
          type: "scatter",
          mode: "lines",
          name: `${week.week_info.year} ${week.week_info.month_name}${week.week_info.is_month_end ? " ME" : ""}`,
          opacity: selected ? 1.0 : 0.65,
          line: { color: baseColor, width: selected ? 2.8 : 1.2 },
          customdata: [idx],
          hovertemplate: `${week.week_info.year} ${week.week_info.month_name}<br>Day: %{x:.2f}<br>Move: %{y:.2f} bps<extra></extra>`
        });
        count += 1;
      });

      document.getElementById("paths-badge").textContent = `${count} paths`;
      if (currentWindowStart !== null && currentWindowEnd !== null) {
        document.getElementById("window-range").textContent = `${hoursToTimeStr(currentWindowStart)} -> ${hoursToTimeStr(currentWindowEnd)}`;
      } else {
        document.getElementById("window-range").textContent = "Full Week";
      }

      const layout = {
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: { color: "#f9fafb" },
        xaxis: {
          title: "Days from Week Start (0 = Sunday 6pm)",
          gridcolor: "#374151",
          range: currentWindowStart !== null ? [currentWindowStart / 24.0, currentWindowEnd / 24.0] : undefined,
          tickvals: [0, 1, 2, 3, 4, 5],
          ticktext: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
        },
        yaxis: {
          title: "Move (bps from visible window start)",
          gridcolor: "#374151",
          zeroline: true,
          zerolinecolor: "#4b5563"
        },
        showlegend: true,
        legend: { orientation: "h", y: -0.14 },
        margin: { t: 20, r: 24, b: 72, l: 62 },
        dragmode: "zoom",
        hovermode: "closest"
      };
      Plotly.react("price-chart", traces, layout, { responsive: true, scrollZoom: true, displayModeBar: true }).then(() => {
        const chart = document.getElementById("price-chart");
        chart.removeAllListeners("plotly_relayout");
        chart.on("plotly_relayout", handleRelayout);
        chart.removeAllListeners("plotly_click");
        chart.on("plotly_click", handleClick);
      });
    }

    function handleRelayout(eventData) {
      let s = null;
      let e = null;
      if (eventData["xaxis.range"]) {
        s = eventData["xaxis.range"][0];
        e = eventData["xaxis.range"][1];
      } else if (eventData["xaxis.range[0]"] !== undefined) {
        s = eventData["xaxis.range[0]"];
        e = eventData["xaxis.range[1]"];
      } else if (eventData["xaxis.autorange"]) {
        currentWindowStart = null;
        currentWindowEnd = null;
        renderPaths();
        return;
      } else {
        return;
      }
      const newStart = s * 24.0;
      const newEnd = e * 24.0;
      if (newEnd - newStart < 1.0) return;
      const delta = 0.5;
      const changed =
        currentWindowStart === null ||
        currentWindowEnd === null ||
        Math.abs(currentWindowStart - newStart) > delta ||
        Math.abs(currentWindowEnd - newEnd) > delta;
      if (changed) {
        currentWindowStart = newStart;
        currentWindowEnd = newEnd;
        renderPaths();
      }
    }

    function handleClick(clickData) {
      if (!clickData.points || clickData.points.length === 0) return;
      const idx = Array.isArray(clickData.points[0].customdata) ? clickData.points[0].customdata[0] : null;
      if (idx === null || idx === undefined) return;
      lastClickedWeek = idx;
      const w = weeksData[idx];
      document.getElementById("selected-label").textContent = `${w.week_info.year}-${w.week_info.month_name}`;
      document.getElementById("btn-candle").disabled = false;
      renderWeekCards();
      renderPaths();
    }

    function openCandlestickModal() {
      if (lastClickedWeek === null) return;
      const w = weeksData[lastClickedWeek];
      const c = w.candles_5m;
      if (!c || !c.timestamps || c.timestamps.length === 0) return;
      const parts = [
        `${w.week_info.year} ${w.week_info.month_name}`,
        `2Y ${w.week_info["2y_date"]}`,
        `5Y ${w.week_info["5y_date"]}`,
        `7Y ${w.week_info["7y_date"]}`
      ];
      if (w.week_info.is_month_end) parts.push("Month-End");
      document.getElementById("modal-title").textContent = parts.join(" | ");

      const trace = {
        x: c.timestamps,
        open: c.opens,
        high: c.highs,
        low: c.lows,
        close: c.closes,
        type: "candlestick",
        increasing: { line: { color: "#22c55e" } },
        decreasing: { line: { color: "#ef4444" } },
        name: "TY 5m"
      };
      const layout = {
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: { color: "#f9fafb" },
        xaxis: { title: "Time", gridcolor: "#374151", rangeslider: { visible: false } },
        yaxis: { title: "TY Price", gridcolor: "#374151" },
        margin: { t: 30, r: 24, b: 46, l: 62 }
      };
      Plotly.react("candlestick-chart", [trace], layout, { responsive: true, scrollZoom: true });
      document.getElementById("candlestick-modal").classList.add("visible");
    }

    function closeCandlestickModal() {
      document.getElementById("candlestick-modal").classList.remove("visible");
    }

    document.getElementById("candlestick-modal").onclick = (e) => {
      if (e.target.classList.contains("modal")) closeCandlestickModal();
    };
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeCandlestickModal();
    });

    function calculateMove(week, bounds, measureType) {
      const pd = week.price_data;
      if (!pd || !pd.hours || !pd.closes || pd.hours.length < 2) return null;
      const arr = pd.hours.map((h, i) => ({ h, c: pd.closes[i] }))
        .filter(d => d.h >= bounds.start && d.h <= bounds.end);
      if (arr.length < 2) return null;
      const start = arr[0].c;
      let high = start;
      let low = start;
      let maxUp = 0;
      let maxDown = 0;
      for (const p of arr) {
        const m = pointsToBps(p.c - start);
        if (m > maxUp) maxUp = m;
        if (m < maxDown) maxDown = m;
        if (p.c > high) high = p.c;
        if (p.c < low) low = p.c;
      }
      const endMove = pointsToBps(arr[arr.length - 1].c - start);
      const range = pointsToBps(high - low);
      const maxAbs = Math.max(Math.abs(maxUp), Math.abs(maxDown));
      switch (measureType) {
        case "max_move": return maxAbs;
        case "end_move": return endMove;
        case "max_up": return maxUp;
        case "max_down": return Math.abs(maxDown);
        case "range": return range;
        default: return maxAbs;
      }
    }

    function gaussianKDE(data) {
      const n = data.length;
      if (!n) return () => 0;
      const mean = data.reduce((a, b) => a + b, 0) / n;
      const std = Math.sqrt(data.reduce((s, v) => s + (v - mean) ** 2, 0) / n) || 1;
      const bw = std * Math.pow(n, -0.2);
      return function(x) {
        let sum = 0;
        for (let i = 0; i < n; i++) {
          const z = (x - data[i]) / bw;
          sum += Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
        }
        return sum / (n * bw);
      };
    }

    function clearDistroStats() {
      ["stat-n","stat-mean","stat-median","stat-std","stat-min","stat-p5","stat-p25","stat-p75","stat-p95","stat-max","stat-zero-pct","stat-exp-rally","stat-exp-selloff"]
        .forEach((id) => { document.getElementById(id).textContent = "-"; });
    }

    function updateDistribution() {
      const windowType = document.getElementById("distro-window").value;
      const fromYear = Number(document.getElementById("distro-year").value || 2015);
      const measure = document.getElementById("distro-measure").value;
      const filterValue = activeFilterValue();
      const annotateJanuary = document.getElementById("annotate-january").checked;
      const annotateMonthEnd = document.getElementById("annotate-month-end").checked;
      const moves = [];
      const labels = [];
      const januaryMarkers = [];
      const monthEndMarkers = [];

      for (const w of weeksData) {
        if (w.week_info.year < fromYear) continue;
        if (!matchesTypeFilter(w, filterValue)) continue;
        const bounds = getWindowBounds(w, windowType);
        const move = calculateMove(w, bounds, measure);
        if (move === null || Number.isNaN(move)) continue;
        const lbl = `${w.week_info.year} ${w.week_info.month_name}`;
        moves.push(move);
        labels.push(lbl);
        if (annotateJanuary && w.week_info.is_january) januaryMarkers.push({ move, label: `${lbl} Jan` });
        if (annotateMonthEnd && w.week_info.is_month_end) monthEndMarkers.push({ move, label: `${lbl} ME` });
      }

      document.getElementById("distro-badge").textContent =
        `${moves.length} weeks (${januaryMarkers.length} Jan, ${monthEndMarkers.length} MoEnd)`;
      if (moves.length === 0) {
        document.getElementById("distro-chart").innerHTML =
          '<div style="text-align:center;padding:60px;color:#9ca3af;">No data for selected filters</div>';
        clearDistroStats();
        return;
      }

      const sorted = [...moves].sort((a, b) => a - b);
      const n = sorted.length;
      const mean = moves.reduce((a, b) => a + b, 0) / n;
      const variance = moves.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1 || 1);
      const std = Math.sqrt(variance);
      const median = n % 2 === 0 ? (sorted[n/2 - 1] + sorted[n/2]) / 2 : sorted[Math.floor(n/2)];
      const min = sorted[0];
      const max = sorted[n - 1];
      const p5 = sorted[Math.floor(n * 0.05)] ?? min;
      const p25 = sorted[Math.floor(n * 0.25)] ?? min;
      const p75 = sorted[Math.floor(n * 0.75)] ?? max;
      const p95 = sorted[Math.min(Math.floor(n * 0.95), n - 1)] ?? max;
      const zeroPct = (sorted.filter(v => v < 0).length / n * 100.0);

      document.getElementById("stat-n").textContent = String(n);
      document.getElementById("stat-mean").textContent = mean.toFixed(2);
      document.getElementById("stat-median").textContent = median.toFixed(2);
      document.getElementById("stat-std").textContent = std.toFixed(2);
      document.getElementById("stat-min").textContent = min.toFixed(2);
      document.getElementById("stat-p5").textContent = p5.toFixed(2);
      document.getElementById("stat-p25").textContent = p25.toFixed(2);
      document.getElementById("stat-p75").textContent = p75.toFixed(2);
      document.getElementById("stat-p95").textContent = p95.toFixed(2);
      document.getElementById("stat-max").textContent = max.toFixed(2);
      document.getElementById("stat-zero-pct").textContent = `${zeroPct.toFixed(1)}%`;

      const rallyMoves = moves.filter(v => v > 0);
      const selloffMovesAbs = moves.filter(v => v < 0).map(v => Math.abs(v));
      const rallyProb = rallyMoves.length / n;
      const selloffProb = selloffMovesAbs.length / n;
      const rallyAvg = rallyMoves.length ? (rallyMoves.reduce((a, b) => a + b, 0) / rallyMoves.length) : 0;
      const selloffAvg = selloffMovesAbs.length ? (selloffMovesAbs.reduce((a, b) => a + b, 0) / selloffMovesAbs.length) : 0;
      document.getElementById("stat-exp-rally").textContent = `${(rallyProb * rallyAvg).toFixed(2)} bps`;
      document.getElementById("stat-exp-selloff").textContent = `${(selloffProb * selloffAvg).toFixed(2)} bps`;

      const meanEl = document.getElementById("stat-mean");
      meanEl.classList.remove("positive", "negative");
      if (measure === "end_move") meanEl.classList.add(mean >= 0 ? "positive" : "negative");

      const kde = gaussianKDE(moves);
      const xRange = max - min || 1;
      const xPad = xRange * 0.2;
      const xGrid = [];
      const yGrid = [];
      for (let i = 0; i < 200; i++) {
        const x = min - xPad + ((xRange + (2 * xPad)) * i / 199);
        xGrid.push(x);
        yGrid.push(kde(x));
      }

      const measureLabels = {
        max_move: "Max Move (bps)",
        end_move: "End-to-End Move (bps)",
        max_up: "Max Up Move (bps)",
        max_down: "Max Down Move (bps)",
        range: "High-Low Range (bps)"
      };
      const windowLabels = {
        full: "Full Week",
        pre_2y: "Pre-2Y",
        "2y_to_5y": "2Y -> 5Y",
        "5y_to_7y": "5Y -> 7Y",
        post_7y: "Post-7Y",
        auction_day_2y: "2Y Day (+/-6h)",
        auction_day_5y: "5Y Day (+/-6h)",
        auction_day_7y: "7Y Day (+/-6h)"
      };

      const markerShapes = [
        ...januaryMarkers.map((m) => ({
          type: "line", x0: m.move, x1: m.move, y0: 0, y1: 1, yref: "paper",
          line: { color: "#f97316", width: 2, dash: "solid" }
        })),
        ...monthEndMarkers.map((m) => ({
          type: "line", x0: m.move, x1: m.move, y0: 0, y1: 1, yref: "paper",
          line: { color: "#14b8a6", width: 1.5, dash: "dot" }
        }))
      ];
      const markerAnnotations = [
        ...januaryMarkers.map((m) => ({
          x: m.move, y: 1.02, yref: "paper", text: m.label, showarrow: false,
          font: { color: "#f97316", size: 9 }, textangle: -45, xanchor: "left"
        })),
        ...monthEndMarkers.map((m) => ({
          x: m.move, y: 1.08, yref: "paper", text: m.label, showarrow: false,
          font: { color: "#14b8a6", size: 9 }, textangle: -45, xanchor: "left"
        }))
      ];

      const layout = {
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: { color: "#f9fafb" },
        title: { text: `${measureLabels[measure]} | ${windowLabels[windowType]} | From ${fromYear}`, font: { size: 14 } },
        xaxis: { title: measureLabels[measure], gridcolor: "#374151", zeroline: measure === "end_move", zerolinecolor: "#4b5563" },
        yaxis: { title: "Density", gridcolor: "#374151" },
        barmode: "overlay",
        legend: { orientation: "h", y: -0.2 },
        margin: { t: 92, r: 20, b: 64, l: 60 },
        shapes: markerShapes,
        annotations: markerAnnotations
      };
      const traces = [
        {
          x: moves, type: "histogram", histnorm: "probability density", opacity: 0.72,
          marker: { color: "#14b8a6" }, nbinsx: Math.min(15, Math.max(5, Math.floor(n / 2))),
          name: "Distribution", hovertemplate: "%{x:.2f} bps<br>Density: %{y:.4f}<extra></extra>"
        },
        {
          x: xGrid, y: yGrid, type: "scatter", mode: "lines", name: "KDE",
          line: { color: "#f59e0b", width: 2 }, hoverinfo: "skip"
        },
        {
          x: moves, y: moves.map(() => -0.002), type: "scatter", mode: "markers",
          marker: { color: "#3b82f6", size: 8, symbol: "line-ns", line: { width: 2 } },
          text: labels, name: "Data Points", hovertemplate: "%{text}: %{x:.2f} bps<extra></extra>"
        }
      ];
      Plotly.react("distro-chart", traces, layout, { responsive: true });
    }

    function initialize() {
      document.getElementById("total-weeks").textContent = weeksData.length;
      document.getElementById("january-weeks").textContent = weeksData.filter(w => w.week_info.is_january).length;
      document.getElementById("month-end-weeks").textContent = weeksData.filter(w => w.week_info.is_month_end).length;
      if (weeksData.length > 0) {
        const years = weeksData.map(w => w.week_info.year);
        document.getElementById("data-range").textContent = `${Math.min(...years)} - ${Math.max(...years)}`;
      } else {
        document.getElementById("data-range").textContent = "No data";
      }

      const yearSel = document.getElementById("distro-year");
      const uniqYears = [...new Set(weeksData.map(w => w.week_info.year))].sort((a, b) => a - b);
      uniqYears.forEach((y) => {
        const opt = document.createElement("option");
        opt.value = String(y);
        opt.textContent = String(y);
        yearSel.appendChild(opt);
      });
      if (uniqYears.length > 0) yearSel.value = String(uniqYears[0]);

      renderWeekCards();
      renderPaths();
      updateDistribution();
    }

    initialize();
  </script>
</body>
</html>
"""


def generate_html(weeks_data: List[Dict]) -> str:
    """Generate standalone dashboard HTML."""
    return (
        HTML_TEMPLATE.replace("__WEEKS_DATA__", json.dumps(weeks_data, indent=2))
        .replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        .replace("__VALID_TY_MIN__", str(VALID_TY_MIN))
        .replace("__VALID_TY_MAX__", str(VALID_TY_MAX))
        .replace("__BPS_PER_POINT__", str(points_to_bps(1.0)))
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 2Y/5Y/7Y supply weeks TY dashboard")
    parser.add_argument("--start-year", type=int, default=2015, help="Start year for auction data")
    parser.add_argument(
        "--min-size",
        type=float,
        default=40_000_000_000,
        help="Minimum auction size to keep standard note auctions and exclude FRN/TIPS reopening rows",
    )
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT_FILE), help="Primary HTML output path")
    parser.add_argument(
        "--dashboards-output",
        type=str,
        default=str(DEFAULT_DASHBOARDS_OUTPUT_FILE),
        help="Optional dashboards mirror output path; pass empty string to skip",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info("Building 2Y/5Y/7Y supply weeks dashboard from %d", args.start_year)
    logger.info("Minimum auction size: %.0f", args.min_size)

    weeks = build_auction_weeks(start_year=args.start_year, min_size=args.min_size)
    if not weeks:
        logger.error("No matched 2Y/5Y/7Y supply weeks found.")
        return

    logger.info("Fetching TY market data for %d weeks...", len(weeks))
    enrich_weeks_with_market_data(weeks)

    html = generate_html(weeks)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written: %s", output_path)

    if args.dashboards_output:
        dashboards_output_path = Path(args.dashboards_output)
        dashboards_output_path.parent.mkdir(parents=True, exist_ok=True)
        dashboards_output_path.write_text(html, encoding="utf-8")
        logger.info("Dashboard mirror written: %s", dashboards_output_path)


if __name__ == "__main__":
    main()
