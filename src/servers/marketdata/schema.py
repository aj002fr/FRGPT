"""Market data schema and column whitelists."""

# Table schema
MARKET_DATA_TABLE = "market_data"

# Column whitelist - only these columns can be queried
ALLOWED_COLUMNS = [
    "id",
    "symbol",
    "bid",
    "ask",
    "price",
    "bid_quantity",
    "offer_quantity",
    "timestamp",
    "file_date",
    "data_source",
    "is_valid",
    "created_at"
]

# Columns allowed for ORDER BY (subset of ALLOWED_COLUMNS that make sense for sorting)
SORTABLE_COLUMNS = [
    "symbol",
    "bid",
    "ask",
    "price",
    "timestamp",
    "file_date",
    "created_at"
]

# Query templates (parameterized for safety)
QUERY_TEMPLATES = {
    "by_symbol": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE symbol LIKE ? AND is_valid = 1",
    "by_date": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE file_date = ? AND is_valid = 1",
    "by_symbol_and_date": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE symbol LIKE ? AND file_date = ? AND is_valid = 1",
    "all_valid": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE is_valid = 1",
    "custom": f"SELECT {{columns}} FROM {MARKET_DATA_TABLE} WHERE {{conditions}}"
}

# Column descriptions
COLUMN_DESCRIPTIONS = {
    "id": "Unique identifier",
    "symbol": "Instrument symbol (e.g., XCME.OZN.AUG25.113.C)",
    "bid": "Bid price",
    "ask": "Ask price",
    "price": "Theoretical price",
    "bid_quantity": "Bid quantity",
    "offer_quantity": "Offer quantity",
    "timestamp": "ISO timestamp",
    "file_date": "Date from file (YYYY-MM-DD)",
    "data_source": "Source filename",
    "is_valid": "Validity flag (1=valid)",
    "created_at": "Creation timestamp"
}


def validate_columns(columns: list[str]) -> tuple[bool, str]:
    """
    Validate column names against whitelist.
    
    Args:
        columns: List of column names
        
    Returns:
        (is_valid, error_message)
    """
    for col in columns:
        if col not in ALLOWED_COLUMNS and col != "*":
            return False, f"Invalid column: {col}. Allowed: {', '.join(ALLOWED_COLUMNS)}"
    return True, ""


def build_column_list(columns: list[str]) -> str:
    """
    Build safe SQL column list.
    
    Args:
        columns: List of column names (already validated)
        
    Returns:
        Comma-separated column list
    """
    if "*" in columns:
        return "*"
    return ", ".join(columns)


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
    return f" ORDER BY {column} {direction.upper()}"


