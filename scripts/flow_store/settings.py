"""Paths and constants for the canonical AccessAPI flow writer."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FLOW_TAPE_DB_PATH_ENV = "FLOW_TAPE_DB_PATH"
DEFAULT_TAPE_DB_PATH = PROJECT_ROOT / "workspace" / "analysis" / "flow_store" / "flow_tape.sqlite"

ACCESS_API_BASE = os.environ.get("ACCESS_API_BASE", "http://100.79.30.21:8001")
ACCESS_API_WS = os.environ.get("ACCESS_API_WS", "ws://100.79.30.21:8001/ws/live/stream")

SYMBOL_ROOTS = {"FV": "ZF", "TU": "ZT", "TY": "ZN", "US": "ZB"}
ROOT_TO_SYMBOL = {root: symbol for symbol, root in SYMBOL_ROOTS.items()}

SOURCE_LIVE = "accessapi_live"
SOURCE_HIST = "accessapi_hist"
SOURCE_BACKUP = "databento_backup"
BAR_SOURCE_CANONICAL = "accessapi"
BAR_SOURCE_PROVISIONAL = "databento_provisional"

MINUTE_US = 60_000_000
SEAL_DELAY_SEC = 10
BACKFILL_SETTLE_MINUTES = 2
LEASE_HEARTBEAT_SEC = 10
LEASE_STALE_SEC = 60
FLUSH_EVERY_SEC = 1.0
STATUS_EVERY_SEC = 10
BACKFILL_EVERY_SEC = 60
COVERAGE_STALE_SEC = 30
QUEUE_MAX = 200_000


def tape_db_path() -> Path:
    return Path(os.environ.get(FLOW_TAPE_DB_PATH_ENV) or DEFAULT_TAPE_DB_PATH)


def history_db_path() -> Path:
    from scripts.aggression_dashboard.flow_history import DB_PATH

    return DB_PATH
