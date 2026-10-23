"""V2.6-isolated WATCH/DISLOCATION store."""

from __future__ import annotations

from pathlib import Path

from scripts.aggression_dashboard_v22.alerts import AlertStore as _V22AlertStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v26_live"
STORE_PATH = STORE_DIR / "alert_episodes.json"


class AlertStore(_V22AlertStore):
    """V2 alert mechanics with a V2.6-only default path."""

    def __init__(self, path: Path = STORE_PATH) -> None:
        super().__init__(path=path)


__all__ = ["AlertStore", "STORE_DIR", "STORE_PATH"]
