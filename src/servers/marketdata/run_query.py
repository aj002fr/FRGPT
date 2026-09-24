"""Market data query tool - PostgreSQL ohlcv_1m."""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

import re

from src.mcp.discovery import register_tool
from .schema import (
    validate_columns,
    resolve_column_aliases,
    build_column_list,
    validate_order_by,
    build_order_by_clause,
    QUERY_TEMPLATES,
    MARKET_DATA_TABLE,
    PHYSICAL_COLUMN_MAP,
    LOGICAL_COLUMNS,
)

logger = logging.getLogger(__name__)


def translate_conditions_to_physical(conditions: str) -> str:
    """
    Translate logical column names to physical column names in SQL conditions.
    
    This allows LLM-generated queries to use logical names like 'timestamp'
    which get translated to physical names like 'ts_event'.
    
    Args:
        conditions: SQL WHERE conditions string
        
    Returns:
        Conditions with physical column names
    """
    translated = conditions
    
    # Replace logical column names with physical ones
    # Use word boundaries to avoid replacing partial matches
    for logical, physical in PHYSICAL_COLUMN_MAP.items():
        if logical != physical:  # Only replace if different
            # Match column name as whole word (not part of string literals)
            # This regex matches the column name when it's:
            # - At word boundary (not inside another word)
            # - Not inside single quotes (string literals)
            pattern = rf'\b{logical}\b'
            translated = re.sub(pattern, physical, translated)
    
    return translated


# Known short symbol codes that need pattern matching
# These map to symbols like "TYU5 Comdty", "ZNZ5 Comdty" in the database
SYMBOL_PREFIXES = ['TY', 'ZN', 'ZB', 'ZF', 'ZT', 'ES', 'NQ', 'YM', 'RTY', 'GC', 'SI', 'CL', 'NG']

# Symbol aliases - map commonly used symbols to what's actually in the database
# ZN and TY are both 10-year Treasury futures but different exchanges/data sources
SYMBOL_ALIASES = {
    'ZN': 'TY',   # CME 10-year -> Bloomberg TY
    'ZB': 'US',   # CME 30-year -> Bloomberg US (if available)
    'ZF': 'FV',   # CME 5-year -> Bloomberg FV (if available)
    'ZT': 'TU',   # CME 2-year -> Bloomberg TU (if available)
}

# Cache for available symbol prefixes (populated on first query)
_available_symbol_prefixes_cache: Optional[set] = None


def get_available_symbol_prefixes() -> set:
    """
    Get the set of symbol prefixes actually available in the database.
    Results are cached for performance.
    
    Returns:
        Set of 2-character symbol prefixes (e.g., {'TY', 'ES'})
    """
    global _available_symbol_prefixes_cache
    
    if _available_symbol_prefixes_cache is not None:
        return _available_symbol_prefixes_cache
    
    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        # Get distinct first 2 characters of symbols
        cursor.execute("""
            SELECT DISTINCT LEFT(symbol, 2) as prefix 
            FROM ohlcv_1m 
            WHERE symbol NOT LIKE 'UD:%'
        """)
        prefixes = {row[0] for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        
        _available_symbol_prefixes_cache = prefixes
        logger.info(f"Available symbol prefixes in database: {sorted(prefixes)}")
        return prefixes
    except Exception as e:
        logger.warning(f"Could not fetch available symbols: {e}")
        return set()


def apply_symbol_aliases(conditions: str) -> str:
    """
    Apply symbol aliases based on what's actually in the database.
    
    If user asks for 'ZN' but database only has 'TY', automatically
    translate ZN -> TY since they're equivalent instruments.
    
    Args:
        conditions: SQL WHERE conditions string
        
    Returns:
        Conditions with aliased symbols
    """
    available = get_available_symbol_prefixes()
    translated = conditions
    
    for alias, actual in SYMBOL_ALIASES.items():
        # Only apply alias if:
        # 1. The alias symbol is NOT in the database
        # 2. The actual symbol IS in the database
        if alias not in available and actual in available:
            # Replace symbol = 'ALIAS' with symbol = 'ACTUAL'
            # Pattern: symbol = 'ZN' or symbol='ZN'
            pattern = rf"(symbol\s*=\s*['\"])({alias})(['\"])"
            replacement = rf"\g<1>{actual}\g<3>"
            new_translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)
            
            # Also handle LIKE/ILIKE patterns: symbol LIKE 'ZN%' or symbol ILIKE 'ZN%'
            pattern_like = rf"(symbol\s+(?:I)?LIKE\s*['\"])({alias})(%['\"])"
            replacement_like = rf"\g<1>{actual}\g<3>"
            new_translated = re.sub(pattern_like, replacement_like, new_translated, flags=re.IGNORECASE)
            
            if new_translated != translated:
                logger.info(f"Symbol alias applied: {alias} -> {actual} (database has {actual}, not {alias})")
                translated = new_translated
    
    return translated


def expand_symbol_patterns(conditions: str) -> str:
    """
    Expand short symbol codes to pattern matches.
    
    The database contains symbols like 'TYU5 Comdty', 'ZNZ5 Comdty' but
    LLMs often generate queries with just 'TY' or 'ZN'. This function
    converts exact matches to ILIKE patterns.
    
    Examples:
        symbol = 'TY'  ->  symbol ILIKE 'TY%'
        symbol = 'ZN'  ->  symbol ILIKE 'ZN%'
    
    Args:
        conditions: SQL WHERE conditions string
        
    Returns:
        Conditions with expanded symbol patterns
    """
    translated = conditions
    
    for prefix in SYMBOL_PREFIXES:
        # Match: symbol = 'PREFIX' (with optional whitespace)
        # Replace with: symbol ILIKE 'PREFIX%'
        pattern = rf"symbol\s*=\s*'{prefix}'"
        replacement = f"symbol ILIKE '{prefix}%'"
        translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)
    
    return translated


def fix_interval_syntax(conditions: str) -> str:
    """
    Fix invalid PostgreSQL interval syntax in SQL conditions.
    
    LLMs sometimes generate: timestamp < 'DATE' + interval '1 day'
    PostgreSQL cannot add interval to a string literal, so we calculate the date instead.
    
    Examples:
        timestamp < '2024-12-06T13:30:00+00:00' + interval '1 day'
        -> timestamp < '2024-12-07T13:30:00+00:00'
    
    Args:
        conditions: SQL WHERE conditions string
        
    Returns:
        Conditions with interval expressions resolved to concrete dates
    """
    # Pattern to match: 'TIMESTAMP' + interval '1 day' (or similar)
    pattern = r"'(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?(?:\+\d{2}:\d{2}|\.\d+\+\d{2}:\d{2})?)'\s*\+\s*interval\s*'(\d+)\s*(day|hour|minute)s?'"
    
    def replace_interval(match):
        timestamp_str = match.group(1)
        amount = int(match.group(2))
        unit = match.group(3).lower()
        
        try:
            # Parse the timestamp (handle various formats)
            if 'T' in timestamp_str:
                # ISO format with time
                if '+' in timestamp_str:
                    # Has timezone - parse as-is
                    ts = datetime.fromisoformat(timestamp_str)
                else:
                    ts = datetime.fromisoformat(timestamp_str)
            else:
                # Date only
                ts = datetime.fromisoformat(timestamp_str)
            
            # Add the interval
            if unit == 'day':
                ts = ts + timedelta(days=amount)
            elif unit == 'hour':
                ts = ts + timedelta(hours=amount)
            elif unit == 'minute':
                ts = ts + timedelta(minutes=amount)
            
            # Return the new timestamp string
            return f"'{ts.isoformat()}'"
        except Exception as e:
            logger.warning(f"Could not parse timestamp '{timestamp_str}' for interval fix: {e}")
            return match.group(0)  # Return original on error
    
    fixed = re.sub(pattern, replace_interval, conditions, flags=re.IGNORECASE)
    
    if fixed != conditions:
        logger.info(f"Fixed interval syntax: {conditions[:100]}... -> {fixed[:100]}...")
    
    return fixed


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent.parent.parent


def get_pg_connection():
    """
    Get PostgreSQL connection for OHLCV data.
    
    Returns:
        psycopg2 connection object
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "psycopg2-binary is required for marketdata run_query. "
            "Install with: pip install psycopg2-binary"
        ) from exc

    import sys
    sys.path.insert(0, str(get_project_root()))
    from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
    
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )
    return conn


@register_tool(
    name="run_query",
    description="Execute SQL query on PostgreSQL ohlcv_1m table with progressive disclosure"
)
def run_query(
    template: str = "all",
    columns: Optional[List[str]] = None,
    params: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by_column: Optional[str] = None,
    order_by_direction: str = "ASC",
) -> Dict[str, Any]:
    """
    Execute query on PostgreSQL ohlcv_1m table.
    
    Progressive disclosure: Full dataset returned, small sample logged.
    
    Args:
        template: Query template name (by_symbol, by_date, etc.)
        columns: List of columns to select (default: all allowed)
        params: Query parameters (e.g., {"symbol_pattern": "ZN%"})
        limit: Maximum rows to return (optional)
        order_by_column: Column to sort by (optional, e.g., "timestamp", "close")
        order_by_direction: Sort direction - "ASC" or "DESC" (default: "ASC")
        
    Returns:
        {
            "data": [...],  # Full dataset
            "metadata": {
                "query": "...",
                "row_count": 42,
                "sample": [...]  # First 5 rows for logging
            }
        }
        
    Raises:
        ValueError: If invalid template, columns, or order_by parameters
        psycopg2.Error: If query fails
    """
    logger.info(f"run_query called: template={template}, columns={columns}, params={params}")
    
    # Default columns (logical names)
    if columns is None:
        columns = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    
    # Validate columns
    is_valid, error = validate_columns(columns)
    if not is_valid:
        raise ValueError(error)
    
    # Validate ORDER BY if specified
    if order_by_column:
        is_valid, error = validate_order_by(order_by_column, order_by_direction)
        if not is_valid:
            raise ValueError(error)
    
    # Get query template
    if template not in QUERY_TEMPLATES:
        raise ValueError(f"Invalid template: {template}. Available: {list(QUERY_TEMPLATES.keys())}")

    template_sql = QUERY_TEMPLATES[template]

    # Build column list (logical -> physical mapping + aliasing)
    column_list = build_column_list(columns)

    sql: str
    query_params: tuple[Any, ...] = ()

    if template == "custom":
        if not params or "conditions" not in params:
            raise ValueError("Custom template requires 'conditions' parameter")
        # Translate logical column names (e.g., 'timestamp') to physical (e.g., 'ts_event')
        physical_conditions = translate_conditions_to_physical(params["conditions"])
        # Fix invalid interval syntax (e.g., 'DATE' + interval '1 day' -> calculated date)
        physical_conditions = fix_interval_syntax(physical_conditions)
        # Apply symbol aliases (e.g., 'ZN' -> 'TY' if ZN not in DB but TY is)
        physical_conditions = apply_symbol_aliases(physical_conditions)
        # Expand short symbol codes to patterns (e.g., 'TY' -> 'TY%')
        physical_conditions = expand_symbol_patterns(physical_conditions)
        # Note: Front-month filtering is now done at ETL time, so ILIKE patterns work directly
        sql = template_sql.format(columns=column_list, conditions=physical_conditions)
        query_params = tuple(params.get("values", []))
        logger.debug(f"Custom conditions translated: {params['conditions']} -> {physical_conditions}")
    elif template == "by_filters":
        sql, query_params = _build_filter_query(template_sql, params or {})
        sql = sql.format(columns=column_list)
    else:
        sql = template_sql.format(columns=column_list)

        # Extract query parameters based on template (logical keys)
        # Note: Front-month filtering is done at ETL time, so ILIKE patterns work directly
        if template == "by_symbol" and params:
            symbol_pattern = params.get("symbol_pattern", "%")
            query_params = (symbol_pattern,)
        elif template == "by_date" and params:
            query_params = (params.get("date"),)
        elif template == "by_symbol_and_date" and params:
            symbol_pattern = params.get("symbol_pattern", "%")
            date_str = params.get("date")
            query_params = (symbol_pattern, date_str)

    # Add ORDER BY if specified
    if order_by_column:
        sql += build_order_by_clause(order_by_column, order_by_direction)

    # Add LIMIT if specified
    if limit:
        sql += f" LIMIT {int(limit)}"
    
    logger.debug(f"Executing SQL: {sql} with params: {query_params}")
    
    # Execute query on PostgreSQL; fallback to mock data if driver/DB unavailable
    try:
        conn = get_pg_connection()
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            cursor = conn.cursor(cursor_factory=RealDictCursor)
            # Pass query_params only if non-empty to avoid psycopg2 formatting issues
            if query_params:
                cursor.execute(sql, query_params)
            else:
                cursor.execute(sql)
            
            # Fetch all results
            rows = cursor.fetchall()
            
            # Convert to list of dicts with JSON-serializable values
            from datetime import datetime, date
            def serialize_value(v):
                if isinstance(v, (datetime, date)):
                    return v.isoformat()
                return v
            
            data = [{k: serialize_value(v) for k, v in dict(row).items()} for row in rows]
            
            row_count = len(data)
            logger.info(f"Query returned {row_count} rows from PostgreSQL ohlcv_1m")
            
            # Sample for logging (first 5 rows)
            sample = data[:5] if row_count > 5 else data
            
            return {
                "data": data,
                "metadata": {
                    "query": sql,
                    "params": list(query_params),
                    "row_count": row_count,
                    "sample": sample,
                    "columns": column_list,
                    "source": "postgresql:ohlcv_1m"
                }
            }
            
        finally:
            conn.close()
    except ImportError as exc:
        raise ImportError(
            "PostgreSQL driver (psycopg2) is not installed. "
            "Install with: pip install psycopg2-binary"
        ) from exc
    except Exception as exc:
        error_msg = str(exc).lower()
        
        # Provide helpful error messages based on common issues
        if "connection refused" in error_msg or "could not connect" in error_msg:
            raise ConnectionError(
                f"Could not connect to PostgreSQL database. "
                f"Please ensure PostgreSQL is running on localhost:5432. "
                f"Original error: {exc}"
            ) from exc
        elif "does not exist" in error_msg and "column" in error_msg:
            raise ValueError(
                f"Invalid column name in query. "
                f"Available columns: timestamp, symbol, open, high, low, close, volume. "
                f"Original error: {exc}"
            ) from exc
        elif "does not exist" in error_msg and "relation" in error_msg:
            raise ValueError(
                f"Table 'ohlcv_1m' does not exist in the database. "
                f"Please create and populate the table first. "
                f"Original error: {exc}"
            ) from exc
        elif "syntax error" in error_msg:
            raise ValueError(
                f"SQL syntax error in query. Check your conditions parameter. "
                f"Original error: {exc}"
            ) from exc
        else:
            # Re-raise with more context
            raise RuntimeError(
                f"PostgreSQL query failed. "
                f"Query: {sql[:200]}... "
                f"Error: {exc}"
            ) from exc


def _build_filter_query(base_sql: str, params: Dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """
    Build a parameterized WHERE clause for the by_filters template.
    
    Supported params (all optional, at least one required):
      - symbol_pattern (ILIKE)
      - start_timestamp, end_timestamp (inclusive)
      - min_open, max_open, min_high, max_high, min_low, max_low,
        min_close, max_close, min_volume, max_volume
    
    Returns:
        (sql_with_where, tuple_of_params)
    """
    conditions: List[str] = []
    values: List[Any] = []

    if not params:
        raise ValueError("by_filters requires at least one filter parameter")

    def add_range(field: str, min_key: str, max_key: str) -> None:
        if min_key in params and params[min_key] is not None:
            conditions.append(f"{field} >= %s")
            values.append(params[min_key])
        if max_key in params and params[max_key] is not None:
            conditions.append(f"{field} <= %s")
            values.append(params[max_key])

    # Symbol pattern - use ILIKE directly (front-month filtering done at ETL time)
    if "symbol_pattern" in params and params["symbol_pattern"] is not None:
        symbol_pattern = params["symbol_pattern"]
        conditions.append("symbol ILIKE %s")
        values.append(symbol_pattern)

    # Time range
    ts_col = PHYSICAL_COLUMN_MAP["timestamp"]
    if "start_timestamp" in params and params["start_timestamp"] is not None:
        conditions.append(f"{ts_col} >= %s")
        values.append(params["start_timestamp"])
    if "end_timestamp" in params and params["end_timestamp"] is not None:
        conditions.append(f"{ts_col} <= %s")
        values.append(params["end_timestamp"])

    # Price/volume ranges
    add_range("open", "min_open", "max_open")
    add_range("high", "min_high", "max_high")
    add_range("low", "min_low", "max_low")
    add_range("close", "min_close", "max_close")
    add_range("volume", "min_volume", "max_volume")

    if not conditions:
        raise ValueError("by_filters requires at least one filter parameter")

    where_clause = " WHERE " + " AND ".join(conditions)
    sql = base_sql + where_clause
    return sql, tuple(values)


# -------------------- Mock fallback (for environments without Postgres) --------------------
def _mock_rows() -> List[Dict[str, Any]]:
    base = []
    # Generate 40 rows for a single symbol/date to satisfy test limits
    for i in range(40):
        symbol = "ZNZ5"
        ts = f"2025-07-21 14:{i:02d}:00"
        base.append({
            "timestamp": ts.replace(" ", "T") + "Z",
            "symbol": symbol,
            "open": 112.0 + i * 0.1,
            "high": 112.1 + i * 0.1,
            "low": 111.9 + i * 0.1,
            "close": 112.05 + i * 0.1,
            "volume": 1000 + i * 10,
        })
    return base


def _matches_pattern(symbol: str, pattern: str) -> bool:
    if pattern.endswith("%") and pattern.count("%") == 1:
        return symbol.startswith(pattern[:-1])
    return pattern.replace("%", "") in symbol


def _apply_filters(data: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    from datetime import datetime

    def parse_ts(value: str) -> datetime:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return datetime.max

    filtered = []
    for row in data:
        keep = True

        if "symbol_pattern" in params and params["symbol_pattern"] is not None:
            if not _matches_pattern(row["symbol"], params["symbol_pattern"]):
                keep = False

        ts = parse_ts(row["timestamp"])
        if "start_timestamp" in params and params["start_timestamp"] is not None:
            if ts < parse_ts(params["start_timestamp"]):
                keep = False
        if "end_timestamp" in params and params["end_timestamp"] is not None:
            if ts > parse_ts(params["end_timestamp"]):
                keep = False

        for field, min_key, max_key in [
            ("open", "min_open", "max_open"),
            ("high", "min_high", "max_high"),
            ("low", "min_low", "max_low"),
            ("close", "min_close", "max_close"),
            ("volume", "min_volume", "max_volume"),
        ]:
            if min_key in params and params[min_key] is not None and row[field] < params[min_key]:
                keep = False
            if max_key in params and params[max_key] is not None and row[field] > params[max_key]:
                keep = False

        if keep:
            filtered.append(row)

    return filtered


def _run_query_mock(
    template: str,
    columns: List[str],
    params: Dict[str, Any],
    limit: Optional[int],
    order_by_column: Optional[str],
    order_by_direction: str,
) -> Dict[str, Any]:
    data = _mock_rows()

    if template == "by_symbol":
        pattern = params.get("symbol_pattern", "%")
        data = [row for row in data if _matches_pattern(row["symbol"], pattern)]
    elif template == "by_date":
        date_val = params.get("date")
        if date_val:
            data = [row for row in data if row["timestamp"].startswith(date_val)]
    elif template == "by_symbol_and_date":
        pattern = params.get("symbol_pattern", "%")
        date_val = params.get("date")
        data = [
            row for row in data
            if _matches_pattern(row["symbol"], pattern)
            and (not date_val or row["timestamp"].startswith(date_val))
        ]
    elif template == "by_filters":
        data = _apply_filters(data, params)
    elif template == "custom":
        # For mock, just return all rows to avoid executing raw SQL
        data = data
    # "all" returns unfiltered data

    # ORDER BY (simple sort)
    if order_by_column:
        reverse = order_by_direction.upper() == "DESC"
        data = sorted(data, key=lambda r: r.get(order_by_column), reverse=reverse)

    # LIMIT
    if limit is not None:
        data = data[: int(limit)]

    # Apply column selection
    def project(row: Dict[str, Any]) -> Dict[str, Any]:
        return {col: row[col] for col in columns if col in row} if columns else row

    projected = [project(r) for r in data]
    row_count = len(projected)

    where_fragments = []
    if template in ("by_symbol", "by_symbol_and_date", "by_filters") and params.get("symbol_pattern"):
        where_fragments.append("symbol ILIKE <pattern>")
    if template in ("by_date", "by_symbol_and_date") and params.get("date"):
        where_fragments.append("ts_event::date = <date>")
    if template == "by_filters":
        where_fragments.append("... filters ...")
    where_sql = ""
    if where_fragments:
        where_sql = " WHERE " + " AND ".join(where_fragments)

    mock_query = (
        f"MOCK SELECT {', '.join(columns)} FROM {MARKET_DATA_TABLE}"
        f"{where_sql} /* fallback */"
    )

    return {
        "data": projected,
        "metadata": {
            "query": mock_query,
            "params": params,
            "row_count": row_count,
            "sample": projected[:5] if row_count > 5 else projected,
            "columns": ", ".join(columns),
            "source": "mock:ohlcv_1m"
        }
    }


@register_tool(
    name="run_multi_date_query",
    description="Fetch OHLC market data for multiple dates/timestamps. Supports time windows with offset_minutes and window_minutes."
)
def run_multi_date_query(
    symbol: str,
    dates: List[str],
    columns: Optional[List[str]] = None,
    limit_per_date: int = 10000,
    offset_minutes: int = 0,
    window_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetch OHLC market data for multiple dates/timestamps, returning grouped results.
    
    This is designed for "for each date, get timeseries" queries where you have
    a list of dates or timestamps (e.g., event dates) and want market data for each.
    
    Args:
        symbol: Symbol to query (e.g., "TY", "ES")
        dates: List of dates/timestamps. Supports:
               - Date only: "2024-12-06" (fetches full day)
               - ISO timestamp: "2024-12-06T13:00:00" (uses exact time as anchor)
        columns: Columns to select (default: timestamp, open, high, low, close)
        limit_per_date: Max rows per date (default: 10000)
        offset_minutes: Minutes after the timestamp to START fetching data (default: 0).
                        Use positive for "X minutes after", negative for "X minutes before".
                        E.g., offset_minutes=0 starts at the event time.
        window_minutes: Duration of the time window in minutes (default: None = full day).
                        E.g., window_minutes=60 fetches 1 hour of data starting from
                        (timestamp + offset_minutes).
        
    Returns:
        {
            "success": True,
            "results": [
                {"date": "2024-12-06", "start_time": "13:00:00", "end_time": "14:00:00", "data": [...], "row_count": N},
                ...
            ],
            "metadata": {
                "symbol": "TY",
                "dates_requested": 5,
                "dates_with_data": 5,
                "total_rows": 5000,
                "offset_minutes": 0,
                "window_minutes": 60
            }
        }
        
    Examples:
        # Get 1 hour of data starting at each event time
        run_multi_date_query("TY", ["2024-12-06T13:00:00"], offset_minutes=0, window_minutes=60)
        
        # Get 1 hour of data starting 30 minutes AFTER each event
        run_multi_date_query("TY", ["2024-12-06T13:00:00"], offset_minutes=30, window_minutes=60)
        
        # Get 2 hours of data starting 1 hour BEFORE each event  
        run_multi_date_query("TY", ["2024-12-06T13:00:00"], offset_minutes=-60, window_minutes=120)
    """
    logger.info(f"run_multi_date_query called: symbol={symbol}, dates={dates}, offset_minutes={offset_minutes}, window_minutes={window_minutes}")
    
    if not dates:
        return {
            "success": False,
            "error": "No dates provided",
            "results": [],
            "metadata": {}
        }
    
    # Default columns for candlestick charts
    if columns is None:
        columns = ["timestamp", "open", "high", "low", "close"]
    
    # Validate columns
    is_valid, error = validate_columns(columns)
    if not is_valid:
        return {"success": False, "error": error, "results": [], "metadata": {}}
    
    results = []
    total_rows = 0
    dates_with_data = 0
    
    for date_str in dates:
        try:
            # Parse timestamp - supports both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS" formats
            date_only = date_str[:10]
            has_time = len(date_str) > 10 and 'T' in date_str
            
            if has_time:
                # Full ISO timestamp provided - use exact time as anchor
                try:
                    anchor_dt = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    # Fallback to date-only if time parsing fails
                    anchor_dt = datetime.strptime(date_only, "%Y-%m-%d")
                    has_time = False
            else:
                # Date only - anchor at midnight
                anchor_dt = datetime.strptime(date_only, "%Y-%m-%d")
            
            # Apply offset and calculate time window
            start_dt = anchor_dt + timedelta(minutes=offset_minutes)
            
            if window_minutes is not None:
                # Specific time window requested
                end_dt = start_dt + timedelta(minutes=window_minutes)
            elif has_time:
                # Has time but no window specified - default to rest of day from start
                end_dt = datetime.strptime(date_only, "%Y-%m-%d") + timedelta(days=1)
            else:
                # Date only, no window - full day
                end_dt = anchor_dt + timedelta(days=1)
            
            # Format for SQL
            start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_ts = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            # Use ILIKE pattern (front-month filtering is done at ETL time)
            symbol_condition = f"symbol ILIKE '{symbol}%'"
            
            # Build conditions with precise time range
            conditions = f"{symbol_condition} AND timestamp >= '{start_ts}' AND timestamp < '{end_ts}'"
            
            logger.info(f"  Time window: {start_ts} to {end_ts}")
            
            # Use existing run_query with the conditions
            query_result = run_query(
                template="custom",
                columns=columns,
                params={"conditions": conditions},
                limit=limit_per_date,
                order_by_column="timestamp",
                order_by_direction="ASC"
            )
            
            data = query_result.get("data", [])
            row_count = len(data)
            
            result_entry = {
                "date": date_only,
                "data": data,
                "row_count": row_count
            }
            # Include time info if time-based query was used
            if has_time or window_minutes is not None:
                result_entry["start_time"] = start_dt.strftime("%H:%M:%S")
                result_entry["end_time"] = end_dt.strftime("%H:%M:%S")
                result_entry["anchor_time"] = anchor_dt.strftime("%H:%M:%S") if has_time else None
            
            results.append(result_entry)
            
            total_rows += row_count
            if row_count > 0:
                dates_with_data += 1
                logger.info(f"  Date {date_only}: {row_count} rows")
            else:
                logger.warning(f"  Date {date_only}: no data found")
                
        except Exception as e:
            logger.error(f"Error fetching data for date {date_str}: {e}")
            results.append({
                "date": date_str[:10] if len(date_str) >= 10 else date_str,
                "data": [],
                "row_count": 0,
                "error": str(e)
            })
    
    metadata = {
        "symbol": symbol,
        "dates_requested": len(dates),
        "dates_with_data": dates_with_data,
        "total_rows": total_rows
    }
    # Include time window params if used
    if offset_minutes != 0:
        metadata["offset_minutes"] = offset_minutes
    if window_minutes is not None:
        metadata["window_minutes"] = window_minutes
    
    return {
        "success": True,
        "results": results,
        "metadata": metadata
    }

