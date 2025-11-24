"""Configuration for Polymarket Agent (Reasoning-Enabled)."""

from pathlib import Path

AGENT_NAME = "polymarket-agent"
AGENT_VERSION = "2.0"


# Workspace Configuration
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    workspace = project_root / "workspace" / "agents" / AGENT_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


# Directory Names
OUT_DIR = "out"
LOGS_DIR = "logs"


# Threshold below which markets are flagged as low volume
LOW_VOLUME_THRESHOLD = 1000  # USD

# Default lookback window (in days) when no explicit date is provided
DEFAULT_LOOKBACK_DAYS = 7

# Maximum number of markets to include in a single response
MAX_MARKETS_TO_RETURN = 10

# Maximum length of incoming natural language query
MAX_QUERY_LENGTH = 500

