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
from .event_dictionary import fuzzy_find_event

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
                "Supports flexible date filtering by year, month, date ranges, or lookback periods.",
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
                "description": "Only return events on or after this date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS). Sets lower bound for date range."
            },
            "lookback_days": {
                "type": "integer",
                "description": "Number of days to look back from today. Alternative to lookback_timestamp."
            },
            "end_timestamp": {
                "type": "string",
                "description": "Only return events on or before this date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS). Sets upper bound for date range. Use with lookback_timestamp for precise date ranges."
            },
            "year": {
                "type": "integer",
                "description": "Filter events by year (e.g., 2024). Can be combined with month_filter for specific month-year combinations."
            },
            "month_filter": {
                "type": "integer",
                "description": "Filter events by month (1-12, e.g., 12 for December). Can be combined with year for specific month-year combinations."
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
    end_timestamp: Optional[str] = None,
    year: Optional[int] = None,
    month_filter: Optional[int] = None,
    exclude_future: bool = True,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query historical event data by event ID or name.
    
    At least one of event_id or event_name must be provided.
    
    Supports flexible date filtering:
    - Use `year` for year-specific queries (e.g., "events in 2024")
    - Use `year` + `month_filter` for specific month-year (e.g., "December 2024")
    - Use `lookback_timestamp` + `end_timestamp` for precise date ranges
    - Use `lookback_days` for relative lookback from today
    
    Args:
        event_id: Event ID/ticker from Trading Economics
        event_name: Event name (partial match)
        country: Country code filter
        lookback_timestamp: Only return events on or after this date (ISO format)
        lookback_days: Days to look back from today (alternative to lookback_timestamp)
        end_timestamp: Only return events on or before this date (ISO format). Sets upper bound.
        year: Filter events by year (e.g., 2024). Can be combined with month_filter.
        month_filter: Only return events from this month (1-12, e.g., 12 for December)
        exclude_future: Exclude future events (default: True) - only return events that have already occurred
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
        normalized_event_names = []
        if event_name:
            matches = fuzzy_find_event(event_name, country=country)
            if matches:
                normalized_event_names = matches
                if len(matches) == 1:
                    logger.info(f"Event name normalized: '{event_name}' -> '{matches[0]}'")
                else:
                    logger.info(f"Event name '{event_name}' matched {len(matches)} events: {matches}")
            else:
                # No match found - use original as fallback
                normalized_event_names = [event_name]
                logger.warning(f"No fuzzy match found for event name '{event_name}', using as-is")
        
        # Build query
        conditions = []
        params = []
        
        # Event ID or ticker match
        if event_id:
            conditions.append("(event_id = ? OR ticker = ? OR event_id LIKE ?)")
            params.extend([event_id, event_id, f"%{event_id}%"])
        
        # Event name match - use all normalized names with OR
        if normalized_event_names:
            if len(normalized_event_names) == 1:
                # Single match - use LIKE for flexibility
                conditions.append("event_name LIKE ?")
                params.append(f"%{normalized_event_names[0]}%")
            else:
                # Multiple matches - use exact match with IN clause
                placeholders = ", ".join("?" * len(normalized_event_names))
                conditions.append(f"event_name IN ({placeholders})")
                params.extend(normalized_event_names)
        
        # Country filter (normalize and use partial match for flexibility)
        if country:
            normalized = normalize_country(country)
            conditions.append("LOWER(country) LIKE ?")
            params.append(f"%{normalized}%")
        
        # Date range filters (lower bound)
        if lookback_timestamp:
            conditions.append("event_date >= ?")
            params.append(lookback_timestamp)
        elif lookback_days:
            # Use local time (US/Eastern) for lookback
            from zoneinfo import ZoneInfo
            eastern_tz = ZoneInfo("America/New_York")
            lookback_date = datetime.now(eastern_tz) - timedelta(days=lookback_days)
            conditions.append("event_date >= ?")
            params.append(lookback_date.strftime("%Y-%m-%dT%H:%M:%S"))
        
        # Date range filters (upper bound)
        if end_timestamp:
            # Normalize end_timestamp - if it's just a date (YYYY-MM-DD), treat as end of day
            end_ts = end_timestamp.strip()
            if len(end_ts) == 10 and end_ts[4] == "-" and end_ts[7] == "-" and "T" not in end_ts:
                end_ts = f"{end_ts}T23:59:59"
            conditions.append("event_date <= ?")
            params.append(end_ts)
            logger.info(f"Filtering events by end_timestamp: {end_ts}")
        
        # Year filter
        if year is not None:
            # SQLite: extract year from ISO date string using strftime
            conditions.append("CAST(strftime('%Y', event_date) AS INTEGER) = ?")
            params.append(year)
            logger.info(f"Filtering events by year: {year}")
        
        # Month filter (1-12)
        if month_filter is not None:
            if 1 <= month_filter <= 12:
                # SQLite: extract month from ISO date string using strftime
                conditions.append("CAST(strftime('%m', event_date) AS INTEGER) = ?")
                params.append(month_filter)
                logger.info(f"Filtering events by month: {month_filter}")
        
        # Exclude future events (events that haven't happened yet)
        if exclude_future:
            # Use local time (US/Eastern) for date comparison
            from zoneinfo import ZoneInfo
            eastern_tz = ZoneInfo("America/New_York")
            today_str = datetime.now(eastern_tz).strftime('%Y-%m-%d')
            # Also exclude events scheduled for today (since they may not have occurred yet)
            conditions.append("date(event_date) < ?")
            params.append(today_str)
            logger.info(f"Excluding future events (event_date < {today_str})")
        
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
        
        # Add computed fields to each event
        for event in events:
            # Add 'surprise' field (actual - consensus, or actual - forecast if consensus not available)
            if event.get("actual") is not None:
                if event.get("consensus") is not None:
                    event["surprise"] = event["actual"] - event["consensus"]
                elif event.get("forecast") is not None:
                    event["surprise"] = event["actual"] - event["forecast"]
                else:
                    event["surprise"] = None
            else:
                event["surprise"] = None
            
            # Add 'timestamp' alias for 'event_date' (for compatibility)
            # event_date is already stored in local time (US/Eastern)
            event["timestamp"] = event.get("event_date")
        
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
                "normalized_event_names": normalized_event_names if normalized_event_names else None,
                "country": country,
                "lookback_timestamp": lookback_timestamp,
                "lookback_days": lookback_days,
                "end_timestamp": end_timestamp,
                "year": year,
                "month_filter": month_filter,
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
        
        # event_date is already stored in local time (US/Eastern)
        # No conversion needed
        
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
    name="get_events_by_date_range",
    description="Get all economic events within a specific date/time range. "
                "Returns high-importance events by default. "
                "Supports both date ranges (YYYY-MM-DD) and timestamp ranges (YYYY-MM-DDTHH:MM:SS).",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {
                "type": "string",
                "description": "Start date or timestamp (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
            },
            "end_date": {
                "type": "string",
                "description": "End date or timestamp (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
            },
            "country": {
                "type": "string",
                "description": "Country code (US, GB, etc.) or full name. Default: US"
            },
            "importance": {
                "type": "string",
                "description": "Filter by importance: low, medium, high. Default: high"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of events to return. Default: 100"
            }
        },
        "required": ["start_date", "end_date"]
    }
)
def get_events_by_date_range(
    start_date: str,
    end_date: str,
    country: str = "US",
    importance: str = "high",
    limit: int = 100
) -> Dict[str, Any]:
    """
    Get economic events within a specific date/time range.
    
    Supports both date-only ranges (whole day) and timestamp ranges (specific hours).
    
    Args:
        start_date: Start date or timestamp (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
        end_date: End date or timestamp (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
        country: Country code or name
        importance: Event importance filter (low, medium, high)
        limit: Max results
        
    Returns:
        Dictionary with events list and metadata
    """
    try:
        conn, db_path = _get_db_connection()
        
        # Normalize country
        country_name = normalize_country(country)
        
        def _normalize_datetime_bounds(dt_str: str, is_end: bool) -> str:
            """
            Normalize date/timestamp strings to match DB `event_date` ISO strings.
            If user passes YYYY-MM-DD, expand to full-day bounds:
              - start: YYYY-MM-DDT00:00:00
              - end:   YYYY-MM-DDT23:59:59
            """
            s = (dt_str or "").strip()
            if len(s) == 10 and s[4] == "-" and s[7] == "-" and "T" not in s:
                return f"{s}T23:59:59" if is_end else f"{s}T00:00:00"
            return s
        
        start_dt = _normalize_datetime_bounds(start_date, is_end=False)
        end_dt = _normalize_datetime_bounds(end_date, is_end=True)
        
        # Parse timestamps to check if we have time precision (post-normalization)
        has_time = 'T' in start_dt or len(start_dt) > 10
        
        logger.info(f"Fetching events for {country_name} from {start_dt} to {end_dt}, importance={importance}, time_precision={has_time}")
        
        # Build query - use <= and >= to include boundary events
        query = f"""
            SELECT * FROM {ECONOMIC_EVENTS_TABLE}
            WHERE country = ?
              AND event_date >= ?
              AND event_date <= ?
        """
        
        params = [country_name, start_dt, end_dt]
        
        # Add importance filter if specified
        if importance:
            query += " AND importance = ?"
            params.append(importance.lower())
        
        query += " ORDER BY event_date ASC LIMIT ?"
        params.append(limit)
        
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        # Convert to dicts and transform to chart-compatible format
        events_raw = [_row_to_dict(row) for row in rows]
        
        # Transform to format expected by candlestick chart:
        # Chart expects: {timestamp, name, importance}
        # DB has: {event_date, event_name, importance}
        events = []
        for evt in events_raw:
            events.append({
                "timestamp": evt.get("event_date"),      # Rename event_date → timestamp
                "name": evt.get("event_name"),           # Rename event_name → name
                "importance": evt.get("importance", "medium")
            })
        
        logger.info(f"get_events_by_date_range: Found {len(events)} events in range {start_dt} to {end_dt}")
        if events:
            logger.info(f"get_events_by_date_range: Sample event: {events[0].get('name', 'N/A')} at {events[0].get('timestamp', 'N/A')}")
        
        return {
            "success": True,
            "events": events,  # Chart-compatible format
            "count": len(events),
            "metadata": {
                "start_date": start_dt,
                "end_date": end_dt,
                "country": country_name,
                "importance": importance,
                "db_path": str(db_path)
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching events by date range: {e}")
        return {
            "success": False,
            "error": str(e),
            "events": [],
            "count": 0
        }


def get_next_event_instance(
    event_name: str,
    country: str = "US",
    include_same_time_events: bool = True,
    days_ahead: int = 14,
    reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find the next future occurrence of a specific event with full component data.
    
    Returns the next scheduled event plus any other events releasing at the same
    timestamp (for grouped releases like CPI headline + core).
    
    Args:
        event_name: Event name to search for (fuzzy matched)
        country: Country code or name (default: US)
        include_same_time_events: Also return other events at the same timestamp
        days_ahead: How many days ahead to look
        reference_date: Look ahead from this date instead of now (YYYY-MM-DD or ISO timestamp).
                        Useful for backtesting or analyzing past scenarios.
        
    Returns:
        Dictionary with:
        - success: bool
        - event: Primary event dict with full fields (actual, consensus, previous, etc.)
        - related_events: List of other events at the same timestamp
        - event_timestamp: ISO timestamp string
        - reference_date: The reference date used (for confirmation)
        - error: Error message if failed
    """
    logger.info(f"Looking for next instance of '{event_name}' for {country}" + 
                (f" from {reference_date}" if reference_date else ""))
    
    try:
        conn, db_path = _get_db_connection()
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": str(e),
            "event": None,
            "related_events": [],
            "event_timestamp": None,
        }
    
    cursor = conn.cursor()
    
    try:
        # Fuzzy match the event name
        normalized_names = fuzzy_find_event(event_name, country=country)
        if not normalized_names:
            normalized_names = [event_name]
            logger.warning(f"No fuzzy match for '{event_name}', using as-is")
        else:
            logger.info(f"Event name matched: {normalized_names}")
        
        # Normalize country
        country_name = normalize_country(country)
        
        # Parse reference date or use current time
        from zoneinfo import ZoneInfo
        eastern_tz = ZoneInfo("America/New_York")
        
        if reference_date:
            # Parse the reference date
            try:
                if 'T' in reference_date:
                    ref_dt = datetime.fromisoformat(reference_date.replace("Z", "+00:00"))
                else:
                    ref_dt = datetime.strptime(reference_date, "%Y-%m-%d")
                # Make timezone-aware if not already
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=eastern_tz)
                now_eastern = ref_dt
            except ValueError as e:
                return {
                    "success": False,
                    "error": f"Invalid reference_date format: {reference_date}. Use YYYY-MM-DD or ISO timestamp.",
                    "event": None,
                    "related_events": [],
                    "event_timestamp": None,
                }
        else:
            now_eastern = datetime.now(eastern_tz)
        
        now_str = now_eastern.strftime("%Y-%m-%dT%H:%M:%S")
        
        # Future cutoff
        future_cutoff = now_eastern + timedelta(days=days_ahead)
        future_str = future_cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        
        # Build query to find next future occurrence
        if len(normalized_names) == 1:
            name_condition = "event_name LIKE ?"
            name_params = [f"%{normalized_names[0]}%"]
        else:
            placeholders = ", ".join("?" * len(normalized_names))
            name_condition = f"event_name IN ({placeholders})"
            name_params = normalized_names
        
        query = f"""
            SELECT * FROM {ECONOMIC_EVENTS_TABLE}
            WHERE {name_condition}
              AND LOWER(country) LIKE ?
              AND event_date > ?
              AND event_date < ?
            ORDER BY event_date ASC
            LIMIT 1
        """
        params = name_params + [f"%{country_name.lower()}%", now_str, future_str]
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return {
                "success": False,
                "error": f"No upcoming '{event_name}' event found in next {days_ahead} days",
                "event": None,
                "related_events": [],
                "event_timestamp": None,
            }
        
        primary_event = _row_to_dict(row)
        event_timestamp = primary_event.get("event_date")
        
        logger.info(f"Found next event: {primary_event.get('event_name')} at {event_timestamp}")
        
        # Find related events at the same timestamp (within 1 minute)
        related_events = []
        if include_same_time_events and event_timestamp:
            # Parse timestamp and create a small window
            try:
                event_dt = datetime.fromisoformat(event_timestamp)
                start_window = (event_dt - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
                end_window = (event_dt + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
                
                query_related = f"""
                    SELECT * FROM {ECONOMIC_EVENTS_TABLE}
                    WHERE LOWER(country) LIKE ?
                      AND event_date >= ?
                      AND event_date <= ?
                      AND id != ?
                    ORDER BY event_name ASC
                """
                cursor.execute(query_related, [
                    f"%{country_name.lower()}%",
                    start_window,
                    end_window,
                    primary_event.get("id"),
                ])
                related_rows = cursor.fetchall()
                related_events = [_row_to_dict(r) for r in related_rows]
                
                if related_events:
                    logger.info(f"Found {len(related_events)} related events at same time")
                    
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not parse event timestamp for related lookup: {e}")
        
        conn.close()
        
        return {
            "success": True,
            "event": primary_event,
            "related_events": related_events,
            "event_timestamp": event_timestamp,
            "event_name_matched": normalized_names[0] if normalized_names else event_name,
            "reference_date": now_str,
        }
        
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        conn.close()
        return {
            "success": False,
            "error": f"Database error: {e}",
            "event": None,
            "related_events": [],
            "event_timestamp": None,
        }


def get_historical_event_instances(
    event_name: str,
    country: str = "US",
    limit: int = 50,
    include_same_time_events: bool = True,
    reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get historical instances of an event with full component data.
    
    For each historical occurrence, returns the event data plus any
    related events that released at the same timestamp.
    
    Args:
        event_name: Event name to search for (fuzzy matched)
        country: Country code or name
        limit: Maximum instances to return
        include_same_time_events: Also include related events at same timestamp
        reference_date: Get history up to this date instead of now (YYYY-MM-DD or ISO timestamp).
                        Useful for backtesting scenarios at a past point in time.
        
    Returns:
        Dictionary with:
        - success: bool
        - instances: List of event instances, each with:
            - event: Primary event dict
            - related_events: List of related events at same time
            - event_timestamp: ISO timestamp
        - count: Number of instances
        - reference_date: The reference date used
    """
    logger.info(f"Getting historical instances of '{event_name}' for {country}" +
                (f" up to {reference_date}" if reference_date else ""))
    
    try:
        conn, db_path = _get_db_connection()
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": str(e),
            "instances": [],
            "count": 0,
        }
    
    cursor = conn.cursor()
    
    try:
        # Fuzzy match the event name
        normalized_names = fuzzy_find_event(event_name, country=country)
        if not normalized_names:
            normalized_names = [event_name]
        
        # Normalize country
        country_name = normalize_country(country)
        
        # Parse reference date or use current time
        from zoneinfo import ZoneInfo
        eastern_tz = ZoneInfo("America/New_York")
        
        if reference_date:
            try:
                if 'T' in reference_date:
                    ref_dt = datetime.fromisoformat(reference_date.replace("Z", "+00:00"))
                else:
                    ref_dt = datetime.strptime(reference_date, "%Y-%m-%d")
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=eastern_tz)
                now_str = ref_dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                now_str = datetime.now(eastern_tz).strftime("%Y-%m-%dT%H:%M:%S")
        else:
            now_str = datetime.now(eastern_tz).strftime("%Y-%m-%dT%H:%M:%S")
        
        # Build query for historical events (past only, with actual values)
        if len(normalized_names) == 1:
            name_condition = "event_name LIKE ?"
            name_params = [f"%{normalized_names[0]}%"]
        else:
            placeholders = ", ".join("?" * len(normalized_names))
            name_condition = f"event_name IN ({placeholders})"
            name_params = normalized_names
        
        query = f"""
            SELECT * FROM {ECONOMIC_EVENTS_TABLE}
            WHERE {name_condition}
              AND LOWER(country) LIKE ?
              AND event_date < ?
              AND actual IS NOT NULL
            ORDER BY event_date DESC
            LIMIT ?
        """
        params = name_params + [f"%{country_name.lower()}%", now_str, limit]
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        instances = []
        for row in rows:
            event = _row_to_dict(row)
            event_timestamp = event.get("event_date")
            
            related_events = []
            if include_same_time_events and event_timestamp:
                try:
                    event_dt = datetime.fromisoformat(event_timestamp)
                    start_window = (event_dt - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
                    end_window = (event_dt + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
                    
                    query_related = f"""
                        SELECT * FROM {ECONOMIC_EVENTS_TABLE}
                        WHERE LOWER(country) LIKE ?
                          AND event_date >= ?
                          AND event_date <= ?
                          AND id != ?
                          AND actual IS NOT NULL
                        ORDER BY event_name ASC
                    """
                    cursor.execute(query_related, [
                        f"%{country_name.lower()}%",
                        start_window,
                        end_window,
                        event.get("id"),
                    ])
                    related_rows = cursor.fetchall()
                    related_events = [_row_to_dict(r) for r in related_rows]
                except (ValueError, TypeError):
                    pass
            
            instances.append({
                "event": event,
                "related_events": related_events,
                "event_timestamp": event_timestamp,
            })
        
        conn.close()
        
        logger.info(f"Found {len(instances)} historical instances of '{event_name}'")
        
        return {
            "success": True,
            "instances": instances,
            "count": len(instances),
            "event_name_matched": normalized_names[0] if normalized_names else event_name,
            "reference_date": now_str,
        }
        
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        conn.close()
        return {
            "success": False,
            "error": f"Database error: {e}",
            "instances": [],
            "count": 0,
        }
