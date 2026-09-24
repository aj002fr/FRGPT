"""E03 V2 residual fade entry/exit baselines."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import TICK_SIZE
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _day_slice, _ts


def _z_at(day: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    row = day[day["bucket_ts"] == ts]
    if row.empty:
        prior = day[day["bucket_ts"] <= ts]
        if prior.empty:
            return None
        row = prior.tail(1)
    z = pd.to_numeric(row["residual_z"], errors="coerce").iloc[0]
    return float(z) if np.isfinite(z) else None


def entry_v1_abs_z_ge_2(panel: pd.DataFrame, event: dict[str, Any], _direction: int) -> pd.Timestamp | None:
    """Enter at/after event start on first bar with |residual_z| >= 2."""
    return _first_abs_z(panel, event, threshold=2.0)


def entry_v2_abs_z_ge_2_5(panel: pd.DataFrame, event: dict[str, Any], _direction: int) -> pd.Timestamp | None:
    return _first_abs_z(panel, event, threshold=2.5)


def _first_abs_z(panel: pd.DataFrame, event: dict[str, Any], *, threshold: float) -> pd.Timestamp | None:
    start = _ts(event["event_start_time"])
    day = _day_slice(panel, str(event["et_date"]), start)
    end = _ts(event.get("event_end_time") or (start + pd.Timedelta(minutes=30)))
    for ts, z in zip(day["bucket_ts"], pd.to_numeric(day["residual_z"], errors="coerce")):
        ts = _ts(ts)
        if ts < start or ts > end:
            continue
        if np.isfinite(z) and abs(float(z)) >= threshold:
            return ts
    return None


def entry_v3_cross_inside_2(panel: pd.DataFrame, event: dict[str, Any], _direction: int) -> pd.Timestamp | None:
    """Enter on first cross of |z| from >=2 back to <2 during the event."""
    start = _ts(event["event_start_time"])
    day = _day_slice(panel, str(event["et_date"]), start)
    end = _ts(event.get("event_end_time") or (start + pd.Timedelta(minutes=30)))
    seen_extreme = False
    prev_abs = None
    for ts, z in zip(day["bucket_ts"], pd.to_numeric(day["residual_z"], errors="coerce")):
        ts = _ts(ts)
        if ts < start or ts > end or not np.isfinite(z):
            continue
        az = abs(float(z))
        if az >= 2.0:
            seen_extreme = True
        if seen_extreme and prev_abs is not None and prev_abs >= 2.0 and az < 2.0:
            return ts
        prev_abs = az
    return None


def entry_v4_extreme_aligns_fade(panel: pd.DataFrame, event: dict[str, Any], direction: int) -> pd.Timestamp | None:
    """|z|>=2 and residual sign matches event direction (price stretched with the move)."""
    start = _ts(event["event_start_time"])
    day = _day_slice(panel, str(event["et_date"]), start)
    end = _ts(event.get("event_end_time") or (start + pd.Timedelta(minutes=30)))
    for ts, z in zip(day["bucket_ts"], pd.to_numeric(day["residual_z"], errors="coerce")):
        ts = _ts(ts)
        if ts < start or ts > end or not np.isfinite(z):
            continue
        z = float(z)
        if abs(z) >= 2.0 and int(np.sign(z)) == int(direction):
            return ts
    return None


V2_ENTRY_RULES = {
    "V1_abs_z_ge_2": entry_v1_abs_z_ge_2,
    "V2_abs_z_ge_2_5": entry_v2_abs_z_ge_2_5,
    "V3_cross_inside_2": entry_v3_cross_inside_2,
    "V4_extreme_aligns_event": entry_v4_extreme_aligns_fade,
}


def simulate_v2_trade(
    panel: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    event_direction: int,
    exit_mode: str,
    symbol: str = "TY",
    R_ticks: float = 4,
    C_ticks: float = 4,
    H_minutes: int = 15,
) -> dict[str, Any]:
    """Fade opposite event_direction; exit by mode."""
    tick = TICK_SIZE[symbol]
    fade_sign = -int(event_direction)
    series = panel.sort_values("bucket_ts").set_index("bucket_ts")
    prices = series["price"]
    idx = pd.DatetimeIndex(prices.index)
    prices.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    zseries = pd.to_numeric(series["residual_z"], errors="coerce")
    zseries.index = prices.index

    entry = _ts(entry_ts)
    if entry not in prices.index:
        prior = prices.index[prices.index <= entry]
        if len(prior) == 0:
            return {"entered": False}
        entry = prior[-1]
    p0 = float(prices.loc[entry])

    if exit_mode == "barrier":
        lab = first_barrier_label(
            prices,
            start_ts=entry,
            direction=event_direction,
            R_ticks=R_ticks,
            C_ticks=C_ticks,
            H=pd.Timedelta(minutes=H_minutes),
            tick_size=tick,
        )
        horizon = entry + pd.Timedelta(minutes=H_minutes)
        if lab.get("hit_ts"):
            exit_ts = _ts(lab["hit_ts"])
        else:
            exit_ts = horizon
        if lab.get("label") == 1:
            net = float(R_ticks)
            hit = True
        elif lab.get("label") == 0:
            net = float(-C_ticks)
            hit = False
        else:
            end_px = float(prices.loc[prices.index <= exit_ts].iloc[-1])
            net = fade_sign * (end_px - p0) / tick
            hit = None
        window = prices[(prices.index >= entry) & (prices.index <= exit_ts)]
        signed = fade_sign * (window.astype(float) - p0) / tick
        return {
            "entered": True,
            "entry_ts": entry.isoformat(),
            "exit_ts": exit_ts.isoformat(),
            "exit_mode": exit_mode,
            "label": lab.get("label"),
            "hit": hit,
            "net_ticks": float(net) if net is not None else None,
            "mae_ticks": float((-signed).max()) if len(signed) else None,
            "mfe_ticks": float(signed.max()) if len(signed) else None,
            "time_in_trade_minutes": (exit_ts - entry).total_seconds() / 60.0,
        }

    if exit_mode == "fixed_30m":
        exit_ts = entry + pd.Timedelta(minutes=30)
        window = prices[(prices.index >= entry) & (prices.index <= exit_ts)]
        if window.empty:
            return {"entered": False}
        end_px = float(window.iloc[-1])
        signed = fade_sign * (window.astype(float) - p0) / tick
        return {
            "entered": True,
            "entry_ts": entry.isoformat(),
            "exit_ts": window.index[-1].isoformat(),
            "exit_mode": exit_mode,
            "label": None,
            "hit": None,
            "net_ticks": float(fade_sign * (end_px - p0) / tick),
            "mae_ticks": float((-signed).max()) if len(signed) else None,
            "mfe_ticks": float(signed.max()) if len(signed) else None,
            "time_in_trade_minutes": (window.index[-1] - entry).total_seconds() / 60.0,
        }

    if exit_mode == "abs_z_lt_1":
        deadline = entry + pd.Timedelta(minutes=30)
        forward = zseries[(zseries.index > entry) & (zseries.index <= deadline)]
        exit_ts = deadline
        resolved = False
        for ts, z in forward.items():
            if np.isfinite(z) and abs(float(z)) < 1.0:
                exit_ts = _ts(ts)
                resolved = True
                break
        window = prices[(prices.index >= entry) & (prices.index <= exit_ts)]
        if window.empty:
            return {"entered": False}
        end_px = float(window.iloc[-1])
        signed = fade_sign * (window.astype(float) - p0) / tick
        return {
            "entered": True,
            "entry_ts": entry.isoformat(),
            "exit_ts": window.index[-1].isoformat(),
            "exit_mode": exit_mode,
            "label": 1 if resolved else -1,
            "hit": resolved,
            "z_resolved": resolved,
            "net_ticks": float(fade_sign * (end_px - p0) / tick),
            "mae_ticks": float((-signed).max()) if len(signed) else None,
            "mfe_ticks": float(signed.max()) if len(signed) else None,
            "time_in_trade_minutes": (window.index[-1] - entry).total_seconds() / 60.0,
        }

    raise ValueError(f"Unknown exit_mode {exit_mode}")
