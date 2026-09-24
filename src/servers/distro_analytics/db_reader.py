"""
Database reader utilities for distribution analytics.

Provides functions to query PostgreSQL ohlcv_1m table and economic_events.db
and return pandas DataFrames for analysis.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from src.servers.tradingeconomics.schema import normalize_country
from src.servers.analytics.schema import (
    BPS_FACTORS,
    get_bps_factor,
    decimal_to_bps,
    bps_to_decimal,
    ticks_to_bps,
    bps_to_ticks,
    DEFAULT_BPS_FACTOR,
)

try:
    import pandas as pd
except ImportError:
    raise ImportError(
        "pandas is required for distro_analytics. "
        "Install with: pip install -r requirements-distro.txt"
    )

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger(__name__)


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent.parent.parent


def get_pg_connection():
    """
    Get PostgreSQL connection for OHLCV data.
    
    Returns:
        psycopg2 connection object
    """
    if not PSYCOPG2_AVAILABLE:
        raise ImportError(
            "psycopg2-binary is required for PostgreSQL. "
            "Install with: pip install psycopg2-binary"
        )
    
    import sys
    sys.path.insert(0, str(get_project_root()))
    from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
    
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def get_economic_events_db_path() -> Path:
    """Get path to economic_events.db."""
    return get_project_root() / "workspace" / "economic_events.db"


def get_plots_output_dir() -> Path:
    """Get default plots output directory for Distro analytics runs.

    All Distro analytics tools save their plots under a shared
    workspace directory: workspace/distro_runs
    """
    plots_dir = get_project_root() / "workspace" / "distro_runs"
    plots_dir.mkdir(parents=True, exist_ok=True)
    return plots_dir


# BPS factors imported from src.servers.analytics.schema
# See: BPS_FACTORS, get_bps_factor, decimal_to_bps, bps_to_decimal, etc.


def get_ohlcv_from_db(
    symbol_pattern: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """
    Query PostgreSQL ohlcv_1m table and return OHLCV DataFrame.
    
    Note: Front-month filtering is done at ETL time, so ILIKE patterns
    work directly without runtime front-month resolution.
    
    Args:
        symbol_pattern: SQL ILIKE pattern for symbol (e.g., "ZN%" or "TY%")
        start_date: Start datetime filter (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD)
        end_date: End datetime filter (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD)
        limit: Maximum rows to return
        
    Returns:
        DataFrame with columns: timestamp, Open, High, Low, Close, Volume
        Indexed by timestamp, sorted ascending.
        
    Raises:
        ImportError: If psycopg2 not installed
        ValueError: If no data found
    """
    conn = get_pg_connection()
    
    # Build query for PostgreSQL ohlcv_1m table
    # Front-month filtering is done at ETL time, so ILIKE works directly
    sql = """
        SELECT 
            ts_event as timestamp,
            open as "Open",
            high as "High",
            low as "Low",
            close as "Close",
            volume as "Volume"
        FROM ohlcv_1m
        WHERE symbol ILIKE %s
    """
    params: List[Any] = [symbol_pattern]
    
    if start_date:
        sql += " AND ts_event >= %s"
        params.append(start_date)
    
    if end_date:
        sql += " AND ts_event <= %s"
        params.append(end_date)
    
    sql += " ORDER BY ts_event ASC"
    
    if limit:
        sql += f" LIMIT {int(limit)}"
    
    try:
        df = pd.read_sql_query(sql, conn, params=params)
        
        if df.empty:
            raise ValueError(f"No data found for symbol pattern: {symbol_pattern}")
        
        # Convert timestamp to datetime (utc=True handles tz-aware timestamps from PostgreSQL)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        
        # Reorder columns to match DistroDashboard expectations
        df = df[['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume']]
        
        # Remove duplicate timestamps (keep last value) - required for index operations
        df = df.drop_duplicates(subset=['timestamp'], keep='last')
        
        # Set timestamp as index for compatibility
        df.set_index('timestamp', inplace=True)
        df.index.name = 'timestamp'
        
        logger.info(f"Loaded {len(df)} rows for {symbol_pattern}")
        
        return df
        
    finally:
        conn.close()


def get_ohlcv_from_parquet(
    folder_path: str,
    symbol: str,
    interval: str,
) -> pd.DataFrame:
    """
    Load OHLCV data from parquet files.
    
    This is an alternative to database loading when parquet files
    are available.
    
    Args:
        folder_path: Path to folder containing parquet files
        symbol: Symbol to load (e.g., "ZN")
        interval: Interval (e.g., "1h", "1m", "1d")
        
    Returns:
        DataFrame with OHLCV data
    """
    import os
    
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    
    # Find matching parquet file
    for file in folder.iterdir():
        if (file.suffix == '.parquet' and 
            symbol in file.name and 
            interval in file.name):
            
            df = pd.read_parquet(file)
            logger.info(f"Loaded {len(df)} rows from {file.name}")
            
            # Normalize column names
            if 'US/Eastern Timezone' in df.columns:
                df['timestamp'] = df['US/Eastern Timezone']
            
            return df
    
    raise FileNotFoundError(
        f"No parquet file found for {symbol} {interval} in {folder_path}"
    )


def get_events_from_db(
    event_name_pattern: Optional[str] = None,
    country: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    importance: Optional[str] = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """
    Query economic_events.db and return events DataFrame.
    
    Args:
        event_name_pattern: SQL LIKE pattern for event name
        country: Country filter (e.g., "US")
        start_date: Start date filter
        end_date: End date filter
        importance: Importance filter ("low", "medium", "high")
        limit: Maximum rows
        
    Returns:
        DataFrame with event data including datetime, actual, consensus, etc.
    """
    db_path = get_economic_events_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Economic events database not found: {db_path}")
    
    conn = sqlite3.connect(str(db_path))
    
    sql = "SELECT * FROM economic_events WHERE 1=1"
    params: List[Any] = []
    
    if event_name_pattern:
        sql += " AND event_name LIKE ?"
        params.append(f"%{event_name_pattern}%")
    
    if country:
        # Normalize country code (e.g., "US" -> "united states")
        normalized = normalize_country(country)
        sql += " AND LOWER(country) LIKE LOWER(?)"
        params.append(f"%{normalized}%")
    
    if start_date:
        sql += " AND event_date >= ?"
        params.append(start_date)
    
    if end_date:
        sql += " AND event_date <= ?"
        params.append(end_date)
    
    if importance:
        sql += " AND importance = ?"
        params.append(importance.lower())
    
    sql += f" ORDER BY event_date DESC LIMIT {limit}"
    
    try:
        df = pd.read_sql_query(sql, conn, params=params)
        
        if not df.empty:
            # Convert event_date to datetime
            df['datetime'] = pd.to_datetime(df['event_date'])
            
        logger.info(f"Loaded {len(df)} events")
        return df
        
    finally:
        conn.close()


def load_event_data_from_db(
    symbol: str,
    event_name: str,
    interval: str = "1h",
    country: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Load event and OHLC data from databases.
    
    This is the DB-aware replacement for DistroDashboard's 
    event_distro_controller.load_event_data() which expects parquet files.
    
    Args:
        symbol: Symbol to load (e.g., "ZN")
        event_name: Event name to filter (e.g., "NFP", "CPI", "FOMC")
        interval: Data interval (e.g., "1h") - currently unused, uses 1m data
        country: Optional country filter for events
        
    Returns:
        dict with 'all_event_ts', 'ohcl_data', 'latest_close_price'
        - all_event_ts: DataFrame with columns: datetime, events, actual, consensus, forecast
        - ohcl_data: DataFrame with OHLCV + 'US/Eastern Timezone' column
        - latest_close_price: float
    """
    import re
    
    # 1. Load events from SQLite filtered by event name
    events_df = get_events_from_db(event_name_pattern=event_name, country=country, limit=50000)
    
    if events_df.empty:
        raise ValueError(f"No events found for event_name={event_name}" + (f", country={country}" if country else ""))
    
    # Transform to DistroDashboard format
    # Expected columns: datetime, events, actual, consensus, forecast
    all_event_ts = events_df.copy()
    
    # Map column names to expected format.
    # NOTE: get_events_from_db already creates a 'datetime' column from 'event_date',
    # so we only need to rename 'event_name' → 'events' here to avoid duplicate keys.
    column_mapping = {
        'event_name': 'events',
    }
    all_event_ts = all_event_ts.rename(columns=column_mapping)
    
    # Ensure required columns exist
    for col in ['actual', 'consensus', 'forecast']:
        if col not in all_event_ts.columns:
            all_event_ts[col] = None
    
    # Ensure we have a proper datetime column
    if 'datetime' not in all_event_ts.columns and 'event_date' in all_event_ts.columns:
        # Fallback: build datetime from event_date if needed
        all_event_ts['datetime'] = pd.to_datetime(
            all_event_ts['event_date'], errors='coerce', utc=True
        )
    else:
        # Normalize whatever is in 'datetime' to tz-aware UTC
        all_event_ts['datetime'] = pd.to_datetime(
            all_event_ts['datetime'], errors='coerce', utc=True
        )
    
    all_event_ts = all_event_ts.dropna(subset=['datetime'])
    
    # Clean event text (remove extra whitespace)
    all_event_ts['events'] = all_event_ts['events'].astype(str).str.strip()
    
    # Group to get latest values per event-datetime combo
    all_event_ts = (
        all_event_ts
        .sort_values(['datetime', 'events'])
        .groupby(['datetime', 'events'], as_index=False)
        .last()
    )
    
    # Convert timezone to US/Eastern
    all_event_ts['datetime'] = all_event_ts['datetime'].dt.tz_convert('US/Eastern')
    
    # Drop any remaining duplicates after timezone conversion
    all_event_ts = all_event_ts.drop_duplicates(subset=['datetime', 'events'], keep='last')
    
    # Reset index to ensure clean integer index (required by DistroDashboard controller)
    all_event_ts = all_event_ts.reset_index(drop=True)
    
    # 2. Load OHLCV from PostgreSQL
    ohcl_data = get_ohlcv_from_db(symbol_pattern=f"{symbol}%")
    
    if ohcl_data.empty:
        raise ValueError(f"No OHLCV data found for symbol={symbol}")
    
    # Add US/Eastern Timezone column (expected by DistroDashboard)
    ohcl_data = add_timezone(ohcl_data)
    
    # Remove any duplicate index entries (required by DistroDashboard controller)
    ohcl_data = ohcl_data[~ohcl_data.index.duplicated(keep='last')]
    
    # Get latest close price
    latest_close_price = float(ohcl_data['Close'].iloc[-1])
    
    logger.info(f"Loaded {len(all_event_ts)} events, {len(ohcl_data)} OHLCV rows for {symbol}")
    
    return {
        'all_event_ts': all_event_ts,
        'ohcl_data': ohcl_data,
        'latest_close_price': latest_close_price,
    }


def add_timezone(
    df: pd.DataFrame,
    timestamp_col: str = 'timestamp',
    source_tz: str = 'UTC',
    target_tz: str = 'US/Eastern',
) -> pd.DataFrame:
    """
    Add/convert timezone for timestamp column.
    
    Args:
        df: DataFrame with timestamp column
        timestamp_col: Name of timestamp column
        source_tz: Source timezone
        target_tz: Target timezone
        
    Returns:
        DataFrame with timezone-aware timestamps
    """
    df = df.copy()
    
    # Handle both index and column timestamps
    if timestamp_col == df.index.name or timestamp_col == 'timestamp':
        if df.index.name == timestamp_col or df.index.name == 'timestamp':
            # Timestamp is index
            if df.index.tz is None:
                df.index = df.index.tz_localize(source_tz)
            df.index = df.index.tz_convert(target_tz)
            df['US/Eastern Timezone'] = df.index
        elif timestamp_col in df.columns:
            # Timestamp is column
            ts = pd.to_datetime(df[timestamp_col])
            if ts.dt.tz is None:
                ts = ts.dt.tz_localize(source_tz)
            df[timestamp_col] = ts.dt.tz_convert(target_tz)
            df['US/Eastern Timezone'] = df[timestamp_col]
    
    return df

