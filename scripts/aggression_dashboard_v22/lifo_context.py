"""Display-only LIFO FRIENDLY annotation for isolated V2.2.

Uses V2 causal residual_z + trade_intensity with the frozen H5 rule:
|z| >= 1 AND trade_intensity <= session expanding p33 → range lean.

UI should show only "LIFO FRIENDLY" — no factor callouts.
Not long/short. Not executable PnL.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v12.range_caution_context import (
    DECLUSTER_STRIDE_MIN,
    FAST_EXPANDING_MIN,
    HORIZON_MIN,
    LARGE_ABS_Z,
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    REGIME_CUTOFFS,
    STRATEGY_STATUS,
    TRADE_INT_LO_QUANTILE,
    decluster_fire_mask,
    score_forward_regime,
)

__all__ = [
    "DECLUSTER_STRIDE_MIN",
    "HORIZON_MIN",
    "LARGE_ABS_Z",
    "LIFO_FRIENDLY_LABEL",
    "LIFO_FRIENDLY_RESEARCH",
    "REGIME_CUTOFFS",
    "STRATEGY_STATUS",
    "annotate_lifo_flags",
    "decluster_fire_mask",
    "latest_lifo_context",
    "score_forward_regime",
]


def annotate_lifo_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add lifo_friendly flags from V2 residual_z + trade_intensity."""
    out = df.copy()
    if "et_date" not in out.columns:
        idx = pd.DatetimeIndex(out.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        out["et_date"] = idx.tz_convert("America/New_York").strftime("%Y-%m-%d")

    z = pd.to_numeric(out.get("residual_z"), errors="coerce")
    out["large_residual"] = (z.abs() >= LARGE_ABS_Z).fillna(False)

    if "trade_intensity" not in out.columns:
        out["trade_intensity"] = np.nan
        out["trade_int_lo"] = False
        out["lifo_friendly"] = False
        return out

    trade_int = pd.to_numeric(out["trade_intensity"], errors="coerce")
    out["trade_intensity"] = trade_int
    lo_thr = trade_int.groupby(out["et_date"], sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=FAST_EXPANDING_MIN).quantile(TRADE_INT_LO_QUANTILE)
    )
    out["trade_int_lo"] = (trade_int <= lo_thr).fillna(False)
    out["lifo_friendly"] = out["large_residual"].fillna(False) & out["trade_int_lo"]
    return out


def latest_lifo_context(df: pd.DataFrame) -> dict[str, Any]:
    """Latest-bar LIFO FRIENDLY state for the API/UI."""
    empty = {
        "state": "NORMAL",
        "active": False,
        "residual_z": None,
        "trade_intensity": None,
        "trade_int_lo": False,
        "large": False,
        "label": LIFO_FRIENDLY_LABEL,
        "research_context": dict(LIFO_FRIENDLY_RESEARCH),
        "non_directional": True,
        "alert_text": "",
        "strategy_status": STRATEGY_STATUS,
    }
    if df.empty or "lifo_friendly" not in df.columns:
        return empty
    row = df.dropna(subset=["residual_z"]).iloc[-1] if "residual_z" in df else df.iloc[-1]
    lifo = bool(row.get("lifo_friendly", False))
    trade_int_val = row.get("trade_intensity", np.nan)
    z_val = row.get("residual_z", np.nan)
    return {
        "state": "LIFO_FRIENDLY" if lifo else "NORMAL",
        "active": lifo,
        "residual_z": float(z_val) if np.isfinite(z_val) else None,
        "trade_intensity": float(trade_int_val) if np.isfinite(trade_int_val) else None,
        "trade_int_lo": bool(row.get("trade_int_lo", False)),
        "large": bool(row.get("large_residual", False)),
        "label": LIFO_FRIENDLY_LABEL,
        "research_context": dict(LIFO_FRIENDLY_RESEARCH),
        "non_directional": True,
        "alert_text": "LIFO FRIENDLY" if lifo else "",
        "strategy_status": STRATEGY_STATUS,
    }
