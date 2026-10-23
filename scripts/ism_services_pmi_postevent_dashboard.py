#!/usr/bin/env python3
"""
ISM Services PMI Post-Event Dashboard (Standalone HTML).

Generates a self-contained HTML dashboard where the user can input
Actual/Consensus values, compute surprise percentile, and compare
distribution stats for:
1) All events
2) Similar events in the same surprise percentile bucket

Distributions are computed at: t+1h, t+2h, t->EOD (5:00pm ET same day, t = event time).
All calculations use rules/treasury_price_conversion.py for ticks/bps.
"""

import argparse
import base64
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from src.servers.analytics.core_plotting import plot_distribution
from src.servers.distro_analytics.db_reader import add_timezone, get_events_from_db, get_ohlcv_from_db
from rules.treasury_price_conversion import decimal_to_ticks, ticks_to_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


HORIZONS: List[Tuple[str, Optional[timedelta]]] = [
    ("t+1h", timedelta(hours=1)),
    ("t+2h", timedelta(hours=2)),
    ("t->EOD", None),
]

EOD_HOUR_ET = 17


def _extract_events(events_df: pd.DataFrame) -> List[Dict[str, object]]:
    date_col = "event_date" if "event_date" in events_df.columns else "datetime"
    if date_col not in events_df.columns:
        raise ValueError("Event data missing event_date/datetime column.")
    event_dt = pd.to_datetime(events_df[date_col], errors="coerce", utc=True)
    event_dt = event_dt.dropna().dt.tz_convert("US/Eastern")
    events: List[Dict[str, object]] = []
    for idx, dt in event_dt.items():
        row = events_df.loc[idx]
        actual = row.get("actual")
        consensus = row.get("consensus")
        if actual is None or consensus is None:
            continue
        try:
            actual_val = float(actual)
            consensus_val = float(consensus)
        except (TypeError, ValueError):
            continue
        events.append(
            {
                "event_dt": dt,
                "date": dt.strftime("%Y-%m-%d"),
                "actual": actual_val,
                "consensus": consensus_val,
            }
        )
    return sorted(events, key=lambda x: x["event_dt"])


def _prepare_ohlcv(symbol_pattern: str, start_date: str) -> pd.DataFrame:
    df = get_ohlcv_from_db(symbol_pattern=symbol_pattern, start_date=start_date)
    df = add_timezone(df, source_tz="UTC", target_tz="US/Eastern").reset_index()
    df["et_ts"] = pd.to_datetime(df["US/Eastern Timezone"])
    return df.sort_values("et_ts").reset_index(drop=True)


def _get_eod_timestamp(event_dt: pd.Timestamp) -> pd.Timestamp:
    # EOD is defined as 5:00pm ET on the same event date.
    return event_dt.replace(hour=EOD_HOUR_ET, minute=0, second=0, microsecond=0)


def _compute_window_return(
    df: pd.DataFrame,
    start_dt: pd.Timestamp,
    end_dt: pd.Timestamp,
) -> Optional[float]:
    mask = (df["et_ts"] >= start_dt) & (df["et_ts"] <= end_dt)
    window_df = df.loc[mask]
    if len(window_df) < 2:
        return None
    window_open = float(window_df["Open"].iloc[0])
    window_close = float(window_df["Close"].iloc[-1])
    points_move = window_close - window_open
    ticks_move = decimal_to_ticks(points_move)
    return float(ticks_to_bps(ticks_move))


def _percentile_rank(values: List[float], target: float) -> float:
    if not values:
        return 0.0
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    return (below + 0.5 * equal) / len(values) * 100.0


def _compute_stats(values: List[float]) -> Dict[str, float]:
    arr = np.array(values, dtype=float)
    pct = np.percentile(arr, [0, 1, 5, 25, 50, 75, 95, 99, 100])
    pos = arr[arr > 0]
    neg = arr[arr < 0]
    p_rally = len(pos) / len(arr)
    p_selloff = len(neg) / len(arr)
    expected_rally = p_rally * (pos.mean() if len(pos) else 0.0)
    expected_selloff = p_selloff * (neg.mean() if len(neg) else 0.0)
    return {
        "count": int(len(arr)),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p0": float(pct[0]),
        "p1": float(pct[1]),
        "p5": float(pct[2]),
        "p25": float(pct[3]),
        "p50": float(pct[4]),
        "p75": float(pct[5]),
        "p95": float(pct[6]),
        "p99": float(pct[7]),
        "p100": float(pct[8]),
        "p_rally": float(p_rally),
        "p_selloff": float(p_selloff),
        "expected_rally": float(expected_rally),
        "expected_selloff": float(expected_selloff),
        "pct_zero": float(_percentile_rank(values, 0.0)),
    }


def _build_stats_lines(stats: Dict[str, float]) -> List[str]:
    return [
        f"N = {stats['count']}",
        f"Mean = {stats['mean']:.2f} bps",
        f"Median = {stats['median']:.2f} bps",
        f"P1/P5/P25 = {stats['p1']:.2f}/{stats['p5']:.2f}/{stats['p25']:.2f} bps",
        f"P50/P75 = {stats['p50']:.2f}/{stats['p75']:.2f} bps",
        f"P95/P99 = {stats['p95']:.2f}/{stats['p99']:.2f} bps",
        f"Min/Max = {stats['p0']:.2f}/{stats['p100']:.2f} bps",
        f"Exp Rally = {stats['expected_rally']:.2f} bps (P={stats['p_rally']:.0%})",
        f"Exp Selloff = {stats['expected_selloff']:.2f} bps (P={stats['p_selloff']:.0%})",
        f"0 bps %ile = {stats['pct_zero']:.1f}",
    ]


def _integer_bin_edges(values: List[float]) -> List[float]:
    if not values:
        return [0.0, 1.0]
    min_v = float(np.floor(min(values)))
    max_v = float(np.ceil(max(values)))
    if min_v > 0:
        min_v = 0.0
    if max_v < 0:
        max_v = 0.0
    if max_v == min_v:
        max_v = min_v + 1.0
    return np.arange(min_v, max_v + 1.0, 1.0).tolist()


def _get_last_n_values(
    values: List[float],
    event_times: List[pd.Timestamp],
    n: int = 2,
) -> List[Dict[str, str]]:
    if not values or not event_times:
        return []
    ordered = sorted(zip(event_times, values), key=lambda x: x[0])
    last_n = ordered[-n:]
    markers: List[Dict[str, str]] = []
    for event_dt, val in last_n:
        markers.append(
            {
                "value": float(val),
                "label": f"{event_dt.strftime('%Y-%m-%d')} {val:+.2f} bps",
            }
        )
    return markers


def _encode_png(png_path: Path) -> str:
    payload = base64.b64encode(png_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _bucket_label(percentile: float, width: int) -> str:
    clamped = max(0.0, min(99.999, percentile))
    idx = int(clamped // width)
    low = idx * width
    high = low + width
    return f"{low:.0f}-{high:.0f}%"


def _build_bucket_labels(width: int) -> List[str]:
    return [f"{i:.0f}-{i + width:.0f}%" for i in range(0, 100, width)]


def _plot_distribution(
    values: List[float],
    title: str,
    output_dir: Path,
    stats_lines: List[str],
    vertical_lines: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
    if not values:
        return None
    bins = _integer_bin_edges(values)
    result = plot_distribution(
        values=values,
        title=title,
        x_label="Return (bps)",
        y_label="Frequency",
        stats_box=True,
        stats_lines=stats_lines,
        vertical_lines=vertical_lines or [],
        bins=bins,
        output_dir=output_dir,
    )
    png_path = result.get("png_path")
    if not png_path:
        return None
    png_path = Path(png_path)
    encoded = _encode_png(png_path)
    png_path.unlink(missing_ok=True)
    return encoded


def main() -> None:
    parser = argparse.ArgumentParser(description="ISM Services PMI post-event dashboard")
    parser.add_argument("--symbol", default="TY", help="Symbol prefix (e.g., TY, ZN)")
    parser.add_argument("--event-name", default="ISM Services PMI")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--bucket-width", type=int, default=10)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "workspace" / "analysis" / "ism_services_pmi_postevent"
    output_dir.mkdir(parents=True, exist_ok=True)

    events_df = get_events_from_db(
        event_name_pattern=args.event_name,
        country="US",
        start_date=args.start_date,
        limit=50000,
    )
    if events_df.empty:
        raise SystemExit(f"No events found for {args.event_name} since {args.start_date}.")

    events = _extract_events(events_df)
    if len(events) < 3:
        raise SystemExit("Insufficient events with actual/consensus data.")

    surprises = [evt["actual"] - evt["consensus"] for evt in events]
    surprise_std = float(np.std(surprises, ddof=1)) if len(surprises) > 1 else 0.0
    if surprise_std == 0.0:
        surprise_std = 1.0

    std_surprises = []
    for evt in events:
        surprise = evt["actual"] - evt["consensus"]
        std_surprise = surprise / surprise_std
        evt["surprise"] = float(surprise)
        evt["std_surprise"] = float(std_surprise)
        std_surprises.append(float(std_surprise))

    for evt in events:
        evt["surp_pct"] = _percentile_rank(std_surprises, evt["std_surprise"])
        evt["bucket"] = _bucket_label(evt["surp_pct"], args.bucket_width)

    symbol_pattern = f"{args.symbol}%"
    logger.info(f"Loading OHLCV for {symbol_pattern} from {args.start_date}")
    ohlcv_df = _prepare_ohlcv(symbol_pattern, args.start_date)

    for evt in events:
        for label, delta in HORIZONS:
            end_dt = _get_eod_timestamp(evt["event_dt"]) if delta is None else evt["event_dt"] + delta
            ret = _compute_window_return(ohlcv_df, evt["event_dt"], end_dt) if end_dt > evt["event_dt"] else None
            evt[f"ret_{label}"] = ret

    bucket_labels = _build_bucket_labels(args.bucket_width)

    plot_payload: Dict[str, Dict[str, object]] = {}
    for label, _ in HORIZONS:
        all_returns = [evt[f"ret_{label}"] for evt in events if evt[f"ret_{label}"] is not None]
        all_times = [evt["event_dt"] for evt in events if evt[f"ret_{label}"] is not None]
        if not all_returns:
            plot_payload[label] = {"all": {"img": None, "stats_lines": [], "count": 0}, "buckets": {}}
            continue
        all_stats = _compute_stats(all_returns)
        all_lines = _build_stats_lines(all_stats)
        all_markers = _get_last_n_values(all_returns, all_times, n=2)
        all_title = f"{args.symbol} ISM Services PMI {label} (All Events)"
        all_img = _plot_distribution(
            values=all_returns,
            title=all_title,
            output_dir=output_dir,
            stats_lines=all_lines,
            vertical_lines=all_markers,
        )
        buckets: Dict[str, Dict[str, object]] = {}
        for bucket in bucket_labels:
            bucket_returns = [
                evt[f"ret_{label}"]
                for evt in events
                if evt[f"ret_{label}"] is not None and evt["bucket"] == bucket
            ]
            bucket_times = [
                evt["event_dt"]
                for evt in events
                if evt[f"ret_{label}"] is not None and evt["bucket"] == bucket
            ]
            if not bucket_returns:
                buckets[bucket] = {"img": None, "stats_lines": [], "count": 0}
                continue
            bucket_stats = _compute_stats(bucket_returns)
            bucket_lines = _build_stats_lines(bucket_stats)
            bucket_markers = _get_last_n_values(bucket_returns, bucket_times, n=2)
            bucket_title = f"{args.symbol} ISM Services PMI {label} ({bucket})"
            bucket_img = _plot_distribution(
                values=bucket_returns,
                title=bucket_title,
                output_dir=output_dir,
                stats_lines=bucket_lines,
                vertical_lines=bucket_markers,
            )
            buckets[bucket] = {
                "img": bucket_img,
                "stats_lines": bucket_lines,
                "count": int(len(bucket_returns)),
            }
        plot_payload[label] = {
            "all": {"img": all_img, "stats_lines": all_lines, "count": int(len(all_returns))},
            "buckets": buckets,
        }

    latest = events[-1]
    default_actual = latest["actual"]
    default_consensus = latest["consensus"]

    dashboard_data = {
        "meta": {
            "event_name": args.event_name,
            "symbol": args.symbol,
            "start_date": args.start_date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_count": len(events),
            "surprise_std": surprise_std,
        },
        "bucket_width": args.bucket_width,
        "bucket_labels": bucket_labels,
        "std_surprises": std_surprises,
        "events": [
            {
                "date": evt["date"],
                "actual": evt["actual"],
                "consensus": evt["consensus"],
                "surprise": evt["surprise"],
                "std_surprise": evt["std_surprise"],
                "surp_pct": evt["surp_pct"],
                "bucket": evt["bucket"],
                "returns": {label: evt[f"ret_{label}"] for label, _ in HORIZONS},
            }
            for evt in events
        ],
        "plots": plot_payload,
        "horizons": [label for label, _ in HORIZONS],
        "defaults": {
            "actual": default_actual,
            "consensus": default_consensus,
        },
    }

    html = _build_html(dashboard_data)
    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "workspace"
        / "scenarios"
        / f"ism_services_pmi_postevent_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    output_path.write_text(html, encoding="utf-8")

    json_path = output_dir / "ism_services_pmi_postevent_summary.json"
    json_path.write_text(json.dumps(dashboard_data["meta"], indent=2))

    logger.info(f"Saved dashboard: {output_path}")
    logger.info(f"Saved summary: {json_path}")


def _build_html(data: Dict[str, object]) -> str:
    payload = json.dumps(data)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ISM Services PMI Post-Event Dashboard</title>
  <style>
    :root {{
      --bg-primary: #0b111b;
      --bg-secondary: #111827;
      --bg-tertiary: #1f2937;
      --text-primary: #f9fafb;
      --text-secondary: #9ca3af;
      --border-color: #374151;
      --accent-blue: #60a5fa;
      --accent-green: #34d399;
      --accent-orange: #fb923c;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      padding: 24px;
    }}
    .container {{ max-width: 1800px; margin: 0 auto; }}
    header {{ margin-bottom: 24px; border-bottom: 1px solid var(--border-color); padding-bottom: 16px; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 6px; }}
    .subtitle {{ color: var(--text-secondary); font-size: 0.95rem; }}
    .controls {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 12px 14px;
    }}
    label {{ font-size: 0.85rem; color: var(--text-secondary); }}
    input[type="number"] {{
      width: 100%;
      margin-top: 6px;
      padding: 8px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-color);
      background: var(--bg-tertiary);
      color: var(--text-primary);
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .metric {{
      background: var(--bg-tertiary);
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
    }}
    .metric .label {{ font-size: 0.8rem; color: var(--text-secondary); }}
    .metric .value {{ font-size: 1.1rem; margin-top: 4px; color: var(--accent-blue); }}
    .horizon {{
      margin-top: 22px;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px;
    }}
    .horizon h2 {{ font-size: 1.1rem; margin-bottom: 12px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 14px;
    }}
    .plot-card {{
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 12px;
    }}
    .plot-card h3 {{ font-size: 0.95rem; margin-bottom: 8px; }}
    .plot-card img {{
      width: 100%;
      border-radius: 6px;
      border: 1px solid var(--border-color);
      background: #0f172a;
    }}
    ul.stats {{
      list-style: none;
      margin-top: 10px;
      font-size: 0.85rem;
      color: var(--text-secondary);
      line-height: 1.4;
    }}
    .note {{
      margin-top: 16px;
      color: var(--text-secondary);
      font-size: 0.85rem;
    }}
    .bucket-badge {{
      color: var(--accent-green);
      font-weight: 600;
    }}
    .warn {{
      color: var(--accent-orange);
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>ISM Services PMI Post-Event Dashboard</h1>
      <div class="subtitle" id="metaLine"></div>
    </header>

    <section class="controls">
      <div class="card">
        <label for="actualInput">Actual</label>
        <input id="actualInput" type="number" step="0.01" />
      </div>
      <div class="card">
        <label for="consensusInput">Consensus</label>
        <input id="consensusInput" type="number" step="0.01" />
      </div>
    </section>

    <section class="metric-grid">
      <div class="metric">
        <div class="label">Surprise</div>
        <div class="value" id="surpriseVal">--</div>
      </div>
      <div class="metric">
        <div class="label">Std Surprise (z)</div>
        <div class="value" id="stdSurpriseVal">--</div>
      </div>
      <div class="metric">
        <div class="label">Surprise Percentile</div>
        <div class="value" id="percentileVal">--</div>
      </div>
      <div class="metric">
        <div class="label">Bucket</div>
        <div class="value bucket-badge" id="bucketVal">--</div>
      </div>
      <div class="metric">
        <div class="label">Similar Events Count</div>
        <div class="value" id="bucketCount">--</div>
      </div>
    </section>

    <div id="horizonContainer"></div>

    <p class="note">
      Surprise percentiles are computed from historical standardized surprises
      (surprise / historical std dev). Returns use TY ticks/bps conversions from
      rules/treasury_price_conversion.py. Histograms are binned so 0 bps is a boundary.
    </p>
  </div>

  <script>
    const DATA = {payload};

    function percentileRank(values, target) {{
      if (!values.length) return 0;
      let below = 0;
      let equal = 0;
      for (const v of values) {{
        if (v < target) below += 1;
        if (v === target) equal += 1;
      }}
      return (below + 0.5 * equal) / values.length * 100;
    }}

    function bucketLabel(percentile, width) {{
      const clamped = Math.max(0, Math.min(99.999, percentile));
      const idx = Math.floor(clamped / width);
      const low = idx * width;
      const high = low + width;
      return `${{low}}-${{high}}%`;
    }}

    function renderStats(listEl, lines) {{
      listEl.innerHTML = "";
      if (!lines || !lines.length) {{
        const li = document.createElement("li");
        li.textContent = "No data for this bucket.";
        li.className = "warn";
        listEl.appendChild(li);
        return;
      }}
      for (const line of lines) {{
        const li = document.createElement("li");
        li.textContent = line;
        listEl.appendChild(li);
      }}
    }}

    function renderHorizons() {{
      const container = document.getElementById("horizonContainer");
      container.innerHTML = "";
      for (const horizon of DATA.horizons) {{
        const section = document.createElement("section");
        section.className = "horizon";
        section.innerHTML = `
          <h2>${{horizon}}</h2>
          <div class="grid">
            <div class="plot-card">
              <h3>All Events</h3>
              <img id="img-all-${{horizon}}" />
              <ul class="stats" id="stats-all-${{horizon}}"></ul>
            </div>
            <div class="plot-card">
              <h3>Similar Events (Bucket)</h3>
              <img id="img-bucket-${{horizon}}" />
              <ul class="stats" id="stats-bucket-${{horizon}}"></ul>
            </div>
          </div>
        `;
        container.appendChild(section);
      }}
    }}

    function updateDashboard() {{
      const actual = parseFloat(document.getElementById("actualInput").value);
      const consensus = parseFloat(document.getElementById("consensusInput").value);
      if (!Number.isFinite(actual) || !Number.isFinite(consensus)) {{
        return;
      }}
      const surprise = actual - consensus;
      const stdSurprise = surprise / DATA.meta.surprise_std;
      const percentile = percentileRank(DATA.std_surprises, stdSurprise);
      const bucket = bucketLabel(percentile, DATA.bucket_width);

      document.getElementById("surpriseVal").textContent = surprise.toFixed(3);
      document.getElementById("stdSurpriseVal").textContent = stdSurprise.toFixed(3);
      document.getElementById("percentileVal").textContent = `${{percentile.toFixed(1)}}%`;
      document.getElementById("bucketVal").textContent = bucket;

      for (const horizon of DATA.horizons) {{
        const allData = DATA.plots[horizon].all;
        const bucketData = DATA.plots[horizon].buckets[bucket] || {{ img: null, stats_lines: [], count: 0 }};

        const allImg = document.getElementById(`img-all-${{horizon}}`);
        const bucketImg = document.getElementById(`img-bucket-${{horizon}}`);
        allImg.src = allData.img || "";
        bucketImg.src = bucketData.img || "";

        renderStats(document.getElementById(`stats-all-${{horizon}}`), allData.stats_lines);
        renderStats(document.getElementById(`stats-bucket-${{horizon}}`), bucketData.stats_lines);
      }}

      const bucketCount = DATA.plots[DATA.horizons[0]].buckets[bucket]?.count ?? 0;
      document.getElementById("bucketCount").textContent = bucketCount;
    }}

    function init() {{
      document.getElementById("metaLine").textContent =
        `${{DATA.meta.symbol}} | ${{DATA.meta.event_name}} | Events: ${{DATA.meta.event_count}} | Start: ${{DATA.meta.start_date}} | Generated: ${{DATA.meta.generated_at}}`;

      document.getElementById("actualInput").value = DATA.defaults.actual;
      document.getElementById("consensusInput").value = DATA.defaults.consensus;

      renderHorizons();
      updateDashboard();

      document.getElementById("actualInput").addEventListener("input", updateDashboard);
      document.getElementById("consensusInput").addEventListener("input", updateDashboard);
    }}

    init();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()

