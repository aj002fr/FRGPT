import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_PATH = PROJECT_ROOT / "workspace"

# =============================================================================
# Market Data Database Configuration (PostgreSQL)
# =============================================================================
# PostgreSQL connection settings for OHLCV data
PG_HOST = os.environ.get("MDP_PG_HOST", "100.82.143.79")
PG_PORT = 5432
PG_DATABASE = "postgres"
PG_USER = "postgres"
PG_PASSWORD = "postgres123"  # Load from keys.env in production
PG_OHLCV_TABLE = "ohlcv_1m"

# Legacy SQLite path (deprecated - use PostgreSQL)
DATABASE_PATH = PROJECT_ROOT / "market_data.db"
DB_TABLE = "market_data"
PREDICTION_QUERIES_TABLE = "prediction_queries"
MAX_ROWS_PER_QUERY = 10000

# Column whitelist for ohlcv_1m table (PostgreSQL)
OHLCV_COLUMNS = [
    "ts_event",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume"
]

# =============================================================================
# Symbol Mapping Configuration (CME Globex → Bloomberg)
# =============================================================================
# Maps parquet file symbols to PostgreSQL/Bloomberg format
SYMBOL_MAPPING = {
    'ZN': 'TY',   # 10-Year Treasury Note
    'ZB': 'US',   # 30-Year Treasury Bond
    'ZT': 'TU',   # 2-Year Treasury Note
    'ZF': 'FV',   # 5-Year Treasury Note
    'ES': 'ES',   # S&P 500 E-mini (no change)
    'NQ': 'NQ',   # Nasdaq 100 E-mini (no change)
    'CL': 'CL',   # Crude Oil (no change)
    'GC': 'GC',   # Gold (no change)
    'DXY': 'DXY', # Dollar Index (no suffix needed)
    'DJI': 'INDU', # Dow Jones Industrial Average
    'GSPC': 'SPX', # S&P 500 Index
}

# Treasury symbols that use quarterly calendar-based rolls
TREASURY_SYMBOLS = {'ZN', 'ZB', 'ZT', 'ZF', 'TY', 'US', 'TU', 'FV'}

# Parquet data source path
PARQUET_DATA_PATH = r"C:\Users\aasth\DistroDashboard\Intraday_data_files_pq"


# =============================================================================
# Economic Events Database Configuration
# =============================================================================
ECONOMIC_EVENTS_DB_PATH = WORKSPACE_PATH / "economic_events.db"
ECONOMIC_EVENTS_TABLE = "economic_events"
LIVE_EVENT_STREAM_TABLE = "live_event_stream"
EVENT_DICTIONARY_PATH = PROJECT_ROOT / "config" / "event_dictionary.json"

# =============================================================================
# Redis Event Stream Service Configuration
# =============================================================================
# Upstream Excel source (ingestor input)
EVENT_UPSTREAM_EXCEL_PATH = r"\\100.95.155.81\FRGMSharedSpace\Meini\3.23 - 4.3 .xlsx"
EVENT_UPSTREAM_EXCEL_DIR = r"\\100.95.155.81\FRGMSharedSpace\Meini"
EVENT_UPSTREAM_EXCEL_DATE_PATTERN = re.compile(
    r"^\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}\s*\.xlsx$",
    re.IGNORECASE,
)


def resolve_event_upstream_excel_path() -> str:
    """
    Auto-detect the newest weekly calendar workbook in FRGMSharedSpace\\Meini.

    Match examples:
      - 2.23 -3.6.xlsx
      - 3.23 - 4.3 .xlsx
      - 12.1-12.15.xlsx

    Falls back to EVENT_UPSTREAM_EXCEL_PATH when no matching file exists.
    """
    base_dir = Path(EVENT_UPSTREAM_EXCEL_DIR)
    if base_dir.exists():
        candidates = [
            p
            for p in base_dir.glob("*.xlsx")
            if EVENT_UPSTREAM_EXCEL_DATE_PATTERN.match(p.name)
        ]
        if candidates:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            return str(newest)
    return EVENT_UPSTREAM_EXCEL_PATH

# PostgreSQL table for alert scheduling source-of-truth
SCHEDULED_EVENT_ALERTS_TABLE = "scheduled_event_alerts"
SCHEDULED_EVENT_ALERTS_ARCHIVE_TABLE = "scheduled_event_alerts_archive"

# Redis connection and stream settings
#
# Environment overrides (recommended for multi-machine shared Redis):
# - REDIS_HOST
# - REDIS_PORT
# - REDIS_DB
# - REDIS_PASSWORD (empty string treated as None)
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {name}: {raw}") from exc


def _env_optional_str(name: str, default: str | None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if raw == "":
        return None
    return raw


REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = _env_int("REDIS_PORT", 6379)
REDIS_DB = _env_int("REDIS_DB", 0)
REDIS_PASSWORD = _env_optional_str("REDIS_PASSWORD", None)
REDIS_EVENT_STREAM = "market:events"
REDIS_DEDUPE_PREFIX = "market:events:dedupe:"
REDIS_DEDUPE_TTL_SECONDS = 86400

# Publisher polling + retention
EVENT_PUBLISHER_POLL_SECONDS = 30
EVENT_RETENTION_DAYS = 14
EVENT_ROWCOUNT_WARN_THRESHOLD = 200000
EVENT_ROWCOUNT_CRITICAL_THRESHOLD = 500000

# Session names emitted into event_name column
TOKYO_OPEN_EVENT_NAME = "tokyo_open"
TOKYO_CLOSE_EVENT_NAME = "tokyo_close"
LONDON_OPEN_EVENT_NAME = "london_open"
LONDON_CLOSE_EVENT_NAME = "london_close"
US_OPEN_EVENT_NAME = "us_open"
US_CLOSE_EVENT_NAME = "us_close"

# Session local times (source-of-truth in local market timezone)
TOKYO_LOCAL_TZ = "Asia/Tokyo"
TOKYO_OPEN_LOCAL_TIME = "09:00"
TOKYO_CLOSE_LOCAL_TIME = "15:00"

LONDON_LOCAL_TZ = "Europe/London"
LONDON_OPEN_LOCAL_TIME = "08:00"
LONDON_CLOSE_LOCAL_TIME = "16:30"

US_LOCAL_TZ = "America/New_York"
US_OPEN_LOCAL_TIME = "08:00"
US_CLOSE_LOCAL_TIME = "17:00"

# Timezone Configuration
# Database stores event timestamps in US/Eastern local time (EST/EDT)
# DST is automatically handled via zoneinfo in schema.py

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
        "description": "Searches Polymarket prediction markets via Gamma API. Returns current prices, historical prices, volume, liquidity, and price trends for markets matching a query.",
        "capabilities": [
            "search prediction markets by topic/keyword",
            "get current Yes/No prices and probabilities",
            "fetch historical prices for specific dates via CLOB API",
            "calculate price changes over time periods",
            "filter by volume and market status"
        ]
    },
    "market_data_agent": {
        "description": "Queries 1-minute OHLCV futures data from PostgreSQL/TimescaleDB (`ohlcv_1m`). Supports symbol patterns (e.g., TY%, ZN%), time ranges, and price/volume filters.",
        "capabilities": [
            "query OHLCV data by symbol pattern (ILIKE)",
            "filter by date/time ranges",
            "filter by price thresholds (min/max open/high/low/close)",
            "filter by volume thresholds",
            "select specific columns (ts_event, symbol, open, high, low, close, volume)"
        ]
    },
    "eventdata_puller_agent": {
        "description": "Fetches, stores, and queries economic calendar events from TradingEconomics. Maintains SQLite database of historical events with actual/forecast/consensus values.",
        "capabilities": [
            "update_calendar: Fetch/update economic calendar from TradingEconomics API",
            "query_event: Query event history by event ID or name with date/country filters",
            "search_events: Search for events by keyword, category, importance, or country",
            "stream_start: Start real-time WebSocket stream for live event releases",
            "stream_stop: Stop active WebSocket stream",
            "stream_status: Get current streaming status and buffer contents"
        ]
    },
    "analytics_agent": {
        "description": "Performs statistical analysis on market and economic data.",
        "capabilities": [
            "compute descriptive statistics (mean, median, std dev, percentiles)",
            "calculate percentile ranks and z-scores for values",
            "compare two distributions (t-test, effect size)",
            "compute Pearson/Spearman correlations",
            "generate SVG plots (histograms, line charts, scatter plots, bar charts)",
            "analyze economic event surprises (actual - consensus)",
            "analyze market prices on economic event dates",
            "compute_returns_distribution: Session-based returns with percentile ranks and z-scores",
            "compute_volatility_distribution: Session-based (High-Low) volatility analysis",
            "compute_probability_matrix: Probability of price moving X bps within Y hours",
            "compute_event_distribution: Price movements around economic events (NFP, CPI, FOMC)"
        ]
    },

    "runner_agent": {
        "description": "Final consolidation agent using GPT-class models. Generates user-facing answers by synthesizing worker outputs and planning context.",
        "capabilities": [
            "consolidate outputs from multiple worker agents",
            "generate natural language answers from structured data",
            "integrate quantitative data with qualitative insights",
            "identify gaps or limitations in underlying data"
        ]
    },
    "orchestrator_agent": {
        "description": "Meta-agent that decomposes complex queries into subtasks, assigns them to specialized agents, and coordinates parallel execution.",
        "capabilities": [
            "decompose multi-part queries into atomic tasks (Planner Stage 1)",
            "select appropriate tools and parameters for each task (Planner Stage 2)",
            "generate async Python scripts for task execution (Coder)",
            "execute independent paths in parallel with dependency handling (Workers)",
            "consolidate results into final answer via reasoning agent (Runner)"
        ]
    },
}
