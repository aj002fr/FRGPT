"""E06 marginal impact-efficiency and decay features.

On the frozen 1m panel, horizons below 60s collapse to 1 bar and are
documented as approximations. Decay uses early vs recent thirds of a
causal window ending at observation_time (default: event start + 5m).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE
from scripts.move_exhaustion.features.per_tick import PROGRESS_FLOOR_TICKS
from scripts.move_exhaustion.simulation.policies import _ts

# Spec names → bars on 1m grid
HORIZON_BARS = {
    "impact_efficiency_5s": 1,  # approx
    "impact_efficiency_30s": 1,  # approx
    "impact_efficiency_1m": 1,
    "impact_efficiency_5m": 5,
}


def _efficiency(progress_ticks: float, directional_flow: float) -> float | None:
    # Spec allows signed progress; callers may NaN-out retracements separately.
    if directional_flow > 0:
        return float(progress_ticks / max(directional_flow, EPS))
    if progress_ticks > 0:
        return None
    return 0.0


def _segment_metrics(
    seg: pd.DataFrame, *, direction: int, tick: float
) -> tuple[float, float, float | None, float]:
    px = pd.to_numeric(seg["price"], errors="coerce")
    progress = direction * (float(px.iloc[-1]) - float(px.iloc[0])) / tick
    flow_col = "agg_1m" if "agg_1m" in seg.columns else "agg_5m"
    flows = direction * pd.to_numeric(seg[flow_col], errors="coerce").fillna(0.0)
    dir_flow = float(np.clip(flows.to_numpy(dtype=float), 0.0, None).mean())
    return progress, dir_flow, _efficiency(progress, dir_flow), _dv01_per_tick(progress, dir_flow)


def _dv01_per_tick(progress_ticks: float, directional_flow: float) -> float:
    denom = max(max(progress_ticks, 0.0), PROGRESS_FLOOR_TICKS)
    return float(max(directional_flow, 0.0) / denom)


def impact_efficiency_at_horizon(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    bars: int,
    observation_time: Any | None = None,
    symbol: str = "TY",
) -> dict[str, float | None]:
    """Causal impact efficiency over the last `bars` ending at observation_time."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    obs = _ts(observation_time) if observation_time is not None else start
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= obs].tail(bars + 1)
    if len(hist) < 2:
        return {
            "directional_progress_ticks": None,
            "directional_flow": None,
            "impact_efficiency": None,
            "dv01_per_tick": None,
        }
    direction = int(event["event_direction"])
    px = pd.to_numeric(hist["price"], errors="coerce")
    progress = direction * (float(px.iloc[-1]) - float(px.iloc[0])) / tick
    flow_col = "agg_1m" if "agg_1m" in hist.columns else "agg_5m"
    # Mean same-direction rolling agg over the window as flow proxy
    flows = direction * pd.to_numeric(hist[flow_col], errors="coerce").fillna(0.0)
    dir_flow = float(np.clip(flows.to_numpy(dtype=float), 0.0, None).mean())
    return {
        "directional_progress_ticks": float(progress),
        "directional_flow": dir_flow,
        "impact_efficiency": _efficiency(progress, dir_flow),
        "dv01_per_tick": _dv01_per_tick(progress, dir_flow),
    }


def impact_efficiency_decay_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    observation_time: Any | None = None,
    landmark_minutes: int = 5,
    min_bars: int = 6,
    symbol: str = "TY",
) -> dict[str, float | None | str]:
    """Early vs recent third efficiency inside [event_start, observation_time].

    Default observation = event_start + landmark_minutes. Uses only rows
    with bucket_ts <= observation_time (causal).
    """
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    obs = (
        _ts(observation_time)
        if observation_time is not None
        else start + pd.Timedelta(minutes=landmark_minutes)
    )
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    window = day[(day["bucket_ts"] >= start) & (day["bucket_ts"] <= obs)]
    out: dict[str, float | None | str] = {
        "observation_time": str(obs),
        "n_bars_in_window": float(len(window)),
        "impact_efficiency_early": None,
        "impact_efficiency_recent": None,
        "impact_efficiency_decay": None,
        "impact_efficiency_ratio": None,
        "flow_per_tick_increase": None,
        "dv01_per_tick_early": None,
        "dv01_per_tick_recent": None,
        "path_window": f"event_start_to_plus_{landmark_minutes}m",
    }
    if len(window) < min_bars:
        return out

    direction = int(event["event_direction"])
    n = len(window)
    third = max(2, n // 3)
    early = window.iloc[:third]
    recent = window.iloc[-third:]

    p_e, f_e, eff_e, dpt_e = _segment_metrics(early, direction=direction, tick=tick)
    p_r, f_r, eff_r, dpt_r = _segment_metrics(recent, direction=direction, tick=tick)
    # Full-window progress for retrace flag (tautology guard)
    px_all = pd.to_numeric(window["price"], errors="coerce")
    full_progress = direction * (float(px_all.iloc[-1]) - float(px_all.iloc[0])) / tick
    out["window_progress_ticks"] = float(full_progress)
    out["already_retracing"] = bool(full_progress < 0)
    out["impact_efficiency_early"] = eff_e
    out["impact_efficiency_recent"] = eff_r
    out["dv01_per_tick_early"] = dpt_e
    out["dv01_per_tick_recent"] = dpt_r
    if eff_e is not None and eff_r is not None:
        out["impact_efficiency_decay"] = float(eff_r - eff_e)
        out["impact_efficiency_ratio"] = float(eff_r / max(abs(eff_e), EPS))
    out["flow_per_tick_increase"] = float(dpt_r / max(dpt_e, EPS))
    out["early_progress_ticks"] = float(p_e)
    out["recent_progress_ticks"] = float(p_r)
    out["early_directional_flow"] = float(f_e)
    out["recent_directional_flow"] = float(f_r)
    return out


def horizon_efficiency_bundle(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    observation_time: Any | None = None,
    symbol: str = "TY",
) -> dict[str, float | None]:
    """Named horizon efficiencies at observation_time (default event start)."""
    obs = observation_time if observation_time is not None else event["event_start_time"]
    feats: dict[str, float | None] = {}
    seen_bars: dict[int, dict[str, float | None]] = {}
    for name, bars in HORIZON_BARS.items():
        if bars not in seen_bars:
            seen_bars[bars] = impact_efficiency_at_horizon(
                panel, event, bars=bars, observation_time=obs, symbol=symbol
            )
        feats[name] = seen_bars[bars]["impact_efficiency"]
        feats[f"{name}_progress"] = seen_bars[bars]["directional_progress_ticks"]
        feats[f"{name}_flow"] = seen_bars[bars]["directional_flow"]
    # Convenience aliases used in ablation
    one = seen_bars[1]
    feats["price_progress_1m"] = one["directional_progress_ticks"]
    feats["directional_flow_1m"] = one["directional_flow"]
    return feats


def classify_efficiency_regime(decay: float | None, thr: dict[str, float]) -> str:
    """Train-quantile regimes for decaying vs improving efficiency."""
    if decay is None or not np.isfinite(decay):
        return "unknown"
    if decay <= thr["q20"]:
        return "extremely_declining"
    if decay <= thr["q40"]:
        return "moderately_declining"
    if decay < thr["q60"]:
        return "stable"
    return "improving"


def fit_decay_thresholds(train_decay: pd.Series) -> dict[str, float]:
    x = pd.to_numeric(train_decay, errors="coerce").dropna()
    if x.empty:
        return {"q20": 0.0, "q40": 0.0, "q60": 0.0, "n_train": 0.0}
    return {
        "q20": float(x.quantile(0.20)),
        "q40": float(x.quantile(0.40)),
        "q60": float(x.quantile(0.60)),
        "n_train": float(len(x)),
    }
