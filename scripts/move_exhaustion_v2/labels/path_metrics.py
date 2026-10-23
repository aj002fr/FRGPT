"""Path metrics over the label window (Move Exhaustion V2, PROGRAM.md §4.2).

Computes, for each episode over the forward window ``(t*, t* + H]`` in
canonical ``(ts_event, sequence)`` order:

* ``forward_return_bps``: directional forward mid return from ``m_t*`` to the
  last event in the window (positive = further in the episode's move
  direction), in bps.
* ``mae_ticks`` / ``mfe_ticks``: nonnegative maximum adverse / favorable
  excursions from ``m_t*`` in ticks (``mae`` is counter-directional, ``mfe``
  is move-directional).
* Barrier-touch times in the wall clock (seconds), LOB-event clock (rows),
  trade clock (trade rows; requires an ``is_trade`` column, otherwise NaN),
  and price-change clock (rows where the mid changed from the previous row).

The window deliberately excludes the t* row itself; events sharing t*'s
``ts_event`` with a later ``sequence`` are part of the forward window.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .barriers import (
    CONTINUATION,
    OSCILLATORY_UNRESOLVED,
    REVERSAL,
    UNRESOLVED_AMBIGUOUS,
    assign_labels,
)

CONSUMED_COLUMNS: Tuple[str, ...] = (
    "trading_date", "ts_event", "sequence", "mid", "is_trade",
)
REQUIRED_COLUMNS: Tuple[str, ...] = (
    "trading_date", "ts_event", "sequence", "mid",
)

DEFAULT_R_TICKS: float = 4.0
DEFAULT_C_TICKS: float = 4.0
DEFAULT_H_MINUTES: float = 15.0
DEFAULT_TICK_SIZE: float = 0.015625


def _as_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="raise")


def _validate(
    episodes: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    R_ticks: float,
    C_ticks: float,
    H_minutes: float,
    tick_size: float,
) -> None:
    missing_ep = [c for c in ("episode_id", "direction", "t_star_ts_event", "t_star_sequence", "t_star_mid") if c not in episodes.columns]
    if missing_ep:
        raise ValueError(f"episodes frame missing required columns: {sorted(missing_ep)}")
    missing_frame = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing_frame:
        raise ValueError(f"frame missing required columns: {sorted(missing_frame)}")
    for name in (R_ticks, C_ticks, H_minutes, tick_size):
        if not (isinstance(name, (int, float)) and name > 0):
            raise ValueError(f"path-metric parameters must be positive numbers, got {name!r}")


def _locate_t_star(
    ts_arr: np.ndarray,
    seq_arr: np.ndarray,
    t_star_ts: pd.Timestamp,
    t_star_seq: int,
) -> int:
    needle = t_star_ts.to_datetime64()
    left = int(np.searchsorted(ts_arr, needle, side="left"))
    right = int(np.searchsorted(ts_arr, needle, side="right"))
    matches = np.flatnonzero(seq_arr[left:right] == t_star_seq)
    if matches.size == 0:
        raise ValueError(
            f"episode t* row (ts_event={t_star_ts}, sequence={t_star_seq}) "
            "not found in frame"
        )
    if matches.size != 1:
        raise ValueError(
            f"episode t* row (ts_event={t_star_ts}, sequence={t_star_seq}) "
            f"matched {matches.size} frame rows"
        )
    return left + int(matches[0])


def compute_path_metrics(
    episodes: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    R_ticks: float = DEFAULT_R_TICKS,
    C_ticks: float = DEFAULT_C_TICKS,
    H_minutes: float = DEFAULT_H_MINUTES,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> pd.DataFrame:
    """Compute forward-return / excursion / clock metrics for each episode.

    Parameters
    ----------
    episodes : pd.DataFrame
        Episode frame from :func:`episodes.builder.build_episodes`.
    frame : pd.DataFrame
        Event frame with ``ts_event``, ``sequence``, ``mid``; optionally an
        ``is_trade`` boolean column for the trade clock.
    R_ticks, C_ticks, H_minutes, tick_size
        Barrier and horizon parameters (frozen primary R=C=4, H=15).

    Returns
    -------
    pd.DataFrame
        One row per episode: outcome/barrier identity from
        :func:`assign_labels` plus ``forward_return_bps``, ``mae_ticks``,
        ``mfe_ticks``, and ``time_to_barrier_{wall_s,lob_events,trades,
        price_changes}``. Barrier-time columns are NaN when no barrier is
        touched; trade-clock columns are NaN when ``is_trade`` is absent.
    """
    _validate(
        episodes,
        frame,
        R_ticks=R_ticks,
        C_ticks=C_ticks,
        H_minutes=H_minutes,
        tick_size=tick_size,
    )

    work = frame.copy()
    work["ts_event"] = _as_utc(work["ts_event"])
    work["sequence"] = pd.to_numeric(work["sequence"], errors="raise")
    work["mid"] = pd.to_numeric(work["mid"], errors="raise").astype(float)
    if "is_trade" in work.columns:
        work["is_trade"] = work["is_trade"].astype(bool)
    work = work.sort_values(["ts_event", "sequence"], kind="stable").reset_index(drop=True)

    ts_arr = work["ts_event"].to_numpy(dtype="datetime64[ns]")
    seq_arr = work["sequence"].to_numpy()
    mid_arr = work["mid"].to_numpy(dtype=float)
    has_trades = "is_trade" in work.columns
    trade_arr = work["is_trade"].to_numpy(dtype=bool) if has_trades else None

    eps = episodes.copy()
    eps["t_star_ts_event"] = _as_utc(eps["t_star_ts_event"])
    eps["t_star_sequence"] = pd.to_numeric(eps["t_star_sequence"], errors="raise")
    eps["t_star_mid"] = pd.to_numeric(eps["t_star_mid"], errors="raise").astype(float)
    eps["direction"] = eps["direction"].astype(int)

    labeled = assign_labels(
        eps,
        frame,
        R_ticks=R_ticks,
        C_ticks=C_ticks,
        H_minutes=H_minutes,
        tick_size=tick_size,
    )
    if labeled["episode_id"].duplicated().any():
        raise ValueError("episode_id must be unique")
    labeled_by_id = labeled.set_index("episode_id", verify_integrity=True)

    # Price-change mask: mid differs from the previous row (canonical order).
    changed = np.zeros(len(work), dtype=bool)
    changed[1:] = mid_arr[1:] != mid_arr[:-1]

    rows: List[dict] = []
    for _, ep in eps.iterrows():
        d = int(ep["direction"])
        s = float(d)
        m_star = float(ep["t_star_mid"])
        t_star_pos = _locate_t_star(
            ts_arr,
            seq_arr,
            ep["t_star_ts_event"],
            int(ep["t_star_sequence"]),
        )
        t_star_ts = ts_arr[t_star_pos]
        horizon_end = t_star_ts + np.timedelta64(int(H_minutes * 60), "s")

        lab = labeled_by_id.loc[ep["episode_id"]]
        window_end = int(np.searchsorted(ts_arr, horizon_end, side="right"))
        if bool(lab["horizon_complete"]):
            window = mid_arr[t_star_pos + 1:window_end]
            max_mid = float(np.max(window)) if window.size else m_star
            min_mid = float(np.min(window)) if window.size else m_star
            end_mid = float(window[-1]) if window.size else m_star
            forward_ret_bps = s * (end_mid - m_star) / m_star * 1e4
            mfe_ticks = max(
                0.0, s * (max_mid - m_star), -s * (m_star - min_mid),
            ) / tick_size
            mae_ticks = max(
                0.0, s * (m_star - min_mid), -s * (max_mid - m_star),
            ) / tick_size
        else:
            forward_ret_bps = mae_ticks = mfe_ticks = np.nan

        # Barrier-touch times in each clock. Reuse the barrier position from
        # assign_labels output.
        barrier_pos: Optional[int] = None
        if pd.notna(lab["barrier_ts_event"]):
            if pd.isna(lab["barrier_sequence"]):
                raise ValueError(
                    f"episode {ep['episode_id']}: barrier timestamp has no sequence"
                )
            barrier_pos = _locate_t_star(
                ts_arr,
                seq_arr,
                lab["barrier_ts_event"],
                int(lab["barrier_sequence"]),
            )

        if barrier_pos is None:
            wall_s = lob_events = trades = price_changes = np.nan
        else:
            wall_s = float((ts_arr[barrier_pos] - t_star_ts) / np.timedelta64(1, "s"))
            lob_events = barrier_pos - t_star_pos
            if trade_arr is not None:
                trades = int(np.count_nonzero(trade_arr[t_star_pos + 1 : barrier_pos + 1]))
            else:
                trades = np.nan
            price_changes = int(np.count_nonzero(changed[t_star_pos + 1 : barrier_pos + 1]))

        rows.append(
            {
                "episode_id": ep["episode_id"],
                "direction": d,
                "t_star_ts_event": ep["t_star_ts_event"],
                "forward_return_bps": round(float(forward_ret_bps), 6),
                "mae_ticks": round(float(mae_ticks), 6),
                "mfe_ticks": round(float(mfe_ticks), 6),
                "time_to_barrier_wall_s": wall_s,
                "time_to_barrier_lob_events": lob_events,
                "time_to_barrier_trades": trades,
                "time_to_barrier_price_changes": price_changes,
            }
        )

    metrics = pd.DataFrame(rows)
    # metrics recomputes the wall-clock touch time; avoid the _x/_y merge
    # collision by dropping the copy carried by assign_labels.
    labeled_drop = labeled.drop(columns=["time_to_barrier_wall_s"])
    out = labeled_drop.merge(metrics, on=["episode_id", "direction", "t_star_ts_event"], how="left")
    return out
