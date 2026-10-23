"""E04 flow-dominance categories using train-only quantiles.

Lightweight Phase-1 version of state-dependent impact:
expected progress is proportional to directional flow with a train-fit
scale (median ticks per unit |flow|). Full GAM/boosted impact model is E07.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE
from scripts.move_exhaustion.simulation.policies import _day_slice, _ts


def event_flow_price_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = 5,
    symbol: str = "TY",
) -> dict[str, float | None]:
    """Causal features at event start: directional progress vs directional flow."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars + 1)
    if len(hist) < 2:
        return {
            "directional_progress_ticks": None,
            "directional_flow": None,
            "impact_efficiency": None,
            "abs_flow": None,
        }
    direction = int(event["event_direction"])
    px = pd.to_numeric(hist["price"], errors="coerce")
    progress = direction * (float(px.iloc[-1]) - float(px.iloc[0])) / tick
    flow_col = "agg_5m" if "agg_5m" in hist.columns else "agg_1m"
    # Use last bar's rolling flow as proxy for lookback aggression
    flow = direction * float(pd.to_numeric(hist[flow_col], errors="coerce").iloc[-1])
    efficiency = progress / max(flow, EPS) if flow > 0 else (np.nan if progress > 0 else 0.0)
    return {
        "directional_progress_ticks": float(progress),
        "directional_flow": float(flow),
        "impact_efficiency": float(efficiency) if np.isfinite(efficiency) else None,
        "abs_flow": float(abs(flow)),
    }


def fit_category_thresholds(train_feats: pd.DataFrame) -> dict[str, float]:
    """Train-only quantiles for classification."""
    flow = pd.to_numeric(train_feats["abs_flow"], errors="coerce").dropna()
    eff = pd.to_numeric(train_feats["impact_efficiency"], errors="coerce").dropna()
    prog = pd.to_numeric(train_feats["directional_progress_ticks"], errors="coerce").dropna()
    return {
        "flow_hi": float(flow.quantile(0.67)) if len(flow) else 0.0,
        "flow_lo": float(flow.quantile(0.33)) if len(flow) else 0.0,
        "eff_hi": float(eff.quantile(0.67)) if len(eff) else 0.0,
        "eff_lo": float(eff.quantile(0.33)) if len(eff) else 0.0,
        "progress_hi": float(prog.quantile(0.67)) if len(prog) else 0.0,
        "n_train": int(len(train_feats)),
    }


def classify_flow_dominance(row: dict[str, Any], thr: dict[str, float]) -> str:
    """Assign aggression / absorption / price-without-flow / mixed."""
    flow = row.get("abs_flow")
    eff = row.get("impact_efficiency")
    prog = row.get("directional_progress_ticks")
    if flow is None or prog is None:
        return "mixed"
    flow = float(flow)
    prog = float(prog)
    eff = float(eff) if eff is not None and np.isfinite(eff) else None

    high_flow = flow >= thr["flow_hi"]
    low_flow = flow <= thr["flow_lo"]
    high_prog = prog >= thr["progress_hi"]
    high_eff = eff is not None and eff >= thr["eff_hi"]
    low_eff = eff is not None and eff <= thr["eff_lo"]

    if high_flow and high_eff:
        return "aggression_dominant"
    if high_flow and low_eff:
        return "apparent_absorption_dominant"
    if low_flow and high_prog:
        return "price_without_flow"
    return "mixed"


def additional_extension_ticks(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    symbol: str = "TY",
    horizon_minutes: int = 15,
) -> float | None:
    """Max further move in event direction after start before horizon."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    day = _day_slice(panel, str(event["et_date"]), start)
    if day.empty:
        return None
    direction = int(event["event_direction"])
    p0 = float(day["price"].iloc[0])
    window = day[day["bucket_ts"] <= start + pd.Timedelta(minutes=horizon_minutes)]
    if window.empty:
        return None
    px = pd.to_numeric(window["price"], errors="coerce")
    if direction > 0:
        return float((px.max() - p0) / tick)
    return float((p0 - px.min()) / tick)


def time_to_label_minutes(
    panel: pd.DataFrame,
    event: dict[str, Any],
) -> float | None:
    """Minutes from event start to first_barrier hit_ts if present in event row."""
    if not event.get("first_barrier") or event.get("label") not in (0, 1):
        # Approximate using end time if labeled elsewhere
        return None
    # Prefer explicit hit from re-score in runner
    return None
