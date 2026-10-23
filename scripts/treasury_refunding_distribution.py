#!/usr/bin/env python
"""
Treasury Refunding Announcement distribution (configurable window).
"""
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ism_manufacturing_pmi_pre_post_distribution import (  # noqa: E402
    _build_stats_lines,
    _compute_returns,
    _compute_stats,
    _extract_event_times,
    _get_last_n_values,
    _integer_bin_edges,
    _prepare_ohlcv,
)
from src.servers.analytics.core_plotting import plot_distribution  # noqa: E402
from src.servers.distro_analytics.db_reader import get_events_from_db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Treasury refunding distribution")
    parser.add_argument("--symbol", default="TY", help="Symbol prefix (e.g., TY, ZN)")
    parser.add_argument("--event-name", default="Treasury Refunding Announcement")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--start-offset-hours", type=float, default=0.0)
    parser.add_argument("--end-offset-hours", type=float, default=2.0)
    parser.add_argument(
        "--tag",
        default=None,
        help="Optional filename tag; defaults to window-based tag",
    )
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

    ohlcv_df = _prepare_ohlcv(f"{args.symbol}%", args.start_date)
    returns, rows, event_times = _compute_returns(
        ohlcv_df,
        events,
        start_offset=timedelta(hours=args.start_offset_hours),
        end_offset=timedelta(hours=args.end_offset_hours),
    )
    if not returns:
        raise SystemExit("Insufficient data to compute returns.")

    stats = _compute_stats(returns)
    last_two = _get_last_n_values(returns, event_times, n=2)
    bins = _integer_bin_edges(returns)
    start_minutes = int(round(args.start_offset_hours * 60))
    end_minutes = int(round(args.end_offset_hours * 60))
    tag = args.tag or f"t{start_minutes:+d}m_to_t{end_minutes:+d}m"
    title = f"{args.symbol} {args.event_name} (t{args.start_offset_hours:+.1f}h → t{args.end_offset_hours:+.1f}h)"
    stats_lines = _build_stats_lines(stats)
    if last_two:
        stats_lines.append(" ")
        stats_lines.append("Last 2 events:")
        for marker in last_two:
            stats_lines.append(marker["label"])

    plot = plot_distribution(
        values=returns,
        title=title,
        x_label=f"{args.symbol} Return (bps)",
        y_label="Frequency",
        stats_box=True,
        stats_lines=stats_lines,
        vertical_lines=last_two,
        bins=bins,
        output_dir=output_dir,
    )
    plot_path = None
    if plot.get("png_path"):
        plot_path = output_dir / f"treasury_refunding_{args.symbol}_{tag}.png"
        Path(plot["png_path"]).replace(plot_path)

    output = {
        "symbol": args.symbol,
        "event_name": args.event_name,
        "start_date": args.start_date,
        "start_offset_hours": args.start_offset_hours,
        "end_offset_hours": args.end_offset_hours,
        "stats": stats,
        "last_two": last_two,
        "plot_path": str(plot_path) if plot_path else None,
        "event_count": len(events),
        "return_count": len(returns),
    }
    json_path = output_dir / f"treasury_refunding_{args.symbol}_{tag}.json"
    csv_path = output_dir / f"treasury_refunding_{args.symbol}_{tag}.csv"
    json_path.write_text(json.dumps(output, indent=2))
    pd.DataFrame(rows).to_csv(csv_path, index=False)


if __name__ == "__main__":
    main()

