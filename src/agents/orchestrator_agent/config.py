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
        "keywords": ["sql", "market data", "database", "query", "price", "bid", "ask", "symbol"],
        "description": "Execute SQL queries on market_data table",
        "class": "MarketDataAgent",
        "module": "src.agents.market_data_agent.run",
        "input_params": ["template", "params", "columns", "limit"],
        "capabilities": [
            "SQL queries on market_data table",
            "Filter by symbol patterns",
            "Filter by date",
            "Retrieve bid/ask prices"
        ]
    },
    "polymarket_agent": {
        "keywords": ["polymarket", "prediction market", "prediction", "forecast", "probability", "odds", "betting"],
        "description": "Search Polymarket prediction markets",
        "class": "PolymarketAgent",
        "module": "src.agents.polymarket_agent.run",
        "input_params": ["query", "session_id", "limit"],
        "capabilities": [
            "Search Polymarket markets",
            "Get market prices and probabilities",
            "Retrieve volume and liquidity data",
            "LLM-powered relevance scoring"
        ]
    },
    "reasoning_agent": {
        "keywords": ["historical", "opinion", "comparison", "trend", "analysis", "sentiment", "change", "evolution"],
        "description": "AI-powered market analysis with historical comparison",
        "class": "ReasoningAgent",
        "module": "src.agents.reasoning_agent.run",
        "input_params": ["query", "session_id"],
        "capabilities": [
            "Parse natural language queries",
            "Extract dates and topics",
            "Compare current vs historical market states",
            "Sort by relevance and volume",
            "Flag low volume markets"
        ]
    }
}

# Default taskmaster settings
DEFAULT_NUM_SUBTASKS = 5

