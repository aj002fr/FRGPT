"""
Trading Economics tool schema and constants.

Defines API endpoints (REST + WebSocket), database schema, event categories,
importance levels, country codes, and helper functions for formatting and
validating Trading Economics API responses.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# API Configuration
# =============================================================================

# REST API Endpoints
TE_API_BASE_URL = "https://api.tradingeconomics.com"
TE_CALENDAR_ENDPOINT = "/calendar"
TE_CALENDAR_COUNTRY_ENDPOINT = "/calendar/country/{country}"
TE_CALENDAR_INDICATOR_ENDPOINT = "/calendar/indicator/{indicator}"
TE_HISTORICAL_ENDPOINT = "/historical/{country}/{indicator}"

# WebSocket Streaming Endpoint
TE_WEBSOCKET_URL = "wss://stream.tradingeconomics.com/"

# API Response formats
TE_FORMAT_JSON = "json"
TE_FORMAT_CSV = "csv"

# =============================================================================
# Database Configuration
# =============================================================================

# Database path (relative to workspace)
def get_db_path() -> Path:
    """Get path to economic events database."""
    project_root = Path(__file__).parent.parent.parent.parent
    workspace = project_root / "workspace"
    return workspace / "economic_events.db"

# Table names
ECONOMIC_EVENTS_TABLE = "economic_events"
LIVE_EVENT_STREAM_TABLE = "live_event_stream"

# =============================================================================
# Event Categories
# =============================================================================

EVENT_CATEGORIES = [
    "GDP",
    "Labour",
    "Prices",
    "Money",
    "Trade",
    "Government",
    "Business",
    "Consumer",
    "Housing",
    "Taxes",
    "Health",
]

# =============================================================================
# Importance Levels
# =============================================================================

IMPORTANCE_LOW = "low"
IMPORTANCE_MEDIUM = "medium"
IMPORTANCE_HIGH = "high"

IMPORTANCE_LEVELS = [IMPORTANCE_LOW, IMPORTANCE_MEDIUM, IMPORTANCE_HIGH]

# Map numeric importance to text (Trading Economics uses 1-3)
IMPORTANCE_MAP = {
    1: IMPORTANCE_LOW,
    2: IMPORTANCE_MEDIUM,
    3: IMPORTANCE_HIGH,
    "1": IMPORTANCE_LOW,
    "2": IMPORTANCE_MEDIUM,
    "3": IMPORTANCE_HIGH,
    "low": IMPORTANCE_LOW,
    "medium": IMPORTANCE_MEDIUM,
    "high": IMPORTANCE_HIGH,
}

# =============================================================================
# Country Names (Trading Economics API uses full names, lowercase)
# =============================================================================

# Map short codes to full country names for API
COUNTRY_CODE_MAP = {
    "US": "united states",
    "GB": "united kingdom",
    "UK": "united kingdom",
    "EU": "euro area",
    "DE": "germany",
    "FR": "france",
    "JP": "japan",
    "CN": "china",
    "CA": "canada",
    "AU": "australia",
    "CH": "switzerland",
    "NZ": "new zealand",
    "ES": "spain",
    "IT": "italy",
    "KR": "south korea",
    "IN": "india",
    "BR": "brazil",
    "MX": "mexico",
    "RU": "russia",
    "TR": "turkey",
}

MAJOR_COUNTRIES = list(COUNTRY_CODE_MAP.keys())


def normalize_country(country: str) -> str:
    """
    Normalize country code/name to Trading Economics API format.
    
    Args:
        country: Country code (US, GB) or name (united states)
        
    Returns:
        Full country name in lowercase for API
    """
    if not country:
        return ""
    
    # Check if it's a code
    upper = country.upper()
    if upper in COUNTRY_CODE_MAP:
        return COUNTRY_CODE_MAP[upper]
    
    # Already a full name, just lowercase
    return country.lower()

# =============================================================================
# Result Limits
# =============================================================================

MAX_CALENDAR_RESULTS = 1000
DEFAULT_CALENDAR_RESULTS = 100
DEFAULT_LOOKBACK_DAYS = 20
DEFAULT_WINDOW_HOURS = 12

# =============================================================================
# WebSocket Configuration
# =============================================================================

WS_RECONNECT_DELAY_BASE = 1  # Base delay in seconds
WS_RECONNECT_DELAY_MAX = 60  # Max delay in seconds
WS_PING_INTERVAL = 30  # Ping interval in seconds
WS_EVENT_BUFFER_SIZE = 100  # Max events to keep in memory

# =============================================================================
# Helper Functions
# =============================================================================


def format_event_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a Trading Economics API event response into standardized structure.
    
    **IMPORTANT Field Mapping:**
    Trading Economics API → Our Database
    - Actual → actual (the released value)
    - Previous → previous (prior period value)
    - Forecast → consensus (market consensus forecast)
    - TEForecast → forecast (Trading Economics' own forecast)
    - Revised → revised (revised previous value)
    
    Args:
        event: Raw event data from Trading Economics API
        
    Returns:
        Formatted event dictionary with standardized fields
    """
    # Extract and normalize fields
    event_id = event.get("CalendarId", event.get("Event", ""))
    event_name = event.get("Event", event.get("event", ""))
    country = event.get("Country", event.get("country", ""))
    category = event.get("Category", event.get("category", ""))
    
    # Parse importance
    raw_importance = event.get("Importance", event.get("importance", 2))
    importance = IMPORTANCE_MAP.get(raw_importance, IMPORTANCE_MEDIUM)
    
    # Parse date - Trading Economics uses various formats
    raw_date = event.get("Date", event.get("date", ""))
    event_date = _parse_event_date(raw_date)
    
    # Parse numeric values (can be None or various formats)
    # TE API fields:
    #   - Actual: Actual value released
    #   - Previous: Previous period's value
    #   - Forecast: Market consensus forecast
    #   - TEForecast: Trading Economics' own forecast
    #   - Revised: Revised previous value
    actual = _parse_numeric(event.get("Actual", event.get("actual")))
    consensus = _parse_numeric(event.get("Forecast", event.get("forecast")))  # Market consensus
    forecast = _parse_numeric(event.get("TEForecast", event.get("teforecast")))  # TE's forecast
    previous = _parse_numeric(event.get("Previous", event.get("previous")))
    revised = _parse_numeric(event.get("Revised", event.get("revised")))
    
    # Extract unit and source
    unit = event.get("Unit", event.get("unit", ""))
    source = event.get("Source", event.get("source", "tradingeconomics"))
    
    # Extract ticker (useful for matching events)
    ticker = event.get("Ticker", event.get("ticker", ""))
    
    return {
        "event_id": str(event_id),
        "event_name": event_name,
        "country": country,
        "category": category,
        "importance": importance,
        "event_date": event_date,
        "actual": actual,
        "consensus": consensus,  # Market consensus (from "Forecast" field)
        "forecast": forecast,    # TE's forecast (from "TEForecast" field)
        "previous": previous,
        "revised": revised,
        "unit": unit,
        "source": source,
        "ticker": ticker,
    }


def _parse_event_date(raw_date: Any) -> str:
    """
    Parse event date from various formats to ISO format.
    
    Args:
        raw_date: Raw date value (string, timestamp, or datetime)
        
    Returns:
        ISO format date string (YYYY-MM-DDTHH:MM:SSZ)
    """
    if not raw_date:
        return ""
    
    if isinstance(raw_date, datetime):
        return raw_date.isoformat()
    
    if isinstance(raw_date, (int, float)):
        # Unix timestamp
        try:
            dt = datetime.fromtimestamp(raw_date, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OSError):
            return str(raw_date)
    
    # String parsing
    raw_str = str(raw_date)
    
    # Try various date formats
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(raw_str.replace("Z", ""), fmt.replace("Z", ""))
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    
    # Return original if can't parse
    return raw_str


def _parse_numeric(value: Any) -> Optional[float]:
    """
    Parse numeric value from API response.
    
    Handles various formats from Trading Economics:
    - Plain numbers: 123.45
    - With commas: 1,234.56
    - With suffixes: 4.173M (millions), 123.45K (thousands), 1.2B (billions)
    - Percentages: 2.5%
    
    Args:
        value: Raw numeric value (string, number, or None)
        
    Returns:
        Float value or None if not parseable
    """
    if value is None or value == "":
        return None
    
    if isinstance(value, (int, float)):
        return float(value)
    
    # Try parsing string
    try:
        # Remove commas and percent signs, strip whitespace
        clean_str = str(value).replace(",", "").replace("%", "").strip()
        
        if not clean_str or clean_str == "-":
            return None
        
        # Handle multiplier suffixes (K, M, B, T)
        multiplier = 1.0
        if clean_str[-1].upper() in ['K', 'M', 'B', 'T']:
            suffix = clean_str[-1].upper()
            clean_str = clean_str[:-1].strip()
            
            multipliers = {
                'K': 1_000,           # Thousand
                'M': 1_000_000,       # Million
                'B': 1_000_000_000,   # Billion
                'T': 1_000_000_000_000  # Trillion
            }
            multiplier = multipliers.get(suffix, 1.0)
        
        # Parse the number
        base_value = float(clean_str)
        return base_value * multiplier
        
    except (ValueError, TypeError):
        pass
    
    return None


def validate_event_data(event: Dict[str, Any]) -> bool:
    """
    Validate that event data has required fields.
    
    Args:
        event: Event dictionary to validate
        
    Returns:
        True if valid, False otherwise
    """
    required_fields = ["event_id", "event_name", "country", "event_date"]
    
    for field in required_fields:
        if not event.get(field):
            return False
    
    return True


def normalize_importance(importance: Any) -> str:
    """
    Normalize importance value to standard format.
    
    Args:
        importance: Raw importance value
        
    Returns:
        Normalized importance string (low/medium/high)
    """
    return IMPORTANCE_MAP.get(importance, IMPORTANCE_MEDIUM)


def calculate_event_window(
    event_date: str,
    window_hours: int = DEFAULT_WINDOW_HOURS
) -> tuple:
    """
    Calculate time window around an event for correlation queries.
    
    Args:
        event_date: ISO format event date
        window_hours: Hours before and after event
        
    Returns:
        Tuple of (start_datetime, end_datetime) as ISO strings
    """
    from datetime import timedelta
    
    try:
        # Parse the event date
        if "T" in event_date:
            dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(event_date)
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Calculate window
        delta = timedelta(hours=window_hours)
        start_dt = dt - delta
        end_dt = dt + delta
        
        return (start_dt.isoformat(), end_dt.isoformat())
        
    except (ValueError, TypeError):
        return ("", "")


def build_api_url(
    endpoint: str,
    api_key: str,
    params: Optional[Dict[str, Any]] = None,
    format_type: str = TE_FORMAT_JSON
) -> str:
    """
    Build Trading Economics API URL with authentication.
    
    Args:
        endpoint: API endpoint path
        api_key: Trading Economics API key
        params: Optional query parameters
        format_type: Response format (json or csv)
        
    Returns:
        Full API URL with query parameters
    """
    import urllib.parse
    
    # URL-encode the endpoint path (handles spaces in country/indicator names)
    parts = endpoint.split("/")
    encoded_parts = [urllib.parse.quote(part, safe="") for part in parts]
    encoded_endpoint = "/".join(encoded_parts)
    
    # Build base URL
    url = f"{TE_API_BASE_URL}{encoded_endpoint}"
    
    # Build query parameters
    query_params = {"c": api_key, "f": format_type}
    if params:
        query_params.update(params)
    
    # Encode and append
    query_string = urllib.parse.urlencode(query_params)
    return f"{url}?{query_string}"


# =============================================================================
# Database Schema SQL
# =============================================================================

CREATE_ECONOMIC_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS economic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT,
    importance TEXT,
    event_date TEXT NOT NULL,
    actual REAL,
    consensus REAL,
    forecast REAL,
    previous REAL,
    revised REAL,
    unit TEXT,
    ticker TEXT,
    source TEXT DEFAULT 'tradingeconomics',
    UNIQUE(event_id, event_date)
);
"""

CREATE_ECONOMIC_EVENTS_INDICES = """
CREATE INDEX IF NOT EXISTS idx_event_id ON economic_events(event_id);
CREATE INDEX IF NOT EXISTS idx_event_date ON economic_events(event_date);
CREATE INDEX IF NOT EXISTS idx_country ON economic_events(country);
CREATE INDEX IF NOT EXISTS idx_importance ON economic_events(importance);
CREATE INDEX IF NOT EXISTS idx_event_name ON economic_events(event_name);
CREATE INDEX IF NOT EXISTS idx_ticker ON economic_events(ticker);
"""

CREATE_LIVE_EVENT_STREAM_TABLE = """
CREATE TABLE IF NOT EXISTS live_event_stream (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT,
    importance TEXT,
    event_date TEXT NOT NULL,
    actual REAL,
    consensus REAL,
    forecast REAL,
    previous REAL,
    unit TEXT,
    ticker TEXT,
    received_at TEXT NOT NULL,
    source TEXT DEFAULT 'websocket',
    UNIQUE(event_id, event_date, received_at)
);
"""

CREATE_LIVE_EVENT_STREAM_INDICES = """
CREATE INDEX IF NOT EXISTS idx_live_event_id ON live_event_stream(event_id);
CREATE INDEX IF NOT EXISTS idx_live_received_at ON live_event_stream(received_at);
CREATE INDEX IF NOT EXISTS idx_live_event_date ON live_event_stream(event_date);
"""

