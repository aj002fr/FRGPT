"""Costed fade simulation helpers for E26 trading-policy experiments.

Assumptions (documented in E26 conclusion):
- Mid from panel `price`; bid/ask via half of `spread_mean`/`spread_last`.
- Fade up-event short at bid; fade down-event long at ask (never mid).
- Round-trip cost = half-spread (entry) + half-spread (exit) + 0.5 tick fee/slippage.
- Primary decision latency = 1 bar (1m); sensitivity 0/1 bar.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import TICK_SIZE
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _day_slice, _ts

FEE_SLIPPAGE_TICKS = 0.5
PROB_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80)


def _spread_ticks(row: pd.Series, tick: float) -> float:
    for col in ("spread_mean", "spread_last", "spread"):
        if col in row.index:
            sp = float(pd.to_numeric(row[col], errors="coerce"))
            if np.isfinite(sp) and sp > 0:
                return max(sp / tick, 1.0)
    return 1.0


def executable_entry_price(
    panel: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    event_direction: int,
    et_date: str,
    symbol: str = "TY",
) -> dict[str, Any] | None:
    """Conservative fade entry: short→bid, long→ask."""
    tick = TICK_SIZE[symbol]
    day = panel[panel["et_date"] == et_date].sort_values("bucket_ts")
    if day.empty:
        return None
    hist = day[day["bucket_ts"] <= entry_ts]
    if hist.empty:
        return None
    row = hist.iloc[-1]
    mid = float(pd.to_numeric(row["price"], errors="coerce"))
    if not np.isfinite(mid):
        return None
    half_spread = 0.5 * _spread_ticks(row, tick) * tick
    fade_sign = -int(event_direction)  # +1 long, -1 short
    # long pays ask; short sells bid
    entry_px = mid + half_spread if fade_sign > 0 else mid - half_spread
    return {
        "entry_ts": _ts(row["bucket_ts"]),
        "mid": mid,
        "entry_price": float(entry_px),
        "half_spread_ticks": float(half_spread / tick),
        "fade_sign": fade_sign,
    }


def apply_latency(entry_ts: pd.Timestamp, latency_bars: int) -> pd.Timestamp:
    return _ts(entry_ts) + pd.Timedelta(minutes=max(0, int(latency_bars)))


def simulate_costed_fade(
    panel: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    event_direction: int,
    et_date: str,
    exit_mode: str = "fixed_15m",
    H_minutes: int = 15,
    R_ticks: float = 4.0,
    C_ticks: float = 4.0,
    symbol: str = "TY",
    latency_bars: int = 1,
) -> dict[str, Any]:
    """Simulate one fade trade with costs. Returns net ticks after costs."""
    tick = TICK_SIZE[symbol]
    delayed = apply_latency(entry_ts, latency_bars)
    ent = executable_entry_price(
        panel, entry_ts=delayed, event_direction=event_direction, et_date=et_date, symbol=symbol
    )
    if ent is None:
        return {"entered": False, "net_ticks": None}

    entry = ent["entry_ts"]
    fade_sign = int(ent["fade_sign"])
    day = _day_slice(panel, et_date, entry)
    if day.empty:
        return {"entered": False, "net_ticks": None}

    prices = day.set_index("bucket_ts")["price"].astype(float)
    idx = pd.DatetimeIndex(prices.index)
    prices.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    residual = None
    if "residual_z" in day.columns:
        residual = day.set_index("bucket_ts")["residual_z"]
        ridx = pd.DatetimeIndex(residual.index)
        residual.index = ridx.tz_localize("UTC") if ridx.tz is None else ridx.tz_convert("UTC")

    # Horizon / barrier exit
    end = entry + pd.Timedelta(minutes=H_minutes)
    window = prices[(prices.index >= entry) & (prices.index <= end)]
    if window.empty:
        return {"entered": False, "net_ticks": None}

    exit_ts = window.index[-1]
    exit_reason = "horizon"
    hit = None
    label = None

    if exit_mode.startswith("fixed_"):
        mins = int(exit_mode.split("_")[1].replace("m", ""))
        end = entry + pd.Timedelta(minutes=mins)
        window = prices[(prices.index >= entry) & (prices.index <= end)]
        if window.empty:
            return {"entered": False, "net_ticks": None}
        exit_ts = window.index[-1]
        exit_reason = exit_mode
        # Mark hit if signed PnL > 0 at exit
    elif exit_mode == "barrier":
        lab = first_barrier_label(
            prices,
            start_ts=entry,
            direction=event_direction,
            R_ticks=R_ticks,
            C_ticks=C_ticks,
            H=pd.Timedelta(minutes=H_minutes),
            tick_size=tick,
        )
        label = lab.get("label")
        if lab.get("hit_ts"):
            exit_ts = _ts(lab["hit_ts"])
            exit_reason = lab.get("first_barrier") or "barrier"
        hit = True if label == 1 else False if label == 0 else None
    elif exit_mode == "abs_z_lt_1" and residual is not None:
        for ts, z in residual[(residual.index > entry) & (residual.index <= end)].items():
            if np.isfinite(z) and abs(float(z)) < 1.0:
                exit_ts = _ts(ts)
                exit_reason = "abs_z_lt_1"
                break

    exit_hist = day[day["bucket_ts"] <= exit_ts]
    if exit_hist.empty:
        return {"entered": False, "net_ticks": None}
    exit_row = exit_hist.iloc[-1]
    exit_mid = float(pd.to_numeric(exit_row["price"], errors="coerce"))
    half_spread_exit = 0.5 * _spread_ticks(exit_row, tick) * tick
    # Cover short at ask; sell long at bid
    exit_px = exit_mid + half_spread_exit if fade_sign < 0 else exit_mid - half_spread_exit

    signed_path = fade_sign * (window.astype(float) - float(ent["entry_price"])) / tick
    mae = float((-signed_path).max()) if len(signed_path) else None
    mfe = float(signed_path.max()) if len(signed_path) else None
    gross = fade_sign * (exit_px - float(ent["entry_price"])) / tick
    # Costs already partially in bid/ask; add explicit fee/slippage proxy once round-trip
    net = float(gross) - FEE_SLIPPAGE_TICKS
    if hit is None:
        hit = bool(net > 0)

    return {
        "entered": True,
        "entry_ts": entry.isoformat(),
        "exit_ts": exit_ts.isoformat(),
        "exit_reason": exit_reason,
        "entry_price": float(ent["entry_price"]),
        "exit_price": float(exit_px),
        "label": label,
        "hit": hit,
        "gross_ticks": float(gross),
        "net_ticks": net,
        "mae_ticks": mae,
        "mfe_ticks": mfe,
        "half_spread_entry_ticks": ent["half_spread_ticks"],
        "fee_slippage_ticks": FEE_SLIPPAGE_TICKS,
        "latency_bars": int(latency_bars),
        "time_in_trade_minutes": (exit_ts - entry).total_seconds() / 60.0,
    }


def date_pnl_concentration(nets_by_date: pd.Series) -> dict[str, Any]:
    """Flag if any single date contributes >20% of positive PnL mass."""
    pos = nets_by_date.clip(lower=0)
    total_pos = float(pos.sum())
    if total_pos <= 0:
        return {"max_date_share_of_positive_pnl": None, "flag_gt_20pct": False, "top_date": None}
    shares = pos / total_pos
    top_date = str(shares.idxmax())
    top_share = float(shares.max())
    return {
        "max_date_share_of_positive_pnl": top_share,
        "flag_gt_20pct": bool(top_share > 0.20),
        "top_date": top_date,
    }
