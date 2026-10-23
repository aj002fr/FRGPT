#!/usr/bin/env python3
"""
Overlay full-year time series on the same axis (interactive Plotly HTML).
Uses PostgreSQL ohlcv_1m data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import pandas as pd
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from rules import treasury_price_conversion as tconv

ET = ZoneInfo("America/New_York")
BASE_YEAR = 2000
TREASURY_MIN = 80.0
TREASURY_MAX = 150.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Overlay each year of OHLCV data on a single Plotly chart."
    )
    parser.add_argument("--symbol-pattern", default="TY%")
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--resample", default="15min")
    parser.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--units", default=None, choices=["price", "ticks", "bps"])
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def _is_treasury_pattern(symbol_pattern: str) -> bool:
    prefix = symbol_pattern.upper().replace("%", "")
    return prefix in {"TY", "ZN", "US", "ZB", "TU", "ZT", "FV", "ZF"}


def _get_pg_connection():
    return psycopg2.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        dbname=settings.PG_DATABASE,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
    )


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    start = datetime(year, 1, 1, tzinfo=ET)
    end = datetime(year + 1, 1, 1, tzinfo=ET)
    return start, end


def _get_year_range(symbol_pattern: str) -> tuple[int | None, int | None]:
    sql = f"""
        SELECT MIN(ts_event) AS min_ts, MAX(ts_event) AS max_ts
        FROM {settings.PG_OHLCV_TABLE}
        WHERE symbol ILIKE %s
    """
    conn = _get_pg_connection()
    try:
        df = pd.read_sql_query(sql, conn, params=[symbol_pattern])
    finally:
        conn.close()
    if df.empty or df.iloc[0].isna().any():
        return None, None
    min_ts = pd.to_datetime(df.loc[0, "min_ts"], utc=True).tz_convert(ET)
    max_ts = pd.to_datetime(df.loc[0, "max_ts"], utc=True).tz_convert(ET)
    return int(min_ts.year), int(max_ts.year)


def _fetch_year_data(
    symbol_pattern: str,
    year: int,
    resample: str,
    normalize: bool,
    units: str,
) -> tuple[pd.Series, pd.Series] | None:
    start_ts, end_ts = _year_bounds(year)
    sql = f"""
        SELECT ts_event, close
        FROM {settings.PG_OHLCV_TABLE}
        WHERE symbol ILIKE %s
          AND ts_event >= %s
          AND ts_event < %s
    """
    params: list[object] = [symbol_pattern, start_ts, end_ts]
    if _is_treasury_pattern(symbol_pattern):
        sql += " AND close >= %s AND close <= %s"
        params.extend([TREASURY_MIN, TREASURY_MAX])
    sql += " ORDER BY ts_event ASC"
    conn = _get_pg_connection()
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return None
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert(ET)
    df = df.set_index("ts_event").sort_index()

    if resample.lower() not in {"1min", "1m"}:
        df = df.resample(resample).last().dropna()

    closes = df["close"].astype(float)
    if normalize:
        delta = closes - closes.iloc[0]
        if units == "ticks":
            y_values = delta.apply(tconv.points_to_ticks)
        elif units == "bps":
            y_values = delta.apply(tconv.points_to_bps)
        else:
            y_values = delta
    else:
        y_values = closes

    base_start = datetime(BASE_YEAR, 1, 1, tzinfo=ET)
    year_start = datetime(year, 1, 1, tzinfo=ET)
    x_values = base_start + (y_values.index - year_start)
    return pd.Series(x_values, index=y_values.index), y_values


def _build_figure(
    traces: list[dict[str, object]],
    title: str,
    y_label: str,
) -> go.Figure:
    fig = go.Figure()
    for trace in traces:
        fig.add_trace(
            go.Scattergl(
                x=list(trace["x"]),
                y=list(trace["y"]),
                name=str(trace["name"]),
                mode="lines",
                line={"width": 1.4},
            )
        )
    fig.update_layout(
        title=title,
        xaxis_title="Calendar Day (aligned)",
        yaxis_title=y_label,
        hovermode="x unified",
        template="plotly_dark",
        legend={"orientation": "h"},
        legend_title_text="Year (click to isolate)",
        margin={"l": 60, "r": 20, "t": 60, "b": 50},
    )
    fig.update_xaxes(
        tickformat="%b %d",
        rangeslider={"visible": True},
    )
    return fig


def _write_html(fig: go.Figure, output_path: str) -> None:
    plot_json = fig.to_plotly_json()
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Yearly Overlay</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    body {{ margin: 0; background: #0b0f1a; color: #e5e7eb; font-family: Arial, sans-serif; }}
    .toolbar {{
      display: flex; gap: 12px; align-items: center; padding: 12px 16px;
      border-bottom: 1px solid #1f2937; background: #0f172a;
    }}
    label {{ font-size: 0.9rem; color: #cbd5f5; }}
    select, button {{
      background: #111827; color: #e5e7eb; border: 1px solid #374151;
      border-radius: 6px; padding: 6px 8px;
    }}
    #chart {{ width: 100%; height: calc(100vh - 54px); }}
  </style>
</head>
<body>
  <div class="toolbar">
    <label for="start-month">Start:</label>
    <select id="start-month">
      <option value="1">Jan</option>
      <option value="2">Feb</option>
      <option value="3">Mar</option>
      <option value="4">Apr</option>
      <option value="5">May</option>
      <option value="6">Jun</option>
      <option value="7">Jul</option>
      <option value="8">Aug</option>
      <option value="9">Sep</option>
      <option value="10">Oct</option>
      <option value="11">Nov</option>
      <option value="12">Dec</option>
    </select>
    <label for="end-month">End:</label>
    <select id="end-month">
      <option value="1">Jan</option>
      <option value="2">Feb</option>
      <option value="3">Mar</option>
      <option value="4">Apr</option>
      <option value="5">May</option>
      <option value="6">Jun</option>
      <option value="7">Jul</option>
      <option value="8">Aug</option>
      <option value="9">Sep</option>
      <option value="10">Oct</option>
      <option value="11">Nov</option>
      <option value="12">Dec</option>
    </select>
    <button id="apply-window">Apply</button>
  </div>
  <div id="chart"></div>
  <script>
    const BASE_YEAR = {BASE_YEAR};
    const fig = {json.dumps(plot_json, cls=PlotlyJSONEncoder)};
    const config = {{
      scrollZoom: true,
      displaylogo: false,
      responsive: true
    }};
    const chart = document.getElementById("chart");
    Plotly.newPlot(chart, fig.data, fig.layout, config).then(() => {{
      const baseData = fig.data.map(trace => {{
        const x = trace.x || [];
        const y = trace.y || [];
        const xMs = x.map(v => new Date(v).getTime());
        return {{ x, y, xMs }};
      }});

      function normalizeToWindow(startMs, endMs) {{
        if (!startMs || !endMs) {{
          Plotly.relayout(chart, {{'yaxis.autorange': true}});
          return;
        }}
        baseData.forEach((series, idx) => {{
          const xMs = series.xMs;
          const y = series.y;
          let startIdx = xMs.findIndex(v => v >= startMs);
          if (startIdx < 0) return;
          const offset = y[startIdx];
          const yAdj = y.map(v => v - offset);
          Plotly.restyle(chart, {{ y: [yAdj] }}, [idx]);
        }});
        Plotly.relayout(chart, {{'yaxis.autorange': true}});
      }}

      function setWindow(startMonth, endMonth) {{
        const start = new Date(BASE_YEAR, startMonth - 1, 1);
        const end = new Date(BASE_YEAR, endMonth, 1);
        Plotly.relayout(chart, {{'xaxis.range': [start, end]}});
        normalizeToWindow(start.getTime(), end.getTime());
      }}

      document.getElementById("apply-window").addEventListener("click", () => {{
        let startMonth = parseInt(document.getElementById("start-month").value, 10);
        let endMonth = parseInt(document.getElementById("end-month").value, 10);
        if (Number.isNaN(startMonth) || Number.isNaN(endMonth)) return;
        if (endMonth < startMonth) {{
          const tmp = startMonth;
          startMonth = endMonth;
          endMonth = tmp;
        }}
        setWindow(startMonth, endMonth);
      }});
    }});
  </script>
</body>
</html>
"""
    Path(output_path).write_text(html, encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.units is None:
        if args.normalize and _is_treasury_pattern(args.symbol_pattern):
            args.units = "ticks"
        else:
            args.units = "price"
    if args.units != "price" and not args.normalize:
        raise SystemExit("--units ticks/bps requires --normalize (use --normalize)")

    start_year, end_year = args.start_year, args.end_year
    if start_year is None or end_year is None:
        min_year, max_year = _get_year_range(args.symbol_pattern)
        if min_year is None or max_year is None:
            raise SystemExit("No data found for symbol pattern.")
        start_year = min_year if start_year is None else start_year
        end_year = max_year if end_year is None else end_year

    traces = []
    for year in range(start_year, end_year + 1):
        result = _fetch_year_data(
            args.symbol_pattern, year, args.resample, args.normalize, args.units
        )
        if result is None:
            continue
        x_values, y_values = result
        traces.append({"x": x_values, "y": y_values, "name": year})

    if not traces:
        raise SystemExit("No data returned for requested years.")

    y_label = "Price"
    if args.normalize and args.units == "ticks":
        y_label = "Change (ticks)"
    elif args.normalize and args.units == "bps":
        y_label = "Change (bps)"
    elif args.normalize:
        y_label = "Change (price)"

    title = f"{args.symbol_pattern} yearly overlay ({start_year}-{end_year})"
    fig = _build_figure(traces, title, y_label)

    output = args.output
    if not output:
        out_dir = PROJECT_ROOT / "workspace" / "analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        symbol_slug = args.symbol_pattern.replace("%", "").lower()
        output = str(out_dir / f"yearly_overlay_{symbol_slug}_{start_year}_{end_year}.html")
    _write_html(fig, output)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

