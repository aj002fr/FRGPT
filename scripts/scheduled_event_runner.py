#!/usr/bin/env python3
"""
Scheduled Event Runner
----------------------
A proactive scheduling system for economic event analysis.

Features:
1. Build weekly schedule from CSV of target events
2. Fetch upcoming event timestamps from Trading Economics calendar
3. Trigger live event analyzer 2 minutes before each event
4. Send results to Telegram as one-way push alerts

Usage:
    # Build schedule for upcoming week from CSV
    python scripts/scheduled_event_runner.py --build-schedule events.csv
    
    # Run once (check for due events and trigger analysis)
    python scripts/scheduled_event_runner.py --run-once
    
    # Run in loop mode (continuous monitoring)
    python scripts/scheduled_event_runner.py --loop
    
    # Show schedule status
    python scripts/scheduled_event_runner.py --status
    
    # Test Telegram connection
    python scripts/scheduled_event_runner.py --test-telegram
"""

import sys
import time
import argparse
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logging_config import setup_logging
from src.core.schedule_store import (
    add_scheduled_events,
    get_due_events,
    get_upcoming_events,
    update_event_status,
    record_run_history,
    get_schedule_stats,
    parse_csv_events,
    STATUS_SCHEDULED,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from src.core.telegram_notifier import (
    send_telegram_message,
    send_telegram_photo,
    format_event_alert,
    test_telegram_connection,
)
from src.servers.tradingeconomics.query_events import query_event_history
from src.servers.tradingeconomics.fetch_calendar import fetch_economic_calendar

# Configure logger
logger = logging.getLogger("ScheduledEventRunner")

# Default configuration
DEFAULT_TRIGGER_MINUTES = 2  # Trigger analysis X minutes before event
DEFAULT_SYMBOLS = "TY"  # Default symbol to analyze
DEFAULT_LOOP_INTERVAL = 30  # Check interval in seconds


def fetch_upcoming_event_instances(
    events: List[Dict[str, Any]],
    days_ahead: int = 7,
) -> List[Dict[str, Any]]:
    """
    Fetch upcoming instances of events from Trading Economics calendar.
    
    For each event in the input list, queries the calendar to find
    upcoming scheduled instances with their timestamps.
    
    Args:
        events: List of event dicts with event_name, country, etc.
        days_ahead: How many days ahead to look
        
    Returns:
        List of event instances with event_timestamp populated
    """
    logger.info(f"Fetching upcoming events for next {days_ahead} days...")
    
    # First, refresh the calendar
    end_date = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    logger.info(f"Refreshing calendar from {start_date} to {end_date}")
    
    # Refresh calendar for all tracked countries
    refresh_result = fetch_economic_calendar(
        start_date=start_date,
        end_date=end_date,
        full_refresh=False,
    )
    
    if refresh_result.get("success"):
        logger.info(f"Calendar refreshed: {refresh_result.get('events_fetched', 0)} events fetched")
    else:
        logger.warning(f"Calendar refresh failed: {refresh_result.get('error')}")
    
    # Now query for each event
    scheduled_instances = []
    
    for event in events:
        event_name = event.get("event_name")
        country = event.get("country", "US")
        
        if not event_name:
            continue
        
        # Query upcoming instances
        result = query_event_history(
            event_name=event_name,
            country=country,
            lookback_timestamp=start_date,
            limit=10,
        )
        
        if not result.get("success"):
            logger.warning(f"Failed to query {event_name}: {result.get('error')}")
            continue
        
        events_found = result.get("events", [])
        
        # Filter for future events only
        now = datetime.now(timezone.utc)
        for evt in events_found:
            event_date_str = evt.get("event_date")
            if not event_date_str:
                continue
            
            try:
                event_dt = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
                
                # Only include future events
                if event_dt > now:
                    instance = {
                        "event_id": evt.get("event_id") or evt.get("ticker"),
                        "event_name": evt.get("event_name"),
                        "country": evt.get("country"),
                        "importance": evt.get("importance"),
                        "event_timestamp": event_date_str,
                        "symbols": event.get("symbols", DEFAULT_SYMBOLS),
                        "telegram_chat_id": event.get("telegram_chat_id"),
                        "consensus": evt.get("consensus"),
                        "forecast": evt.get("forecast"),
                        "previous": evt.get("previous"),
                    }
                    scheduled_instances.append(instance)
                    logger.info(f"Found upcoming: {event_name} at {event_date_str}")
                    
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse date {event_date_str}: {e}")
                continue
    
    logger.info(f"Found {len(scheduled_instances)} upcoming event instances")
    return scheduled_instances


def run_event_analysis(
    event: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run analysis for a scheduled event.
    
    This function orchestrates the analysis pipeline:
    1. Fetch historical data for percentile calculation
    2. Compute surprise percentile and z-score
    3. Generate distribution plots
    4. Return structured results
    
    Args:
        event: Scheduled event dict
        output_dir: Directory for plot output
        
    Returns:
        Analysis results dict
    """
    event_name = event.get("event_name")
    event_timestamp = event.get("event_timestamp")
    symbols = event.get("symbols", DEFAULT_SYMBOLS)
    country = event.get("country", "US")
    
    logger.info(f"Running analysis for {event_name} ({country}) at {event_timestamp}")
    
    output_dir = output_dir or (PROJECT_ROOT / "workspace" / "plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    start_time = time.time()
    
    try:
        # Import analysis functions from jolts_live_analyzer
        # These are now generalized to work with any event
        from scripts.jolts_live_analyzer import (
            analyze_event_data,
            analyze_market_reactions,
            generate_plots,
        )
        
        # Use the generalized analyze_event_data function from jolts_live_analyzer
        analysis_results = analyze_event_data(
            event_name=event_name,
            event_country=country,
            live_event=None,  # No live event, analyzing from history
            history_limit=100,
        )
        
        if not analysis_results.get("success"):
            return {
                "success": False,
                "error": analysis_results.get("error", "Analysis failed"),
            }
        
        # Extract key metrics from analysis results
        latest_event = analysis_results.get("latest_release", {})
        all_events = analysis_results.get("all_events", [])
        historical_events = all_events[1:] if len(all_events) > 1 else []
        
        # Get surprise metrics from analysis
        surprise_analysis = analysis_results.get("analysis", {}).get("surprise", {})
        percentile_surprise = surprise_analysis.get("percentile_rank")
        z_score = surprise_analysis.get("z_score")
        current_surprise = latest_event.get("surprise")
        
        # Analyze market reactions (if symbols provided)
        market_results = None
        plot_paths = []
        
        try:
            # Check if distro analytics is available
            from src.servers.distro_analytics.db_reader import get_ohlcv_from_db
            DISTRO_AVAILABLE = True
        except ImportError:
            DISTRO_AVAILABLE = False
        
        if DISTRO_AVAILABLE:
            for symbol in symbols.split(","):
                symbol = symbol.strip()
                if not symbol:
                    continue
                
                try:
                    market_results = analyze_market_reactions(
                        events=all_events,
                        symbol=symbol,
                        delta_hrs=1,
                    )
                    
                    if market_results.get("success"):
                        # Generate plots using the full analysis_results
                        symbol_plots = generate_plots(
                            analysis_results,
                            market_results,
                            output_dir,
                            event_name=event_name,
                        )
                        plot_paths.extend(symbol_plots)
                        
                except Exception as e:
                    logger.warning(f"Market analysis failed for {symbol}: {e}")
        
        duration_ms = (time.time() - start_time) * 1000
        
        return {
            "success": True,
            "event_name": event_name,
            "event_timestamp": event_timestamp,
            "country": country,
            "latest_event": latest_event,
            "historical_count": len(historical_events),
            "current_surprise": current_surprise,
            "percentile_surprise": percentile_surprise,
            "z_score": z_score,
            "market_results": market_results,
            "plot_paths": plot_paths,
            "duration_ms": duration_ms,
        }
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "duration_ms": (time.time() - start_time) * 1000,
        }


def send_event_alert(
    event: Dict[str, Any],
    analysis_result: Dict[str, Any],
    send_plot: bool = True,
) -> Dict[str, Any]:
    """
    Send Telegram alert for event analysis.
    
    Args:
        event: Scheduled event dict
        analysis_result: Analysis results from run_event_analysis
        send_plot: Whether to send the first plot image
        
    Returns:
        Dict with send status
    """
    event_name = event.get("event_name", "Unknown Event")
    event_timestamp = event.get("event_timestamp", "")
    chat_id = event.get("telegram_chat_id")  # None uses default
    
    # Extract values from analysis
    latest = analysis_result.get("latest_event", {})
    actual = latest.get("actual")
    consensus = latest.get("consensus") or latest.get("forecast")
    previous = latest.get("previous")
    unit = latest.get("unit", "")
    
    surprise = analysis_result.get("current_surprise")
    percentile = analysis_result.get("percentile_surprise")
    z_score = analysis_result.get("z_score")
    
    # Get market return if available
    market_return_bps = None
    symbol = None
    market_results = analysis_result.get("market_results")
    if market_results and market_results.get("success"):
        symbol = market_results.get("symbol")
        latest_mkt = market_results.get("latest", {})
        market_return_bps = latest_mkt.get("return_bps")
    
    # Format message
    message = format_event_alert(
        event_name=event_name,
        event_time=event_timestamp,
        actual=actual,
        consensus=consensus,
        previous=previous,
        surprise=surprise,
        percentile=percentile,
        z_score=z_score,
        symbol=symbol,
        market_return_bps=market_return_bps,
        unit=unit,
    )
    
    # Send message
    result = send_telegram_message(text=message, chat_id=chat_id)
    
    if not result.get("success"):
        return result
    
    message_id = result.get("message_id")
    
    # Optionally send plot
    if send_plot:
        plot_paths = analysis_result.get("plot_paths", [])
        if plot_paths:
            plot_result = send_telegram_photo(
                photo_path=plot_paths[0],
                caption=f"📈 {event_name} Distribution",
                chat_id=chat_id,
            )
            result["plot_sent"] = plot_result.get("success", False)
    
    return result


def build_schedule(
    csv_path: str,
    days_ahead: int = 7,
    default_symbols: str = DEFAULT_SYMBOLS,
) -> Dict[str, Any]:
    """
    Build schedule from CSV file.
    
    Args:
        csv_path: Path to CSV file with event definitions
        days_ahead: Days ahead to fetch events for
        default_symbols: Default symbols if not in CSV
        
    Returns:
        Dict with build results
    """
    logger.info(f"Building schedule from {csv_path}")
    
    # Parse CSV
    try:
        events = parse_csv_events(csv_path, default_symbols=default_symbols)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}
    
    if not events:
        return {"success": False, "error": "No events found in CSV"}
    
    logger.info(f"Parsed {len(events)} events from CSV")
    
    # Fetch upcoming instances
    instances = fetch_upcoming_event_instances(events, days_ahead=days_ahead)
    
    if not instances:
        return {
            "success": True,
            "message": "No upcoming events found",
            "events_parsed": len(events),
            "instances_found": 0,
        }
    
    # Add to schedule store
    result = add_scheduled_events(instances)
    
    return {
        "success": True,
        "events_parsed": len(events),
        "instances_found": len(instances),
        "inserted": result.get("inserted", 0),
        "updated": result.get("updated", 0),
        "skipped": result.get("skipped", 0),
    }


def run_once(
    trigger_minutes: int = DEFAULT_TRIGGER_MINUTES,
    output_dir: Optional[Path] = None,
    send_alerts: bool = True,
) -> Dict[str, Any]:
    """
    Check for due events and run analysis for any found.
    
    Args:
        trigger_minutes: Minutes before event to trigger
        output_dir: Directory for plot output
        send_alerts: Whether to send Telegram alerts
        
    Returns:
        Dict with run results
    """
    logger.info(f"Checking for events due within {trigger_minutes} minutes...")
    
    due_events = get_due_events(minutes_ahead=trigger_minutes)
    
    if not due_events:
        logger.info("No events due")
        return {
            "success": True,
            "events_processed": 0,
            "message": "No events due",
        }
    
    logger.info(f"Found {len(due_events)} due events")
    
    results = []
    
    for event in due_events:
        event_id = event.get("id")
        event_name = event.get("event_name")
        
        logger.info(f"Processing: {event_name}")
        
        # Mark as running
        update_event_status(event_id, STATUS_RUNNING)
        
        try:
            # Run analysis
            analysis_result = run_event_analysis(event, output_dir=output_dir)
            
            # Send alert if enabled and analysis succeeded
            telegram_result = None
            if send_alerts and analysis_result.get("success"):
                telegram_result = send_event_alert(event, analysis_result)
            
            # Record history and update status
            if analysis_result.get("success"):
                update_event_status(
                    event_id,
                    STATUS_COMPLETED,
                    result_summary=f"Percentile: {analysis_result.get('percentile_surprise', 'N/A')}"
                )
                
                record_run_history(
                    scheduled_event_id=event_id,
                    status=STATUS_COMPLETED,
                    percentile_surprise=analysis_result.get("percentile_surprise"),
                    z_score=analysis_result.get("z_score"),
                    market_return_bps=(
                        analysis_result.get("market_results", {}).get("latest", {}).get("return_bps")
                    ),
                    telegram_sent=telegram_result.get("success", False) if telegram_result else False,
                    telegram_message_id=telegram_result.get("message_id") if telegram_result else None,
                    plot_paths=analysis_result.get("plot_paths"),
                    full_result=analysis_result,
                    duration_ms=analysis_result.get("duration_ms"),
                )
            else:
                update_event_status(
                    event_id,
                    STATUS_FAILED,
                    error_message=analysis_result.get("error"),
                )
                
                record_run_history(
                    scheduled_event_id=event_id,
                    status=STATUS_FAILED,
                    error_message=analysis_result.get("error"),
                    duration_ms=analysis_result.get("duration_ms"),
                )
            
            results.append({
                "event_name": event_name,
                "success": analysis_result.get("success"),
                "telegram_sent": telegram_result.get("success") if telegram_result else None,
            })
            
        except Exception as e:
            logger.error(f"Error processing {event_name}: {e}")
            update_event_status(event_id, STATUS_FAILED, error_message=str(e))
            record_run_history(
                scheduled_event_id=event_id,
                status=STATUS_FAILED,
                error_message=str(e),
            )
            results.append({
                "event_name": event_name,
                "success": False,
                "error": str(e),
            })
    
    return {
        "success": True,
        "events_processed": len(results),
        "results": results,
    }


def run_loop(
    trigger_minutes: int = DEFAULT_TRIGGER_MINUTES,
    check_interval: int = DEFAULT_LOOP_INTERVAL,
    output_dir: Optional[Path] = None,
    send_alerts: bool = True,
) -> None:
    """
    Run continuously, checking for due events.
    
    Args:
        trigger_minutes: Minutes before event to trigger
        check_interval: Seconds between checks
        output_dir: Directory for plot output
        send_alerts: Whether to send Telegram alerts
    """
    logger.info(f"Starting scheduler loop (check every {check_interval}s, trigger {trigger_minutes}min before)")
    print(f"\n[SCHEDULER] Running in loop mode")
    print(f"   Trigger: {trigger_minutes} minutes before event")
    print(f"   Check interval: {check_interval} seconds")
    print(f"   Telegram alerts: {'Yes' if send_alerts else 'No'}")
    print(f"   Press Ctrl+C to stop\n")
    
    last_status_time = datetime.now()
    events_processed = 0
    
    try:
        while True:
            # Check for due events
            result = run_once(
                trigger_minutes=trigger_minutes,
                output_dir=output_dir,
                send_alerts=send_alerts,
            )
            
            events_processed += result.get("events_processed", 0)
            
            # Show status every 5 minutes
            now = datetime.now()
            if (now - last_status_time).total_seconds() >= 300:
                stats = get_schedule_stats()
                print(f"[STATUS] {now.strftime('%H:%M:%S')} - "
                      f"Upcoming: {stats.get('upcoming_scheduled', 0)}, "
                      f"Processed: {events_processed}")
                last_status_time = now
            
            time.sleep(check_interval)
            
    except KeyboardInterrupt:
        print(f"\n[STOPPED] Scheduler stopped. Total events processed: {events_processed}")


def show_status() -> None:
    """Display current schedule status."""
    stats = get_schedule_stats()
    
    print("\n" + "=" * 60)
    print("SCHEDULE STATUS")
    print("=" * 60)
    
    print(f"\nTotal events: {stats.get('total_events', 0)}")
    print(f"Upcoming scheduled: {stats.get('upcoming_scheduled', 0)}")
    print(f"Runs in last 24h: {stats.get('runs_last_24h', 0)}")
    
    status_counts = stats.get("status_counts", {})
    if status_counts:
        print("\nBy status:")
        for status, count in status_counts.items():
            print(f"   {status}: {count}")
    
    # Show upcoming events
    upcoming = get_upcoming_events(hours_ahead=24, status=STATUS_SCHEDULED)
    
    if upcoming:
        print(f"\nUpcoming events (next 24h):")
        print("-" * 60)
        for event in upcoming[:10]:
            print(f"   {event['event_timestamp'][:16]} | {event['event_name'][:30]}")
    
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Scheduled Event Runner - Proactive economic event analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build schedule from CSV
    python scripts/scheduled_event_runner.py --build-schedule events.csv
    
    # Run once (check and process due events)
    python scripts/scheduled_event_runner.py --run-once
    
    # Run in continuous loop
    python scripts/scheduled_event_runner.py --loop
    
    # Show schedule status
    python scripts/scheduled_event_runner.py --status
    
    # Test Telegram connection
    python scripts/scheduled_event_runner.py --test-telegram

CSV Format:
    event_name,country,importance,symbols,telegram_chat_id
    "Non-Farm Payrolls",US,high,TY,
    "CPI YoY",US,high,"TY,ES",
    "JOLTS Job Openings",US,medium,TY,
        """
    )
    
    # Mode selection
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--build-schedule", type=str, metavar="CSV_FILE",
                           help="Build schedule from CSV file")
    mode_group.add_argument("--run-once", action="store_true",
                           help="Run once (check for due events)")
    mode_group.add_argument("--loop", action="store_true",
                           help="Run in continuous loop mode")
    mode_group.add_argument("--status", action="store_true",
                           help="Show schedule status")
    mode_group.add_argument("--test-telegram", action="store_true",
                           help="Test Telegram bot connection")
    
    # Options
    parser.add_argument("--trigger-minutes", type=int, default=DEFAULT_TRIGGER_MINUTES,
                       help=f"Minutes before event to trigger (default: {DEFAULT_TRIGGER_MINUTES})")
    parser.add_argument("--days-ahead", type=int, default=7,
                       help="Days ahead to fetch events (default: 7)")
    parser.add_argument("--check-interval", type=int, default=DEFAULT_LOOP_INTERVAL,
                       help=f"Loop check interval in seconds (default: {DEFAULT_LOOP_INTERVAL})")
    parser.add_argument("--symbols", type=str, default=DEFAULT_SYMBOLS,
                       help=f"Default symbols to analyze (default: {DEFAULT_SYMBOLS})")
    parser.add_argument("--output-dir", type=str, default=None,
                       help="Output directory for plots")
    parser.add_argument("--no-telegram", action="store_true",
                       help="Disable Telegram alerts")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level=log_level)
    
    # Determine output directory
    output_dir = Path(args.output_dir) if args.output_dir else None
    
    # Execute mode
    if args.build_schedule:
        result = build_schedule(
            csv_path=args.build_schedule,
            days_ahead=args.days_ahead,
            default_symbols=args.symbols,
        )
        
        if result.get("success"):
            print(f"\n[OK] Schedule built successfully")
            print(f"   Events parsed from CSV: {result.get('events_parsed', 0)}")
            print(f"   Upcoming instances found: {result.get('instances_found', 0)}")
            print(f"   Inserted: {result.get('inserted', 0)}")
            print(f"   Updated: {result.get('updated', 0)}")
            print(f"   Skipped: {result.get('skipped', 0)}")
        else:
            print(f"\n[ERROR] {result.get('error')}")
            return 1
    
    elif args.run_once:
        result = run_once(
            trigger_minutes=args.trigger_minutes,
            output_dir=output_dir,
            send_alerts=not args.no_telegram,
        )
        
        processed = result.get("events_processed", 0)
        if processed > 0:
            print(f"\n[OK] Processed {processed} events")
            for r in result.get("results", []):
                status = "OK" if r.get("success") else "FAIL"
                tg = "📱" if r.get("telegram_sent") else ""
                print(f"   [{status}] {r.get('event_name')} {tg}")
        else:
            print(f"\n[OK] No events due at this time")
    
    elif args.loop:
        run_loop(
            trigger_minutes=args.trigger_minutes,
            check_interval=args.check_interval,
            output_dir=output_dir,
            send_alerts=not args.no_telegram,
        )
    
    elif args.status:
        show_status()
    
    elif args.test_telegram:
        print("\n[TEST] Testing Telegram connection...")
        result = test_telegram_connection()
        
        if result.get("success"):
            print(f"[OK] Bot connected: @{result.get('bot_username')}")
            print(f"     Chat ID configured: {'Yes' if result.get('chat_id_configured') else 'No'}")
            
            # Send a test message
            test_result = send_telegram_message(
                text="🔔 <b>Scheduled Event Runner</b>\n\nTest message - connection successful!",
            )
            if test_result.get("success"):
                print(f"[OK] Test message sent (ID: {test_result.get('message_id')})")
            else:
                print(f"[WARN] Could not send test message: {test_result.get('error')}")
        else:
            print(f"[ERROR] {result.get('error')}")
            return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

