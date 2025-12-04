"""Configuration for the standalone runner agent (final consolidator)."""

from pathlib import Path

# Agent identity
AGENT_NAME = "runner-agent"
AGENT_VERSION = "1.0"


def get_workspace_path() -> Path:
    """Get agent workspace path."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "agents" / AGENT_NAME


# Directory structure
OUT_DIR = "out"
LOGS_DIR = "logs"


def get_logs_path() -> Path:
    """Get logs directory path."""
    return get_workspace_path() / LOGS_DIR


def get_out_path() -> Path:
    """Get output directory path."""
    return get_workspace_path() / OUT_DIR


# LLM configuration
DEFAULT_MODEL = "gpt-5"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 1500


