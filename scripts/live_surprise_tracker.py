#!/usr/bin/env python3
"""
Live Surprise Tracker
---------------------
A lightweight, low-latency script that:
1. Maintains a persistent WebSocket connection to Trading Economics.
2. Monitors for live economic event releases.
3. Immediately calculates the 'surprise' (Actual - Consensus).
4. Compares the surprise with historical data to calculate a percentile rank.
5. Outputs the analysis in real-time.

Usage:
    python scripts/live_surprise_tracker.py --country US
    python scripts/live_surprise_tracker.py --country US --importance high
    python scripts/live_surprise_tracker.py --update-history  # Update DB first
"""

import sys
import time
import argparse
import logging
import sqlite3
import statistics
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import core tools directly
from src.core.logging_config import setup_logging
from src.servers.tradingeconomics.websocket_client import TEWebSocketClient
from src.servers.tradingeconomics.query_events import query_event_history
from src.servers.tradingeconomics.fetch_calendar import fetch_economic_calendar

# Configure logger
logger = logging.getLogger("LiveSurpriseTracker")

def calculate_percentile_rank(value: float, dataset: List[float]) -> float:
    """Calculate the percentile rank of a value within a dataset."""
    if not dataset:
        return 0.0
    
    sorted_data = sorted(dataset)
    smaller_count = sum(1 for x in sorted_data if x < value)
    return (smaller_count / len(sorted_data)) * 100

def analyze_surprise(event: Dict[str, Any]) -> None:
    """Analyze a single live event."""
    event_name = event.get("event_name")
    country = event.get("country")
    actual = event.get("actual")
    consensus = event.get("consensus")
    event_id = event.get("event_id")

    # We need both Actual and Consensus to calculate surprise
    if actual is None or consensus is None:
        return

    # 1. Calculate Surprise
    surprise = actual - consensus
    
    # 2. Fetch History
    # We use the event_name and country to find past instances
    logger.info(f"Analyzing history for: {event_name} ({country})...")
    history_result = query_event_history(
        event_name=event_name,
        country=country,
        limit=100  # Last 100 instances
    )
    
    historical_surprises = []
    if history_result.get("success"):
        events = history_result.get("events", [])
        for hist_evt in events:
            h_actual = hist_evt.get("actual")
            h_consensus = hist_evt.get("consensus")
            # Exclude current event if it made it to DB already to avoid bias, 
            # though usually history query excludes 'today' or we can check ID
            if h_actual is not None and h_consensus is not None:
                # Basic dedup
                if hist_evt.get("event_date") != event.get("event_date"):
                    historical_surprises.append(h_actual - h_consensus)

    # 3. Calculate Stats
    rank = 0.0
    z_score = 0.0
    if historical_surprises:
        rank = calculate_percentile_rank(surprise, historical_surprises)
        try:
            mean = statistics.mean(historical_surprises)
            stdev = statistics.stdev(historical_surprises)
            if stdev > 0:
                z_score = (surprise - mean) / stdev
        except statistics.StatisticsError:
            pass

    # 4. Output Result
    print("\n" + "="*60)
    print(f"🚨 LIVE EVENT RELEASE: {event_name} ({country})")
    print(f"   Time: {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 60)
    print(f"   Actual:    {actual}")
    print(f"   Consensus: {consensus}")
    print(f"   Surprise:  {surprise:+.4f}")
    print("-" * 60)
    
    if historical_surprises:
        print(f"   Historical Context ({len(historical_surprises)} events):")
        print(f"   Percentile Rank: {rank:.1f}%")
        print(f"   Z-Score:         {z_score:+.2f}")
        
        # Interpretation
        if abs(z_score) > 2.0:
            print("   ⚠️  EXTREME SURPRISE (> 2 Sigma)")
        elif abs(z_score) > 1.0:
            print("   ⚠️  SIGNIFICANT SURPRISE (> 1 Sigma)")
        else:
            print("   ✅  Inline with expectations")
    else:
        print("   (Insufficient history for analysis)")
    print("="*60 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Live Surprise Tracker")
    parser.add_argument("--country", type=str, help="Filter by country (e.g. US, UK)")
    parser.add_argument("--importance", type=str, help="Filter by importance (low, medium, high)")
    parser.add_argument("--update-history", action="store_true", help="Update historical calendar before starting")
    parser.add_argument("--no-stream", action="store_true", help="Don't stream (just test analysis)")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)
    
    # Optional: Update History
    if args.update_history:
        print("Updating historical calendar (this may take a moment)...")
        fetch_economic_calendar(country=args.country, full_refresh=False)
        print("History updated.")

    if args.no_stream:
        return

    # Initialize WebSocket Client
    client = TEWebSocketClient()
    
    countries = [args.country] if args.country else None
    importance = [args.importance] if args.importance else None

    print(f"\n📡 Starting Live Surprise Tracker")
    print(f"   Countries:  {countries or 'All'}")
    print(f"   Importance: {importance or 'All'}")
    print("   Waiting for events... (Press Ctrl+C to stop)\n")

    try:
        # Start background thread
        client.start(countries=countries, importance=importance)
        
        # Main Loop
        while True:
            # Poll for events
            events = client.get_events(limit=10)
            
            for event in events:
                # We only care if we have an Actual value
                if event.get("actual") is not None:
                    analyze_surprise(event)
                else:
                    # Log other updates if verbose, or just ignore
                    pass
            
            # Sleep to avoid busy loop
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping tracker...")
    finally:
        client.stop()

if __name__ == "__main__":
    main()
