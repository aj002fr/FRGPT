#!/usr/bin/env python3
"""
Test script for Trading Economics WebSocket functionality.
Run this script to verify real-time event streaming.

Usage:
    python scripts/test_te_websocket.py [--duration 60] [--country US]
"""

import sys
import time
import argparse
import logging
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.core.logging_config import setup_logging
from src.servers.tradingeconomics.websocket_client import (
    start_event_stream,
    stop_event_stream,
    get_live_events,
    get_stream_status
)

def main():
    parser = argparse.ArgumentParser(description="Test Trading Economics WebSocket")
    parser.add_argument("--duration", type=int, default=60, help="Duration to run in seconds")
    parser.add_argument("--country", type=str, help="Filter by country (e.g., US, UK)")
    parser.add_argument("--importance", type=str, help="Filter by importance (low, medium, high)")
    args = parser.parse_args()

    # Setup logging to console and file
    logger = setup_logging(level=logging.INFO)
    logger.info("Starting WebSocket Test Script")

    # Prepare filters
    countries = [args.country] if args.country else None
    importance = [args.importance] if args.importance else None

    try:
        # 1. Start Stream
        logger.info(f"Step 1: Starting stream (Duration: {args.duration}s)...")
        if countries:
            logger.info(f"  Filter Country: {countries}")
        if importance:
            logger.info(f"  Filter Importance: {importance}")

        start_result = start_event_stream(countries=countries, importance=importance)
        logger.info(f"Start Result: {json.dumps(start_result, indent=2)}")

        if not start_result.get("success", False) and start_result.get("status") != "already_running":
            logger.error("Failed to start stream. Exiting.")
            return

        # 2. Monitor Loop
        logger.info("Step 2: Monitoring stream...")
        start_time = time.time()
        
        while time.time() - start_time < args.duration:
            # Get Status
            status = get_stream_status()
            stats = status.get("statistics", {})
            logger.info(f"Status: Connected={status.get('is_connected')}, "
                        f"Msgs={stats.get('messages_received')}, "
                        f"Events={stats.get('events_processed')}, "
                        f"Errors={stats.get('errors')}")

            # Get Events
            events_result = get_live_events(limit=5)
            events = events_result.get("events", [])
            
            if events:
                logger.info(f"Received {len(events)} new events:")
                for evt in events:
                    logger.info(f"  > {evt['event_name']} ({evt['country']}): Actual={evt.get('actual')} vs Cons={evt.get('consensus')}")
            
            time.sleep(5)

        # 3. Stop Stream
        logger.info("Step 3: Stopping stream...")
        stop_result = stop_event_stream()
        logger.info(f"Stop Result: {json.dumps(stop_result, indent=2)}")

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user. Stopping stream...")
        stop_event_stream()
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        stop_event_stream()

if __name__ == "__main__":
    main()

