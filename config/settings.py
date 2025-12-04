from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_PATH = PROJECT_ROOT / "workspace"

# =============================================================================
# Market Data Database Configuration
# =============================================================================
DATABASE_PATH = PROJECT_ROOT / "market_data.db"
DB_TABLE = "market_data"
PREDICTION_QUERIES_TABLE = "prediction_queries"
MAX_ROWS_PER_QUERY = 10000

# Column whitelist for market_data table
ALLOWED_COLUMNS = [
    "id",
    "symbol",
    "bid",
    "ask",
    "price",
    "bid_quantity",
    "offer_quantity",
    "timestamp",
    "file_date",
    "data_source",
    "is_valid",
    "created_at"
]

# =============================================================================
# Economic Events Database Configuration
# =============================================================================
ECONOMIC_EVENTS_DB_PATH = WORKSPACE_PATH / "economic_events.db"
ECONOMIC_EVENTS_TABLE = "economic_events"
LIVE_EVENT_STREAM_TABLE = "live_event_stream"
EVENT_DICTIONARY_PATH = PROJECT_ROOT / "config" / "event_dictionary.json"

# Economic events table columns
ECONOMIC_EVENTS_COLUMNS = [
    "id",
    "event_id",
    "event_name",
    "country",
    "category",
    "importance",
    "event_date",
    "actual",
    "consensus",
    "forecast",
    "previous",
    "revised",
    "unit",
    "ticker",
    "source"
]

# EventData Puller defaults
DEFAULT_WINDOW_HOURS = 12
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_EVENT_MAX_RESULTS = 100
MAX_WINDOW_HOURS = 168  # 7 days
VALID_IMPORTANCE_LEVELS = ["low", "medium", "high"]

# =============================================================================
# Trading Lexicon Configuration
# =============================================================================
# Path to JSON trading lexicon built from exported Telegram messages
TRADING_LEXICON_PATH = WORKSPACE_PATH / "trading_lexicon.json"

# Predictive Markets Configuration
MAX_SEARCH_RESULTS = 20
DEFAULT_SEARCH_RESULTS = 10
ALLOWED_PREDICTION_DOMAINS = [
    "polymarket.com"
]

# API Keys (loaded from keys.env)
def get_api_key(key_name: str) -> str:
    """Load API key from keys.env file."""
    keys_file = PROJECT_ROOT / "config" / "keys.env"
    
    if not keys_file.exists():
        raise FileNotFoundError(f"Keys file not found: {keys_file}")
    
    with open(keys_file, 'r') as f:
        for line in f:
            if line.startswith(f'{key_name}='):
                return line.split('=', 1)[1].strip()
    
    raise ValueError(f"{key_name} not found in keys.env")

# Agent Configuration
AGENT_VERSION = "1.0"

# Logging
LOG_LEVEL = "INFO"
LOG_TO_FILE = True
LOG_TO_CONSOLE = True

AGENT_CAPABILITIES = {
    "polymarket_agent": {
        "description": "The Polymarket Agent is a specialized agent that can search for markets related to the user query from the Polymarket platform.",
        "capabilities": [
            "search for market data from the Polymarket platform",
            "get public opinion from the Polymarket platform"
        ]
    },
    "market_data_agent": {
        "description": "The Market Data Agent is a specialized agent that can search for and analyze market data from the market data database.",
        "capabilities": [
            "search for market data from the market data database",
            "analyze market data from the market data database"
        ]
    },
    "orchestrator_agent": {
        "description": "The Orchestrator Agent is a specialized agent that can orchestrate the other agents to perform the tasks.",
        "capabilities": [
            "orchestrate the other agents to perform the tasks",
            "provide insights and analysis of the tasks",
            "provide insights and analysis of the other agents"
        ]
    },
    "analytics_agent": {
        "description": "The Analytics Agent performs statistical analysis and generates visualizations for market and economic data.",
        "capabilities": [
            "compute descriptive statistics (mean, median, std dev, percentiles)",
            "calculate percentile ranks and z-scores",
            "compare distributions and compute effect sizes",
            "compute correlations between variables",
            "generate SVG plots (histograms, line charts, scatter plots, bar charts)",
            "analyze economic event surprises (actual vs consensus)",
            "analyze market prices on economic event dates"
        ]
    },
    "eventdata_puller_agent": {
        "description": "The EventData Puller Agent fetches, stores, and queries economic calendar events from TradingEconomics. It maintains a local SQLite database of historical events and supports real-time WebSocket streaming.",
        "capabilities": [
            "update_calendar: Fetch/update economic calendar from TradingEconomics API",
            "query_event: Query event history by event ID or name with filters",
            "find_correlations: Find correlated events within a time window",
            "search_events: Search for events by keyword, category, or country",
            "stream_start: Start real-time WebSocket stream for live events",
            "stream_stop: Stop active WebSocket stream",
            "stream_status: Get current streaming status"
        ]
    }
}
    