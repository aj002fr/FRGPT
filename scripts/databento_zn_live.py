#!/usr/bin/env python3
"""
Databento ZN (10-Year Treasury Note Futures) Live Data Streamer.

Fetches live trade and order book data from CME Globex via Databento API
and stores it in SQLite for analysis.

Usage:
    python scripts/databento_zn_live.py                    # Stream trades only
    python scripts/databento_zn_live.py --schema mbo       # Stream full order book
    python scripts/databento_zn_live.py --schema mbp-1     # Stream top-of-book
    python scripts/databento_zn_live.py --replay 1h        # Replay last 1 hour + live
    python scripts/databento_zn_live.py --duration 300     # Stream for 5 minutes
    python scripts/databento_zn_live.py --continuous       # Use continuous front contract
    python scripts/databento_zn_live.py --print-only       # Print to console only (no DB)

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
from typing import Optional, Callable
import time

try:
    import databento as db
except ImportError:
    print("ERROR: databento package not installed.")
    print("Install with: pip install databento")
    sys.exit(1)


# ============================================================================
# Configuration
# ============================================================================

# CME Globex MDP 3.0 dataset for futures
DATASET = "GLBX.MDP3"

# ZN symbol configurations
SYMBOLS = {
    "parent": "ZN.FUT",           # All ZN expirations
    "continuous": "ZN.c.0",       # Front-month continuous contract
    "specific": "ZNH5",           # Example: March 2025 contract
}

# Default database path
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "workspace" / "live_data" / "zn_live.db"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# ============================================================================
# Database Setup
# ============================================================================

def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database with tables for trades and order book data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()
    
    # Trades table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zn_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_event INTEGER NOT NULL,          -- Event timestamp (nanoseconds)
            ts_recv INTEGER NOT NULL,           -- Receive timestamp (nanoseconds)
            ts_insert TEXT NOT NULL,            -- Local insert timestamp
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            size INTEGER NOT NULL,
            side TEXT,                          -- 'A' (ask/sell) or 'B' (bid/buy)
            action TEXT,                        -- Trade action
            raw_json TEXT                       -- Full message as JSON
        )
    ''')
    
    # Order book updates table (MBO/MBP)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zn_orderbook (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_event INTEGER NOT NULL,
            ts_recv INTEGER NOT NULL,
            ts_insert TEXT NOT NULL,
            symbol TEXT NOT NULL,
            schema_type TEXT NOT NULL,          -- 'mbo' or 'mbp-1', 'mbp-10'
            bid_price REAL,
            bid_size INTEGER,
            ask_price REAL,
            ask_size INTEGER,
            depth INTEGER,                      -- Book depth level
            raw_json TEXT
        )
    ''')
    
    # Aggregated OHLCV bars (computed from trades)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zn_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_start TEXT NOT NULL,
            bar_end TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            trade_count INTEGER NOT NULL,
            UNIQUE(bar_start, symbol)
        )
    ''')
    
    # Session metadata
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stream_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            schema TEXT NOT NULL,
            symbol TEXT NOT NULL,
            symbol_type TEXT NOT NULL,
            trade_count INTEGER DEFAULT 0,
            book_update_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running'
        )
    ''')
    
    # Create indexes for faster queries
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_ts ON zn_trades(ts_event)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_symbol ON zn_trades(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ob_ts ON zn_orderbook(ts_event)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bars_start ON zn_bars(bar_start)')
    
    conn.commit()
    logger.info(f"Database initialized: {db_path}")
    return conn


# ============================================================================
# Message Handlers
# ============================================================================

class ZNDataHandler:
    """Handles incoming Databento messages for ZN futures."""
    
    def __init__(
        self,
        conn: Optional[sqlite3.Connection] = None,
        print_trades: bool = True,
        print_book: bool = False,
        on_trade: Optional[Callable] = None,
    ):
        self.conn = conn
        self.print_trades = print_trades
        self.print_book = print_book
        self.on_trade = on_trade
        
        self.trade_count = 0
        self.book_update_count = 0
        self.last_price = None
        self.session_high = None
        self.session_low = None
        self.session_volume = 0
        
    def handle_record(self, record):
        """Process incoming Databento record."""
        try:
            # Get record type name
            record_type = type(record).__name__
            
            if record_type == "TradeMsg":
                self._handle_trade(record)
            elif record_type == "MboMsg":
                self._handle_mbo(record)
            elif record_type in ("Mbp1Msg", "Mbp10Msg"):
                self._handle_mbp(record)
            elif record_type == "OhlcvMsg":
                self._handle_ohlcv(record)
            elif record_type in ("InstrumentDefMsg", "SymbolMappingMsg"):
                self._handle_definition(record)
            elif record_type == "SystemMsg":
                logger.info(f"System message: {record}")
            elif record_type == "ErrorMsg":
                logger.error(f"Error from Databento: {record}")
            else:
                logger.debug(f"Unhandled record type: {record_type}")
                
        except Exception as e:
            logger.error(f"Error handling record: {e}")
            
    def _handle_trade(self, msg):
        """Process trade message."""
        self.trade_count += 1
        
        # Extract fields - Databento uses fixed-point prices
        price = msg.price / 1e9 if hasattr(msg, 'price') else 0
        size = msg.size if hasattr(msg, 'size') else 0
        ts_event = msg.ts_event if hasattr(msg, 'ts_event') else 0
        ts_recv = msg.ts_recv if hasattr(msg, 'ts_recv') else 0
        
        # Get symbol from the record's instrument_id or use default
        symbol = getattr(msg, 'symbol', 'ZN')
        if hasattr(msg, 'instrument_id'):
            symbol = f"ZN_{msg.instrument_id}"
            
        # Side: 'A' = Ask (sell), 'B' = Bid (buy)
        side = getattr(msg, 'side', '?')
        if hasattr(side, 'value'):
            side = side.value
        side_str = str(side) if side else '?'
        
        # Update session stats
        self.last_price = price
        self.session_volume += size
        if self.session_high is None or price > self.session_high:
            self.session_high = price
        if self.session_low is None or price < self.session_low:
            self.session_low = price
        
        # Print to console
        if self.print_trades:
            ts_readable = datetime.fromtimestamp(ts_event / 1e9, tz=timezone.utc).strftime('%H:%M:%S.%f')[:-3]
            direction = "^" if side_str == 'A' else "v" if side_str == 'B' else "-"
            print(f"  TRADE {ts_readable} | ZN {price:>10.5f} | Size: {size:>5} | {direction} {side_str}")
        
        # Store to database
        if self.conn:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO zn_trades 
                    (ts_event, ts_recv, ts_insert, symbol, price, size, side, action, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ts_event,
                    ts_recv,
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    price,
                    size,
                    side_str,
                    getattr(msg, 'action', None),
                    json.dumps({"price": price, "size": size, "side": side_str})
                ))
                self.conn.commit()
            except Exception as e:
                logger.error(f"DB insert error: {e}")
        
        # Callback
        if self.on_trade:
            self.on_trade({
                "type": "trade",
                "symbol": symbol,
                "price": price,
                "size": size,
                "side": side_str,
                "ts_event": ts_event,
            })
            
    def _handle_mbo(self, msg):
        """Process Market-By-Order (full order book) message."""
        self.book_update_count += 1
        
        price = msg.price / 1e9 if hasattr(msg, 'price') else 0
        size = msg.size if hasattr(msg, 'size') else 0
        side = getattr(msg, 'side', '?')
        
        if self.print_book:
            print(f"  MBO   | ZN {price:>10.5f} | Size: {size:>5} | Side: {side}")
        
        if self.conn:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO zn_orderbook 
                    (ts_event, ts_recv, ts_insert, symbol, schema_type, 
                     bid_price, bid_size, ask_price, ask_size, depth, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    getattr(msg, 'ts_event', 0),
                    getattr(msg, 'ts_recv', 0),
                    datetime.now(timezone.utc).isoformat(),
                    'ZN',
                    'mbo',
                    price if str(side) == 'B' else None,
                    size if str(side) == 'B' else None,
                    price if str(side) == 'A' else None,
                    size if str(side) == 'A' else None,
                    getattr(msg, 'depth', 0),
                    json.dumps({"price": price, "size": size, "side": str(side)})
                ))
                self.conn.commit()
            except Exception as e:
                logger.error(f"MBO DB insert error: {e}")
                
    def _handle_mbp(self, msg):
        """Process Market-By-Price (aggregated book levels) message."""
        self.book_update_count += 1
        
        # MBP messages have bid/ask levels
        bid_price = getattr(msg, 'bid_px_00', 0) / 1e9 if hasattr(msg, 'bid_px_00') else None
        bid_size = getattr(msg, 'bid_sz_00', 0) if hasattr(msg, 'bid_sz_00') else None
        ask_price = getattr(msg, 'ask_px_00', 0) / 1e9 if hasattr(msg, 'ask_px_00') else None
        ask_size = getattr(msg, 'ask_sz_00', 0) if hasattr(msg, 'ask_sz_00') else None
        
        if self.print_book:
            bid_str = f"{bid_price:.5f}x{bid_size}" if bid_price else "---"
            ask_str = f"{ask_price:.5f}x{ask_size}" if ask_price else "---"
            print(f"  MBP   | ZN | Bid: {bid_str} | Ask: {ask_str}")
        
        if self.conn:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO zn_orderbook 
                    (ts_event, ts_recv, ts_insert, symbol, schema_type,
                     bid_price, bid_size, ask_price, ask_size, depth, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    getattr(msg, 'ts_event', 0),
                    getattr(msg, 'ts_recv', 0),
                    datetime.now(timezone.utc).isoformat(),
                    'ZN',
                    type(msg).__name__.lower(),
                    bid_price,
                    bid_size,
                    ask_price,
                    ask_size,
                    0,
                    json.dumps({"bid": [bid_price, bid_size], "ask": [ask_price, ask_size]})
                ))
                self.conn.commit()
            except Exception as e:
                logger.error(f"MBP DB insert error: {e}")
    
    def _handle_ohlcv(self, msg):
        """Process OHLCV bar message."""
        open_px = msg.open / 1e9 if hasattr(msg, 'open') else 0
        high_px = msg.high / 1e9 if hasattr(msg, 'high') else 0
        low_px = msg.low / 1e9 if hasattr(msg, 'low') else 0
        close_px = msg.close / 1e9 if hasattr(msg, 'close') else 0
        volume = msg.volume if hasattr(msg, 'volume') else 0
        
        print(f"  BAR   | ZN | O:{open_px:.5f} H:{high_px:.5f} L:{low_px:.5f} C:{close_px:.5f} V:{volume}")
        
    def _handle_definition(self, msg):
        """Process instrument definition message."""
        logger.info(f"Instrument definition received: {type(msg).__name__}")
        
    def get_stats(self) -> dict:
        """Return session statistics."""
        return {
            "trade_count": self.trade_count,
            "book_update_count": self.book_update_count,
            "last_price": self.last_price,
            "session_high": self.session_high,
            "session_low": self.session_low,
            "session_volume": self.session_volume,
        }


# ============================================================================
# Main Streaming Function
# ============================================================================

def stream_zn_live(
    api_key: str,
    schema: str = "trades",
    symbol_type: str = "parent",
    replay_duration: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    db_path: Optional[Path] = None,
    print_only: bool = False,
    print_book: bool = False,
    on_trade: Optional[Callable] = None,
) -> dict:
    """
    Stream live ZN futures data from Databento.
    
    Args:
        api_key: Databento API key
        schema: Data schema ('trades', 'mbo', 'mbp-1', 'mbp-10', 'ohlcv-1s')
        symbol_type: 'parent' (all expirations), 'continuous' (front month), or specific contract
        replay_duration: Optional replay duration ('1h', '30m', etc.) for intraday replay
        duration_seconds: Optional streaming duration limit in seconds
        db_path: Path to SQLite database (None = use default)
        print_only: If True, print to console only without database storage
        print_book: If True, print order book updates
        on_trade: Optional callback function for trade events
        
    Returns:
        dict with session statistics
    """
    # Resolve symbol
    if symbol_type in SYMBOLS:
        symbol = SYMBOLS[symbol_type]
        stype_in = "parent" if symbol_type == "parent" else "continuous" if symbol_type == "continuous" else "raw_symbol"
    else:
        # Assume it's a specific contract symbol
        symbol = symbol_type
        stype_in = "raw_symbol"
    
    logger.info(f"Starting ZN live stream")
    logger.info(f"  Dataset: {DATASET}")
    logger.info(f"  Schema:  {schema}")
    logger.info(f"  Symbol:  {symbol} (stype={stype_in})")
    
    # Initialize database
    conn = None
    if not print_only:
        db_path = db_path or DEFAULT_DB_PATH
        conn = init_database(db_path)
        
        # Log session start
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO stream_sessions (start_time, schema, symbol, symbol_type, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (datetime.now(timezone.utc).isoformat(), schema, symbol, symbol_type, 'running'))
        session_id = cursor.lastrowid
        conn.commit()
    else:
        session_id = None
    
    # Create handler
    handler = ZNDataHandler(
        conn=conn,
        print_trades=True,
        print_book=print_book,
        on_trade=on_trade,
    )
    
    # Create Databento Live client
    client = db.Live(key=api_key)
    
    # Build subscription parameters
    sub_params = {
        "dataset": DATASET,
        "schema": schema,
        "symbols": symbol,
        "stype_in": stype_in,
    }
    
    # Handle replay (intraday historical + live)
    if replay_duration:
        # Parse duration string (e.g., '1h', '30m')
        duration_map = {'h': 3600, 'm': 60, 's': 1}
        unit = replay_duration[-1].lower()
        value = int(replay_duration[:-1])
        seconds = value * duration_map.get(unit, 60)
        
        start_time = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        sub_params["start"] = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(f"  Replay from: {sub_params['start']}")
    
    # For MBO schema, request snapshot
    if schema == "mbo":
        sub_params["snapshot"] = True
        logger.info("  Snapshot: enabled (MBO)")
    
    # Subscribe
    try:
        client.subscribe(**sub_params)
        logger.info("Subscription created successfully")
    except Exception as e:
        logger.error(f"Subscription failed: {e}")
        if conn:
            conn.close()
        raise
    
    logger.info("=" * 60)
    logger.info("STREAMING LIVE - Press Ctrl+C to stop")
    logger.info("=" * 60)
    
    start_time = time.time()
    
    # Note: Don't call client.start() before iterating - iteration starts the stream
    try:
        for record in client:
            handler.handle_record(record)
            
            # Check duration limit
            if duration_seconds and (time.time() - start_time) >= duration_seconds:
                logger.info(f"Duration limit reached ({duration_seconds}s)")
                break
                
    except KeyboardInterrupt:
        logger.info("\nStream interrupted by user")
    except Exception as e:
        logger.error(f"Stream error: {e}")
    finally:
        # Cleanup
        try:
            client.terminate()
        except:
            pass
        
        # Update session record
        if conn and session_id:
            stats = handler.get_stats()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE stream_sessions 
                SET end_time = ?, trade_count = ?, book_update_count = ?, status = ?
                WHERE id = ?
            ''', (
                datetime.now(timezone.utc).isoformat(),
                stats["trade_count"],
                stats["book_update_count"],
                'completed',
                session_id
            ))
            conn.commit()
            conn.close()
    
    # Print summary
    stats = handler.get_stats()
    logger.info("=" * 60)
    logger.info("SESSION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Trades received:     {stats['trade_count']}")
    logger.info(f"  Book updates:        {stats['book_update_count']}")
    logger.info(f"  Last price:          {stats['last_price']:.5f}" if stats['last_price'] else "  Last price: N/A")
    logger.info(f"  Session high:        {stats['session_high']:.5f}" if stats['session_high'] else "  Session high: N/A")
    logger.info(f"  Session low:         {stats['session_low']:.5f}" if stats['session_low'] else "  Session low: N/A")
    logger.info(f"  Total volume:        {stats['session_volume']}")
    if not print_only:
        logger.info(f"  Data saved to:       {db_path}")
    logger.info("=" * 60)
    
    return stats


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Stream live ZN futures data from Databento",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Stream trades (default)
    python databento_zn_live.py
    
    # Stream with replay of last hour
    python databento_zn_live.py --replay 1h
    
    # Stream full order book (MBO)
    python databento_zn_live.py --schema mbo
    
    # Stream top-of-book only
    python databento_zn_live.py --schema mbp-1
    
    # Stream for 5 minutes then stop
    python databento_zn_live.py --duration 300
    
    # Use continuous front-month contract
    python databento_zn_live.py --continuous
    
    # Print only (no database)
    python databento_zn_live.py --print-only
"""
    )
    
    parser.add_argument(
        '--schema', 
        choices=['trades', 'mbo', 'mbp-1', 'mbp-10', 'ohlcv-1s', 'ohlcv-1m'],
        default='trades',
        help='Data schema to subscribe (default: trades)'
    )
    parser.add_argument(
        '--continuous',
        action='store_true',
        help='Use continuous front-month contract instead of all expirations'
    )
    parser.add_argument(
        '--symbol',
        type=str,
        default=None,
        help='Specific contract symbol (e.g., ZNH5 for March 2025)'
    )
    parser.add_argument(
        '--replay',
        type=str,
        default=None,
        help='Replay duration before live (e.g., 1h, 30m, 2h)'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=None,
        help='Stream duration limit in seconds'
    )
    parser.add_argument(
        '--db',
        type=str,
        default=None,
        help='Path to SQLite database'
    )
    parser.add_argument(
        '--print-only',
        action='store_true',
        help='Print to console only, do not save to database'
    )
    parser.add_argument(
        '--print-book',
        action='store_true',
        help='Print order book updates (verbose)'
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
    
    # Determine symbol type
    if args.symbol:
        symbol_type = args.symbol
    elif args.continuous:
        symbol_type = "continuous"
    else:
        symbol_type = "parent"
    
    # Resolve database path
    db_path = Path(args.db) if args.db else None
    
    # Run stream
    try:
        stream_zn_live(
            api_key=api_key,
            schema=args.schema,
            symbol_type=symbol_type,
            replay_duration=args.replay,
            duration_seconds=args.duration,
            db_path=db_path,
            print_only=args.print_only,
            print_book=args.print_book,
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

