"""Isolated RANGE-caution episode store for Aggression V1.2.

Writes only under workspace/analysis/aggression_v12_live/. Does not touch V1
cache as a signal store, and never writes V2/V2.1/V3 live dirs.
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

from scripts.aggression_dashboard_v12.range_caution_context import (
    DECLUSTER_STRIDE_MIN,
    HORIZON_MIN,
    LABEL,
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    REGIME_CUTOFFS,
    RESEARCH_CONTEXT,
    SOFT_BREAKOUT_LABEL,
    SOFT_BREAKOUT_RESEARCH,
    STRATEGY_STATUS,
    annotate_caution_flags,
    decluster_fire_mask,
    fit_causal_v1_residual,
    latest_caution_context,
    score_forward_regime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v12_live"
STATE_PATH = STORE_DIR / "caution_episodes.json"
LOG_PATH = STORE_DIR / "caution_episodes.csv"
REFERENCE_RANGE_BASELINE = float(RESEARCH_CONTEXT["validation_baseline_range_rate"])


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


class CautionStore:
    def __init__(self, store_dir: Path = STORE_DIR) -> None:
        self.store_dir = Path(store_dir)
        self.state_path = self.store_dir / STATE_PATH.name
        self.log_path = self.store_dir / LOG_PATH.name
        self._lock = threading.Lock()
        self.episodes: list[dict[str, Any]] = self._load()
        self._last_signal_log_ts: pd.Timestamp | None = None
        self._last_signal_state: str | None = None

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
            "alert_kind",
            "et_date",
            "lock_ts_utc",
            "lock_price",
            "residual",
            "residual_z",
            "outcome",
            "net_ticks",
            "abs_net_ticks",
            "path_eff",
            "disp_to_range",
            "phase",
            "label",
        ]
        write_header = not self.log_path.exists()
        if not write_header:
            with self.log_path.open("r", encoding="utf-8", newline="") as handle:
                existing = next(csv.reader(handle), [])
            if existing and "alert_kind" not in existing:
                fields = [name for name in fields if name != "alert_kind"]
        with self.log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            row = {k: _finite(episode.get(k)) for k in fields}
            kind = str(episode.get("alert_kind") or "range_caution")
            if "alert_kind" in fields:
                row["alert_kind"] = kind
            row["label"] = LIFO_FRIENDLY_LABEL if kind == "lifo" else LABEL
            writer.writerow(row)

    def append_signal_log(self, context: dict[str, Any], *, force: bool = False) -> None:
        """Daily audit trail; throttled to state changes or ~60s."""
        now = pd.Timestamp.now(tz="UTC")
        state = str(context.get("state") or "")
        should = force or state != self._last_signal_state
        if not should and self._last_signal_log_ts is not None:
            should = (now - self._last_signal_log_ts) >= pd.Timedelta(seconds=60)
        if not should:
            return
        et_date = str(context.get("et_date") or datetime.now(timezone.utc).date())
        path = self.store_dir / f"signal_log_{et_date.replace('-', '')}.csv"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        fields = [
            "logged_at_utc",
            "ts_utc",
            "et_date",
            "state",
            "residual",
            "residual_z",
            "widening_15m",
            "large",
            "price",
        ]
        write_header = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "logged_at_utc": now.isoformat(),
                    "ts_utc": context.get("ts_utc"),
                    "et_date": et_date,
                    "state": state,
                    "residual": _finite(context.get("residual")),
                    "residual_z": _finite(context.get("residual_z")),
                    "widening_15m": bool(context.get("widening_15m")),
                    "large": bool(context.get("large")),
                    "price": _finite(context.get("price")),
                }
            )
        self._last_signal_log_ts = now
        self._last_signal_state = state

    @staticmethod
    def build_panel(flow_df: pd.DataFrame, price_df: pd.DataFrame) -> pd.DataFrame:
        """Align V1 flow + price into a causal residual panel."""
        if flow_df.empty or price_df.empty:
            return pd.DataFrame()
        flow = flow_df.copy()
        price = price_df.copy()
        if "price" not in price.columns:
            if "close" in price.columns:
                price = price.rename(columns={"close": "price"})
            else:
                return pd.DataFrame()
        flow_idx = pd.DatetimeIndex(flow.index)
        price_idx = pd.DatetimeIndex(price.index)
        flow.index = flow_idx.tz_localize("UTC") if flow_idx.tz is None else flow_idx.tz_convert("UTC")
        price.index = price_idx.tz_localize("UTC") if price_idx.tz is None else price_idx.tz_convert("UTC")

        if "signed_flow_bucket" in flow.columns:
            signed = flow["signed_flow_bucket"].astype(float).fillna(0.0)
        elif "total_aggress" in flow.columns:
            signed = flow["total_aggress"].astype(float).diff().fillna(0.0)
        else:
            return pd.DataFrame()

        # Daily-reset cumsum for research residual.
        et_dates = pd.Series(flow.index.tz_convert("America/New_York").date.astype(str), index=flow.index)
        agg_cum = signed.groupby(et_dates).cumsum()
        panel = pd.DataFrame(
            {
                "total_aggress": agg_cum,
                "signed_flow_1m": signed,
                "price": price["price"].astype(float).reindex(flow.index).ffill(),
            },
            index=flow.index,
        ).dropna(subset=["price"])
        if panel.empty:
            return panel
        # Trade intensity = prints per bucket (matches TBBO research trade_intensity).
        if "trade_count" in flow.columns:
            panel["trade_intensity"] = (
                pd.to_numeric(flow["trade_count"], errors="coerce").reindex(panel.index).fillna(0.0)
            )
        else:
            panel["trade_intensity"] = signed.abs()
        panel["et_date"] = panel.index.tz_convert("America/New_York").date.astype(str)
        panel["day_open_price"] = panel.groupby("et_date")["price"].transform("first")
        panel = fit_causal_v1_residual(panel)
        panel = annotate_caution_flags(panel)
        return panel

    def _maybe_append_fire(
        self,
        *,
        panel: pd.DataFrame,
        ts: pd.Timestamp,
        alert_kind: str,
        episode_id: str,
        existing_ids: set[Any],
        label: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if episode_id in existing_ids:
            return
        ts_u = _utc(ts)
        et_date = str(panel.loc[ts, "et_date"])
        same_kind = [
            ep
            for ep in self.episodes
            if ep.get("et_date") == et_date
            and str(ep.get("alert_kind") or "range_caution") == alert_kind
        ]
        if same_kind:
            last_lock = _utc(same_kind[-1]["lock_ts_utc"])
            if (ts_u - last_lock) < pd.Timedelta(minutes=DECLUSTER_STRIDE_MIN):
                return
        row = panel.loc[ts]
        episode = {
            "episode_id": episode_id,
            "alert_kind": alert_kind,
            "et_date": et_date,
            "lock_ts_utc": ts_u.isoformat(),
            "lock_price": _finite(row.get("price")),
            "residual": _finite(row.get("v1_residual")),
            "residual_z": _finite(row.get("v1_residual_z")),
            "widening_15m": bool(row.get("residual_widening_15m", False)),
            "large": bool(row.get("large_residual", False)),
            "phase": "LOCKED",
            "outcome": None,
            "net_ticks": None,
            "abs_net_ticks": None,
            "path_eff": None,
            "disp_to_range": None,
            "logged": False,
            "label": label,
        }
        if extra:
            episode.update(extra)
        self.episodes.append(episode)
        existing_ids.add(episode_id)

    def update(self, panel: pd.DataFrame) -> dict[str, Any]:
        """Detect new fires, score matured episodes, return live payload."""
        context = latest_caution_context(panel) if not panel.empty else latest_caution_context(pd.DataFrame())
        with self._lock:
            if not panel.empty and "cautious_range" in panel:
                existing_ids = {ep.get("episode_id") for ep in self.episodes}
                caution_fires = decluster_fire_mask(
                    panel["cautious_range"], DECLUSTER_STRIDE_MIN
                )
                for ts, is_fire in caution_fires.items():
                    if not is_fire:
                        continue
                    ts_u = _utc(ts)
                    self._maybe_append_fire(
                        panel=panel,
                        ts=ts,
                        alert_kind="range_caution",
                        episode_id=ts_u.strftime("%Y%m%dT%H%M%S"),
                        existing_ids=existing_ids,
                        label=LABEL,
                        extra={"widening_15m": True, "large": True},
                    )

                if "lifo_friendly" in panel.columns:
                    lifo_fires = decluster_fire_mask(
                        panel["lifo_friendly"].fillna(False), DECLUSTER_STRIDE_MIN
                    )
                    for ts, is_fire in lifo_fires.items():
                        if not is_fire:
                            continue
                        ts_u = _utc(ts)
                        self._maybe_append_fire(
                            panel=panel,
                            ts=ts,
                            alert_kind="lifo",
                            episode_id=f"lifo_{ts_u.strftime('%Y%m%dT%H%M%S')}",
                            existing_ids=existing_ids,
                            label=LIFO_FRIENDLY_LABEL,
                        )

                # Score matured episodes.
                prices = panel["price"] if "price" in panel else pd.Series(dtype=float)
                for episode in self.episodes:
                    if episode.get("phase") == "FINALIZED":
                        continue
                    lock_ts = _utc(episode["lock_ts_utc"])
                    scored = score_forward_regime(prices, lock_ts, cutoffs=REGIME_CUTOFFS)
                    if scored.get("outcome") == "INCOMPLETE":
                        episode["phase"] = "SCORING"
                        episode["score_status"] = scored
                        continue
                    episode["phase"] = "FINALIZED"
                    episode["outcome"] = scored.get("outcome")
                    episode["net_ticks"] = _finite(scored.get("net_ticks"))
                    episode["abs_net_ticks"] = _finite(scored.get("abs_net_ticks"))
                    episode["path_eff"] = _finite(scored.get("path_eff"))
                    episode["disp_to_range"] = _finite(scored.get("disp_to_range"))
                    episode["score_status"] = scored
                    if not episode.get("logged"):
                        self._append_log(episode)
                        episode["logged"] = True

                self._save()

            try:
                self.append_signal_log(context)
            except Exception:
                pass

            return self.payload(context)

    @staticmethod
    def _outcome_stats(
        episodes: list[dict[str, Any]],
        *,
        label: str,
        include_baseline: bool = False,
    ) -> dict[str, Any]:
        finalized = [
            ep
            for ep in episodes
            if ep.get("phase") == "FINALIZED" and ep.get("outcome")
        ]
        n = len(finalized)
        n_active = sum(1 for ep in episodes if ep.get("phase") != "FINALIZED")
        if n == 0:
            out = {
                "n_finalized": 0,
                "n_active": n_active,
                "range_rate": None,
                "trend_rate": None,
                "ambiguous_rate": None,
                "label": label,
            }
            if include_baseline:
                out["reference_validation_range_baseline"] = REFERENCE_RANGE_BASELINE
            return out
        outcomes = [str(ep.get("outcome")) for ep in finalized]
        n_range = sum(1 for o in outcomes if o == "RANGE")
        n_trend = sum(1 for o in outcomes if o in {"TREND_UP", "TREND_DOWN"})
        n_amb = sum(1 for o in outcomes if o == "AMBIGUOUS")
        out = {
            "n_finalized": n,
            "n_active": n_active,
            "range_rate": n_range / n,
            "trend_rate": n_trend / n,
            "ambiguous_rate": n_amb / n,
            "n_range": n_range,
            "n_trend": n_trend,
            "n_ambiguous": n_amb,
            "label": label,
        }
        if include_baseline:
            out["reference_validation_range_baseline"] = REFERENCE_RANGE_BASELINE
        return out

    def stats(self) -> dict[str, Any]:
        caution = [
            ep
            for ep in self.episodes
            if str(ep.get("alert_kind") or "range_caution") == "range_caution"
        ]
        return self._outcome_stats(caution, label=LABEL, include_baseline=True)

    def lifo_stats(self) -> dict[str, Any]:
        lifo = [ep for ep in self.episodes if ep.get("alert_kind") == "lifo"]
        return self._outcome_stats(lifo, label=LIFO_FRIENDLY_LABEL)

    def payload(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = context or latest_caution_context(pd.DataFrame())
        recent = list(reversed(self.episodes[-20:]))
        active = next(
            (
                ep
                for ep in reversed(self.episodes)
                if ep.get("phase") != "FINALIZED"
                and str(ep.get("alert_kind") or "range_caution") == "range_caution"
            ),
            None,
        )
        active_lifo = next(
            (
                ep
                for ep in reversed(self.episodes)
                if ep.get("phase") != "FINALIZED" and ep.get("alert_kind") == "lifo"
            ),
            None,
        )
        return {
            "success": True,
            "version": "V1.2",
            "strategy_status": STRATEGY_STATUS,
            "label": LABEL,
            "soft_breakout_label": SOFT_BREAKOUT_LABEL,
            "lifo_friendly_label": LIFO_FRIENDLY_LABEL,
            "non_directional": True,
            "context": ctx,
            "active_episode": active,
            "active_lifo_episode": active_lifo,
            "recent_episodes": recent,
            "stats": self.stats(),
            "lifo_stats": self.lifo_stats(),
            "research_context": dict(RESEARCH_CONTEXT),
            "soft_breakout_research": dict(SOFT_BREAKOUT_RESEARCH),
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
        # Prefer et_date match when present.
        filtered = [r for r in rows if str(r.get("et_date") or "").startswith(today[:10])]
        if filtered:
            return filtered
        return [r for r in rows if today.replace("-", "") in str(r.get("episode_id") or "")]
