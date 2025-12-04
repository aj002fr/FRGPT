"""EventData Puller Agent configuration."""

from pathlib import Path
from typing import List, Dict, Set

# Agent metadata
AGENT_NAME = "eventdata-puller-agent"
AGENT_VERSION = "1.0"

# Workspace paths
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME


def get_db_path() -> Path:
    """Get path to economic events database."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "economic_events.db"


# Output directories
OUT_DIR = "out"
LOGS_DIR = "logs"

# Default parameters
DEFAULT_WINDOW_HOURS = 12
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_MAX_RESULTS = 100

# Available actions
AVAILABLE_ACTIONS = [
    "update_calendar",      # Fetch/update economic calendar from API
    "query_event",          # Query event history by ID/name
    "find_correlations",    # Find correlated events within time window
    "search_events",        # Search for events by keyword/category
    "stream_start",         # Start WebSocket stream (not active by default)
    "stream_stop",          # Stop WebSocket stream
    "stream_status",        # Get stream status
    "get_live_events",      # Get buffered live events
]

# Action parameter requirements
REQUIRED_PARAMS = {
    "update_calendar": [],  # All optional
    "query_event": [],      # At least event_id or event_name required
    "find_correlations": [],  # At least target_event_id or target_event_name required
    "search_events": [],    # All optional (returns all if no filters)
    "stream_start": [],     # All optional
    "stream_stop": [],      # No params
    "stream_status": [],    # No params
    "get_live_events": [],  # All optional (limit)
}

# Importance levels for validation
VALID_IMPORTANCE_LEVELS = ["low", "medium", "high"]

# Maximum results limits
MAX_RESULTS = 1000
MAX_CORRELATION_RESULTS = 100
MAX_WINDOW_HOURS = 168  # 7 days
