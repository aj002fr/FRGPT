#!/usr/bin/env python
"""
US Holiday Candlestick Dashboard
--------------------------------
Generates a standalone HTML dashboard with:
- A table of selected US market holidays (from start year onward)
- Click-to-view TY candlestick chart for +/- N days around each holiday
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2

# Add project root for config import
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import ECONOMIC_EVENTS_DB_PATH, PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

VALID_TY_MIN = 80.0
VALID_TY_MAX = 150.0
TIER1_COLORS = {"CPI": "#2563eb", "NFP": "#7c3aed", "PCE": "#ea580c"}


@dataclass(frozen=True)
class HolidayDef:
    name: str
    actual_date: date
    observed_date: date
    note: str


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth weekday in a month. weekday: Mon=0..Sun=6."""
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    day_num = 1 + shift + (n - 1) * 7
    return date(year, month, day_num)


def last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last weekday in a month. weekday: Mon=0..Sun=6."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def observed_weekday(d: date) -> date:
    """Observed weekday rule for fixed-date holidays."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def easter_sunday(year: int) -> date:
    """Compute Easter Sunday using Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def fmt_day(d: date) -> str:
    return d.strftime("%A, %B ") + str(d.day)


def holiday_defs_for_year(year: int) -> List[HolidayDef]:
    """Return the requested holiday set for a given year."""
    out: List[HolidayDef] = []

    # New Year's Day
    ny_actual = date(year, 1, 1)
    ny_observed = observed_weekday(ny_actual)
    ny_note = ""
    if ny_observed != ny_actual:
        ny_note = f"Observed on {fmt_day(ny_observed)}"
    out.append(HolidayDef("New Year's Day", ny_actual, ny_observed, ny_note))

    # MLK Day (3rd Monday of Jan)
    out.append(
        HolidayDef(
            "Martin Luther King, Jr. Day",
            nth_weekday(year, 1, 0, 3),
            nth_weekday(year, 1, 0, 3),
            "",
        )
    )

    # Washington's Birthday (3rd Monday of Feb)
    out.append(
        HolidayDef(
            "Washington's Birthday",
            nth_weekday(year, 2, 0, 3),
            nth_weekday(year, 2, 0, 3),
            "",
        )
    )

    # Good Friday (2 days before Easter)
    gf = easter_sunday(year) - timedelta(days=2)
    out.append(HolidayDef("Good Friday", gf, gf, ""))

    # Memorial Day (last Monday in May)
    mem = last_weekday(year, 5, 0)
    out.append(HolidayDef("Memorial Day", mem, mem, ""))

    # Juneteenth
    june_actual = date(year, 6, 19)
    june_observed = observed_weekday(june_actual)
    june_note = ""
    if june_observed != june_actual:
        june_note = f"Observed on {fmt_day(june_observed)}"
    out.append(
        HolidayDef(
            "Juneteenth National Independence Day",
            june_actual,
            june_observed,
            june_note,
        )
    )

    # Independence Day
    july_actual = date(year, 7, 4)
    july_observed = observed_weekday(july_actual)
    july_note = ""
    if july_observed != july_actual:
        july_note = f"Observed on {fmt_day(july_observed)}"
    out.append(HolidayDef("Independence Day", july_actual, july_observed, july_note))

    # Labor Day (first Monday of Sep)
    labor = nth_weekday(year, 9, 0, 1)
    out.append(HolidayDef("Labor Day", labor, labor, ""))

    # Thanksgiving (4th Thursday of Nov)
    tg = nth_weekday(year, 11, 3, 4)
    out.append(HolidayDef("Thanksgiving Day", tg, tg, ""))

    # Christmas Day
    xmas_actual = date(year, 12, 25)
    xmas_observed = observed_weekday(xmas_actual)
    xmas_note = ""
    if xmas_observed != xmas_actual:
        xmas_note = f"Observed on {fmt_day(xmas_observed)}"
    out.append(HolidayDef("Christmas Day", xmas_actual, xmas_observed, xmas_note))

    return out


def aggregate_to_5m(rows: List[Tuple[datetime, float, float, float, float]]) -> Dict[str, List]:
    """Aggregate 1m OHLC rows into 5m candles."""
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


def parse_event_dt(value: str) -> Optional[datetime]:
    """Parse event_date string from economic_events DB."""
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
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def classify_tier1_event(event_name: str) -> Optional[str]:
    """Map DB event names to Tier-1 families used for overlays."""
    name = (event_name or "").strip().lower()
    if not name:
        return None

    if "pce" in name:
        return "PCE"
    if "non farm payroll" in name or "nonfarm payroll" in name:
        return "NFP"
    if (
        "inflation rate mom" in name
        or "inflation rate yoy" in name
        or "consumer price index" in name
    ) and "producer" not in name:
        return "CPI"
    return None


def fetch_tier1_events_for_window(start_dt: datetime, end_dt: datetime) -> List[Dict]:
    """Fetch Tier-1 (CPI/NFP/PCE) US events inside the candle window."""
    if not ECONOMIC_EVENTS_DB_PATH.exists():
        return []

    start_date = (start_dt - timedelta(days=1)).date().isoformat()
    end_date = (end_dt + timedelta(days=1)).date().isoformat()

    sql = """
        SELECT event_name, event_date
        FROM economic_events
        WHERE country LIKE '%United States%'
          AND event_date IS NOT NULL
          AND event_date <> ''
          AND substr(event_date, 1, 10) >= ?
          AND substr(event_date, 1, 10) <= ?
        ORDER BY event_date ASC
    """

    out: List[Dict] = []
    with sqlite3.connect(str(ECONOMIC_EVENTS_DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(sql, (start_date, end_date))
        rows = cur.fetchall()

    for event_name, event_date in rows:
        event_type = classify_tier1_event(event_name)
        if not event_type:
            continue
        ts = parse_event_dt(event_date)
        if ts is None:
            continue
        if ts < start_dt or ts >= end_dt:
            continue
        out.append(
            {
                "name": event_name,
                "type": event_type,
                "label": event_type,
                "color": TIER1_COLORS[event_type],
                "ts": ts.isoformat(),
            }
        )

    return out


def fetch_candles(
    conn,
    symbol: str,
    center_date: date,
    days_each_side: int,
) -> Dict[str, List]:
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
    return aggregate_to_5m(rows)


def build_dashboard_data(start_year: int, end_year: int, symbol: str, days_each_side: int) -> List[Dict]:
    all_holidays: List[HolidayDef] = []
    for year in range(start_year, end_year + 1):
        all_holidays.extend(holiday_defs_for_year(year))

    all_holidays.sort(key=lambda h: (h.observed_date, h.name))

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )
    try:
        payload: List[Dict] = []
        for idx, h in enumerate(all_holidays):
            start_dt = datetime.combine(h.observed_date - timedelta(days=days_each_side), time.min)
            end_dt = datetime.combine(h.observed_date + timedelta(days=days_each_side + 1), time.min)
            candles = fetch_candles(conn, symbol=symbol, center_date=h.observed_date, days_each_side=days_each_side)
            # Keep only holidays where we actually have market candles in the window.
            if not candles.get("x"):
                continue
            tier1_events = fetch_tier1_events_for_window(start_dt=start_dt, end_dt=end_dt)
            payload.append(
                {
                    "id": len(payload) + 1,
                    "year": h.actual_date.year,
                    "name": h.name,
                    "actual_date": h.actual_date.isoformat(),
                    "observed_date": h.observed_date.isoformat(),
                    "display_date": fmt_day(h.actual_date),
                    "note": h.note,
                    "candles": candles,
                    "tier1_events": tier1_events,
                }
            )
        return payload
    finally:
        conn.close()


def generate_html(holidays_data: List[Dict], symbol: str, start_year: int, end_year: int, days_each_side: int) -> str:
    payload = json.dumps(holidays_data, separators=(",", ":"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>US Holiday Candlestick Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f7f8fa;
      color: #172033;
    }}
    .container {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 16px;
    }}
    h1 {{
      margin: 0 0 6px 0;
      font-size: 24px;
    }}
    .subtitle {{
      color: #4b5563;
      margin-bottom: 14px;
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 14px;
    }}
    .panel {{
      background: white;
      border: 1px solid #d8dde6;
      border-radius: 8px;
      overflow: hidden;
    }}
    .panel-header {{
      padding: 10px 12px;
      border-bottom: 1px solid #d8dde6;
      background: #f2f4f7;
      font-weight: 700;
      font-size: 14px;
    }}
    .stats-row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid #e5e9f0;
      background: #fafbfd;
    }}
    .stat-card {{
      border: 1px solid #d8dde6;
      border-radius: 6px;
      padding: 8px;
      background: #ffffff;
    }}
    .stat-title {{
      font-size: 12px;
      color: #4b5563;
      margin-bottom: 4px;
      font-weight: 700;
    }}
    .stat-line {{
      font-size: 13px;
      color: #1f2937;
      line-height: 1.35;
    }}
    .stat-subtle {{
      font-size: 12px;
      color: #6b7280;
      margin-top: 2px;
    }}
    .table-wrap {{
      max-height: 760px;
      overflow: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    thead th {{
      position: sticky;
      top: 0;
      background: #79bdd8;
      color: #111827;
      text-align: left;
      padding: 8px;
      border-bottom: 1px solid #a5b5c8;
    }}
    tbody tr {{
      cursor: pointer;
    }}
    tbody tr:hover {{
      background: #eef6fb;
    }}
    tbody tr.selected {{
      background: #dceef9;
    }}
    td {{
      border-bottom: 1px solid #e3e8ef;
      padding: 8px;
      vertical-align: top;
    }}
    td .name {{
      font-weight: 700;
      margin-bottom: 3px;
    }}
    td .meta {{
      color: #5b6472;
      font-size: 12px;
    }}
    #chart {{
      width: 100%;
      height: 760px;
    }}
    .footer {{
      margin-top: 10px;
      color: #5b6472;
      font-size: 12px;
    }}
    @media (max-width: 1000px) {{
      .grid {{
        grid-template-columns: 1fr;
      }}
      #chart {{
        height: 520px;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>US Holiday Candlestick Dashboard ({start_year}-{end_year})</h1>
    <div class="subtitle">Click any holiday to view {symbol} candlesticks for +/-{days_each_side} calendar days around the observed holiday date. Tier-1 overlays: CPI (blue), NFP (purple), PCE (orange).</div>
    <div class="grid">
      <div class="panel">
        <div class="panel-header">Holiday List</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Holiday</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody id="holiday-rows"></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header" id="chart-title">Candlestick</div>
        <div class="stats-row">
          <div class="stat-card">
            <div class="stat-title">Window Return (t-3d -> t+3d)</div>
            <div class="stat-line" id="window-return-line">-</div>
            <div class="stat-subtle" id="window-return-sub">-</div>
          </div>
          <div class="stat-card">
            <div class="stat-title">Holiday-Day Move (Close-Open)</div>
            <div class="stat-line" id="holiday-move-line">-</div>
            <div class="stat-subtle" id="holiday-move-sub">-</div>
          </div>
        </div>
        <div id="chart"></div>
      </div>
    </div>
    <div class="footer">Generated: {generated_at} | Symbol: {symbol} | TY filter range [{VALID_TY_MIN}, {VALID_TY_MAX}]</div>
  </div>

  <script>
    const HOLIDAYS = {payload};
    let selectedId = null;
    const CHART_CONFIG = {{
      responsive: true,
      scrollZoom: true,
      displayModeBar: true,
      displaylogo: false,
      doubleClick: 'reset+autosize'
    }};

    function pointsToBps(points) {{
      return points * 8.0;
    }}

    function fmtSigned(value, decimals=2) {{
      if (!Number.isFinite(value)) return 'NA';
      const abs = Math.abs(value).toFixed(decimals);
      return `${{value >= 0 ? '+' : '-'}}${{abs}}`;
    }}

    function setStats(item) {{
      const c = item.candles || {{}};
      const x = c.x || [];
      const opens = c.open || [];
      const closes = c.close || [];

      const windowLineEl = document.getElementById('window-return-line');
      const windowSubEl = document.getElementById('window-return-sub');
      const holidayLineEl = document.getElementById('holiday-move-line');
      const holidaySubEl = document.getElementById('holiday-move-sub');

      if (!x.length || !opens.length || !closes.length) {{
        windowLineEl.textContent = 'NA';
        windowSubEl.textContent = 'No candles in window';
        holidayLineEl.textContent = 'NA';
        holidaySubEl.textContent = 'No candles in window';
        return;
      }}

      // Window return: first available close -> last available close in the plotted +/- window.
      const windowPoints = closes[closes.length - 1] - closes[0];
      const windowBps = pointsToBps(windowPoints);
      windowLineEl.textContent = `${{fmtSigned(windowBps, 2)}} bps`;
      windowSubEl.textContent = `From first close (${{
        closes[0].toFixed(4)
      }}) to last close (${{closes[closes.length - 1].toFixed(4)}})`;

      // Holiday-day move: close-open for observed holiday date, else nearest trading session.
      const dayMap = new Map();
      for (let i = 0; i < x.length; i++) {{
        const day = String(x[i]).slice(0, 10);
        if (!dayMap.has(day)) {{
          dayMap.set(day, {{ open: opens[i], close: closes[i] }});
        }} else {{
          const d = dayMap.get(day);
          d.close = closes[i];
        }}
      }}

      const observed = item.observed_date;
      let useDay = observed;
      let usedNearest = false;
      if (!dayMap.has(useDay)) {{
        const keys = Array.from(dayMap.keys());
        if (!keys.length) {{
          holidayLineEl.textContent = 'NA';
          holidaySubEl.textContent = 'No daily candles available';
          return;
        }}
        const obsTs = new Date(observed + 'T00:00:00').getTime();
        keys.sort((a, b) => {{
          const da = Math.abs(new Date(a + 'T00:00:00').getTime() - obsTs);
          const db = Math.abs(new Date(b + 'T00:00:00').getTime() - obsTs);
          if (da !== db) return da - db;
          // tie-break: earlier date first
          return a < b ? -1 : 1;
        }});
        useDay = keys[0];
        usedNearest = true;
      }}

      const dayData = dayMap.get(useDay);
      const dayPoints = dayData.close - dayData.open;
      const dayBps = pointsToBps(dayPoints);
      holidayLineEl.textContent = `${{fmtSigned(dayBps, 2)}} bps`;
      holidaySubEl.textContent = usedNearest
        ? `Observed date closed: used nearest session ${{useDay}}`
        : `Observed session ${{useDay}} (open ${{dayData.open.toFixed(4)}} -> close ${{dayData.close.toFixed(4)}})`;
    }}

    function aggregateCandlesForPlot(candles, targetCount) {{
      const x = candles.x || [];
      const o = candles.open || [];
      const h = candles.high || [];
      const l = candles.low || [];
      const cl = candles.close || [];
      const n = x.length;
      return {{
        x: x,
        open: o,
        high: h,
        low: l,
        close: cl,
        rawCount: n,
        plottedCount: n,
        bucketSize: 1
      }};
    }}

    function rowHtml(item) {{
      const noteHtml = item.note ? `<div class="meta">${{item.note}}</div>` : '';
      const obsDiff = item.actual_date !== item.observed_date
        ? `<div class="meta">Observed: ${{item.observed_date}}</div>`
        : '';
      return `
        <tr data-id="${{item.id}}">
          <td>
            <div class="name">${{item.name}}</div>
            <div class="meta">Year: ${{item.year}}</div>
          </td>
          <td>
            <div>${{item.display_date}}</div>
            ${{obsDiff}}
            ${{noteHtml}}
          </td>
        </tr>
      `;
    }}

    function renderRows() {{
      const body = document.getElementById('holiday-rows');
      body.innerHTML = HOLIDAYS.map(rowHtml).join('');
      body.querySelectorAll('tr').forEach((tr) => {{
        tr.addEventListener('click', () => {{
          const id = Number(tr.getAttribute('data-id'));
          selectHoliday(id);
        }});
      }});
    }}

    function selectHoliday(id) {{
      selectedId = id;
      document.querySelectorAll('#holiday-rows tr').forEach((tr) => {{
        const rid = Number(tr.getAttribute('data-id'));
        tr.classList.toggle('selected', rid === id);
      }});
      const item = HOLIDAYS.find((h) => h.id === id);
      if (item) renderChart(item);
    }}

    function renderChart(item) {{
      const title = `${{item.name}} (${{item.year}})`;
      document.getElementById('chart-title').textContent = title;
      const c = item.candles || {{}};
      const x = c.x || [];
      if (!x.length) {{
        setStats(item);
        Plotly.newPlot('chart', [], {{
          title: 'No market data found for this window',
          paper_bgcolor: 'white',
          plot_bgcolor: 'white'
        }}, CHART_CONFIG);
        return;
      }}

      const traces = [
        {{
          type: 'candlestick',
          x: [],
          open: [],
          high: [],
          low: [],
          close: [],
          name: '{symbol}',
          increasing: {{line: {{color: '#16a34a'}}}},
          decreasing: {{line: {{color: '#dc2626'}}}},
          hovertemplate: '<b>%{{x}}</b><br>' +
                         'Open: %{{open:.4f}}<br>' +
                         'High: %{{high:.4f}}<br>' +
                         'Low: %{{low:.4f}}<br>' +
                         'Close: %{{close:.4f}}<extra></extra>',
        }}
      ];
      const plotted = aggregateCandlesForPlot(c, 0);
      traces[0].x = plotted.x;
      traces[0].open = plotted.open;
      traces[0].high = plotted.high;
      traces[0].low = plotted.low;
      traces[0].close = plotted.close;

      const shapes = [];
      const annotations = [];
      const actualLine = `${{item.actual_date}}T12:00:00`;
      const observedLine = `${{item.observed_date}}T12:00:00`;

      shapes.push({{
        type: 'line',
        x0: observedLine,
        x1: observedLine,
        y0: 0,
        y1: 1,
        yref: 'paper',
        line: {{color: '#2563eb', width: 2, dash: 'dot'}}
      }});
      annotations.push({{
        x: observedLine,
        y: 1.0,
        yref: 'paper',
        text: 'Observed',
        showarrow: false,
        xanchor: 'left',
        yanchor: 'bottom',
        font: {{size: 11, color: '#1d4ed8'}}
      }});

      if (item.actual_date !== item.observed_date) {{
        shapes.push({{
          type: 'line',
          x0: actualLine,
          x1: actualLine,
          y0: 0,
          y1: 1,
          yref: 'paper',
          line: {{color: '#6b7280', width: 1, dash: 'dash'}}
        }});
        annotations.push({{
          x: actualLine,
          y: 1.0,
          yref: 'paper',
          text: 'Actual',
          showarrow: false,
          xanchor: 'right',
          yanchor: 'bottom',
          font: {{size: 11, color: '#6b7280'}}
        }});
      }}

      const tier1 = item.tier1_events || [];
      tier1.forEach((ev, idx) => {{
        const lineColor = ev.color || '#374151';
        const ts = ev.ts;
        shapes.push({{
          type: 'line',
          x0: ts,
          x1: ts,
          y0: 0,
          y1: 1,
          yref: 'paper',
          line: {{color: lineColor, width: 1.5, dash: 'dash'}}
        }});
        annotations.push({{
          x: ts,
          y: 1.02 + ((idx % 2) * 0.03),
          yref: 'paper',
          text: ev.label || 'Tier1',
          showarrow: false,
          xanchor: 'left',
          yanchor: 'bottom',
          font: {{size: 10, color: lineColor}}
        }});
      }});

      const layout = {{
        margin: {{l: 50, r: 20, t: 20, b: 50}},
        xaxis: {{title: 'Date/Time', rangeslider: {{visible: false}}}},
        yaxis: {{
          title: '{symbol} Price (Decimal)'
        }},
        dragmode: 'zoom',
        // Use point hover mode so Plotly does not inject unified default rows.
        hovermode: 'closest',
        paper_bgcolor: 'white',
        plot_bgcolor: 'white',
        shapes: shapes,
        annotations: annotations
      }};

      setStats(item);
      Plotly.newPlot('chart', traces, layout, CHART_CONFIG).catch(() => {{}});
    }}

    renderRows();
    if (HOLIDAYS.length) {{
      selectHoliday(HOLIDAYS[0].id);
    }}
  </script>
</body>
</html>
"""


def default_output_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "workspace" / "scenarios" / f"us_holiday_candlestick_dashboard_{ts}.html"


def parse_args() -> argparse.Namespace:
    now_year = datetime.now().year
    parser = argparse.ArgumentParser(description="Build US holiday candlestick dashboard")
    parser.add_argument("--start-year", type=int, default=2022, help="First year to include")
    parser.add_argument("--end-year", type=int, default=now_year, help="Last year to include")
    parser.add_argument("--symbol", default="TY", help="OHLCV symbol to query (default TY)")
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="Calendar days on each side of holiday for candlestick window",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="Output HTML path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.end_year < args.start_year:
        raise ValueError("end-year must be >= start-year")
    if args.days < 1:
        raise ValueError("days must be >= 1")

    data = build_dashboard_data(
        start_year=args.start_year,
        end_year=args.end_year,
        symbol=args.symbol,
        days_each_side=args.days,
    )

    html = generate_html(
        holidays_data=data,
        symbol=args.symbol,
        start_year=args.start_year,
        end_year=args.end_year,
        days_each_side=args.days,
    )

    out_path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"[ok] Dashboard written: {out_path}")
    print(f"[ok] Holidays included: {len(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
