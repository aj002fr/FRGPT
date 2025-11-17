"""Configuration for ReasoningAgent."""

from pathlib import Path

# Agent identity
AGENT_NAME = "reasoning-agent"
AGENT_VERSION = "2.0"  # Simplified: always current + historical

# Workspace paths (relative to project root)
WORKSPACE_ROOT = Path("workspace") / "agents" / AGENT_NAME
OUTPUT_DIR = WORKSPACE_ROOT / "out"
LOGS_DIR = WORKSPACE_ROOT / "logs"

# Query parsing
MAX_QUERY_LENGTH = 500

# Market filtering
LOW_VOLUME_THRESHOLD = 1000  # Flag markets with volume < $1,000
DEFAULT_LOOKBACK_DAYS = 7  # Default to 1 week ago if no date specified
MAX_MARKETS_TO_RETURN = 10  # Maximum markets in response


