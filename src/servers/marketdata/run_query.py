"""Market data query tool."""

import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.mcp.discovery import register_tool
from .schema import (
    validate_columns,
    build_column_list,
    validate_order_by,
    build_order_by_clause,
    QUERY_TEMPLATES,
    MARKET_DATA_TABLE
)

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    """Get database path."""
    # Look for market_data.db in project root
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "market_data.db"
    
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    return db_path


@register_tool(
    name="run_query",
    description="Execute SQL query on market data database with progressive disclosure"
)
def run_query(
    template: str = "all_valid",
    columns: Optional[List[str]] = None,
    params: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by_column: Optional[str] = None,
    order_by_direction: str = "ASC"
) -> Dict[str, Any]:
    """
    Execute query on market data database.
    
    Progressive disclosure: Full dataset returned, small sample logged.
    
    Args:
        template: Query template name (by_symbol, by_date, etc.)
        columns: List of columns to select (default: all allowed)
        params: Query parameters (e.g., {"symbol_pattern": "%.C"})
        limit: Maximum rows to return (optional)
        order_by_column: Column to sort by (optional, e.g., "file_date", "price")
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
        sqlite3.Error: If query fails
    """
    logger.info(f"run_query called: template={template}, columns={columns}, params={params}")
    
    # Default columns
    if columns is None:
        columns = ["*"]
    
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
    
    # Build column list
    column_list = build_column_list(columns)
    
    # Format SQL
    if template == "custom":
        # Custom requires conditions parameter
        if not params or "conditions" not in params:
            raise ValueError("Custom template requires 'conditions' parameter")
        sql = template_sql.format(columns=column_list, conditions=params["conditions"])
        query_params = params.get("values", [])
    else:
        sql = template_sql.format(columns=column_list)
        
        # Extract query parameters based on template
        query_params = []
        if template == "by_symbol" and params:
            query_params = [params.get("symbol_pattern", "%")]
        elif template == "by_date" and params:
            query_params = [params.get("file_date")]
        elif template == "by_symbol_and_date" and params:
            query_params = [
                params.get("symbol_pattern", "%"),
                params.get("file_date")
            ]
    
    # Add ORDER BY if specified
    if order_by_column:
        sql += build_order_by_clause(order_by_column, order_by_direction)
    
    # Add LIMIT if specified
    if limit:
        sql += f" LIMIT {int(limit)}"
    
    logger.debug(f"Executing SQL: {sql} with params: {query_params}")
    
    # Execute query
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    
    # Performance optimizations
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")  # ~64MB
    
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    
    try:
        cursor = conn.cursor()
        cursor.execute(sql, query_params)
        
        # Fetch all results
        rows = cursor.fetchall()
        
        # Convert to dictionaries
        data = [dict(row) for row in rows]
        
        row_count = len(data)
        logger.info(f"Query returned {row_count} rows")
        
        # Sample for logging (first 5 rows)
        sample = data[:5] if row_count > 5 else data
        
        return {
            "data": data,
            "metadata": {
                "query": sql,
                "params": query_params,
                "row_count": row_count,
                "sample": sample,
                "columns": column_list
            }
        }
        
    finally:
        conn.close()


