"""Orchestrator Agent Configuration."""

from pathlib import Path

# Agent identity
AGENT_NAME = "orchestrator-agent"
AGENT_VERSION = "1.0"

# Workspace configuration
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME

# Directory structure
OUT_DIR = "out"
LOGS_DIR = "logs"
SCRIPTS_DIR = "generated_scripts"
DB_DIR = "database"

# Database configuration
DB_NAME = "orchestrator_results.db"

def get_db_path() -> Path:
    """Get path to orchestrator database."""
    project_root = Path(__file__).parent.parent.parent.parent
    workspace = project_root / "workspace"
    db_path = workspace / DB_NAME
    return db_path

# Execution configuration
MAX_PARALLEL_TASKS = 5
TASK_TIMEOUT_SECONDS = 300  # 5 minutes per task
TASK_EXECUTION_TIMEOUT = 300  # 5 minutes per task (alias)
SCRIPT_EXECUTION_TIMEOUT = 600  # 10 minutes total
DEPENDENCY_WAIT_TIMEOUT = 300  # 5 minutes for dependencies

# Agent capabilities registry
AGENT_CAPABILITIES = {
    "market_data_agent": {
        "keywords": [
            "sql",
            "market data",
            "database",
            "query",
            "price",
            "bid",
            "ask",
            "symbol",
            "futures",
            "options",
        ],
        "description": (
            "MarketData agent for treasury futures and options. Executes SQL "
            "queries against the market_data table to retrieve historical or "
            "live prices, volumes, and related fields."
        ),
        "class": "MarketDataAgent",
        "module": "src.agents.market_data_agent.run",
        "input_params": ["template", "params", "columns", "limit"],
        "capabilities": [
            "Run parameterised SQL templates on the market_data table",
            "Filter by symbol (including patterns) and expiry",
            "Filter by date and time windows (historical or recent)",
            "Retrieve bid/ask, last price, and volume fields",
            "Support intraday vs daily sampling via query templates",
        ],
    },
    "polymarket_agent": {
        "keywords": [
            "polymarket",
            "prediction market",
            "prediction",
            "forecast",
            "probability",
            "odds",
            "betting",
            "historical",
            "opinion",
            "comparison",
            "trend",
            "analysis",
            "sentiment",
            "change",
            "evolution",
        ],
        "description": (
            "Predictive Markets agent for Polymarket. Searches markets, "
            "returns current prices and volumes, and can fetch historical "
            "market data for public-opinion style analysis."
        ),
        "class": "PolymarketAgent",
        "module": "src.agents.polymarket_agent.run",
        "input_params": ["query", "session_id", "limit"],
        "capabilities": [
            "Search Polymarket markets using natural-language queries",
            "Return current market prices/probabilities and 24h/7d volume",
            "Retrieve liquidity information for discovered markets",
            "Rank markets by relevance and volume using LLM scoring",
            "Extract entities (events, dates, topics) from user queries",
            "Fetch historical price series for selected markets",
            "Compare current vs past market states for a given topic",
        ],
    },
}

# Default taskmaster settings
DEFAULT_NUM_SUBTASKS = 5

