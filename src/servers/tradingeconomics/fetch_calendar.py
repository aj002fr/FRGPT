"""
Fetch economic calendar from Trading Economics API.

Provides incremental updates to the economic_events database.

IMPORTANT: Trading Economics API has a hard limit of 1000 results per request.
For large date ranges, this module automatically chunks requests into 90-day periods to avoid data truncation.
"""

import json
import logging
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.mcp.discovery import register_tool
from src.servers.tradingeconomics.schema import (
    TE_API_BASE_URL,
    TE_CALENDAR_ENDPOINT,
    TE_FORMAT_JSON,
    get_db_path,
    ECONOMIC_EVENTS_TABLE,
    CREATE_ECONOMIC_EVENTS_TABLE,
    CREATE_ECONOMIC_EVENTS_INDICES,
    format_event_result,
    validate_event_data,
    build_api_url,
    normalize_country,
    MAX_CALENDAR_RESULTS,
    DEFAULT_LOOKBACK_DAYS,
)
from src.servers.tradingeconomics.filters import filter_events_list, ALL_TRACKED_COUNTRIES
from src.servers.tradingeconomics.event_dictionary import (
    fuzzy_find_event,
    validate_event_name,
    search_event_name,
    get_countries_for_event,
)

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    """Load Trading Economics API key from config."""
    from config.settings import get_api_key
    return get_api_key("TRADING_ECONOMICS_API_KEY")


def _ensure_database() -> Path:
    """Ensure database exists with correct schema."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        # Create table
        cursor.execute(CREATE_ECONOMIC_EVENTS_TABLE)
        
        # Create indices (split by semicolon and execute each)
        for index_sql in CREATE_ECONOMIC_EVENTS_INDICES.strip().split(";"):
            if index_sql.strip():
                cursor.execute(index_sql)
        
        conn.commit()
        logger.info(f"Database ensured at {db_path}")
        
    finally:
        conn.close()
    
    return db_path


def _get_last_event_date(db_path: Path) -> Optional[str]:
    """Get the most recent event date from database."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"""
            SELECT MAX(event_date) 
            FROM {ECONOMIC_EVENTS_TABLE}
        """)
        result = cursor.fetchone()
        return result[0] if result and result[0] else None
        
    finally:
        conn.close()


def _chunk_date_range(start_date: str, end_date: str, chunk_days: int = 90) -> List[tuple]:
    """
    Split a date range into smaller chunks to avoid API result truncation.
    
    Trading Economics API has a 1000-result limit. For busy countries/periods,
    we need to chunk large date ranges.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        chunk_days: Maximum days per chunk (default: 90)
        
    Returns:
        List of (start, end) date tuples
    """
    from datetime import datetime, timedelta
    
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    days_diff = (end_dt - start_dt).days
    
    if days_diff <= chunk_days:
        return [(start_date, end_date)]
    
    chunks = []
    current_start = start_dt
    while current_start < end_dt:
        chunk_end = min(current_start + timedelta(days=chunk_days), end_dt)
        chunks.append((
            current_start.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d")
        ))
        current_start = chunk_end + timedelta(days=1)
    
    return chunks


def _fetch_calendar_from_api(
    api_key: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    country: Optional[str] = None,
    event_name: Optional[str] = None,
    importance: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch calendar events from Trading Economics API.
    
    Automatically validates and corrects event names using the cached event dictionary.
    If the exact event name is not found, attempts fuzzy matching to find the closest match.

    1. event_name only (no dates) → `/calendar/indicator/{indicator}` (upcoming events)
    2. event_name + country + dates → `/calendar/country/{country}/{start}/{end}` + client filter
    3. event_name + dates (no country) → fetch all countries + client filter
    4. country + dates → `/calendar/country/{country}/{start}/{end}`
    5. country only → `/calendar/country/{country}`
    6. dates only (no country/event) → fetch all countries
    7. no filters → `/calendar` (default upcoming week)
    
    **Supported API Endpoints:**
    - `/calendar` - all upcoming events (~1 week ahead)
    - `/calendar/country/{country}` - upcoming events for a country
    - `/calendar/country/{country}/{start}/{end}` - country events with date range
    - `/calendar/indicator/{indicator}` - all upcoming events for specific indicator (NO date range)
    
    **Important:** The API does NOT support `/calendar/indicator/{indicator}/{start}/{end}`.
    For historical event data by name, we fetch by country+dates and filter client-side.
    
    Args:
        api_key: Trading Economics API key
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        country: Country code or full name (e.g., 'US', 'united states')
        event_name: Event/indicator name (e.g., 'CPI', 'Non-Farm Payrolls', 'JOLTs Job Openings'). 
                   Will be validated and auto-corrected using event dictionary.
                   - Without dates: fetches upcoming events directly
                   - With dates: fetches by country+dates and filters client-side
        importance: Importance filter (optional, values: low/medium/high)
        
    Returns:
        List of event dictionaries
    """
    # Normalize country name for API (e.g., "US" -> "united states")
    normalized_country = normalize_country(country) if country else None
    
    # Validate and correct event name using dictionary (if available)
    normalized_events = [] 
    if event_name:
        try:
            if validate_event_name(event_name, country=country):
                normalized_events = [event_name]
                logger.info(f"Event name '{event_name}' validated successfully")
            else:
                matched_names = fuzzy_find_event(event_name, country=country)
                if matched_names:
                    logger.warning(f"Event name '{event_name}' matched {len(matched_names)} events: {matched_names}")
                    logger.info(f"Will fetch data for ALL {len(matched_names)} matching events")
                    normalized_events = matched_names
                else:
                    # Dictionary might not be built or event doesn't exist
                    # Try to find similar events to provide helpful feedback
                    matches = search_event_name(event_name, country=country, limit=3)
                    if matches:
                        suggestions = [m["event_name"] for m in matches]
                        logger.warning(f"Event name '{event_name}' not found. Did you mean: {suggestions[:3]}?")
                        # Ideally this should consider user feedback on suggestions
                        normalized_events = suggestions
                    else:
                        logger.warning(f"Event name '{event_name}' not found in dictionary. Trying API with original name...")
                        normalized_events = [event_name]
        except Exception as dict_error:
            logger.warning(f"Could not validate event name via dictionary ({dict_error}). Using original name: '{event_name}'")
            normalized_events = [event_name]
    

    params = {}
    if importance:
        params["importance"] = importance
    

    if normalized_events and not (start_date and end_date):
        # Event name(s) WITHOUT date range → direct fetch (upcoming events only)
        if len(normalized_events) > 1:
            logger.info(f"Multiple events matched ({len(normalized_events)}). Will fetch upcoming for all: {normalized_events}")
            endpoint = None # Will be handled by multi-fetch logic below
        else:
            logger.info(f"Fetching upcoming events for '{normalized_events[0]}' via /calendar/indicator")
            endpoint = f"/calendar/indicator/{normalized_events[0]}"
        
    elif normalized_events and normalized_country and start_date and end_date:
        # Event(s) + specific country + dates → fetch that country and filter by event
        # NOTE: /calendar/indicator only returns UPCOMING events, not historical!
        logger.info(f"Fetching historical data from '{normalized_country}' for {len(normalized_events)} events: {normalized_events}")
        endpoint = f"/calendar/country/{normalized_country}/{start_date}/{end_date}"
        # Will filter by event names after fetch
        
    elif normalized_events and start_date and end_date:
        logger.info(f"Looking up which countries  have {len(normalized_events)} events: {normalized_events}")
        endpoint = None  # Will fetch from specific countries only
        
    elif normalized_country and start_date and end_date:
        # Country + date range: /calendar/country/{country}/{start}/{end}
        endpoint = f"/calendar/country/{normalized_country}/{start_date}/{end_date}"
        
    elif normalized_country:
        # Country only: /calendar/country/{country}
        endpoint = f"/calendar/country/{normalized_country}"
        
    elif start_date and end_date:
        # Date range without country or event → use /calendar/country/All
        endpoint = None  # Will use /calendar/country/All in multi-fetch handler
        
    else:
        # No specific filters - use default calendar (upcoming week)
        endpoint = TE_CALENDAR_ENDPOINT
    
    # Handle fetching when endpoint is None
    
    # CASE 1: Multiple events, NO date range (Upcoming only)
    if endpoint is None and normalized_events and not (start_date and end_date):
        all_events = []
        import time
        
        logger.info(f"Fetching upcoming events for {len(normalized_events)} indicators...")
        
        for i, event_name in enumerate(normalized_events):
            # /calendar/indicator/{event}
            indicator_endpoint = f"/calendar/indicator/{event_name}"
            url = build_api_url(indicator_endpoint, api_key, params)
            
            try:
                logger.info(f"Fetching indicator {i+1}/{len(normalized_events)}: '{event_name}'")
                req = urllib.request.Request(url)
                req.add_header("Accept", "application/json")
                req.add_header("User-Agent", "MarketDataPuller/1.0")
                
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    
                    if isinstance(data, list):
                        # Filter to be safe, ensuring we only get the requested event
                        # (API usually behaves, but fuzzy matching might have happened upstream)
                        filtered = [
                            e for e in data 
                            if e.get("Event", "").lower() == event_name.lower()
                        ]
                        
                        # Fallback: if exact name match fails (e.g. API returns slightly different casing/spacing),
                        # take all results since we queried a specific indicator endpoint.
                        if filtered:
                            all_events.extend(filtered)
                            logger.info(f"  Found {len(filtered)} events for '{event_name}'")
                        elif data:
                            all_events.extend(data)
                            logger.info(f"  Found {len(data)} events for '{event_name}' (exact name match failed, kept all)")
                        else:
                            logger.info(f"  No events found for '{event_name}'")
                            
                    elif isinstance(data, dict) and "Message" in data:
                        logger.warning(f"  API Message for '{event_name}': {data.get('Message')}")
            except Exception as e:
                logger.warning(f"Failed to fetch for indicator '{event_name}': {e}")
            
            # Rate limit
            if i < len(normalized_events) - 1:
                time.sleep(0.3)
        
        return all_events

    # CASE 2: Date range (complex fetch strategy)
    if endpoint is None and start_date and end_date:
        all_events = []
        import time
        
        if normalized_events:
            # 1. EVENT-FIRST: Look up which countries have these specific events
            # 2. DATE-RANGE: Fetch only from those countries for the date range
            # 3. IMMEDIATE FILTER: Filter for the specific events as we receive data
            # Note: API doesn't support /calendar/indicator/{event}/{start}/{end}
            # So this is the most efficient approach possible
            
            countries_to_fetch = set()
            events_by_country = {}  # Track which events are in which country
            
            for event_name in normalized_events:
                try:
                    event_countries = get_countries_for_event(event_name)
                    if event_countries:
                        countries_to_fetch.update(event_countries)
                        logger.info(f"'{event_name}' found in {len(event_countries)} countries: {event_countries[:5]}{'...' if len(event_countries) > 5 else ''}")
                        # Track events per country for logging
                        for country in event_countries:
                            if country not in events_by_country:
                                events_by_country[country] = []
                            events_by_country[country].append(event_name)
                    else:
                        logger.warning(f"'{event_name}' not found in dictionary, will try all tracked countries")
                        countries_to_fetch.update([normalize_country(c) for c in ALL_TRACKED_COUNTRIES if normalize_country(c)])
                except Exception as e:
                    logger.warning(f"Error looking up countries for '{event_name}': {e}")
                    # Fall back to all countries if lookup fails
                    countries_to_fetch.update([normalize_country(c) for c in ALL_TRACKED_COUNTRIES if normalize_country(c)])
            
            logger.info(f"Will fetch from {len(countries_to_fetch)} countries (instead of all {len(ALL_TRACKED_COUNTRIES)} tracked)")
            
            # Chunk date range to avoid API's 1000-result limit
            date_chunks = _chunk_date_range(start_date, end_date, chunk_days=90)
            if len(date_chunks) > 1:
                logger.info(f"Date range split into {len(date_chunks)} chunks to avoid API truncation")
            
            # Fetch from each relevant country (sorted for consistent ordering)
            for country_name in sorted(list(countries_to_fetch)):
                expected = events_by_country.get(country_name, normalized_events)
                
                # Fetch each date chunk for this country
                for chunk_idx, (chunk_start, chunk_end) in enumerate(date_chunks):
                    country_endpoint = f"/calendar/country/{country_name}/{chunk_start}/{chunk_end}"
                    country_url = build_api_url(country_endpoint, api_key, params)
                    
                    if len(date_chunks) > 1:
                        logger.info(f"Fetching {country_name} chunk {chunk_idx+1}/{len(date_chunks)} ({chunk_start} to {chunk_end})")
                    else:
                        logger.info(f"Fetching {country_name} (expecting: {expected})")
                    
                    try:
                        req = urllib.request.Request(country_url)
                        req.add_header("Accept", "application/json")
                        req.add_header("User-Agent", "MarketDataPuller/1.0")
                        
                        with urllib.request.urlopen(req, timeout=10) as response:
                            data = json.loads(response.read().decode("utf-8"))
                            if isinstance(data, list):
                                # Check for API truncation
                                if len(data) >= 1000:
                                    logger.warning(f"  ⚠ Got exactly 1000 results - API may be truncating! Consider smaller date ranges.")
                                
                                # IMMEDIATE FILTER: Only keep the events we want
                                filtered = [
                                    e for e in data 
                                    if any(e.get("Event", "").lower() == name.lower() for name in normalized_events)
                                ]
                                if filtered:
                                    logger.info(f"  [OK] {country_name}: Found {len(filtered)} matching events (filtered from {len(data)} total)")
                                    all_events.extend(filtered)
                                else:
                                    logger.info(f"  [SKIP] {country_name}: No matching events (searched {len(data)} events)")
                    except Exception as e:
                        logger.warning(f"Failed to fetch for {country_name} ({chunk_start} to {chunk_end}): {e}")
                    
                    # Rate limit protection between chunks
                    if len(date_chunks) > 1 or len(countries_to_fetch) > 1:
                        time.sleep(0.3)
            
            logger.info(f"Total: {len(all_events)} events fetched for {len(normalized_events)} indicators from {len(countries_to_fetch)} countries")
            return all_events
        else:
            # No specific events - use /calendar/country/All
            logger.info("No specific events requested. Fetching all events from all countries...")
            all_countries_endpoint = f"/calendar/country/All/{start_date}/{end_date}"
            all_countries_url = build_api_url(all_countries_endpoint, api_key, params)
            
            try:
                req = urllib.request.Request(all_countries_url)
                req.add_header("Accept", "application/json")
                req.add_header("User-Agent", "MarketDataPuller/1.0")
                
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    if isinstance(data, list):
                        logger.info(f"Fetched {len(data)} events from /calendar/country/All")
                        return data
                    else:
                        logger.warning(f"Unexpected response format: {type(data)}")
                        return []
            except Exception as e:
                logger.error(f"Failed to fetch from /calendar/country/All: {e}")
                return []
    
    # Check if this is a date-ranged endpoint that might need chunking
    # Pattern: /calendar/country/{country}/{start}/{end}
    if "/calendar/country/" in endpoint and start_date and end_date and endpoint.count("/") >= 5:
        # This endpoint has dates - chunk if needed
        date_chunks = _chunk_date_range(start_date, end_date, chunk_days=90)
        
        if len(date_chunks) > 1:
            logger.info(f"Chunking {endpoint} into {len(date_chunks)} requests to avoid API limits")
            all_data = []
            import time
            
            for chunk_idx, (chunk_start, chunk_end) in enumerate(date_chunks):
                # Replace dates in endpoint
                chunked_endpoint = endpoint.rsplit("/", 2)[0] + f"/{chunk_start}/{chunk_end}"
                chunk_url = build_api_url(chunked_endpoint, api_key, params)
                
                logger.info(f"Fetching chunk {chunk_idx+1}/{len(date_chunks)}: {chunk_start} to {chunk_end}")
                
                try:
                    req = urllib.request.Request(chunk_url)
                    req.add_header("Accept", "application/json")
                    req.add_header("User-Agent", "MarketDataPuller/1.0")
                    
                    with urllib.request.urlopen(req, timeout=30) as response:
                        data = json.loads(response.read().decode("utf-8"))
                        if isinstance(data, list):
                            if len(data) >= 1000:
                                logger.warning(f"  [!] Chunk returned 1000 results - may still be truncated!")
                            all_data.extend(data)
                except Exception as e:
                    logger.warning(f"Failed to fetch chunk {chunk_idx+1}: {e}")
                
                # Rate limit
                if chunk_idx < len(date_chunks) - 1:
                    time.sleep(0.3)
            
            logger.info(f"Total events from all chunks: {len(all_data)}")
            
            # Apply event filter if needed
            if normalized_events:
                filtered = [
                    e for e in all_data 
                    if any(e.get("Event", "").lower() == name.lower() for name in normalized_events)
                ]
                logger.info(f"Filtered to {len(filtered)} events matching: {normalized_events}")
                return filtered
            
            return all_data
    
    # Single request (no chunking needed)
    url = build_api_url(endpoint, api_key, params)
    
    logger.info(f"Fetching calendar from: {endpoint}")
    
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "MarketDataPuller/1.0")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            if isinstance(data, list):
                if len(data) >= 1000:
                    logger.warning(f"Got 1000 results - API may be truncating! Consider using smaller date ranges.")
                
                logger.info(f"Fetched {len(data)} events from API")
                
                # If we have specific events to filter for, apply filter now
                if normalized_events:
                    filtered = [
                        e for e in data 
                        if any(e.get("Event", "").lower() == name.lower() for name in normalized_events)
                    ]
                    logger.info(f"Filtered to {len(filtered)} events matching: {normalized_events}")
                    return filtered
                
                return data
            elif isinstance(data, dict) and "error" in data:
                logger.error(f"API error: {data.get('error')}")
                return []
            elif isinstance(data, dict) and "Message" in data:
                logger.error(f"API error: {data.get('Message')}")
                return []
            else:
                logger.warning(f"Unexpected response format: {type(data)}")
                return []
                
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except:
            pass
        logger.error(f"HTTP error fetching calendar: {e.code} - {e.reason}. {error_body}")
        return []
    except urllib.error.URLError as e:
        logger.error(f"URL error fetching calendar: {e.reason}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching calendar: {e}")
        return []


def _insert_or_update_events(
    db_path: Path,
    events: List[Dict[str, Any]]
) -> tuple:
    """
    Insert or update events in database.
    
    Args:
        db_path: Path to database
        events: List of formatted event dictionaries
        
    Returns:
        Tuple of (inserted_count, updated_count)
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    inserted = 0
    updated = 0
    
    try:
        for event in events:
            if not validate_event_data(event):
                logger.debug(f"Skipping invalid event: {event.get('event_name', 'Unknown')}")
                continue
            
            # Check if event exists
            cursor.execute(f"""
                SELECT id FROM {ECONOMIC_EVENTS_TABLE}
                WHERE event_id = ? AND event_date = ?
            """, (event["event_id"], event["event_date"]))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update existing event (actual/revised/forecasts may have changed)
                cursor.execute(f"""
                    UPDATE {ECONOMIC_EVENTS_TABLE}
                    SET event_name = ?,
                        country = ?,
                        category = ?,
                        importance = ?,
                        actual = ?,
                        consensus = ?,
                        forecast = ?,
                        previous = ?,
                        revised = ?,
                        unit = ?,
                        ticker = ?
                    WHERE id = ?
                """, (
                    event["event_name"],
                    event["country"],
                    event.get("category", ""),
                    event.get("importance", "medium"),
                    event.get("actual"),
                    event.get("consensus"),
                    event.get("forecast"),
                    event.get("previous"),
                    event.get("revised"),
                    event.get("unit", ""),
                    event.get("ticker", ""),
                    existing[0]
                ))
                updated += 1
            else:
                # Insert new event
                cursor.execute(f"""
                    INSERT INTO {ECONOMIC_EVENTS_TABLE}
                    (event_id, event_name, country, category, importance,
                     event_date, actual, consensus, forecast, previous, revised,
                     unit, ticker, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event["event_id"],
                    event["event_name"],
                    event["country"],
                    event.get("category", ""),
                    event.get("importance", "medium"),
                    event["event_date"],
                    event.get("actual"),
                    event.get("consensus"),
                    event.get("forecast"),
                    event.get("previous"),
                    event.get("revised"),
                    event.get("unit", ""),
                    event.get("ticker", ""),
                    event.get("source", "tradingeconomics")
                ))
                inserted += 1
        
        conn.commit()
        
    finally:
        conn.close()
    
    return (inserted, updated)


@register_tool(
    name="fetch_economic_calendar",
    description="Fetch and update economic calendar from Trading Economics API. "
                "Supports incremental updates (only fetches new events since last update) "
                "or full historical fetch. Returns count of new/updated events.",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {
                "type": "string",
                "description": "Start date (YYYY-MM-DD). If not provided, uses last event date or DEFAULT_LOOKBACK_DAYS ago."
            },
            "end_date": {
                "type": "string",
                "description": "End date (YYYY-MM-DD). If not provided, uses today."
            },
            "country": {
                "type": "string",
                "description": "Country code (US, GB, EU) or full name (united states, euro area). "
                              "Optional: filters by country."
            },
            "event_name": {
                "type": "string",
                "description": "Event/indicator name to fetch (e.g., 'Non-Farm Payrolls', 'CPI'). "
                              "If provided, only fetches this specific event."
            },
            "importance": {
                "type": "string",
                "description": "Importance filter ('low', 'medium', 'high'). If not provided, fetches all."
            },
            "full_refresh": {
                "type": "boolean",
                "description": "If true, ignores last event date and fetches full history. Default: false."
            }
        },
        "required": []
    }
)
def fetch_economic_calendar(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    country: Optional[str] = None,
    event_name: Optional[str] = None,
    importance: Optional[str] = None,
    full_refresh: bool = False
) -> Dict[str, Any]:
    """
    Fetch and update economic calendar from Trading Economics API.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        country: Country code filter
        event_name: Specific event/indicator to fetch (e.g., 'CPI', 'Non-Farm Payrolls')
        importance: Importance filter
        full_refresh: If true, fetches full history ignoring last event date
        
    Returns:
        Dictionary with fetch results and metadata
    """
    logger.info("Starting economic calendar fetch")
    
    try:
        api_key = _get_api_key()
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Failed to load API key: {e}")
        return {
            "success": False,
            "error": f"Failed to load API key: {e}",
            "events_inserted": 0,
            "events_updated": 0,
        }
    
    # Ensure database exists
    db_path = _ensure_database()
    
    if not end_date:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    if not start_date:
        if full_refresh:
            lookback_date = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
            start_date = lookback_date.strftime("%Y-%m-%d")
        else:
            last_date = _get_last_event_date(db_path)
            if last_date:
                try:
                    last_dt = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
                    start_date = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                except ValueError:
                    start_date = last_date[:10]  # Just take date part
            else:
                lookback_date = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
                start_date = lookback_date.strftime("%Y-%m-%d")
    
    logger.info(f"Fetching events from {start_date} to {end_date}")
    
    raw_events = _fetch_calendar_from_api(
        api_key=api_key,
        start_date=start_date,
        end_date=end_date,
        country=country,
        event_name=event_name,
        importance=importance
    )
    
    if not raw_events:
        logger.info("No events fetched from API")
        return {
            "success": True,
            "events_fetched": 0,
            "events_inserted": 0,
            "events_updated": 0,
            "date_range": {"start": start_date, "end": end_date},
            "country_filter": country,
            "importance_filter": importance,
        }
    
    # Format events
    formatted_events = [format_event_result(e) for e in raw_events]
    
    # Apply filters BEFORE inserting into database
    # This ensures we only store relevant events (saves space, cleaner DB)
    filtered_events = filter_events_list(formatted_events, apply_exclusions=True)
    
    logger.info(f"Filtered {len(formatted_events)} raw events down to {len(filtered_events)} relevant events")
    
    if not filtered_events:
        logger.info("No relevant events found after filtering")
        return {
            "success": True,
            "events_fetched": len(raw_events),
            "events_inserted": 0,
            "events_updated": 0,
            "date_range": {"start": start_date, "end": end_date},
            "country_filter": country,
            "filtered_out": len(formatted_events),
        }
    
    # Insert/update in database
    inserted, updated = _insert_or_update_events(db_path, filtered_events)
    
    logger.info(f"Calendar update complete: {inserted} inserted, {updated} updated")
    
    return {
        "success": True,
        "events_fetched": len(raw_events),
        "events_inserted": inserted,
        "events_updated": updated,
        "date_range": {"start": start_date, "end": end_date},
        "country_filter": country,
        "importance_filter": importance,
        "database_path": str(db_path),
    }

