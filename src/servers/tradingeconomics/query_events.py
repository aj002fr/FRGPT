"""
Query economic events by ID/code with optional lookback period.

Provides historical event data retrieval from the local database.
"""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.mcp.discovery import register_tool
from .schema import (
    get_db_path,
    ECONOMIC_EVENTS_TABLE,
    DEFAULT_LOOKBACK_DAYS,
    normalize_country,
)

logger = logging.getLogger(__name__)


def _get_db_connection() -> tuple:
    """Get database connection, returns (connection, db_path)."""
    db_path = get_db_path()
    
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}. Run fetch_economic_calendar first.")
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn, db_path


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert sqlite Row to dictionary."""
    return {key: row[key] for key in row.keys()}


@register_tool(
    name="query_event_history",
    description="Query all past instances of a specific economic event by ID or name. "
                "Returns historical data including actual, forecast, and previous values. "
                "Optionally filter by lookback period.",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "Event ID or ticker from Trading Economics (e.g., 'UNITEDSTANONFAM', 'USANFP')"
            },
            "event_name": {
                "type": "string",
                "description": "Event name to search for (partial match supported). "
                              "Use this if event_id is not known."
            },
            "country": {
                "type": "string",
                "description": "Country filter - accepts code (US, GB) or full name (United States)"
            },
            "lookback_timestamp": {
                "type": "string",
                "description": "Only return events on or after this date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
            },
            "lookback_days": {
                "type": "integer",
                "description": "Number of days to look back from today. Alternative to lookback_timestamp."
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of events to return. Default: 100"
            }
        },
        "required": []
    }
)
def query_event_history(
    event_id: Optional[str] = None,
    event_name: Optional[str] = None,
    country: Optional[str] = None,
    lookback_timestamp: Optional[str] = None,
    lookback_days: Optional[int] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query historical event data by event ID or name.
    
    At least one of event_id or event_name must be provided.
    
    Args:
        event_id: Event ID/ticker from Trading Economics
        event_name: Event name (partial match)
        country: Country code filter
        lookback_timestamp: Only return events on or after this date
        lookback_days: Days to look back (alternative to lookback_timestamp)
        limit: Maximum results to return
        
    Returns:
        Dictionary with event history and metadata
    """
    logger.info(f"Querying event history: id={event_id}, name={event_name}")
    
    if not event_id and not event_name:
        return {
            "success": False,
            "error": "Either event_id or event_name must be provided",
            "events": [],
            "count": 0,
        }
    
    try:
        conn, db_path = _get_db_connection()
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": str(e),
            "events": [],
            "count": 0,
        }
    
    cursor = conn.cursor()
    
    try:
        # Build query
        conditions = []
        params = []
        
        # Event ID or ticker match
        if event_id:
            conditions.append("(event_id = ? OR ticker = ? OR event_id LIKE ?)")
            params.extend([event_id, event_id, f"%{event_id}%"])
        
        # Event name partial match
        if event_name:
            conditions.append("event_name LIKE ?")
            params.append(f"%{event_name}%")
        
        # Country filter (normalize and use partial match for flexibility)
        if country:
            normalized = normalize_country(country)
            conditions.append("LOWER(country) LIKE ?")
            params.append(f"%{normalized}%")
        
        # Lookback filter
        if lookback_timestamp:
            conditions.append("event_date >= ?")
            params.append(lookback_timestamp)
        elif lookback_days:
            lookback_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            conditions.append("event_date >= ?")
            params.append(lookback_date.isoformat())
        
        # Build SQL
        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT 
                id, event_id, event_name, country, category, importance,
                event_date, actual, consensus, forecast, previous, revised,
                unit, ticker, source
            FROM {ECONOMIC_EVENTS_TABLE}
            WHERE {where_clause}
            ORDER BY event_date DESC
            LIMIT ?
        """
        params.append(limit)
        
        # Execute query
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        events = [_row_to_dict(row) for row in rows]
        
        # Calculate summary statistics
        actuals = [e["actual"] for e in events if e["actual"] is not None]
        forecasts = [e["forecast"] for e in events if e["forecast"] is not None]
        
        summary = {
            "total_instances": len(events),
            "actual_available": len(actuals),
            "forecast_available": len(forecasts),
        }
        
        if actuals:
            summary["actual_avg"] = round(sum(actuals) / len(actuals), 4)
            summary["actual_min"] = min(actuals)
            summary["actual_max"] = max(actuals)
        
        if forecasts:
            summary["forecast_avg"] = round(sum(forecasts) / len(forecasts), 4)
        
        # Calculate beat/miss rate if both actual and forecast available
        beats = 0
        misses = 0
        inline = 0
        for e in events:
            if e["actual"] is not None and e["forecast"] is not None:
                diff = e["actual"] - e["forecast"]
                if diff > 0:
                    beats += 1
                elif diff < 0:
                    misses += 1
                else:
                    inline += 1
        
        if beats + misses + inline > 0:
            summary["beats"] = beats
            summary["misses"] = misses
            summary["inline"] = inline
            summary["beat_rate"] = round(beats / (beats + misses + inline), 4)
        
        logger.info(f"Found {len(events)} event instances")
        
        return {
            "success": True,
            "events": events,
            "count": len(events),
            "summary": summary,
            "query": {
                "event_id": event_id,
                "event_name": event_name,
                "country": country,
                "lookback_timestamp": lookback_timestamp,
                "lookback_days": lookback_days,
                "limit": limit,
            },
        }
        
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return {
            "success": False,
            "error": f"Database error: {e}",
            "events": [],
            "count": 0,
        }
        
    finally:
        conn.close()


@register_tool(
    name="search_events",
    description="Search economic events by keyword, category, or importance. "
                "Useful for discovering available events before querying history.",
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Search keyword for event name"
            },
            "country": {
                "type": "string",
                "description": "Country code filter (e.g., 'US', 'GB')"
            },
            "category": {
                "type": "string",
                "description": "Category filter (e.g., 'Labour', 'Prices', 'GDP')"
            },
            "importance": {
                "type": "string",
                "description": "Importance filter ('low', 'medium', 'high')"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of distinct events to return. Default: 50"
            }
        },
        "required": []
    }
)
def search_events(
    keyword: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    importance: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Search for distinct economic events in the database.
    
    Returns unique event types (not individual instances).
    
    Args:
        keyword: Search keyword for event name
        country: Country code filter
        category: Category filter
        importance: Importance filter
        limit: Maximum results
        
    Returns:
        Dictionary with matching events and metadata
    """
    logger.info(f"Searching events: keyword={keyword}, country={country}")
    
    try:
        conn, db_path = _get_db_connection()
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": str(e),
            "events": [],
            "count": 0,
        }
    
    cursor = conn.cursor()
    
    try:
        # Build query for distinct events
        conditions = []
        params = []
        
        if keyword:
            conditions.append("event_name LIKE ?")
            params.append(f"%{keyword}%")
        
        if country:
            conditions.append("country = ?")
            params.append(country.upper())
        
        if category:
            conditions.append("category LIKE ?")
            params.append(f"%{category}%")
        
        if importance:
            conditions.append("importance = ?")
            params.append(importance.lower())
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Query for distinct events with occurrence count
        sql = f"""
            SELECT 
                event_id,
                event_name,
                country,
                category,
                importance,
                ticker,
                COUNT(*) as occurrence_count,
                MIN(event_date) as first_occurrence,
                MAX(event_date) as last_occurrence
            FROM {ECONOMIC_EVENTS_TABLE}
            WHERE {where_clause}
            GROUP BY event_id, event_name, country
            ORDER BY occurrence_count DESC, last_occurrence DESC
            LIMIT ?
        """
        params.append(limit)
        
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        events = [_row_to_dict(row) for row in rows]
        
        logger.info(f"Found {len(events)} distinct events")
        
        return {
            "success": True,
            "events": events,
            "count": len(events),
            "query": {
                "keyword": keyword,
                "country": country,
                "category": category,
                "importance": importance,
                "limit": limit,
            },
        }
        
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return {
            "success": False,
            "error": f"Database error: {e}",
            "events": [],
            "count": 0,
        }
        
    finally:
        conn.close()


@register_tool(
    name="find_correlated_events",
    description="Find events that occurred within a time window of a target event. "
                "Useful for analyzing which economic events were released close together.",
    input_schema={
        "type": "object",
        "properties": {
            "target_event_id": {
                "type": "string",
                "description": "Target event ID to find correlations for"
            },
            "target_event_name": {
                "type": "string",
                "description": "Target event name to find correlations for"
            },
            "target_event_date": {
                "type": "string",
                "description": "Target event date/time (ISO format YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
            },
            "window_hours": {
                "type": "number",
                "description": "Time window in hours (±). Default: 12 hours."
            },
            "exclude_same_event": {
                "type": "boolean",
                "description": "Exclude events with same name as target. Default: true."
            },
            "min_importance": {
                "type": "string",
                "description": "Minimum importance level (low/medium/high). Optional."
            },
            "country": {
                "type": "string",
                "description": "Filter by country. Optional."
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of correlated events to return. Default: 50."
            }
        },
        "required": ["target_event_date"]
    }
)
def find_correlated_events(
    target_event_id: Optional[str] = None,
    target_event_name: Optional[str] = None,
    target_event_date: Optional[str] = None,
    window_hours: float = 12.0,
    exclude_same_event: bool = True,
    min_importance: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Find events that occurred within a time window of a target event.
    
    Args:
        target_event_id: Target event ID (optional)
        target_event_name: Target event name (optional)
        target_event_date: Target event date/time (ISO format)
        window_hours: Time window in hours (±12 by default)
        exclude_same_event: Whether to exclude events with same name
        min_importance: Minimum importance filter
        country: Country filter
        limit: Maximum results
        
    Returns:
        Dictionary with correlated events and metadata
    """
    if not target_event_date:
        return {
            "success": False,
            "error": "target_event_date is required",
            "correlated_events": [],
            "count": 0,
        }
    
    try:
        # Parse target date
        # Handle both date and datetime formats
        if 'T' in target_event_date:
            target_dt = datetime.fromisoformat(target_event_date.replace("Z", "+00:00"))
        else:
            # Just a date - assume start of day UTC
            target_dt = datetime.fromisoformat(target_event_date + "T00:00:00+00:00")
        
        # Calculate window
        window_delta = timedelta(hours=window_hours)
        start_dt = target_dt - window_delta
        end_dt = target_dt + window_delta
        
        # Format for SQL
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        conn, cursor = _get_db_connection()
        
        # Build query
        query = f"""
            SELECT * FROM {ECONOMIC_EVENTS_TABLE}
            WHERE event_date BETWEEN ? AND ?
        """
        params = [start_str, end_str]
        
        # Exclude target event
        if exclude_same_event and target_event_name:
            query += " AND event_name != ?"
            params.append(target_event_name)
        
        if exclude_same_event and target_event_id:
            query += " AND event_id != ?"
            params.append(target_event_id)
        
        # Apply filters
        if country:
            query += " AND LOWER(country) = LOWER(?)"
            params.append(country)
        
        if min_importance:
            importance_map = {"low": 1, "medium": 2, "high": 3}
            if min_importance.lower() in importance_map:
                min_val = importance_map[min_importance.lower()]
                importance_filter = " OR ".join([
                    f"LOWER(importance) = '{level}'" 
                    for level, val in importance_map.items() 
                    if val >= min_val
                ])
                query += f" AND ({importance_filter})"
        
        query += " ORDER BY event_date ASC LIMIT ?"
        params.append(limit)
        
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to dictionaries and calculate time differences
        correlated_events = []
        for row in rows:
            event = _row_to_dict(row)
            
            # Calculate time difference from target
            event_dt = datetime.fromisoformat(event["event_date"].replace("Z", "+00:00"))
            time_diff = (event_dt - target_dt).total_seconds() / 3600  # hours
            
            event["hours_from_target"] = round(time_diff, 2)
            event["timing"] = "before" if time_diff < 0 else "after" if time_diff > 0 else "simultaneous"
            
            correlated_events.append(event)
        
        conn.close()
        
        logger.info(f"Found {len(correlated_events)} correlated events within ±{window_hours}h of {target_event_date}")
        
        return {
            "success": True,
            "target_event_date": target_event_date,
            "window_hours": window_hours,
            "correlated_events": correlated_events,
            "count": len(correlated_events),
        }
        
    except Exception as e:
        logger.error(f"Error finding correlated events: {e}")
        return {
            "success": False,
            "error": str(e),
            "correlated_events": [],
            "count": 0,
        }
