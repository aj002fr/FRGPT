"""E09 large-vs-small trade composition (1m panel approximations).

Without trade-print size buckets, approximate large/small directional flow
via panel large_trade_share × signed agg flow.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS
from scripts.move_exhaustion.simulation.policies import _ts

LOOKBACK_BARS = 10


def large_small_composition_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
) -> dict[str, float | None]:
    """Causal large/small composition at event start."""
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars)
    empty = {
        "large_directional_flow": None,
        "small_directional_flow": None,
        "large_trade_share": None,
        "change_in_large_trade_share": None,
        "large_trade_intensity": None,
        "small_trade_intensity": None,
        "opposite_large_trade_arrival": None,
        "directional_flow": None,
        "avg_trade_size": None,
    }
    if len(hist) < 4:
        return empty

    flow_col = "agg_1m" if "agg_1m" in hist.columns else "agg_5m"
    flow = pd.to_numeric(hist[flow_col], errors="coerce").fillna(0.0)
    share = pd.to_numeric(hist.get("large_trade_share"), errors="coerce")
    intensity = pd.to_numeric(hist.get("trade_intensity"), errors="coerce")
    avg_size = pd.to_numeric(hist.get("avg_trade_size"), errors="coerce")

    dir_flow = direction * flow
    # Use last bar for levels; early vs recent for change
    mid = max(2, len(hist) // 2)
    share_early = float(share.iloc[:mid].mean()) if share.notna().any() else np.nan
    share_recent = float(share.iloc[mid:].mean()) if share.notna().any() else np.nan
    share_now = float(share.iloc[-1]) if np.isfinite(share.iloc[-1]) else share_recent

    flow_now = float(dir_flow.iloc[-1])
    intens_now = float(intensity.iloc[-1]) if np.isfinite(intensity.iloc[-1]) else np.nan
    large_share = share_now if np.isfinite(share_now) else 0.0
    large_share = float(np.clip(large_share, 0.0, 1.0))

    # Opposite large: recent bars with opposite signed flow and above-median large share
    opp = dir_flow < 0
    if share.notna().any() and opp.any():
        thr = float(share.dropna().median())
        opp_large = bool(((opp) & (share >= thr)).iloc[mid:].any())
    else:
        opp_large = False

    return {
        "large_directional_flow": float(max(flow_now, 0.0) * large_share),
        "small_directional_flow": float(max(flow_now, 0.0) * (1.0 - large_share)),
        "large_trade_share": float(large_share) if np.isfinite(large_share) else None,
        "change_in_large_trade_share": (
            float(share_recent - share_early)
            if np.isfinite(share_recent) and np.isfinite(share_early)
            else None
        ),
        "large_trade_intensity": (
            float(intens_now * large_share) if np.isfinite(intens_now) else None
        ),
        "small_trade_intensity": (
            float(intens_now * (1.0 - large_share)) if np.isfinite(intens_now) else None
        ),
        "opposite_large_trade_arrival": float(1.0 if opp_large else 0.0),
        "directional_flow": float(flow_now),
        "avg_trade_size": float(avg_size.iloc[-1]) if np.isfinite(avg_size.iloc[-1]) else None,
        "large_flow_early": float(np.mean(np.clip(dir_flow.iloc[:mid], 0, None) * share.iloc[:mid].fillna(0))),
        "large_flow_recent": float(
            np.mean(np.clip(dir_flow.iloc[mid:], 0, None) * share.iloc[mid:].fillna(0))
        ),
        "small_flow_recent": float(
            np.mean(
                np.clip(dir_flow.iloc[mid:], 0, None)
                * (1.0 - share.iloc[mid:].fillna(0).clip(0, 1))
            )
        ),
    }


def fit_composition_thresholds(train: pd.DataFrame) -> dict[str, float]:
    large = pd.to_numeric(train["large_directional_flow"], errors="coerce").dropna()
    small = pd.to_numeric(train["small_directional_flow"], errors="coerce").dropna()
    dshare = pd.to_numeric(train["change_in_large_trade_share"], errors="coerce").dropna()
    return {
        "large_hi": float(large.quantile(0.67)) if len(large) else 0.0,
        "small_hi": float(small.quantile(0.67)) if len(small) else 0.0,
        "large_drop": float(dshare.quantile(0.33)) if len(dshare) else -0.05,
        "n_train": float(len(train)),
    }


def classify_composition(row: dict[str, Any], thr: dict[str, float]) -> str:
    """Assign large-led / small-led / disappearance / opposite-large / mixed."""
    large = row.get("large_directional_flow")
    small = row.get("small_directional_flow")
    dshare = row.get("change_in_large_trade_share")
    opp = row.get("opposite_large_trade_arrival")
    large_early = row.get("large_flow_early")
    large_recent = row.get("large_flow_recent")

    if opp is not None and float(opp) >= 0.5:
        return "opposite_large_trade_emergence"
    if (
        large_early is not None
        and large_recent is not None
        and float(large_early) > EPS
        and float(large_recent) < 0.5 * float(large_early)
        and small is not None
        and float(small) >= thr["small_hi"]
    ):
        return "large_flow_disappearance"
    if large is not None and float(large) >= thr["large_hi"] and (
        small is None or float(small) < thr["small_hi"]
    ):
        return "large_led_continuation"
    if small is not None and float(small) >= thr["small_hi"] and (
        large is None or float(large) < thr["large_hi"]
    ):
        return "small_led_continuation"
    if dshare is not None and float(dshare) <= thr["large_drop"] and small is not None and float(small) > 0:
        return "large_flow_disappearance"
    return "mixed"
