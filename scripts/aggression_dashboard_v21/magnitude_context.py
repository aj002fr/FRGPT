"""Display-only, non-directional magnitude context for isolated V2.1.

Guardrail: pure computation only. No imports from V1/V2/V3 apps, no I/O, no
alert-direction changes, and no live-store writes.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TICK_SIZE = 1.0 / 64.0
LOOKBACK_BARS = 15
VOL_P70 = 0.0129490926588241
RANGE_P70_TICKS = 3.0
LABEL = "PROVISIONAL / NON-DIRECTIONAL"
VALIDATED_CONTEXT = {
    "holdout_days": 9,
    "big_move_rate_baseline": 0.13815789473684212,
    "big_move_rate_elevated": 0.26229508196721313,
    "resolution_rate_baseline": 0.3989071038251366,
    "resolution_rate_elevated": 0.5245901639344263,
    "directional_pnl_validated": False,
}


def _empty() -> dict[str, Any]:
    return {
        "state": "NORMAL",
        "ready": False,
        "prior10_realized_vol": None,
        "prior10_range_ticks": None,
        "lookback_bars": LOOKBACK_BARS,
        "reasons": [],
        "thresholds": {
            "realized_vol_p70": VOL_P70,
            "range_p70_ticks": RANGE_P70_TICKS,
        },
        "validated_context": dict(VALIDATED_CONTEXT),
        "label": LABEL,
        "non_directional": True,
    }


def magnitude_context(model_df: pd.DataFrame) -> dict[str, Any]:
    """Return the Phase-1 magnitude-state annotation for the latest live row."""
    if model_df.empty or "price" not in model_df:
        return _empty()
    prices = pd.to_numeric(model_df["price"], errors="coerce").dropna().tail(LOOKBACK_BARS)
    if len(prices) < LOOKBACK_BARS:
        return _empty()
    returns = prices.diff().dropna()
    realized_vol = float(returns.std(ddof=1)) if len(returns) >= 2 else np.nan
    range_ticks = float((prices.max() - prices.min()) / TICK_SIZE)
    if not np.isfinite(realized_vol) or not np.isfinite(range_ticks):
        return _empty()
    reasons = []
    if realized_vol >= VOL_P70:
        reasons.append("prior-window realized volatility above selection p70")
    if range_ticks >= RANGE_P70_TICKS:
        reasons.append("prior-window price range at or above selection p70")
    return {
        "state": "ELEVATED" if reasons else "NORMAL",
        "ready": True,
        "prior10_realized_vol": realized_vol,
        "prior10_range_ticks": range_ticks,
        "lookback_bars": LOOKBACK_BARS,
        "reasons": reasons,
        "thresholds": {
            "realized_vol_p70": VOL_P70,
            "range_p70_ticks": RANGE_P70_TICKS,
        },
        "validated_context": dict(VALIDATED_CONTEXT),
        "label": LABEL,
        "non_directional": True,
    }


__all__ = [
    "LABEL",
    "LOOKBACK_BARS",
    "RANGE_P70_TICKS",
    "VALIDATED_CONTEXT",
    "VOL_P70",
    "magnitude_context",
]
