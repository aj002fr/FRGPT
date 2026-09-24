"""E13 fair-value and residual dynamics (causal V2 panel features)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE
from scripts.move_exhaustion.simulation.policies import _ts

LOOKBACK_BARS = 5


def residual_dynamics_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
    symbol: str = "TY",
) -> dict[str, float | None | str]:
    """Causal residual / FV velocities and gap-closure share at event start."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars + 1)
    empty = {
        "residual_z": None,
        "residual": None,
        "residual_z_velocity": None,
        "fair_value_velocity": None,
        "price_velocity": None,
        "price_share_so_far": None,
        "fv_share_so_far": None,
        "gap_closure_ticks": None,
        "model_fair_value_disagreement": None,
        "event_vs_residual_alignment": None,
        "dynamics_category": "unknown",
    }
    if len(hist) < 2 or "fair_value" not in hist.columns:
        return empty

    px = pd.to_numeric(hist["price"], errors="coerce")
    fv = pd.to_numeric(hist["fair_value"], errors="coerce")
    resid = pd.to_numeric(hist["residual"], errors="coerce")
    if "residual_z" in hist.columns:
        rz = pd.to_numeric(hist["residual_z"], errors="coerce")
    else:
        rz = pd.Series(np.nan, index=hist.index)

    if not np.isfinite(px.iloc[-1]) or not np.isfinite(fv.iloc[-1]):
        return empty

    bars = max(len(hist) - 1, 1)
    d_price = float(px.iloc[-1] - px.iloc[0])
    d_fv = float(fv.iloc[-1] - fv.iloc[0])
    price_vel = d_price / tick / bars  # ticks per bar
    fv_vel = d_fv / tick / bars

    rz_now = float(rz.iloc[-1]) if np.isfinite(rz.iloc[-1]) else None
    rz_prev = float(rz.iloc[0]) if np.isfinite(rz.iloc[0]) else None
    rz_vel = (rz_now - rz_prev) / bars if rz_now is not None and rz_prev is not None else None

    r0 = float(resid.iloc[0]) if np.isfinite(resid.iloc[0]) else float(px.iloc[0] - fv.iloc[0])
    rt = float(resid.iloc[-1]) if np.isfinite(resid.iloc[-1]) else float(px.iloc[-1] - fv.iloc[-1])
    # Residual = price - FV. Closing a positive residual: price down or FV up.
    # Price contribution to closing toward 0 from r0: -sign(r0)*d_price
    # FV contribution: +sign(r0)*d_fv
    sgn = 1.0 if r0 >= 0 else -1.0
    price_close = float(-sgn * d_price / tick)  # positive => price helped close gap
    fv_close = float(sgn * d_fv / tick)  # positive => FV helped close gap
    total_close = price_close + fv_close
    gap_closure = float(abs(r0) - abs(rt)) / tick  # positive => |residual| shrank

    if abs(total_close) > EPS:
        price_share = float(price_close / abs(total_close)) if total_close != 0 else None
        # Clamp display share to [0,1] when both contribute positively to closure
        if price_close >= 0 and fv_close >= 0 and (price_close + fv_close) > EPS:
            price_share = float(price_close / (price_close + fv_close))
            fv_share = float(fv_close / (price_close + fv_close))
        else:
            fv_share = float(fv_close / abs(total_close))
    else:
        price_share = None
        fv_share = None

    # Alignment: residual sign vs event direction (fade = residual opposes? 
    # V2 fade: residual_z aligns with event for fade-with-V2 means residual same sign as overshoot)
    alignment = None
    if rz_now is not None:
        alignment = float(direction * np.sign(rz_now))

    # Only one FV model on panel — disagreement unavailable
    disagreement = None

    category = classify_dynamics(
        r0=r0,
        price_vel_ticks=price_vel,
        fv_vel_ticks=fv_vel,
        gap_closure_ticks=gap_closure,
    )

    return {
        "residual_z": rz_now,
        "residual": float(rt),
        "residual_z_velocity": rz_vel,
        "fair_value_velocity": float(fv_vel),
        "price_velocity": float(price_vel),
        "price_share_so_far": price_share,
        "fv_share_so_far": fv_share,
        "gap_closure_ticks": gap_closure,
        "price_closure_ticks": price_close,
        "fv_closure_ticks": fv_close,
        "model_fair_value_disagreement": disagreement,
        "event_vs_residual_alignment": alignment,
        "abs_residual_z": abs(rz_now) if rz_now is not None else None,
        "dynamics_category": category,
    }


def classify_dynamics(
    *,
    r0: float,
    price_vel_ticks: float,
    fv_vel_ticks: float,
    gap_closure_ticks: float,
) -> str:
    """Bucket how price vs FV are moving relative to the residual gap."""
    # Price reverting toward FV: price moving opposite residual sign
    price_reverting = (-np.sign(r0) * price_vel_ticks) > 0.02
    # FV chasing price: FV moving same direction as residual (toward price)
    fv_chasing = (np.sign(r0) * fv_vel_ticks) > 0.02
    # Both closing: both contribute positively
    both = price_reverting and fv_chasing
    widening = gap_closure_ticks < -0.25

    if widening and not price_reverting:
        return "gap_continuing_to_widen"
    if both:
        return "both_moving_toward_each_other"
    if price_reverting and abs(fv_vel_ticks) <= 0.02:
        return "price_reverting_toward_stable_fair_value"
    if fv_chasing and not price_reverting:
        return "fair_value_chasing_price"
    if price_reverting:
        return "price_reverting_toward_stable_fair_value"
    if fv_chasing:
        return "fair_value_chasing_price"
    return "mixed_or_quiet"


def attractive_reversion_flag(row: dict[str, Any], *, z_thresh: float = 2.0) -> bool:
    """Extreme residual + price beginning to revert + FV stable/slow."""
    az = row.get("abs_residual_z")
    cat = row.get("dynamics_category")
    fv_vel = row.get("fair_value_velocity")
    if az is None or not np.isfinite(float(az)):
        return False
    if float(az) < z_thresh:
        return False
    if cat != "price_reverting_toward_stable_fair_value":
        return False
    if fv_vel is None or abs(float(fv_vel)) > 0.05:
        return False
    return True
