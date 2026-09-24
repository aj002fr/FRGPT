#!/usr/bin/env python
"""
ISM Manufacturing PMI pre/post distribution (t-2h to t, t to t+2h).

Generates two histogram plots with custom stats boxes plus JSON/CSV outputs.
"""
import argparse
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


def _extract_event_times(events_df: pd.DataFrame) -> List[pd.Timestamp]:
    date_col = "event_date" if "event_date" in events_df.columns else "datetime"
    if date_col not in events_df.columns:
        raise ValueError("Event data missing event_date/datetime column.")
    event_dt = pd.to_datetime(events_df[date_col], errors="coerce", utc=True)
    event_dt = event_dt.dropna().dt.tz_convert("US/Eastern")
    event_dt = event_dt.sort_values().drop_duplicates()
    return event_dt.tolist()


def _prepare_ohlcv(symbol_pattern: str, start_date: str) -> pd.DataFrame:
    df = get_ohlcv_from_db(symbol_pattern=symbol_pattern, start_date=start_date)
    df = add_timezone(df, source_tz="UTC", target_tz="US/Eastern").reset_index()
    df["et_ts"] = pd.to_datetime(df["US/Eastern Timezone"])
    return df.sort_values("et_ts").reset_index(drop=True)


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


def _compute_returns(
    df: pd.DataFrame,
    events: List[pd.Timestamp],
    start_offset: timedelta,
    end_offset: timedelta,
) -> Tuple[List[float], List[Dict[str, str]], List[pd.Timestamp]]:
    returns: List[float] = []
    rows: List[Dict[str, str]] = []
    event_times: List[pd.Timestamp] = []
    for event_dt in events:
        window_start = event_dt + start_offset
        window_end = event_dt + end_offset
        ret = _compute_window_return(df, window_start, window_end)
        if ret is None:
            continue
        returns.append(ret)
        event_times.append(event_dt)
        rows.append(
            {
                "event_time_et": event_dt.isoformat(),
                "window_start_et": window_start.isoformat(),
                "window_end_et": window_end.isoformat(),
                "return_bps": f"{ret:.4f}",
            }
        )
    return returns, rows, event_times


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


def _integer_bin_edges(values: List[float]) -> List[float]:
    if not values:
        return [0.0, 1.0]
    min_v = float(np.floor(min(values)))
    max_v = float(np.ceil(max(values)))
    if max_v == min_v:
        max_v = min_v + 1.0
    return np.arange(min_v, max_v + 1.0, 1.0).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description="ISM Manufacturing PMI pre/post distribution")
    parser.add_argument("--symbol", default="TY", help="Symbol prefix (e.g., TY, ZN)")
    parser.add_argument("--event-name", default="ISM Manufacturing PMI")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--pre-hours", type=float, default=2.0)
    parser.add_argument("--post-hours", type=float, default=2.0)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "workspace" / "analysis"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    events_df = get_events_from_db(
        event_name_pattern=args.event_name,
        country="US",
        start_date=args.start_date,
        limit=50000,
    )
    if events_df.empty:
        raise SystemExit(f"No events found for {args.event_name} since {args.start_date}.")

    events = _extract_event_times(events_df)
    if not events:
        raise SystemExit("No valid event timestamps found.")

    symbol_pattern = f"{args.symbol}%"
    logger.info(f"Loading OHLCV for {symbol_pattern} from {args.start_date}")
    ohlcv_df = _prepare_ohlcv(symbol_pattern, args.start_date)

    pre_returns, pre_rows, pre_event_times = _compute_returns(
        ohlcv_df,
        events,
        start_offset=timedelta(hours=-args.pre_hours),
        end_offset=timedelta(hours=0),
    )
    post_returns, post_rows, post_event_times = _compute_returns(
        ohlcv_df,
        events,
        start_offset=timedelta(hours=0),
        end_offset=timedelta(hours=args.post_hours),
    )

    if not pre_returns or not post_returns:
        raise SystemExit("Insufficient data to compute pre/post returns.")

    pre_stats = _compute_stats(pre_returns)
    post_stats = _compute_stats(post_returns)
    pre_last2 = _get_last_n_values(pre_returns, pre_event_times, n=2)
    post_last2 = _get_last_n_values(post_returns, post_event_times, n=2)
    pre_bins = _integer_bin_edges(pre_returns)
    post_bins = _integer_bin_edges(post_returns)

    pre_title = f"{args.symbol} ISM Manufacturing PMI (t-{args.pre_hours}h → t)"
    post_title = f"{args.symbol} ISM Manufacturing PMI (t → t+{args.post_hours}h)"

    pre_plot = plot_distribution(
        values=pre_returns,
        title=pre_title,
        x_label=f"{args.symbol} Return (bps)",
        y_label="Frequency",
        stats_box=True,
        stats_lines=_build_stats_lines(pre_stats),
        vertical_lines=pre_last2,
        bins=pre_bins,
        output_dir=output_dir,
    )
    post_plot = plot_distribution(
        values=post_returns,
        title=post_title,
        x_label=f"{args.symbol} Return (bps)",
        y_label="Frequency",
        stats_box=True,
        stats_lines=_build_stats_lines(post_stats),
        vertical_lines=post_last2,
        bins=post_bins,
        output_dir=output_dir,
    )

    pre_plot_path = None
    if pre_plot.get("png_path"):
        pre_plot_path = output_dir / f"ism_mfg_pmi_pre_{args.symbol}.png"
        Path(pre_plot["png_path"]).replace(pre_plot_path)

    post_plot_path = None
    if post_plot.get("png_path"):
        post_plot_path = output_dir / f"ism_mfg_pmi_post_{args.symbol}.png"
        Path(post_plot["png_path"]).replace(post_plot_path)

    output = {
        "symbol": args.symbol,
        "event_name": args.event_name,
        "start_date": args.start_date,
        "pre_hours": args.pre_hours,
        "post_hours": args.post_hours,
        "pre_stats": pre_stats,
        "post_stats": post_stats,
        "plot_path_pre": str(pre_plot_path) if pre_plot_path else None,
        "plot_path_post": str(post_plot_path) if post_plot_path else None,
        "event_count": len(events),
        "pre_count": len(pre_returns),
        "post_count": len(post_returns),
    }

    json_path = output_dir / f"ism_mfg_pmi_pre_post_{args.symbol}.json"
    pre_csv_path = output_dir / f"ism_mfg_pmi_pre_{args.symbol}.csv"
    post_csv_path = output_dir / f"ism_mfg_pmi_post_{args.symbol}.csv"
    json_path.write_text(json.dumps(output, indent=2))
    pd.DataFrame(pre_rows).to_csv(pre_csv_path, index=False)
    pd.DataFrame(post_rows).to_csv(post_csv_path, index=False)

    logger.info(f"Saved pre plot: {pre_plot_path}")
    logger.info(f"Saved post plot: {post_plot_path}")
    logger.info(f"Saved stats: {json_path}")
    logger.info(f"Saved rows: {pre_csv_path}")
    logger.info(f"Saved rows: {post_csv_path}")


if __name__ == "__main__":
    main()

