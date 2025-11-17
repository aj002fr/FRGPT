"""Configuration settings for code-mode MCP system."""

from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATABASE_PATH = PROJECT_ROOT / "market_data.db"
WORKSPACE_PATH = PROJECT_ROOT / "workspace"

# Database Configuration
DB_TABLE = "market_data"
PREDICTION_QUERIES_TABLE = "prediction_queries"

# Validation Configuration  
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

# Predictive Markets Configuration
MAX_SEARCH_RESULTS = 20
DEFAULT_SEARCH_RESULTS = 10
ALLOWED_PREDICTION_DOMAINS = [
    "polymarket.com",
    "kalshi.com",
    "predictit.org"
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
        "description": "The Polymarket Agent is a specialized agent that can search for and analyze market data from the Polymarket platform.",
        "capabilities": [
            "search for market data from the Polymarket platform",
            "analyze market data from the Polymarket platform",
            "provide insights and analysis of market data from the Polymarket platform"
        ]
    },
    "market_data_agent": {
        "description": "The Market Data Agent is a specialized agent that can search for and analyze market data from the market data database.",
        "capabilities": [
            "search for market data from the market data database",
            "analyze market data from the market data database",
            "provide insights and analysis of market data from the market data database"
        ]
    }
}
    "orchestrator_agent": {
        "description": "The Orchestrator Agent is a specialized agent that can orchestrate the other agents to perform the tasks.",
        "capabilities": [
            "orchestrate the other agents to perform the tasks",
            "provide insights and analysis of the tasks",
            "provide insights and analysis of the other agents"
        ]
    }
