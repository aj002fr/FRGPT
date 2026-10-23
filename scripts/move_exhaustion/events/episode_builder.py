"""Build non-overlapping move events from detector fires."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import TICK_SIZE
from scripts.move_exhaustion.events.shock_detector import detect_triggers
from scripts.move_exhaustion.labels.barriers import first_barrier_label


def build_events(
    panel: pd.DataFrame,
    fires: pd.DataFrame,
    *,
    max_horizon_minutes: int = 30,
    retrace_frac: float = 0.75,
    symbol: str = "TY",
) -> pd.DataFrame:
    """Greedy causal de-dupe: one active event per date until termination."""
    if fires.empty:
        return pd.DataFrame()
    tick = TICK_SIZE[symbol]
    panel = panel.sort_values(["et_date", "bucket_ts"]).copy()
    fires = fires.sort_values(["et_date", "bucket_ts"]).copy()
    prices = panel.set_index("bucket_ts")["price"]

    events: list[dict[str, Any]] = []
    active_until: dict[str, pd.Timestamp] = {}

    for _, row in fires.iterrows():
        et_date = str(row["et_date"])
        ts = pd.Timestamp(row["bucket_ts"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        if et_date in active_until and ts <= active_until[et_date]:
            continue
        direction = int(np.sign(row["event_direction"]))
        start_price = float(row["price"])
        # Anchor ≈ lookback start for 5m move
        day = panel[panel["et_date"] == et_date].sort_values("bucket_ts")
        prior = day[day["bucket_ts"] <= ts].tail(6)
        anchor_price = float(prior["price"].iloc[0]) if len(prior) else start_price
        initial_move_ticks = abs(start_price - anchor_price) / tick

        deadline = ts + pd.Timedelta(minutes=max_horizon_minutes)
        forward = day[(day["bucket_ts"] > ts) & (day["bucket_ts"] <= deadline)]
        end_ts = deadline
        end_reason = "max_horizon"
        extreme = start_price
        for _, fr in forward.iterrows():
            px = float(fr["price"])
            fts = pd.Timestamp(fr["bucket_ts"])
            if direction > 0:
                extreme = max(extreme, px)
                # terminal reversal: 4 ticks against from start (also used later as label)
                if px <= start_price - 4 * tick:
                    end_ts, end_reason = fts, "terminal_reversal"
                    break
                # 75% retrace of initial move from extreme
                run = extreme - anchor_price
                if run > 0 and (extreme - px) >= retrace_frac * run:
                    end_ts, end_reason = fts, "retrace_75"
                    break
            else:
                extreme = min(extreme, px)
                if px >= start_price + 4 * tick:
                    end_ts, end_reason = fts, "terminal_reversal"
                    break
                run = anchor_price - extreme
                if run > 0 and (px - extreme) >= retrace_frac * run:
                    end_ts, end_reason = fts, "retrace_75"
                    break
        else:
            if not forward.empty:
                end_ts = pd.Timestamp(forward["bucket_ts"].iloc[-1])

        event_id = f"{row['detector']}_{et_date}_{ts.strftime('%Y%m%dT%H%M%S')}_{direction}"
        events.append(
            {
                "event_id": event_id,
                "detector": row["detector"],
                "et_date": et_date,
                "symbol": symbol,
                "event_start_time": ts.isoformat(),
                "event_end_time": pd.Timestamp(end_ts).isoformat(),
                "event_direction": direction,
                "start_price": start_price,
                "anchor_price": anchor_price,
                "initial_move_ticks": float(initial_move_ticks),
                "end_reason": end_reason,
                "residual_z": float(row["residual_z"]) if pd.notna(row.get("residual_z")) else None,
                "move_z_5m": float(row["move_z_5m"]) if pd.notna(row.get("move_z_5m")) else None,
            }
        )
        active_until[et_date] = pd.Timestamp(end_ts)

    return pd.DataFrame(events)


def label_events(
    events: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    R_ticks: float = 4,
    C_ticks: float = 4,
    H_minutes: int = 15,
    symbol: str = "TY",
) -> pd.DataFrame:
    """Attach primary barrier labels at event start."""
    if events.empty:
        return events.copy()
    tick = TICK_SIZE[symbol]
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]
    rows = []
    for _, ev in events.iterrows():
        lab = first_barrier_label(
            prices,
            start_ts=pd.Timestamp(ev["event_start_time"]),
            direction=int(ev["event_direction"]),
            R_ticks=R_ticks,
            C_ticks=C_ticks,
            H=pd.Timedelta(minutes=H_minutes),
            tick_size=tick,
        )
        row = ev.to_dict()
        row["label"] = lab.get("label")
        row["label_reason"] = lab.get("reason")
        row["first_barrier"] = lab.get("first_barrier")
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_detector(events: pd.DataFrame) -> dict[str, Any]:
    if events.empty:
        return {
            "n_events": 0,
            "n_dates": 0,
            "exhaustion_rate": None,
            "continuation_rate": None,
            "unresolved_rate": None,
            "median_initial_move_ticks": None,
            "max_date_share": None,
        }
    n = len(events)
    labels = events["label"].astype(float)
    resolved = labels.isin([0, 1])
    n_res = int(resolved.sum())
    n_ex = int((labels == 1).sum())
    n_cont = int((labels == 0).sum())
    n_un = int((labels == -1).sum())
    by_date = events.groupby("et_date").size()
    return {
        "n_events": n,
        "n_dates": int(events["et_date"].nunique()),
        "exhaustion_rate": (n_ex / n_res) if n_res else None,
        "continuation_rate": (n_cont / n_res) if n_res else None,
        "unresolved_rate": n_un / n,
        "n_resolved": n_res,
        "median_initial_move_ticks": float(events["initial_move_ticks"].median()),
        "max_date_share": float(by_date.max() / n),
        "mean_events_per_date": float(n / max(events["et_date"].nunique(), 1)),
    }
