"""Barrier outcome labels (Move Exhaustion V2, PROGRAM.md §4.2).

From ``t*`` (first touch of B), competing barriers on the mid:

* Reversal barrier: ``-R`` ticks from ``m_t*`` (against move direction).
* Continuation barrier: ``+C`` ticks beyond ``m_t*`` (in move direction).
* ``Y = REVERSAL`` if the reversal barrier is touched first;
  ``CONTINUATION`` if the continuation barrier is touched first;
  ``OSCILLATORY_UNRESOLVED`` if neither is touched within horizon ``H``.
* If both barriers are touched inside one event burst (same ``ts_event``), the
  label is ``UNRESOLVED_AMBIGUOUS`` and the episode is excluded downstream.

The horizon is wall-clock ``H`` minutes (price-change-time horizon variants are
deferred/inactive for X1 per PROGRAM.md §10). All outputs are deterministic:
rows are canonically sorted by ``(ts_event, sequence)`` and first-touch is
resolved in that order; a same-``ts_event`` burst that touches both barriers
resolves to ``UNRESOLVED_AMBIGUOUS`` regardless of intra-burst order.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data.session import expected_session

REVERSAL = "REVERSAL"
CONTINUATION = "CONTINUATION"
OSCILLATORY_UNRESOLVED = "OSCILLATORY_UNRESOLVED"
UNRESOLVED_AMBIGUOUS = "UNRESOLVED_AMBIGUOUS"
CENSORED_INCOMPLETE_HORIZON = "CENSORED_INCOMPLETE_HORIZON"

OUTCOME_CLASSES: Tuple[str, ...] = (REVERSAL, CONTINUATION, OSCILLATORY_UNRESOLVED)
EXCLUDED_LABEL: str = UNRESOLVED_AMBIGUOUS

CONSUMED_COLUMNS: Tuple[str, ...] = (
    "trading_date", "ts_event", "sequence", "mid",
)
REQUIRED_COLUMNS: Tuple[str, ...] = ("trading_date", "ts_event", "sequence", "mid")

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
    missing_ep = [
        c for c in (
            "episode_id", "trading_date", "direction", "t_star_ts_event",
            "t_star_sequence", "t_star_mid",
        ) if c not in episodes.columns
    ]
    if missing_ep:
        raise ValueError(f"episodes frame missing required columns: {sorted(missing_ep)}")
    missing_frame = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing_frame:
        raise ValueError(f"frame missing required columns: {sorted(missing_frame)}")
    for name in (R_ticks, C_ticks, H_minutes, tick_size):
        if not (isinstance(name, (int, float)) and name > 0):
            raise ValueError(f"label parameters must be positive numbers, got {name!r}")


def _locate_t_star(
    ts_arr: np.ndarray,
    seq_arr: np.ndarray,
    t_star_ts: pd.Timestamp,
    t_star_seq: int,
) -> int:
    """Locate t* in O(log N + same-timestamp burst size)."""
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


def assign_labels(
    episodes: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    R_ticks: float = DEFAULT_R_TICKS,
    C_ticks: float = DEFAULT_C_TICKS,
    H_minutes: float = DEFAULT_H_MINUTES,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> pd.DataFrame:
    """Assign barrier outcomes to episodes.

    Parameters
    ----------
    episodes : pd.DataFrame
        Output of :func:`episodes.builder.build_episodes` (or a compatible
        frame carrying ``episode_id``, ``direction``, ``t_star_ts_event``,
        ``t_star_sequence``, ``t_star_mid``).
    frame : pd.DataFrame
        Event frame with ``ts_event``, ``sequence``, ``mid`` covering the
        episodes' forward horizons. Rows are sorted canonically by
        ``(ts_event, sequence)`` inside this function.
    R_ticks : float
        Reversal barrier distance (frozen primary 4).
    C_ticks : float
        Continuation barrier distance (frozen primary 4).
    H_minutes : float
        Wall-clock horizon in minutes (frozen primary 15).
    tick_size : float
        Instrument tick size in price units (frozen 1/64 for ZN).

    Returns
    -------
    pd.DataFrame
        One row per input episode with ``outcome`` (one of REVERSAL /
        CONTINUATION / OSCILLATORY_UNRESOLVED / UNRESOLVED_AMBIGUOUS),
        ``barrier_touched`` (first barrier, or UNRESOLVED_AMBIGUOUS /
        None), ``barrier_ts_event``, ``barrier_sequence``, and wall-clock
        seconds from t* to the first barrier touch.
    """
    _validate(
        episodes,
        frame,
        R_ticks=R_ticks,
        C_ticks=C_ticks,
        H_minutes=H_minutes,
        tick_size=tick_size,
    )

    work = frame[list(REQUIRED_COLUMNS)].copy()
    work["trading_date"] = pd.to_datetime(
        work["trading_date"], errors="raise",
    ).dt.date.astype(str)
    work["ts_event"] = _as_utc(work["ts_event"])
    work["sequence"] = pd.to_numeric(work["sequence"], errors="raise")
    work["mid"] = pd.to_numeric(work["mid"], errors="raise").astype(float)
    work = work.sort_values(["ts_event", "sequence"], kind="stable").reset_index(drop=True)

    ts_arr = work["ts_event"].to_numpy(dtype="datetime64[ns]")
    seq_arr = work["sequence"].to_numpy()
    mid_arr = work["mid"].to_numpy(dtype=float)

    eps = episodes.copy().reset_index(drop=True)
    eps["trading_date"] = pd.to_datetime(
        eps["trading_date"], errors="raise",
    ).dt.date.astype(str)
    eps["t_star_ts_event"] = _as_utc(eps["t_star_ts_event"])
    eps["t_star_sequence"] = pd.to_numeric(eps["t_star_sequence"], errors="raise")
    eps["t_star_mid"] = pd.to_numeric(eps["t_star_mid"], errors="raise").astype(float)
    eps["direction"] = eps["direction"].astype(int)

    outcomes: List[str] = []
    touched: List[Optional[str]] = []
    barrier_ts: List[Optional[pd.Timestamp]] = []
    barrier_seq: List[Optional[int]] = []
    wall_s: List[Optional[float]] = []
    horizon_complete: List[bool] = []
    censor_reason: List[Optional[str]] = []
    last_event_by_date = work.groupby("trading_date")["ts_event"].max().to_dict()

    for _, ep in eps.iterrows():
        d = int(ep["direction"])
        s = float(d)
        m_star = float(ep["t_star_mid"])
        cont_barrier = m_star + s * C_ticks * tick_size
        rev_barrier = m_star - s * R_ticks * tick_size
        t_star_pos = _locate_t_star(
            ts_arr,
            seq_arr,
            ep["t_star_ts_event"],
            int(ep["t_star_sequence"]),
        )
        t_star_ts = ts_arr[t_star_pos]
        horizon_end = t_star_ts + np.timedelta64(int(H_minutes * 60), "s")
        trading_date = str(ep["trading_date"])
        schedule_end = expected_session(trading_date)["expected_last_trade_utc"]
        last_event = last_event_by_date.get(trading_date)
        reason: Optional[str] = None
        if schedule_end is None:
            reason = "scheduled_session_closed"
        elif horizon_end > pd.Timestamp(schedule_end).to_datetime64():
            reason = "horizon_exceeds_scheduled_close"
        elif last_event is None or pd.Timestamp(last_event).to_datetime64() < horizon_end:
            reason = "source_events_end_before_horizon"
        if reason is not None:
            outcomes.append(CENSORED_INCOMPLETE_HORIZON)
            touched.append(None)
            barrier_ts.append(None)
            barrier_seq.append(None)
            wall_s.append(None)
            horizon_complete.append(False)
            censor_reason.append(reason)
            continue

        first_touch_pos: Optional[int] = None
        first_touch_kind: Optional[str] = None
        window_end = int(np.searchsorted(ts_arr, horizon_end, side="right"))
        window_start = t_star_pos + 1
        window = mid_arr[window_start:window_end]
        cont_hits = np.flatnonzero(s * window >= s * cont_barrier)
        rev_hits = np.flatnonzero(s * window <= s * rev_barrier)
        cont_touch = window_start + int(cont_hits[0]) if cont_hits.size else None
        rev_touch = window_start + int(rev_hits[0]) if rev_hits.size else None

        if cont_touch is not None or rev_touch is not None:
            first_touch_pos = min(
                x for x in (cont_touch, rev_touch) if x is not None
            )
            first_ts = ts_arr[first_touch_pos]
            burst_left = int(np.searchsorted(ts_arr, first_ts, side="left"))
            burst_right = min(
                window_end,
                int(np.searchsorted(ts_arr, first_ts, side="right")),
            )
            burst = mid_arr[max(window_start, burst_left):burst_right]
            burst_cont = bool(np.any(s * burst >= s * cont_barrier))
            burst_rev = bool(np.any(s * burst <= s * rev_barrier))
            if burst_cont and burst_rev:
                first_touch_kind = UNRESOLVED_AMBIGUOUS
            elif cont_touch == first_touch_pos:
                first_touch_kind = CONTINUATION
            else:
                first_touch_kind = REVERSAL

        if first_touch_pos is None:
            outcome = OSCILLATORY_UNRESOLVED
            touch_kind = None
            touch_ts = None
            touch_seq = None
            touch_wall = None
        else:
            assert first_touch_kind is not None
            outcome = first_touch_kind
            touch_kind = None if first_touch_kind == UNRESOLVED_AMBIGUOUS else first_touch_kind
            touch_ts = pd.Timestamp(ts_arr[first_touch_pos]).tz_localize("UTC")
            touch_seq = int(seq_arr[first_touch_pos])
            touch_wall = float((ts_arr[first_touch_pos] - t_star_ts) / np.timedelta64(1, "s"))

        outcomes.append(outcome)
        touched.append(touch_kind)
        barrier_ts.append(touch_ts)
        barrier_seq.append(touch_seq)
        wall_s.append(touch_wall)
        horizon_complete.append(True)
        censor_reason.append(None)

    labeled = eps[[
        "episode_id", "trading_date", "direction", "t_star_ts_event",
        "t_star_sequence", "t_star_mid",
    ]].copy()
    labeled["outcome"] = outcomes
    labeled["barrier_touched"] = touched
    labeled["barrier_ts_event"] = pd.array(
        barrier_ts,
        dtype="datetime64[ns, UTC]",
    )
    labeled["barrier_sequence"] = pd.array(barrier_seq, dtype="Int64")
    labeled["time_to_barrier_wall_s"] = wall_s
    labeled["horizon_complete"] = horizon_complete
    labeled["censor_reason"] = censor_reason
    return labeled
