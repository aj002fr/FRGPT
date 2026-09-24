#!/usr/bin/env python3
"""Inventory AccessAPI database TBBO availability by date and symbol."""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import pyarrow.ipc as pa_ipc


API_BASE = "http://100.79.30.21:8001"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo"
SYMBOLS = ["TY", "FV", "TU", "US"]


def _fetch_count(api_base: str, symbol: str, start: str, end: str, timeout: int) -> tuple[int, str | None]:
    query = urllib.parse.urlencode({
        "source": "database",
        "schema": "tbbo",
        "symbols": symbol,
        "start": start,
        "end": end,
        "format": "arrow",
    })
    url = f"{api_base.rstrip('/')}/historical/trades?{query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
        table = pa_ipc.open_stream(io.BytesIO(raw)).read_all()
        return int(table.num_rows), None
    except Exception as exc:
        return 0, str(exc)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    dates = pd.bdate_range(start=args.start_date, end=args.end_date).strftime("%Y-%m-%d").tolist()
    rows: List[Dict[str, Any]] = []
    for date_text in dates:
        row: Dict[str, Any] = {"date": date_text}
        ok_symbols = 0
        for symbol in SYMBOLS:
            start = f"{date_text}T{args.probe_time}:00Z"
            end_dt = pd.Timestamp(start) + pd.Timedelta(minutes=args.probe_minutes)
            end = end_dt.isoformat().replace("+00:00", "Z")
            count, error = _fetch_count(args.api_base, symbol, start, end, int(args.timeout))
            row[f"{symbol}_count"] = count
            row[f"{symbol}_error"] = error
            if count > 0 and error is None:
                ok_symbols += 1
        row["all_symbols_ok"] = ok_symbols == len(SYMBOLS)
        rows.append(row)
        print(date_text, {symbol: row[f"{symbol}_count"] for symbol in SYMBOLS}, "ok" if row["all_symbols_ok"] else "partial")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    csv_path = output_dir / "tbbo_db_inventory.csv"
    summary_path = output_dir / "tbbo_db_inventory_summary.json"
    df.to_csv(csv_path, index=False)
    good_dates = df.loc[df["all_symbols_ok"], "date"].tolist()
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_base": args.api_base,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "probe_time": args.probe_time,
        "probe_minutes": int(args.probe_minutes),
        "symbols": SYMBOLS,
        "dates_checked": len(rows),
        "good_dates": good_dates,
        "bad_dates": df.loc[~df["all_symbols_ok"], "date"].tolist(),
        "artifacts": {
            "inventory_csv": str(csv_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory DB TBBO availability.")
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--start-date", default="2026-01-27")
    parser.add_argument("--end-date", default="2026-06-05")
    parser.add_argument("--probe-time", default="13:00")
    parser.add_argument("--probe-minutes", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({
        "dates_checked": summary["dates_checked"],
        "good_count": len(summary["good_dates"]),
        "first_good": summary["good_dates"][:3],
        "last_good": summary["good_dates"][-3:],
        "artifacts": summary["artifacts"],
    }, indent=2))


if __name__ == "__main__":
    main()
