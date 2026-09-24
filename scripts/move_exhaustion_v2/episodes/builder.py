"""Price-only directional episode builder (Move Exhaustion V2, PROGRAM.md §4.1).

The builder consumes only the mid-price path plus event identity columns, so
the episode definition is provably flow-free (X1 contract assumption; enforced
by ``test_episode_builder_is_flow_free`` in the test suite). It never touches
flow, depth, size, or volume fields.

Frozen mapping (config/frozen/episodes.yaml)::

    B_ticks                 -> B_ticks (default primary 8)
    x_ticks                 -> x_ticks (default primary 2)
    dedupe.one_active_episode_per_direction
    dedupe.no_new_anchor_inside_unresolved_prior_horizon
                            -> per-direction block of max_horizon_minutes
    dedupe.min_anchor_separation_minutes_wall_clock
                            -> min_anchor_separation_minutes

Semantics implemented here:

* One candidate state machine per direction (``direction in {-1, +1}``).
* A candidate anchors at its first event and qualifies at the first touch of
  ``B`` (``s*(mid - anchor_mid) >= B_ticks * tick_size``) that occurs before a
  counter-directional pullback from the running extreme exceeds ``x`` ticks
  (strictly: ``pullback_ticks > x_ticks``).
* A failed candidate restarts immediately at the current event (fresh anchor,
  fresh running extreme).
* After qualification, only that direction is blocked until
  ``t_star + max_horizon_minutes``. Opposite-direction candidate state is
  preserved. Anchor separation is enforced per direction.

All outputs are deterministic: the frame is canonically sorted by
``(trading_date, ts_event, sequence)`` before processing, sessions are
processed independently, and no randomness is involved.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CONSUMED_COLUMNS: Tuple[str, ...] = ("trading_date", "ts_event", "sequence", "mid")
"""Every column the episode definition reads. Flow-free by construction."""

REQUIRED_COLUMNS: Tuple[str, ...] = ("trading_date", "ts_event", "sequence", "mid")

DEFAULT_B_TICKS: float = 8.0
DEFAULT_X_TICKS: float = 2.0
DEFAULT_MAX_HORIZON_MINUTES: float = 15.0
DEFAULT_MIN_ANCHOR_SEPARATION_MINUTES: float = 5.0
DEFAULT_TICK_SIZE: float = 0.015625  # 1/64 point, ZN


def _as_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="raise")


def _tick_round(value: float) -> float:
    return round(float(value), 6)


def _validate(
    frame: pd.DataFrame,
    *,
    B_ticks: float,
    x_ticks: float,
    directions: Tuple[int, ...],
    max_horizon_minutes: float,
    min_anchor_separation_minutes: float,
    tick_size: float,
) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"frame missing required columns: {sorted(missing)}")
    if not pd.api.types.is_numeric_dtype(frame["mid"]):
        raise TypeError("column 'mid' must be numeric")
    for name in (B_ticks, x_ticks, tick_size):
        if not (isinstance(name, (int, float)) and name > 0):
            raise ValueError(f"episode parameters must be positive numbers, got {name!r}")
    for name in (max_horizon_minutes, min_anchor_separation_minutes):
        if not (isinstance(name, (int, float)) and name >= 0):
            raise ValueError(f"episode parameters must be nonnegative, got {name!r}")
    if not directions:
        raise ValueError("directions must not be empty")
    if any(d not in (-1, 1) for d in directions):
        raise ValueError(f"directions must be -1 or +1, got {directions!r}")


def _realized_vol_bps(mids: np.ndarray) -> float:
    """Per-event log-return realized vol in bps over a mid sub-series."""
    mids = np.asarray(mids, dtype=float)
    if len(mids) < 3:
        return float("nan")
    log_mids = np.log(mids)
    rets = np.diff(log_mids)
    if rets.size < 2:
        return float("nan")
    return float(np.std(rets, ddof=1) * 1e4)


def build_episodes(
    frame: pd.DataFrame,
    *,
    B_ticks: float = DEFAULT_B_TICKS,
    x_ticks: float = DEFAULT_X_TICKS,
    directions: Tuple[int, ...] = (-1, 1),
    max_horizon_minutes: float = DEFAULT_MAX_HORIZON_MINUTES,
    min_anchor_separation_minutes: float = DEFAULT_MIN_ANCHOR_SEPARATION_MINUTES,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> pd.DataFrame:
    """Build price-only directional episodes from an event frame.

    Parameters
    ----------
    frame : pd.DataFrame
        Must contain ``trading_date``, ``ts_event``, ``sequence``, ``mid``.
        Any additional columns (e.g. trade indicators) are ignored by the
        episode definition. ``ts_event`` is interpreted as UTC.
    B_ticks : float
        Qualification move in ticks (frozen primary 8).
    x_ticks : float
        Counter-directional pullback tolerance in ticks from the running
        extreme (frozen primary 2). A pullback strictly exceeding ``x_ticks``
        fails the candidate.
    directions : tuple of {-1, +1}
        Directions to scan. Defaults to both.
    max_horizon_minutes : float
        After qualification, same-direction anchors are blocked until
        ``t_star + max_horizon_minutes`` wall clock.
    min_anchor_separation_minutes : float
        Consecutive anchors are additionally kept at least this far apart in
        wall clock.
    tick_size : float
        Instrument tick size in price units (frozen 1/64 for ZN).

    Returns
    -------
    pd.DataFrame
        One row per qualified episode with anchor/t* identity, move and
        pullback metrics, and time-to-B in the wall/LOB/price-change clocks.
        Trade-clock fields are NaN because the builder consumes no trade
        indicator; the X1 runner joins the store's trade clock when reporting
        ``trade_count`` / ``time_to_B_trades``.
    """
    _validate(
        frame,
        B_ticks=B_ticks,
        x_ticks=x_ticks,
        directions=directions,
        max_horizon_minutes=max_horizon_minutes,
        min_anchor_separation_minutes=min_anchor_separation_minutes,
        tick_size=tick_size,
    )

    work = frame[list(REQUIRED_COLUMNS)].copy()
    work["ts_event"] = _as_utc(work["ts_event"])
    work["trading_date"] = pd.to_datetime(work["trading_date"], errors="raise").dt.normalize()
    work["sequence"] = pd.to_numeric(work["sequence"], errors="raise")
    work["mid"] = pd.to_numeric(work["mid"], errors="raise").astype(float)
    work = work.sort_values(["trading_date", "ts_event", "sequence"], kind="stable").reset_index(drop=True)

    b_price = B_ticks * tick_size
    x_price = x_ticks * tick_size
    horizon_delta = pd.Timedelta(minutes=max_horizon_minutes)
    separation_delta = pd.Timedelta(minutes=min_anchor_separation_minutes)

    records: List[dict] = []
    suppressed_qualifications = 0
    episode_counter = 0

    for trading_date, group in work.groupby("trading_date", sort=True):
        ts = group["ts_event"].to_numpy(dtype="datetime64[ns]")
        seq = group["sequence"].to_numpy()
        mids = group["mid"].to_numpy(dtype=float)
        dates = group["trading_date"].to_numpy(dtype="datetime64[ns]")

        # Candidate state per direction.
        anchor_row: Dict[int, Optional[int]] = {d: None for d in directions}
        anchor_ts: Dict[int, Optional[np.datetime64]] = {d: None for d in directions}
        anchor_mid: Dict[int, Optional[float]] = {d: None for d in directions}
        extreme_mid: Dict[int, Optional[float]] = {d: None for d in directions}
        max_pullback_ticks: Dict[int, float] = {d: 0.0 for d in directions}

        block_until: Dict[int, Optional[np.datetime64]] = {
            d: None for d in directions
        }
        last_qualified_anchor_ts: Dict[int, Optional[np.datetime64]] = {
            d: None for d in directions
        }

        for i in range(len(group)):
            event_ts = ts[i]
            event_seq = int(seq[i])
            event_mid = mids[i]

            for d in directions:
                if block_until[d] is not None and event_ts < block_until[d]:
                    suppressed_qualifications += 1
                    continue
                if anchor_row[d] is None:
                    last_anchor = last_qualified_anchor_ts[d]
                    if (
                        last_anchor is not None
                        and event_ts < last_anchor + separation_delta.to_timedelta64()
                    ):
                        suppressed_qualifications += 1
                        continue
                    anchor_row[d] = i
                    anchor_ts[d] = event_ts
                    anchor_mid[d] = event_mid
                    extreme_mid[d] = event_mid
                    max_pullback_ticks[d] = 0.0

                current = anchor_row[d]
                assert current is not None
                s = float(d)
                move = s * (event_mid - anchor_mid[d])

                if d == 1:
                    extreme_mid[d] = max(extreme_mid[d], event_mid)
                else:
                    extreme_mid[d] = min(extreme_mid[d], event_mid)

                pullback = s * (extreme_mid[d] - event_mid)

                max_pullback_ticks[d] = max(max_pullback_ticks[d], _tick_round(pullback / tick_size))

                if move >= b_price:
                    # First B touch before pullback from the running extreme
                    # exceeded x -> qualified episode (anchor already passed
                    # the block/separation gate above).
                    move_ticks = _tick_round(move / tick_size)
                    pullback_ticks = max_pullback_ticks[d]
                    anchor_m = anchor_mid[d]
                    tstar_m = event_mid
                    anchor_ts_value = ts[current]
                    anchor_seq = int(seq[current])

                    window = group.iloc[current : i + 1]
                    mid_changed = (
                        window["mid"].to_numpy(dtype=float)[1:]
                        != window["mid"].to_numpy(dtype=float)[:-1]
                    ).sum()
                    lob_events = i - current
                    wall_s = float((event_ts - anchor_ts_value) / np.timedelta64(1, "s"))

                    episode_counter += 1
                    records.append(
                        {
                            "episode_id": f"X1-{episode_counter:06d}",
                            "trading_date": pd.Timestamp(trading_date),
                            "direction": int(d),
                            "anchor_ts_event": pd.Timestamp(anchor_ts_value).tz_localize("UTC"),
                            "anchor_sequence": anchor_seq,
                            "anchor_mid": float(anchor_m),
                            "t_star_ts_event": pd.Timestamp(event_ts).tz_localize("UTC"),
                            "t_star_sequence": event_seq,
                            "t_star_mid": float(tstar_m),
                            "move_ticks": move_ticks,
                            "move_bps": _tick_round(
                                (s * (tstar_m - anchor_m) / anchor_m) * 1e4
                            ),
                            "max_pullback_ticks": pullback_ticks,
                            "time_to_B_wall_s": wall_s,
                            "time_to_B_lob_events": lob_events,
                            "time_to_B_price_changes": int(mid_changed),
                            "time_to_B_trades": float("nan"),
                            "lob_event_count": lob_events,
                            "trade_count": float("nan"),
                            "price_change_count": int(mid_changed),
                            "realized_vol_during_move_bps": _realized_vol_bps(
                                window["mid"].to_numpy(dtype=float)
                            ),
                        }
                    )

                    # Same-direction block only. Opposite-direction candidate
                    # state is intentionally untouched.
                    block_until[d] = event_ts + horizon_delta.to_timedelta64()
                    last_qualified_anchor_ts[d] = anchor_ts_value

                    anchor_row[d] = None
                    anchor_ts[d] = None
                    anchor_mid[d] = None
                    extreme_mid[d] = None

                elif pullback > x_price:
                    # Counter-directional pullback from the running extreme
                    # exceeded x: fail and restart at the current event.
                    anchor_row[d] = i
                    anchor_ts[d] = event_ts
                    anchor_mid[d] = event_mid
                    extreme_mid[d] = event_mid
                    max_pullback_ticks[d] = 0.0

    episodes = pd.DataFrame(records)
    if episodes.empty:
        episodes = pd.DataFrame(
            columns=[
                "episode_id",
                "trading_date",
                "direction",
                "anchor_ts_event",
                "anchor_sequence",
                "anchor_mid",
                "t_star_ts_event",
                "t_star_sequence",
                "t_star_mid",
                "move_ticks",
                "move_bps",
                "max_pullback_ticks",
                "time_to_B_wall_s",
                "time_to_B_lob_events",
                "time_to_B_price_changes",
                "time_to_B_trades",
                "lob_event_count",
                "trade_count",
                "price_change_count",
                "realized_vol_during_move_bps",
            ]
        )
    episodes.attrs["suppressed_qualifications"] = suppressed_qualifications
    return episodes
