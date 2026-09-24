from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "unified_dashboard_event_config.json"


def load_unified_dashboard_event_config() -> Dict[str, Any]:
    """Load the unified dashboard event config mapping from JSON."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing unified dashboard config: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)




