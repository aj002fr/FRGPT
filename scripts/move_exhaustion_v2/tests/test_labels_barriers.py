"""Synthetic tests for barrier outcome labels (X1 core)."""

import numpy as np
import pandas as pd
import pytest

from scripts.move_exhaustion_v2.episodes.builder import build_episodes
from scripts.move_exhaustion_v2.labels.barriers import (
    CENSORED_INCOMPLETE_HORIZON,
    CONTINUATION,
    OSCILLATORY_UNRESOLVED,
    REVERSAL,
    UNRESOLVED_AMBIGUOUS,
    assign_labels,
)

from conftest import TICK, _mk_frame


def _episode(B=8, x=2, H=15, directions=(-1, 1)):
    frame = _mk_frame([100.0, 100.0625, 100.125])
    return build_episodes(
        frame,
        B_ticks=B,
        x_ticks=x,
        max_horizon_minutes=H,
        directions=directions,
    )


def _frame(episode_path, forward, step_s=10, is_trade=None):
    """Coherent frame: episode path (through t*) + forward rows after t*."""
    ep = _mk_frame(episode_path, step_s=step_s)
    t_star_ts = ep["ts_event"].iloc[-1]
    fwd = _mk_frame(
        forward,
        start=t_star_ts,
        step_s=step_s,
        sequence_offset=len(episode_path),
        ts_offset_s=step_s,
        is_trade=is_trade,
    )
    frame = pd.concat([ep, fwd], ignore_index=True)
    horizon_ts = t_star_ts + pd.Timedelta(minutes=15)
    if frame["ts_event"].max() < horizon_ts:
        sentinel = {
            "trading_date": "2024-01-02",
            "ts_event": horizon_ts,
            "sequence": int(frame["sequence"].max()) + 1,
            "mid": episode_path[-1],
        }
        if "is_trade" in frame:
            sentinel["is_trade"] = False
        frame = pd.concat([frame, pd.DataFrame([sentinel])], ignore_index=True)
    return frame


def test_continuation_when_continuation_barrier_touched_first():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.25, 100.5, 99.9375])
    lab = assign_labels(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = lab.iloc[0]
    assert row["outcome"] == CONTINUATION
    assert row["barrier_touched"] == CONTINUATION
    assert row["barrier_ts_event"] == pd.Timestamp("2024-01-02 09:30:30", tz="UTC")
    assert row["time_to_barrier_wall_s"] == 10.0


def test_reversal_when_reversal_barrier_touched_first():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.0, 99.875, 100.5])
    lab = assign_labels(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = lab.iloc[0]
    assert row["outcome"] == REVERSAL
    assert row["barrier_touched"] == REVERSAL
    assert row["barrier_ts_event"] == pd.Timestamp("2024-01-02 09:30:30", tz="UTC")
    assert row["time_to_barrier_wall_s"] == 10.0


def test_down_move_labels_are_direction_signed():
    frame = _mk_frame([100.0, 99.875, 99.75])
    eps = build_episodes(frame, B_ticks=16, x_ticks=2, max_horizon_minutes=15, directions=(-1,))
    full = _frame([100.0, 99.875, 99.75], [99.625, 99.5, 100.25])
    lab = assign_labels(eps, full, R_ticks=4, C_ticks=4, H_minutes=15)
    row = lab.iloc[0]
    assert row["direction"] == -1
    assert row["outcome"] == CONTINUATION  # lower = continuation for down-move
    assert row["time_to_barrier_wall_s"] == 10.0


def test_ambiguous_when_both_barriers_touched_at_same_ts_event():
    eps = _episode()
    anchor = _mk_frame([100.0, 100.0625, 100.125], step_s=10)
    t_star_ts = anchor["ts_event"].iloc[-1]
    burst = pd.DataFrame(
        {
            "trading_date": "2024-01-02",
            "ts_event": [t_star_ts + pd.Timedelta(seconds=10)] * 2,
            "sequence": [4, 5],
            "mid": [100.1875, 100.0625],
        }
    )
    frame = pd.concat([anchor, burst], ignore_index=True)
    frame = pd.concat(
        [
            frame,
            pd.DataFrame([{
                "trading_date": "2024-01-02",
                "ts_event": t_star_ts + pd.Timedelta(minutes=15),
                "sequence": 6,
                "mid": 100.125,
            }]),
        ],
        ignore_index=True,
    )
    lab = assign_labels(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = lab.iloc[0]
    assert row["outcome"] == UNRESOLVED_AMBIGUOUS
    assert row["barrier_touched"] is None or pd.isna(row["barrier_touched"])
    assert row["barrier_ts_event"] == pd.Timestamp("2024-01-02 09:30:30", tz="UTC")
    assert row["time_to_barrier_wall_s"] == 10.0


def test_unresolved_when_neither_barrier_touched_within_horizon():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.15625, 100.09375, 100.125])
    lab = assign_labels(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = lab.iloc[0]
    assert row["outcome"] == OSCILLATORY_UNRESOLVED
    assert row["barrier_touched"] is None or pd.isna(row["barrier_touched"])
    assert pd.isna(row["barrier_ts_event"])
    assert pd.isna(row["time_to_barrier_wall_s"])


def test_barrier_touch_at_exact_horizon_counts():
    # Build the episode on the same 900s grid so t* aligns with the frame.
    build_frame = _mk_frame([100.0, 100.0625, 100.125], step_s=900)
    eps = build_episodes(build_frame, B_ticks=8, x_ticks=2, max_horizon_minutes=15)
    # Touch at exactly +15 minutes from t*: must be CONTINUATION.
    frame = _frame([100.0, 100.0625, 100.125], [100.1875], step_s=900)
    lab = assign_labels(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    assert lab.iloc[0]["outcome"] == CONTINUATION
    assert lab.iloc[0]["time_to_barrier_wall_s"] == 900.0


def test_barrier_touch_after_horizon_does_not_count():
    build_frame = _mk_frame([100.0, 100.0625, 100.125], step_s=901)
    eps = build_episodes(build_frame, B_ticks=8, x_ticks=2)
    frame = _frame([100.0, 100.0625, 100.125], [100.1875], step_s=901)
    lab = assign_labels(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    assert lab.iloc[0]["outcome"] == OSCILLATORY_UNRESOLVED


def test_incomplete_source_horizon_is_censored_before_barriers():
    eps = _episode()
    frame = pd.concat(
        [
            _mk_frame([100.0, 100.0625, 100.125]),
            _mk_frame(
                [100.5],
                start="2024-01-02 09:30:20",
                ts_offset_s=10,
                sequence_offset=3,
            ),
        ],
        ignore_index=True,
    )
    row = assign_labels(eps, frame).iloc[0]
    assert row["outcome"] == CENSORED_INCOMPLETE_HORIZON
    assert row["horizon_complete"] == False
    assert row["censor_reason"] == "source_events_end_before_horizon"


def test_horizon_past_scheduled_close_is_censored():
    base = _mk_frame(
        [100.0, 100.0625, 100.125],
        start="2024-01-02 21:50:00",
        step_s=60,
    )
    eps = build_episodes(base, B_ticks=8, x_ticks=2, max_horizon_minutes=15)
    coverage = _mk_frame(
        [100.125],
        start="2024-01-02 22:10:00",
        sequence_offset=3,
    )
    row = assign_labels(eps, pd.concat([base, coverage], ignore_index=True)).iloc[0]
    assert row["outcome"] == CENSORED_INCOMPLETE_HORIZON
    assert row["censor_reason"] == "horizon_exceeds_scheduled_close"


def test_barrier_columns_preserve_nondefault_episode_index():
    eps = _episode()
    eps.index = pd.Index([42])
    frame = _frame([100.0, 100.0625, 100.125], [100.1875])
    lab = assign_labels(eps, frame)
    assert lab.iloc[0]["outcome"] == CONTINUATION
    assert pd.notna(lab.iloc[0]["barrier_ts_event"])
    assert pd.notna(lab.iloc[0]["barrier_sequence"])


def test_missing_t_star_row_raises():
    eps = _episode()
    frame = _mk_frame([100.25, 100.5], start="2024-01-02 09:31:00")
    with pytest.raises(ValueError, match="not found"):
        assign_labels(eps, frame)


def test_validation_errors():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.25])
    with pytest.raises(ValueError, match="missing required columns"):
        assign_labels(eps, frame.drop(columns=["mid"]))
    with pytest.raises(ValueError, match="positive"):
        assign_labels(eps, frame, R_ticks=0)
    with pytest.raises(ValueError, match="missing required columns"):
        assign_labels(eps.drop(columns=["direction"]), frame)