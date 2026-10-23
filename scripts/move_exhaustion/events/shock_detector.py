"""Causal move-event detectors (Phase 1 on 1m panel).

Spec L∈{30s,60s,120s,300s}; on the frozen 1m grid we use L_bars∈{1,2,5}
and document the approximation in the E01 manifest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE

PRIMARY_MOVE_Z = 2.5
PRIMARY_FLOW_Z = 1.5
PRIMARY_RESIDUAL_Z = 2.0
BREAKOUT_LOOKBACK_BARS = 30


def _causal_robust_z_by_date(df: pd.DataFrame, values: pd.Series) -> pd.Series:
    parts: list[pd.Series] = []
    for _, idx in df.groupby("et_date", sort=False).groups.items():
        x = pd.to_numeric(values.loc[idx], errors="coerce")
        past = x.shift(1)
        med = past.expanding(min_periods=20).median()
        mad = (past - med).abs().expanding(min_periods=20).median()
        scale = (1.4826 * mad).replace(0.0, np.nan)
        std = past.expanding(min_periods=20).std(ddof=1)
        scale = scale.fillna(std).clip(lower=EPS)
        parts.append((x - med) / scale)
    return pd.concat(parts).sort_index()


def annotate_move_features(df: pd.DataFrame, *, symbol: str = "TY") -> pd.DataFrame:
    """Add causal move/flow z features used by detectors."""
    out = df.sort_values(["et_date", "bucket_ts"]).reset_index(drop=True)
    tick = TICK_SIZE[symbol]
    price = pd.to_numeric(out["price"], errors="coerce")
    out["move_ticks_1m"] = price.groupby(out["et_date"]).diff(1) / tick
    out["move_ticks_2m"] = price.groupby(out["et_date"]).diff(2) / tick
    out["move_ticks_5m"] = price.groupby(out["et_date"]).diff(5) / tick
    out["move_z_1m"] = _causal_robust_z_by_date(out, out["move_ticks_1m"])
    out["move_z_2m"] = _causal_robust_z_by_date(out, out["move_ticks_2m"])
    out["move_z_5m"] = _causal_robust_z_by_date(out, out["move_ticks_5m"])

    flow_col = "agg_5m" if "agg_5m" in out.columns else "agg_1m"
    out["signed_flow_5m"] = pd.to_numeric(out[flow_col], errors="coerce")
    out["flow_z_5m"] = _causal_robust_z_by_date(out, out["signed_flow_5m"])

    flags = []
    for _, day in out.groupby("et_date", sort=False):
        px = pd.to_numeric(day["price"], errors="coerce")
        prior = px.shift(1)
        hi = prior.rolling(BREAKOUT_LOOKBACK_BARS, min_periods=10).max()
        lo = prior.rolling(BREAKOUT_LOOKBACK_BARS, min_periods=10).min()
        flags.append(((px >= hi) | (px <= lo)).fillna(False))
    out["new_30m_extreme"] = pd.concat(flags).sort_index().astype(bool)
    return out


def detect_triggers(
    df: pd.DataFrame,
    *,
    detector: str,
    move_z_col: str = "move_z_5m",
    move_z_threshold: float = PRIMARY_MOVE_Z,
    flow_z_threshold: float = PRIMARY_FLOW_Z,
    residual_z_threshold: float = PRIMARY_RESIDUAL_Z,
) -> pd.DataFrame:
    """Return raw threshold fires (not yet de-duplicated into events)."""
    work = df.copy()
    move_z = pd.to_numeric(work[move_z_col], errors="coerce")
    flow_z = pd.to_numeric(work["flow_z_5m"], errors="coerce")
    resid_z = pd.to_numeric(work["residual_z"], errors="coerce")
    direction = np.sign(pd.to_numeric(work["move_ticks_5m"], errors="coerce"))

    if detector == "A_price_shock":
        fire = move_z.abs() >= move_z_threshold
    elif detector == "B_price_plus_flow":
        dir_flow = direction * flow_z
        fire = (move_z.abs() >= 2.0) & (dir_flow >= flow_z_threshold)
    elif detector == "C_v2_residual_shock":
        fire = resid_z.abs() >= residual_z_threshold
        direction = np.sign(resid_z)
    elif detector == "D_breakout_velocity":
        fire = work["new_30m_extreme"].fillna(False) & (move_z.abs() >= 2.0)
    else:
        raise ValueError(f"Unknown detector {detector}")

    out = work.loc[fire.fillna(False)].copy()
    out["detector"] = detector
    out["event_direction"] = direction.loc[out.index]
    out = out[out["event_direction"].notna() & (out["event_direction"] != 0)]
    return out
