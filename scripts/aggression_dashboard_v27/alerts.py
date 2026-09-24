from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Isolated V2.7 live store — never write to aggression_v2_live/ or v22/v23.
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v27_live"
STORE_PATH = STORE_DIR / "alert_episodes.json"
LOG_PATH = STORE_DIR / "alert_log.csv"
WATCH_Z = 1.5
DISLOCATION_Z = 2.0
RESET_Z = 0.5
FLIP_Z = 1.0
MIN_HOLD_MINUTES = 5
HORIZON_MINUTES = 10
SESSION_OPEN_HOUR_ET = 18
SCORE_HORIZONS = (5, 10)
TICK_SIZE = 1.0 / 64.0
Z_METHOD = "prior_blend_v1"
SESSION_MIN_TICKS = 2.0
SESSION_MAX_TICKS = 6.0
# Display-only: this Globex session's banner/table start here. Later sessions use 18:00 ET.
STATS_CUTOVER_ET = pd.Timestamp("2026-09-02 12:07:00", tz="America/New_York")
V2_LIVE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
GATE_FLAT_TEXT = (
    "FLAT — wait for |z| ≥ 1.5, session 2–6 ticks, same sign, new session-sign after exit"
)
_V2_SESSION_CACHE: dict[str, pd.Series] = {}
_V2_SESSION_MTIME: dict[str, float] = {}
NEW_LOG_COLUMNS = [
    "z_method",
    "close_price",
    "close_fair_value",
    "mae_to_resolve_ticks",
    "price_share_of_reversion",
    "dir_pnl_trade",
    "adverse_trade",
    "dir_pnl_trade_ticks",
    "exit_reason",
    "minutes_held",
]


def session_ticks(residual: Any) -> float | None:
    try:
        value = float(residual)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value / TICK_SIZE


def residual_sign(residual: Any) -> float:
    ticks = session_ticks(residual)
    if ticks is None or ticks == 0.0:
        return 0.0
    return float(np.sign(ticks))


def conviction_entry_gate(
    local_residual: Any,
    session_residual: Any,
    last_exit_session_sign: float | None = None,
) -> str | None:
    """Return skip reason, or None if the local-z bar may open."""
    ticks = session_ticks(session_residual)
    if ticks is None:
        return "SESSION_UNAVAILABLE"
    if abs(ticks) < SESSION_MIN_TICKS:
        return "NOWCAST_ONLY"
    if abs(ticks) > SESSION_MAX_TICKS:
        return "REGIME_TAIL"
    local_sign = residual_sign(local_residual)
    session_sign = residual_sign(session_residual)
    if local_sign == 0.0 or session_sign == 0.0 or local_sign != session_sign:
        return "DISAGREE"
    if last_exit_session_sign is not None and last_exit_session_sign != 0.0 and session_sign == last_exit_session_sign:
        return "SAME_SIGN_OPEN"
    return None


def attach_session_residual(model_df: pd.DataFrame) -> pd.DataFrame:
    """Stamp session_residual from read-only V2 logs unless already present."""
    if model_df is None or model_df.empty:
        return model_df
    out = model_df.copy()
    if "session_residual" in out.columns and pd.to_numeric(out["session_residual"], errors="coerce").notna().any():
        return out
    lookup = _v2_session_lookup(out.index)
    if lookup.empty:
        out["session_residual"] = np.nan
        return out
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    floored = idx.floor("min")
    out["session_residual"] = [lookup.get(ts, np.nan) for ts in floored]
    return out


def _v2_session_lookup(index: pd.Index) -> pd.Series:
    if index is None or len(index) == 0:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    et_dates = sorted(set(idx.tz_convert("America/New_York").strftime("%Y%m%d")))
    extra = (idx.min().tz_convert("America/New_York") - pd.Timedelta(hours=6)).strftime("%Y%m%d")
    dates = sorted(set([*et_dates, extra]))
    parts = []
    for date_text in dates:
        series = _load_v2_session_log(date_text)
        if not series.empty:
            parts.append(series)
    if not parts:
        return pd.Series(dtype=float)
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _load_v2_session_log(date_text: str) -> pd.Series:
    path = V2_LIVE_DIR / f"signal_log_{date_text}.csv"
    if not path.exists():
        return pd.Series(dtype=float)
    mtime = path.stat().st_mtime
    cached = _V2_SESSION_CACHE.get(date_text)
    if cached is not None and _V2_SESSION_MTIME.get(date_text) == mtime:
        return cached
    try:
        frame = pd.read_csv(path, usecols=["ts", "residual"])
    except Exception:
        empty = pd.Series(dtype=float)
        _V2_SESSION_CACHE[date_text] = empty
        _V2_SESSION_MTIME[date_text] = mtime
        return empty
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame["residual"] = pd.to_numeric(frame["residual"], errors="coerce")
    frame = frame.dropna(subset=["ts", "residual"])
    series = frame.set_index(frame["ts"].dt.floor("min"))["residual"]
    series = series[~series.index.duplicated(keep="last")].sort_index()
    _V2_SESSION_CACHE[date_text] = series
    _V2_SESSION_MTIME[date_text] = mtime
    return series


def _record_v27_alert(episode: dict[str, Any], kind: str, ts: Any = None) -> None:
    try:
        from scripts.aggression_v1_v27_banner_alerts.store import record_v27_alert

        record_v27_alert(episode, kind, ts=ts)
    except Exception:
        return


def backfill_trade_pnl(episode: dict[str, Any]) -> None:
    """Fill trade P&L from entry/exit last prints when the field was never stored."""
    if episode.get("dir_pnl_trade") is not None:
        return
    open_price = episode.get("open_price")
    close_price = episode.get("close_price")
    sign = episode.get("direction_sign")
    if open_price is None or close_price is None or sign is None:
        return
    pnl = float(sign) * (float(close_price) - float(open_price))
    episode["dir_pnl_trade"] = pnl
    episode["dir_pnl_trade_ticks"] = pnl / TICK_SIZE
    if episode.get("adverse_trade") is None:
        episode["adverse_trade"] = min(pnl, 0.0)
    if episode.get("minutes_held") is None and episode.get("minutes_to_close") is not None:
        episode["minutes_held"] = episode["minutes_to_close"]
    if episode.get("exit_reason") is None:
        if episode.get("reversion_status") == "resolved":
            episode["exit_reason"] = "Z_RESET"
        elif episode.get("reversion_status") == "expired":
            episode["exit_reason"] = "CLOCK_10M"


def _first_exit_row(
    window: pd.DataFrame,
    episode: dict[str, Any],
    hold_ts: pd.Timestamp,
) -> tuple[pd.Series | None, str]:
    """One-sided fade exit after min-hold, plus opposite-side flip even during the hold."""
    z = window["residual_z"].astype(float)
    short = float(episode.get("direction_sign") or 0.0) < 0.0
    after_hold = window.index >= hold_ts
    if short:
        mild = (z <= RESET_Z) & after_hold
        flip = z <= -FLIP_Z
    else:
        mild = (z >= -RESET_Z) & after_hold
        flip = z >= FLIP_Z
    hit = window[mild | flip]
    if hit.empty:
        return None, ""
    row = hit.iloc[0]
    z_exit = float(row["residual_z"])
    flipped = (z_exit <= -FLIP_Z) if short else (z_exit >= FLIP_Z)
    return row, ("FLIP" if flipped else "Z_RESET")


class AlertStore:
    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = path.parent / LOG_PATH.name
        self._lock = threading.Lock()
        self.episodes = self._load()
        self._last_gate: dict[str, Any] = {
            "skip_reason": None,
            "session_ticks": None,
            "agree": None,
            "z_hot": False,
        }

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
                backfill_trade_pnl(episode)
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
            session_residual = row.get("session_residual")
            ticks = session_ticks(session_residual)
            agree = residual_sign(residual) != 0.0 and residual_sign(residual) == residual_sign(session_residual)
            skip = None
            if state != "NONE" and self._active_episode() is None:
                skip = conviction_entry_gate(
                    residual,
                    session_residual,
                    self._last_exit_session_sign(ts),
                )
                if skip is None:
                    episode = self._new_episode(state, ts, row)
                    self.episodes.append(episode)
                    if self.path == STORE_PATH:
                        _record_v27_alert(episode, "ENTRY", ts)
            self._last_gate = {
                "skip_reason": skip,
                "session_ticks": ticks,
                "agree": agree,
                "z_hot": state != "NONE",
            }
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
            "session_residual": None if session_ticks(row.get("session_residual")) is None else float(row.get("session_residual")),
            "session_ticks": session_ticks(row.get("session_residual")),
            "agree": residual_sign(residual) != 0.0 and residual_sign(residual) == residual_sign(row.get("session_residual")),
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
        deadline = (
            pd.Timestamp(episode["deadline_ts"])
            if episode.get("deadline_ts")
            else opened + pd.Timedelta(minutes=HORIZON_MINUTES)
        )
        window = model_df[(model_df.index >= opened) & (model_df.index <= now_ts)].dropna(subset=["residual_z"])
        if window.empty:
            return
        episode["max_abs_z"] = max(float(episode.get("max_abs_z") or 0.0), float(window["residual_z"].abs().max()))
        if float(episode.get("max_abs_z") or 0.0) >= DISLOCATION_Z:
            episode["escalated_to_dislocation"] = True
        hold_ts = opened + pd.Timedelta(minutes=MIN_HOLD_MINUTES)
        closed, reason = _first_exit_row(window, episode, hold_ts)
        if episode.get("reversion_status") == "pending" and closed is not None:
            close_ts = pd.Timestamp(closed.name)
            resolution_window = window[window.index <= close_ts]
            self._close_trade(episode, closed, close_ts, resolution_window, reason)
            return
        if episode.get("reversion_status") == "pending":
            self._update_mae_to_resolve(episode, window)
            for horizon in SCORE_HORIZONS:
                self._score_horizon(episode, model_df, opened, horizon)
            if now_ts >= deadline:
                episode["horizon_10m_marked"] = True
        if episode.get("reversion_status") == "resolved" and episode.get("status") == "open":
            # Legacy rows that reset z but waited for the 10m clock: flatten now.
            if episode.get("dir_pnl_trade") is None and episode.get("close_price") is not None:
                self._score_trade_from_close(episode)
            episode["exit_reason"] = episode.get("exit_reason") or "Z_RESET"
            episode["status"] = "finalized"

    def _close_trade(
        self,
        episode: dict[str, Any],
        close_row: pd.Series,
        close_ts: pd.Timestamp,
        path: pd.DataFrame,
        reason: str,
    ) -> None:
        opened = pd.Timestamp(episode["opened_ts"])
        episode["close_price"] = float(close_row["price"]) if pd.notna(close_row.get("price")) else None
        episode["close_fair_value"] = float(close_row["fair_value"]) if pd.notna(close_row.get("fair_value")) else None
        episode["closed_ts"] = close_ts.isoformat()
        minutes_held = float((close_ts - opened).total_seconds() / 60.0)
        episode["minutes_to_close"] = minutes_held
        episode["minutes_held"] = minutes_held
        episode["exit_reason"] = reason
        episode["reversion_status"] = "expired" if reason == "CLOCK_10M" else "resolved"
        episode["price_share_of_reversion"] = self._price_share_of_reversion(episode)
        self._update_mae_to_resolve(episode, path)
        self._score_trade_path(episode, path)
        episode["status"] = "finalized"
        close_session = close_row.get("session_residual") if close_row is not None else None
        if session_ticks(close_session) is not None:
            episode["exit_session_sign"] = residual_sign(close_session)
        elif episode.get("session_residual") is not None:
            episode["exit_session_sign"] = residual_sign(episode.get("session_residual"))
        if self.path == STORE_PATH:
            _record_v27_alert(episode, "EXIT", close_ts)

    def _last_exit_session_sign(self, now_ts: Any) -> float | None:
        start = session_start_et(now_ts)
        prior = [
            episode
            for episode in episodes_in_session(self.episodes, start)
            if episode.get("status") == "finalized"
        ]
        if not prior:
            return None
        last = prior[-1]
        if last.get("exit_session_sign") is not None:
            return float(last["exit_session_sign"])
        if last.get("session_residual") is not None:
            return residual_sign(last.get("session_residual"))
        return None

    def _score_trade_from_close(self, episode: dict[str, Any]) -> None:
        open_price = float(episode["open_price"])
        close_price = float(episode["close_price"])
        direction = float(episode["direction_sign"])
        pnl = float(direction * (close_price - open_price))
        episode["dir_pnl_trade"] = pnl
        episode["dir_pnl_trade_ticks"] = pnl / TICK_SIZE
        if episode.get("adverse_trade") is None:
            episode["adverse_trade"] = min(pnl, 0.0)

    def _score_trade_path(self, episode: dict[str, Any], window: pd.DataFrame) -> None:
        price_window = window.dropna(subset=["price"])
        if price_window.empty or episode.get("close_price") is None:
            return
        open_price = float(episode["open_price"])
        direction = float(episode["direction_sign"])
        path_pnl = direction * (price_window["price"].astype(float) - open_price)
        pnl = float(direction * (float(episode["close_price"]) - open_price))
        episode["dir_pnl_trade"] = pnl
        episode["dir_pnl_trade_ticks"] = pnl / TICK_SIZE
        episode["adverse_trade"] = float(path_pnl.min())

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
        now_ts = pd.Timestamp(datetime.now(timezone.utc))
        return {
            "active": active,
            "conviction_gate": dict(self._last_gate),
            "trade_signal": build_trade_signal(active, self.episodes, now_ts, self._last_gate),
            "chart_marks": chart_marks(visible_chart_episodes(self.episodes, active)),
            "episodes": self.episodes[-50:],
            "stats": self._collect_stats(self.episodes),
            "session_stats": self._session_day_stats(self.episodes, now_ts),
        }

    def _collect_stats(self, episodes: list[dict[str, Any]]) -> Dict[str, Any]:
        watch = [e for e in episodes if e.get("state") == "WATCH"]
        dislocations = [e for e in episodes if e.get("state") == "DISLOCATION"]
        resolved = [e for e in episodes if e.get("reversion_status") == "resolved"]
        expired = [e for e in episodes if e.get("reversion_status") == "expired"]
        minutes = [e.get("minutes_to_close") for e in resolved if e.get("minutes_to_close") is not None]
        finalized = [e for e in episodes if e.get("status") == "finalized"]
        pnl10 = [e.get("dir_pnl_10m") for e in finalized if e.get("dir_pnl_10m") is not None]
        return {
            "watch_issued": len(watch),
            "dislocation_issued": len(dislocations),
            "watch_stats": self._split_stats(watch),
            "dislocation_stats": self._split_stats(dislocations),
            "resolved": len(resolved),
            "expired": len(expired),
            "resolution_rate": len(resolved) / len(episodes) if episodes else 0.0,
            "median_minutes_to_close": float(pd.Series(minutes).median()) if minutes else None,
            "finalized": len(finalized),
            "n_trades": len(finalized),
            "directional_hit_rate_trade": self._hit_rate(finalized, "dir_pnl_trade"),
            "avg_dir_pnl_trade": self._avg_key(finalized, "dir_pnl_trade"),
            "avg_dir_pnl_trade_ticks": self._avg_key(finalized, "dir_pnl_trade_ticks"),
            "avg_adverse_trade": self._avg_key(finalized, "adverse_trade"),
            "directional_hit_rate_10m": float((pd.Series(pnl10) > 0).mean()) if pnl10 else None,
            "avg_dir_pnl_10m": float(pd.Series(pnl10).mean()) if pnl10 else None,
            "avg_adverse_10m": self._avg_key(finalized, "adverse_10m"),
            "directional_hit_rate_5m": self._hit_rate(finalized, "dir_pnl_5m"),
            "avg_dir_pnl_5m": self._avg_key(finalized, "dir_pnl_5m"),
            "avg_adverse_5m": self._avg_key(finalized, "adverse_5m"),
            "median_mae_to_resolve_ticks": self._median_key(resolved, "mae_to_resolve_ticks"),
            "median_price_share_of_reversion": self._median_key(resolved, "price_share_of_reversion"),
            "avg_price_share_of_reversion": self._avg_key(resolved, "price_share_of_reversion"),
            "horizon_minutes": HORIZON_MINUTES,
            "min_hold_minutes": MIN_HOLD_MINUTES,
            "reset_z": RESET_Z,
            "watch_z": WATCH_Z,
            "dislocation_z": DISLOCATION_Z,
        }

    def _session_day_stats(
        self,
        episodes: list[dict[str, Any]],
        now: Any = None,
    ) -> Dict[str, Any]:
        start = visible_session_start_et(now)
        session_open = session_start_et(now)
        rows = episodes_in_session(episodes, start)
        stats = self._collect_stats(rows)
        finalized = [e for e in rows if e.get("status") == "finalized"]
        ticks = [
            float(e["dir_pnl_trade_ticks"])
            for e in finalized
            if e.get("dir_pnl_trade_ticks") is not None
        ]
        stats.update({
            "session_start_et": start.strftime("%Y-%m-%d %H:%M ET"),
            "session_open_et": session_open.strftime("%Y-%m-%d %H:%M ET"),
            "stats_window": "gate_cutover" if start > session_open else "session",
            "session_open_hour_et": SESSION_OPEN_HOUR_ET,
            "ticks_captured": float(sum(ticks)) if ticks else 0.0,
            "n_issued": len(rows),
            "n_closed": len(finalized),
            "n_open": sum(1 for e in rows if e.get("status") == "open"),
            "n_win": sum(1 for value in ticks if value > 0),
            "n_lose": sum(1 for value in ticks if value < 0),
            "n_flat": sum(1 for value in ticks if value == 0),
        })
        return stats

    def _hit_rate(self, episodes: list[dict[str, Any]], key: str) -> float | None:
        values = [e.get(key) for e in episodes if e.get(key) is not None]
        return float((pd.Series(values) > 0).mean()) if values else None

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
        pnl10 = [e.get("dir_pnl_10m") for e in finalized if e.get("dir_pnl_10m") is not None]
        minutes = [e.get("minutes_to_close") for e in resolved if e.get("minutes_to_close") is not None]
        return {
            "issued": len(episodes),
            "resolved": len(resolved),
            "expired": len(expired),
            "finalized": len(finalized),
            "resolution_rate": len(resolved) / len(episodes) if episodes else 0.0,
            "median_minutes_to_close": float(pd.Series(minutes).median()) if minutes else None,
            "directional_hit_rate_trade": self._hit_rate(finalized, "dir_pnl_trade"),
            "avg_dir_pnl_trade": self._avg_key(finalized, "dir_pnl_trade"),
            "directional_hit_rate_10m": float((pd.Series(pnl10) > 0).mean()) if pnl10 else None,
            "avg_dir_pnl_10m": float(pd.Series(pnl10).mean()) if pnl10 else None,
            "avg_adverse_10m": self._avg_key(finalized, "adverse_10m"),
            "median_mae_to_resolve_ticks": self._median_key(resolved, "mae_to_resolve_ticks"),
            "median_price_share_of_reversion": self._median_key(resolved, "price_share_of_reversion"),
            "avg_price_share_of_reversion": self._avg_key(resolved, "price_share_of_reversion"),
        }

    def log_rows(self, scope: str = "today") -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self.episodes)
        if scope == "today":
            rows = episodes_in_session(rows, visible_session_start_et())
        for row in rows:
            backfill_trade_pnl(row)
        return rows


def session_start_et(now: Any = None) -> pd.Timestamp:
    """Most recent 18:00 ET at or before now — Globex session open."""
    now_et = (_as_utc(now) or pd.Timestamp.now(tz="UTC")).tz_convert("America/New_York")
    start = now_et.normalize() + pd.Timedelta(hours=SESSION_OPEN_HOUR_ET)
    if now_et < start:
        start = start - pd.Timedelta(days=1)
    return start


def visible_session_start_et(now: Any = None) -> pd.Timestamp:
    """Session-day display start: cutover only for the session that contains it."""
    start = session_start_et(now)
    session_end = start + pd.Timedelta(days=1)
    if start <= STATS_CUTOVER_ET < session_end:
        return STATS_CUTOVER_ET
    return start


def episodes_in_session(
    episodes: list[dict[str, Any]],
    start: pd.Timestamp | None = None,
) -> list[dict[str, Any]]:
    open_et = start if start is not None else session_start_et()
    start_utc = open_et.tz_convert("UTC") if open_et.tzinfo else open_et.tz_localize("UTC")
    kept: list[dict[str, Any]] = []
    for episode in episodes:
        opened = _as_utc(episode.get("opened_ts"))
        if opened is not None and opened >= start_utc:
            kept.append(episode)
    return kept


def _as_utc(ts: Any) -> pd.Timestamp | None:
    if ts is None:
        return None
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _et_clock(ts: Any) -> str | None:
    stamp = _as_utc(ts)
    if stamp is None:
        return None
    return stamp.tz_convert("America/New_York").strftime("%H:%M ET")


def _chart_x(ts: Any) -> str | None:
    stamp = _as_utc(ts)
    if stamp is None:
        return None
    return str(stamp.tz_convert("America/New_York").tz_localize(None))


def _side_label(episode: dict[str, Any] | None) -> str | None:
    if not episode:
        return None
    if episode.get("direction") == "SHORT_RICH" or float(episode.get("direction_sign") or 0.0) < 0:
        return "SHORT"
    if episode.get("direction") == "LONG_CHEAP" or float(episode.get("direction_sign") or 0.0) > 0:
        return "LONG"
    return None


def _exit_reason(episode: dict[str, Any] | None) -> str | None:
    if not episode:
        return None
    stored = episode.get("exit_reason")
    if stored in {"Z_RESET", "FLIP", "CLOCK_10M"}:
        return stored
    if episode.get("reversion_status") == "resolved":
        return "Z_RESET"
    if episode.get("reversion_status") == "expired":
        return "CLOCK_10M"
    return None


def build_trade_signal(
    active: dict[str, Any] | None,
    episodes: list[dict[str, Any]],
    now_ts: pd.Timestamp | None,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _as_utc(now_ts) or pd.Timestamp(datetime.now(timezone.utc))
    last = next((e for e in reversed(episodes) if e.get("status") == "finalized"), None)
    episode = active or last
    side = _side_label(episode)
    if active is None:
        last_reason = _exit_reason(last)
        last_label = None
        if last_reason == "Z_RESET":
            last_label = "EXIT Z-RESET"
        elif last_reason == "FLIP":
            last_label = "EXIT FLIP"
        elif last_reason == "CLOCK_10M":
            last_label = "EXIT 10M CLOCK"
        skip = (gate or {}).get("skip_reason")
        ticks = (gate or {}).get("session_ticks")
        tick_bit = "" if ticks is None else f", session {float(ticks):+.1f} ticks"
        if skip and (gate or {}).get("z_hot"):
            entry_text = f"FLAT — SKIP {skip}{tick_bit}"
            now_text = f"Local |z| is hot but gated ({skip})"
        else:
            entry_text = GATE_FLAT_TEXT
            now_text = "No open lean"
        return {
            "phase": "FLAT",
            "research_only": True,
            "entry_action": None,
            "entry_side": None,
            "entry_text": entry_text,
            "exit_text": "Then EXIT when the fade side dies (|z| through 0.5) or flips to the other side (|z|≥1). 10m is a mark, not a flatten",
            "now_text": now_text,
            "min_hold_remaining_min": None,
            "minutes_to_deadline": None,
            "can_z_exit": False,
            "last_exit_action": last_label,
            "last_exit_ts_et": _et_clock(last.get("closed_ts") or last.get("deadline_ts")) if last else None,
            "last_exit_price": last.get("close_price") if last else None,
            "entry_ts_et": None,
            "entry_price": None,
            "entry_z": None,
            "deadline_ts_et": None,
            "min_hold_ts_et": None,
            "exit_action": None,
            "skip_reason": skip,
            "session_ticks": ticks,
        }
    opened = _as_utc(active["opened_ts"])
    deadline = _as_utc(active["deadline_ts"])
    hold_ts = opened + pd.Timedelta(minutes=MIN_HOLD_MINUTES) if opened is not None else None
    minutes_held = float((now - opened).total_seconds() / 60.0) if opened is not None else None
    minutes_to_deadline = float((deadline - now).total_seconds() / 60.0) if deadline is not None else None
    min_hold_left = float((hold_ts - now).total_seconds() / 60.0) if hold_ts is not None else None
    z_hit = active.get("reversion_status") == "resolved"
    can_z_exit = (min_hold_left is not None and min_hold_left <= 0) and not z_hit
    if z_hit:
        phase = "Z_EXITED"
        exit_action = "EXIT FLIP" if active.get("exit_reason") == "FLIP" else "EXIT Z-RESET"
        now_text = (
            f"{exit_action} at {_et_clock(active.get('closed_ts'))}."
        )
    else:
        phase = "IN_TRADE"
        exit_action = None
        hold_bit = (
            f"z-exit ready"
            if can_z_exit
            else f"z-exit in {max(min_hold_left or 0.0, 0.0):.0f}m"
        )
        if minutes_to_deadline is not None and minutes_to_deadline > 0:
            mark_bit = f"10m mark in {minutes_to_deadline:.0f}m"
        else:
            mark_bit = "10m mark passed, still open"
        now_text = f"HOLDING {max(minutes_held or 0.0, 0.0):.0f}m · {hold_bit} · {mark_bit}"
    return {
        "phase": phase,
        "research_only": True,
        "entry_action": f"ENTER {side}" if side else "ENTER",
        "entry_side": side,
        "entry_text": (
            f"ENTER {side} @ {_et_clock(opened)}  {active.get('open_price')}  "
            f"z={float(active.get('open_residual_z') or 0.0):+.2f}"
        ),
        "exit_text": (
            f"EXIT fade-side |z| through {RESET_Z} after {_et_clock(hold_ts)}  ·  "
            f"or EXIT FLIP if other side ≥ {FLIP_Z}  ·  10m mark at {_et_clock(deadline)}"
        ),
        "now_text": now_text,
        "min_hold_remaining_min": None if min_hold_left is None else max(min_hold_left, 0.0),
        "minutes_to_deadline": minutes_to_deadline,
        "can_z_exit": can_z_exit,
        "last_exit_action": None,
        "last_exit_ts_et": _et_clock(active.get("closed_ts")) if z_hit else None,
        "last_exit_price": active.get("close_price") if z_hit else None,
        "entry_ts_et": _et_clock(opened),
        "entry_price": active.get("open_price"),
        "entry_z": active.get("open_residual_z"),
        "deadline_ts_et": _et_clock(deadline),
        "min_hold_ts_et": _et_clock(hold_ts),
        "exit_action": exit_action,
    }


def visible_chart_episodes(
    episodes: list[dict[str, Any]],
    active: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Only the open lean and the last finished one — keeps the live plot readable."""
    chosen: list[dict[str, Any]] = []
    if active:
        chosen.append(active)
    last = next((e for e in reversed(episodes) if e.get("status") == "finalized"), None)
    if last is not None and last is not active and last not in chosen:
        chosen.append(last)
    return chosen


def chart_marks(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []
    for episode in episodes:
        side = _side_label(episode)
        opened_x = _chart_x(episode.get("opened_ts"))
        if opened_x:
            marks.append({
                "kind": "entry",
                "x": opened_x,
                "price": episode.get("open_price"),
                "z": episode.get("open_residual_z"),
                "label": f"ENTER {side}" if side else "ENTER",
            })
        if episode.get("reversion_status") == "resolved" and episode.get("closed_ts"):
            marks.append({
                "kind": "exit",
                "x": _chart_x(episode.get("closed_ts")),
                "price": episode.get("close_price"),
                "z": None,
                "label": "EXIT FLIP" if episode.get("exit_reason") == "FLIP" else "EXIT Z-RESET",
            })
        elif episode.get("reversion_status") == "expired":
            marks.append({
                "kind": "exit",
                "x": _chart_x(episode.get("deadline_ts")),
                "price": episode.get("close_price") or episode.get("open_price"),
                "z": None,
                "label": "EXIT 10M",
            })
        elif episode.get("status") == "open":
            marks.append({
                "kind": "deadline",
                "x": _chart_x(episode.get("deadline_ts")),
                "price": episode.get("open_price"),
                "z": None,
                "label": "10M MARK",
            })
    return [mark for mark in marks if mark.get("x")]
