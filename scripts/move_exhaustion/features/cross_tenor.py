"""E14 cross-tenor confirmation features.

Frozen TY TBBO panel has FV/TU/US/TY agg_5m flow but not neighbour mid prices.
Price-breadth / return-PCA features are marked unavailable; flow confirmation
is the Phase-1 implementable proxy.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS
from scripts.move_exhaustion.simulation.policies import _ts

NEIGHBORS = ("FV", "TU", "US")
LOOKBACK_BARS = 5


def cross_tenor_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
) -> dict[str, float | None | str | bool]:
    """Causal cross-tenor flow confirmation at event start (TY target)."""
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars + 1)
    empty = {
        "curve_flow_breadth": None,
        "curve_price_breadth": None,
        "number_of_confirming_tenors": None,
        "number_of_confirming_tenors_lag1": None,
        "target_flow": None,
        "target_minus_flow_level": None,
        "target_minus_level_factor": None,
        "target_minus_slope_curvature_fit": None,
        "price_features_available": False,
        "confirmation_category": "unknown",
        "target_first_mover_flow": None,
        "target_last_mover_flow": None,
    }
    if len(hist) < 2:
        return empty

    def _col(name: str) -> str | None:
        c = f"{name}_agg_5m"
        return c if c in hist.columns else None

    ty_col = _col("TY") or ("agg_5m" if "agg_5m" in hist.columns else "agg_1m")
    ty_flow = direction * pd.to_numeric(hist[ty_col], errors="coerce").fillna(0.0)
    ty_now = float(ty_flow.iloc[-1])
    ty_prev = float(ty_flow.iloc[-2]) if len(ty_flow) >= 2 else 0.0

    neighbor_now = {}
    neighbor_prev = {}
    for n in NEIGHBORS:
        c = _col(n)
        if c is None:
            continue
        s = direction * pd.to_numeric(hist[c], errors="coerce").fillna(0.0)
        neighbor_now[n] = float(s.iloc[-1])
        neighbor_prev[n] = float(s.iloc[-2]) if len(s) >= 2 else 0.0

    if not neighbor_now:
        return empty

    confirming = [v for v in neighbor_now.values() if v > 0]
    confirming_lag = [v for v in neighbor_prev.values() if v > 0]
    n_conf = len(confirming)
    n_conf_lag = len(confirming_lag)
    breadth = float(n_conf / max(len(neighbor_now), 1))
    flow_level = float(np.mean(list(neighbor_now.values())))
    target_minus_level = float(ty_now - flow_level)

    # Crude slope proxy: US - TU (long vs short end) when both present
    slope_fit = None
    if "US" in neighbor_now and "TU" in neighbor_now:
        slope = neighbor_now["US"] - neighbor_now["TU"]
        # residual of TY vs average of neighbors already in target_minus_level;
        # slope residual: TY vs interpolated by duration order TU < FV < TY < US
        # Use TY - 0.5*(FV+US) if FV present else TY - mean
        if "FV" in neighbor_now:
            interp = 0.5 * (neighbor_now["FV"] + neighbor_now["US"])
            slope_fit = float(ty_now - interp)
        else:
            slope_fit = target_minus_level

    # First/last mover: compare when target flow turned positive vs neighbors (lag window)
    # Over lookback, first bar where each series > 0
    def _first_pos(series: pd.Series) -> int | None:
        for i, v in enumerate(series.to_numpy(dtype=float)):
            if v > 0:
                return i
        return None

    ty_first = _first_pos(ty_flow)
    neigh_firsts = []
    for n in neighbor_now:
        c = _col(n)
        s = direction * pd.to_numeric(hist[c], errors="coerce").fillna(0.0)
        fi = _first_pos(s)
        if fi is not None:
            neigh_firsts.append(fi)
    target_first = bool(
        ty_first is not None and (not neigh_firsts or ty_first < min(neigh_firsts))
    )
    target_last = bool(
        ty_first is not None and neigh_firsts and ty_first > max(neigh_firsts)
    )

    # Categories (mutually preferential)
    ty_pos = ty_now > EPS
    n_tot = len(neighbor_now)
    if ty_pos and n_conf == 0:
        cat = "target_moves_alone"
    elif ty_pos and n_conf == n_tot:
        cat = "all_tenors_confirm_flow"
    elif target_first and ty_pos and n_conf >= 1:
        cat = "target_is_first_tenor_to_move"
    elif target_last and ty_pos and n_conf >= 1:
        cat = "target_is_last_tenor_to_move"
    elif ty_pos and n_conf >= 1:
        cat = "partial_flow_confirmation"
    elif (not ty_pos) and n_conf >= 1:
        cat = "neighbors_confirm_target_weak"
    else:
        cat = "mixed_or_quiet"

    return {
        "curve_flow_breadth": breadth,
        "curve_price_breadth": None,  # unavailable on panel
        "number_of_confirming_tenors": float(n_conf),
        "number_of_confirming_tenors_lag1": float(n_conf_lag),
        "target_flow": ty_now,
        "neighbor_flow_level": flow_level,
        "target_minus_flow_level": target_minus_level,
        "target_minus_level_factor": None,  # needs return PCA
        "target_minus_slope_curvature_fit": slope_fit,
        "price_features_available": False,
        "confirmation_category": cat,
        "target_first_mover_flow": target_first,
        "target_last_mover_flow": target_last,
        "n_neighbors": float(len(neighbor_now)),
        "target_flow_delta": float(ty_now - ty_prev),
    }


# Duration-unit DV01 (contracts ≈ signed_DV01 / unit_DV01). Matches aggression_dv01.json.
DEFAULT_UNIT_DV01 = {"FV": 42.0, "TU": 36.0, "TY": 85.0, "US": 125.0}


def cross_tenor_flow_confirmation_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
    unit_dv01: dict[str, float] | None = None,
) -> dict[str, float | None | str | bool]:
    """E15: duration-normalised signed flow factors at event start.

    Hypothesis: curve-wide aggressive flow supports continuation; target-only
    residual flow is easier to absorb (higher exhaustion).
    """
    units = unit_dv01 or DEFAULT_UNIT_DV01
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars + 1)
    empty = {
        "target_directional_flow": None,
        "curve_directional_flow_factor": None,
        "target_residual_flow": None,
        "lagged_neighbouring_flow": None,
        "curve_flow_breadth_norm": None,
        "n_confirming_tenors_norm": None,
        "flow_regime": "unknown",
        "price_features_available": False,
    }
    if len(hist) < 2:
        return empty

    def _agg(name: str) -> str | None:
        c = f"{name}_agg_5m"
        if c in hist.columns:
            return c
        if name == "TY" and "agg_5m" in hist.columns:
            return "agg_5m"
        if name == "TY" and "agg_1m" in hist.columns:
            return "agg_1m"
        return None

    def _norm(name: str, col: str, lag: int = 0) -> float:
        raw = pd.to_numeric(hist[col], errors="coerce").fillna(0.0)
        idx = -1 - lag
        if abs(idx) > len(raw):
            return 0.0
        u = float(units.get(name, 1.0))
        return float(direction * float(raw.iloc[idx]) / max(u, EPS))

    ty_col = _agg("TY")
    if ty_col is None:
        return empty
    target = _norm("TY", ty_col, 0)

    neigh_now: dict[str, float] = {}
    neigh_lag: dict[str, float] = {}
    for n in NEIGHBORS:
        c = _agg(n)
        if c is None:
            continue
        neigh_now[n] = _norm(n, c, 0)
        neigh_lag[n] = _norm(n, c, 1)

    if not neigh_now:
        return empty

    curve = float(np.mean(list(neigh_now.values())))
    residual = float(target - curve)
    lagged = float(np.sum(list(neigh_lag.values())))
    confirming = [v for v in neigh_now.values() if v > 0]
    breadth = float(len(confirming) / max(len(neigh_now), 1))

    # Regime: target-only vs curve-wide (relative magnitudes, causal at start)
    abs_t = abs(target)
    abs_c = abs(curve)
    if target > EPS and abs_c <= 0.25 * max(abs_t, EPS) and len(confirming) == 0:
        regime = "target_only"
    elif target > EPS and curve > EPS and breadth >= 0.66:
        regime = "curve_wide"
    elif target > EPS and curve > EPS:
        regime = "partial_curve"
    elif target > EPS and curve <= EPS:
        regime = "target_against_curve"
    else:
        regime = "mixed_or_quiet"

    return {
        "target_directional_flow": target,
        "curve_directional_flow_factor": curve,
        "target_residual_flow": residual,
        "lagged_neighbouring_flow": lagged,
        "curve_flow_breadth_norm": breadth,
        "n_confirming_tenors_norm": float(len(confirming)),
        "flow_regime": regime,
        "price_features_available": False,
        "abs_target_flow": abs_t,
        "abs_curve_flow": abs_c,
    }
