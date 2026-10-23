"""
Dashboard Agent Configuration.
"""

from pathlib import Path

AGENT_NAME = "dashboard-agent"
AGENT_VERSION = "1.0.0"

# Workspace paths
def get_workspace_path() -> Path:
    """Get workspace path for dashboard agent."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME


def get_out_path() -> Path:
    """Get output path for dashboard agent."""
    return get_workspace_path() / "out"


def get_logs_path() -> Path:
    """Get logs path for dashboard agent."""
    return get_workspace_path() / "logs"


# Default settings
DEFAULT_COLUMNS = 2
DEFAULT_LAYOUT = "grid"

# Timeout for orchestrator (seconds)
ORCHESTRATOR_TIMEOUT = 120
