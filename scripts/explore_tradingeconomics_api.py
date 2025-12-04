"""
Standalone Trading Economics API Explorer

A simple script to explore Trading Economics API capabilities directly,
without any framework dependencies. Just pure API calls.

Usage:
    python scripts/explore_tradingeconomics_api.py

Modify the DEMO_* constants at the top to test different scenarios.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

# =============================================================================
# Configuration - Modify these to explore different API features
# =============================================================================

# Your Trading Economics API key
# Load from config/keys.env or set directly here for testing
API_KEY = None  # Will be loaded from config/keys.env

# =============================================================================
# PRE-CONFIGURED DEMO SCENARIOS - Change ACTIVE_DEMO to switch
# =============================================================================

ACTIVE_DEMO = 1  # <-- CHANGE THIS NUMBER TO RUN DIFFERENT DEMOS (1-8)

DEMO_SCENARIOS = {
    # Demo 1: US Non-Farm Payrolls (most watched indicator)
    1: {
        "name": "US Non-Farm Payrolls",
        "country": "united states",
        "indicator": "non-farm payrolls",
        "start_date": "2025-09-01",
    },
    
    # Demo 2: US CPI/Inflation
    2: {
        "name": "US Inflation (CPI)",
        "country": "united states",
        "indicator": "inflation rate",
        "start_date": "2025-09-01",
    },
    
    # Demo 3: US GDP
    3: {
        "name": "US GDP Growth",
        "country": "united states",
        "indicator": "gdp growth rate",
        "start_date": "2025-09-01",
    },
    
    # Demo 4: FOMC Interest Rate Decision
    4: {
        "name": "Fed Interest Rate",
        "country": "united states",
        "indicator": "interest rate",
        "start_date": "2025-09-01",
    },
    
    # Demo 5: UK Economic Data
    5: {
        "name": "UK Inflation",
        "country": "united kingdom",
        "indicator": "inflation rate",
        "start_date": "2025-09-01",
    },
    
    # Demo 6: Eurozone ECB
    6: {
        "name": "ECB Interest Rate",
        "country": "euro area",
        "indicator": "interest rate",
        "start_date": "2025-09-01",
    },
    
    # Demo 7: China GDP
    7: {
        "name": "China GDP",
        "country": "china",
        "indicator": "gdp growth rate",
        "start_date": "2025-09-01",
    },
    
    # Demo 8: Japan BOJ
    8: {
        "name": "Japan Interest Rate",
        "country": "japan",
        "indicator": "interest rate",
        "start_date": "2025-09-01",
    },
}

# Load active demo settings
_active = DEMO_SCENARIOS.get(ACTIVE_DEMO, DEMO_SCENARIOS[1])
DEMO_COUNTRY = _active["country"]
DEMO_INDICATOR = _active["indicator"]
DEMO_START_DATE = _active["start_date"]
DEMO_END_DATE = None  # None = today
DEMO_IMPORTANCE = None  # 1=low, 2=medium, 3=high, None=all

print(f"[ACTIVE DEMO {ACTIVE_DEMO}] {_active['name']}")

# API endpoints
BASE_URL = "https://api.tradingeconomics.com"

# =============================================================================
# API Key Loading
# =============================================================================

def load_api_key() -> str:
    """Load API key from config/keys.env file."""
    global API_KEY
    
    if API_KEY:
        return API_KEY
    
    # Try to load from config/keys.env
    project_root = Path(__file__).parent.parent
    keys_file = project_root / "config" / "keys.env"
    
    if keys_file.exists():
        with open(keys_file, 'r') as f:
            for line in f:
                if line.startswith('TRADING_ECONOMICS_API_KEY='):
                    API_KEY = line.split('=', 1)[1].strip()
                    return API_KEY
    
    raise ValueError(
        "API key not found. Either:\n"
        "  1. Set API_KEY variable at top of this script\n"
        "  2. Add TRADING_ECONOMICS_API_KEY=your_key to config/keys.env"
    )


# =============================================================================
# API Helper Functions
# =============================================================================

def api_request(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """
    Make a request to Trading Economics API.
    
    Args:
        endpoint: API endpoint path (e.g., "/calendar")
        params: Optional query parameters
        
    Returns:
        Parsed JSON response
    """
    api_key = load_api_key()
    
    # URL-encode the endpoint path (handles spaces in country/indicator names)
    # Split by / and encode each part separately
    parts = endpoint.split("/")
    encoded_parts = [urllib.parse.quote(part, safe="") for part in parts]
    encoded_endpoint = "/".join(encoded_parts)
    
    # Build URL with params
    query_params = {"c": api_key, "f": "json"}
    if params:
        query_params.update(params)
    
    url = f"{BASE_URL}{encoded_endpoint}"
    if query_params:
        url += "?" + urllib.parse.urlencode(query_params)
    
    print(f"\n[API] GET {endpoint}")
    
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "TradingEconomicsExplorer/1.0")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
            
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP {e.code}: {e.reason}")
        try:
            error_body = e.read().decode("utf-8")
            print(f"[ERROR] Response: {error_body[:500]}")
        except:
            pass
        return None
    except urllib.error.URLError as e:
        print(f"[ERROR] URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        return None


def print_json(data: Any, max_items: int = 5):
    """Pretty print JSON data with truncation."""
    if data is None:
        print("  (no data)")
        return
    
    if isinstance(data, list):
        print(f"  ({len(data)} items)")
        for i, item in enumerate(data[:max_items]):
            print(f"\n  [{i}] {json.dumps(item, indent=4, default=str)}")
        if len(data) > max_items:
            print(f"\n  ... and {len(data) - max_items} more items")
    else:
        print(json.dumps(data, indent=2, default=str))


# =============================================================================
# API Exploration Functions
# Based on: https://docs.tradingeconomics.com/economic_calendar/snapshot/
# =============================================================================

def explore_calendar():
    """
    Explore the Economic Calendar API.
    
    Endpoints (from docs):
    - /calendar                                    -> All events
    - /calendar/country/{country}                  -> Events for country
    - /calendar/country/{country}/{start}/{end}    -> Country + date range
    - /calendar/indicator/{indicator}              -> Events for indicator
    - /calendar/indicator/{indicator}/{start}/{end} -> Indicator + date range
    - /calendar/updates                            -> Recent updates
    """
    print("\n" + "=" * 70)
    print("  1. ECONOMIC CALENDAR")
    print("  Docs: https://docs.tradingeconomics.com/economic_calendar/snapshot/")
    print("=" * 70)
    
    # Get all current calendar events
    print("\n[1.1] All Calendar Events (current)")
    print("      Endpoint: /calendar")
    data = api_request("/calendar")
    print_json(data, max_items=3)
    
    # Get calendar for specific country
    print(f"\n[1.2] Calendar for {DEMO_COUNTRY.title()}")
    print(f"      Endpoint: /calendar/country/{DEMO_COUNTRY}")
    data = api_request(f"/calendar/country/{DEMO_COUNTRY}")
    print_json(data, max_items=3)
    
    # Get calendar with date range (MUST include country)
    end_date = DEMO_END_DATE or datetime.now().strftime("%Y-%m-%d")
    print(f"\n[1.3] Calendar {DEMO_COUNTRY} from {DEMO_START_DATE} to {end_date}")
    print(f"      Endpoint: /calendar/country/{DEMO_COUNTRY}/{DEMO_START_DATE}/{end_date}")
    data = api_request(f"/calendar/country/{DEMO_COUNTRY}/{DEMO_START_DATE}/{end_date}")
    print_json(data, max_items=3)
    
    # Get specific indicator events
    print(f"\n[1.4] Indicator: {DEMO_INDICATOR}")
    print(f"      Endpoint: /calendar/indicator/{DEMO_INDICATOR}")
    data = api_request(f"/calendar/indicator/{DEMO_INDICATOR}")
    print_json(data, max_items=3)
    
    # Get calendar updates (recent changes)
    print("\n[1.5] Calendar Updates (recent changes)")
    print("      Endpoint: /calendar/updates")
    updates = api_request("/calendar/updates")
    print_json(updates, max_items=3)
    
    return data


def explore_indicators():
    """
    Explore the Indicators API.
    
    Endpoints:
    - /country/{country}/indicator/{indicator}     -> Indicator snapshot
    - /historical/country/{country}/indicator/{indicator} -> Historical data
    """
    print("\n" + "=" * 70)
    print("  2. INDICATORS")
    print("=" * 70)
    
    # Get indicator snapshot for a country
    print(f"\n[2.1] Indicator snapshot: {DEMO_INDICATOR} ({DEMO_COUNTRY})")
    print(f"      Endpoint: /country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}")
    data = api_request(f"/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}")
    print_json(data, max_items=5)
    
    # Get all indicators for a country
    print(f"\n[2.2] All indicators for {DEMO_COUNTRY.title()}")
    print(f"      Endpoint: /country/{DEMO_COUNTRY}")
    data = api_request(f"/country/{DEMO_COUNTRY}")
    print_json(data, max_items=5)
    
    return data


def explore_historical():
    """
    Explore Historical Data API.
    
    Endpoints:
    - /historical/country/{country}/indicator/{indicator}
    - /historical/country/{country}/indicator/{indicator}/{start}/{end}
    """
    print("\n" + "=" * 70)
    print("  3. HISTORICAL DATA")
    print("=" * 70)
    
    # Get all historical data for an indicator
    print(f"\n[3.1] Historical: {DEMO_INDICATOR} ({DEMO_COUNTRY})")
    print(f"      Endpoint: /historical/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}")
    data = api_request(f"/historical/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}")
    print_json(data, max_items=5)
    
    # Get historical with date range
    end_date = DEMO_END_DATE or datetime.now().strftime("%Y-%m-%d")
    print(f"\n[3.2] Historical from {DEMO_START_DATE} to {end_date}")
    print(f"      Endpoint: /historical/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}/{DEMO_START_DATE}/{end_date}")
    data = api_request(
        f"/historical/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}/{DEMO_START_DATE}/{end_date}"
    )
    print_json(data, max_items=5)
    
    return data


def explore_forecast():
    """
    Explore Forecast API.
    
    Endpoints:
    - /forecast/country/{country}
    - /forecast/country/{country}/indicator/{indicator}
    """
    print("\n" + "=" * 70)
    print("  4. FORECASTS")
    print("=" * 70)
    
    # Get all forecasts for a country
    print(f"\n[4.1] All forecasts for {DEMO_COUNTRY.title()}")
    print(f"      Endpoint: /forecast/country/{DEMO_COUNTRY}")
    data = api_request(f"/forecast/country/{DEMO_COUNTRY}")
    print_json(data, max_items=5)
    
    # Get forecast for specific indicator
    print(f"\n[4.2] Forecast: {DEMO_INDICATOR}")
    print(f"      Endpoint: /forecast/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}")
    data = api_request(f"/forecast/country/{DEMO_COUNTRY}/indicator/{DEMO_INDICATOR}")
    print_json(data, max_items=3)
    
    return data


def explore_news():
    """
    Explore News API.
    
    Endpoints:
    - /news                    -> Latest news
    - /news/country/{country}  -> News for country
    """
    print("\n" + "=" * 70)
    print("  6. NEWS")
    print("=" * 70)
    
    # Get latest news
    print("\n[6.1] Latest Economic News")
    print("      Endpoint: /news")
    data = api_request("/news")
    print_json(data, max_items=3)
    
    # Get news for country
    print(f"\n[6.2] News for {DEMO_COUNTRY.title()}")
    print(f"      Endpoint: /news/country/{DEMO_COUNTRY}")
    data = api_request(f"/news/country/{DEMO_COUNTRY}")
    print_json(data, max_items=3)
    
    return data


def explore_countries():
    """
    Explore Countries API.
    
    Endpoints:
    - /country -> List all available countries
    """
    print("\n" + "=" * 70)
    print("  7. COUNTRIES")
    print("=" * 70)
    
    # Get all countries
    print("\n[7.1] Available Countries")
    print("      Endpoint: /country")
    data = api_request("/country")
    print_json(data, max_items=10)
    
    return data


def explore_events_today(country: str = "united states"):
    """
    Get a focused view of today's important events for a specific country.
    
    Uses /calendar/country/{country}/{date}/{date} for direct date filtering.
    """
    print("\n" + "=" * 70)
    print("  8. TODAY'S IMPORTANT EVENTS")
    print("=" * 70)
    
    today = datetime.now().strftime("%Y-%m-%d")
    endpoint = f"/calendar/country/{country}/{today}/{today}"
    print(f"\n[8.0] Fetching events for {today} ({country})...")
    print(f"      Endpoint: {endpoint}")
    
    data = api_request(endpoint)
    
    if not data:
        print(f"  No events found for {country} today")
        return
    
    # Filter high importance events
    high_importance = [e for e in data if e.get("Importance", 0) >= 2]
    
    print(f"\n[8.1] All events today: {len(data)}")
    print(f"[8.2] Medium/High importance: {len(high_importance)}")
    
    if high_importance:
        # Sort by importance (high first)
        high_importance.sort(key=lambda e: e.get("Importance", 0), reverse=True)
        
        print("\n[IMPORTANT EVENTS]")
        for i, event in enumerate(high_importance[:15], 1):
            name = event.get("Event", "Unknown")
            actual = event.get("Actual") or "pending"
            forecast = event.get("Forecast") or "-"
            previous = event.get("Previous") or "-"
            importance = event.get("Importance", 0)
            imp_str = {1: "Low", 2: "Medium", 3: "High"}.get(importance, "?")
            
            print(f"\n  {i}. {name} [{imp_str}]")
            print(f"     Actual: {actual}  Forecast: {forecast}  Previous: {previous}")
    
    return data


def find_event_correlations(event_name: str = None, window_hours: int = 12):
    """
    Find events that occurred near a specific event.
    
    This demonstrates how to find correlated events.
    """
    event_name = event_name or DEMO_INDICATOR
    
    print("\n" + "=" * 70)
    print(f"  9. EVENT CORRELATIONS: {event_name}")
    print("=" * 70)
    
    # Get historical instances of the event (by indicator + date range)
    end_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n[9.1] Getting historical {event_name} events...")
    print(f"      Endpoint: /calendar/indicator/{event_name}/{DEMO_START_DATE}/{end_date}")
    data = api_request(f"/calendar/indicator/{event_name}/{DEMO_START_DATE}/{end_date}")
    
    if not data:
        # Try without date range
        print("      Trying without date range...")
        print(f"      Endpoint: /calendar/indicator/{event_name}")
        data = api_request(f"/calendar/indicator/{event_name}")
    
    if not data:
        print(f"  No historical data found for {event_name}")
        return
    
    print(f"      Found {len(data)} historical instances")
    
    # For the most recent instance, find nearby events
    if data:
        recent = data[0]
        event_date = recent.get("Date", "")
        event_country = recent.get("Country", "united states")
        
        if event_date:
            # Parse date
            try:
                dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
                start = (dt - timedelta(hours=window_hours)).strftime("%Y-%m-%d")
                end = (dt + timedelta(hours=window_hours)).strftime("%Y-%m-%d")
                
                print(f"\n[9.2] Events within +/-{window_hours} hours of {event_date[:10]}")
                # Need to query by country to get date range
                print(f"      Endpoint: /calendar/country/{event_country}/{start}/{end}")
                nearby = api_request(f"/calendar/country/{event_country}/{start}/{end}")
                
                if nearby:
                    # Filter to different events
                    other_events = [
                        e for e in nearby 
                        if e.get("Event", "").lower() != event_name.lower()
                        and e.get("Importance", 0) >= 2
                    ]
                    
                    print(f"      Found {len(other_events)} other important events")
                    for e in other_events[:5]:
                        print(f"      - {e.get('Event')} ({e.get('Country')})")
                        
            except Exception as e:
                print(f"  Error parsing date: {e}")
    
    return data


# =============================================================================
# QUICK DEBUG QUERIES - Call these directly for fast testing
# =============================================================================

def debug_nfp():
    """Quick query: US Non-Farm Payrolls (last 12 months).
    
    Endpoint: /calendar/indicator/{indicator}/{start}/{end}
    Docs: https://docs.tradingeconomics.com/economic_calendar/snapshot/
    """
    load_api_key()
    print("\n[DEBUG] US Non-Farm Payrolls - Last 12 months")
    print("        Endpoint: /calendar/indicator/non-farm payrolls/2024-01-01/2025-12-31")
    data = api_request("/calendar/indicator/non-farm payrolls/2024-01-01/2025-12-31")
    if data:
        # Filter to US only
        us_nfp = [e for e in data if "united states" in e.get("Country", "").lower()]
        print(f"\nFound {len(us_nfp)} US NFP releases:")
        for e in us_nfp[:12]:
            date = e.get("Date", "")[:10]
            actual = e.get("Actual", "?")
            forecast = e.get("Forecast", "?")
            prev = e.get("Previous", "?")
            print(f"  {date}: Actual={actual}  Forecast={forecast}  Previous={prev}")
    return data


def debug_cpi():
    """Quick query: US CPI Inflation (last 12 months).
    
    Endpoint: /calendar/country/{country}/indicator/{indicator}/{start}/{end}
    """
    load_api_key()
    print("\n[DEBUG] US CPI Inflation - Last 12 months")
    print("        Endpoint: /calendar/country/united states/indicator/inflation rate/2024-01-01/2025-12-31")
    data = api_request("/calendar/country/united states/indicator/inflation rate/2024-01-01/2025-12-31")
    if data:
        print(f"\nFound {len(data)} US CPI releases:")
        for e in data[:12]:
            date = e.get("Date", "")[:10]
            actual = e.get("Actual", "?")
            forecast = e.get("Forecast", "?")
            prev = e.get("Previous", "?")
            print(f"  {date}: Actual={actual}  Forecast={forecast}  Previous={prev}")
    return data


def debug_fomc():
    """Quick query: FOMC Interest Rate Decisions.
    
    Endpoint: /calendar/country/{country}/indicator/{indicator}/{start}/{end}
    """
    load_api_key()
    print("\n[DEBUG] FOMC Interest Rate Decisions")
    print("        Endpoint: /calendar/country/united states/indicator/interest rate/2025-09-01/2025-12-31")
    data = api_request("/calendar/country/united states/indicator/interest rate/2025-09-01/2025-12-31")
    if data:
        print(f"\nFound {len(data)} FOMC decisions:")
        for e in data[:10]:
            date = e.get("Date", "")[:10]
            actual = e.get("Actual", "?")
            prev = e.get("Previous", "?")
            event = e.get("Event", "")
            print(f"  {date}: {event} = {actual}  (Previous={prev})")
    return data


def debug_today(country: str = "united states"):
    """Quick query: Today's high-importance events for a specific country.
    
    Uses /calendar/country/{country}/{date}/{date} for direct date filtering.
    """
    load_api_key()
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    endpoint = f"/calendar/country/{country}/{today}/{tomorrow}"
    print(f"\n[DEBUG] Events for TODAY ONLY: {today} ({country})")
    print(f"        Endpoint: {endpoint}")
    
    data = api_request(endpoint)
    
    if data:
        # Filter by importance
        important = [e for e in data if e.get("Importance", 0) >= 2]
        
        print(f"\nTotal events today: {len(data)}")
        print(f"Important events (medium/high): {len(important)}")
        
        # Sort by importance (high first)
        important.sort(key=lambda e: e.get("Importance", 0), reverse=True)
        
        for e in important[:20]:
            name = e.get("Event", "?")
            actual = e.get("Actual") or "pending"
            forecast = e.get("Forecast") or "-"
            previous = e.get("Previous") or "-"
            imp = {1: "Low", 2: "Med", 3: "HIGH"}.get(e.get("Importance", 0), "?")
            print(f"\n  [{imp}] {name}")
            print(f"         Actual={actual}  Forecast={forecast}  Previous={previous}")
        
        return data
    else:
        print(f"\nNo events found for {country} today")
        return []


def debug_this_week():
    """Quick query: This week's high-importance events.
    
    Endpoint: /calendar/country/{country}/{start}/{end}
    Note: Must specify country for date range queries.
    """
    load_api_key()
    from datetime import datetime, timedelta
    today = datetime.now()
    # Get Monday of this week
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=6)  # Include weekend
    start = monday.strftime("%Y-%m-%d")
    end = friday.strftime("%Y-%m-%d")
    
    print(f"\n[DEBUG] High-Importance Events This Week ({start} to {end})")
    
    # Query major countries
    all_events = []
    for country in ["united states", "euro area", "united kingdom", "japan", "china"]:
        print(f"        Fetching: /calendar/country/{country}/{start}/{end}")
        data = api_request(f"/calendar/country/{country}/{start}/{end}")
        if data:
            all_events.extend(data)
    
    if all_events:
        important = [e for e in all_events if e.get("Importance", 0) >= 2]
        # Group by date
        by_date = {}
        for e in important:
            date = e.get("Date", "")[:10]
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(e)
        
        print(f"\nFound {len(important)} important events across major economies:")
        for date in sorted(by_date.keys()):
            print(f"\n  {date}:")
            for e in by_date[date][:5]:
                name = e.get("Event", "?")
                country = e.get("Country", "?")
                print(f"    - {country}: {name}")
    return all_events


def debug_correlations():
    """Quick query: Find events around last NFP release.
    
    Demonstrates correlation analysis.
    """
    load_api_key()
    print("\n[DEBUG] Events Around Last NFP Release (+/- 24 hours)")
    print("        Endpoint: /calendar/indicator/non-farm payrolls")
    
    # Get last NFP
    nfp = api_request("/calendar/indicator/non-farm payrolls")
    if nfp and len(nfp) > 0:
        # Filter to US
        us_nfp = [e for e in nfp if "united states" in e.get("Country", "").lower()]
        if us_nfp:
            last_nfp = us_nfp[0]
            nfp_date = last_nfp.get("Date", "")
            print(f"\nLast US NFP: {nfp_date[:10]}")
            print(f"  Actual: {last_nfp.get('Actual')}")
            print(f"  Forecast: {last_nfp.get('Forecast')}")
            print(f"  Previous: {last_nfp.get('Previous')}")
            
            # Get events around that date (US only)
            try:
                from datetime import datetime, timedelta
                dt = datetime.fromisoformat(nfp_date.replace("Z", "+00:00"))
                start = (dt - timedelta(hours=24)).strftime("%Y-%m-%d")
                end = (dt + timedelta(hours=24)).strftime("%Y-%m-%d")
                
                print(f"\n        Endpoint: /calendar/country/united states/{start}/{end}")
                nearby = api_request(f"/calendar/country/united states/{start}/{end}")
                if nearby:
                    other = [e for e in nearby if "non-farm" not in e.get("Event", "").lower()]
                    important_other = [e for e in other if e.get("Importance", 0) >= 2]
                    print(f"\nOther important events within 24 hours: {len(important_other)}")
                    for e in important_other[:10]:
                        name = e.get("Event", "?")
                        imp = {1: "Low", 2: "Med", 3: "HIGH"}.get(e.get("Importance", 0), "?")
                        print(f"  [{imp}] {name}")
            except Exception as ex:
                print(f"  Error: {ex}")
    return nfp


def debug_custom(country: str = "united states", indicator: str = "gdp growth rate"):
    """Quick query: Custom country/indicator combo."""
    load_api_key()
    print(f"\n[DEBUG] Custom Query: {indicator} ({country})")
    data = api_request(f"/calendar/country/{country}/indicator/{indicator}/2023-01-01/2025-12-31")
    if data:
        print(f"\nFound {len(data)} releases:")
        for e in data[:10]:
            date = e.get("Date", "")[:10]
            actual = e.get("Actual", "?")
            forecast = e.get("Forecast", "?")
            print(f"  {date}: Actual={actual}  Forecast={forecast}")
    return data


# =============================================================================
# Interactive Demo
# =============================================================================

def run_full_demo():
    """Run through all API exploration functions."""
    print("\n" + "#" * 70)
    print("#  TRADING ECONOMICS API EXPLORER")
    print("#  " + "-" * 66)
    print(f"#  Country: {DEMO_COUNTRY}")
    print(f"#  Indicator: {DEMO_INDICATOR}")
    print(f"#  Date Range: {DEMO_START_DATE} to {DEMO_END_DATE or 'today'}")
    print("#" * 70)
    
    try:
        # Test API key first
        load_api_key()
        print("\n[OK] API key loaded successfully")
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        return
    
    # Run explorations
    explore_calendar()
    explore_indicators()
    explore_historical()
    explore_forecast()
    explore_news()
    explore_countries()
    explore_events_today()
    find_event_correlations()
    
    print("\n" + "=" * 70)
    print("  DEMO COMPLETE")
    print("=" * 70)
    print("\nModify DEMO_* constants at top of script to explore different data.")
    print("Or call individual functions like explore_calendar() in Python.")


def run_quick_test():
    """Quick test to verify API connectivity."""
    print("\n[QUICK TEST] Verifying API connectivity...")
    
    try:
        load_api_key()
        print("[OK] API key loaded")
    except ValueError as e:
        print(f"[ERROR] {e}")
        return False
    
    # Try a simple request
    data = api_request("/calendar")
    
    if data:
        print(f"[OK] API returned {len(data)} calendar events")
        return True
    else:
        print("[ERROR] API request failed")
        return False


# =============================================================================
# Main Entry Point
# =============================================================================

# Set this to the debug function you want to run when pressing F5
# Options: "full", "nfp", "cpi", "fomc", "today", "week", "correlations"
DEFAULT_DEBUG_MODE = "today"  # <-- CHANGE THIS FOR QUICK DEBUG


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        
        if cmd == "test":
            run_quick_test()
        elif cmd == "calendar":
            load_api_key()
            explore_calendar()
        elif cmd == "indicators":
            load_api_key()
            explore_indicators()
        elif cmd == "historical":
            load_api_key()
            explore_historical()
        elif cmd == "forecast":
            load_api_key()
            explore_forecast()
        elif cmd == "news":
            load_api_key()
            explore_news()
        elif cmd == "countries":
            load_api_key()
            explore_countries()
        elif cmd == "today":
            load_api_key()
            explore_events_today()
        elif cmd == "correlations":
            load_api_key()
            find_event_correlations()
        # Quick debug commands
        elif cmd == "nfp":
            debug_nfp()
        elif cmd == "cpi":
            debug_cpi()
        elif cmd == "fomc":
            debug_fomc()
        elif cmd == "week":
            debug_this_week()
        else:
            print(f"Unknown command: {cmd}")
            print("\nAvailable commands:")
            print("  test         - Quick API connectivity test")
            print("  calendar     - Explore calendar API")
            print("  indicators   - Explore indicators API")
            print("  historical   - Explore historical data API")
            print("  forecast     - Explore forecast API")
            print("  news         - Explore news API")
            print("  countries    - List available countries")
            print("  today        - Show today's important events")
            print("  correlations - Find correlated events")
            print("\nQuick debug:")
            print("  nfp          - US Non-Farm Payrolls")
            print("  cpi          - US CPI Inflation")
            print("  fomc         - FOMC Rate Decisions")
            print("  week         - This week's events")
    else:
        # Run based on DEFAULT_DEBUG_MODE
        print(f"\n[Running DEFAULT_DEBUG_MODE = '{DEFAULT_DEBUG_MODE}']")
        print("(Change DEFAULT_DEBUG_MODE at bottom of script to switch)\n")
        
        if DEFAULT_DEBUG_MODE == "full":
            run_full_demo()
        elif DEFAULT_DEBUG_MODE == "nfp":
            debug_nfp()
        elif DEFAULT_DEBUG_MODE == "cpi":
            debug_cpi()
        elif DEFAULT_DEBUG_MODE == "fomc":
            debug_fomc()
        elif DEFAULT_DEBUG_MODE == "today":
            debug_today()
        elif DEFAULT_DEBUG_MODE == "week":
            debug_this_week()
        elif DEFAULT_DEBUG_MODE == "correlations":
            debug_correlations()
        else:
            run_full_demo()

