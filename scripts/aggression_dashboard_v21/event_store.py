"""Isolated event-flow paper episode state for Aggression V2.1."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v21_live"
STATE_PATH = STORE_DIR / "event_episodes.json"
LOG_PATH = STORE_DIR / "event_paper_log.csv"
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
HORIZONS = (5, 15, 30)
STALE_FEED_SECONDS = 30.0


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


class EventPaperStore:
    def __init__(self, store_dir: Path = STORE_DIR) -> None:
        self.store_dir = store_dir
        self.state_path = store_dir / STATE_PATH.name
        self.log_path = store_dir / LOG_PATH.name
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

    @staticmethod
    def _feed_fresh(feed_status: dict[str, Any]) -> bool:
        if not feed_status.get("connected", False):
            return False
        age = feed_status.get("last_tick_age_sec")
        return age is not None and float(age) <= STALE_FEED_SECONDS

    @staticmethod
    def _frame(features: pd.DataFrame) -> pd.DataFrame:
        if features.empty:
            return features
        out = features.copy()
        idx = pd.DatetimeIndex(out.index)
        out.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        return out.sort_index()

    @staticmethod
    def _base_episode(event: pd.Series) -> dict[str, Any]:
        event_ts = _utc(event["event_ts_utc"])
        return {
            "release_id": str(event["release_id"]),
            "event_ts_utc": event_ts.isoformat(),
            "event_ts_et": _utc(event["event_ts_et"]).tz_convert("America/New_York").isoformat(),
            "et_date": str(event["et_date"]),
            "event_name": str(event.get("event_name") or ""),
            "component_count": int(event.get("component_count") or 0),
            "phase": "COLLECTING",
            "lean": None,
            "direction_sign": None,
            "locked_ts_utc": None,
            "lock_price": None,
            "release_price": None,
            "release_to_lock_ticks": None,
            "first5_agg_sum": None,
            "first5_buckets": 0,
            "outcomes": {},
            "quality": {
                "feed_stale_at_lock": False,
                "missing_lock_price": False,
                "incomplete_first5": False,
                "zero_flow": False,
            },
            "logged": False,
        }

    def _lock_signal(
        self,
        episode: dict[str, Any],
        features: pd.DataFrame,
        feed_status: dict[str, Any],
    ) -> bool:
        release_ts = _utc(episode["event_ts_utc"])
        lock_ts = release_ts + pd.Timedelta(minutes=5)
        if not self._feed_fresh(feed_status):
            episode["quality"]["feed_stale_at_lock"] = True
            return False
        pre = features.loc[features.index <= release_ts].tail(1)
        first5 = features.loc[(features.index > release_ts) & (features.index <= lock_ts)]
        lock = features.loc[features.index <= lock_ts].tail(1)
        if pre.empty or first5.empty or lock.empty or lock.index[-1] <= release_ts:
            episode["quality"]["missing_lock_price"] = True
            return False
        flow_sum = float(pd.to_numeric(first5["agg_1m"], errors="coerce").sum())
        flow_sign = float(np.sign(flow_sum))
        if flow_sign == 0.0:
            flow_sign = float(np.sign(pd.to_numeric(first5["agg_5m"], errors="coerce").iloc[-1]))
        incomplete = len(first5) < 5
        valid_sign = flow_sign if not incomplete else 0.0
        release_price = float(pre.iloc[-1]["price"])
        lock_price = float(lock.iloc[-1]["price"])
        episode.update(
            {
                "phase": "LEAN_LOCKED" if valid_sign else "NO_SIGNAL",
                "lean": "LONG" if valid_sign > 0 else "SHORT" if valid_sign < 0 else "NO_SIGNAL",
                "direction_sign": valid_sign,
                "locked_ts_utc": lock_ts.isoformat(),
                "lock_price": lock_price,
                "release_price": release_price,
                "release_to_lock_ticks": (lock_price - release_price) / TICK_SIZE,
                "first5_agg_sum": flow_sum,
                "first5_buckets": int(len(first5)),
            }
        )
        episode["quality"]["incomplete_first5"] = bool(incomplete)
        episode["quality"]["zero_flow"] = bool(flow_sign == 0.0)
        return True

    @staticmethod
    def _score(episode: dict[str, Any], features: pd.DataFrame, horizon: int) -> bool:
        if not episode.get("locked_ts_utc") or not episode.get("direction_sign"):
            return False
        lock_ts = _utc(episode["locked_ts_utc"])
        target_ts = lock_ts + pd.Timedelta(minutes=horizon)
        path = features.loc[(features.index > lock_ts) & (features.index <= target_ts)]
        if path.empty or path.index[-1] < target_ts - pd.Timedelta(seconds=90):
            return False
        direction = float(episode["direction_sign"])
        lock_price = float(episode["lock_price"])
        end_price = float(path.iloc[-1]["price"])
        ret_ticks = (end_price - lock_price) / TICK_SIZE
        directional_path = direction * (path["price"].astype(float) - lock_price) / TICK_SIZE
        episode["outcomes"][f"{horizon}m"] = {
            "complete": True,
            "end_ts_utc": target_ts.isoformat(),
            "end_price": end_price,
            "ret_ticks": ret_ticks,
            "gross_ticks": direction * ret_ticks,
            "after_cost_ticks": direction * ret_ticks - COST_TICKS,
            "adverse_ticks": float(directional_path.min()),
        }
        return True

    def _append_log(self, episode: dict[str, Any]) -> None:
        if episode.get("logged"):
            return
        row = {key: value for key, value in episode.items() if key not in {"outcomes", "quality", "logged"}}
        for horizon in HORIZONS:
            outcome = episode.get("outcomes", {}).get(f"{horizon}m", {})
            for key in ("ret_ticks", "gross_ticks", "after_cost_ticks", "adverse_ticks"):
                row[f"{key}_{horizon}m"] = outcome.get(key)
        row["quality"] = json.dumps(episode.get("quality", {}), sort_keys=True)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([row]).to_csv(self.log_path, mode="a", header=not self.log_path.exists(), index=False)
        episode["logged"] = True

    def update(
        self,
        features: pd.DataFrame,
        schedule: pd.DataFrame,
        feed_status: dict[str, Any],
        now: pd.Timestamp | None = None,
    ) -> None:
        now_utc = _utc(now or pd.Timestamp.now(tz="UTC"))
        frame = self._frame(features)
        changed = False
        with self._lock:
            by_id = {str(ep["release_id"]): ep for ep in self.episodes}
            for _, event in schedule.iterrows():
                event_ts = _utc(event["event_ts_utc"])
                if event_ts > now_utc or event_ts < now_utc - pd.Timedelta(hours=2):
                    continue
                rid = str(event["release_id"])
                episode = by_id.get(rid)
                if episode is None:
                    episode = self._base_episode(event)
                    self.episodes.append(episode)
                    by_id[rid] = episode
                    changed = True
                lock_ts = event_ts + pd.Timedelta(minutes=5)
                if now_utc < lock_ts:
                    episode["phase"] = "COLLECTING"
                    continue
                if not episode.get("locked_ts_utc"):
                    changed = self._lock_signal(episode, frame, feed_status) or changed
                if episode.get("lean") == "NO_SIGNAL" and now_utc >= lock_ts:
                    episode["phase"] = "FINALIZED"
                    self._append_log(episode)
                    changed = True
                    continue
                if episode.get("locked_ts_utc"):
                    episode["phase"] = "SCORING"
                    for horizon in HORIZONS:
                        if f"{horizon}m" not in episode["outcomes"] and now_utc >= lock_ts + pd.Timedelta(minutes=horizon):
                            changed = self._score(episode, frame, horizon) or changed
                    if "30m" in episode["outcomes"]:
                        episode["phase"] = "FINALIZED"
                        self._append_log(episode)
                        changed = True
            if changed:
                self._save()

    def payload(
        self,
        schedule: pd.DataFrame,
        schedule_status: dict[str, Any],
        feed_status: dict[str, Any],
        now: pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        now_utc = _utc(now or pd.Timestamp.now(tz="UTC"))
        if schedule.empty or "event_ts_utc" not in schedule:
            upcoming = pd.DataFrame(
                columns=["event_ts_utc", "event_ts_et", "event_name", "component_count", "release_id", "et_date"]
            )
        else:
            upcoming = schedule[
                (schedule["event_ts_utc"] > now_utc)
                & (schedule["event_ts_utc"] <= now_utc + pd.Timedelta(hours=48))
            ].head(20)
        with self._lock:
            recent = sorted(self.episodes, key=lambda ep: ep["event_ts_utc"], reverse=True)[:50]
            active = next((ep for ep in recent if ep.get("phase") not in {"FINALIZED"}), None)
            finalized = [ep for ep in self.episodes if ep.get("phase") == "FINALIZED"]
        pnl = [
            ep.get("outcomes", {}).get("30m", {}).get("after_cost_ticks")
            for ep in finalized
            if ep.get("outcomes", {}).get("30m", {}).get("after_cost_ticks") is not None
        ]
        return {
            "success": True,
            "generated_at": now_utc.isoformat(),
            "strategy_status": "PROVISIONAL / RESEARCH-ONLY",
            "feed": feed_status,
            "events_db": schedule_status,
            "upcoming": upcoming.assign(
                event_ts_utc=upcoming["event_ts_utc"].map(lambda ts: _utc(ts).isoformat()),
                event_ts_et=upcoming["event_ts_et"].map(lambda ts: pd.Timestamp(ts).isoformat()),
            ).to_dict(orient="records"),
            "active": active,
            "recent": recent,
            "stats": {
                "finalized": len(finalized),
                "avg_after_cost_30m_ticks": float(np.mean(pnl)) if pnl else None,
                "hit_rate_after_cost_30m": float(np.mean(np.asarray(pnl) > 0)) if pnl else None,
            },
        }

    def log_rows(self, scope: str = "today") -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        data = pd.read_csv(self.log_path)
        if scope != "all" and "et_date" in data:
            today = pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d")
            data = data[data["et_date"].astype(str) == today]
        return data.replace({np.nan: None}).tail(500).to_dict(orient="records")


__all__ = ["COST_TICKS", "EventPaperStore", "HORIZONS", "STORE_DIR", "TICK_SIZE"]
