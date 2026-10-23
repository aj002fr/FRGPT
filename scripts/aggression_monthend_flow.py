#!/usr/bin/env python
"""Flow-only month-end buying stats from the aggression TBBO feature matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "monthend"
PLOTLY_ASSET = PROJECT_ROOT / "workspace" / "scenarios" / "_assets" / "plotly-2.27.0.min.js"
DEFAULT_MONTH_END_DAYS = 3
DEFAULT_CLOSE_WINDOW_MIN = 90
REQUIRED_COLUMNS = {"bucket_ts", "et_date", "agg_cum_daily", "agg_1m", "minutes_since_open"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flow-only month-end aggression stats from tbbo_feature_matrix.csv"
    )
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="Input TBBO feature matrix CSV")
    parser.add_argument(
        "--month-end-days",
        type=int,
        default=DEFAULT_MONTH_END_DAYS,
        help="Trailing business days of each complete calendar month tagged as month-end",
    )
    parser.add_argument(
        "--close-window-min",
        type=int,
        default=DEFAULT_CLOSE_WINDOW_MIN,
        help="Trailing observed session minutes used for the close-window flow stat",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory")
    parser.add_argument("--html", action="store_true", help="Also write an intraday timing HTML chart")
    return parser.parse_args()


def load_matrix(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Matrix not found: {path}")
    data = pd.read_csv(path, parse_dates=["bucket_ts"])
    missing = sorted(REQUIRED_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(f"Matrix is missing required columns: {', '.join(missing)}")
    data["et_date"] = pd.to_datetime(data["et_date"]).dt.date
    data["minutes_since_open"] = pd.to_numeric(data["minutes_since_open"], errors="coerce")
    data["agg_cum_daily"] = pd.to_numeric(data["agg_cum_daily"], errors="coerce")
    data["agg_1m"] = pd.to_numeric(data["agg_1m"], errors="coerce").fillna(0.0)
    data = data.dropna(subset=["bucket_ts", "et_date", "minutes_since_open", "agg_cum_daily"])
    data.sort_values(["et_date", "bucket_ts"], inplace=True)
    return data


def trailing_business_days(year: int, month: int, n: int) -> set[pd.Timestamp]:
    first_day = pd.Timestamp(year=year, month=month, day=1)
    last_calendar_day = first_day + pd.offsets.MonthEnd(0)
    business_days = pd.bdate_range(first_day, last_calendar_day)
    if business_days.empty:
        return set()
    return set(pd.Timestamp(day).normalize() for day in business_days[-n:])


def tag_month_end_days(dates: pd.Series, month_end_days: int) -> set[Any]:
    if month_end_days <= 0:
        raise ValueError("--month-end-days must be > 0")
    observed = sorted(pd.to_datetime(pd.Series(dates).dropna().unique()))
    if not observed:
        return set()
    observed_by_month: dict[tuple[int, int], list[pd.Timestamp]] = {}
    for day in observed:
        ts = pd.Timestamp(day).normalize()
        observed_by_month.setdefault((ts.year, ts.month), []).append(ts)

    month_end_tags: set[Any] = set()
    for (year, month), month_days in observed_by_month.items():
        trailing = trailing_business_days(year, month, month_end_days)
        if not trailing:
            continue
        last_required = max(trailing)
        if max(month_days) < last_required:
            continue
        for day in month_days:
            if day in trailing:
                month_end_tags.add(day.date())
    return month_end_tags


def build_daily_stats(data: pd.DataFrame, month_end_days: int, close_window_min: int) -> pd.DataFrame:
    if close_window_min <= 0:
        raise ValueError("--close-window-min must be > 0")
    month_end_dates = tag_month_end_days(data["et_date"], month_end_days)
    rows: list[dict[str, Any]] = []
    for et_date, day_df in data.groupby("et_date", sort=True):
        day = day_df.sort_values("bucket_ts").copy()
        max_minute = float(day["minutes_since_open"].max())
        close_start = max_minute - close_window_min + 1
        close_df = day[day["minutes_since_open"] >= close_start]
        net_flow = float(day["agg_cum_daily"].iloc[-1])
        close_flow = float(close_df["agg_1m"].sum())
        share_into_close = close_flow / net_flow if abs(net_flow) > 1e-9 else np.nan
        rows.append(
            {
                "date": et_date.isoformat(),
                "is_month_end": et_date in month_end_dates,
                "net_flow": net_flow,
                "close_window_flow": close_flow,
                "share_into_close": share_into_close,
                "close_window_start_minute": close_start,
                "session_last_minute": max_minute,
                "rows": int(len(day)),
            }
        )
    return pd.DataFrame(rows)


def build_intraday_profiles(data: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()
    minute_grid = np.arange(
        int(data["minutes_since_open"].min()),
        int(data["minutes_since_open"].max()) + 1,
    )
    month_end_lookup = dict(zip(daily["date"], daily["is_month_end"]))
    profiles: list[pd.Series] = []
    labels: list[dict[str, Any]] = []
    for et_date, day_df in data.groupby("et_date", sort=True):
        day = day_df.sort_values("bucket_ts")
        series = (
            day.groupby("minutes_since_open")["agg_cum_daily"]
            .last()
            .reindex(minute_grid)
            .ffill()
            .fillna(0.0)
        )
        profiles.append(series)
        labels.append({"date": et_date.isoformat(), "is_month_end": bool(month_end_lookup.get(et_date.isoformat()))})
    if not profiles:
        return pd.DataFrame()
    profile_df = pd.DataFrame(profiles, index=[row["date"] for row in labels])
    profile_df.columns = minute_grid
    profile_df["is_month_end"] = [row["is_month_end"] for row in labels]
    return profile_df


def summarize(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, mask in [("Normal", ~daily["is_month_end"]), ("Month-end", daily["is_month_end"])]:
        subset = daily.loc[mask].copy()
        rows.append(
            {
                "day_type": label,
                "n": int(len(subset)),
                "avg_net_flow": float(subset["net_flow"].mean()) if not subset.empty else np.nan,
                "median_net_flow": float(subset["net_flow"].median()) if not subset.empty else np.nan,
                "pct_net_buying": float((subset["net_flow"] > 0).mean()) if not subset.empty else np.nan,
                "avg_close_window_flow": float(subset["close_window_flow"].mean()) if not subset.empty else np.nan,
                "avg_share_into_close": float(subset["share_into_close"].replace([np.inf, -np.inf], np.nan).mean())
                if not subset.empty
                else np.nan,
            }
        )
    return pd.DataFrame(rows)


def fmt_num(value: Any, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{decimals}f}"


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{100.0 * float(value):.1f}%"


def print_summary(summary: pd.DataFrame, daily: pd.DataFrame, month_end_days: int, close_window_min: int) -> None:
    print()
    print("Month-End Flow Stats (flow-only)")
    print("=" * 72)
    print(
        "Descriptive flow only: this does not separate scheduled-event or headline flow. "
        "The close-window share is the flow-only hint for mechanical extension buying."
    )
    print(f"Month-end definition: trailing {month_end_days} business day(s); close window: last {close_window_min} observed minutes.")
    print()
    header = f"{'Day type':<12} {'N':>5} {'Avg net':>14} {'Median net':>14} {'% buying':>10} {'Avg close':>14} {'Close share':>12}"
    print(header)
    print("-" * len(header))
    for row in summary.to_dict(orient="records"):
        print(
            f"{row['day_type']:<12} "
            f"{int(row['n']):>5} "
            f"{fmt_num(row['avg_net_flow']):>14} "
            f"{fmt_num(row['median_net_flow']):>14} "
            f"{fmt_pct(row['pct_net_buying']):>10} "
            f"{fmt_num(row['avg_close_window_flow']):>14} "
            f"{fmt_pct(row['avg_share_into_close']):>12}"
        )
    month_end_rows = daily[daily["is_month_end"]].copy()
    print()
    print("Month-end days")
    print("-" * 72)
    if month_end_rows.empty:
        print("No complete month-end days found in the matrix.")
        return
    for row in month_end_rows.to_dict(orient="records"):
        print(
            f"{row['date']}: net={fmt_num(row['net_flow'])}, "
            f"close={fmt_num(row['close_window_flow'])}, "
            f"share={fmt_pct(row['share_into_close'])}"
        )


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    return float(value)


def write_html(out_path: Path, profiles: pd.DataFrame, summary: pd.DataFrame, daily: pd.DataFrame) -> None:
    if profiles.empty:
        raise ValueError("No intraday profiles available for HTML output")
    if not PLOTLY_ASSET.exists():
        raise FileNotFoundError(f"Plotly asset not found: {PLOTLY_ASSET}")

    month_end_profiles = profiles[profiles["is_month_end"]].drop(columns=["is_month_end"])
    normal_profiles = profiles[~profiles["is_month_end"]].drop(columns=["is_month_end"])
    minutes = [int(col) for col in month_end_profiles.columns]
    payload = {
        "minutes": minutes,
        "monthEndMean": [_json_value(v) for v in month_end_profiles.mean(axis=0).tolist()],
        "normalMean": [_json_value(v) for v in normal_profiles.mean(axis=0).tolist()],
        "monthEndDates": daily.loc[daily["is_month_end"], "date"].tolist(),
        "summaryRows": summary.to_dict(orient="records"),
    }
    plotly_js = PLOTLY_ASSET.read_text(encoding="utf-8")
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Month-End Flow Timing</title>
  <script>{plotly_js}</script>
  <style>
    body {{ margin: 24px; background: #0f172a; color: #e2e8f0; font-family: Segoe UI, Arial, sans-serif; }}
    .note {{ color: #94a3b8; max-width: 980px; line-height: 1.4; }}
    #chart {{ width: 100%; height: 620px; }}
    table {{ border-collapse: collapse; margin-top: 18px; }}
    th, td {{ border: 1px solid #334155; padding: 6px 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
  </style>
</head>
<body>
  <h2>Month-End Flow Timing (Flow-Only)</h2>
  <p class="note">Descriptive flow only. This does not separate scheduled-event or headline-driven flow; the close-window timing is the flow-only hint that buying is mechanical extension rather than news.</p>
  <div id="chart"></div>
  <div id="summary"></div>
  <script>
    const payload = {payload_json};
    Plotly.newPlot("chart", [
      {{
        x: payload.minutes,
        y: payload.normalMean,
        mode: "lines",
        name: "Normal days avg cumulative flow",
        line: {{ color: "#60a5fa", width: 2 }}
      }},
      {{
        x: payload.minutes,
        y: payload.monthEndMean,
        mode: "lines",
        name: "Month-end days avg cumulative flow",
        line: {{ color: "#34d399", width: 3 }}
      }}
    ], {{
      paper_bgcolor: "#0f172a",
      plot_bgcolor: "#111827",
      font: {{ color: "#e2e8f0" }},
      xaxis: {{ title: "Minutes since session open" }},
      yaxis: {{ title: "Cumulative signed flow (DV01-weighted units)", zeroline: true }},
      margin: {{ l: 80, r: 40, t: 40, b: 70 }},
      legend: {{ orientation: "h" }}
    }});
    const rows = payload.summaryRows.map(row => `
      <tr>
        <td>${{row.day_type}}</td>
        <td>${{row.n}}</td>
        <td>${{Math.round(row.avg_net_flow).toLocaleString()}}</td>
        <td>${{Math.round(row.median_net_flow).toLocaleString()}}</td>
        <td>${{(100 * row.pct_net_buying).toFixed(1)}}%</td>
        <td>${{Math.round(row.avg_close_window_flow).toLocaleString()}}</td>
        <td>${{(100 * row.avg_share_into_close).toFixed(1)}}%</td>
      </tr>
    `).join("");
    document.getElementById("summary").innerHTML = `
      <h3>Summary</h3>
      <table>
        <thead><tr><th>Day type</th><th>N</th><th>Avg net</th><th>Median net</th><th>% buying</th><th>Avg close</th><th>Close share</th></tr></thead>
        <tbody>${{rows}}</tbody>
      </table>
      <p class="note">Tagged month-end dates: ${{payload.monthEndDates.join(", ") || "none"}}</p>
    `;
  </script>
</body>
</html>
""".format(plotly_js=plotly_js, payload_json=json.dumps(payload))
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    args = parse_args()
    matrix_path = Path(args.matrix)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_matrix(matrix_path)
    daily = build_daily_stats(data, args.month_end_days, args.close_window_min)
    summary = summarize(daily)

    daily_path = out_dir / "monthend_flow_daily.csv"
    daily.to_csv(daily_path, index=False)
    print_summary(summary, daily, args.month_end_days, args.close_window_min)
    print()
    print(f"Wrote daily stats: {daily_path}")

    if args.html:
        profiles = build_intraday_profiles(data, daily)
        html_path = out_dir / "monthend_flow_timing.html"
        write_html(html_path, profiles, summary, daily)
        print(f"Wrote timing chart: {html_path}")


if __name__ == "__main__":
    main()
