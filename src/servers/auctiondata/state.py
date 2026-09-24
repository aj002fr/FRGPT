"""Persistent state for Treasury auction alert dedupe."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .schema import AUCTION_PULLER_STATE_PATH


def load_state(path: Path = AUCTION_PULLER_STATE_PATH) -> dict[str, str]:
    """Load CUSIP -> alerted_at timestamp state."""

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict) and all(isinstance(key, str) for key in data):
        return {str(key): str(value) for key, value in data.items()}
    return {}


def save_state(state: dict[str, str], path: Path = AUCTION_PULLER_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def was_alerted(cusip: str, state: dict[str, str]) -> bool:
    return bool(cusip and cusip in state)


def mark_alerted(cusip: str, state: dict[str, str]) -> None:
    if cusip:
        state[cusip] = datetime.now(timezone.utc).isoformat()

