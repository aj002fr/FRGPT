"""Configuration for Polymarket Agent."""

from pathlib import Path

# Agent Identification
AGENT_NAME = "polymarket-agent"
AGENT_VERSION = "1.0"

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

# Search Defaults
DEFAULT_MAX_RESULTS = 20
MAX_RESULTS = 50

# Session ID Configuration
SESSION_ID_HASH_LENGTH = 3  # Number of random bytes for session ID hash

