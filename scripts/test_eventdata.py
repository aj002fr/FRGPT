"""EventData Puller Agent test script.

Test script for economic event data functionality using Trading Economics API.

Usage:
    # Find exact event names
    python scripts/test_eventdata.py --find-name "JOLTS" --country US
    python scripts/test_eventdata.py --find-name "NFP"
    
    # Update economic calendar
    python scripts/test_eventdata.py --update
    
    # Query specific event history
    python scripts/test_eventdata.py --query "Non-Farm Payrolls"
    python scripts/test_eventdata.py --query NFP --country US --lookback 90
    
    # Find correlated events
    python scripts/test_eventdata.py --correlate "CPI" --window 6
    
    # Search events
    python scripts/test_eventdata.py --search "inflation" --country US
    
    # Show sample queries
    python scripts/test_eventdata.py --list
"""

import sys
import argparse
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logging_config import setup_logging  # noqa: E402


# =============================================================================
# Default values for IDE debug mode (F5 without CLI args)
# =============================================================================

# Set to True to run default demo when no CLI args provided
RUN_DEFAULT_DEMO = True

# Default demo configuration - change these to test different scenarios
DEFAULT_ACTION = "stream_start"  # Options: "query_event", "find_correlations", "search_events", "update_calendar", "stream_start", "stream_stop", "stream_status"

# Default parameters for query_event action
DEFAULT_EVENT_NAME = "Initial Jobless Claims"      # Change this to test other events (e.g., "Non-Farm Payrolls", "GDP")
DEFAULT_COUNTRY = "US"         # Change this to test other countries (e.g., "United Kingdom", "Germany")
DEFAULT_LOOKBACK_DAYS = 2000      # Look back 90 days
DEFAULT_WINDOW_HOURS = 24       # 12 hour window for correlations
DEFAULT_INCLUDE_CORRELATIONS = True # Include correlated events

# Default parameters for search action
DEFAULT_SEARCH_KEYWORD = None
DEFAULT_SEARCH_IMPORTANCE = None
DEFAULT_SEARCH_LIMIT = 1000

# Default parameters for WebSocket streaming
DEFAULT_STREAM_DURATION_MINUTES = 10    # How long to keep stream open (in minutes)
DEFAULT_STREAM_SCHEDULE = "08:29"          # Schedule start time (e.g., "08:30" for 8:30 AM local time, or "2025-12-03 13:25" for specific date/time)
DEFAULT_STREAM_IMPORTANCE = None     # Filter: "high", "medium", "low", or None for all
DEFAULT_STREAM_AUTO_STOP = True         # Auto-stop after duration (False = run until manually stopped)


# =============================================================================
# Sample queries for testing
# =============================================================================

# Sample queries for testing
SAMPLE_QUERIES = [
    {
        "id": 1,
        "name": "Update US economic calendar",
        "description": "Fetch/update economic events for the US",
        "action": "update_calendar",
        "params": {"country": "US"},
    },
    {
        "id": 2,
        "name": "Non-Farm Payrolls history",
        "description": "Query all Non-Farm Payrolls releases in the last year",
        "action": "query_event",
        "params": {"event_name": "Non-Farm Payrolls", "country": "US", "lookback_days": 365},
    },
    {
        "id": 3,
        "name": "CPI with correlations",
        "description": "Query CPI releases with correlated events within ±6 hours",
        "action": "query_event",
        "params": {"event_name": "CPI", "country": "US", "window_hours": 6, "include_correlations": True},
    },
    {
        "id": 4,
        "name": "FOMC correlations",
        "description": "Find events that happened within ±12 hours of FOMC decisions",
        "action": "find_correlations",
        "params": {"event_name": "Interest Rate Decision", "country": "US", "window_hours": 12},
    },
    {
        "id": 5,
        "name": "Search high-importance events",
        "description": "Search for high-importance US economic events",
        "action": "search_events",
        "params": {"country": "US", "importance": "high"},
    },
    {
        "id": 6,
        "name": "GDP releases",
        "description": "Query GDP releases for major economies",
        "action": "query_event",
        "params": {"event_name": "GDP", "lookback_days": 180},
    },
    {
        "id": 7,
        "name": "UK economic events",
        "description": "Update and search UK economic calendar",
        "action": "search_events",
        "params": {"country": "GB", "importance": "medium"},
    },
    {
        "id": 8,
        "name": "Eurozone events",
        "description": "Search ECB and Eurozone economic events",
        "action": "search_events",
        "params": {"country": "EU", "keyword": "ECB"},
    },
]


def run_default_demo():
    """
    Run default demo for IDE debug mode (no CLI args).
    
    Modify DEFAULT_* constants at top of file to change behavior.
    """
    print("\n" + "=" * 70)
    print("  EventData Puller - Default Demo Mode")
    print("  (Running with defaults - modify DEFAULT_* constants to change)")
    print("=" * 70)
    
    if DEFAULT_ACTION == "query_event":
        print(f"\n[DEMO] Querying: {DEFAULT_EVENT_NAME}")
        print(f"       Country: {DEFAULT_COUNTRY}")
        print(f"       Lookback: {DEFAULT_LOOKBACK_DAYS} days")
        print(f"       Window: +/-{DEFAULT_WINDOW_HOURS} hours")
        print(f"       Include correlations: {DEFAULT_INCLUDE_CORRELATIONS}")
        
        run_query(
            event_name=DEFAULT_EVENT_NAME,
            country=DEFAULT_COUNTRY,
            lookback_days=DEFAULT_LOOKBACK_DAYS,
            window_hours=DEFAULT_WINDOW_HOURS,
            include_correlations=DEFAULT_INCLUDE_CORRELATIONS
        )
    
    elif DEFAULT_ACTION == "find_correlations":
        print(f"\n[DEMO] Finding correlations for: {DEFAULT_EVENT_NAME}")
        print(f"       Country: {DEFAULT_COUNTRY}")
        print(f"       Window: +/-{DEFAULT_WINDOW_HOURS} hours")
        
        run_correlations(
            event_name=DEFAULT_EVENT_NAME,
            country=DEFAULT_COUNTRY,
            window_hours=DEFAULT_WINDOW_HOURS
        )
    
    elif DEFAULT_ACTION == "search_events":
        print(f"\n[DEMO] Searching events")
        print(f"       Keyword: {DEFAULT_SEARCH_KEYWORD}")
        print(f"       Country: {DEFAULT_COUNTRY}")
        print(f"       Importance: {DEFAULT_SEARCH_IMPORTANCE}")
        
        run_search(
            keyword=DEFAULT_SEARCH_KEYWORD,
            country=DEFAULT_COUNTRY,
            importance=DEFAULT_SEARCH_IMPORTANCE,
            limit=DEFAULT_SEARCH_LIMIT
        )
    
    elif DEFAULT_ACTION == "update_calendar":
        print(f"\n[DEMO] Updating calendar")
        print(f"       Country: {DEFAULT_COUNTRY}")
        
        run_update(country=DEFAULT_COUNTRY)
    
    elif DEFAULT_ACTION in ("stream_start", "stream_stop", "stream_status"):
        print(f"\n[DEMO] WebSocket Stream: {DEFAULT_ACTION}")
        run_stream_action(
            DEFAULT_ACTION,
            country=DEFAULT_COUNTRY,
            importance=DEFAULT_STREAM_IMPORTANCE,
            duration_minutes=DEFAULT_STREAM_DURATION_MINUTES,
            schedule_time=DEFAULT_STREAM_SCHEDULE,
            auto_stop=DEFAULT_STREAM_AUTO_STOP
        )
    
    else:
        print(f"\n[ERROR] Unknown DEFAULT_ACTION: {DEFAULT_ACTION}")
        print("        Valid options: query_event, find_correlations, search_events, update_calendar, stream_start, stream_stop, stream_status")


def list_queries():
    """List all sample queries."""
    print("\n[QUERIES] Sample EventData Queries:\n")
    print("-" * 70)
    
    for query in SAMPLE_QUERIES:
        print(f"  [{query['id']}] {query['name']}")
        print(f"      {query['description']}")
        print(f"      Action: {query['action']}")
        print(f"      Params: {query['params']}")
        print()
    
    print("-" * 70)
    print("\nUsage:")
    print("  python scripts/test_eventdata.py --run N    # Run query N")
    print("  python scripts/test_eventdata.py --query 'CPI'")
    print("  python scripts/test_eventdata.py --update --country US")


def run_sample_query(query_id: int) -> None:
    """Run a sample query by ID."""
    query = next((q for q in SAMPLE_QUERIES if q["id"] == query_id), None)
    
    if not query:
        print(f"❌ Query {query_id} not found. Use --list to see available queries.")
        return
    
    print(f"\n>> Running Sample Query [{query_id}]: {query['name']}")
    print(f"   {query['description']}\n")
    
    from src.agents.eventdata_puller_agent import EventDataPullerAgent
    
    try:
        agent = EventDataPullerAgent()
        
        params = query["params"].copy()
        params["action"] = query["action"]
        
        output_path = agent.run(**params)
        
        print(f"[SUCCESS] Query completed successfully!")
        print(f"   Output: {output_path}\n")
        
        _display_results(output_path)
        
    except Exception as exc:
        print(f"❌ Error running query: {exc}")
        logging.exception("Query failed")


def run_find_name(keyword: str, country: Optional[str] = None) -> None:
    """Find exact event names matching a keyword."""
    print(f"\n>> Searching for event names matching: '{keyword}'")
    if country:
        print(f"   Country filter: {country}")
    print()
    
    from src.mcp.client import MCPClient
    
    try:
        client = MCPClient()
        result = client.call_tool(
            "search_event_names",
            arguments={
                "keyword": keyword,
                "country": country,
                "limit": 20
            }
        )
        
        if not result.get("success"):
            print(f"❌ Search failed: {result.get('error', 'Unknown error')}")
            return
        
        matches = result.get("matches", [])
        source = result.get("source", "unknown")
        
        print(f"[FOUND] {len(matches)} matches (source: {source})")
        print()
        
        for i, match in enumerate(matches, 1):
            event_name = match.get("event_name", "Unknown")
            country_val = match.get("country", match.get("countries", ""))
            importance = match.get("importance", "")
            category = match.get("category", "")
            
            print(f"  {i}. {event_name}")
            
            if isinstance(country_val, list):
                print(f"      Countries: {', '.join(country_val[:5])}{' ...' if len(country_val) > 5 else ''}")
            else:
                print(f"      Country: {country_val}")
            
            if category:
                print(f"      Category: {category}")
            if importance:
                imp_str = {1: "Low", 2: "Medium", 3: "High"}.get(importance, str(importance))
                print(f"      Importance: {imp_str}")
            print()
        
    except Exception as exc:
        print(f"❌ Error searching: {exc}")
        logging.exception("Search failed")


def run_update(country: str = None, full_refresh: bool = False) -> None:
    """Update economic calendar."""
    print("\n>> Updating Economic Calendar")
    if country:
        print(f"   Country filter: {country}")
    if full_refresh:
        print("   Mode: Full refresh")
    print()
    
    from src.agents.eventdata_puller_agent import EventDataPullerAgent
    
    try:
        agent = EventDataPullerAgent()
        output_path = agent.run(
            action="update_calendar",
            country=country,
            full_refresh=full_refresh
        )
        
        print(f"[SUCCESS] Calendar update completed!")
        print(f"   Output: {output_path}\n")
        
        _display_results(output_path)
        
    except Exception as exc:
        print(f"❌ Error updating calendar: {exc}")
        logging.exception("Calendar update failed")


def _wait_for_schedule(schedule_time: str) -> None:
    """
    Wait until the scheduled time.
    
    Args:
        schedule_time: Time string in format "HH:MM" (today) or "YYYY-MM-DD HH:MM" (specific date)
    """
    import time
    from datetime import datetime, timedelta
    
    now = datetime.now()
    
    # Parse schedule time
    if " " in schedule_time:
        # Full datetime format: "2025-12-03 13:25"
        target = datetime.strptime(schedule_time, "%Y-%m-%d %H:%M")
    else:
        # Time only format: "13:25" - assume today
        time_parts = schedule_time.split(":")
        target = now.replace(hour=int(time_parts[0]), minute=int(time_parts[1]), second=0, microsecond=0)
        
        # If time already passed today, schedule for tomorrow
        if target < now:
            target += timedelta(days=1)
    
    wait_seconds = (target - now).total_seconds()
    
    if wait_seconds <= 0:
        print(f"   Schedule time {schedule_time} already passed, starting immediately...")
        return
    
    print(f"\n   ⏰ SCHEDULED START")
    print(f"   Target time: {target.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Waiting: {int(wait_seconds // 60)} minutes {int(wait_seconds % 60)} seconds")
    print()
    
    # Wait with countdown updates
    while wait_seconds > 0:
        mins, secs = divmod(int(wait_seconds), 60)
        hours, mins = divmod(mins, 60)
        
        if hours > 0:
            countdown = f"{hours}h {mins}m {secs}s"
        elif mins > 0:
            countdown = f"{mins}m {secs}s"
        else:
            countdown = f"{secs}s"
        
        print(f"\r   Starting in: {countdown}     ", end="", flush=True)
        
        # Sleep in chunks for responsive Ctrl+C
        sleep_chunk = min(wait_seconds, 1)
        time.sleep(sleep_chunk)
        wait_seconds -= sleep_chunk
    
    print("\n   Time reached! Starting stream...\n")


def run_stream_action(
    action: str,
    country: str = None,
    importance: str = None,
    duration_minutes: float = None,
    schedule_time: str = None,
    auto_stop: bool = True
) -> None:
    """
    Run WebSocket stream actions (start, stop, status).
    
    Args:
        action: One of "stream_start", "stream_stop", "stream_status"
        country: Country filter for stream_start
        importance: Importance filter for stream_start
        duration_minutes: How long to keep stream running (default from DEFAULT_STREAM_DURATION_MINUTES)
        schedule_time: Optional scheduled start time (e.g., "13:25" or "2025-12-03 13:25")
        auto_stop: Whether to automatically stop after duration (default True)
    """
    import time
    from src.agents.eventdata_puller_agent import EventDataPullerAgent
    from src.bus.file_bus import read_json
    
    # Use defaults if not specified
    if duration_minutes is None:
        duration_minutes = DEFAULT_STREAM_DURATION_MINUTES
    if schedule_time is None:
        schedule_time = DEFAULT_STREAM_SCHEDULE
    
    agent = EventDataPullerAgent()
    
    if action == "stream_start":
        print("\n" + "=" * 60)
        print("  WebSocket Stream - Trading Economics Live Events")
        print("=" * 60)
        
        if country:
            print(f"   Country filter: {country}")
        if importance:
            print(f"   Importance filter: {importance}")
        print(f"   Duration: {duration_minutes} minutes")
        print(f"   Auto-stop: {auto_stop}")
        
        # Wait for scheduled time if specified
        if schedule_time:
            _wait_for_schedule(schedule_time)
        
        try:
            print("\n>> Starting WebSocket Stream...")
            output_path = agent.run(
                action="stream_start",
                country=country,
                importance=importance
            )
            result = read_json(output_path)
            data = result.get("data", [{}])[0]
            
            status = data.get("status", "unknown")
            if status in ("started", "already_running"):
                print(f"[SUCCESS] WebSocket stream {status}!")
                print(f"   Output: {output_path}")
                
                # Calculate monitoring parameters
                total_seconds = int(duration_minutes * 60)
                update_interval = 10  # seconds between status updates
                num_updates = total_seconds // update_interval
                
                print(f"\n   📡 Monitoring for {duration_minutes} minutes...")
                print(f"   Press Ctrl+C to stop early\n")
                print("   " + "-" * 50)
                
                start_time = time.time()
                last_events = 0
                
                try:
                    for i in range(num_updates):
                        time.sleep(update_interval)
                        elapsed = time.time() - start_time
                        remaining = total_seconds - elapsed
                        
                        # Get status
                        status_path = agent.run(action="stream_status")
                        status_result = read_json(status_path)
                        status_data = status_result.get("data", [{}])[0]
                        
                        is_running = status_data.get("is_running", False)
                        is_connected = status_data.get("is_connected", False)
                        stats = status_data.get("statistics", {})
                        msgs = stats.get("messages_received", 0)
                        events = stats.get("events_processed", 0)
                        queue_size = status_data.get("queue_size", 0)
                        
                        # Format elapsed/remaining
                        elapsed_str = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
                        remaining_str = f"{int(remaining // 60):02d}:{int(remaining % 60):02d}"
                        
                        # Check for new events
                        new_events_count = events - last_events
                        event_indicator = f" 🔔 +{new_events_count} NEW!" if new_events_count > 0 else ""
                        last_events = events
                        
                        # Connection status
                        conn_status = "🟢" if is_connected else "🔴"
                        
                        print(f"   [{elapsed_str}] {conn_status} Msgs={msgs:4d} | Events={events:3d} | Queue={queue_size:3d} | Remaining: {remaining_str}{event_indicator}")
                        
                        # Fetch and display new events if any
                        if new_events_count > 0 or queue_size > 0:
                            try:
                                events_path = agent.run(action="get_live_events", limit=5)
                                events_result = read_json(events_path)
                                events_data = events_result.get("data", [{}])[0]
                                live_events = events_data.get("events", [])
                                
                                if live_events:
                                    print(f"   Latest Events:")
                                    for evt in live_events:
                                        name = evt.get("event_name", "Unknown")
                                        ctry = evt.get("country", "")
                                        act = evt.get("actual")
                                        cons = evt.get("consensus")
                                        prev = evt.get("previous")
                                        print(f"      > {name} ({ctry}): Actual={act} | Consensus={cons} | Prev={prev}")
                            except Exception as e:
                                print(f"      ⚠️ Error fetching live events: {e}")

                        if not is_running:
                            print("\n   ⚠️  Stream stopped unexpectedly!")
                            break
                            
                except KeyboardInterrupt:
                    print("\n\n   ⏹️  Interrupted by user")
                
                print("   " + "-" * 50)
                
                # Stop the stream if auto_stop is enabled
                if auto_stop:
                    print("\n>> Stopping stream...")
                    stop_path = agent.run(action="stream_stop")
                    stop_result = read_json(stop_path)
                    stop_data = stop_result.get("data", [{}])[0]
                    final_stats = stop_data.get("final_stats", {})
                    
                    print("\n" + "=" * 60)
                    print("  STREAM SUMMARY")
                    print("=" * 60)
                    print(f"   Total messages received: {final_stats.get('messages_received', 0)}")
                    print(f"   Events processed: {final_stats.get('events_processed', 0)}")
                    print(f"   Errors: {final_stats.get('errors', 0)}")
                    print(f"   Started: {final_stats.get('start_time', 'N/A')}")
                    print(f"   Last message: {final_stats.get('last_message_time', 'None')}")
                    print("=" * 60)
                else:
                    print("\n   Stream still running (auto_stop=False)")
                    print("   Run with --stream-stop to stop manually")
                
            elif status == "error":
                print(f"[ERROR] {data.get('error', 'Unknown error')}")
                print(f"   {data.get('message', '')}")
            else:
                print(f"[INFO] Status: {status}")
                print(f"   {data.get('message', '')}")
                
        except KeyboardInterrupt:
            print("\n\n   Stopping due to interrupt...")
            agent.run(action="stream_stop")
            print("   Stream stopped.")
        except Exception as exc:
            print(f"❌ Error with stream: {exc}")
            logging.exception("Stream action failed")
    
    elif action == "stream_stop":
        print("\n>> Stopping WebSocket Stream")
        try:
            output_path = agent.run(action="stream_stop")
            result = read_json(output_path)
            data = result.get("data", [{}])[0]
            print(f"   Status: {data.get('status', 'unknown')}")
            if data.get("final_stats"):
                print(f"   Final stats: {json.dumps(data['final_stats'], indent=2)}")
        except Exception as exc:
            print(f"❌ Error stopping stream: {exc}")
    
    elif action == "stream_status":
        print("\n>> WebSocket Stream Status")
        try:
            output_path = agent.run(action="stream_status")
            result = read_json(output_path)
            data = result.get("data", [{}])[0]
            print(f"   Running: {data.get('is_running', False)}")
            print(f"   Connected: {data.get('is_connected', False)}")
            print(f"   Queue size: {data.get('queue_size', 0)}")
            if data.get("statistics"):
                print(f"   Stats: {json.dumps(data['statistics'], indent=2)}")
            if data.get("error"):
                print(f"   Error: {data['error']}")
        except Exception as exc:
            print(f"❌ Error getting status: {exc}")


def run_query(
    event_name: str,
    country: str = None,
    lookback_days: int = None,
    window_hours: float = 12,
    include_correlations: bool = True
) -> None:
    """Query event history."""
    print(f"\n>> Querying Event History: {event_name}")
    if country:
        print(f"   Country: {country}")
    if lookback_days:
        print(f"   Lookback: {lookback_days} days")
    print(f"   Correlation window: ±{window_hours} hours")
    print()
    
    from src.agents.eventdata_puller_agent import EventDataPullerAgent
    
    try:
        agent = EventDataPullerAgent()
        output_path = agent.run(
            action="query_event",
            event_name=event_name,
            country=country,
            lookback_days=lookback_days,
            window_hours=window_hours,
            include_correlations=include_correlations
        )
        
        print(f"[SUCCESS] Query completed!")
        print(f"   Output: {output_path}\n")
        
        _display_results(output_path)
        
    except Exception as exc:
        print(f"❌ Error querying event: {exc}")
        logging.exception("Query failed")


def run_correlations(
    event_name: str,
    country: str = None,
    target_date: str = None,
    window_hours: float = 12
) -> None:
    """Find correlated events."""
    print(f"\n>> Finding Correlated Events for: {event_name}")
    print(f"   Window: ±{window_hours} hours")
    if country:
        print(f"   Country: {country}")
    if target_date:
        print(f"   Target date: {target_date}")
    print()
    
    from src.agents.eventdata_puller_agent import EventDataPullerAgent
    
    try:
        agent = EventDataPullerAgent()
        output_path = agent.run(
            action="find_correlations",
            event_name=event_name,
            country=country,
            target_event_date=target_date,
            window_hours=window_hours
        )
        
        print(f"[SUCCESS] Correlation search completed!")
        print(f"   Output: {output_path}\n")
        
        _display_results(output_path)
        
    except Exception as exc:
        print(f"❌ Error finding correlations: {exc}")
        logging.exception("Correlation search failed")


def run_search(
    keyword: str = None,
    country: str = None,
    category: str = None,
    importance: str = None,
    limit: int = 50
) -> None:
    """Search events."""
    print("\n>> Searching Events")
    if keyword:
        print(f"   Keyword: {keyword}")
    if country:
        print(f"   Country: {country}")
    if category:
        print(f"   Category: {category}")
    if importance:
        print(f"   Importance: {importance}")
    print(f"   Limit: {limit}")
    print()
    
    from src.agents.eventdata_puller_agent import EventDataPullerAgent
    
    try:
        agent = EventDataPullerAgent()
        output_path = agent.run(
            action="search_events",
            keyword=keyword,
            country=country,
            category=category,
            importance=importance,
            limit=limit
        )
        
        print(f"[SUCCESS] Search completed!")
        print(f"   Output: {output_path}\n")
        
        _display_results(output_path)
        
    except Exception as exc:
        print(f"❌ Error searching events: {exc}")
        logging.exception("Search failed")


def _display_results(output_path: Path) -> None:
    """Display results from output file."""
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        result_data = data["data"][0] if data.get("data") else {}
        metadata = data.get("metadata", {})
        
        print("[RESULTS]")
        print(f"   Action: {metadata.get('action', 'N/A')}")
        print(f"   Success: {result_data.get('success', 'N/A')}")
        
        # Show event count
        events = result_data.get("events", [])
        print(f"   Events found: {len(events)}")
        
        # Show summary if available
        summary = result_data.get("summary", {})
        if summary:
            print("\n[SUMMARY]")
            for key, value in summary.items():
                if isinstance(value, float):
                    print(f"   {key}: {value:.4f}")
                else:
                    print(f"   {key}: {value}")
        
        # Show top events
        if events:
            print("\n[TOP EVENTS]")
            for i, event in enumerate(events[:5], 1):
                name = event.get("event_name", "Unknown")
                country = event.get("country", "")
                date = event.get("event_date", "")[:10]
                actual = event.get("actual")
                forecast = event.get("forecast")
                importance = event.get("importance", "")
                
                print(f"\n   {i}. {name} ({country})")
                print(f"      Date: {date}")
                print(f"      Importance: {importance}")
                if actual is not None:
                    print(f"      Actual: {actual}")
                if forecast is not None:
                    print(f"      Forecast: {forecast}")
                    if actual is not None:
                        diff = actual - forecast
                        direction = "beat" if diff > 0 else "miss" if diff < 0 else "inline"
                        print(f"      Result: {direction} ({diff:+.2f})")
        
        # Show correlations if available
        correlations = result_data.get("correlations", [])
        if correlations:
            print("\n[CORRELATIONS]")
            for corr in correlations[:3]:
                date = corr.get("event_date", "")[:10]
                count = corr.get("correlated_count", 0)
                print(f"\n   Event on {date}: {count} correlated events")
                
                for ce in corr.get("correlated_events", [])[:3]:
                    ce_name = ce.get("event_name", "")
                    ce_hours = ce.get("hours_from_target", 0)
                    ce_timing = "before" if ce_hours < 0 else "after"
                    print(f"      - {ce_name} ({abs(ce_hours):.1f}h {ce_timing})")
        
        # Show correlated events for find_correlations action
        correlated_events = result_data.get("correlated_events", [])
        if correlated_events and not correlations:
            print("\n[CORRELATED EVENTS]")
            for i, event in enumerate(correlated_events[:10], 1):
                name = event.get("event_name", "Unknown")
                hours = event.get("hours_from_target", 0)
                timing = event.get("timing", "")
                importance = event.get("importance", "")
                
                print(f"   {i}. {name} ({hours:+.1f}h {timing}, {importance})")
        
    except Exception as e:
        print(f"   Could not display results: {e}")


def main() -> None:
    """Main CLI entry point."""
    setup_logging()
    
    parser = argparse.ArgumentParser(
        description="EventData Puller Agent - Test Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_eventdata.py --list                  # Show sample queries
  python scripts/test_eventdata.py --run 2                 # Run sample query 2
  python scripts/test_eventdata.py --find-name "JOLTS"     # Find exact event name
  python scripts/test_eventdata.py --update --country US   # Update US calendar
  python scripts/test_eventdata.py --query "CPI"           # Query CPI events
  python scripts/test_eventdata.py --correlate "NFP" --window 6
  python scripts/test_eventdata.py --search --country US --importance high
        """
    )
    
    # Action groups
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--list", action="store_true", help="List sample queries")
    action_group.add_argument("--run", type=int, metavar="N", help="Run sample query N")
    action_group.add_argument("--update", action="store_true", help="Update economic calendar")
    action_group.add_argument("--query", type=str, metavar="EVENT", help="Query event history")
    action_group.add_argument("--correlate", type=str, metavar="EVENT", help="Find correlated events")
    action_group.add_argument("--search", action="store_true", help="Search events")
    action_group.add_argument("--find-name", type=str, metavar="KEYWORD", help="Find exact event name (e.g., 'JOLTS', 'NFP')")
    
    # Common parameters
    parser.add_argument("--country", type=str, help="Country code (e.g., US, GB, EU)")
    parser.add_argument("--lookback", type=int, help="Days to look back")
    parser.add_argument("--window", type=float, default=12, help="Correlation window hours (default: 12)")
    parser.add_argument("--importance", type=str, choices=["low", "medium", "high"], help="Importance filter")
    parser.add_argument("--keyword", type=str, help="Search keyword")
    parser.add_argument("--category", type=str, help="Event category filter")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD)")
    parser.add_argument("--full-refresh", action="store_true", help="Full calendar refresh")
    parser.add_argument("--no-correlations", action="store_true", help="Skip correlation analysis")
    parser.add_argument("--limit", type=int, default=50, help="Maximum results")
    
    args = parser.parse_args()
    
    # Check if any action was specified
    has_action = any([
        args.list,
        args.run,
        args.update,
        args.query,
        args.correlate,
        args.search,
        args.find_name
    ])
    
    # Execute action
    if args.list:
        list_queries()
    elif args.run:
        run_sample_query(args.run)
    elif args.find_name:
        run_find_name(keyword=args.find_name, country=args.country)
    elif args.update:
        run_update(country=args.country, full_refresh=args.full_refresh)
    elif args.query:
        run_query(
            event_name=args.query,
            country=args.country,
            lookback_days=args.lookback,
            window_hours=args.window,
            include_correlations=not args.no_correlations
        )
    elif args.correlate:
        run_correlations(
            event_name=args.correlate,
            country=args.country,
            target_date=args.date,
            window_hours=args.window
        )
    elif args.search:
        run_search(
            keyword=args.keyword,
            country=args.country,
            category=args.category,
            importance=args.importance,
            limit=args.limit
        )
    elif not has_action and RUN_DEFAULT_DEMO:
        # No CLI args and demo mode enabled - run default demo
        run_default_demo()
    else:
        # Default: show list
        list_queries()


if __name__ == "__main__":
    main()

