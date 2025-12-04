"""
Event filtering logic for Trading Economics data.
Shared between the server (ingestion) and agent (querying).
"""

from typing import List, Dict, Optional

# =============================================================================
# EVENT FILTERING CONFIGURATION
# =============================================================================

# Countries/regions we care about
MAJOR_COUNTRIES_US = ["US", "united states"]
MAJOR_COUNTRIES_INTL = ["DE", "GB", "FR", "EA", "EU", "ES", "IT", "CN", "JP",
                        "germany", "united kingdom", "france", "euro area", 
                        "spain", "italy", "china", "japan"]
ALL_TRACKED_COUNTRIES = MAJOR_COUNTRIES_US + MAJOR_COUNTRIES_INTL

# -----------------------------------------------------------------------------
# US EVENTS TO EXCLUDE (filter out these noise events)
# -----------------------------------------------------------------------------
EXCLUDED_EVENT_KEYWORDS_US = [
    "oil",
    "api crude oil stock",
    "crude oil",
    "eia",
    "baker hughes",
    "mba",  # MBA Mortgage Applications
    "mortgage application",
    "quarterly grain stock",
    "grain stock",
    "mortgage rate",
]

# -----------------------------------------------------------------------------
# EVENTS TO CONSOLIDATE (group into one-liners)
# These related events should be grouped together in output
# -----------------------------------------------------------------------------
CONSOLIDATE_EVENTS = {
    "inflation_prices": ["ppi", "cpi", "pce", "core ppi", "core cpi", "core pce"],
    "consumer_sentiment": ["u-mich", "michigan", "consumer sentiment", "consumer confidence", "cci"],
    "employment": ["nfp", "non-farm payrolls", "adp", "jolts", "initial jobless claims", "continuing claims"],
    "pmi": ["s&p pmi", "ism pmi", "manufacturing pmi", "services pmi", "composite pmi"],
    "housing": ["housing starts", "building permits", "existing home sales", "new home sales", "pending home sales"],
}

# -----------------------------------------------------------------------------
# HIGH IMPORTANCE EVENTS - US (always highlight these)
# -----------------------------------------------------------------------------
HIGHLIGHT_EVENTS_US = [
    # Treasury Supply
    "2-year", "3-year", "5-year", "7-year", "10-year", "20-year", "30-year",
    "ust supply", "treasury auction", "bond auction",
    
    # Inflation
    "cpi", "ppi", "pce", "core cpi", "core ppi", "core pce",
    "inflation rate",
    
    # Employment
    "non-farm payrolls", "nfp", "adp employment", "jolts", 
    "initial jobless claims", "continuing claims",
    
    # Fed
    "fomc", "fed interest rate", "federal funds rate",
    "fed chair", "powell",
    
    # GDP & Growth
    "gdp", "gdp growth rate",
    
    # Consumer & Retail
    "retail sales", "consumer confidence", "cci",
    "u-mich", "michigan consumer", "consumer sentiment",
    
    # Regional Fed surveys
    "philly fed", "philadelphia fed", "ny empire", "empire state",
    
    # Trade
    "trade balance",
    
    # Manufacturing & Business
    "ism manufacturing", "ism services", "ism pmi",
    "s&p manufacturing pmi", "s&p services pmi",
    "durable goods", "durable goods orders",
    "housing starts",
]

# -----------------------------------------------------------------------------
# HIGH IMPORTANCE EVENTS - INTERNATIONAL (always highlight these)
# -----------------------------------------------------------------------------
HIGHLIGHT_EVENTS_INTL = [
    # Bond Supply (2yr+) for DE, GB, EU/EA, FR, JP, CN
    "bund", "gilt", "oat", "jgb", "bond auction", "supply",
    "2-year", "5-year", "10-year", "20-year", "30-year",
    
    # Interest Rate Decisions (all major central banks)
    "interest rate", "rate decision",
    "ecb", "boe", "boj", "pboc", "snb", "rba", "rbnz",
    "bank of england", "bank of japan", "european central bank",
    
    # Inflation
    "inflation rate", "cpi", "hicp",
    
    # Unemployment
    "unemployment rate", "unemployment",
    
    # Germany specific
    "zew", "ifo",
    
    # Japan specific
    "boj", "jgb purchases", "tankan",
]

# -----------------------------------------------------------------------------
# EVENTS TO INCLUDE FOR MAJOR EUROPE/ASIA
# Only pull these event types for international markets
# -----------------------------------------------------------------------------
INCLUDE_EVENTS_INTL = [
    "interest rate",
    "inflation",
    "cpi",
    "retail sales",
    "unemployment",
    "gdp",
    "pmi",
    "zew",
    "ifo",
    "trade balance",
    "supply",
    "bond auction",
    "bund",
    "gilt",
    "jgb",
]

# -----------------------------------------------------------------------------
# EARNINGS CONFIGURATION
# Only include earnings for these criteria
# -----------------------------------------------------------------------------
EARNINGS_CONFIG = {
    "min_market_cap_b": 250,  # $250B+ market cap
    
    # Always include these big tech companies
    "big_tech": [
        "MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "GOOG", 
        "META", "TSLA", "AMD", "ORCL"
    ],
    
    # Banks and financial institutions (major ones)
    "financials": [
        "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "SCHW"
    ],
    
    # Retail bellwethers
    "retail": [
        "HD", "TGT", "WMT", "COST", "LOW"
    ],
    
    # Delivery/logistics
    "logistics": [
        "FDX", "UPS"
    ],
}


# =============================================================================
# FILTER HELPER FUNCTIONS
# =============================================================================

def should_exclude_event(event_name: str, country: str) -> bool:
    """
    Check if an event should be excluded based on filters.
    """
    if not event_name:
        return False
    
    event_lower = event_name.lower()
    country_lower = (country or "").lower()
    
    # Only apply exclusions to US events
    is_us = any(c in country_lower for c in ["us", "united states"])
    
    if is_us:
        for keyword in EXCLUDED_EVENT_KEYWORDS_US:
            if keyword in event_lower:
                return True
    
    return False


def should_include_intl_event(event_name: str, country: str) -> bool:
    """
    Check if an international event should be included.
    """
    if not event_name:
        return False
    
    event_lower = event_name.lower()
    country_lower = (country or "").lower()
    
    # US events have different rules
    is_us = any(c in country_lower for c in ["us", "united states"])
    if is_us:
        return True  # US events filtered by exclusion list, not inclusion
    
    # Check if country is one we track
    is_tracked = any(c in country_lower for c in [
        "germany", "de", "united kingdom", "gb", "uk", "france", "fr",
        "euro", "ea", "eu", "spain", "es", "italy", "it", 
        "china", "cn", "japan", "jp", "south korea", "kr", "india", "in",
        "brazil", "br", "mexico", "mx", "russia", "ru", "turkey", "tr"
    ])
    
    if not is_tracked:
        return False
    
    # Check if event type is one we care about
    for keyword in INCLUDE_EVENTS_INTL:
        if keyword in event_lower:
            return True
    
    return False


def is_highlight_event(event_name: str, country: str) -> bool:
    """
    Check if an event is high importance and should be highlighted.
    """
    if not event_name:
        return False
    
    event_lower = event_name.lower()
    country_lower = (country or "").lower()
    
    is_us = any(c in country_lower for c in ["us", "united states"])
    
    highlight_list = HIGHLIGHT_EVENTS_US if is_us else HIGHLIGHT_EVENTS_INTL
    
    for keyword in highlight_list:
        if keyword in event_lower:
            return True
    
    return False


def get_consolidation_group(event_name: str) -> str:
    """
    Get the consolidation group for an event (if any).
    """
    if not event_name:
        return ""
    
    event_lower = event_name.lower()
    
    for group_name, keywords in CONSOLIDATE_EVENTS.items():
        for keyword in keywords:
            if keyword in event_lower:
                return group_name
    
    return ""


def filter_events_list(events: list, apply_exclusions: bool = True) -> list:
    """
    Apply all event filters to a list of events.
    Returns filtered list of events with highlight flags.
    """
    filtered = []
    
    for event in events:
        event_name = event.get("event_name", event.get("Event", ""))
        country = event.get("country", event.get("Country", ""))
        
        # Apply exclusions
        if apply_exclusions and should_exclude_event(event_name, country):
            continue
        
        # Check inclusion for international events
        is_us = any(c in (country or "").lower() for c in ["us", "united states"])
        if not is_us and not should_include_intl_event(event_name, country):
            continue
        
        # Add highlight flag
        event["is_highlight"] = is_highlight_event(event_name, country)
        event["consolidation_group"] = get_consolidation_group(event_name)
        
        filtered.append(event)
    
    return filtered

