"""Causal reduced 1-minute heuristic for the exploratory C018 finding.

This is not the offline MBP-1 C018 cycle. It uses only V2's one-minute price
panel and a cutoff frozen once from the discovery-prefix C018 artifact.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TICK_SIZE = 1.0 / 64.0
LOOKBACK_BARS = 30
MOVE_TICKS = 8.0
T3_RANGE_CUTOFF_TICKS = 10.5
DECLUSTER_STRIDE_MIN = 15
STRATEGY_STATUS = "PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE"
C018_LABEL = "PROVISIONAL / C018-FADE-LEAN / NOT OFFLINE C018 / NON-EXECUTABLE"
CUTOFF_SOURCE = (
    "workspace/analysis/move_exhaustion_v2/loop/"
    "C018_vol_regime/episodes_c018.csv::range_ticks.quantile(2/3)"
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def annotate_c018_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate each completed bar from the 30-bar window ending at that bar."""
    out = df.copy().sort_index()
    if out.empty or "price" not in out:
        return out

    price = pd.to_numeric(out["price"], errors="coerce")
    window_low = price.rolling(LOOKBACK_BARS, min_periods=LOOKBACK_BARS).min()
    window_high = price.rolling(LOOKBACK_BARS, min_periods=LOOKBACK_BARS).max()
    range_ticks = (window_high - window_low) / TICK_SIZE
    up_ext = (price - window_low) / TICK_SIZE
    down_ext = (window_high - price) / TICK_SIZE

    up_reached = up_ext.ge(MOVE_TICKS)
    down_reached = down_ext.ge(MOVE_TICKS)
    up_edge = up_reached & ~up_reached.shift(1, fill_value=False)
    down_edge = down_reached & ~down_reached.shift(1, fill_value=False)

    move_direction = pd.Series("", index=out.index, dtype=object)
    move_direction.loc[up_edge & ~down_edge] = "UP"
    move_direction.loc[down_edge & ~up_edge] = "DOWN"
    both = up_edge & down_edge
    move_direction.loc[both & up_ext.gt(down_ext)] = "UP"
    move_direction.loc[both & down_ext.gt(up_ext)] = "DOWN"

    t3_gate = range_ticks.ge(T3_RANGE_CUTOFF_TICKS)
    fire = move_direction.ne("") & t3_gate
    fade_lean = pd.Series("", index=out.index, dtype=object)
    fade_lean.loc[fire & move_direction.eq("UP")] = "FADE DOWN"
    fade_lean.loc[fire & move_direction.eq("DOWN")] = "FADE UP"

    out["c018_window_low"] = window_low
    out["c018_window_high"] = window_high
    out["c018_range_30m_ticks"] = range_ticks
    out["c018_up_ext_ticks"] = up_ext
    out["c018_down_ext_ticks"] = down_ext
    out["c018_move_direction"] = move_direction
    out["c018_t3_gate"] = t3_gate.fillna(False)
    out["c018_fire"] = fire.fillna(False)
    out["c018_fade_lean"] = fade_lean
    return out


def latest_c018_context(df: pd.DataFrame) -> dict[str, Any]:
    """Return display context for the latest completed candidate bar."""
    if df.empty or "c018_fire" not in df:
        return {
            "active": False,
            "state": "WARMING_UP",
            "fade_lean": "",
            "label": C018_LABEL,
            "strategy_status": STRATEGY_STATUS,
            "range_cutoff_ticks": T3_RANGE_CUTOFF_TICKS,
            "not_offline_c018": True,
        }
    row = df.iloc[-1]
    active = bool(row.get("c018_fire", False))
    return {
        "active": active,
        "state": "C018 FADE LEAN" if active else "NORMAL",
        "ts_utc": pd.Timestamp(row.name).isoformat(),
        "price": _finite(row.get("price")),
        "range_30m_ticks": _finite(row.get("c018_range_30m_ticks")),
        "up_ext_ticks": _finite(row.get("c018_up_ext_ticks")),
        "down_ext_ticks": _finite(row.get("c018_down_ext_ticks")),
        "move_direction": str(row.get("c018_move_direction") or ""),
        "fade_lean": str(row.get("c018_fade_lean") or ""),
        "t3_gate": bool(row.get("c018_t3_gate", False)),
        "label": C018_LABEL,
        "strategy_status": STRATEGY_STATUS,
        "range_cutoff_ticks": T3_RANGE_CUTOFF_TICKS,
        "not_offline_c018": True,
    }


__all__ = [
    "C018_LABEL",
    "CUTOFF_SOURCE",
    "DECLUSTER_STRIDE_MIN",
    "LOOKBACK_BARS",
    "MOVE_TICKS",
    "STRATEGY_STATUS",
    "T3_RANGE_CUTOFF_TICKS",
    "TICK_SIZE",
    "annotate_c018_flags",
    "latest_c018_context",
]
