"""Configuration for Telegram Bot integration."""

import os
from pathlib import Path

# ============================================================================
# TELEGRAM BOT CONFIGURATION
# ============================================================================
# Loads from config/keys.env file or environment variables
# ============================================================================

def _load_keys_env():
    """Load keys from config/keys.env file."""
    keys_file = Path(__file__).parent.parent / "config" / "keys.env"
    if keys_file.exists():
        with open(keys_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Only set if not already in environment
                    if key not in os.environ:
                        os.environ[key] = value

# Load keys from file
_load_keys_env()

# Your Telegram bot token from @BotFather
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")


_chat_ids_raw = os.getenv("TELEGRAM_CHAT_ID", "0")
ALLOWED_CHAT_IDS = [int(cid.strip()) for cid in _chat_ids_raw.split(",") if cid.strip()]

# ============================================================================
# BOT BEHAVIOR SETTINGS
# ============================================================================

# Maximum message length for Telegram (will split longer messages)
MAX_MESSAGE_LENGTH = 4096

# Timeout for orchestrator execution (seconds)
ORCHESTRATOR_TIMEOUT = 300  # 5 minutes

# Whether to send plots as images
SEND_PLOTS = True

# Whether to send the full JSON output file
SEND_JSON_OUTPUT = False

# Directory where plots are generated (charts from analytics)
PLOTS_DIR = "workspace/plots"

