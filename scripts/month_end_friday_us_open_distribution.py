#!/usr/bin/env python
"""
Month-end Friday US Open Distribution (08:00-17:00 ET).
"""
import argparse
import json
import logging
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from src.servers.analytics.core_plotting import plot_distribution
from src.servers.distro_analytics.db_reader import add_timezone, get_ohlcv_from_db
from rules.treasury_price_conversion import points_to_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


def _build_month_end_dates(dates: List[date]) -> List[date]:
    month_ends: Dict[Tuple[int, int], date] = {}
    for d in dates:
        key = (d.year, d.month)
        if key not in month_ends or d > month_ends[key]:
            month_ends[key] = d
    return sorted(month_ends.values())


def _is_friday(dt: date) -> bool:
    return dt.weekday() == 4


def _compute_session_returns(
    df: pd.DataFrame,
    target_dates: set,
    session_start: time,
    session_end: time,
) -> Tuple[List[float], List[Dict[str, float]]]:
    returns: List[float] = []
    rows: List[Dict[str, float]] = []
    for dt, day_df in df.groupby("date"):
        if dt not in target_dates:
            continue
        mask = (day_df["time"] >= session_start) & (day_df["time"] <= session_end)
        session_df = day_df.loc[mask]
        if len(session_df) < 2:
            continue
        session_open = float(session_df["Open"].iloc[0])
        session_close = float(session_df["Close"].iloc[-1])
        ret_bps = points_to_bps(session_close - session_open)
        returns.append(ret_bps)
        rows.append({"date": dt.isoformat(), "return_bps": round(ret_bps, 4)})
    return returns, rows


def _compute_stats(values: List[float]) -> Dict[str, float]:
    arr = np.array(values, dtype=float)
    pct = np.percentile(arr, [0, 1, 5, 50, 95, 99, 100])
    pos = arr[arr > 0]
    neg = arr[arr < 0]
    expected_rally = (len(pos) / len(arr)) * (pos.mean() if len(pos) else 0.0)
    expected_selloff = (len(neg) / len(arr)) * (neg.mean() if len(neg) else 0.0)
    return {
        "count": int(len(arr)),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p0": float(pct[0]),
        "p1": float(pct[1]),
        "p5": float(pct[2]),
        "p50": float(pct[3]),
        "p95": float(pct[4]),
        "p99": float(pct[5]),
        "p100": float(pct[6]),
        "expected_rally": float(expected_rally),
        "expected_selloff": float(expected_selloff),
    }


def _build_stats_lines(stats: Dict[str, float]) -> List[str]:
    return [
        f"N = {stats['count']}",
        f"Mean = {stats['mean']:.2f} bps",
        f"Median = {stats['median']:.2f} bps",
        f"P1 = {stats['p1']:.2f} bps",
        f"P5 = {stats['p5']:.2f} bps",
        f"P95 = {stats['p95']:.2f} bps",
        f"P99 = {stats['p99']:.2f} bps",
        f"0th pct = {stats['p0']:.2f} bps",
        f"Exp Rally = {stats['expected_rally']:.2f} bps",
        f"Exp Selloff = {stats['expected_selloff']:.2f} bps",
    ]


def _build_combined_stats_lines(all_stats: Dict[str, float], fri_stats: Dict[str, float]) -> List[str]:
    return [
        f"All Month-End N = {all_stats['count']}",
        f"All Mean = {all_stats['mean']:.2f} bps",
        f"All Median = {all_stats['median']:.2f} bps",
        f"All P5/P95 = {all_stats['p5']:.2f}/{all_stats['p95']:.2f} bps",
        f"All P1/P99 = {all_stats['p1']:.2f}/{all_stats['p99']:.2f} bps",
        f"All 0th = {all_stats['p0']:.2f} bps",
        f"All Exp Rally = {all_stats['expected_rally']:.2f} bps",
        f"All Exp Selloff = {all_stats['expected_selloff']:.2f} bps",
        " ",
        f"Friday N = {fri_stats['count']}",
        f"Friday Mean = {fri_stats['mean']:.2f} bps",
        f"Friday Median = {fri_stats['median']:.2f} bps",
        f"Friday P5/P95 = {fri_stats['p5']:.2f}/{fri_stats['p95']:.2f} bps",
        f"Friday P1/P99 = {fri_stats['p1']:.2f}/{fri_stats['p99']:.2f} bps",
        f"Friday 0th = {fri_stats['p0']:.2f} bps",
        f"Friday Exp Rally = {fri_stats['expected_rally']:.2f} bps",
        f"Friday Exp Selloff = {fri_stats['expected_selloff']:.2f} bps",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Month-end Friday US Open distribution")
    parser.add_argument("--symbol", default="TY", help="Symbol prefix (e.g., TY, ZN)")
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--session-start", default="08:00")
    parser.add_argument("--session-end", default="17:00")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "workspace" / "analysis"))
    args = parser.parse_args()

    start_date = f"{args.start_year}-01-01"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session_start = datetime.strptime(args.session_start, "%H:%M").time()
    session_end = datetime.strptime(args.session_end, "%H:%M").time()

    symbol_pattern = f"{args.symbol}%"
    logger.info(f"Loading {symbol_pattern} from {start_date}")
    df = get_ohlcv_from_db(symbol_pattern, start_date=start_date)
    df = add_timezone(df, source_tz="UTC", target_tz="US/Eastern").reset_index()
    df["timestamp"] = pd.to_datetime(df["US/Eastern Timezone"])
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time

    month_end_dates = _build_month_end_dates(df["date"].unique().tolist())
    month_end_set = set(month_end_dates)
    friday_set = {d for d in month_end_dates if _is_friday(d)}

    returns, rows = _compute_session_returns(df, month_end_set, session_start, session_end)
    friday_returns, friday_rows = _compute_session_returns(df, friday_set, session_start, session_end)

    if not returns:
        raise SystemExit("No month-end returns found for the given range.")

    all_stats = _compute_stats(returns)
    if friday_returns:
        fri_stats = _compute_stats(friday_returns)
    else:
        fri_stats = {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "p0": 0.0,
            "p1": 0.0,
            "p5": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "p100": 0.0,
            "expected_rally": 0.0,
            "expected_selloff": 0.0,
        }
    combined_stats_lines = _build_combined_stats_lines(all_stats, fri_stats)

    title_all = f"{args.symbol} Month-End Returns (08:00-17:00 ET)"
    plot_all = plot_distribution(
        values=returns,
        title=title_all,
        x_label=f"{args.symbol} Return (bps)",
        y_label="Frequency",
        stats_box=True,
        stats_lines=combined_stats_lines,
        overlay_values=friday_returns,
        overlay_label="Fridays",
        output_dir=output_dir,
    )
    plot_path = None
    if plot_all.get("png_path"):
        plot_path = output_dir / f"month_end_days_us_open_{args.symbol}.png"
        Path(plot_all["png_path"]).replace(plot_path)

    title_fri = f"{args.symbol} Month-End Friday Returns (08:00-17:00 ET)"
    plot_fri = plot_distribution(
        values=friday_returns if friday_returns else [0.0],
        title=title_fri,
        x_label=f"{args.symbol} Return (bps)",
        y_label="Frequency",
        stats_box=True,
        stats_lines=_build_stats_lines(fri_stats),
        output_dir=output_dir,
    )
    plot_path_fri = None
    if plot_fri.get("png_path"):
        plot_path_fri = output_dir / f"month_end_friday_us_open_{args.symbol}.png"
        Path(plot_fri["png_path"]).replace(plot_path_fri)

    output = {
        "symbol": args.symbol,
        "start_year": args.start_year,
        "session_start": args.session_start,
        "session_end": args.session_end,
        "stats_all": all_stats,
        "stats_friday": fri_stats,
        "plot_path_all": str(plot_path) if plot_path else None,
        "plot_path_friday": str(plot_path_fri) if plot_path_fri else None,
    }

    json_path = output_dir / f"month_end_days_us_open_{args.symbol}.json"
    csv_path = output_dir / f"month_end_days_us_open_{args.symbol}.csv"
    csv_path_fri = output_dir / f"month_end_friday_us_open_{args.symbol}.csv"
    json_path.write_text(json.dumps(output, indent=2))
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    pd.DataFrame(friday_rows).to_csv(csv_path_fri, index=False)

    logger.info(f"Saved plot: {plot_path}")
    logger.info(f"Saved Friday plot: {plot_path_fri}")
    logger.info(f"Saved stats: {json_path}")
    logger.info(f"Saved rows: {csv_path}")
    logger.info(f"Saved Friday rows: {csv_path_fri}")


if __name__ == "__main__":
    main()

