"""Isolated LIFO FRIENDLY episode store for Aggression V2.7.

Writes only under workspace/analysis/aggression_v27_live/. Does not touch
V1/V1.2/V2/V2.1/V2.2/V2.3 live dirs.
"""

from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v27.lifo_context import (
    DECLUSTER_STRIDE_MIN,
    HORIZON_MIN,
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    REGIME_CUTOFFS,
    STRATEGY_STATUS,
    annotate_lifo_flags,
    decluster_fire_mask,
    latest_lifo_context,
    score_forward_regime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v27_live"
STATE_PATH = STORE_DIR / "lifo_episodes.json"
LOG_PATH = STORE_DIR / "lifo_episodes.csv"


def _utc(ts: Any) -> pd.Timestamp:
    value = pd.Timestamp(ts)
    return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")


def _finite(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=_finite), encoding="utf-8")
    temp.replace(path)


class LifoStore:
    def __init__(self, store_dir: Path = STORE_DIR) -> None:
        self.store_dir = Path(store_dir)
        self.state_path = self.store_dir / STATE_PATH.name
        self.log_path = self.store_dir / LOG_PATH.name
        self._lock = threading.Lock()
        self.episodes: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.state_path.exists():
            return []
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    def _save(self) -> None:
        _atomic_json(self.state_path, self.episodes[-500:])

    def _append_log(self, episode: dict[str, Any]) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        fields = [
            "episode_id",
            "et_date",
            "lock_ts_utc",
            "lock_price",
            "residual",
            "residual_z",
            "outcome",
            "net_ticks",
            "abs_net_ticks",
            "path_eff",
            "phase",
            "label",
        ]
        write_header = not self.log_path.exists()
        with self.log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            row = {k: _finite(episode.get(k)) for k in fields}
            row["label"] = LIFO_FRIENDLY_LABEL
            writer.writerow(row)

    def update(self, model_df: pd.DataFrame) -> dict[str, Any]:
        panel = annotate_lifo_flags(model_df) if not model_df.empty else model_df
        context = latest_lifo_context(panel)
        with self._lock:
            if not panel.empty and "lifo_friendly" in panel.columns:
                existing_ids = {ep.get("episode_id") for ep in self.episodes}
                fires = decluster_fire_mask(panel["lifo_friendly"].fillna(False), DECLUSTER_STRIDE_MIN)
                for ts, is_fire in fires.items():
                    if not is_fire:
                        continue
                    ts_u = _utc(ts)
                    episode_id = f"lifo_{ts_u.strftime('%Y%m%dT%H%M%S')}"
                    if episode_id in existing_ids:
                        continue
                    et_date = str(panel.loc[ts, "et_date"])
                    same = [ep for ep in self.episodes if ep.get("et_date") == et_date]
                    if same:
                        last_lock = _utc(same[-1]["lock_ts_utc"])
                        if (ts_u - last_lock) < pd.Timedelta(minutes=DECLUSTER_STRIDE_MIN):
                            continue
                    row = panel.loc[ts]
                    self.episodes.append(
                        {
                            "episode_id": episode_id,
                            "alert_kind": "lifo",
                            "et_date": et_date,
                            "lock_ts_utc": ts_u.isoformat(),
                            "lock_price": _finite(row.get("price")),
                            "residual": _finite(row.get("residual")),
                            "residual_z": _finite(row.get("residual_z")),
                            "phase": "LOCKED",
                            "outcome": None,
                            "net_ticks": None,
                            "abs_net_ticks": None,
                            "path_eff": None,
                            "logged": False,
                            "label": LIFO_FRIENDLY_LABEL,
                        }
                    )
                    existing_ids.add(episode_id)

                prices = panel["price"] if "price" in panel else pd.Series(dtype=float)
                for episode in self.episodes:
                    if episode.get("phase") == "FINALIZED":
                        continue
                    scored = score_forward_regime(prices, _utc(episode["lock_ts_utc"]), cutoffs=REGIME_CUTOFFS)
                    if scored.get("outcome") == "INCOMPLETE":
                        episode["phase"] = "SCORING"
                        episode["score_status"] = scored
                        continue
                    episode["phase"] = "FINALIZED"
                    episode["outcome"] = scored.get("outcome")
                    episode["net_ticks"] = _finite(scored.get("net_ticks"))
                    episode["abs_net_ticks"] = _finite(scored.get("abs_net_ticks"))
                    episode["path_eff"] = _finite(scored.get("path_eff"))
                    episode["score_status"] = scored
                    if not episode.get("logged"):
                        self._append_log(episode)
                        episode["logged"] = True
                self._save()
            return self.payload(context)

    def lifo_stats(self) -> dict[str, Any]:
        finalized = [
            ep for ep in self.episodes if ep.get("phase") == "FINALIZED" and ep.get("outcome")
        ]
        n = len(finalized)
        n_active = sum(1 for ep in self.episodes if ep.get("phase") != "FINALIZED")
        if n == 0:
            return {
                "n_finalized": 0,
                "n_active": n_active,
                "range_rate": None,
                "trend_rate": None,
                "ambiguous_rate": None,
                "label": LIFO_FRIENDLY_LABEL,
            }
        outcomes = [str(ep.get("outcome")) for ep in finalized]
        n_range = sum(1 for o in outcomes if o == "RANGE")
        n_trend = sum(1 for o in outcomes if o in {"TREND_UP", "TREND_DOWN"})
        n_amb = sum(1 for o in outcomes if o == "AMBIGUOUS")
        return {
            "n_finalized": n,
            "n_active": n_active,
            "range_rate": n_range / n,
            "trend_rate": n_trend / n,
            "ambiguous_rate": n_amb / n,
            "n_range": n_range,
            "n_trend": n_trend,
            "n_ambiguous": n_amb,
            "label": LIFO_FRIENDLY_LABEL,
        }

    def payload(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = context or latest_lifo_context(pd.DataFrame())
        active = next(
            (ep for ep in reversed(self.episodes) if ep.get("phase") != "FINALIZED"),
            None,
        )
        return {
            "success": True,
            "version": "V2.7",
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
        rows = list(csv.DictReader(self.log_path.open(encoding="utf-8")))
        if scope != "today":
            return rows
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        return [r for r in rows if str(r.get("et_date") or "").startswith(today[:10])]


__all__ = ["LifoStore", "STORE_DIR"]
