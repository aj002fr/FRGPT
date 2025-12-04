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
MAX_PARALLEL_TASKS = 8
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
        "execution_mode": "worker_script",  # Runs in generated scripts
        "reasoning_enabled": False,         # Pure data fetcher
    },
    "polymarket_agent": {
        "keywords": [
            "polymarket",
            "prediction market",
            "prediction",
            "forecast",
            "kalshi",
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
            "predictit"
        ],
        "description": (
            "Predictive Markets agent for Polymarket. "
            "Searches Polymarket markets, returns current prices and volumes, "
            "and can fetch historical market data for public-opinion style analysis."
        ),
        "class": "PolymarketAgent",
        "module": "src.agents.polymarket_agent.run",
        "input_params": ["query", "session_id", "limit"],
        "capabilities": [
            "Search Polymarket markets using natural-language queries",
            "Return current market prices/probabilities and 24h/7d volume",
            "Rank markets by volume and relevance",
            "Retrieve liquidity information for discovered markets",
            "Extract entities (events, dates, topics) from user queries",
            "Fetch historical price series for selected markets (Polymarket)",
            "Compare current vs past market states for a given topic",
            "Tag results with platform source (polymarket)",
        ],
        "execution_mode": "worker_script",  # Runs in generated scripts
        "reasoning_enabled": False,         # Pure data fetcher
    },
    "runner_agent": {
        "keywords": [
            "explain",
            "summarize",
            "summary",
            "combine",
            "synthesis",
            "analysis",
            "interpretation",
            "overall answer",
            "narrative",
            "explanation",
            "consolidation",
            "conclusion",
            "final answer",
        ],
        "description": (
            "Reaoning enabled agent used for final consolidation. "
            "Takes worker outputs and the original user query and produces the final user-facing answer."
        ),
        "class": "RunnerAgent",
        "module": "src.agents.reasoning_agent.run",
        "input_params": ["query", "worker_outputs", "planning_table", "run_id"],
        "capabilities": [
            "Combine outputs from multiple worker agents into a single answer",
            "Highlight key quantitative and qualitative insights",
            "Explain relationships and differences between data sources",
            "Describe limitations, gaps, and caveats in the underlying data",
        ],
        "execution_mode": "orchestrator_direct",  # Called directly by orchestrator in Stage 5
        "reasoning_enabled": True,                # Uses AI reasoning for consolidation
    },
    "eventdata_puller_agent": {
        "keywords": [
            "economic event",
            "economic calendar",
            "economic data",
            "gdp",
            "inflation",
            "employment",
            "nonfarm",
            "non-farm",
            "payrolls",
            "cpi",
            "ppi",
            "interest rate",
            "central bank",
            "fomc",
            "ecb",
            "boe",
            "fed",
            "federal reserve",
            "correlation",
            "concurrent events",
            "trading economics",
            "macro",
            "macroeconomic",
            "release",
            "announcement",
        ],
        "description": (
            "Economic events agent using Trading Economics API. "
            "Maintains historical economic calendar and queries events and data releases "
            "with optional lookback windows and correlation analysis with other events."
        ),
        "class": "EventDataPullerAgent",
        "module": "src.agents.eventdata_puller_agent.run",
        "input_params": ["event_id", "event_name", "lookback_timestamp", "lookback_days", "window_hours", "update_calendar", "country", "importance"],
        "capabilities": [
            "Update historical economic calendar from Trading Economics API",
            "Query event history by event ID/code or name",
            "Filter events by lookback timestamp or days",
            "Find correlated events within configurable time windows (±N hours)",
            "Return actual vs forecast vs previous values for events",
            "Search events by keyword, category, or importance",
            "Stream live events via WebSocket (optional)",
        ],
        "execution_mode": "worker_script",  # Runs in generated scripts
        "reasoning_enabled": False,         # Pure data fetcher
    },
    "analytics_agent": {
        "keywords": [
            "statistics",
            "statistical",
            "mean",
            "median",
            "average",
            "std",
            "standard deviation",
            "percentile",
            "percentile rank",
            "distribution",
            "distro",
            "histogram",
            "correlation",
            "z-score",
            "zscore",
            "outlier",
            "variance",
            "skewness",
            "kurtosis",
            "plot",
            "chart",
            "visualization",
            "visualize",
            "graph",
            "scatter",
            "line chart",
            "bar chart",
            "surprise",
            "actual vs consensus",
            "actual vs forecast",
            "beat",
            "miss",
            "compare distributions",
            "effect size",
            "analyze",
            "analysis",
        ],
        "description": (
            "Analytics agent for statistical analysis and visualization. "
            "Computes descriptive statistics, percentile ranks, correlations, "
            "and generates SVG plots (histograms, line charts, scatter plots, bar charts). "
            "Can analyze economic event surprises and market data on event dates."
        ),
        "class": "AnalyticsAgent",
        "module": "src.agents.analytics_agent.run",
        "input_params": ["analysis_type", "params", "generate_plot"],
        "capabilities": [
            "Compute descriptive statistics (mean, median, std dev, percentiles, skewness, kurtosis)",
            "Calculate percentile rank of a value within a distribution",
            "Compare two distributions with Cohen's d effect size",
            "Compute Pearson correlation between variables",
            "Generate histogram plots (SVG)",
            "Generate line charts (SVG)",
            "Generate scatter plots (SVG)",
            "Generate bar charts (SVG)",
            "Analyze economic event surprises (actual - consensus) with historical percentile",
            "Analyze market prices on economic event dates",
            "Detect outliers using IQR or z-score methods",
        ],
        "execution_mode": "worker_script",  # Runs in generated scripts
        "reasoning_enabled": False,         # Pure computation/visualization
    },
}

# Default taskmaster settings
DEFAULT_NUM_SUBTASKS = 10

