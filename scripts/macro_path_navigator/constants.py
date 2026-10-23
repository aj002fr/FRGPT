from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = PROJECT_ROOT / "workspace"
ECONOMIC_EVENTS_DB = WORKSPACE / "economic_events.db"
IRAN_HEADLINES_DB = WORKSPACE / "iran_headlines.db"
STATE_DIR = WORKSPACE / "state" / "macro_path_navigator"
ADHOC_EVENTS_PATH = STATE_DIR / "adhoc_events.json"
CONFIG_DIR = PROJECT_ROOT / "config"
FOMC_SPEAKER_LEAN_PATH = CONFIG_DIR / "fomc_speaker_lean.json"

LOCAL_STATE_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".market_data"))) / "market_data_puller"
LOCAL_STATUS_DIR = LOCAL_STATE_ROOT / "status"
LOCAL_LOG_DIR = LOCAL_STATE_ROOT / "logs"

DEFAULT_PORT = int(os.environ.get("MACRO_PATH_PORT", "5088"))
ET_TZ = "America/New_York"

