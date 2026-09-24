#!/usr/bin/env python
"""
Overnight Session (6pm-8am ET) Distribution Dashboard.

Generates a standalone HTML with:
- Returns distribution (bps)
- Volatility distribution (High-Low, bps)
- Distro-style stats box (mean, percentiles, expected rally/selloff, 0 bps percentile)
- Filters: date range, year, month, day-of-week
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.servers.distro_analytics.db_reader import add_timezone, get_ohlcv_from_db
from rules.treasury_price_conversion import decimal_to_ticks, ticks_to_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


def _prepare_ohlcv(symbol_pattern: str, start_date: str) -> pd.DataFrame:
    df = get_ohlcv_from_db(symbol_pattern=symbol_pattern, start_date=start_date)
    df = add_timezone(df, source_tz="UTC", target_tz="US/Eastern").reset_index()
    df["et_ts"] = pd.to_datetime(df["US/Eastern Timezone"], errors="coerce")
    if df["et_ts"].dt.tz is None:
        df["et_ts"] = df["et_ts"].dt.tz_localize("US/Eastern")
    return df.sort_values("et_ts").reset_index(drop=True)


def _compute_session_metrics(
    df: pd.DataFrame,
    session_start: pd.Timestamp,
    session_end: pd.Timestamp,
) -> Optional[Dict[str, float]]:
    mask = (df["et_ts"] >= session_start) & (df["et_ts"] <= session_end)
    session_df = df.loc[mask]
    if len(session_df) < 2:
        return None
    open_px = float(session_df["Open"].iloc[0])
    close_px = float(session_df["Close"].iloc[-1])
    high_px = float(session_df["High"].max())
    low_px = float(session_df["Low"].min())
    return {
        "return_bps": float(ticks_to_bps(decimal_to_ticks(close_px - open_px))),
        "vol_bps": float(ticks_to_bps(decimal_to_ticks(high_px - low_px))),
        "open": open_px,
        "close": close_px,
        "high": high_px,
        "low": low_px,
    }


def _build_sessions(
    df: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> List[Dict[str, float]]:
    sessions: List[Dict[str, float]] = []
    for d in pd.date_range(start_date, end_date, freq="D"):
        end_date_str = d.strftime("%Y-%m-%d")
        start_date_str = (d - timedelta(days=1)).strftime("%Y-%m-%d")
        session_start = pd.Timestamp(f"{start_date_str} 18:00:00", tz="US/Eastern")
        session_end = pd.Timestamp(f"{end_date_str} 08:00:00", tz="US/Eastern")
        metrics = _compute_session_metrics(df, session_start, session_end)
        if metrics is None:
            continue
        sessions.append(
            {
                "session_end_date": end_date_str,
                "session_label": f"{start_date_str} 18:00 -> {end_date_str} 08:00",
                "year": d.year,
                "month": d.month,
                "day_of_week": d.strftime("%a"),
                "return_bps": round(metrics["return_bps"], 4),
                "vol_bps": round(metrics["vol_bps"], 4),
            }
        )
    return sessions


def _build_html(data: List[Dict[str, float]], symbol: str, start_date: str, end_date: str) -> str:
    payload = json.dumps(data)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{symbol} Overnight Session Distribution (6pm-8am ET)</title>
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
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      line-height: 1.5;
    }}
    .container {{ max-width: 1800px; margin: 0 auto; padding: 30px; }}
    header {{
      margin-bottom: 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--border-color);
    }}
    h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 8px; }}
    .subtitle {{ color: var(--text-secondary); font-size: 1rem; }}
    .filters {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }}
    .filter-card {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 12px 14px;
    }}
    .filter-title {{
      font-size: 0.9rem;
      color: var(--text-secondary);
      margin-bottom: 8px;
    }}
    .filter-row {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    input[type="date"] {{
      background: var(--bg-tertiary);
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      padding: 6px 10px;
      border-radius: 6px;
    }}
    .checkbox-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(70px, 1fr));
      gap: 6px;
    }}
    label.checkbox {{
      display: flex;
      gap: 6px;
      align-items: center;
      font-size: 0.85rem;
      color: var(--text-secondary);
      cursor: pointer;
    }}
    .summary-bar {{
      display: flex;
      gap: 20px;
      margin-top: 16px;
      flex-wrap: wrap;
    }}
    .summary-item {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      padding: 10px 14px;
      border-radius: 8px;
    }}
    .summary-label {{ color: var(--text-secondary); font-size: 0.8rem; }}
    .summary-value {{ color: var(--accent-teal); font-size: 1.1rem; font-weight: 600; }}
    .panel {{
      margin-top: 24px;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px;
    }}
    .panel h2 {{
      font-size: 1.1rem;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .badge {{
      background: var(--accent-purple);
      color: #000;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
    }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 20px;
    }}
    .stats-box {{
      margin-top: 10px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 0.85rem;
      color: var(--text-secondary);
      line-height: 1.6;
    }}
    .stats-box span {{ color: var(--text-primary); font-weight: 600; }}
    .empty-state {{
      text-align: center;
      color: var(--text-secondary);
      padding: 30px 10px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{symbol} Overnight Session Distribution</h1>
      <div class="subtitle">Session window: 6:00pm → 8:00am ET | Data range: {start_date} to {end_date}</div>
      <div class="filters">
        <div class="filter-card">
          <div class="filter-title">Date range</div>
          <div class="filter-row">
            <input type="date" id="startDate" />
            <span style="color: var(--text-secondary);">to</span>
            <input type="date" id="endDate" />
          </div>
        </div>
        <div class="filter-card">
          <div class="filter-title">Year</div>
          <div id="yearFilters" class="checkbox-grid"></div>
        </div>
        <div class="filter-card">
          <div class="filter-title">Month</div>
          <div id="monthFilters" class="checkbox-grid"></div>
        </div>
        <div class="filter-card">
          <div class="filter-title">Day of week</div>
          <div id="dowFilters" class="checkbox-grid"></div>
        </div>
      </div>
      <div class="summary-bar">
        <div class="summary-item">
          <div class="summary-label">Sessions (filtered)</div>
          <div class="summary-value" id="sessionCount">0</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Date range</div>
          <div class="summary-value" id="rangeLabel">--</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Session window</div>
          <div class="summary-value">6pm → 8am</div>
        </div>
      </div>
    </header>

    <div class="panel">
      <h2>Returns Distribution <span class="badge">bps</span></h2>
      <div class="chart-grid">
        <div>
          <div id="returnsChart" style="height: 360px;"></div>
          <div id="returnsStats" class="stats-box"></div>
        </div>
      </div>
    </div>

    <div class="panel">
      <h2>Volatility Distribution <span class="badge" style="background: var(--accent-orange);">bps</span></h2>
      <div class="chart-grid">
        <div>
          <div id="volChart" style="height: 360px;"></div>
          <div id="volStats" class="stats-box"></div>
        </div>
      </div>
    </div>

    <div class="panel" style="margin-top: 18px;">
      <div class="subtitle">Generated: {generated_at}</div>
    </div>
  </div>

  <script>
    const sessions = {payload};

    const MIN_DATE = sessions.length ? sessions[0].session_end_date : null;
    const MAX_DATE = sessions.length ? sessions[sessions.length - 1].session_end_date : null;

    function uniqueValues(arr, key) {{
      return [...new Set(arr.map(x => x[key]))].sort();
    }}

    function percentileRank(sorted, value) {{
      if (!sorted.length) return 0;
      let below = 0;
      let equal = 0;
      sorted.forEach(v => {{
        if (v < value) below += 1;
        else if (v === value) equal += 1;
      }});
      return ((below + 0.5 * equal) / sorted.length) * 100;
    }}

    function gaussianKDE(data) {{
      const n = data.length;
      const mean = data.reduce((a, b) => a + b, 0) / n;
      const std = Math.sqrt(data.reduce((sum, v) => sum + (v - mean) ** 2, 0) / n);
      const bandwidth = std * Math.pow(n, -0.2) || 1;
      return function(x) {{
        let sum = 0;
        for (let i = 0; i < n; i++) {{
          const z = (x - data[i]) / bandwidth;
          sum += Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
        }}
        return sum / (n * bandwidth);
      }};
    }}

    function buildCheckboxes(containerId, values, labelFormatter) {{
      const container = document.getElementById(containerId);
      container.innerHTML = '';
      values.forEach(val => {{
        const id = `${{containerId}}-${{val}}`;
        const label = document.createElement('label');
        label.className = 'checkbox';
        label.innerHTML = `<input type="checkbox" id="${{id}}" checked /> ${{labelFormatter(val)}}`;
        container.appendChild(label);
      }});
    }}

    function getCheckedValues(containerId) {{
      const container = document.getElementById(containerId);
      const inputs = container.querySelectorAll('input[type="checkbox"]');
      return Array.from(inputs)
        .filter(i => i.checked)
        .map(i => i.id.replace(`${{containerId}}-`, ''));
    }}

    function applyFilters() {{
      const startDate = document.getElementById('startDate').value;
      const endDate = document.getElementById('endDate').value;
      const years = getCheckedValues('yearFilters').map(v => parseInt(v, 10));
      const months = getCheckedValues('monthFilters').map(v => parseInt(v, 10));
      const dows = getCheckedValues('dowFilters');

      return sessions.filter(s => {{
        if (startDate && s.session_end_date < startDate) return false;
        if (endDate && s.session_end_date > endDate) return false;
        if (years.length && !years.includes(s.year)) return false;
        if (months.length && !months.includes(s.month)) return false;
        if (dows.length && !dows.includes(s.day_of_week)) return false;
        return true;
      }});
    }}

    function buildStats(values) {{
      const sorted = [...values].sort((a, b) => a - b);
      const n = sorted.length;
      if (!n) {{
        return {{
          n: 0, mean: 0, median: 0, p1: 0, p5: 0, p25: 0, p75: 0, p95: 0, p99: 0,
          min: 0, max: 0, expRally: 0, expSelloff: 0, zeroPct: 0
        }};
      }}
      const mean = values.reduce((a, b) => a + b, 0) / n;
      const median = n % 2 === 0 ? (sorted[n/2 - 1] + sorted[n/2]) / 2 : sorted[Math.floor(n/2)];
      const percentile = p => sorted[Math.max(0, Math.min(sorted.length - 1, Math.ceil((p / 100) * n) - 1))];
      const p1 = percentile(1);
      const p5 = percentile(5);
      const p25 = percentile(25);
      const p75 = percentile(75);
      const p95 = percentile(95);
      const p99 = percentile(99);
      const min = sorted[0];
      const max = sorted[n - 1];
      const pos = values.filter(v => v > 0);
      const neg = values.filter(v => v < 0);
      const expRally = (pos.length / n) * (pos.length ? pos.reduce((a, b) => a + b, 0) / pos.length : 0);
      const expSelloff = (neg.length / n) * (neg.length ? neg.reduce((a, b) => a + b, 0) / neg.length : 0);
      const zeroPct = percentileRank(sorted, 0).toFixed(1);
      return {{ n, mean, median, p1, p5, p25, p75, p95, p99, min, max, expRally, expSelloff, zeroPct }};
    }}

    function renderDistribution(values, chartId, statsId, title, showZeroLine) {{
      const chartDiv = document.getElementById(chartId);
      const statsDiv = document.getElementById(statsId);
      if (!values.length) {{
        chartDiv.innerHTML = '<div class="empty-state">No data</div>';
        statsDiv.textContent = '';
        return;
      }}

      const stats = buildStats(values);
      statsDiv.innerHTML = `
        <div><span>N</span>: ${{stats.n}}</div>
        <div><span>Mean</span>: ${{stats.mean.toFixed(2)}} bps</div>
        <div><span>Median</span>: ${{stats.median.toFixed(2)}} bps</div>
        <div><span>P1 / P5 / P25</span>: ${{stats.p1.toFixed(2)}} / ${{stats.p5.toFixed(2)}} / ${{stats.p25.toFixed(2)}}</div>
        <div><span>P75 / P95 / P99</span>: ${{stats.p75.toFixed(2)}} / ${{stats.p95.toFixed(2)}} / ${{stats.p99.toFixed(2)}}</div>
        <div><span>Min / Max</span>: ${{stats.min.toFixed(2)}} / ${{stats.max.toFixed(2)}}</div>
        <div><span>Exp Rally</span>: ${{stats.expRally.toFixed(2)}} bps</div>
        <div><span>Exp Selloff</span>: ${{stats.expSelloff.toFixed(2)}} bps</div>
        <div><span>0 bps %ile</span>: ${{stats.zeroPct}}</div>
      `;

      const min = Math.min(...values);
      const max = Math.max(...values);
      const binStart = Math.floor(min);
      const binEnd = Math.ceil(max);
      const kde = gaussianKDE(values);
      const xRange = (max - min) || 1;
      const xPadding = xRange * 0.15;
      const xGrid = [];
      const yGrid = [];
      const numPoints = 300;
      for (let i = 0; i < numPoints; i++) {{
        const x = min - xPadding + (xRange + 2 * xPadding) * i / (numPoints - 1);
        xGrid.push(x);
        yGrid.push(kde(x));
      }}

      const histTrace = {{
        x: values,
        type: 'histogram',
        histnorm: 'probability density',
        opacity: 0.6,
        marker: {{ color: 'skyblue' }},
        xbins: {{ start: binStart, end: binEnd, size: 1 }},
        hovertemplate: '%{{x:.2f}}<br>Density: %{{y:.4f}}<extra></extra>'
      }};
      const kdeTrace = {{
        x: xGrid,
        y: yGrid,
        type: 'scatter',
        mode: 'lines',
        line: {{ color: 'darkblue', width: 2 }},
        hoverinfo: 'skip'
      }};
      const layout = {{
        title: {{ text: title, font: {{ size: 13, color: '#333' }} }},
        xaxis: {{
          title: {{ text: 'Value (bps)', font: {{ size: 11 }} }},
          gridcolor: '#e0e0e0',
          zeroline: showZeroLine,
          zerolinecolor: '#888'
        }},
        yaxis: {{
          title: {{ text: 'Density', font: {{ size: 11 }} }},
          gridcolor: '#e0e0e0'
        }},
        barmode: 'overlay',
        height: 320,
        margin: {{ t: 35, r: 30, b: 45, l: 55 }},
        paper_bgcolor: 'white',
        plot_bgcolor: 'white',
        showlegend: false,
        shapes: showZeroLine ? [{{
          type: 'line',
          x0: 0, x1: 0,
          y0: 0, y1: 1,
          yref: 'paper',
          line: {{ color: '#16a34a', width: 2, dash: 'dot' }}
        }}] : []
      }};

      Plotly.react(chartId, [histTrace, kdeTrace], layout, {{ responsive: true, displayModeBar: false }});
    }}

    function renderAll() {{
      const filtered = applyFilters();
      const returns = filtered.map(s => s.return_bps);
      const vols = filtered.map(s => s.vol_bps);
      document.getElementById('sessionCount').textContent = filtered.length.toString();
      if (filtered.length) {{
        const dates = filtered.map(s => s.session_end_date).sort();
        document.getElementById('rangeLabel').textContent = `${{dates[0]}} → ${{dates[dates.length - 1]}}`;
      }} else {{
        document.getElementById('rangeLabel').textContent = '--';
      }}
      renderDistribution(returns, 'returnsChart', 'returnsStats', 'Overnight Returns (bps)', true);
      renderDistribution(vols, 'volChart', 'volStats', 'Overnight Volatility (bps)', false);
    }}

    function init() {{
      if (!sessions.length) return;
      const years = uniqueValues(sessions, 'year');
      const months = uniqueValues(sessions, 'month');
      const dows = uniqueValues(sessions, 'day_of_week');

      buildCheckboxes('yearFilters', years, v => v);
      buildCheckboxes('monthFilters', months, v => v.toString().padStart(2, '0'));
      buildCheckboxes('dowFilters', dows, v => v);

      document.getElementById('startDate').value = MIN_DATE;
      document.getElementById('endDate').value = MAX_DATE;

      ['startDate', 'endDate'].forEach(id => {{
        document.getElementById(id).addEventListener('change', renderAll);
      }});
      ['yearFilters', 'monthFilters', 'dowFilters'].forEach(id => {{
        document.getElementById(id).addEventListener('change', renderAll);
      }});

      renderAll();
    }}

    init();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Overnight session distribution dashboard")
    parser.add_argument("--symbol", default="TY", help="Symbol prefix (e.g., TY, ZN)")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--output", default=None, help="Output HTML path")
    args = parser.parse_args()

    symbol_pattern = f"{args.symbol}%"
    logger.info(f"Loading OHLCV for {symbol_pattern} from {args.start_date}")
    df = _prepare_ohlcv(symbol_pattern, args.start_date)
    if df.empty:
        raise SystemExit("No OHLCV data found for the given range.")

    sessions = _build_sessions(df, args.start_date, args.end_date)
    if not sessions:
        raise SystemExit("No overnight sessions found for the given range.")

    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "workspace"
        / "scenarios"
        / f"overnight_session_distribution_{args.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = _build_html(sessions, args.symbol, args.start_date, args.end_date)
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"Saved: {output_path}")


if __name__ == "__main__":
    main()

