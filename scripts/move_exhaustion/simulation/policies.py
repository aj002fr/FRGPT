"""Price-only fade entry policies for E02 baselines."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import TICK_SIZE
from scripts.move_exhaustion.labels.barriers import first_barrier_label


PolicyFn = Callable[[pd.DataFrame, dict[str, Any], int], pd.Timestamp | None]


def _ts(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _day_slice(panel: pd.DataFrame, et_date: str, start: pd.Timestamp) -> pd.DataFrame:
    day = panel[panel["et_date"] == et_date].sort_values("bucket_ts")
    return day[day["bucket_ts"] >= start]


def entry_immediate(panel: pd.DataFrame, event: dict[str, Any], _direction: int) -> pd.Timestamp | None:
    return _ts(event["event_start_time"])


def entry_no_new_extreme(panel: pd.DataFrame, event: dict[str, Any], direction: int, wait_bars: int) -> pd.Timestamp | None:
    """Enter after `wait_bars` consecutive bars without a new event-direction extreme."""
    start = _ts(event["event_start_time"])
    day = _day_slice(panel, str(event["et_date"]), start)
    if day.empty:
        return None
    prices = pd.to_numeric(day["price"], errors="coerce")
    extreme = float(prices.iloc[0])
    quiet = 0
    for ts, px in zip(day["bucket_ts"], prices):
        ts = _ts(ts)
        if ts <= start:
            continue
        px = float(px)
        if direction > 0:
            if px > extreme:
                extreme = px
                quiet = 0
            else:
                quiet += 1
        else:
            if px < extreme:
                extreme = px
                quiet = 0
            else:
                quiet += 1
        if quiet >= wait_bars:
            return ts
    return None


def entry_no_extreme_30s(panel: pd.DataFrame, event: dict[str, Any], direction: int) -> pd.Timestamp | None:
    # 1m grid: nearest is 1 bar
    return entry_no_new_extreme(panel, event, direction, wait_bars=1)


def entry_no_extreme_60s(panel: pd.DataFrame, event: dict[str, Any], direction: int) -> pd.Timestamp | None:
    # 1m grid approximation of 60s: require 2 quiet bars
    return entry_no_new_extreme(panel, event, direction, wait_bars=2)

def entry_first_1tick_retrace(panel: pd.DataFrame, event: dict[str, Any], direction: int) -> pd.Timestamp | None:
    start = _ts(event["event_start_time"])
    tick = TICK_SIZE[str(event.get("symbol") or "TY")]
    day = _day_slice(panel, str(event["et_date"]), start)
    if day.empty:
        return None
    extreme = float(day["price"].iloc[0])
    for ts, px in zip(day["bucket_ts"], pd.to_numeric(day["price"], errors="coerce")):
        ts = _ts(ts)
        if ts <= start:
            continue
        px = float(px)
        if direction > 0:
            extreme = max(extreme, px)
            if extreme - px >= tick:
                return ts
        else:
            extreme = min(extreme, px)
            if px - extreme >= tick:
                return ts
    return None


def entry_nonpositive_velocity(panel: pd.DataFrame, event: dict[str, Any], direction: int) -> pd.Timestamp | None:
    """Enter when 1m directional velocity becomes <= 0."""
    start = _ts(event["event_start_time"])
    day = _day_slice(panel, str(event["et_date"]), start)
    if len(day) < 2:
        return None
    prices = pd.to_numeric(day["price"], errors="coerce")
    dprice = prices.diff()
    for ts, dp in zip(day["bucket_ts"], dprice):
        ts = _ts(ts)
        if ts <= start or not np.isfinite(dp):
            continue
        if direction * float(dp) <= 0:
            return ts
    return None


POLICIES: dict[str, PolicyFn] = {
    "B1_fade_immediate": entry_immediate,
    "B2_fade_no_extreme_30s": entry_no_extreme_30s,
    "B3_fade_no_extreme_60s": entry_no_extreme_60s,
    "B4_fade_first_1tick_retrace": entry_first_1tick_retrace,
    "B5_fade_nonpositive_velocity": entry_nonpositive_velocity,
}


def simulate_fade_from_entry(
    panel: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    event_direction: int,
    R_ticks: float = 4,
    C_ticks: float = 4,
    H_minutes: int = 15,
    symbol: str = "TY",
) -> dict[str, Any]:
    """Fade = trade opposite event_direction. Net ticks from entry to first barrier/horizon."""
    tick = TICK_SIZE[symbol]
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]
    lab = first_barrier_label(
        prices,
        start_ts=entry_ts,
        direction=event_direction,
        R_ticks=R_ticks,
        C_ticks=C_ticks,
        H=pd.Timedelta(minutes=H_minutes),
        tick_size=tick,
    )
    # Price path for MAE/MFE from fade perspective
    series = prices.copy()
    idx = pd.DatetimeIndex(series.index)
    series.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    entry = _ts(entry_ts)
    if entry not in series.index:
        prior = series.index[series.index <= entry]
        if len(prior) == 0:
            return {"entered": False, **lab}
        entry = prior[-1]
    p0 = float(series.loc[entry])
    window = series[(series.index >= entry) & (series.index <= entry + pd.Timedelta(minutes=H_minutes))]
    # Fade sign: +1 means long (event was down), -1 short (event was up)
    fade_sign = -int(event_direction)
    signed = fade_sign * (window.astype(float) - p0) / tick
    mfe = float(signed.max()) if len(signed) else None
    mae = float((-signed).max()) if len(signed) else None
    # If reversal barrier first → fade wins ~R ticks; continuation → loses ~C
    if lab.get("label") == 1:
        net = float(R_ticks)
        hit = True
    elif lab.get("label") == 0:
        net = float(-C_ticks)
        hit = False
    else:
        # unresolved: mark-to-market at horizon end
        if len(signed):
            net = float(signed.iloc[-1])
        else:
            net = None
        hit = None
    return {
        "entered": True,
        "entry_ts": entry.isoformat(),
        "entry_price": p0,
        "label": lab.get("label"),
        "label_reason": lab.get("reason"),
        "first_barrier": lab.get("first_barrier"),
        "hit": hit,
        "net_ticks": net,
        "mfe_ticks": mfe,
        "mae_ticks": mae,
        "time_in_trade_minutes": (
            None
            if not lab.get("hit_ts")
            else (pd.Timestamp(lab["hit_ts"]) - entry).total_seconds() / 60.0
        ),
    }
