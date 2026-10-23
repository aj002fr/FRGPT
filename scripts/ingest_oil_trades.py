#!/usr/bin/env python3
"""
Ingest oil futures tick data from the BBG Excel workbook into SQLite.

Reads the saved values from Z:/Aastha/oil_trade.xlsx Sheet1 (BDH tick
output: timestamp, type, price, size) and appends new rows to the
oil_trades table in workspace/iran_headlines.db.

The BDH formula in Sheet1!A2 returns trades for CL1 Comdty with columns
(Date, Type, Price, Size). BBG sometimes shifts columns when the formula
date range is changed, so parsing tolerates misaligned rows.

Usage:
    python scripts/ingest_oil_trades.py
    python scripts/ingest_oil_trades.py --excel "Z:/Aastha/oil_trade.xlsx"
    python scripts/ingest_oil_trades.py --db workspace/iran_headlines.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_EXCEL = Path(r"Z:\Aastha\oil_trade.xlsx")
DEFAULT_DB = PROJECT_ROOT / "workspace" / "iran_headlines.db"
SHEET_NAME = "Sheet1"
SYMBOL = "CL1"


def init_oil_table(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oil_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT 'CL1',
            trade_type TEXT,
            price REAL NOT NULL,
            size INTEGER,
            UNIQUE(timestamp, price, size)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oil_trades_timestamp ON oil_trades(timestamp)")
    conn.commit()
    return conn


def parse_oil_excel(excel_path: Path) -> list[dict]:
    import pandas as pd

    if not excel_path.exists():
        raise FileNotFoundError(f"Oil trade workbook not found: {excel_path}")

    df = pd.read_excel(
        str(excel_path),
        sheet_name=SHEET_NAME,
        header=None,
        skiprows=1,
    )

    if df.shape[1] < 2:
        raise ValueError(f"Expected at least 2 columns in {SHEET_NAME}, got {df.shape[1]}")

    records = []
    for _, row in df.iterrows():
        raw_vals = [row.iloc[i] if i < len(row) else None for i in range(min(4, df.shape[1]))]

        ts_val = raw_vals[0]
        if ts_val is None or (isinstance(ts_val, str) and "requesting" in ts_val.lower()):
            continue

        ts = None
        if isinstance(ts_val, datetime):
            ts = ts_val
        elif isinstance(ts_val, str):
            try:
                ts = datetime.fromisoformat(str(ts_val))
            except ValueError:
                pass
        else:
            try:
                ts = pd.to_datetime(ts_val)
                if pd.isna(ts):
                    ts = None
            except Exception:
                pass
        if ts is None:
            continue

        trade_type = None
        price = None
        size = None

        if len(raw_vals) >= 4:
            col1 = raw_vals[1]
            col2 = raw_vals[2]
            col3 = raw_vals[3]

            if isinstance(col1, str) and col1.upper() == "TRADE":
                trade_type = "TRADE"
                try:
                    price = float(col2)
                except (TypeError, ValueError):
                    continue
                try:
                    size = int(col3) if col3 is not None and not _is_nan(col3) else None
                except (TypeError, ValueError):
                    size = None
            else:
                try:
                    price = float(col1)
                except (TypeError, ValueError):
                    continue
                try:
                    size = int(col2) if col2 is not None and not _is_nan(col2) else None
                except (TypeError, ValueError):
                    size = None
        elif len(raw_vals) >= 2:
            try:
                price = float(raw_vals[1])
            except (TypeError, ValueError):
                continue

        if price is None or price <= 0:
            continue

        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        records.append({
            "timestamp": ts_utc.isoformat(),
            "symbol": SYMBOL,
            "trade_type": trade_type or "TRADE",
            "price": round(price, 4),
            "size": size,
        })

    return records


def _is_nan(value) -> bool:
    try:
        import math
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def ingest_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    inserted = 0
    for rec in records:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO oil_trades
                    (timestamp, symbol, trade_type, price, size)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rec["timestamp"], rec["symbol"], rec["trade_type"], rec["price"], rec["size"]),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest oil futures tick data from BBG Excel into SQLite.")
    parser.add_argument("--excel", type=Path, default=DEFAULT_EXCEL, help=f"Oil trade workbook (default: {DEFAULT_EXCEL})")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Target SQLite DB (default: {DEFAULT_DB})")
    args = parser.parse_args()

    logger.info("Parsing oil trades from %s", args.excel)
    records = parse_oil_excel(args.excel)
    logger.info("Parsed %s trade records", len(records))

    if not records:
        print("No trade records found in the workbook.")
        return

    conn = init_oil_table(args.db)
    try:
        before = conn.execute("SELECT count(*) FROM oil_trades").fetchone()[0]
        inserted = ingest_records(conn, records)
        after = conn.execute("SELECT count(*) FROM oil_trades").fetchone()[0]
        ts_range = conn.execute("SELECT min(timestamp), max(timestamp) FROM oil_trades").fetchone()
    finally:
        conn.close()

    logger.info("Before=%s, parsed=%s, new_inserted=%s, after=%s", before, len(records), after - before, after)
    logger.info("Date range: %s to %s", ts_range[0], ts_range[1])
    print(f"Ingested {after - before} new oil trades into {args.db} (total: {after})")


if __name__ == "__main__":
    main()
