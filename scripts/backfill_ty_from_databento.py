#!/usr/bin/env python3
"""
Backfill TY 1-minute OHLCV from Databento into the local serving store.

The unified dashboard serves historical paths from PostgreSQL ohlcv_1m for
speed. This script uses Databento as the upstream source for dates missing
from that materialized store and inserts the most liquid 10Y futures contract
as the continuous TY symbol.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (  # noqa: E402
    PG_DATABASE,
    PG_HOST,
    PG_OHLCV_TABLE,
    PG_PASSWORD,
    PG_PORT,
    PG_USER,
)

ET = ZoneInfo("America/New_York")
DEFAULT_ENV_PATH = Path(r"Z:\Aarsh\Data\AccessAPI\.env")
DEFAULT_DATES = ("2026-03-06", "2026-04-03", "2026-05-08")
DEFAULT_RAW_SYMBOL = "auto"
TARGET_SYMBOL = "TY"
TY_PRICE_MIN = 80.0
TY_PRICE_MAX = 150.0
QUARTER_MONTH_CODES = ((3, "H"), (6, "M"), (9, "U"), (12, "Z"))


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    quote_chars = "\"'"
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip(quote_chars)
    return values


def _get_databento_key(env_path: Path) -> str:
    key = os.getenv("DATABENTO_API_KEY")
    if not key:
        key = _load_env_file(env_path).get("DATABENTO_API_KEY")
    key = (key or "").strip().strip("\"'")
    if not key:
        raise RuntimeError(f"DATABENTO_API_KEY not found in environment or {env_path}")
    return key


def _previous_business_day(day: date) -> date:
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def _contract_code(year: int, month_code: str) -> str:
    return f"ZN{month_code}{str(year)[-1]}"


def _front_contract_for_date(day: date) -> str:
    # For event-window studies, use the next quarterly delivery month. This
    # matches the liquid ZN contract around NFP dates (e.g. May 2026 -> ZNM6).
    for contract_month, month_code in QUARTER_MONTH_CODES:
        if day.month < contract_month:
            return _contract_code(day.year, month_code)
    return _contract_code(day.year + 1, "H")


def _event_window_utc(event_date: str) -> tuple[datetime, datetime]:
    day = datetime.strptime(event_date, "%Y-%m-%d").date()
    prev = _previous_business_day(day)
    start_et = datetime.combine(prev, time(15, 0), tzinfo=ET)
    end_et = datetime.combine(day, time(15, 0), tzinfo=ET)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def _fetch_databento_ohlcv(
    api_key: str,
    raw_symbol: str,
    start_utc: datetime,
    end_utc: datetime,
):
    try:
        import databento as db
    except ImportError as exc:
        raise RuntimeError("databento package is required for this backfill") from exc

    client = db.Historical(key=api_key)
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        schema="ohlcv-1m",
        stype_in="raw_symbol",
        symbols=[raw_symbol],
        start=start_utc.isoformat(),
        end=end_utc.isoformat(),
    )
    df = data.to_df()
    if df.empty:
        return df
    return df.reset_index()


def _records_from_df(df) -> list[tuple]:
    records = []
    for row in df.itertuples(index=False):
        ts = getattr(row, "ts_event")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_utc = ts.astimezone(timezone.utc)

        open_px = float(getattr(row, "open"))
        high_px = float(getattr(row, "high"))
        low_px = float(getattr(row, "low"))
        close_px = float(getattr(row, "close"))
        if not (TY_PRICE_MIN <= close_px <= TY_PRICE_MAX):
            continue

        records.append(
            (
                ts_utc,
                open_px,
                high_px,
                low_px,
                close_px,
                int(getattr(row, "volume", 0) or 0),
                TARGET_SYMBOL,
                ts_utc.astimezone(ET).date(),
            )
        )
    return records


def _insert_records(records: Iterable[tuple]) -> int:
    records = list(records)
    if not records:
        return 0

    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    INSERT INTO {PG_OHLCV_TABLE}
                        (ts_event, open, high, low, close, volume, symbol, trading_day)
                    VALUES %s
                    ON CONFLICT (ts_event, symbol) DO NOTHING
                    """,
                    records,
                    page_size=1000,
                )
                return cur.rowcount
    finally:
        conn.close()


def backfill_dates(
    event_dates: Iterable[str],
    env_path: Path,
    raw_symbol: str,
    dry_run: bool,
) -> int:
    api_key = _get_databento_key(env_path)
    total_inserted = 0
    for event_date in event_dates:
        event_day = datetime.strptime(event_date, "%Y-%m-%d").date()
        resolved_raw_symbol = _front_contract_for_date(event_day) if raw_symbol.lower() == "auto" else raw_symbol
        start_utc, end_utc = _event_window_utc(event_date)
        print(
            f"[backfill] {event_date} {resolved_raw_symbol} "
            f"{start_utc.isoformat()} -> {end_utc.isoformat()}"
        )
        df = _fetch_databento_ohlcv(api_key, resolved_raw_symbol, start_utc, end_utc)
        records = _records_from_df(df)
        print(f"[backfill] fetched_rows={len(df)} valid_records={len(records)}")
        if dry_run:
            continue
        inserted = _insert_records(records)
        total_inserted += inserted
        print(f"[backfill] inserted={inserted}")
    print(f"[backfill] total_inserted={total_inserted}")
    return total_inserted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing continuous TY 1m OHLCV from Databento."
    )
    parser.add_argument(
        "--dates",
        nargs="+",
        default=list(DEFAULT_DATES),
        help="Event dates to backfill (YYYY-MM-DD). Default: known 2026 NFP gaps.",
    )
    parser.add_argument(
        "--env-path",
        default=str(DEFAULT_ENV_PATH),
        help="Path to AccessAPI .env containing DATABENTO_API_KEY.",
    )
    parser.add_argument(
        "--raw-symbol",
        default=DEFAULT_RAW_SYMBOL,
        help="Raw Databento/CME contract symbol to fetch, or 'auto' for front ZN contract.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without inserting.")
    args = parser.parse_args()

    backfill_dates(
        event_dates=args.dates,
        env_path=Path(args.env_path),
        raw_symbol=args.raw_symbol,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
