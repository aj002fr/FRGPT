#!/usr/bin/env python
"""
Month-End & Friday Candlestick Dashboard
-----------------------------------------
Generates a standalone HTML dashboard with two tabs:
  1. Month Ends (last business day of each month) from 2022 onward
  2. Fridays from the past 2 months
Click any row to view TY 5-minute candlestick chart for +/- N days around it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER
from rules.treasury_price_conversion import points_to_bps

VALID_TY_MIN = 80.0
VALID_TY_MAX = 150.0


def last_business_day(year: int, month: int) -> date:
    """Return the last business day (Mon-Fri) of a given month."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def get_month_end_dates(start_year: int, end_date: date) -> List[date]:
    """Get all month-end business days from start_year to end_date."""
    dates: List[date] = []
    y, m = start_year, 1
    while True:
        d = last_business_day(y, m)
        if d > end_date:
            break
        dates.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return dates


def get_recent_fridays(ref_date: date, months_back: int = 2) -> List[date]:
    """Get all Fridays in the past N months from ref_date."""
    cutoff = ref_date - timedelta(days=months_back * 31)
    fridays: List[date] = []
    d = cutoff
    while d <= ref_date:
        if d.weekday() == 4:
            fridays.append(d)
        d += timedelta(days=1)
    return fridays


def aggregate_to_5m(rows: List[Tuple[datetime, float, float, float, float]]) -> Dict[str, List]:
    out = {"x": [], "open": [], "high": [], "low": [], "close": []}
    if not rows:
        return out
    rows = sorted(rows, key=lambda r: r[0])

    bucket_ts: Optional[datetime] = None
    o = h = l = c = None

    def flush() -> None:
        if bucket_ts is None:
            return
        out["x"].append(bucket_ts.isoformat())
        out["open"].append(float(o))
        out["high"].append(float(h))
        out["low"].append(float(l))
        out["close"].append(float(c))

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


def ensure_candles_sorted(candles: Dict[str, List]) -> Dict[str, List]:
    """Defensive sort by timestamp to keep OHLC arrays strictly aligned in time."""
    x_list = candles.get("x", [])
    o_list = candles.get("open", [])
    h_list = candles.get("high", [])
    l_list = candles.get("low", [])
    c_list = candles.get("close", [])
    n = min(len(x_list), len(o_list), len(h_list), len(l_list), len(c_list))
    if n == 0:
        return {"x": [], "open": [], "high": [], "low": [], "close": []}
    rows = [
        (str(x_list[i]), float(o_list[i]), float(h_list[i]), float(l_list[i]), float(c_list[i]))
        for i in range(n)
    ]
    rows.sort(key=lambda r: r[0])
    return {
        "x": [r[0] for r in rows],
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
    }


def fetch_candles(conn, symbol: str, center_date: date, days_each_side: int) -> Dict[str, List]:
    start_dt = datetime.combine(center_date - timedelta(days=days_each_side), time.min)
    end_dt = datetime.combine(center_date + timedelta(days=days_each_side + 1), time.min)
    sql = """
        SELECT ts_event, open, high, low, close
        FROM ohlcv_1m
        WHERE symbol = %s
          AND ts_event >= %s
          AND ts_event < %s
          AND close >= %s
          AND close <= %s
        ORDER BY ts_event
    """
    with conn.cursor() as cur:
        cur.execute(sql, (symbol, start_dt, end_dt, VALID_TY_MIN, VALID_TY_MAX))
        rows = cur.fetchall()
    return ensure_candles_sorted(aggregate_to_5m(rows))


def compute_monday_open_minus_friday_close_bps(conn, symbol: str, friday_date: date) -> Optional[float]:
    """Weekend gap in bps: Monday open - Friday close."""
    if friday_date.weekday() != 4:
        return None

    friday_start = datetime.combine(friday_date, time.min)
    saturday_start = friday_start + timedelta(days=1)
    monday_date = friday_date + timedelta(days=3)
    monday_start = datetime.combine(monday_date, time.min)
    tuesday_start = monday_start + timedelta(days=1)

    friday_sql = """
        SELECT close
        FROM ohlcv_1m
        WHERE symbol = %s
          AND ts_event >= %s
          AND ts_event < %s
          AND close >= %s
          AND close <= %s
        ORDER BY ts_event DESC
        LIMIT 1
    """
    monday_sql = """
        SELECT open
        FROM ohlcv_1m
        WHERE symbol = %s
          AND ts_event >= %s
          AND ts_event < %s
          AND open >= %s
          AND open <= %s
        ORDER BY ts_event ASC
        LIMIT 1
    """

    with conn.cursor() as cur:
        cur.execute(friday_sql, (symbol, friday_start, saturday_start, VALID_TY_MIN, VALID_TY_MAX))
        friday_row = cur.fetchone()
        cur.execute(monday_sql, (symbol, monday_start, tuesday_start, VALID_TY_MIN, VALID_TY_MAX))
        monday_row = cur.fetchone()

    if not friday_row or not monday_row:
        return None

    friday_close = float(friday_row[0])
    monday_open = float(monday_row[0])
    return round(float(points_to_bps(monday_open - friday_close)), 2)


def build_data(
    start_year: int,
    end_date: date,
    months_back: int,
    symbol: str,
    days_each_side: int,
) -> Tuple[List[Dict], List[Dict]]:
    me_dates = get_month_end_dates(start_year, end_date)
    fri_dates = get_recent_fridays(end_date, months_back)

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE, user=PG_USER, password=PG_PASSWORD
    )
    try:
        me_payload: List[Dict] = []
        for d in me_dates:
            candles = fetch_candles(conn, symbol, d, days_each_side)
            if not candles.get("x"):
                continue
            is_friday = d.weekday() == 4
            gap = compute_monday_open_minus_friday_close_bps(conn, symbol, d) if is_friday else None
            me_payload.append({
                "id": len(me_payload) + 1,
                "date": d.isoformat(),
                "display": d.strftime("%b %d, %Y"),
                "dow": d.strftime("%A"),
                "month": d.strftime("%b %Y"),
                "is_friday": is_friday,
                "gap_bps": gap,
                "candles": candles,
            })

        fri_payload: List[Dict] = []
        for d in fri_dates:
            candles = fetch_candles(conn, symbol, d, days_each_side)
            if not candles.get("x"):
                continue
            eom = last_business_day(d.year, d.month)
            is_month_end = d == eom
            gap = compute_monday_open_minus_friday_close_bps(conn, symbol, d)
            fri_payload.append({
                "id": len(fri_payload) + 1,
                "date": d.isoformat(),
                "display": d.strftime("%b %d, %Y"),
                "dow": "Friday",
                "is_month_end": is_month_end,
                "gap_bps": gap,
                "candles": candles,
            })

        return me_payload, fri_payload
    finally:
        conn.close()


def generate_html(
    me_data: List[Dict],
    fri_data: List[Dict],
    symbol: str,
    start_year: int,
    days_each_side: int,
) -> str:
    me_json = json.dumps(me_data, separators=(",", ":"))
    fri_json = json.dumps(fri_data, separators=(",", ":"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{symbol} Month-End &amp; Friday Candlestick Dashboard</title>
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
      --accent-purple: #a855f7;
      --accent-orange: #f97316;
      --accent-blue: #3b82f6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      line-height: 1.5;
    }}
    .container {{ max-width: 1800px; margin: 0 auto; padding: 24px; }}
    header {{
      margin-bottom: 20px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--border-color);
    }}
    h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 6px; }}
    .subtitle {{ color: var(--text-secondary); font-size: 0.9rem; }}

    .tabs {{
      display: flex;
      gap: 0;
      margin-top: 16px;
    }}
    .tab-btn {{
      padding: 10px 24px;
      border: 1px solid var(--border-color);
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      cursor: pointer;
      font-size: 0.9rem;
      font-weight: 600;
      transition: all 0.15s ease;
    }}
    .tab-btn:first-child {{ border-radius: 8px 0 0 8px; }}
    .tab-btn:last-child {{ border-radius: 0 8px 8px 0; }}
    .tab-btn.active {{
      background: var(--accent-teal);
      color: #000;
      border-color: var(--accent-teal);
    }}

    .summary-bar {{
      display: flex;
      gap: 16px;
      margin-top: 14px;
      flex-wrap: wrap;
    }}
    .summary-item {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      padding: 8px 14px;
      border-radius: 8px;
    }}
    .summary-label {{ color: var(--text-secondary); font-size: 0.8rem; }}
    .summary-value {{ color: var(--accent-teal); font-size: 1.05rem; font-weight: 600; }}

    .grid {{
      display: grid;
      grid-template-columns: 480px 1fr;
      gap: 16px;
      margin-top: 18px;
    }}
    .panel {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      overflow: hidden;
    }}
    .panel-header {{
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-color);
      background: var(--bg-tertiary);
      font-weight: 700;
      font-size: 0.9rem;
    }}
    .table-wrap {{
      max-height: 750px;
      overflow-y: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }}
    thead th {{
      position: sticky;
      top: 0;
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      text-align: left;
      padding: 8px 10px;
      border-bottom: 1px solid var(--border-color);
      font-weight: 600;
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    tbody tr {{
      cursor: pointer;
      transition: background 0.1s;
    }}
    tbody tr:hover {{ background: rgba(20, 184, 166, 0.08); }}
    tbody tr.selected {{ background: rgba(20, 184, 166, 0.15); }}
    td {{
      border-bottom: 1px solid var(--border-color);
      padding: 8px 10px;
      vertical-align: middle;
    }}
    .badge {{
      display: inline-block;
      padding: 1px 7px;
      border-radius: 999px;
      font-size: 0.7rem;
      font-weight: 600;
    }}
    .badge-friday {{
      background: var(--accent-purple);
      color: #fff;
    }}
    .badge-monthend {{
      background: var(--accent-orange);
      color: #000;
    }}

    .stats-row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--border-color);
    }}
    .stat-card {{
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 10px;
      background: var(--bg-tertiary);
    }}
    .stat-title {{ font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 4px; font-weight: 600; }}
    .stat-line {{ font-size: 0.95rem; color: var(--text-primary); font-weight: 600; }}
    .stat-subtle {{ font-size: 0.78rem; color: var(--text-secondary); margin-top: 2px; }}
    #chart {{ width: 100%; height: 720px; }}

    .footer {{
      margin-top: 14px;
      color: var(--text-secondary);
      font-size: 0.78rem;
      padding-top: 10px;
      border-top: 1px solid var(--border-color);
    }}
    @media (max-width: 1000px) {{
      .grid {{ grid-template-columns: 1fr; }}
      #chart {{ height: 480px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{symbol} Month-End &amp; Friday Candlestick Dashboard</h1>
      <div class="subtitle">Click any row to view {symbol} 5-min candlesticks for &plusmn;{days_each_side} calendar days around the date. Gap = Monday open &minus; Friday close (bps). Conversions: 1 point = 32 ticks = 8 bps.</div>
      <div class="tabs">
        <button class="tab-btn active" data-tab="monthend">Month Ends ({start_year}+)</button>
        <button class="tab-btn" data-tab="friday">Recent Fridays</button>
      </div>
      <div class="summary-bar">
        <div class="summary-item">
          <div class="summary-label">Dates (filtered)</div>
          <div class="summary-value" id="dateCount">0</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Date range</div>
          <div class="summary-value" id="rangeLabel">--</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Active tab</div>
          <div class="summary-value" id="tabLabel">Month Ends</div>
        </div>
      </div>
    </header>

    <div class="grid">
      <div class="panel">
        <div class="panel-header" id="listTitle">Month-End Dates</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Day</th>
                <th>Gap (bps)</th>
                <th>Tags</th>
              </tr>
            </thead>
            <tbody id="date-rows"></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header" id="chart-title">Candlestick</div>
        <div class="stats-row" style="grid-template-columns: 1fr 1fr 1fr;">
          <div class="stat-card">
            <div class="stat-title">Window Return (&plusmn;{days_each_side}d first close &rarr; last close)</div>
            <div class="stat-line" id="window-return-line">-</div>
            <div class="stat-subtle" id="window-return-sub">-</div>
          </div>
          <div class="stat-card">
            <div class="stat-title">Day Return (selected date open &rarr; close)</div>
            <div class="stat-line" id="day-return-line">-</div>
            <div class="stat-subtle" id="day-return-sub">-</div>
          </div>
          <div class="stat-card">
            <div class="stat-title">Gap (Monday open &minus; Friday close)</div>
            <div class="stat-line" id="gap-line">-</div>
            <div class="stat-subtle" id="gap-sub">-</div>
          </div>
        </div>
        <div id="chart"></div>
      </div>
    </div>

    <div class="footer">Generated: {generated_at} | Symbol: {symbol} | TY filter [{VALID_TY_MIN}, {VALID_TY_MAX}]</div>
  </div>

  <script>
    const ME_DATA = {me_json};
    const FRI_DATA = {fri_json};

    let activeTab = 'monthend';
    let selectedId = null;

    const CHART_CFG = {{
      responsive: true,
      scrollZoom: true,
      displayModeBar: true,
      displaylogo: false,
      doubleClick: 'reset+autosize',
    }};

    const TICKS_PER_POINT = 32;
    const TICKS_PER_BP = 4;
    const BPS_PER_POINT = TICKS_PER_POINT / TICKS_PER_BP;  // 8
    function pointsToBps(pts) {{ return pts * BPS_PER_POINT; }}
    function fmtSigned(v, dec) {{
      if (!Number.isFinite(v)) return 'NA';
      return (v >= 0 ? '+' : '') + v.toFixed(dec) + ' bps';
    }}
    function fmtGap(v) {{
      if (v == null || !Number.isFinite(v)) return '-';
      const color = v > 0 ? '#16a34a' : v < 0 ? '#dc2626' : '#9ca3af';
      const label = v > 0 ? 'Gap Up' : v < 0 ? 'Gap Down' : 'Flat';
      return `<span style="color:${{color}};font-weight:600">${{v > 0 ? '+' : ''}}${{v.toFixed(2)}}</span> <span style="color:#6b7280;font-size:0.75rem">${{label}}</span>`;
    }}

    function activeData() {{ return activeTab === 'monthend' ? ME_DATA : FRI_DATA; }}
    function normalizeCandlesChrono(candles) {{
      const x = Array.isArray(candles?.x) ? candles.x : [];
      const o = Array.isArray(candles?.open) ? candles.open : [];
      const h = Array.isArray(candles?.high) ? candles.high : [];
      const l = Array.isArray(candles?.low) ? candles.low : [];
      const c = Array.isArray(candles?.close) ? candles.close : [];
      const n = Math.min(x.length, o.length, h.length, l.length, c.length);
      if (!n) return {{ x: [], open: [], high: [], low: [], close: [] }};
      const rows = [];
      for (let i = 0; i < n; i++) rows.push([String(x[i]), Number(o[i]), Number(h[i]), Number(l[i]), Number(c[i])]);
      rows.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
      return {{
        x: rows.map(r => r[0]),
        open: rows.map(r => r[1]),
        high: rows.map(r => r[2]),
        low: rows.map(r => r[3]),
        close: rows.map(r => r[4]),
      }};
    }}

    function renderRows() {{
      const data = activeData();
      const body = document.getElementById('date-rows');
      body.innerHTML = data.map(item => {{
        let tags = '';
        if (activeTab === 'monthend') {{
          if (item.is_friday) tags += '<span class="badge badge-friday">Fri</span> ';
        }} else {{
          if (item.is_month_end) tags += '<span class="badge badge-monthend">MonthEnd</span> ';
        }}
        return `<tr data-id="${{item.id}}">
          <td>${{item.display}}</td>
          <td>${{item.dow}}</td>
          <td>${{fmtGap(item.gap_bps)}}</td>
          <td>${{tags}}</td>
        </tr>`;
      }}).join('');

      body.querySelectorAll('tr').forEach(tr => {{
        tr.addEventListener('click', () => {{
          selectRow(Number(tr.getAttribute('data-id')));
        }});
      }});

      document.getElementById('dateCount').textContent = data.length;
      if (data.length) {{
        document.getElementById('rangeLabel').textContent =
          data[0].date + ' \u2192 ' + data[data.length - 1].date;
      }} else {{
        document.getElementById('rangeLabel').textContent = '--';
      }}
    }}

    function selectRow(id) {{
      selectedId = id;
      document.querySelectorAll('#date-rows tr').forEach(tr => {{
        tr.classList.toggle('selected', Number(tr.getAttribute('data-id')) === id);
      }});
      const item = activeData().find(d => d.id === id);
      if (item) renderChart(item);
    }}

    function setStats(item) {{
      const c = normalizeCandlesChrono(item.candles || {{}});
      const x = c.x || [];
      const opens = c.open || [];
      const closes = c.close || [];
      const wLine = document.getElementById('window-return-line');
      const wSub = document.getElementById('window-return-sub');
      const dLine = document.getElementById('day-return-line');
      const dSub = document.getElementById('day-return-sub');
      const gLine = document.getElementById('gap-line');
      const gSub = document.getElementById('gap-sub');

      if (!x.length) {{
        wLine.textContent = 'NA'; wSub.textContent = 'No candles';
        dLine.textContent = 'NA'; dSub.textContent = 'No candles';
        gLine.textContent = 'NA'; gSub.textContent = 'No candles';
        return;
      }}

      const wBps = pointsToBps(closes[closes.length - 1] - closes[0]);
      wLine.textContent = fmtSigned(wBps, 2);
      wSub.textContent = `First close ${{closes[0].toFixed(4)}} \u2192 Last close ${{closes[closes.length - 1].toFixed(4)}}`;

      const dayMap = new Map();
      for (let i = 0; i < x.length; i++) {{
        const day = String(x[i]).slice(0, 10);
        if (!dayMap.has(day)) dayMap.set(day, {{ open: opens[i], close: closes[i] }});
        else dayMap.get(day).close = closes[i];
      }}

      const targetDay = item.date;
      let useDay = targetDay;
      if (!dayMap.has(useDay)) {{
        const keys = Array.from(dayMap.keys());
        if (!keys.length) {{
          dLine.textContent = 'NA'; dSub.textContent = '--';
          gLine.textContent = 'NA'; gSub.textContent = '--';
          return;
        }}
        const t0 = new Date(targetDay + 'T00:00:00').getTime();
        keys.sort((a, b) => Math.abs(new Date(a+'T00:00:00').getTime() - t0) - Math.abs(new Date(b+'T00:00:00').getTime() - t0));
        useDay = keys[0];
      }}
      const dd = dayMap.get(useDay);
      const dBps = pointsToBps(dd.close - dd.open);
      dLine.textContent = fmtSigned(dBps, 2);
      dSub.textContent = `${{useDay}} open ${{dd.open.toFixed(4)}} \u2192 close ${{dd.close.toFixed(4)}}`;

      if (item.gap_bps != null && Number.isFinite(item.gap_bps)) {{
        const gapLabel = item.gap_bps > 0 ? 'Gap Up' : item.gap_bps < 0 ? 'Gap Down' : 'Flat';
        gLine.textContent = fmtSigned(item.gap_bps, 2);
        gLine.style.color = item.gap_bps > 0 ? '#16a34a' : item.gap_bps < 0 ? '#dc2626' : '#9ca3af';
        gSub.textContent = `${{gapLabel}} | Monday open - Friday close`;
      }} else {{
        gLine.textContent = 'NA';
        gLine.style.color = '';
        gSub.textContent = 'No Friday/Monday pair available';
      }}
    }}

    function renderChart(item) {{
      document.getElementById('chart-title').textContent = `${{item.display}} (${{item.dow}})`;
      const c = normalizeCandlesChrono(item.candles || {{}});
      const x = c.x || [];
      if (!x.length) {{
        setStats(item);
        Plotly.newPlot('chart', [], {{
          title: 'No market data',
          paper_bgcolor: '#111827', plot_bgcolor: '#111827',
          font: {{ color: '#9ca3af' }}
        }}, CHART_CFG);
        return;
      }}

      const traces = [{{
        type: 'candlestick',
        x: x, open: c.open, high: c.high, low: c.low, close: c.close,
        name: '{symbol}',
        increasing: {{ line: {{ color: '#16a34a' }} }},
        decreasing: {{ line: {{ color: '#dc2626' }} }},
        hovertemplate: '<b>%{{x}}</b><br>O: %{{open:.4f}}<br>H: %{{high:.4f}}<br>L: %{{low:.4f}}<br>C: %{{close:.4f}}<extra></extra>',
      }}];

      const dateLine = item.date + 'T12:00:00';
      const shapes = [{{
        type: 'line', x0: dateLine, x1: dateLine,
        y0: 0, y1: 1, yref: 'paper',
        line: {{ color: '#3b82f6', width: 2, dash: 'dot' }}
      }}];
      const annotations = [{{
        x: dateLine, y: 1.0, yref: 'paper',
        text: activeTab === 'monthend' ? 'Month End' : 'Friday',
        showarrow: false, xanchor: 'left', yanchor: 'bottom',
        font: {{ size: 11, color: '#60a5fa' }}
      }}];

      const layout = {{
        margin: {{ l: 50, r: 20, t: 20, b: 50 }},
        xaxis: {{ title: 'Date/Time', rangeslider: {{ visible: false }}, color: '#9ca3af', gridcolor: '#1f2937' }},
        yaxis: {{ title: '{symbol} Price', color: '#9ca3af', gridcolor: '#1f2937' }},
        dragmode: 'zoom',
        hovermode: 'closest',
        paper_bgcolor: '#111827',
        plot_bgcolor: '#111827',
        font: {{ color: '#f9fafb' }},
        shapes: shapes,
        annotations: annotations,
      }};

      setStats(item);
      Plotly.newPlot('chart', traces, layout, CHART_CFG).catch(() => {{}});
    }}

    function switchTab(tab) {{
      activeTab = tab;
      selectedId = null;
      document.querySelectorAll('.tab-btn').forEach(btn => {{
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
      }});
      document.getElementById('listTitle').textContent =
        tab === 'monthend' ? 'Month-End Dates' : 'Recent Fridays';
      document.getElementById('tabLabel').textContent =
        tab === 'monthend' ? 'Month Ends' : 'Recent Fridays';
      renderRows();
      const data = activeData();
      if (data.length) selectRow(data[0].id);
    }}

    document.querySelectorAll('.tab-btn').forEach(btn => {{
      btn.addEventListener('click', () => switchTab(btn.getAttribute('data-tab')));
    }});

    switchTab('monthend');
  </script>
</body>
</html>
"""


def default_output_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "workspace" / "scenarios" / f"monthend_friday_dashboard_{ts}.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Month-End & Friday candlestick dashboard")
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--months-back", type=int, default=2, help="Fridays lookback in months")
    parser.add_argument("--symbol", default="TY")
    parser.add_argument("--days", type=int, default=2, help="Calendar days on each side for candle window")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    today = date.today()

    print(f"[info] Building month-end dates from {args.start_year} to {today}")
    print(f"[info] Building Fridays for past {args.months_back} months")

    me_data, fri_data = build_data(
        start_year=args.start_year,
        end_date=today,
        months_back=args.months_back,
        symbol=args.symbol,
        days_each_side=args.days,
    )

    print(f"[info] Month-end dates with data: {len(me_data)}")
    print(f"[info] Recent Fridays with data: {len(fri_data)}")

    html = generate_html(
        me_data=me_data,
        fri_data=fri_data,
        symbol=args.symbol,
        start_year=args.start_year,
        days_each_side=args.days,
    )

    out_path = args.output or default_output_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"[ok] Dashboard written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
