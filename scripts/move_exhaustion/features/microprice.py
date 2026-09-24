"""E12 microprice and book-imbalance confirmation features."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import TICK_SIZE
from scripts.move_exhaustion.simulation.policies import _ts

LOOKBACK_BARS = 10


def microprice_imbalance_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
    symbol: str = "TY",
) -> dict[str, float | None | bool]:
    """Causal microprice/imbalance diagnostics at event start."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars)
    empty = {
        "directional_microprice_signal": None,
        "directional_book_imbalance": None,
        "microprice_signal_change": None,
        "imbalance_change": None,
        "imbalance_persistence": None,
        "price_continues_microprice_turns": None,
        "imbalance_reverses_before_price": None,
        "price_progress_ticks": None,
    }
    if len(hist) < 4:
        return empty

    px = pd.to_numeric(hist["price"], errors="coerce")
    # Prefer last/mean microprice deviation columns from V2 panel
    if "microprice_dev_last" in hist.columns:
        mp = pd.to_numeric(hist["microprice_dev_last"], errors="coerce")
    elif "microprice_dev_mean" in hist.columns:
        mp = pd.to_numeric(hist["microprice_dev_mean"], errors="coerce")
    else:
        mp = pd.Series(np.nan, index=hist.index)

    if "size_imbalance_last" in hist.columns:
        imb = pd.to_numeric(hist["size_imbalance_last"], errors="coerce")
    elif "size_imbalance_mean" in hist.columns:
        imb = pd.to_numeric(hist["size_imbalance_mean"], errors="coerce")
    else:
        imb = pd.Series(np.nan, index=hist.index)

    # microprice_dev is typically (microprice - mid); convert to ticks
    dir_mp = direction * (mp / tick)
    dir_imb = direction * imb

    mid = max(2, len(hist) // 2)
    early_mp = float(dir_mp.iloc[:mid].mean()) if dir_mp.iloc[:mid].notna().any() else np.nan
    recent_mp = float(dir_mp.iloc[mid:].mean()) if dir_mp.iloc[mid:].notna().any() else np.nan
    early_imb = float(dir_imb.iloc[:mid].mean()) if dir_imb.iloc[:mid].notna().any() else np.nan
    recent_imb = float(dir_imb.iloc[mid:].mean()) if dir_imb.iloc[mid:].notna().any() else np.nan

    mp_now = float(dir_mp.iloc[-1]) if np.isfinite(dir_mp.iloc[-1]) else recent_mp
    imb_now = float(dir_imb.iloc[-1]) if np.isfinite(dir_imb.iloc[-1]) else recent_imb

    price_progress = direction * (float(px.iloc[-1]) - float(px.iloc[0])) / tick
    early_price = direction * (float(px.iloc[mid - 1]) - float(px.iloc[0])) / tick
    recent_price = direction * (float(px.iloc[-1]) - float(px.iloc[mid])) / tick

    # Persistence: fraction of bars with directional imbalance > 0
    valid_imb = dir_imb.dropna()
    persistence = float((valid_imb > 0).mean()) if len(valid_imb) else None

    # Pattern: price still advancing in event direction but microprice turns against
    price_up = recent_price > 0
    mp_turns = (
        np.isfinite(early_mp)
        and np.isfinite(recent_mp)
        and early_mp > 0
        and recent_mp < 0
    )
    price_continues_mp_turns = bool(price_up and mp_turns)

    # Imbalance reverses (was +, now -) while recent price still non-negative
    imb_rev = (
        np.isfinite(early_imb)
        and np.isfinite(recent_imb)
        and early_imb > 0
        and recent_imb < 0
        and recent_price >= 0
    )

    return {
        "directional_microprice_signal": float(mp_now) if np.isfinite(mp_now) else None,
        "directional_book_imbalance": float(imb_now) if np.isfinite(imb_now) else None,
        "microprice_signal_change": (
            float(recent_mp - early_mp)
            if np.isfinite(recent_mp) and np.isfinite(early_mp)
            else None
        ),
        "imbalance_change": (
            float(recent_imb - early_imb)
            if np.isfinite(recent_imb) and np.isfinite(early_imb)
            else None
        ),
        "imbalance_persistence": persistence,
        "price_continues_microprice_turns": price_continues_mp_turns,
        "imbalance_reverses_before_price": bool(imb_rev),
        "price_progress_ticks": float(price_progress),
        "recent_price_progress_ticks": float(recent_price),
        "early_price_progress_ticks": float(early_price),
    }
