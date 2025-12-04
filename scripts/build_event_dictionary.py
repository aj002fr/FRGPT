"""
Build a dictionary of all available economic events from Trading Economics.

Fetches event names for all tracked countries and saves them to a JSON file.
This provides a reference for exact event naming without repeated API calls.
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.servers.tradingeconomics.filters import ALL_TRACKED_COUNTRIES
from src.servers.tradingeconomics.schema import normalize_country, build_api_url
from config.settings import get_api_key
import urllib.request


def fetch_events_for_country(api_key: str, country: str) -> list:
    """Fetch recent events for a country."""
    try:
        normalized = normalize_country(country)
        if not normalized:
            print(f"  [SKIP] Could not normalize: {country}")
            return []
        
        # Use recent date range (last 30 days + next 7 days)
        end_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        endpoint = f"/calendar/country/{normalized}/{start_date}/{end_date}"
        url = build_api_url(endpoint, api_key)
        
        print(f"  [FETCH] {normalized}... ", end="", flush=True)
        
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "MarketDataPuller/1.0")
        
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            if isinstance(data, list):
                print(f"{len(data)} events")
                return data
            else:
                print("No data")
                return []
                
    except Exception as e:
        print(f"ERROR: {e}")
        return []


def build_event_dictionary():
    """Build comprehensive event dictionary for all tracked countries."""
    print("=" * 70)
    print("Building Event Dictionary for Trading Economics")
    print("=" * 70)
    
    # Load API key
    try:
        api_key = get_api_key("TRADING_ECONOMICS_API_KEY")
        print(f"\n[OK] API key loaded")
    except Exception as e:
        print(f"\n[ERROR] Failed to load API key: {e}")
        return
    
    print(f"\n[INFO] Tracked countries: {len(ALL_TRACKED_COUNTRIES)}")
    print(f"        {', '.join(ALL_TRACKED_COUNTRIES[:10])}...")
    
    # Dictionary structure:
    # {
    #   "by_country": {
    #     "united states": {
    #       "Non-Farm Payrolls": {"category": "Labour", "importance": 3, ...},
    #       ...
    #     }
    #   },
    #   "by_event": {
    #     "Non-Farm Payrolls": ["united states"],
    #     "CPI": ["united states", "germany", "united kingdom", ...],
    #     ...
    #   }
    # }
    
    event_dict = {
        "by_country": {},
        "by_event": {},
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "countries_count": len(ALL_TRACKED_COUNTRIES),
        }
    }
    
    all_events_raw = []
    
    print("\n[STEP 1] Fetching events from API...")
    print("-" * 70)
    
    for i, country in enumerate(ALL_TRACKED_COUNTRIES, 1):
        print(f"[{i}/{len(ALL_TRACKED_COUNTRIES)}] ", end="")
        events = fetch_events_for_country(api_key, country)
        all_events_raw.extend(events)
        
        # Rate limiting
        if i < len(ALL_TRACKED_COUNTRIES):
            time.sleep(0.6)
    
    print(f"\n[INFO] Fetched {len(all_events_raw)} total event records")
    
    print("\n[STEP 2] Processing and organizing...")
    print("-" * 70)
    
    # Process events
    for event in all_events_raw:
        event_name = event.get("Event", "")
        country_raw = event.get("Country", "")
        
        if not event_name or not country_raw:
            continue
        
        # Normalize country for consistency
        country_normalized = normalize_country(country_raw)
        if not country_normalized:
            country_normalized = country_raw.lower()
        
        # Add to by_country
        if country_normalized not in event_dict["by_country"]:
            event_dict["by_country"][country_normalized] = {}
        
        if event_name not in event_dict["by_country"][country_normalized]:
            event_dict["by_country"][country_normalized][event_name] = {
                "category": event.get("Category", ""),
                "importance": event.get("Importance", 2),
                "ticker": event.get("Ticker", ""),
                "example_date": event.get("Date", "")[:10] if event.get("Date") else "",
            }
        
        # Add to by_event
        if event_name not in event_dict["by_event"]:
            event_dict["by_event"][event_name] = []
        
        if country_normalized not in event_dict["by_event"][event_name]:
            event_dict["by_event"][event_name].append(country_normalized)
    
    # Sort everything
    for country in event_dict["by_country"]:
        event_dict["by_country"][country] = dict(sorted(event_dict["by_country"][country].items()))
    
    event_dict["by_event"] = dict(sorted(event_dict["by_event"].items()))
    
    # Add counts to metadata
    event_dict["metadata"]["unique_events"] = len(event_dict["by_event"])
    event_dict["metadata"]["total_country_event_pairs"] = sum(
        len(events) for events in event_dict["by_country"].values()
    )
    
    print(f"\n[RESULTS]")
    print(f"  Countries: {len(event_dict['by_country'])}")
    print(f"  Unique events: {event_dict['metadata']['unique_events']}")
    print(f"  Total mappings: {event_dict['metadata']['total_country_event_pairs']}")
    
    # Save to file
    output_path = PROJECT_ROOT / "config" / "event_dictionary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(event_dict, f, indent=2, ensure_ascii=False)
    
    print(f"\n[SAVED] {output_path}")
    
    # Show sample
    print("\n[SAMPLE] United States events (first 10):")
    us_events = event_dict["by_country"].get("united states", {})
    for i, (event_name, details) in enumerate(list(us_events.items())[:10], 1):
        importance = {1: "Low", 2: "Med", 3: "High"}.get(details["importance"], "?")
        print(f"  {i}. {event_name}")
        print(f"      Category: {details['category']}, Importance: {importance}")
    
    print("\n[SAMPLE] Common events (appearing in 5+ countries):")
    common_events = [
        (name, countries) for name, countries in event_dict["by_event"].items()
        if len(countries) >= 5
    ]
    common_events.sort(key=lambda x: len(x[1]), reverse=True)
    
    for name, countries in common_events[:10]:
        print(f"  - {name} ({len(countries)} countries)")
    
    print("\n" + "=" * 70)
    print("[DONE] Event dictionary built successfully!")
    print("=" * 70)


if __name__ == "__main__":
    build_event_dictionary()

