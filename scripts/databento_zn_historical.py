#!/usr/bin/env python3
"""
Databento ZN (10-Year Treasury Note Futures) Historical Data Fetcher.

Fetches historical trade data from CME Globex via Databento API.
Works with standard Databento plans (no live license required).

Usage:
    python scripts/databento_zn_historical.py                      # Last 1 hour of trades
    python scripts/databento_zn_historical.py --hours 4            # Last 4 hours
    python scripts/databento_zn_historical.py --date 2026-01-20    # Specific date
    python scripts/databento_zn_historical.py --schema mbp-1       # Top-of-book data

Environment:
    DATABENTO_API_KEY: Your Databento API key (required)
"""

import os
import sys
import argparse
import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import databento as db
except ImportError:
    print("ERROR: databento package not installed.")
    print("Install with: pip install databento")
    sys.exit(1)

# Configuration
DATASET = "GLBX.MDP3"
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "workspace" / "live_data" / "zn_historical.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zn_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_event INTEGER NOT NULL,
            ts_recv INTEGER NOT NULL,
            ts_insert TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            size INTEGER NOT NULL,
            side TEXT,
            raw_json TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zn_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_event INTEGER NOT NULL,
            ts_insert TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bid_price REAL,
            bid_size INTEGER,
            ask_price REAL,
            ask_size INTEGER
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_ts ON zn_trades(ts_event)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_quotes_ts ON zn_quotes(ts_event)')
    conn.commit()
    
    logger.info(f"Database initialized: {db_path}")
    return conn


def fetch_zn_historical(
    api_key: str,
    start: datetime,
    end: datetime,
    schema: str = "trades",
    symbol: str = "ZN.FUT",
    db_path: Path = None,
    print_data: bool = True,
) -> dict:
    """
    Fetch historical ZN futures data from Databento.
    
    Args:
        api_key: Databento API key
        start: Start datetime (UTC)
        end: End datetime (UTC)
        schema: Data schema ('trades', 'mbp-1', 'mbp-10', 'ohlcv-1s', 'ohlcv-1m')
        symbol: Symbol to fetch
        db_path: Path to SQLite database
        print_data: Print data to console
        
    Returns:
        dict with fetch statistics
    """
    logger.info(f"Fetching ZN historical data")
    logger.info(f"  Dataset: {DATASET}")
    logger.info(f"  Schema:  {schema}")
    logger.info(f"  Symbol:  {symbol}")
    logger.info(f"  Start:   {start.isoformat()}")
    logger.info(f"  End:     {end.isoformat()}")
    
    # Initialize database
    db_path = db_path or DEFAULT_DB_PATH
    conn = init_database(db_path)
    
    # Create Historical client
    client = db.Historical(key=api_key)
    
    # Get cost estimate first
    try:
        cost = client.metadata.get_cost(
            dataset=DATASET,
            symbols=[symbol],
            stype_in="parent",
            schema=schema,
            start=start.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        logger.info(f"  Estimated cost: ${cost:.4f}")
    except Exception as e:
        logger.warning(f"Could not get cost estimate: {e}")
    
    # Fetch data
    try:
        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[symbol],
            stype_in="parent",
            schema=schema,
            start=start.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as e:
        logger.error(f"Failed to fetch data: {e}")
        conn.close()
        raise
    
    # Convert to DataFrame for easier processing
    df = data.to_df()
    
    if df.empty:
        logger.warning("No data returned for the specified time range")
        conn.close()
        return {"records": 0}
    
    logger.info(f"  Records fetched: {len(df)}")
    
    # Store to database
    cursor = conn.cursor()
    trade_count = 0
    quote_count = 0
    
    for idx, row in df.iterrows():
        ts_event = int(idx.value) if hasattr(idx, 'value') else 0
        
        if schema == "trades":
            # Handle trade data - price is already in decimal format from to_df()
            price = float(row.get('price', 0)) if 'price' in row else 0
            size = int(row.get('size', 0))
            side = str(row.get('side', '?'))
            symbol_str = str(row.get('symbol', 'ZN'))
            
            if print_data and trade_count < 50:  # Print first 50
                ts_readable = idx.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                print(f"  TRADE {ts_readable} | {symbol_str} {price:>10.5f} | Size: {size:>5} | {side}")
            
            cursor.execute('''
                INSERT INTO zn_trades (ts_event, ts_recv, ts_insert, symbol, price, size, side, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ts_event, ts_event,
                datetime.now(timezone.utc).isoformat(),
                symbol_str, price, size, side,
                json.dumps({"price": price, "size": size, "side": side})
            ))
            trade_count += 1
            
        elif schema in ("mbp-1", "mbp-10"):
            # Handle quote/book data
            bid_price = row.get('bid_px_00', 0) / 1e9 if 'bid_px_00' in row else None
            bid_size = int(row.get('bid_sz_00', 0)) if 'bid_sz_00' in row else None
            ask_price = row.get('ask_px_00', 0) / 1e9 if 'ask_px_00' in row else None
            ask_size = int(row.get('ask_sz_00', 0)) if 'ask_sz_00' in row else None
            symbol_str = str(row.get('symbol', 'ZN'))
            
            if print_data and quote_count < 50:
                ts_readable = idx.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                bid_str = f"{bid_price:.5f}x{bid_size}" if bid_price else "---"
                ask_str = f"{ask_price:.5f}x{ask_size}" if ask_price else "---"
                print(f"  QUOTE {ts_readable} | {symbol_str} | Bid: {bid_str} | Ask: {ask_str}")
            
            cursor.execute('''
                INSERT INTO zn_quotes (ts_event, ts_insert, symbol, bid_price, bid_size, ask_price, ask_size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                ts_event,
                datetime.now(timezone.utc).isoformat(),
                symbol_str, bid_price, bid_size, ask_price, ask_size
            ))
            quote_count += 1
    
    conn.commit()
    conn.close()
    
    # Summary
    stats = {
        "records": len(df),
        "trades": trade_count,
        "quotes": quote_count,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    
    logger.info("=" * 60)
    logger.info("FETCH COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Total records:  {len(df)}")
    if trade_count > 0:
        logger.info(f"  Trades stored:  {trade_count}")
    if quote_count > 0:
        logger.info(f"  Quotes stored:  {quote_count}")
    logger.info(f"  Database:       {db_path}")
    logger.info("=" * 60)
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical ZN futures data from Databento",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Fetch last 1 hour of trades
    python databento_zn_historical.py
    
    # Fetch last 4 hours
    python databento_zn_historical.py --hours 4
    
    # Fetch specific date
    python databento_zn_historical.py --date 2026-01-20
    
    # Fetch top-of-book quotes
    python databento_zn_historical.py --schema mbp-1
"""
    )
    
    parser.add_argument(
        '--schema',
        choices=['trades', 'mbp-1', 'mbp-10', 'ohlcv-1s', 'ohlcv-1m'],
        default='trades',
        help='Data schema (default: trades)'
    )
    parser.add_argument(
        '--hours',
        type=float,
        default=1.0,
        help='Hours of history to fetch (default: 1)'
    )
    parser.add_argument(
        '--date',
        type=str,
        default=None,
        help='Specific date to fetch (YYYY-MM-DD format)'
    )
    parser.add_argument(
        '--start',
        type=str,
        default=None,
        help='Start datetime (ISO format)'
    )
    parser.add_argument(
        '--end',
        type=str,
        default=None,
        help='End datetime (ISO format)'
    )
    parser.add_argument(
        '--symbol',
        type=str,
        default='ZN.FUT',
        help='Symbol to fetch (default: ZN.FUT)'
    )
    parser.add_argument(
        '--db',
        type=str,
        default=None,
        help='Path to SQLite database'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Do not print individual records'
    )
    parser.add_argument(
        '--api-key',
        type=str,
        default=None,
        help='Databento API key (or set DATABENTO_API_KEY env var)'
    )
    
    args = parser.parse_args()
    
    # Get API key
    api_key = args.api_key or os.getenv("DATABENTO_API_KEY")
    if not api_key:
        print("ERROR: Databento API key required.")
        print("Set DATABENTO_API_KEY environment variable or use --api-key flag")
        sys.exit(1)
    
    # Determine time range
    now = datetime.now(timezone.utc)
    
    if args.start and args.end:
        start = datetime.fromisoformat(args.start.replace('Z', '+00:00'))
        end = datetime.fromisoformat(args.end.replace('Z', '+00:00'))
    elif args.date:
        # Full trading day
        date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start = date.replace(hour=0, minute=0, second=0)
        end = date.replace(hour=23, minute=59, second=59)
    else:
        # Last N hours
        end = now
        start = now - timedelta(hours=args.hours)
    
    # Database path
    db_path = Path(args.db) if args.db else None
    
    # Fetch data
    try:
        fetch_zn_historical(
            api_key=api_key,
            start=start,
            end=end,
            schema=args.schema,
            symbol=args.symbol,
            db_path=db_path,
            print_data=not args.quiet,
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

