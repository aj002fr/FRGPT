"""Market data schema and column whitelists for PostgreSQL ohlcv_1m table."""

MARKET_DATA_TABLE = "ohlcv_1m"

# Logical column names exposed to callers (agent/tool params and outputs)
LOGICAL_COLUMNS = [
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

# Column aliases (convenience aliases that map to logical columns)
COLUMN_ALIASES = {
    "price": "close",  # Common alias: "price" maps to "close"
}

# Physical column mapping in PostgreSQL
PHYSICAL_COLUMN_MAP = {
    "timestamp": "ts_event",
    "symbol": "symbol",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}

# Column whitelist for selection (includes aliases)
ALLOWED_COLUMNS = LOGICAL_COLUMNS + list(COLUMN_ALIASES.keys()) + ["*"]

# Columns allowed for ORDER BY (use logical names; mapping applied during build)
SORTABLE_COLUMNS = LOGICAL_COLUMNS

# Query templates (PostgreSQL uses %s placeholders)
QUERY_TEMPLATES = {
    "by_symbol": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE symbol ILIKE %s",
    "by_date": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE {PHYSICAL_COLUMN_MAP['timestamp']}::date = %s",
    "by_symbol_and_date": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE symbol ILIKE %s AND {PHYSICAL_COLUMN_MAP['timestamp']}::date = %s",
    "by_filters": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE}",  # WHERE added dynamically
    "all": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE}",
    "custom": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE {{conditions}}",
}

# Column descriptions for ohlcv_1m table
COLUMN_DESCRIPTIONS = {
    "timestamp": "ISO timestamp (1-minute intervals, logical alias of ts_event)",
    "symbol": "Instrument symbol (e.g., ZN, ZB, ZF, ZT, ES, NQ)",
    "open": "Opening price for the 1-minute bar",
    "high": "Highest price during the 1-minute bar",
    "low": "Lowest price during the 1-minute bar",
    "close": "Closing price for the 1-minute bar",
    "volume": "Trading volume during the 1-minute bar",
}


def validate_columns(columns: list[str]) -> tuple[bool, str]:
    """
    Validate column names against whitelist.
    
    Args:
        columns: List of column names (may include aliases)
        
    Returns:
        (is_valid, error_message)
    """
    for col in columns:
        if col not in ALLOWED_COLUMNS:
            return False, f"Invalid column: {col}. Allowed: {', '.join(ALLOWED_COLUMNS)}"
    return True, ""


def resolve_column_aliases(columns: list[str]) -> list[str]:
    """
    Resolve column aliases to their actual column names.
    
    Args:
        columns: List of column names (may include aliases like "price")
        
    Returns:
        List of resolved column names
    """
    return [COLUMN_ALIASES.get(col, col) for col in columns]


def build_column_list(columns: list[str]) -> str:
    """
    Build safe SQL column list.
    
    Args:
        columns: List of column names (already validated and resolved)
        
    Returns:
        Comma-separated column list
    """
    if "*" in columns:
        # Expand to explicit list with proper aliasing for timestamp
        return ", ".join(
            f"{PHYSICAL_COLUMN_MAP['timestamp']} AS timestamp"
            if col == "timestamp" else col
            for col in LOGICAL_COLUMNS
        )

    # Resolve aliases first
    resolved = resolve_column_aliases(columns)
    
    mapped = []
    for col in resolved:
        if col == "timestamp":
            mapped.append(f"{PHYSICAL_COLUMN_MAP['timestamp']} AS timestamp")
        else:
            mapped.append(col)
    return ", ".join(mapped)


def validate_order_by(column: str, direction: str = "ASC") -> tuple[bool, str]:
    """
    Validate ORDER BY clause parameters.
    
    Args:
        column: Column name to sort by
        direction: Sort direction (ASC or DESC)
        
    Returns:
        (is_valid, error_message)
    """
    if column not in SORTABLE_COLUMNS:
        return False, f"Invalid ORDER BY column: {column}. Allowed: {', '.join(SORTABLE_COLUMNS)}"
    
    direction_upper = direction.upper()
    if direction_upper not in ["ASC", "DESC"]:
        return False, f"Invalid sort direction: {direction}. Must be ASC or DESC"
    
    return True, ""


def build_order_by_clause(column: str, direction: str = "ASC") -> str:
    """
    Build safe ORDER BY clause.
    
    Args:
        column: Column name (already validated)
        direction: Sort direction (already validated)
        
    Returns:
        SQL ORDER BY clause
    """
    physical_col = PHYSICAL_COLUMN_MAP.get(column, column)
    return f" ORDER BY {physical_col} {direction.upper()}"


