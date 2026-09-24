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
# Each agent has an "enabled" flag - set to False to exclude from planning
AGENT_CAPABILITIES = {
    "market_data_agent": {
        "enabled": True,
        "keywords": [
            "sql",
            "market data",
            "database",
            "query",
            "price",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "symbol",
            "futures",
            "options",
            "treasury",
        ],
        "description": "SQL-based market data retrieval for treasury futures OHLCV (open/high/low/close/volume) from PostgreSQL. Executes parameterized queries with symbol/time/price filters.",
        "returns": "JSON with market_data table rows and metadata.",
        "class": "MarketDataAgent",
        "module": "src.agents.market_data_agent.run",
        "input_params": ["template", "params", "columns", "limit"],
        "param_descriptions": {
            "template": "Query template name (e.g., by_symbol, by_date, by_filters)",
            "params": "Template-specific parameters as dict (e.g., {symbol_pattern: 'ZN%', date: '2025-01-01', time: '12:00:00'})",
            "columns": "Optional list of columns to select (default: all OHLCV columns)",
            "limit": "Optional row limit (integer, max 10000)"
        },
        "capabilities": [
            "Run parameterised SQL templates on the market_data table",
            "Filter by symbol (including patterns) and expiry",
            "Filter by date and time windows (historical or recent)",
            "Retrieve open/high/low/close/volume fields",
            "Support intraday vs daily sampling via query templates",
            "Provide OHLCV data for treasury futures",
        ],
        "execution_mode": "worker_script",
        "reasoning_enabled": False,
    },
    "polymarket_agent": {
        "enabled": True,
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
            "predictit",
            "people thinking",
        ],
        "description": "Prediction market data from Polymarket. Searches markets by natural language query, returns current prices/probabilities, volumes, and optional historical comparisons.",
        "returns": "JSON with market details, prices, probabilities, and volumes.",
        "class": "PolymarketAgent",
        "module": "src.agents.polymarket_agent.run",
        "input_params": ["query", "session_id", "limit"],
        "param_descriptions": {
            "query": "Natural language search query (e.g., 'Bitcoin predictions', 'Will Trump win?')",
            "session_id": "Optional session identifier (auto-generated if not provided)",
            "limit": "Optional result limit (integer, default 10, max 50)"
        },
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
        "execution_mode": "worker_script",
        "reasoning_enabled": False,
    },
    "runner_agent": {
        "enabled": True,
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
        "description": "Final consolidation using AI reasoning. Combines all upstream worker outputs with the original query to generate a user-facing narrative answer.",
        "returns": "Natural language answer with structured summary.",
        "class": "RunnerAgent",
        "module": "src.agents.runner_agent.run",
        "input_params": ["query", "worker_outputs", "planning_table", "run_id"],
        "param_descriptions": {
            "query": "Original natural language user query",
            "worker_outputs": "List of task outputs from all completed worker tasks",
            "planning_table": "Optional planning metadata from orchestrator",
            "run_id": "Optional orchestration run identifier"
        },
        "capabilities": [
            "Combine outputs from multiple worker agents into a single answer",
            "Highlight key quantitative and qualitative insights",
            "Explain relationships and differences between data sources",
            "Describe limitations, gaps, and caveats in the underlying data",
        ],
        "execution_mode": "orchestrator_direct",
        "reasoning_enabled": True,
    },
    "eventdata_puller_agent": {
        "enabled": True,
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
            "events",
            "trading economics",
            "macro",
            "macroeconomic",
            "release",
            "announcement",
            "data release",
        ],
        "description": "Economic events and calendar data via Trading Economics API. Queries historical macro releases (NFP, CPI, FOMC) with optional lookback windows.",
        "returns": "JSON with event records (actual, consensus, previous, timestamps).",
        "class": "EventDataPullerAgent",
        "module": "src.agents.eventdata_puller_agent.run",
        "input_params": [
            "event_id",
            "event_name",
            "lookback_timestamp",
            "lookback_days",
            "window_hours",
            "update_calendar",
            "country",
            "importance",
        ],
        "param_descriptions": {
            "event_id": "Optional event ID/ticker from Trading Economics (e.g., 'USANFP', 'USCPIYOY')",
            "event_name": "Optional event name for search (e.g., 'Non-Farm Payrolls', 'CPI')",
            "lookback_timestamp": "Optional start date filter in ISO format (e.g., '2024-01-01')",
            "lookback_days": "Optional days to look back from today (integer, e.g., 365)",
            "update_calendar": "Optional flag to update calendar from API (boolean)",
            "country": "Optional country code filter (e.g., 'US', 'GB', 'EU')",
            "importance": "Optional importance filter ('low', 'medium', 'high')",
            "time": "Optional time filter in ISO format (e.g., '12:00:00')",
            "month": "Optional month filter (integer, e.g., 1, 2, 3, etc.)",
            "year": "Optional year filter (integer, e.g., 2024, 2025, etc.)",
        },
        "capabilities": [
            "Update historical economic calendar from Trading Economics API",
            "Query event history by event ID/code or name",
            "Filter events by lookback timestamp or days",
            "Return actual vs forecast vs previous values for events",
            "Search events by keyword, category, or importance",
            "Stream live events via WebSocket",
        ],
        "execution_mode": "worker_script",
        "reasoning_enabled": False,
    },
    "analytics_agent": {
        "enabled": True,  # Toggle to enable/disable this agent
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
            "compare distributions",
            "effect size",
            "analyze",
            "analysis",
            # DistroDashboard keywords
            "returns distribution",
            "session returns",
            "volatility distribution",
            "probability matrix",
            "trading session",
            "london session",
            "us open",
            "asia session",
            "event distribution",
            "time series",
            "timeseries",
            "time-series",
            "price movement",
            "price history",
            "price chart",
            "intraday",
            "daily chart",
            "hourly chart",
            "plot",
            "price distribution"
        ],
        "description": "Statistical analysis and visualization on provided numeric series (no DB access). Use for: charts (timeseries, line, bar, scatter, histogram, candlestick), statistics (mean, percentile, correlation, z-score), or any plot/graph request.",
        "returns": "JSON with statistics and SVG paths for generated visualizations.",
        "class": "AnalyticsAgent",
        "module": "src.agents.analytics_agent.run",
        "input_params": ["analysis_type", "params", "generate_plot"],
        "param_descriptions": {
            "analysis_type": "Type of analysis (e.g., 'descriptive', 'percentile_rank', 'correlation', 'candlestick')",
            "params": "Analysis-specific parameters as dict (varies by analysis_type)",
            "generate_plot": "Optional flag to generate visualization (boolean, default true)"
        },
        "capabilities": [
            "Compute descriptive statistics (mean, median, std dev, percentiles, skewness, kurtosis)",
            "Calculate percentile rank of a value within a distribution",
            "Compare two distributions with Cohen's d effect size",
            "Compute Pearson correlation between variables",
            "Generate histogram plots (PNG)",
            "Generate line charts (PNG)",
            "Generate scatter plots (PNG)",
            "Generate bar charts (PNG)",
            "Detect outliers using IQR or z-score methods",
            "Generate candlestick charts",
            # DistroDashboard capabilities
            "Compute session-based returns distribution (London, US Open, US Mid, US Close, Asia)",
            "Compute volatility (High-Low) distribution across trading sessions",
            "Compute probability matrices for price movements (X bps within Y hours)",
            "Compute event-specific return distributions (NFP, CPI, FOMC, etc.)",
            "Generate distribution plots with statistics, percentiles, and z-scores",
        ],
        "execution_mode": "worker_script",  # Runs in generated scripts
        "reasoning_enabled": False,         # Pure computation/visualization
    },
    "anomaly_notes_agent": {
        "enabled": True,
        "keywords": [
            "anomaly",
            "unusual",
            "unexpected",
            "move",
            "spike",
            "notes",
            "annotate",
            "explain",
            "outlier",
            "abnormal",
            "large range",
            "large move",
        ],
        "description": "Detects unusual market moves and generates contextual notes. Uses internal economic event data first, falls back to Perplexity web search for unexplained moves.",
        "returns": "JSON with anomaly notes, detection results, and attribution sources.",
        "class": "AnomalyNotesAgent",
        "module": "src.agents.anomaly_notes_agent.run",
        "input_params": ["action", "start_date", "end_date", "date", "symbol", "z_threshold"],
        "param_descriptions": {
            "action": "Action: 'annotate_date_range', 'detect_anomalies', 'query_notes', 'explain_move'",
            "start_date": "Start date for range-based actions (YYYY-MM-DD)",
            "end_date": "End date for range-based actions (YYYY-MM-DD)",
            "date": "Specific date for explain_move action (YYYY-MM-DD)",
            "symbol": "Market symbol (default: TY)",
            "z_threshold": "Z-score threshold for anomaly detection (default: 1.5)",
        },
        "capabilities": [
            "Detect days with unusual price moves (z-score > threshold)",
            "Match anomalies to scheduled economic events from database",
            "Search Perplexity for news on unexplained moves",
            "Store and retrieve market anomaly notes in PostgreSQL",
            "Explain specific day's market move with context",
        ],
        "execution_mode": "worker_script",
        "reasoning_enabled": False,
    },

}


def get_enabled_agent_capabilities() -> dict:
    """
    Get only enabled agent capabilities.
    
    Returns:
        Dictionary of enabled agents only
    """
    return {
        name: caps for name, caps in AGENT_CAPABILITIES.items()
        if caps.get("enabled", True)
    }


def get_enabled_agent_names() -> list:
    """
    Get list of enabled agent names.
    
    Returns:
        List of enabled agent names
    """
    return [
        name for name, caps in AGENT_CAPABILITIES.items()
        if caps.get("enabled", True)
    ]


def is_agent_enabled(agent_name: str) -> bool:
    """
    Check if an agent is enabled.
    
    Args:
        agent_name: Name of the agent
        
    Returns:
        True if enabled, False otherwise
    """
    caps = AGENT_CAPABILITIES.get(agent_name, {})
    return caps.get("enabled", True)

def get_planner_agent_registry() -> list:
    """
    Get planner agent registry with only enabled agents.
    
    Returns:
        List of enabled agent registry entries for planners
    """
    registry = []
    for name, caps in AGENT_CAPABILITIES.items():
        if not caps.get("enabled", True):
            continue  # Skip disabled agents
        
        # Use param_descriptions if available, otherwise fall back to param names list
        params = caps.get("param_descriptions", caps.get("input_params", []))
        
        entry = {
            "name": name,
            "description": caps.get("description", ""),
            "returns": caps.get("returns", ""),
            "capabilities": caps.get("capabilities", []),
            "parameters": params,
        }
        registry.append(entry)
    
    return registry


# Legacy: Keep PLANNER_AGENT_REGISTRY for backwards compatibility
# New code should use get_planner_agent_registry() instead
PLANNER_AGENT_REGISTRY = get_planner_agent_registry()

# Default taskmaster settings
DEFAULT_NUM_SUBTASKS = 10

# Planner 1 Glossary
# Add domain-specific or user-specific terms that the model should understand
# Format: List of tuples (term, definition)
# Example: ("2s10s", "2-year to 10-year Treasury spread")
PLANNER1_GLOSSARY = [
    # Add custom terminology here as needed
]


def get_glossary_context() -> str:
    """
    Build glossary context string for Planner 1 prompt.
    
    Returns:
        Formatted glossary section (empty string if no terms)
    """
    if not PLANNER1_GLOSSARY:
        return ""
    
    glossary_lines = []
    for term, definition in PLANNER1_GLOSSARY:
        glossary_lines.append(f"  - **{term}**: {definition}")
    
    glossary_text = "\n".join(glossary_lines)
    
    return (
        f"\n\n**Glossary (User-Specific Terminology)**:\n"
        f"{glossary_text}\n"
        f"When these terms appear in the user query, interpret them according to the definitions above."
    )

