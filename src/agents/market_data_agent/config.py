"""Market Data Agent configuration."""

from pathlib import Path
from typing import List

# Agent metadata
AGENT_NAME = "market-data-agent"
AGENT_VERSION = "1.0"

# Workspace paths
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME

# Output directories
OUT_DIR = "out"
LOGS_DIR = "logs"

# Default column whitelist (logical names exposed by run_query)
DEFAULT_COLUMNS = [
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

# Query templates available (TimescaleDB / PostgreSQL ohlcv_1m)
AVAILABLE_TEMPLATES = [
    "by_symbol",
    "by_date",
    "by_symbol_and_date",
    "by_filters",
    "all",
    "custom",
]

# Validation rules
MAX_ROWS = 10000  # Maximum rows per query
REQUIRED_PARAMS = {
    "by_symbol": ["symbol_pattern"],
    "by_date": ["date"],
    "by_symbol_and_date": ["symbol_pattern", "date"],
    "by_filters": [],  # validated separately to require at least one filter
    "all": [],
    "custom": ["conditions"],  # conditions required; values optional
}


