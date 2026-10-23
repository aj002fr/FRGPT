from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
STORE_PATH = STORE_DIR / "alert_episodes.json"
LOG_PATH = STORE_DIR / "alert_log.csv"
WATCH_Z = 1.5
DISLOCATION_Z = 2.0
RESET_Z = 1.0
HORIZON_MINUTES = 30
SCORE_HORIZONS = (15, 30)
TICK_SIZE = 1.0 / 64.0
Z_METHOD = "prior_blend_v1"
NEW_LOG_COLUMNS = [
    "z_method",
    "close_price",
    "close_fair_value",
    "mae_to_resolve_ticks",
    "price_share_of_reversion",
]


class AlertStore:
    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = path.parent / LOG_PATH.name
        self._lock = threading.Lock()
        self.episodes = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            episodes = json.loads(self.path.read_text(encoding="utf-8"))
            for episode in episodes:
                if "reversion_status" not in episode and episode.get("status") in {"resolved", "expired"}:
                    episode["reversion_status"] = episode["status"]
                    episode["status"] = "finalized"
                    episode["legacy_schema"] = True
                    episode["logged"] = True
            return episodes
        except Exception:
            return []

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.episodes, indent=2, default=str), encoding="utf-8")

    def update(self, model_df: pd.DataFrame) -> Dict[str, Any]:
        with self._lock:
            if model_df.empty or "residual_z" not in model_df:
                return self._summary_unlocked(None)
            latest = model_df.dropna(subset=["residual_z"]).tail(1)
            if latest.empty:
                return self._summary_unlocked(None)
            row = latest.iloc[0]
            ts = pd.Timestamp(row.name)
            z = float(row["residual_z"])
            residual = float(row["residual"])
            abs_z = abs(z)
            state = "DISLOCATION" if abs_z >= DISLOCATION_Z else "WATCH" if abs_z >= WATCH_Z else "NONE"

            self._evaluate_all_open(model_df, ts)
            if state != "NONE" and self._active_episode() is None:
                self.episodes.append(self._new_episode(state, ts, row))
            self._save()
            self._append_finalized_logs()
            return self._summary_unlocked(self._active_episode())

    def _new_episode(self, state: str, ts: pd.Timestamp, row: pd.Series) -> dict[str, Any]:
        residual = float(row["residual"])
        direction = "SHORT_RICH" if residual > 0 else "LONG_CHEAP"
        return {
            "id": f"{ts.isoformat()}_{state}",
            "et_date": str(ts.tz_convert("America/New_York").date()) if ts.tzinfo else str(ts.date()),
            "symbol": "TY",
            "state": state,
            "opened_ts": ts.isoformat(),
            "deadline_ts": (ts + pd.Timedelta(minutes=HORIZON_MINUTES)).isoformat(),
            "open_price": float(row["price"]),
            "open_fair_value": float(row["fair_value"]),
            "open_residual_z": float(row["residual_z"]),
            "open_residual": residual,
            "direction": direction,
            "direction_sign": -1.0 if residual > 0 else 1.0,
            "z_method": str(row.get("z_method") or Z_METHOD),
            "status": "open",
            "reversion_status": "pending",
            "closed_ts": None,
            "minutes_to_close": None,
            "max_abs_z": abs(float(row["residual_z"])),
            "escalated_to_dislocation": state == "DISLOCATION",
            "logged": False,
        }

    def _active_episode(self) -> dict[str, Any] | None:
        for episode in reversed(self.episodes):
            if episode.get("status") == "open":
                return episode
        return None

    def _evaluate_all_open(self, model_df: pd.DataFrame, now_ts: pd.Timestamp) -> None:
        for episode in self.episodes:
            if episode.get("status") == "open":
                self._evaluate_open_episode(episode, model_df, now_ts)

    def _evaluate_open_episode(self, episode: dict[str, Any], model_df: pd.DataFrame, now_ts: pd.Timestamp) -> None:
        opened = pd.Timestamp(episode["opened_ts"])
        deadline = pd.Timestamp(episode["deadline_ts"])
        window = model_df[(model_df.index >= opened) & (model_df.index <= now_ts)].dropna(subset=["residual_z"])
        if window.empty and now_ts >= deadline:
            episode["status"] = "finalized"
            episode["reversion_status"] = "interrupted"
            return
        if window.empty:
            return
        episode["max_abs_z"] = max(float(episode.get("max_abs_z") or 0.0), float(window["residual_z"].abs().max()))
        if float(episode.get("max_abs_z") or 0.0) >= DISLOCATION_Z:
            episode["escalated_to_dislocation"] = True
        closed = window[window["residual_z"].abs() < RESET_Z]
        if episode.get("reversion_status") == "pending" and not closed.empty:
            close_ts = pd.Timestamp(closed.index[0])
            resolution_window = window[window.index <= close_ts]
            self._update_mae_to_resolve(episode, resolution_window)
            close_row = closed.iloc[0]
            episode["close_price"] = float(close_row["price"]) if pd.notna(close_row.get("price")) else None
            episode["close_fair_value"] = float(close_row["fair_value"]) if pd.notna(close_row.get("fair_value")) else None
            episode["price_share_of_reversion"] = self._price_share_of_reversion(episode)
            episode["reversion_status"] = "resolved"
            episode["closed_ts"] = close_ts.isoformat()
            episode["minutes_to_close"] = float((close_ts - opened).total_seconds() / 60.0)
        elif episode.get("reversion_status") == "pending":
            self._update_mae_to_resolve(episode, window)
        for horizon in SCORE_HORIZONS:
            self._score_horizon(episode, model_df, opened, horizon)
        if now_ts >= deadline:
            if episode.get("reversion_status") == "pending":
                episode["reversion_status"] = "expired"
            episode["status"] = "finalized"

    def _update_mae_to_resolve(self, episode: dict[str, Any], window: pd.DataFrame) -> None:
        price_window = window.dropna(subset=["price"])
        if price_window.empty:
            return
        open_price = float(episode["open_price"])
        direction = float(episode["direction_sign"])
        path_pnl = direction * (price_window["price"].astype(float) - open_price)
        adverse_points = min(float(path_pnl.min()), 0.0)
        episode["mae_to_resolve_ticks"] = abs(adverse_points) / TICK_SIZE

    def _price_share_of_reversion(self, episode: dict[str, Any]) -> float | None:
        close_price = episode.get("close_price")
        if close_price is None:
            return None
        gap = float(episode.get("open_residual") or 0.0)
        if abs(gap) < TICK_SIZE:
            return None
        sign = 1.0 if gap > 0 else -1.0
        share = sign * (float(episode["open_price"]) - float(close_price)) / abs(gap)
        return float(max(-1.0, min(2.0, share)))

    def _score_horizon(self, episode: dict[str, Any], model_df: pd.DataFrame, opened: pd.Timestamp, horizon: int) -> None:
        key = f"{horizon}m"
        if f"dir_pnl_{key}" in episode:
            return
        horizon_ts = opened + pd.Timedelta(minutes=horizon)
        window = model_df[(model_df.index >= opened) & (model_df.index <= horizon_ts)].dropna(subset=["price"])
        if window.empty or pd.Timestamp(window.index.max()) < horizon_ts:
            return
        open_price = float(episode["open_price"])
        direction = float(episode["direction_sign"])
        end_price = float(window.iloc[-1]["price"])
        path_pnl = direction * (window["price"].astype(float) - open_price)
        episode[f"dir_move_{key}"] = end_price - open_price
        episode[f"dir_pnl_{key}"] = float(direction * (end_price - open_price))
        episode[f"adverse_{key}"] = float(path_pnl.min())

    def _append_finalized_logs(self) -> None:
        rows = [episode for episode in self.episodes if episode.get("status") == "finalized" and not episode.get("logged")]
        if not rows:
            return
        frame = pd.DataFrame(rows)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        frame = self._migrate_log_columns(frame)
        frame.to_csv(self.log_path, mode="a", header=not self.log_path.exists(), index=False)
        for episode in rows:
            episode["logged"] = True

    def _migrate_log_columns(self, frame: pd.DataFrame) -> pd.DataFrame:
        desired = list(dict.fromkeys([*frame.columns.tolist(), *NEW_LOG_COLUMNS]))
        if not self.log_path.exists():
            return frame.reindex(columns=desired)
        try:
            existing = pd.read_csv(self.log_path)
        except Exception:
            return frame
        columns = list(dict.fromkeys([*existing.columns.tolist(), *desired]))
        if columns != existing.columns.tolist():
            existing.reindex(columns=columns).to_csv(self.log_path, index=False)
        return frame.reindex(columns=columns)

    def summary(self, active: dict[str, Any] | None) -> Dict[str, Any]:
        with self._lock:
            return self._summary_unlocked(active)

    def _summary_unlocked(self, active: dict[str, Any] | None) -> Dict[str, Any]:
        watch = [e for e in self.episodes if e.get("state") == "WATCH"]
        dislocations = [e for e in self.episodes if e.get("state") == "DISLOCATION"]
        resolved = [e for e in self.episodes if e.get("reversion_status") == "resolved"]
        expired = [e for e in self.episodes if e.get("reversion_status") == "expired"]
        minutes = [e.get("minutes_to_close") for e in resolved if e.get("minutes_to_close") is not None]
        finalized = [e for e in self.episodes if e.get("status") == "finalized"]
        pnl30 = [e.get("dir_pnl_30m") for e in finalized if e.get("dir_pnl_30m") is not None]
        return {
            "active": active,
            "episodes": self.episodes[-50:],
            "stats": {
                "watch_issued": len(watch),
                "dislocation_issued": len(dislocations),
                "watch_stats": self._split_stats(watch),
                "dislocation_stats": self._split_stats(dislocations),
                "resolved": len(resolved),
                "expired": len(expired),
                "resolution_rate": len(resolved) / len(self.episodes) if self.episodes else 0.0,
                "median_minutes_to_close": float(pd.Series(minutes).median()) if minutes else None,
                "finalized": len(finalized),
                "directional_hit_rate_30m": float((pd.Series(pnl30) > 0).mean()) if pnl30 else None,
                "avg_dir_pnl_30m": float(pd.Series(pnl30).mean()) if pnl30 else None,
                "avg_adverse_30m": self._avg_key(finalized, "adverse_30m"),
                "median_mae_to_resolve_ticks": self._median_key(resolved, "mae_to_resolve_ticks"),
                "median_price_share_of_reversion": self._median_key(resolved, "price_share_of_reversion"),
                "avg_price_share_of_reversion": self._avg_key(resolved, "price_share_of_reversion"),
                "horizon_minutes": HORIZON_MINUTES,
                "reset_z": RESET_Z,
                "watch_z": WATCH_Z,
                "dislocation_z": DISLOCATION_Z,
            },
        }

    def _avg_key(self, episodes: list[dict[str, Any]], key: str) -> float | None:
        values = [e.get(key) for e in episodes if e.get(key) is not None]
        return float(pd.Series(values).mean()) if values else None

    def _median_key(self, episodes: list[dict[str, Any]], key: str) -> float | None:
        values = [e.get(key) for e in episodes if e.get(key) is not None]
        return float(pd.Series(values).median()) if values else None

    def _split_stats(self, episodes: list[dict[str, Any]]) -> Dict[str, Any]:
        resolved = [e for e in episodes if e.get("reversion_status") == "resolved"]
        expired = [e for e in episodes if e.get("reversion_status") == "expired"]
        finalized = [e for e in episodes if e.get("status") == "finalized"]
        pnl30 = [e.get("dir_pnl_30m") for e in finalized if e.get("dir_pnl_30m") is not None]
        minutes = [e.get("minutes_to_close") for e in resolved if e.get("minutes_to_close") is not None]
        return {
            "issued": len(episodes),
            "resolved": len(resolved),
            "expired": len(expired),
            "finalized": len(finalized),
            "resolution_rate": len(resolved) / len(episodes) if episodes else 0.0,
            "median_minutes_to_close": float(pd.Series(minutes).median()) if minutes else None,
            "directional_hit_rate_30m": float((pd.Series(pnl30) > 0).mean()) if pnl30 else None,
            "avg_dir_pnl_30m": float(pd.Series(pnl30).mean()) if pnl30 else None,
            "avg_adverse_30m": self._avg_key(finalized, "adverse_30m"),
            "median_mae_to_resolve_ticks": self._median_key(resolved, "mae_to_resolve_ticks"),
            "median_price_share_of_reversion": self._median_key(resolved, "price_share_of_reversion"),
            "avg_price_share_of_reversion": self._avg_key(resolved, "price_share_of_reversion"),
        }

    def log_rows(self, scope: str = "today") -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self.episodes)
        if scope == "today":
            today = datetime.now(timezone.utc).astimezone().date().isoformat()
            rows = [row for row in rows if row.get("et_date") == today]
        return rows
