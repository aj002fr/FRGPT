#!/usr/bin/env python
"""
7-Year Note Auction Z-Score Dispersion Dashboard
-------------------------------------------------
Buckets historical 7Y auctions by z-score of auction metrics (Dealer%,
Bid/Cover, Direct%, Indirect%) and plots post-auction TY return
distributions per bucket.

Usage:
    python scripts/seven_year_auction_zscore_dashboard.py
    python scripts/seven_year_auction_zscore_dashboard.py --start-year 2015
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER
from rules.treasury_price_conversion import points_to_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = PROJECT_ROOT / "auction_data" / "7y_note_processed.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "scenarios"
PLOTLY_CACHE = OUTPUT_DIR / "_assets" / "plotly-2.27.0.min.js"
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.27.0.min.js"
VALID_TY = (80.0, 150.0)

# 7Y auction results at 1:00 PM ET
AUCTION_HOUR = 13

HORIZONS = [
    ("t+30m", 30),
    ("t+1h", 60),
    ("t+2h", 120),
    ("t+4h", 240),
]

# Today's auction parameters
TODAY = {
    "date": "02/25/2026",
    "stop": 3.615,
    "wi": 3.608,
    "tail_bps": 0.7,
    "bid_cover": 2.32,
    "direct_pct": 24.7,
    "indirect_pct": 62.5,
    "dealer_pct": 12.8,
}

# ────────────────────────────────────────────────────────────────────
# CSV parsing
# ────────────────────────────────────────────────────────────────────

def _pf(val: str) -> Optional[float]:
    v = val.strip().replace("%", "").replace(",", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_money(val: str) -> Optional[float]:
    v = val.strip().replace("$", "").replace(",", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_date(val: str) -> Optional[datetime]:
    val = val.strip()
    if not val:
        return None
    try:
        return datetime.strptime(val, "%m/%d/%Y")
    except ValueError:
        return None


def load_auctions() -> List[Dict[str, Any]]:
    rows: list[dict] = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            dt = _parse_date(r.get("Auction", ""))
            dp = _pf(r.get("Dealer%", ""))
            if dt is None or dp is None:
                continue
            rows.append({
                "date": dt,
                "date_str": dt.strftime("%m/%d/%Y"),
                "dealer_pct": dp,
                "direct_pct": _pf(r.get("Direct%", "")),
                "indirect_pct": _pf(r.get("Indirect%", "")),
                "bid_cover": _pf(r.get("Bid/Cover", "")),
                "stop": _pf(r.get("Stop", "")),
                "coupon": _pf(r.get("Coupon", "")),
                "tail": _pf(r.get("Tail", "")),
                "dlr_hit": _pf(r.get("Dlr Hit", "")),
                "dir_hit": _pf(r.get("Dir Hit", "")),
                "indir_hit": _pf(r.get("Indir Hit", "")),
                "size": _parse_money(r.get("Size", "")),
            })
    logger.info("Loaded %d auctions with dealer data", len(rows))
    return rows


# ────────────────────────────────────────────────────────────────────
# Z-score computation
# ────────────────────────────────────────────────────────────────────

ROLLING_WINDOW = 24  # trailing auctions for z-score reference (~2 years)


def compute_zscores(auctions: List[Dict], start_year: int) -> List[Dict]:
    """Add rolling z-scores for key metrics using a trailing window of
    ROLLING_WINDOW auctions so buckets reflect the current regime."""

    metrics = ["dealer_pct", "bid_cover", "direct_pct", "indirect_pct"]
    all_sorted = sorted(auctions, key=lambda a: a["date"])
    filtered = [a for a in all_sorted if a["date"].year >= start_year]

    for i, a in enumerate(all_sorted):
        if a["date"].year < start_year:
            continue
        idx = all_sorted.index(a)
        lookback = all_sorted[max(0, idx - ROLLING_WINDOW):idx]

        for m in metrics:
            prior = [x[m] for x in lookback if x[m] is not None]
            val = a[m]
            if val is not None and len(prior) >= 5:
                mu = statistics.mean(prior)
                sd = statistics.stdev(prior)
                a[f"z_{m}"] = (val - mu) / sd if sd > 0 else 0.0
                a[f"avg_{m}"] = mu
                a[f"sd_{m}"] = sd
            else:
                a[f"z_{m}"] = None
                a[f"avg_{m}"] = None
                a[f"sd_{m}"] = None

        zd = a.get("z_dealer_pct")
        zbc = a.get("z_bid_cover")
        if zd is not None and zbc is not None:
            a["z_composite"] = (zd - zbc) / 2.0
        else:
            a["z_composite"] = None

    return [a for a in filtered if a.get("z_dealer_pct") is not None]


# ────────────────────────────────────────────────────────────────────
# Market data fetching
# ────────────────────────────────────────────────────────────────────

def fetch_post_auction_returns(
    auctions: List[Dict],
    conn,
) -> List[Dict]:
    """Fetch TY returns post-auction for each historical auction."""
    cur = conn.cursor()
    for a in auctions:
        dt = a["date"]
        auction_ts = dt.replace(hour=AUCTION_HOUR, minute=0, second=0)
        end_ts = auction_ts + timedelta(minutes=max(h[1] for h in HORIZONS) + 10)

        cur.execute(
            """
            SELECT ts_event, close
            FROM ohlcv_1m
            WHERE symbol = 'TY'
              AND ts_event >= %s AND ts_event <= %s
              AND close >= %s AND close <= %s
            ORDER BY ts_event
            """,
            (auction_ts, end_ts, VALID_TY[0], VALID_TY[1]),
        )
        rows = cur.fetchall()
        if not rows:
            for label, _ in HORIZONS:
                a[label] = None
            a["path_minutes"] = []
            a["path_bps"] = []
            continue

        ref = float(rows[0][1])
        price_map: Dict[int, float] = {}
        for ts, close in rows:
            offset = int((ts.replace(tzinfo=None) - auction_ts).total_seconds() / 60)
            price_map[offset] = float(close)

        for label, mins in HORIZONS:
            best_off = None
            best_diff = 999
            for off in price_map:
                d = abs(off - mins)
                if d < best_diff:
                    best_diff = d
                    best_off = off
            if best_off is not None and best_diff <= 5:
                a[label] = points_to_bps(price_map[best_off] - ref)
            else:
                a[label] = None

        # Minute-by-minute path (for path overlay)
        path_min = sorted(price_map.keys())
        a["path_minutes"] = path_min
        a["path_bps"] = [points_to_bps(price_map[m] - ref) for m in path_min]

    return auctions


# ────────────────────────────────────────────────────────────────────
# Bucketing
# ────────────────────────────────────────────────────────────────────

def bucket_label(z: float) -> str:
    if z <= -1.0:
        return "Strong (z<-1)"
    elif z <= 0.0:
        return "Avg-Strong (-1<z<0)"
    elif z <= 1.0:
        return "Avg-Weak (0<z<1)"
    else:
        return "Weak (z>1)"


BUCKET_ORDER = [
    "Strong (z<-1)",
    "Avg-Strong (-1<z<0)",
    "Avg-Weak (0<z<1)",
    "Weak (z>1)",
]


def assign_buckets(auctions: List[Dict], metric: str) -> List[Dict]:
    for a in auctions:
        z = a.get(f"z_{metric}") if metric != "composite" else a.get("z_composite")
        if z is not None:
            a["bucket"] = bucket_label(z)
            a["z_selected"] = z
        else:
            a["bucket"] = None
            a["z_selected"] = None
    return auctions


# ────────────────────────────────────────────────────────────────────
# HTML generation
# ────────────────────────────────────────────────────────────────────

def build_payload(auctions: List[Dict]) -> List[Dict]:
    """Serialize auctions for JSON embedding."""
    out = []
    for a in auctions:
        if a.get("z_dealer_pct") is None:
            continue
        rec = {
            "date": a["date_str"],
            "year": a["date"].year,
            "dealer_pct": a["dealer_pct"],
            "direct_pct": a["direct_pct"],
            "indirect_pct": a["indirect_pct"],
            "bid_cover": a["bid_cover"],
            "stop": a["stop"],
            "z_dealer_pct": round(a.get("z_dealer_pct") or 0, 3),
            "z_bid_cover": round(a.get("z_bid_cover") or 0, 3),
            "z_direct_pct": round(a.get("z_direct_pct") or 0, 3),
            "z_indirect_pct": round(a.get("z_indirect_pct") or 0, 3),
            "z_composite": round(a.get("z_composite") or 0, 3),
            "avg_dealer_pct": round(a.get("avg_dealer_pct") or 0, 2),
        }
        for label, _ in HORIZONS:
            rec[label] = round(a[label], 2) if a.get(label) is not None else None
        rec["path_minutes"] = a.get("path_minutes", [])
        rec["path_bps"] = [round(v, 2) for v in a.get("path_bps", [])]
        out.append(rec)
    return out


def _plotly_script_tag() -> str:
    """Return a <script> tag with inlined Plotly if cached, else CDN fallback."""
    if PLOTLY_CACHE.exists():
        js = PLOTLY_CACHE.read_text(encoding="utf-8")
        return f"<script>{js}</script>"
    return f'<script src="{PLOTLY_CDN}"></script>'


def generate_html(payload: List[Dict], today_info: Dict) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_json = json.dumps(payload)
    today_json = json.dumps(today_info)
    bps_per_pt = points_to_bps(1.0)
    plotly_tag = _plotly_script_tag()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>7Y Auction Z-Score Dispersion Dashboard</title>
{plotly_tag}
<style>
:root {{
  --bg-primary: #0a0f1a; --bg-secondary: #111827; --bg-tertiary: #1f2937;
  --text-primary: #f9fafb; --text-secondary: #9ca3af; --border-color: #374151;
  --accent-teal: #14b8a6; --accent-green: #22c55e; --accent-orange: #f97316;
  --accent-blue: #3b82f6; --accent-red: #ef4444; --accent-purple: #a855f7;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg-primary); color: var(--text-primary);
  min-height: 100vh; line-height: 1.45;
}}
.container {{ max-width: 1850px; margin: 0 auto; padding: 24px; }}
header {{ margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--border-color); }}
h1 {{ font-size: 1.8rem; margin-bottom: 6px; }}
.subtitle {{ color: var(--text-secondary); font-size: 0.92rem; }}
.controls {{
  display: flex; gap: 14px; flex-wrap: wrap; align-items: center;
  margin-bottom: 20px; padding: 14px; background: var(--bg-secondary);
  border: 1px solid var(--border-color); border-radius: 10px;
}}
.controls label {{ color: var(--text-secondary); font-size: 0.85rem; margin-right: 4px; }}
.controls select {{
  background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border-color);
  border-radius: 6px; padding: 6px 10px; font-size: 0.9rem;
}}
.today-bar {{
  display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 20px;
  padding: 14px 18px; background: var(--bg-secondary); border: 1px solid var(--accent-teal);
  border-radius: 10px;
}}
.today-item {{ text-align: center; min-width: 90px; }}
.today-label {{ color: var(--text-secondary); font-size: 0.78rem; }}
.today-value {{ color: var(--accent-teal); font-size: 1.1rem; font-weight: 600; margin-top: 2px; }}
.today-value.neg {{ color: var(--accent-red); }}
.today-value.pos {{ color: var(--accent-green); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 18px; margin-bottom: 22px; }}
.card {{
  background: var(--bg-secondary); border: 1px solid var(--border-color);
  border-radius: 10px; padding: 16px;
}}
.card-title {{ font-size: 1.0rem; font-weight: 600; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }}
.badge {{
  font-size: 0.78rem; padding: 2px 10px; border-radius: 8px;
  color: #000; font-weight: 600;
}}
.badge-strong {{ background: var(--accent-green); }}
.badge-avg-strong {{ background: var(--accent-blue); color: #fff; }}
.badge-avg-weak {{ background: var(--accent-orange); }}
.badge-weak {{ background: var(--accent-red); color: #fff; }}
.stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 8px; }}
.stat-box {{ background: var(--bg-tertiary); border-radius: 6px; padding: 6px 8px; text-align: center; }}
.stat-label {{ color: var(--text-secondary); font-size: 0.72rem; }}
.stat-val {{ font-size: 0.92rem; font-weight: 600; margin-top: 1px; }}
.wide-card {{
  background: var(--bg-secondary); border: 1px solid var(--border-color);
  border-radius: 10px; padding: 16px; margin-bottom: 22px;
}}
table {{
  width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 10px;
}}
th, td {{ padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--border-color); }}
th {{ color: var(--text-secondary); font-weight: 500; position: sticky; top: 0; background: var(--bg-secondary); }}
td:first-child, th:first-child {{ text-align: left; }}
tr:hover {{ background: var(--bg-tertiary); }}
tr.today-row {{ background: rgba(20, 184, 166, 0.15); font-weight: 600; }}
.positive {{ color: var(--accent-green); }}
.negative {{ color: var(--accent-red); }}
.scroll-wrap {{ max-height: 420px; overflow-y: auto; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>7Y Note Auction &mdash; Z-Score Dispersion Dashboard</h1>
    <p class="subtitle">Post-auction TY return distributions bucketed by auction metric z-scores &bull; Generated {generated}</p>
  </header>

  <!-- Today's Auction Bar -->
  <div class="today-bar" id="today-bar"></div>

  <!-- Controls -->
  <div class="controls">
    <div>
      <label>Bucket By:</label>
      <select id="sel-metric" onchange="rebuild()">
        <option value="dealer_pct" selected>Dealer %</option>
        <option value="bid_cover">Bid/Cover</option>
        <option value="direct_pct">Direct %</option>
        <option value="indirect_pct">Indirect %</option>
        <option value="composite">Composite (Dealer - B/C)</option>
      </select>
    </div>
    <div>
      <label>Horizon:</label>
      <select id="sel-horizon" onchange="rebuild()">
        <option value="t+30m">t+30m</option>
        <option value="t+1h" selected>t+1h</option>
        <option value="t+2h">t+2h</option>
        <option value="t+4h">t+4h</option>
      </select>
    </div>
    <div>
      <label>From Year:</label>
      <select id="sel-year" onchange="rebuild()">
        <option value="2009">2009</option>
        <option value="2012">2012</option>
        <option value="2015" selected>2015</option>
        <option value="2018">2018</option>
        <option value="2020">2020</option>
        <option value="2022">2022</option>
      </select>
    </div>
    <div>
      <label>Z-sign convention:</label>
      <span style="color:var(--text-secondary);font-size:0.82rem;">&nbsp;Dealer%: +z = more dealer take (weaker) &bull; Bid/Cover: +z = higher B/C (stronger)</span>
    </div>
  </div>

  <!-- Bucket Cards (4 cards: distro plot + stats) -->
  <div class="grid" id="bucket-grid"></div>

  <!-- Path Overlay -->
  <div class="wide-card">
    <div class="card-title">Post-Auction Normalized Paths (by bucket)
      <span class="badge badge-avg-strong" id="path-badge">0 auctions</span>
    </div>
    <div id="path-chart" style="height:400px;"></div>
  </div>

  <!-- Scatter: z-score vs return -->
  <div class="wide-card">
    <div class="card-title">Z-Score vs Post-Auction Return</div>
    <div id="scatter-chart" style="height:360px;"></div>
  </div>

  <!-- Auction Table -->
  <div class="wide-card">
    <div class="card-title">Auction Detail Table
      <span class="badge badge-avg-strong" id="tbl-badge">0 rows</span>
    </div>
    <div class="scroll-wrap">
      <table id="auction-table">
        <thead>
          <tr>
            <th>Date</th><th>Stop</th><th>B/C</th><th>Dlr%</th><th>Dir%</th><th>Ind%</th>
            <th>z(Dlr)</th><th>z(B/C)</th><th>z(Dir)</th><th>z(Ind)</th><th>z(Comp)</th>
            <th>Bucket</th><th>t+30m</th><th>t+1h</th><th>t+2h</th><th>t+4h</th>
          </tr>
        </thead>
        <tbody id="auction-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const auctionData = {data_json};
const todayInfo = {today_json};
const bpsPerPt = {bps_per_pt};
const bucketOrder = ["Strong (z<-1)", "Avg-Strong (-1<z<0)", "Avg-Weak (0<z<1)", "Weak (z>1)"];
const bucketColors = {{
  "Strong (z<-1)": "#22c55e",
  "Avg-Strong (-1<z<0)": "#3b82f6",
  "Avg-Weak (0<z<1)": "#f97316",
  "Weak (z>1)": "#ef4444"
}};
const bucketBadge = {{
  "Strong (z<-1)": "badge-strong",
  "Avg-Strong (-1<z<0)": "badge-avg-strong",
  "Avg-Weak (0<z<1)": "badge-avg-weak",
  "Weak (z>1)": "badge-weak"
}};

function bucketForZ(z) {{
  if (z <= -1) return bucketOrder[0];
  if (z <= 0) return bucketOrder[1];
  if (z <= 1) return bucketOrder[2];
  return bucketOrder[3];
}}

function renderToday() {{
  const t = todayInfo;
  const items = [
    ["Date", t.date], ["Stop", t.stop + "%"], ["WI", t.wi + "%"],
    ["Tail", t.tail_bps + " bps"], ["Bid/Cover", t.bid_cover],
    ["Dealer%", t.dealer_pct + "%"], ["Direct%", t.direct_pct + "%"],
    ["Indirect%", t.indirect_pct + "%"],
  ];
  const bar = document.getElementById("today-bar");
  bar.innerHTML = '<div style="font-weight:600;color:var(--accent-teal);width:100%;margin-bottom:4px;">7Y NOTE AUCTION &mdash; 02/25/2026</div>' +
    items.map(([l, v]) => `<div class="today-item"><div class="today-label">${{l}}</div><div class="today-value">${{v}}</div></div>`).join("");
}}

function getMetric() {{ return document.getElementById("sel-metric").value; }}
function getHorizon() {{ return document.getElementById("sel-horizon").value; }}
function getFromYear() {{ return parseInt(document.getElementById("sel-year").value); }}

function getZKey(metric) {{
  return metric === "composite" ? "z_composite" : "z_" + metric;
}}

function filtered() {{
  const yr = getFromYear();
  return auctionData.filter(a => a.year >= yr);
}}

function assignBuckets(data, metric) {{
  const zk = getZKey(metric);
  const isReverse = (metric === "bid_cover" || metric === "indirect_pct" || metric === "direct_pct");
  return data.map(a => {{
    let z = a[zk];
    if (z == null) return null;
    // For bid/cover, direct, indirect: positive z = stronger, so flip for bucket labeling
    // For dealer: positive z = weaker (more dealer take) — matches bucket convention
    const zForBucket = isReverse ? -z : z;
    return {{ ...a, bucket: bucketForZ(zForBucket), z_display: z, z_bucket: zForBucket }};
  }}).filter(Boolean);
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

function pctile(arr, p) {{
  const s = [...arr].sort((a, b) => a - b);
  const i = Math.floor(s.length * p);
  return s[Math.min(i, s.length - 1)];
}}

function computeStats(vals) {{
  if (!vals.length) return null;
  const s = [...vals].sort((a, b) => a - b);
  const n = s.length;
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const variance = vals.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (n - 1 || 1);
  const std = Math.sqrt(variance);
  const median = n % 2 === 0 ? (s[n/2 - 1] + s[n/2]) / 2 : s[Math.floor(n/2)];
  const rally = vals.filter(v => v > 0);
  const sell = vals.filter(v => v < 0);
  const rallyRate = rally.length / n;
  const sellRate = sell.length / n;
  const rallyAvg = rally.length ? rally.reduce((a,b)=>a+b,0)/rally.length : 0;
  const sellAvg = sell.length ? Math.abs(sell.reduce((a,b)=>a+b,0)/sell.length) : 0;
  const expRally = rallyRate * rallyAvg;
  const expSelloff = sellRate * sellAvg;
  return {{
    n, mean, median, std,
    min: s[0], max: s[n-1],
    p5: pctile(vals, 0.05), p25: pctile(vals, 0.25),
    p75: pctile(vals, 0.75), p95: pctile(vals, 0.95),
    rallyRate: (rallyRate * 100).toFixed(0),
    expRally: expRally.toFixed(2),
    expSelloff: expSelloff.toFixed(2),
  }};
}}

function renderBucketCards(bucketed) {{
  const horizon = getHorizon();
  const grid = document.getElementById("bucket-grid");
  grid.innerHTML = "";

  bucketOrder.forEach(bname => {{
    const items = bucketed.filter(a => a.bucket === bname);
    const vals = items.map(a => a[horizon]).filter(v => v != null);
    const stats = computeStats(vals);

    const card = document.createElement("div");
    card.className = "card";

    const badge = bucketBadge[bname];
    const color = bucketColors[bname];
    let statsHtml = "";
    if (stats) {{
      statsHtml = `
        <div class="stat-grid">
          <div class="stat-box"><div class="stat-label">N</div><div class="stat-val">${{stats.n}}</div></div>
          <div class="stat-box"><div class="stat-label">Mean</div><div class="stat-val ${{stats.mean >= 0 ? 'positive' : 'negative'}}">${{stats.mean.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">Median</div><div class="stat-val ${{stats.median >= 0 ? 'positive' : 'negative'}}">${{stats.median.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">Std</div><div class="stat-val">${{stats.std.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">P5</div><div class="stat-val">${{stats.p5.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">P25</div><div class="stat-val">${{stats.p25.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">P75</div><div class="stat-val">${{stats.p75.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">P95</div><div class="stat-val">${{stats.p95.toFixed(2)}}</div></div>
          <div class="stat-box"><div class="stat-label">Rally %</div><div class="stat-val">${{stats.rallyRate}}%</div></div>
          <div class="stat-box"><div class="stat-label">E[Rally]</div><div class="stat-val positive">${{stats.expRally}}</div></div>
          <div class="stat-box"><div class="stat-label">E[Selloff]</div><div class="stat-val negative">${{stats.expSelloff}}</div></div>
          <div class="stat-box"><div class="stat-label">Min/Max</div><div class="stat-val">${{stats.min.toFixed(1)}}/${{stats.max.toFixed(1)}}</div></div>
        </div>`;
    }} else {{
      statsHtml = '<div style="color:var(--text-secondary);text-align:center;padding:20px;">No data</div>';
    }}

    card.innerHTML = `
      <div class="card-title">${{bname}} <span class="badge ${{badge}}">${{vals.length}} auctions</span></div>
      <div id="distro-${{bname.replace(/[^a-zA-Z]/g, '')}}" style="height:220px;"></div>
      ${{statsHtml}}`;
    grid.appendChild(card);

    // Plot distribution
    if (vals.length >= 2) {{
      const kde = gaussianKDE(vals);
      const lo = Math.min(...vals) - 5;
      const hi = Math.max(...vals) + 5;
      const xs = [];
      for (let x = lo; x <= hi; x += 0.5) xs.push(x);
      const ys = xs.map(x => kde(x));
      const traces = [
        {{ x: vals, type: "histogram", histnorm: "probability density",
           marker: {{ color: color, opacity: 0.45 }}, nbinsx: 15, name: "Density" }},
        {{ x: xs, y: ys, type: "scatter", mode: "lines",
           line: {{ color: color, width: 2.5 }}, name: "KDE" }}
      ];
      const layout = {{
        paper_bgcolor: "#111827", plot_bgcolor: "#111827",
        font: {{ color: "#f9fafb", size: 10 }},
        xaxis: {{ title: horizon + " (bps)", gridcolor: "#374151", zeroline: true, zerolinecolor: "#4b5563" }},
        yaxis: {{ gridcolor: "#374151", showticklabels: false }},
        showlegend: false,
        margin: {{ t: 10, r: 12, b: 36, l: 20 }},
        bargap: 0.08,
      }};
      Plotly.newPlot("distro-" + bname.replace(/[^a-zA-Z]/g, ""), traces, layout, {{ responsive: true, displayModeBar: false }});
    }}
  }});
}}

function renderPathOverlay(bucketed) {{
  const traces = [];
  let total = 0;
  bucketOrder.forEach(bname => {{
    const items = bucketed.filter(a => a.bucket === bname && a.path_minutes && a.path_minutes.length > 2);
    const color = bucketColors[bname];
    items.forEach((a, i) => {{
      traces.push({{
        x: a.path_minutes, y: a.path_bps,
        type: "scatter", mode: "lines",
        line: {{ color, width: 1.0 }},
        opacity: 0.5,
        name: i === 0 ? bname : undefined,
        showlegend: i === 0,
        legendgroup: bname,
        hovertemplate: `${{a.date}}<br>%{{x}}m: %{{y:.1f}} bps<extra>${{bname}}</extra>`,
      }});
      total++;
    }});
  }});

  document.getElementById("path-badge").textContent = total + " auctions";

  const layout = {{
    paper_bgcolor: "#111827", plot_bgcolor: "#111827",
    font: {{ color: "#f9fafb" }},
    xaxis: {{ title: "Minutes from auction", gridcolor: "#374151" }},
    yaxis: {{ title: "Move (bps)", gridcolor: "#374151", zeroline: true, zerolinecolor: "#4b5563" }},
    showlegend: true, legend: {{ orientation: "h", y: -0.15 }},
    margin: {{ t: 16, r: 20, b: 60, l: 56 }},
    hovermode: "closest",
  }};
  Plotly.react("path-chart", traces, layout, {{ responsive: true, scrollZoom: true, displayModeBar: true }});
}}

function renderScatter(bucketed) {{
  const horizon = getHorizon();
  const metric = getMetric();
  const zk = getZKey(metric);
  const traces = [];

  bucketOrder.forEach(bname => {{
    const items = bucketed.filter(a => a.bucket === bname && a[horizon] != null);
    if (!items.length) return;
    traces.push({{
      x: items.map(a => a.z_display),
      y: items.map(a => a[horizon]),
      text: items.map(a => a.date),
      type: "scatter", mode: "markers",
      marker: {{ color: bucketColors[bname], size: 8, opacity: 0.8 }},
      name: bname,
      hovertemplate: "%{{text}}<br>z: %{{x:.2f}}<br>" + horizon + ": %{{y:.1f}} bps<extra></extra>",
    }});
  }});

  const layout = {{
    paper_bgcolor: "#111827", plot_bgcolor: "#111827",
    font: {{ color: "#f9fafb" }},
    xaxis: {{ title: "Z-score (" + metric.replace("_", " ") + ")", gridcolor: "#374151", zeroline: true, zerolinecolor: "#4b5563" }},
    yaxis: {{ title: horizon + " return (bps)", gridcolor: "#374151", zeroline: true, zerolinecolor: "#4b5563" }},
    showlegend: true, legend: {{ orientation: "h", y: -0.18 }},
    margin: {{ t: 16, r: 20, b: 56, l: 56 }},
  }};
  Plotly.react("scatter-chart", traces, layout, {{ responsive: true }});
}}

function renderTable(bucketed) {{
  const tbody = document.getElementById("auction-tbody");
  const sorted = [...bucketed].sort((a, b) => {{
    const da = new Date(a.date); const db = new Date(b.date);
    return db - da;
  }});
  document.getElementById("tbl-badge").textContent = sorted.length + " rows";

  function fmt(v, dec=1) {{ return v == null ? "-" : v.toFixed(dec); }}
  function cls(v) {{ return v == null ? "" : (v >= 0 ? "positive" : "negative"); }}

  let html = "";
  sorted.forEach(a => {{
    html += `<tr>
      <td>${{a.date}}</td><td>${{fmt(a.stop, 3)}}</td><td>${{fmt(a.bid_cover, 2)}}</td>
      <td>${{fmt(a.dealer_pct, 1)}}</td><td>${{fmt(a.direct_pct, 1)}}</td><td>${{fmt(a.indirect_pct, 1)}}</td>
      <td class="${{cls(a.z_dealer_pct)}}">${{fmt(a.z_dealer_pct, 2)}}</td>
      <td class="${{cls(a.z_bid_cover)}}">${{fmt(a.z_bid_cover, 2)}}</td>
      <td class="${{cls(a.z_direct_pct)}}">${{fmt(a.z_direct_pct, 2)}}</td>
      <td class="${{cls(a.z_indirect_pct)}}">${{fmt(a.z_indirect_pct, 2)}}</td>
      <td class="${{cls(a.z_composite)}}">${{fmt(a.z_composite, 2)}}</td>
      <td><span class="badge ${{bucketBadge[a.bucket]}}" style="font-size:0.72rem;">${{a.bucket.split("(")[0].trim()}}</span></td>
      <td class="${{cls(a["t+30m"])}}">${{fmt(a["t+30m"])}}</td>
      <td class="${{cls(a["t+1h"])}}">${{fmt(a["t+1h"])}}</td>
      <td class="${{cls(a["t+2h"])}}">${{fmt(a["t+2h"])}}</td>
      <td class="${{cls(a["t+4h"])}}">${{fmt(a["t+4h"])}}</td>
    </tr>`;
  }});
  tbody.innerHTML = html;
}}

function rebuild() {{
  const metric = getMetric();
  const data = filtered();
  const bucketed = assignBuckets(data, metric);
  renderBucketCards(bucketed);
  renderPathOverlay(bucketed);
  renderScatter(bucketed);
  renderTable(bucketed);
}}

renderToday();
rebuild();
</script>
</body>
</html>"""


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="7Y Auction Z-Score Dashboard")
    parser.add_argument("--start-year", type=int, default=2009)
    args = parser.parse_args()

    auctions = load_auctions()
    auctions = compute_zscores(auctions, start_year=args.start_year)
    logger.info("Computed z-scores for %d auctions", len(auctions))

    logger.info("Connecting to PostgreSQL for TY data...")
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
    )
    try:
        auctions = fetch_post_auction_returns(auctions, conn)
    finally:
        conn.close()

    have_returns = sum(1 for a in auctions if a.get("t+1h") is not None)
    logger.info("Fetched returns for %d / %d auctions", have_returns, len(auctions))

    payload = build_payload(auctions)
    html = generate_html(payload, TODAY)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"7y_auction_zscore_dashboard_{ts}.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s", out_path)


if __name__ == "__main__":
    main()
