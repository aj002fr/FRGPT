"""Consumer Agent configuration."""

from pathlib import Path

# Agent metadata
AGENT_NAME = "consumer-agent"
AGENT_VERSION = "1.0"

# Workspace paths
def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME

# Output directories
OUT_DIR = "out"
LOGS_DIR = "logs"


