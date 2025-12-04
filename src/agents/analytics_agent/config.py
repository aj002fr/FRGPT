"""Analytics Agent configuration."""

from pathlib import Path
from typing import List

# Agent metadata
AGENT_NAME = "analytics-agent"
AGENT_VERSION = "1.0"

# Workspace paths
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME


def get_market_data_db_path() -> Path:
    """Get path to market data database."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "market_data.db"


def get_economic_events_db_path() -> Path:
    """Get path to economic events database."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "economic_events.db"


def get_plots_dir() -> Path:
    """Get path to plots output directory."""
    return get_workspace_path() / "plots"


# Output directories
OUT_DIR = "out"
LOGS_DIR = "logs"
PLOTS_DIR = "plots"

# Available databases
AVAILABLE_DATABASES = {
    "market_data": {
        "description": "Market prices, bids, asks, and quantities",
        "tables": ["market_data"],
        "key_columns": ["symbol", "bid", "ask", "price", "timestamp", "file_date"],
    },
    "economic_events": {
        "description": "Economic calendar events with actual, forecast, and consensus values",
        "tables": ["economic_events"],
        "key_columns": ["event_name", "country", "actual", "consensus", "forecast", "previous", "event_date"],
    },
}

# Default statistical percentiles
DEFAULT_PERCENTILES = [5, 10, 25, 50, 75, 90, 95]

# Plot defaults
DEFAULT_PLOT_WIDTH = 800
DEFAULT_PLOT_HEIGHT = 400
DEFAULT_HISTOGRAM_BINS = 20

# Analysis types
ANALYSIS_TYPES = [
    "descriptive",      # Basic statistics
    "percentile_rank",  # Where does a value fall in distribution
    "distribution",     # Histogram and distribution analysis
    "comparison",       # Compare two datasets
    "correlation",      # Correlation between variables
    "event_impact",     # Market data around economic events
    "surprise_analysis", # Analyze actual vs consensus surprises
]

# Maximum rows to fetch for analysis
MAX_ROWS = 10000

# Validation rules
MAX_DATA_POINTS_FOR_PLOT = 5000
MIN_DATA_POINTS_FOR_STATS = 2

