"""Barrier labels for move-exhaustion events."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def first_barrier_label(
    prices: pd.Series,
    *,
    start_ts: pd.Timestamp,
    direction: int,
    R_ticks: float,
    C_ticks: float,
    H: pd.Timedelta,
    tick_size: float,
) -> dict[str, Any]:
    """Return which barrier is hit first after start_ts within horizon H.

    Labels: 1=reversal first, 0=continuation first, -1=unresolved/ambiguous.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be ±1")
    series = prices.sort_index()
    idx = pd.DatetimeIndex(series.index)
    series.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    start = pd.Timestamp(start_ts)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    if start not in series.index:
        # nearest previous bar
        prior = series.index[series.index <= start]
        if len(prior) == 0:
            return {"label": -1, "reason": "no_price_at_start"}
        start = prior[-1]
    p0 = float(series.loc[start])
    window = series[(series.index > start) & (series.index <= start + H)]
    if window.empty:
        return {"label": -1, "reason": "empty_forward_window", "first_barrier": None}
    cont = p0 + direction * C_ticks * tick_size
    rev = p0 - direction * R_ticks * tick_size
    hit_c = None
    hit_r = None
    for ts, px in window.items():
        px = float(px)
        if hit_c is None and ((direction > 0 and px >= cont) or (direction < 0 and px <= cont)):
            hit_c = ts
        if hit_r is None and ((direction > 0 and px <= rev) or (direction < 0 and px >= rev)):
            hit_r = ts
        if hit_c is not None and hit_r is not None:
            break
    if hit_c is None and hit_r is None:
        return {
            "label": -1,
            "reason": "unresolved",
            "first_barrier": None,
            "start_price": p0,
            "continuation_price": cont,
            "reversal_price": rev,
        }
    if hit_c is not None and hit_r is not None and hit_c == hit_r:
        return {"label": -1, "reason": "ambiguous_same_bar", "first_barrier": "both"}
    if hit_r is not None and (hit_c is None or hit_r < hit_c):
        return {
            "label": 1,
            "reason": "reversal_first",
            "first_barrier": "reversal",
            "hit_ts": hit_r.isoformat(),
            "start_price": p0,
        }
    return {
        "label": 0,
        "reason": "continuation_first",
        "first_barrier": "continuation",
        "hit_ts": hit_c.isoformat(),
        "start_price": p0,
    }
