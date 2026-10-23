#!/usr/bin/env python
"""
3Y/10Y/30Y Auction Weeks Dashboard Generator
---------------------------------------------
Builds a standalone HTML dashboard for TY price behavior around weeks that
contain all three:
  - 3-Year Note Auction
  - 10-Year Note Auction
  - 30-Year Bond Auction

Date anchors are loaded from workspace/economic_events.db and no auction
fundamental fields are used.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2

# Add project root for config import
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER
from rules.treasury_price_conversion import points_to_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

EVENT_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_FILE = PROJECT_ROOT / "scripts" / "ten_thirty_auction_weeks_dashboard.html"
VALID_TY_MIN = 80.0
VALID_TY_MAX = 150.0

THREE_EVENT_NAME = "3-Year Note Auction"
TEN_EVENT_NAME = "10-Year Note Auction"
THIRTY_EVENT_NAME = "30-Year Bond Auction"


def parse_event_dt(value: str) -> Optional[datetime]:
    """Parse economic event timestamp string into datetime."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "")
    for candidate in (raw, raw.replace("T", " ")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    # Last fallback to date-only parse
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def load_auction_events(start_year: int) -> List[Dict]:
    """Load 3Y + 10Y + 30Y auction events from economic_events.db."""
    if not EVENT_DB.exists():
        raise FileNotFoundError(f"Missing event DB: {EVENT_DB}")

    sql = """
        SELECT event_name, event_date
        FROM economic_events
        WHERE event_name IN (?, ?, ?)
          AND event_date IS NOT NULL
          AND event_date <> ''
          AND country LIKE '%United States%'
        ORDER BY event_date ASC
    """

    rows: List[Tuple[str, str]]
    with sqlite3.connect(str(EVENT_DB)) as conn:
        cur = conn.cursor()
        cur.execute(sql, (THREE_EVENT_NAME, TEN_EVENT_NAME, THIRTY_EVENT_NAME))
        rows = cur.fetchall()

    name_to_tenor = {
        THREE_EVENT_NAME: "3Y",
        TEN_EVENT_NAME: "10Y",
        THIRTY_EVENT_NAME: "30Y",
    }
    events: List[Dict] = []
    for name, event_date in rows:
        dt = parse_event_dt(event_date)
        if dt is None or dt.year < start_year:
            continue
        tenor = name_to_tenor.get(name, "10Y")
        events.append({"event_name": name, "tenor": tenor, "dt": dt})

    logger.info("Loaded %d auction events from %s", len(events), EVENT_DB)
    return events


def build_auction_weeks(events: List[Dict]) -> List[Dict]:
    """Create weekly records containing 3Y, 10Y and 30Y auction dates."""
    by_week: Dict[str, Dict] = {}
    for ev in events:
        dt = ev["dt"]
        week_key = f"{dt.year}-W{dt.isocalendar()[1]:02d}"
        slot = by_week.setdefault(
            week_key,
            {
                "year": dt.year,
                "month": dt.month,
                "month_name": dt.strftime("%b"),
                "tenor_dates": {},
            },
        )
        tenor = ev["tenor"]
        prev = slot["tenor_dates"].get(tenor)
        if prev is None or dt < prev:
            slot["tenor_dates"][tenor] = dt

    weeks: List[Dict] = []
    for week_key, data in sorted(by_week.items()):
        three_dt = data["tenor_dates"].get("3Y")
        ten_dt = data["tenor_dates"].get("10Y")
        thirty_dt = data["tenor_dates"].get("30Y")
        if not ten_dt or not thirty_dt:
            continue

        all_dates = [d for d in (three_dt, ten_dt, thirty_dt) if d]
        week_start = min(all_dates)
        week_start = week_start - timedelta(days=week_start.weekday())

        week_info: Dict = {
            "year": data["year"],
            "month": data["month"],
            "month_name": data["month_name"],
            "week_start": week_start.strftime("%Y-%m-%d"),
            "10y_date": ten_dt.strftime("%Y-%m-%d %H:%M"),
            "10y_day": ten_dt.strftime("%a"),
            "30y_date": thirty_dt.strftime("%Y-%m-%d %H:%M"),
            "30y_day": thirty_dt.strftime("%a"),
            "has_3y": three_dt is not None,
        }
        if three_dt:
            week_info["3y_date"] = three_dt.strftime("%Y-%m-%d %H:%M")
            week_info["3y_day"] = three_dt.strftime("%a")
        else:
            week_info["3y_date"] = None
            week_info["3y_day"] = None

        weeks.append({"week_key": week_key, "week_info": week_info})

    logger.info("Constructed %d matched supply weeks (10Y/30Y required, 3Y optional)", len(weeks))
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
        # same bucket
        h = max(h, high)
        l = min(l, low)
        c = close

    flush()
    return out


def trim_candles_to_days(candles_5m: Dict[str, List], keep_days: set[str]) -> Dict[str, List]:
    """Keep only 5m candles whose calendar date is in keep_days."""
    out = {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": []}
    ts = candles_5m.get("timestamps", [])
    opens = candles_5m.get("opens", [])
    highs = candles_5m.get("highs", [])
    lows = candles_5m.get("lows", [])
    closes = candles_5m.get("closes", [])

    for i, ts_val in enumerate(ts):
        day_str = str(ts_val)[:10]
        if day_str not in keep_days:
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
    """Fetch TY market data for a supply-style week window."""
    cur = conn.cursor()
    start_dt = datetime.strptime(week_start, "%Y-%m-%d")
    query_start = start_dt - timedelta(days=1, hours=6)  # Sunday 6pm
    query_end = start_dt + timedelta(days=4, hours=17)   # Friday 5pm

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
            "price_data": {"timestamps": [], "hours": [], "closes": []},
            "candles_5m": {"timestamps": [], "opens": [], "highs": [], "lows": [], "closes": []},
        }

    first_ts = rows[0][0]
    if getattr(first_ts, "tzinfo", None) is not None:
        query_start_aligned = query_start.replace(tzinfo=first_ts.tzinfo)
    else:
        query_start_aligned = query_start

    timestamps = [r[0].isoformat() for r in rows]
    closes = [float(r[4]) for r in rows]
    hours = [round((r[0] - query_start_aligned).total_seconds() / 3600.0, 4) for r in rows]
    candles_5m = aggregate_to_5m(rows)
    return {
        "price_data": {"timestamps": timestamps, "hours": hours, "closes": closes},
        "candles_5m": candles_5m,
    }


def generate_html(weeks_data: List[Dict]) -> str:
    """Generate standalone dashboard HTML."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = json.dumps(weeks_data, indent=2)
    bps_per_point = points_to_bps(1.0)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>3Y/10Y/30Y Supply Weeks - TY Price Paths</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    :root {{
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
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      line-height: 1.45;
    }}
    .container {{ max-width: 1800px; margin: 0 auto; padding: 28px; }}
    header {{ margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border-color); }}
    h1 {{ font-size: 1.9rem; margin-bottom: 8px; }}
    .subtitle {{ color: var(--text-secondary); font-size: 0.98rem; }}
    .stats-bar {{ display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px; }}
    .stat-item {{
      background: var(--bg-secondary);
      padding: 10px 16px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
    }}
    .stat-label {{ color: var(--text-secondary); font-size: 0.82rem; }}
    .stat-value {{ color: var(--accent-teal); font-size: 1.15rem; font-weight: 600; margin-top: 2px; }}
    .chart-container {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 18px;
      margin-bottom: 22px;
    }}
    .chart-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .chart-title {{ font-size: 1.08rem; font-weight: 600; }}
    .badge {{
      background: var(--accent-teal);
      color: #00110f;
      border-radius: 10px;
      padding: 3px 10px;
      font-size: 0.78rem;
      font-weight: 600;
    }}
    .hint-bar {{
      color: var(--text-secondary);
      background: var(--bg-tertiary);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 0.85rem;
      margin-bottom: 10px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .weeks-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(235px, 1fr));
      gap: 12px;
    }}
    .week-card {{
      background: var(--bg-tertiary);
      border: 2px solid var(--border-color);
      border-radius: 8px;
      padding: 10px;
      cursor: pointer;
      transition: border-color 0.2s ease, background 0.2s ease;
    }}
    .week-card:hover {{ border-color: var(--accent-teal); }}
    .week-card.selected {{ border-color: var(--accent-teal); background: rgba(20, 184, 166, 0.12); }}
    .week-top {{ display: flex; justify-content: space-between; align-items: center; }}
    .week-year {{ color: var(--accent-teal); font-weight: 700; font-size: 1.06rem; }}
    .chip {{
      display: inline-block;
      border-radius: 10px;
      padding: 2px 8px;
      font-size: 0.72rem;
      background: rgba(59, 130, 246, 0.2);
      color: #bfdbfe;
    }}
    .week-dates {{ color: var(--text-secondary); font-size: 0.82rem; margin-top: 6px; line-height: 1.45; }}
    .btn {{
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 8px 14px;
      background: var(--bg-tertiary);
      color: var(--text-primary);
      cursor: pointer;
      font-size: 0.88rem;
    }}
    .btn:hover {{ border-color: var(--accent-teal); }}
    .btn[disabled] {{ opacity: 0.45; cursor: not-allowed; }}
    #price-chart {{ width: 100%; height: 560px; }}
    .distro-controls {{
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      margin-bottom: 14px;
      align-items: center;
    }}
    .control-group {{ display: flex; align-items: center; gap: 8px; }}
    .control-group label {{ font-size: 0.9rem; color: var(--text-secondary); }}
    .control-group select {{
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      color: var(--text-primary);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 0.9rem;
    }}
    .distro-grid {{
      display: grid;
      grid-template-columns: 1fr 300px;
      gap: 14px;
    }}
    #distro-chart {{ width: 100%; height: 420px; }}
    .distro-stats {{
      background: var(--bg-tertiary);
      border-radius: 8px;
      padding: 10px;
      border: 1px solid var(--border-color);
    }}
    .distro-stats h4 {{ margin-bottom: 8px; font-size: 0.95rem; }}
    .stat-row {{ display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 0.86rem; }}
    .stat-row .label {{ color: var(--text-secondary); }}
    .stat-row .value {{ color: var(--text-primary); }}
    .positive {{ color: var(--accent-green) !important; }}
    .negative {{ color: var(--accent-red) !important; }}
    .modal {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      z-index: 9999;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .modal.visible {{ display: flex; }}
    .modal-content {{
      width: min(1200px, 96vw);
      max-height: 90vh;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      overflow: hidden;
    }}
    .modal-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-color);
      font-size: 1rem;
      font-weight: 600;
    }}
    .modal-close {{
      border: none;
      background: transparent;
      color: var(--text-secondary);
      font-size: 1.4rem;
      cursor: pointer;
      padding: 2px 8px;
    }}
    .modal-close:hover {{ color: var(--text-primary); }}
    .modal-body {{ padding: 12px; }}
    #candlestick-chart {{ width: 100%; height: 560px; }}
    .generated-info {{
      color: var(--text-secondary);
      text-align: center;
      font-size: 0.8rem;
      margin-top: 18px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>3Y/10Y/30Y Supply Weeks Analysis</h1>
      <p class="subtitle">Date anchors from economic_events DB. Focus: TY market behavior around 3Y, 10Y and 30Y auction windows.</p>
      <div class="stats-bar">
        <div class="stat-item"><div class="stat-label">Total Matched Weeks</div><div class="stat-value" id="total-weeks">-</div></div>
        <div class="stat-item"><div class="stat-label">Data Range</div><div class="stat-value" id="data-range">-</div></div>
        <div class="stat-item"><div class="stat-label">Selected Week</div><div class="stat-value" id="selected-label">None</div></div>
        <div class="stat-item"><div class="stat-label">Window</div><div class="stat-value" id="window-range">Full Week</div></div>
      </div>
    </header>

    <div class="chart-container">
      <div class="chart-header">
        <div style="display:flex; align-items:center; gap:10px;">
          <span class="chart-title">Normalized TY Price Paths (bps from visible window start)</span>
          <span class="badge" id="paths-badge">0 paths</span>
        </div>
        <div style="display:flex; gap:8px;">
          <select id="post10y-filter" onchange="onPost10yFilterChange()" class="btn" style="padding:8px 10px;">
            <option value="all" selected>Post-Auction: All</option>
            <option value="rallied_10y">After 10Y: Rallied</option>
            <option value="soldoff_10y">After 10Y: Sold Off</option>
            <option value="rallied_3y">After 3Y: Rallied</option>
            <option value="soldoff_3y">After 3Y: Sold Off</option>
          </select>
          <button class="btn" onclick="resetZoom()">Reset Zoom</button>
        </div>
      </div>
      <div class="hint-bar">
        <span>Zoom/pan to re-normalize all traces.</span>
        <span>|</span>
        <span>Click a path then open Candlestick Detail.</span>
        <span>|</span>
        <span>Post-auction filter applies to paths and distribution stats.</span>
      </div>
      <div id="price-chart"></div>
    </div>

    <div class="chart-container">
      <div class="chart-header">
        <span class="chart-title">Auction Weeks</span>
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
            <option value="pre_3y">Pre-3Y</option>
            <option value="3y_to_10y">3Y to 10Y</option>
            <option value="10y_to_30y" selected>10Y to 30Y</option>
            <option value="post_30y">Post-30Y</option>
            <option value="auction_day_3y">3Y Auction Day (+/-6h)</option>
            <option value="auction_day_10y">10Y Auction Day (+/-6h)</option>
            <option value="auction_day_30y">30Y Auction Day (+/-6h)</option>
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
      </div>
      <div class="hint-bar">
        <span>Histogram and stats are computed from selected year onward and the active post-auction filter.</span>
        <span>|</span>
      </div>
      <div class="distro-grid">
        <div id="distro-chart"></div>
        <div class="distro-stats">
          <h4>All Weeks Stats (bps)</h4>
          <div class="stat-row"><span class="label">N (all)</span><span class="value" id="stat-n">-</span></div>
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

    <p class="generated-info">Generated: {generated_at} | Source: economic_events 3Y/10Y/30Y auction dates | TY range filter: [{VALID_TY_MIN}, {VALID_TY_MAX}]</p>
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
    const weeksData = {payload};
    let lastClickedWeek = null;
    let currentWindowStart = null;
    let currentWindowEnd = null;

    const BPS_PER_POINT = {bps_per_point};

    function pointsToBps(points) {{
      return points * BPS_PER_POINT;
    }}

    function hoursToTimeStr(hours) {{
      if (hours === null || hours === undefined) return "N/A";
      const day = Math.floor(hours / 24);
      const h = Math.floor(hours % 24);
      const m = Math.round((hours - Math.floor(hours)) * 60);
      const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"];
      return `${{dayNames[Math.max(0, Math.min(dayNames.length - 1, day))]}} ${{String(h).padStart(2, "0")}}:${{String(m).padStart(2, "0")}}`;
    }}

    function parseAuctionHours(week) {{
      const info = week.week_info;
      const out = {{}};
      ["3y", "10y", "30y"].forEach((tenor) => {{
        const key = `${{tenor}}_date`;
        const dateStr = info[key];
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
        const hours = 6 + (dayOffset * 24) + hh + (mm / 60.0); // sunday 6pm = 0
        out[tenor] = hours;
      }});
      return out;
    }}

    function getWindowBounds(week, windowType) {{
      const auc = parseAuctionHours(week);
      const auc3 = auc["3y"] ?? 43.0;     // fallback Tue ~1pm
      const auc10 = auc["10y"] ?? 67.0;   // fallback Wed ~1pm
      const auc30 = auc["30y"] ?? 91.0;   // fallback Thu ~1pm
      switch (windowType) {{
        case "full": return {{ start: 0, end: 119 }};
        case "pre_3y": return {{ start: 0, end: auc3 }};
        case "3y_to_10y": return {{ start: auc3, end: auc10 }};
        case "10y_to_30y": return {{ start: auc10, end: auc30 }};
        case "post_30y": return {{ start: auc30, end: 119 }};
        case "auction_day_3y": return {{ start: auc3 - 6, end: auc3 + 6 }};
        case "auction_day_10y": return {{ start: auc10 - 6, end: auc10 + 6 }};
        case "auction_day_30y": return {{ start: auc30 - 6, end: auc30 + 6 }};
        default: return {{ start: 0, end: 119 }};
      }}
    }}

    function renderWeekCards() {{
      const grid = document.getElementById("weeks-grid");
      grid.innerHTML = "";
      const filterValue = document.getElementById("post10y-filter").value;
      let selectedStillVisible = false;
      weeksData.forEach((week, idx) => {{
        if (!matchesPost10yFilter(week, filterValue)) return;
        const card = document.createElement("div");
        const selClass = lastClickedWeek === idx ? "selected" : "";
        if (selClass) selectedStillVisible = true;
        card.className = `week-card ${{selClass}}`;
        const chip = week.week_info.has_3y
          ? `<span class="chip" style="background:rgba(34,197,94,0.2);color:#86efac;">3Y+10Y+30Y</span>`
          : `<span class="chip" style="background:rgba(249,115,22,0.2);color:#fdba74;">10Y+30Y only</span>`;
        const has3y = week.week_info["3y_date"] ? `3Y: ${{week.week_info["3y_day"]}} (${{week.week_info["3y_date"]}})<br/>` : "";
        card.innerHTML = `
          <div class="week-top">
            <div class="week-year">${{week.week_info.year}} ${{week.week_info.month_name}}</div>
            ${{chip}}
          </div>
          <div class="week-dates">
            Week of ${{week.week_info.week_start}}<br/>
            ${{has3y}}
            10Y: ${{week.week_info["10y_day"] || "-"}} (${{week.week_info["10y_date"] || "-"}})<br/>
            30Y: ${{week.week_info["30y_day"] || "-"}} (${{week.week_info["30y_date"] || "-"}})
          </div>
        `;
        card.onclick = () => {{
          lastClickedWeek = idx;
          document.getElementById("selected-label").textContent = `${{week.week_info.year}}-${{week.week_info.month_name}}`;
          document.getElementById("btn-candle").disabled = false;
          renderWeekCards();
          renderPaths();
        }};
        grid.appendChild(card);
      }});
      if (!selectedStillVisible) {{
        lastClickedWeek = null;
        document.getElementById("selected-label").textContent = "None";
        document.getElementById("btn-candle").disabled = true;
      }}
    }}

    function resetZoom() {{
      currentWindowStart = null;
      currentWindowEnd = null;
      renderPaths();
    }}

    function matchesPost10yFilter(week, filterValue) {{
      if (filterValue === "all") return true;
      const auc = parseAuctionHours(week);
      let aucHour = null;
      if (filterValue.endsWith("_10y")) {{
        aucHour = auc["10y"];
      }} else if (filterValue.endsWith("_3y")) {{
        aucHour = auc["3y"];
        if (aucHour === undefined || aucHour === null) return false;
      }}
      if (aucHour === undefined || aucHour === null) return false;
      const bounds = {{ start: aucHour, end: aucHour + 0.5 }};
      const postMove = calculateMove(week, bounds, "end_move");
      if (postMove === null || Number.isNaN(postMove)) return false;
      if (filterValue.startsWith("rallied")) return postMove > 0;
      if (filterValue.startsWith("soldoff")) return postMove < 0;
      return true;
    }}

    function onPost10yFilterChange() {{
      renderWeekCards();
      renderPaths();
      updateDistribution();
    }}

    function renderPaths() {{
      const colors = [
        "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
        "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
        "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000"
      ];
      const traces = [];
      let count = 0;
      const filterValue = document.getElementById("post10y-filter").value;
      weeksData.forEach((week, idx) => {{
        if (!matchesPost10yFilter(week, filterValue)) return;
        const pd = week.price_data;
        if (!pd || !pd.hours || !pd.closes || pd.hours.length < 2) return;
        let arr = pd.hours.map((h, i) => {{ return {{ h, c: pd.closes[i] }}; }});
        if (currentWindowStart !== null && currentWindowEnd !== null) {{
          arr = arr.filter(d => d.h >= currentWindowStart && d.h <= currentWindowEnd);
        }}
        if (arr.length < 2) return;
        const ref = arr[0].c;
        const x = arr.map(d => d.h / 24.0);
        const y = arr.map(d => pointsToBps(d.c - ref));
        const selected = idx === lastClickedWeek;
        const baseColor = colors[count % colors.length];
        traces.push({{
          x,
          y,
          type: "scatter",
          mode: "lines",
          name: `${{week.week_info.year}} ${{week.week_info.month_name}}`,
          opacity: selected ? 1.0 : 0.65,
          line: {{ color: baseColor, width: selected ? 2.6 : 1.1 }},
          customdata: [idx],
          hovertemplate: `${{week.week_info.year}} ${{week.week_info.month_name}}<br>Day: %{{x:.2f}}<br>Move: %{{y:.2f}} bps<extra></extra>`
        }});
        count += 1;
      }});

      document.getElementById("paths-badge").textContent = `${{count}} paths`;
      if (currentWindowStart !== null && currentWindowEnd !== null) {{
        document.getElementById("window-range").textContent = `${{hoursToTimeStr(currentWindowStart)}} -> ${{hoursToTimeStr(currentWindowEnd)}}`;
      }} else {{
        document.getElementById("window-range").textContent = "Full Week";
      }}

      const layout = {{
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: {{ color: "#f9fafb" }},
        xaxis: {{
          title: "Days from Week Start (0 = Sunday 6pm)",
          gridcolor: "#374151",
          range: currentWindowStart !== null ? [currentWindowStart / 24.0, currentWindowEnd / 24.0] : undefined,
          tickvals: [0, 1, 2, 3, 4, 5],
          ticktext: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
        }},
        yaxis: {{
          title: "Move (bps from visible window start)",
          gridcolor: "#374151",
          zeroline: true,
          zerolinecolor: "#4b5563"
        }},
        showlegend: true,
        legend: {{ orientation: "h", y: -0.14 }},
        margin: {{ t: 20, r: 24, b: 72, l: 62 }},
        dragmode: "zoom",
        hovermode: "closest"
      }};
      const config = {{ responsive: true, scrollZoom: true, displayModeBar: true }};

      Plotly.react("price-chart", traces, layout, config).then(() => {{
        const chart = document.getElementById("price-chart");
        chart.removeAllListeners("plotly_relayout");
        chart.on("plotly_relayout", handleRelayout);
        chart.removeAllListeners("plotly_click");
        chart.on("plotly_click", handleClick);
      }});
    }}

    function handleRelayout(eventData) {{
      let s = null;
      let e = null;
      if (eventData["xaxis.range"]) {{
        s = eventData["xaxis.range"][0];
        e = eventData["xaxis.range"][1];
      }} else if (eventData["xaxis.range[0]"] !== undefined) {{
        s = eventData["xaxis.range[0]"];
        e = eventData["xaxis.range[1]"];
      }} else if (eventData["xaxis.autorange"]) {{
        currentWindowStart = null;
        currentWindowEnd = null;
        renderPaths();
        return;
      }} else {{
        return;
      }}
      const newStart = s * 24.0;
      const newEnd = e * 24.0;
      if (newEnd - newStart < 1.0) return;
      const delta = 0.5;
      const changed =
        currentWindowStart === null ||
        currentWindowEnd === null ||
        Math.abs(currentWindowStart - newStart) > delta ||
        Math.abs(currentWindowEnd - newEnd) > delta;
      if (changed) {{
        currentWindowStart = newStart;
        currentWindowEnd = newEnd;
        renderPaths();
      }}
    }}

    function handleClick(clickData) {{
      if (!clickData.points || clickData.points.length === 0) return;
      const point = clickData.points[0];
      const idx = Array.isArray(point.customdata) ? point.customdata[0] : null;
      if (idx === null || idx === undefined) return;
      lastClickedWeek = idx;
      const w = weeksData[idx];
      document.getElementById("selected-label").textContent = `${{w.week_info.year}}-${{w.week_info.month_name}}`;
      document.getElementById("btn-candle").disabled = false;
      renderWeekCards();
      renderPaths();
    }}

    function openCandlestickModal() {{
      if (lastClickedWeek === null) return;
      const w = weeksData[lastClickedWeek];
      const c = w.candles_5m;
      if (!c || !c.timestamps || c.timestamps.length === 0) return;
      const parts = [`${{w.week_info.year}} ${{w.week_info.month_name}}`];
      if (w.week_info["3y_date"]) parts.push(`3Y ${{w.week_info["3y_date"]}}`);
      parts.push(`10Y ${{w.week_info["10y_date"]}}`);
      parts.push(`30Y ${{w.week_info["30y_date"]}}`);
      document.getElementById("modal-title").textContent = parts.join(" | ");

      const day3y = (w.week_info["3y_date"] || "").slice(0, 10);
      const day10y = (w.week_info["10y_date"] || "").slice(0, 10);
      const day30y = (w.week_info["30y_date"] || "").slice(0, 10);
      const keepDays = new Set([day3y, day10y, day30y].filter(Boolean));
      const keepIdx = [];
      for (let i = 0; i < c.timestamps.length; i++) {{
        const tsDay = String(c.timestamps[i]).slice(0, 10);
        if (keepDays.has(tsDay)) keepIdx.push(i);
      }}
      if (keepIdx.length === 0) return;

      const x = keepIdx.map(i => c.timestamps[i]);
      const open = keepIdx.map(i => c.opens[i]);
      const high = keepIdx.map(i => c.highs[i]);
      const low = keepIdx.map(i => c.lows[i]);
      const close = keepIdx.map(i => c.closes[i]);

      const trace = {{
        x: x,
        open: open,
        high: high,
        low: low,
        close: close,
        type: "candlestick",
        increasing: {{ line: {{ color: "#22c55e" }} }},
        decreasing: {{ line: {{ color: "#ef4444" }} }},
        name: "TY 5m"
      }};
      const layout = {{
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: {{ color: "#f9fafb" }},
        xaxis: {{ title: "Time", gridcolor: "#374151", rangeslider: {{ visible: false }} }},
        yaxis: {{ title: "TY Price", gridcolor: "#374151" }},
        margin: {{ t: 30, r: 24, b: 46, l: 62 }}
      }};
      Plotly.react("candlestick-chart", [trace], layout, {{ responsive: true }});
      document.getElementById("candlestick-modal").classList.add("visible");
    }}

    function closeCandlestickModal() {{
      document.getElementById("candlestick-modal").classList.remove("visible");
    }}

    document.getElementById("candlestick-modal").onclick = (e) => {{
      if (e.target.classList.contains("modal")) closeCandlestickModal();
    }};
    document.addEventListener("keydown", (e) => {{
      if (e.key === "Escape") closeCandlestickModal();
    }});

    function calculateMove(week, bounds, measureType) {{
      const pd = week.price_data;
      if (!pd || !pd.hours || !pd.closes || pd.hours.length < 2) return null;
      const arr = pd.hours.map((h, i) => {{ return {{ h, c: pd.closes[i] }}; }})
        .filter(d => d.h >= bounds.start && d.h <= bounds.end);
      if (arr.length < 2) return null;
      const start = arr[0].c;
      let high = start;
      let low = start;
      let maxUp = 0;
      let maxDown = 0;
      for (const p of arr) {{
        const m = pointsToBps(p.c - start);
        if (m > maxUp) maxUp = m;
        if (m < maxDown) maxDown = m;
        if (p.c > high) high = p.c;
        if (p.c < low) low = p.c;
      }}
      const endMove = pointsToBps(arr[arr.length - 1].c - start);
      const range = pointsToBps(high - low);
      const maxAbs = Math.max(Math.abs(maxUp), Math.abs(maxDown));
      switch (measureType) {{
        case "max_move": return maxAbs;
        case "end_move": return endMove;
        case "max_up": return maxUp;
        case "max_down": return Math.abs(maxDown);
        case "range": return range;
        default: return maxAbs;
      }}
    }}

    function gaussianKDE(data) {{
      const n = data.length;
      if (!n) return () => 0;
      const mean = data.reduce((a, b) => a + b, 0) / n;
      const std = Math.sqrt(data.reduce((s, v) => s + (v - mean) ** 2, 0) / n) || 1;
      const bw = std * Math.pow(n, -0.2);
      return function(x) {{
        let sum = 0;
        for (let i = 0; i < n; i++) {{
          const z = (x - data[i]) / bw;
          sum += Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
        }}
        return sum / (n * bw);
      }};
    }}

    function clearDistroStats() {{
      ["stat-n","stat-mean","stat-median","stat-std","stat-min","stat-p5","stat-p25","stat-p75","stat-p95","stat-max","stat-zero-pct","stat-exp-rally","stat-exp-selloff"]
        .forEach((id) => {{ document.getElementById(id).textContent = "-"; }});
    }}

    function updateDistribution() {{
      const windowType = document.getElementById("distro-window").value;
      const fromYear = Number(document.getElementById("distro-year").value || 2015);
      const measure = document.getElementById("distro-measure").value;
      const filterValue = document.getElementById("post10y-filter").value;
      const moves = [];
      const labels = [];
      for (const w of weeksData) {{
        if (w.week_info.year < fromYear) continue;
        if (!matchesPost10yFilter(w, filterValue)) continue;
        const bounds = getWindowBounds(w, windowType);
        const move = calculateMove(w, bounds, measure);
        if (move === null || Number.isNaN(move)) continue;
        const lbl = `${{w.week_info.year}} ${{w.week_info.month_name}}`;
        moves.push(move);
        labels.push(lbl);
      }}
      document.getElementById("distro-badge").textContent = `${{moves.length}} weeks`;
      if (moves.length === 0) {{
        document.getElementById("distro-chart").innerHTML =
          '<div style="text-align:center;padding:60px;color:#9ca3af;">No data for selected filters</div>';
        clearDistroStats();
        return;
      }}
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
      document.getElementById("stat-zero-pct").textContent = `${{zeroPct.toFixed(1)}}%`;

      // Expected rally/selloff in bps:
      // E[rally] = P(move>0) * avg(move | move>0)
      // E[selloff] = P(move<0) * avg(abs(move) | move<0)
      const rallyMoves = moves.filter(v => v > 0);
      const selloffMovesAbs = moves.filter(v => v < 0).map(v => Math.abs(v));
      const rallyProb = rallyMoves.length / n;
      const selloffProb = selloffMovesAbs.length / n;
      const rallyAvg = rallyMoves.length ? (rallyMoves.reduce((a, b) => a + b, 0) / rallyMoves.length) : 0;
      const selloffAvg = selloffMovesAbs.length ? (selloffMovesAbs.reduce((a, b) => a + b, 0) / selloffMovesAbs.length) : 0;
      const expectedRally = rallyProb * rallyAvg;
      const expectedSelloff = selloffProb * selloffAvg;
      document.getElementById("stat-exp-rally").textContent = `${{expectedRally.toFixed(2)}} bps`;
      document.getElementById("stat-exp-selloff").textContent = `${{expectedSelloff.toFixed(2)}} bps`;

      const meanEl = document.getElementById("stat-mean");
      meanEl.classList.remove("positive", "negative");
      if (measure === "end_move") meanEl.classList.add(mean >= 0 ? "positive" : "negative");

      const kde = gaussianKDE(moves);
      const xRange = max - min || 1;
      const xPad = xRange * 0.2;
      const xGrid = [];
      const yGrid = [];
      for (let i = 0; i < 200; i++) {{
        const x = min - xPad + ((xRange + (2 * xPad)) * i / 199);
        xGrid.push(x);
        yGrid.push(kde(x));
      }}

      const measureLabels = {{
        max_move: "Max Move (bps)",
        end_move: "End-to-End Move (bps)",
        max_up: "Max Up Move (bps)",
        max_down: "Max Down Move (bps)",
        range: "High-Low Range (bps)"
      }};
      const windowLabels = {{
        full: "Full Week",
        pre_3y: "Pre-3Y",
        "3y_to_10y": "3Y -> 10Y",
        "10y_to_30y": "10Y -> 30Y",
        post_30y: "Post-30Y",
        auction_day_3y: "3Y Day (+/-6h)",
        auction_day_10y: "10Y Day (+/-6h)",
        auction_day_30y: "30Y Day (+/-6h)"
      }};

      const layout = {{
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: {{ color: "#f9fafb" }},
        title: {{ text: `${{measureLabels[measure]}} | ${{windowLabels[windowType]}} | From ${{fromYear}}`, font: {{ size: 14 }} }},
        xaxis: {{ title: measureLabels[measure], gridcolor: "#374151", zeroline: measure === "end_move", zerolinecolor: "#4b5563" }},
        yaxis: {{ title: "Density", gridcolor: "#374151" }},
        barmode: "overlay",
        legend: {{ orientation: "h", y: -0.2 }},
        margin: {{ t: 82, r: 20, b: 64, l: 60 }}
      }};
      const traces = [
        {{
          x: moves, type: "histogram", histnorm: "probability density", opacity: 0.72,
          marker: {{ color: "#14b8a6" }}, nbinsx: Math.min(15, Math.max(5, Math.floor(n / 2))),
          name: "Distribution", hovertemplate: "%{{x:.2f}} bps<br>Density: %{{y:.4f}}<extra></extra>"
        }},
        {{
          x: xGrid, y: yGrid, type: "scatter", mode: "lines", name: "KDE",
          line: {{ color: "#f59e0b", width: 2 }}, hoverinfo: "skip"
        }},
        {{
          x: moves, y: moves.map(() => -0.002), type: "scatter", mode: "markers",
          marker: {{ color: "#3b82f6", size: 8, symbol: "line-ns", line: {{ width: 2 }} }},
          text: labels, name: "Data Points", hovertemplate: "%{{text}}: %{{x:.2f}} bps<extra></extra>"
        }}
      ];
      Plotly.react("distro-chart", traces, layout, {{ responsive: true }});
    }}

    function initialize() {{
      document.getElementById("total-weeks").textContent = weeksData.length;
      if (weeksData.length > 0) {{
        const years = weeksData.map(w => w.week_info.year);
        document.getElementById("data-range").textContent = `${{Math.min(...years)}} - ${{Math.max(...years)}}`;
      }} else {{
        document.getElementById("data-range").textContent = "No data";
      }}

      const yearSel = document.getElementById("distro-year");
      const uniqYears = [...new Set(weeksData.map(w => w.week_info.year))].sort((a, b) => a - b);
      uniqYears.forEach((y) => {{
        const opt = document.createElement("option");
        opt.value = String(y);
        opt.textContent = String(y);
        yearSel.appendChild(opt);
      }});
      if (uniqYears.length > 0) {{
        yearSel.value = String(uniqYears[0]);
      }}

      renderWeekCards();
      renderPaths();
      updateDistribution();
    }}

    initialize();
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 3Y/10Y/30Y auction weeks TY dashboard")
    parser.add_argument("--start-year", type=int, default=2022, help="Start year for auction events")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info("Building 3Y/10Y/30Y auction weeks dashboard from %d", args.start_year)

    events = load_auction_events(start_year=args.start_year)
    weeks = build_auction_weeks(events)
    if not weeks:
        logger.error("No matched 10Y/30Y weeks found.")
        return

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )
    try:
        logger.info("Fetching TY market data for %d weeks...", len(weeks))
        for i, week in enumerate(weeks, start=1):
            wk = week["week_info"]["week_start"]
            market = fetch_market_data_for_week(wk, conn)

            day_3y = str(week["week_info"].get("3y_date", "") or "")[:10]
            day_10y = str(week["week_info"].get("10y_date", ""))[:10]
            day_30y = str(week["week_info"].get("30y_date", ""))[:10]
            keep_days = {d for d in (day_3y, day_10y, day_30y) if d}
            market["candles_5m"] = trim_candles_to_days(market["candles_5m"], keep_days)
            # price_data.timestamps are not used by current plotting/stat logic; drop to reduce HTML size.
            market["price_data"].pop("timestamps", None)

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

    html = generate_html(weeks)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    logger.info("Dashboard written: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
