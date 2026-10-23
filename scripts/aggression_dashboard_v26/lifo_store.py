"""V2.6-isolated LIFO episode store with human-feedback fields."""

from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.aggression_dashboard_v22.lifo_store import LifoStore as _V22LifoStore
from scripts.aggression_dashboard_v26.lifo_context import (
    HORIZON_MIN,
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    STRATEGY_STATUS,
    latest_lifo_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v26_live"
STATE_PATH = STORE_DIR / "lifo_episodes.json"
LOG_PATH = STORE_DIR / "lifo_episodes.csv"
ET = ZoneInfo("America/New_York")


class LifoStore(_V22LifoStore):
    """Keep V2.2 mechanics while owning all V2.6 state."""

    def __init__(self, store_dir: Path = STORE_DIR) -> None:
        super().__init__(store_dir=store_dir)

    def update(self, model_df: pd.DataFrame) -> dict[str, Any]:
        payload = super().update(model_df)
        changed = False
        with self._lock:
            for episode in self.episodes:
                if episode.get("strategy_status") != STRATEGY_STATUS:
                    episode["strategy_status"] = STRATEGY_STATUS
                    changed = True
                for key, value in {
                    "human_correct": None,
                    "human_feedback_status": None,
                    "human_feedback_note": None,
                    "human_feedback_updated_at": None,
                }.items():
                    if key not in episode:
                        episode[key] = value
                        changed = True
            if changed:
                self._save()
        return self.payload(payload.get("context"))

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        with self._lock:
            episode = next(
                (ep for ep in self.episodes if ep.get("episode_id") == episode_id),
                None,
            )
            return copy.deepcopy(episode) if episode is not None else None

    def set_feedback(
        self,
        episode_id: str,
        correct: bool | None,
        *,
        note: str | None,
        feedback_ts: str,
    ) -> dict[str, Any]:
        with self._lock:
            episode = next(
                (ep for ep in self.episodes if ep.get("episode_id") == episode_id),
                None,
            )
            if episode is None:
                raise KeyError(episode_id)
            episode["human_correct"] = correct
            episode["human_feedback_status"] = "graded" if correct is not None else "skipped"
            episode["human_feedback_note"] = note
            episode["human_feedback_updated_at"] = feedback_ts
            self._save()
            return copy.deepcopy(episode)

    def snapshot_episodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.episodes)

    def payload(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = context or latest_lifo_context(pd.DataFrame())
        active = next(
            (ep for ep in reversed(self.episodes) if ep.get("phase") != "FINALIZED"),
            None,
        )
        return {
            "success": True,
            "version": "V2.6",
            "strategy_status": STRATEGY_STATUS,
            "lifo_friendly_label": LIFO_FRIENDLY_LABEL,
            "non_directional": True,
            "context": ctx,
            "active_lifo_episode": active,
            "recent_episodes": list(reversed(self.episodes[-20:])),
            "lifo_stats": self.lifo_stats(),
            "lifo_friendly_research": dict(LIFO_FRIENDLY_RESEARCH),
            "isolation": {
                "store": str(self.store_dir),
                "v1_v2_v3_modified": False,
            },
            "horizon_min": HORIZON_MIN,
        }

    def read_log(self, scope: str = "today") -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        with self.log_path.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if scope != "today":
            return rows
        today_et = datetime.now(timezone.utc).astimezone(ET).date().isoformat()
        return [row for row in rows if row.get("et_date") == today_et]


__all__ = ["LifoStore", "STORE_DIR", "STATE_PATH", "LOG_PATH"]
