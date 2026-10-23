"""Isolated episode store for the V2.6 reduced C018 fade lean."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v26.c018_context import (
    C018_LABEL,
    CUTOFF_SOURCE,
    DECLUSTER_STRIDE_MIN,
    STRATEGY_STATUS,
    T3_RANGE_CUTOFF_TICKS,
    annotate_c018_flags,
    latest_c018_context,
)
from scripts.aggression_dashboard_v26.lifo_context import decluster_fire_mask

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v26_live"
STATE_PATH = STORE_DIR / "c018_episodes.json"
ET = ZoneInfo("America/New_York")


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


class C018Store:
    def __init__(self, store_dir: Path = STORE_DIR) -> None:
        self.store_dir = Path(store_dir)
        self.state_path = self.store_dir / STATE_PATH.name
        self._lock = threading.Lock()
        self.episodes = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.state_path.exists():
            return []
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except Exception:
            return []

    def _save(self) -> None:
        _atomic_json(self.state_path, self.episodes)

    def update(self, model_df: pd.DataFrame) -> dict[str, Any]:
        # The newest model row may still be forming. C018 only sees completed bars.
        completed = model_df.iloc[:-1].copy() if len(model_df) > 1 else model_df.iloc[0:0].copy()
        panel = annotate_c018_flags(completed)
        context = latest_c018_context(panel)
        with self._lock:
            existing_ids = {ep.get("episode_id") for ep in self.episodes}
            changed = False
            if not panel.empty and "c018_fire" in panel:
                fires = decluster_fire_mask(panel["c018_fire"], DECLUSTER_STRIDE_MIN)
                for ts, is_fire in fires.items():
                    if not bool(is_fire):
                        continue
                    ts_utc = _utc(ts)
                    episode_id = f"c018_{ts_utc.strftime('%Y%m%dT%H%M%S')}"
                    if episode_id in existing_ids:
                        continue
                    et_date = ts_utc.tz_convert(ET).date().isoformat()
                    same_day = [ep for ep in self.episodes if ep.get("et_date") == et_date]
                    if same_day:
                        previous = _utc(same_day[-1]["lock_ts_utc"])
                        if ts_utc - previous < pd.Timedelta(minutes=DECLUSTER_STRIDE_MIN):
                            continue
                    row = panel.loc[ts]
                    self.episodes.append(
                        {
                            "episode_id": episode_id,
                            "alert_kind": "c018",
                            "et_date": et_date,
                            "lock_ts_utc": ts_utc.isoformat(),
                            "lock_price": _finite(row.get("price")),
                            "range_30m_ticks": _finite(row.get("c018_range_30m_ticks")),
                            "up_ext_ticks": _finite(row.get("c018_up_ext_ticks")),
                            "down_ext_ticks": _finite(row.get("c018_down_ext_ticks")),
                            "move_direction": str(row.get("c018_move_direction") or ""),
                            "fade_lean": str(row.get("c018_fade_lean") or ""),
                            "phase": "LOCKED",
                            "human_correct": None,
                            "human_feedback_status": None,
                            "human_feedback_note": None,
                            "human_feedback_updated_at": None,
                            "label": C018_LABEL,
                            "strategy_status": STRATEGY_STATUS,
                            "not_offline_c018": True,
                        }
                    )
                    existing_ids.add(episode_id)
                    changed = True
                if changed:
                    self._save()
            return self._payload_unlocked(context)

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
        with self._lock:
            return self._payload_unlocked(context or latest_c018_context(pd.DataFrame()))

    def _payload_unlocked(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "version": "V2.6",
            "strategy_status": STRATEGY_STATUS,
            "label": C018_LABEL,
            "context": context,
            "recent_episodes": list(reversed(self.episodes[-20:])),
            "range_cutoff_ticks": T3_RANGE_CUTOFF_TICKS,
            "cutoff_source": CUTOFF_SOURCE,
            "decluster_stride_min": DECLUSTER_STRIDE_MIN,
            "not_offline_c018": True,
            "isolation": {"store": str(self.store_dir), "executable": False},
        }


__all__ = ["C018Store", "STATE_PATH", "STORE_DIR"]
