"""
Fetch specific events by their event_id and event_date.

Since Trading Economics API doesn't support fetching by event_id,
this script:
1. Takes a list of (event_id, event_date) tuples
2. Groups by unique dates
3. Fetches events for each date individually
4. Filters to match only the specified event_ids
5. Inserts into database

This is much more efficient than fetching 5 years of data when you
know the exact dates you need.
"""

import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Dict, Any, Set

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logging_config import setup_logging
from src.servers.tradingeconomics.fetch_calendar import (
    _get_api_key,
    _ensure_database,
    _fetch_calendar_from_api,
    _insert_or_update_events,
)
from src.servers.tradingeconomics.schema import format_event_result

logger = logging.getLogger(__name__)


def load_event_list_from_json(json_file: Path) -> List[Tuple[str, str]]:
    """
    Load event list from JSON query result file.
    
    Extracts (event_id, event_date) tuples from the events array.
    
    Args:
        json_file: Path to JSON file containing query results
        
    Returns:
        List of (event_id, event_date) tuples
    """
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    events = []
    
    # Handle both direct events array and nested structure
    if isinstance(data, dict):
        if 'data' in data and isinstance(data['data'], list):
            for item in data['data']:
                if 'events' in item:
                    for event in item['events']:
                        event_id = event.get('event_id')
                        event_date = event.get('event_date')
                        if event_id and event_date:
                            events.append((event_id, event_date))
        elif 'events' in data:
            for event in data['events']:
                event_id = event.get('event_id')
                event_date = event.get('event_date')
                if event_id and event_date:
                    events.append((event_id, event_date))
    elif isinstance(data, list):
        for event in data:
            event_id = event.get('event_id')
            event_date = event.get('event_date')
            if event_id and event_date:
                events.append((event_id, event_date))
    
    return events


def extract_unique_dates(events: List[Tuple[str, str]]) -> List[str]:
    """
    Extract unique dates from event list.
    
    Args:
        events: List of (event_id, event_date) tuples
        
    Returns:
        Sorted list of unique dates (YYYY-MM-DD format)
    """
    dates = set()
    for event_id, event_date in events:
        # Parse ISO datetime and extract date
        dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
        date_str = dt.strftime('%Y-%m-%d')
        dates.add(date_str)
    
    return sorted(list(dates))


def fetch_events_for_date(
    api_key: str,
    country: str,
    date: str,
    target_event_ids: Set[str]
) -> List[Dict[str, Any]]:
    """
    Fetch events for a specific date and filter by event_ids.
    
    Args:
        api_key: Trading Economics API key
        country: Country name (e.g., "united states")
        date: Date in YYYY-MM-DD format
        target_event_ids: Set of event_ids to filter for
        
    Returns:
        List of formatted events matching the target event_ids
    """
    logger.info(f"Fetching events for {country} on {date}")
    
    # Fetch all events for this date
    raw_events = _fetch_calendar_from_api(
        api_key=api_key,
        start_date=date,
        end_date=date,
        country=country
    )
    
    # Filter for target event_ids and format
    matched_events = []
    for raw_event in raw_events:
        event_id = str(raw_event.get('CalendarId', ''))
        if event_id in target_event_ids:
            formatted = format_event_result(raw_event)
            matched_events.append(formatted)
    
    if matched_events:
        logger.info(f"  Found {len(matched_events)} matching events")
    else:
        logger.debug(f"  No matching events found")
    
    return matched_events


def fetch_specific_events(
    event_list: List[Tuple[str, str]],
    country: str = "United States",
    delay_between_calls: float = 0.5
) -> Dict[str, Any]:
    """
    Fetch specific events by their event_id and date.
    
    Args:
        event_list: List of (event_id, event_date) tuples to fetch
        country: Country name (default: "United States")
        delay_between_calls: Delay in seconds between API calls
        
    Returns:
        Dictionary with fetch results
    """
    logger.info(f"Starting targeted event fetch for {len(event_list)} events")
    
    # Get API key
    try:
        api_key = _get_api_key()
    except Exception as e:
        logger.error(f"Failed to load API key: {e}")
        return {
            "success": False,
            "error": str(e),
            "events_fetched": 0,
            "events_inserted": 0,
            "events_updated": 0,
        }
    
    # Ensure database exists
    db_path = _ensure_database()
    
    # Extract unique dates and create event_id set
    unique_dates = extract_unique_dates(event_list)
    target_event_ids = {event_id for event_id, _ in event_list}
    
    logger.info(f"Target: {len(target_event_ids)} unique event_ids across {len(unique_dates)} dates")
    logger.info(f"Date range: {unique_dates[0]} to {unique_dates[-1]}")
    logger.info(f"Estimated API calls: {len(unique_dates)}")
    
    # Fetch events for each date
    all_fetched_events = []
    
    for idx, date in enumerate(unique_dates, 1):
        logger.info(f"[{idx}/{len(unique_dates)}] Fetching {date}")
        
        try:
            events = fetch_events_for_date(
                api_key=api_key,
                country=country.lower(),
                date=date,
                target_event_ids=target_event_ids
            )
            all_fetched_events.extend(events)
        except Exception as e:
            logger.error(f"Failed to fetch {date}: {e}")
        
        # Rate limiting
        if idx < len(unique_dates):
            time.sleep(delay_between_calls)
    
    logger.info(f"Total events fetched: {len(all_fetched_events)}")
    
    # Insert into database
    if all_fetched_events:
        try:
            inserted, updated = _insert_or_update_events(db_path, all_fetched_events)
            logger.info(f"Database updated: {inserted} inserted, {updated} updated")
            
            return {
                "success": True,
                "events_fetched": len(all_fetched_events),
                "events_inserted": inserted,
                "events_updated": updated,
                "unique_dates_queried": len(unique_dates),
                "api_calls_made": len(unique_dates),
                "target_event_count": len(event_list),
                "database_path": str(db_path),
            }
        except Exception as e:
            logger.error(f"Failed to insert events: {e}")
            return {
                "success": False,
                "error": str(e),
                "events_fetched": len(all_fetched_events),
                "events_inserted": 0,
                "events_updated": 0,
            }
    else:
        logger.warning("No events were fetched")
        return {
            "success": False,
            "error": "No events fetched from API",
            "events_fetched": 0,
            "events_inserted": 0,
            "events_updated": 0,
        }


def load_event_list_from_simple_json(json_file: Path) -> List[Tuple[str, str]]:
    """
    Load event list from simple JSON format.
    
    Expected format:
    {
        "events": [
            {"event_id": "...", "event_date": "..."},
            ...
        ]
    }
    
    Args:
        json_file: Path to JSON file
        
    Returns:
        List of (event_id, event_date) tuples
    """
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    events = []
    if 'events' in data:
        for event in data['events']:
            event_id = event.get('event_id')
            event_date = event.get('event_date')
            if event_id and event_date:
                events.append((event_id, event_date))
    
    return events


def main():
    """Main entry point."""
    setup_logging()
    
    print("=" * 70)
    print("  Fetch Specific Events by ID and Date")
    print("=" * 70)
    print()
    
    # Load from config file
    json_file = PROJECT_ROOT / "config" / "target_events_jolts.json"
    
    if json_file.exists():
        print(f"Loading events from: {json_file}")
        event_list = load_event_list_from_simple_json(json_file)
        print(f"Loaded {len(event_list)} events")
    else:
        # Try alternate location (query results from agent)
        json_file = PROJECT_ROOT / "workspace" / "agents" / "eventdata-puller-agent" / "query_results.json"
        if json_file.exists():
            print(f"Loading events from: {json_file}")
            event_list = load_event_list_from_json(json_file)
            print(f"Loaded {len(event_list)} events")
        else:
            print("ERROR: No JSON file found")
            print(f"Expected: {PROJECT_ROOT / 'config' / 'target_events_jolts.json'}")
            return
    
    if not event_list:
        print("ERROR: No events to fetch")
        return
    
    print(f"\nFetching {len(event_list)} specific events...")
    print("This will query only the dates for these events.\n")
    
    result = fetch_specific_events(
        event_list=event_list,
        country="United States",
        delay_between_calls=0.5
    )
    
    print("\n" + "=" * 70)
    print("  Results")
    print("=" * 70)
    print(json.dumps(result, indent=2))
    
    if result.get("success"):
        print(f"\n[OK] Successfully fetched and stored {result['events_fetched']} events")
        print(f"     API calls: {result['api_calls_made']}")
        print(f"     Database: {result['database_path']}")
    else:
        print(f"\n[ERROR] {result.get('error', 'Unknown error')}")


if __name__ == "__main__":
    main()

