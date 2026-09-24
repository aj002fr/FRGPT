"""Fill flow_history.sqlite from AccessAPI trades, 2022 through the existing V1 seam.

Isolated: does not import or restart live V1/V2/V3. Writes 1-minute bars only.
Does not cache bulk TBBO on the workspace share.
"""

from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import pyarrow.ipc as pa_ipc

from scripts.aggression_dashboard.core import (
    SYMBOL_PARENT_MAP,
    load_dv01_table,
    printed_minute_bars,
    trades_to_symbol_minute_bars,
)
from scripts.aggression_dashboard.core import _select_front_month_per_day
from scripts.aggression_dashboard.flow_history import DB_PATH, persist_completed_bars
from scripts.aggression_flow_history_dash.session import session_bounds

logger = logging.getLogger(__name__)

API_BASE = "http://100.79.30.21:8001"
JOURNAL = PROJECT_ROOT / "workspace" / "analysis" / "aggression_flow_history" / "accessapi_2022_journal.sqlite"
SOURCE = "accessapi_2022"
CME_TO_DASH = {"ZN": "TY", "ZF": "FV", "ZT": "TU", "ZB": "US"}
DEFAULT_START = date(2022, 1, 3)
SEAM_SESSION = date(2026, 6, 12)
SYMBOLS = ("FV", "TU", "TY", "US")


def weekday_sessions(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _journal(path: Path = JOURNAL) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            rows INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            completed_at TEXT NOT NULL,
            PRIMARY KEY (session_date, symbol)
        )
        """
    )
    return conn


def already_done(session: date, symbol: str, path: Path = JOURNAL) -> bool:
    conn = _journal(path)
    try:
        row = conn.execute(
            "SELECT status FROM chunks WHERE session_date = ? AND symbol = ?",
            (session.isoformat(), symbol),
        ).fetchone()
    finally:
        conn.close()
    return row is not None and row[0] == "ok"


def mark_done(session: date, symbol: str, rows: int, status: str, detail: str = "", path: Path = JOURNAL) -> None:
    conn = _journal(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO chunks
            (session_date, symbol, rows, status, detail, completed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session.isoformat(), symbol, int(rows), status, detail, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def existing_seam(db_path=None) -> datetime | None:
    """Earliest V1 cache/live bar. Ignore AccessAPI rows already written."""
    path = DB_PATH if db_path is None else db_path
    conn = sqlite3.connect(str(path))
    try:
        raw = conn.execute(
            """
            SELECT MIN(ts_utc) FROM v1_aggress_flow_1m
            WHERE source IS NULL OR source <> ?
            """,
            (SOURCE,),
        ).fetchone()[0]
    finally:
        conn.close()
    if not raw:
        return None
    stamp = pd.Timestamp(raw)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.to_pydatetime()


def fetch_trades(symbol: str, start: datetime, end: datetime, *, api_base: str, timeout: int) -> pd.DataFrame:
    query = urllib.parse.urlencode(
        {
            "source": "database",
            "schema": "trades",
            "symbols": symbol,
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "format": "arrow",
        }
    )
    url = f"{api_base.rstrip('/')}/historical/trades?{query}"
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read()
            if not raw:
                return pd.DataFrame()
            table = pa_ipc.open_stream(io.BytesIO(raw)).read_all()
            if table.num_rows == 0:
                return pd.DataFrame()
            return table.to_pandas()
        except Exception as exc:
            last_exc = exc
            logger.warning("%s %s fetch attempt %s failed: %s", symbol, start.date(), attempt, exc)
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"AccessAPI fetch failed for {symbol}: {last_exc}") from last_exc


def trades_to_v1_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"])
    work = raw.copy()
    work["ts"] = pd.to_datetime(work["ts_event"], utc=True, errors="coerce")
    work["price"] = pd.to_numeric(work["price"], errors="coerce")
    work["size"] = pd.to_numeric(work["size"], errors="coerce")
    work["side"] = work["side"].astype(str)
    work["raw_symbol"] = work["symbol"].astype(str)
    work["instrument_id"] = work["raw_symbol"]
    keep = (
        work["ts"].notna()
        & work["price"].between(50, 250, inclusive="neither")
        & (work["size"] > 0)
        & work["raw_symbol"].str[:2].isin(CME_TO_DASH)
        & (work["raw_symbol"].str[:2] == next(k for k, v in CME_TO_DASH.items() if v == symbol))
    )
    out = work.loc[keep, ["ts", "price", "size", "side", "instrument_id", "raw_symbol"]].copy()
    out["dashboard_symbol"] = symbol
    out.sort_values("ts", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def fold_session(frame: pd.DataFrame, symbol: str, dv01: float) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    filtered, _front = _select_front_month_per_day(frame)
    bars, _ = trades_to_symbol_minute_bars(filtered, symbol, dv01_unit=dv01)
    return printed_minute_bars(bars)


def backfill_session(
    session: date,
    symbol: str,
    *,
    seam: datetime | None,
    api_base: str,
    timeout: int,
    db_path=None,
    dv01: float,
) -> int:
    start, end = session_bounds(session)
    if seam is not None and start >= seam:
        mark_done(session, symbol, 0, "ok", "after_seam")
        return 0
    fetch_end = end if seam is None else min(end, seam)
    raw = fetch_trades(symbol, start, fetch_end, api_base=api_base, timeout=timeout)
    frame = trades_to_v1_frame(raw, symbol)
    bars = fold_session(frame, symbol, dv01)
    persist_kwargs: dict[str, Any] = {
        "after": start,
        "before": fetch_end,
        "source": SOURCE,
    }
    if db_path is not None:
        persist_kwargs["db_path"] = db_path
    written = persist_completed_bars({symbol: bars}, **persist_kwargs)
    mark_done(session, symbol, written, "ok", f"prints={len(frame)}")
    return written


def run(args: argparse.Namespace) -> dict[str, Any]:
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    for symbol in symbols:
        if symbol not in SYMBOL_PARENT_MAP:
            raise ValueError(f"Unsupported symbol {symbol}")
    units = load_dv01_table()
    seam = None if args.no_seam else existing_seam(args.db_path)
    sessions = weekday_sessions(start, end)
    stats = {"sessions": len(sessions), "written": 0, "skipped": 0, "errors": 0}
    logger.info("AccessAPI 2022+ backfill %s -> %s seam=%s", start, end, seam)
    for session in sessions:
        for symbol in symbols:
            if already_done(session, symbol):
                stats["skipped"] += 1
                continue
            try:
                written = backfill_session(
                    session,
                    symbol,
                    seam=seam,
                    api_base=args.api_base,
                    timeout=args.timeout,
                    db_path=args.db_path,
                    dv01=float(units.get(symbol, 1.0)),
                )
                stats["written"] += written
                logger.info("%s %s wrote %s 1m bars", session, symbol, written)
            except Exception as exc:
                stats["errors"] += 1
                mark_done(session, symbol, 0, "error", str(exc)[:300])
                logger.exception("%s %s failed", session, symbol)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild 5064 flow history from AccessAPI trades")
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=SEAM_SESSION.isoformat())
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--no-seam", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
    print(run(args))


if __name__ == "__main__":
    main()
