"""Query history retrieval tool for Polymarket markets."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.mcp.discovery import register_tool
from .schema import HISTORY_TABLE

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    """Get database path - using separate Polymarket database."""
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "polymarket_markets.db"
    
    if not db_path.exists():
        raise FileNotFoundError(
            f"Polymarket database not found: {db_path}. "
            "Run scripts/setup_polymarket_db.py first."
        )
    
    return db_path


@register_tool(
    name="get_polymarket_history",
    description="Retrieve past Polymarket market queries from history"
)
def get_polymarket_history(
    session_id: Optional[str] = None,
    limit: int = 10,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Retrieve query history from database.
    
    Args:
        session_id: Filter by session ID (optional)
        limit: Maximum number of results (default: 10)
        start_date: Filter by start date (ISO format, optional)
        end_date: Filter by end date (ISO format, optional)
        
    Returns:
        {
            "history": [...],  # List of historical queries
            "metadata": {
                "count": 5,
                "filters": {...},
                "timestamp": "..."
            }
        }
        
    Raises:
        ValueError: If invalid parameters
        sqlite3.Error: If database query fails
    """
    logger.info(f"get_polymarket_history called: session_id={session_id}, limit={limit}")
    
    # Validate parameters
    if limit <= 0:
        raise ValueError("Limit must be positive")
    
    if limit > 100:
        raise ValueError("Limit cannot exceed 100")
    
    # Build query
    sql = f"SELECT * FROM {HISTORY_TABLE} WHERE platform='polymarket'"
    conditions = []
    params = []
    
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    
    if start_date:
        conditions.append("timestamp >= ?")
        params.append(start_date)
    
    if end_date:
        conditions.append("timestamp <= ?")
        params.append(end_date)
    
    if conditions:
        sql += " AND " + " AND ".join(conditions)
    
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    
    logger.debug(f"Executing SQL: {sql} with params: {params}")
    
    # Execute query
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        # Convert to dictionaries and parse JSON fields
        history = []
        for row in rows:
            entry = dict(row)
            
            # Parse JSON fields
            if entry.get("results"):
                try:
                    entry["results"] = json.loads(entry["results"])
                except json.JSONDecodeError:
                    entry["results"] = []
            
            if entry.get("market_ids"):
                try:
                    entry["market_ids"] = json.loads(entry["market_ids"])
                except json.JSONDecodeError:
                    entry["market_ids"] = []
            
            if entry.get("expanded_keywords"):
                try:
                    entry["expanded_keywords"] = json.loads(entry["expanded_keywords"])
                except json.JSONDecodeError:
                    entry["expanded_keywords"] = []
            
            history.append(entry)
        
        logger.info(f"Retrieved {len(history)} history entries")
        
        return {
            "history": history,
            "metadata": {
                "count": len(history),
                "filters": {
                    "session_id": session_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                    "platform": "polymarket"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
        
    finally:
        conn.close()


