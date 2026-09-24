#!/usr/bin/env python3
"""Record a V3 reconciliation fixture: Databento hist (stored side) + AccessAPI trades (live side).

Offline tool for the print_reconcile golden test. Reads the existing Databento parquet
cache and the AccessAPI historical endpoint; writes parquet files only under --out.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard_v3.core import (  # noqa: E402
    SYMBOL_PARENT_MAP,
    _cache_path_for_contract_trades,
    _select_front_month_per_day,
)
from scripts.aggression_flow_history_dash.backfill_accessapi import API_BASE, fetch_trades  # noqa: E402

LIVE_COLUMNS = ["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"]
EXPECTED_WINDOWS = (("11:31", "13:31"), ("12:30", "14:30"))


def expected_name(symbol: str, start: str, end: str) -> str:
    return f"expected_{symbol}_{start.replace(':', '')}_{end.replace(':', '')}.npz"


def window(frame: pd.DataFrame, day: str, start: str, end: str) -> pd.DataFrame:
    lo = pd.Timestamp(f"{day} {start}", tz="UTC")
    hi = pd.Timestamp(f"{day} {end}", tz="UTC")
    return frame.loc[(frame["ts"] >= lo) & (frame["ts"] <= hi)].reset_index(drop=True)


def write_expected(out: Path, day: str, symbols: list[str]) -> None:
    """Record the original matcher's output on 2h US-open windows (slow: minutes per window)."""
    import numpy as np

    from scripts.aggression_dashboard_v3 import print_reconcile as pr

    for symbol in symbols:
        hist = pr._as_trades(pd.read_parquet(out / f"hist_{symbol}.parquet"))
        live = pr._as_trades(pd.read_parquet(out / f"live_{symbol}.parquet"))
        for start, end in EXPECTED_WINDOWS:
            h = window(hist, day, start, end)
            lv = window(live, day, start, end)
            np.savez_compressed(
                out / expected_name(symbol, start, end),
                matched_live=pr._matched_incoming_reference(h, lv, symbol),
                matched_hist=pr._matched_incoming_reference(lv, h, symbol),
                n_hist=len(h),
                n_live=len(lv),
            )
            print(f"{symbol} {start}-{end}: expected written ({len(h):,} hist, {len(lv):,} live)", flush=True)


def _utc(day: str, hhmm: str) -> datetime:
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def hist_frame(symbol: str, day: datetime, start: datetime, end: datetime, cache_dir: Path | None) -> pd.DataFrame:
    """What V3's backfill stores for one symbol: front month per UTC day, dashboard_symbol set."""
    path = _cache_path_for_contract_trades(SYMBOL_PARENT_MAP[symbol], day)
    if cache_dir is not None:
        path = cache_dir / path.name
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df, _front = _select_front_month_per_day(df)
    df["dashboard_symbol"] = symbol
    df = df.loc[(df["ts"] >= start) & (df["ts"] <= end)].copy()
    df.sort_values("ts", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def live_frame(symbol: str, start: datetime, end: datetime, api_base: str, timeout: int) -> pd.DataFrame:
    """AccessAPI trades shaped like LiveTradeBuffer._record_to_trade rows."""
    raw = fetch_trades(symbol, start, end, api_base=api_base, timeout=timeout)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=LIVE_COLUMNS)
    out = pd.DataFrame({
        "ts": pd.to_datetime(raw["ts_event"], utc=True),
        "price": pd.to_numeric(raw["price"], errors="coerce").astype(float),
        "size": pd.to_numeric(raw["size"], errors="coerce"),
        "side": raw["side"].fillna("N").astype(str).str.upper().replace("", "N"),
        "raw_symbol": raw["symbol"].fillna("").astype(str),
    })
    ids = raw["instrument_id"].astype(str) if "instrument_id" in raw.columns else out["raw_symbol"]
    out["instrument_id"] = ids.where(ids.str.len() > 0, out["raw_symbol"])
    out["dashboard_symbol"] = symbol
    out = out.loc[out["ts"].notna() & (out["price"] > 0) & (out["size"] > 0)].copy()
    out["size"] = out["size"].astype(float).astype(int)
    out.sort_values("ts", inplace=True, kind="stable")
    out.reset_index(drop=True, inplace=True)
    return out[LIVE_COLUMNS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="UTC day of the Databento cache file, YYYY-MM-DD")
    parser.add_argument("--start-utc", default="11:00")
    parser.add_argument("--end-utc", default="15:30")
    parser.add_argument("--symbols", default=",".join(SYMBOL_PARENT_MAP))
    parser.add_argument("--cache-dir", type=Path, default=None, help="Databento trade cache dir (default: repo cache)")
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-only", action="store_true", help="only (re)write expected_*.npz from existing fixture files")
    args = parser.parse_args()

    if args.expected_only:
        write_expected(args.out, args.date, [s.strip().upper() for s in args.symbols.split(",") if s.strip()])
        return 0
    day = _utc(args.date, "00:00")
    start = _utc(args.date, args.start_utc)
    end = _utc(args.date, args.end_utc)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {"date": args.date, "start_utc": start.isoformat(), "end_utc": end.isoformat(), "symbols": {}}
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        hist = hist_frame(symbol, day, start, end, args.cache_dir)
        live = live_frame(symbol, start, end, args.api_base, args.timeout)
        hist.to_parquet(args.out / f"hist_{symbol}.parquet", index=False)
        live.to_parquet(args.out / f"live_{symbol}.parquet", index=False)
        manifest["symbols"][symbol] = {"hist_rows": len(hist), "live_rows": len(live)}
        print(f"{symbol}: hist {len(hist):,} rows, live {len(live):,} rows")
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
