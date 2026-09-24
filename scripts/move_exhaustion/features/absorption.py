"""E10 TBBO apparent absorption at the event extreme (1m approximations)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE
from scripts.move_exhaustion.simulation.policies import _ts

LOOKBACK_BARS = 15


def apparent_absorption_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
    symbol: str = "TY",
) -> dict[str, float | None]:
    """Causal absorption diagnostics in the pre-event lookback ending at start.

    Tracks the running extreme in the event direction; bars that do not print a
    new extreme contribute to absorption tallies (flow/intensity/time at extreme).
    """
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars)
    empty = {
        "trades_at_extreme": None,
        "volume_at_extreme": None,
        "directional_dv01_at_extreme": None,
        "average_opposing_depth_at_extreme": None,
        "maximum_opposing_depth_at_extreme": None,
        "fraction_of_trades_without_price_advance": None,
        "seconds_at_extreme": None,
        "new_extreme_rate": None,
        "bars_at_extreme": None,
        "spread_at_event": None,
        "depth_at_event": None,
        "realized_vol_at_event": None,
        "minutes_since_open": None,
        "initial_move_ticks": None,
    }
    if len(hist) < 4:
        return empty

    px = pd.to_numeric(hist["price"], errors="coerce").to_numpy(dtype=float)
    flow_col = "agg_1m" if "agg_1m" in hist.columns else "agg_5m"
    flow = direction * pd.to_numeric(hist[flow_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    intensity = pd.to_numeric(hist.get("trade_intensity"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    depth = pd.to_numeric(hist.get("displayed_depth_mean"), errors="coerce").to_numpy(dtype=float)
    imb = pd.to_numeric(hist.get("size_imbalance_mean"), errors="coerce").to_numpy(dtype=float)
    # Opposing depth proxy: when buying (dir>0), more offer-heavy book → negative imbalance
    # Use depth * max(-direction * imbalance, 0) + depth as soft opposing pressure
    opposing = []
    for d, im in zip(depth, imb):
        if not np.isfinite(d):
            opposing.append(np.nan)
            continue
        if np.isfinite(im):
            # imbalance >0 bid-heavy; opposing for up-move is offer side ≈ depth*(1-clip)
            side = 0.5 - 0.5 * direction * im  # up-move: higher when im negative
            opposing.append(float(d * np.clip(side, 0.05, 1.0)))
        else:
            opposing.append(float(d))
    opposing = np.asarray(opposing, dtype=float)

    extreme = px[0]
    n_new = 0
    at_ext_flow = 0.0
    at_ext_trades = 0.0
    at_ext_bars = 0
    at_ext_opp = []
    for i in range(1, len(px)):
        moved = False
        if direction > 0 and px[i] > extreme + 0.5 * tick:
            extreme = px[i]
            n_new += 1
            moved = True
        elif direction < 0 and px[i] < extreme - 0.5 * tick:
            extreme = px[i]
            n_new += 1
            moved = True
        if moved:
            continue
        # No new extreme: absorption tally if same-direction flow
        pos = max(flow[i], 0.0)
        at_ext_flow += pos
        at_ext_trades += max(intensity[i], 0.0)
        at_ext_bars += 1
        if np.isfinite(opposing[i]):
            at_ext_opp.append(opposing[i])

    total_trades = float(np.sum(np.clip(intensity, 0, None)))
    frac = float(at_ext_trades / max(total_trades, EPS)) if total_trades > 0 else None
    # 1m bars → 60 seconds each
    seconds = float(at_ext_bars * 60)
    new_rate = float(n_new / max(len(hist) - 1, 1))

    last = hist.iloc[-1]
    # Initial move: from lookback start to event start in event direction
    initial_move = direction * (float(px[-1]) - float(px[0])) / tick

    return {
        "trades_at_extreme": float(at_ext_trades),
        "volume_at_extreme": float(at_ext_flow),  # DV01 proxy as volume stand-in
        "directional_dv01_at_extreme": float(at_ext_flow),
        "average_opposing_depth_at_extreme": float(np.nanmean(at_ext_opp)) if at_ext_opp else None,
        "maximum_opposing_depth_at_extreme": float(np.nanmax(at_ext_opp)) if at_ext_opp else None,
        "fraction_of_trades_without_price_advance": frac,
        "seconds_at_extreme": seconds,
        "new_extreme_rate": new_rate,
        "bars_at_extreme": float(at_ext_bars),
        "spread_at_event": float(pd.to_numeric(last.get("spread_mean"), errors="coerce"))
        if pd.notna(last.get("spread_mean"))
        else None,
        "depth_at_event": float(pd.to_numeric(last.get("displayed_depth_mean"), errors="coerce"))
        if pd.notna(last.get("displayed_depth_mean"))
        else None,
        "realized_vol_at_event": float(pd.to_numeric(last.get("realized_vol"), errors="coerce"))
        if pd.notna(last.get("realized_vol"))
        else None,
        "minutes_since_open": float(pd.to_numeric(last.get("minutes_since_open"), errors="coerce"))
        if pd.notna(last.get("minutes_since_open"))
        else None,
        "initial_move_ticks": float(initial_move),
    }


def fit_absorption_z_stats(train: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Train-only mean/std for z-scoring absorption components."""
    cols = (
        "directional_dv01_at_extreme",
        "fraction_of_trades_without_price_advance",
        "seconds_at_extreme",
        "new_extreme_rate",
    )
    stats: dict[str, dict[str, float]] = {}
    for c in cols:
        x = pd.to_numeric(train[c], errors="coerce").dropna()
        mu = float(x.mean()) if len(x) else 0.0
        sd = float(x.std(ddof=1)) if len(x) > 1 else 1.0
        if not np.isfinite(sd) or sd < EPS:
            sd = 1.0
        stats[c] = {"mean": mu, "std": sd}
    return stats


def apparent_absorption_score(row: dict[str, Any], zstats: dict[str, dict[str, float]]) -> float | None:
    """Hand-crafted descriptive score (not for direct deployment)."""

    def z(col: str) -> float | None:
        v = row.get(col)
        if v is None or not np.isfinite(float(v)):
            return None
        st = zstats[col]
        return (float(v) - st["mean"]) / st["std"]

    parts = [
        z("directional_dv01_at_extreme"),
        z("fraction_of_trades_without_price_advance"),
        z("seconds_at_extreme"),
    ]
    neg = z("new_extreme_rate")
    if any(p is None for p in parts) or neg is None:
        return None
    return float(parts[0] + parts[1] + parts[2] - neg)
