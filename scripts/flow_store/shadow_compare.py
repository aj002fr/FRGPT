#!/usr/bin/env python3
"""Milestone 3 check: compare a writer's tape and bars with AccessAPI historical for an interval.

For each symbol: per-key identity counts (tape vs historical), the frozen front contract vs the
previous-session volume leader, every sealed minute's bar vs a bar rebuilt from historical
trades (counts and contracts exact, DV01 flows within 1e-9 relative), coverage gaps, and
unsealed session minutes. Read-only on the writer's databases.

    python -m scripts.flow_store.shadow_compare --tape ...flow_tape.sqlite --history ...flow_history.sqlite \
        --start 2026-09-24T17:00Z --end 2026-09-24T19:00Z --out report.json
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard.core import load_dv01_table  # noqa: E402
from scripts.flow_store.aggregate import bars_from_trades, minute_floor_us  # noqa: E402
from scripts.flow_store.backfill import fetch_accessapi_trades  # noqa: E402
from scripts.flow_store.sessions import session_for_ns  # noqa: E402
from scripts.flow_store.settings import MINUTE_US, SYMBOL_ROOTS  # noqa: E402
from scripts.flow_store.store import BAR_FIELDS, minute_clocks  # noqa: E402

EXACT = ("buy_contracts", "sell_contracts", "signed_contracts", "trade_count")
FLOWS = ("buy_flow", "sell_flow", "signed_flow")


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def compare_symbol(tape: sqlite3.Connection, hist_db: sqlite3.Connection, symbol: str, lo: int, hi: int, dv01: float,
                   fetch=fetch_accessapi_trades) -> dict:
    hist_trades = [t for t in fetch(symbol, lo, hi) if lo <= t.ts_us < hi]
    tape_keys = collections.Counter(tuple(r) for r in tape.execute(
        "SELECT contract, ts_us, price_nanos, size, side FROM trade_tape WHERE symbol = ? AND ts_us >= ? AND ts_us < ?",
        (symbol, lo, hi)))
    hist_keys = collections.Counter(t.key for t in hist_trades)
    report = {
        "symbol": symbol,
        "trades": {"tape": sum(tape_keys.values()), "hist": sum(hist_keys.values()),
                   "tape_only": sum((tape_keys - hist_keys).values()), "hist_only": sum((hist_keys - tape_keys).values())},
        "examples": {"tape_only": [list(k) for k in list((tape_keys - hist_keys))[:5]],
                     "hist_only": [list(k) for k in list((hist_keys - tape_keys))[:5]]},
    }
    fronts = dict(tape.execute("SELECT session, contract FROM front_contract WHERE symbol = ?", (symbol,)).fetchall())
    report["front_contracts"] = fronts
    gaps = []
    cursor = lo
    for s, e in tape.execute("SELECT start_us, end_us FROM coverage WHERE symbol = ? AND end_us > ? AND start_us < ? ORDER BY start_us",
                             (symbol, lo, hi)).fetchall():
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < hi:
        gaps.append((cursor, hi))
    in_session_gaps = [(a, b) for a, b in gaps if session_for_ns(a * 1000) is not None]
    report["coverage_gaps_in_session"] = [[pd.Timestamp(a, unit="us", tz="UTC").isoformat(), (b - a) / 1e6] for a, b in in_session_gaps]
    mism = []
    unsealed = []
    minute = minute_floor_us(lo)
    by_front = collections.defaultdict(list)
    for t in hist_trades:
        session = session_for_ns(t.ts_us * 1000)
        if session is not None and fronts.get(session.isoformat()) == t.contract:
            by_front[minute_floor_us(t.ts_us)].append(t)
    compared = 0
    while minute + MINUTE_US <= hi:
        session = session_for_ns(minute * 1000)
        if session is not None:
            sealed = tape.execute("SELECT source FROM sealed_minutes WHERE symbol = ? AND minute_us = ?", (symbol, minute)).fetchone()
            if sealed is None:
                unsealed.append(pd.Timestamp(minute, unit="us", tz="UTC").isoformat())
            else:
                compared += 1
                expected = bars_from_trades(by_front.get(minute, []), dv01_unit=dv01).get(minute)
                ts_utc, _ = minute_clocks(minute)
                row = hist_db.execute(f"SELECT {', '.join(BAR_FIELDS)} FROM v1_aggress_flow_1m WHERE ts_utc = ? AND symbol = ?",
                                      (ts_utc, symbol)).fetchone()
                got = dict(zip(BAR_FIELDS, row)) if row else None
                problem = None
                if (expected is None) != (got is None):
                    problem = "presence"
                elif expected is not None:
                    exp = expected.as_dict()
                    if any(float(exp[f]) != float(got[f]) for f in EXACT):
                        problem = "counts"
                    elif any(not math.isclose(float(exp[f]), float(got[f]), rel_tol=1e-9, abs_tol=1e-9) for f in FLOWS):
                        problem = "flows"
                if problem:
                    mism.append({"minute": ts_utc, "problem": problem, "expected": expected.as_dict() if expected else None, "got": got})
        minute += MINUTE_US
    report["bars"] = {"compared": compared, "mismatches": len(mism), "examples": mism[:5], "unsealed_session_minutes": len(unsealed),
                      "unsealed_examples": unsealed[:5]}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbols", default=",".join(SYMBOL_ROOTS))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    lo = int(pd.Timestamp(args.start).tz_convert("UTC").value // 1000)
    hi = int(pd.Timestamp(args.end).tz_convert("UTC").value // 1000)
    dv01 = load_dv01_table()
    tape, hist_db = _ro(args.tape), _ro(args.history)
    report = {"start": args.start, "end": args.end,
              "symbols": [compare_symbol(tape, hist_db, s, lo, hi, dv01.get(s, 1.0)) for s in args.symbols.split(",")]}
    report["clean"] = all(s["trades"]["tape_only"] == 0 and s["trades"]["hist_only"] == 0 and s["bars"]["mismatches"] == 0
                          and s["bars"]["unsealed_session_minutes"] == 0 and not s["coverage_gaps_in_session"]
                          for s in report["symbols"])
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"clean": report["clean"], **{s["symbol"]: {"trades": s["trades"], "bar_mismatches": s["bars"]["mismatches"],
                                                                  "compared": s["bars"]["compared"], "unsealed": s["bars"]["unsealed_session_minutes"],
                                                                  "gaps": len(s["coverage_gaps_in_session"])} for s in report["symbols"]}}, indent=1))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
