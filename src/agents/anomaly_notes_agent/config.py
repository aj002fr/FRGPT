"""Anomaly Notes Agent configuration."""

from pathlib import Path
from typing import List

# Agent metadata
AGENT_NAME = "anomaly-notes-agent"
AGENT_VERSION = "1.0"

# Workspace paths
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME


# Output directories
OUT_DIR = "out"
LOGS_DIR = "logs"

# Detection parameters
DEFAULT_Z_THRESHOLD = 1.5
ROLLING_WINDOW_DAYS = 20
MIN_DATA_POINTS = 10

# Available actions
AVAILABLE_ACTIONS = [
    "annotate_date_range",  # Full pipeline: detect -> match events -> search -> store
    "detect_anomalies",     # Just detection, no storage
    "query_notes",          # Retrieve existing notes
    "explain_move",         # Get context for a specific day
]

# Action parameter requirements
REQUIRED_PARAMS = {
    "annotate_date_range": ["start_date", "end_date"],
    "detect_anomalies": ["start_date", "end_date"],
    "query_notes": [],  # All optional
    "explain_move": ["date"],
}

# Default symbol
DEFAULT_SYMBOL = "TY"

# Perplexity settings
PERPLEXITY_MODEL = "llama-3.1-sonar-small-128k-online"
PERPLEXITY_MAX_TOKENS = 250
PERPLEXITY_TIMEOUT = 30






