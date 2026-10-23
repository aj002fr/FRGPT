"""Synthetic tests for forward-return / MAE-MFE / clock path metrics."""

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
)
from scripts.move_exhaustion_v2.labels.path_metrics import compute_path_metrics

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
    ep_is_trade = None if is_trade is None else list(is_trade)[: len(episode_path)]
    ep = _mk_frame(episode_path, step_s=step_s, is_trade=ep_is_trade)
    t_star_ts = ep["ts_event"].iloc[-1]
    fwd_is_trade = None if is_trade is None else list(is_trade)[len(episode_path):]
    fwd = _mk_frame(
        forward,
        start=t_star_ts,
        step_s=step_s,
        sequence_offset=len(episode_path),
        ts_offset_s=step_s,
        is_trade=fwd_is_trade,
    )
    frame = pd.concat([ep, fwd], ignore_index=True)
    horizon_ts = t_star_ts + pd.Timedelta(minutes=15)
    if frame["ts_event"].max() < horizon_ts:
        sentinel = {
            "trading_date": "2024-01-02",
            "ts_event": horizon_ts,
            "sequence": int(frame["sequence"].max()) + 1,
            "mid": forward[-1],
        }
        if "is_trade" in frame:
            sentinel["is_trade"] = False
        frame = pd.concat([frame, pd.DataFrame([sentinel])], ignore_index=True)
    return frame


def test_forward_return_and_excursions_for_continuation_path():
    eps = _episode()
    frame = _frame(
        [100.0, 100.0625, 100.125],
        [100.25, 100.5, 100.375, 100.25, 100.0],
        is_trade=[True, False, True, False, True, False, True, False],
    )
    pm = compute_path_metrics(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = pm.iloc[0]
    assert row["outcome"] == CONTINUATION
    # m_t* = 100.125; last window mid = 100.0 -> -8 ticks directional.
    assert row["forward_return_bps"] == pytest.approx(-8 * TICK / 100.125 * 1e4, rel=1e-6)
    # Window = rows 3..7 (100.25, 100.5, 100.375, 100.25, 100.0): max 100.5
    # (+24 ticks MFE), min 100.0 (-8 ticks MAE).
    assert row["mfe_ticks"] == pytest.approx(24.0)
    assert row["mae_ticks"] == pytest.approx(8.0)
    # First barrier touch: +4 ticks at row 3 (t=10s after t*).
    assert row["time_to_barrier_wall_s"] == 10.0
    assert row["time_to_barrier_lob_events"] == 1
    assert row["time_to_barrier_trades"] == 0  # row 3 is not a trade
    assert row["time_to_barrier_price_changes"] == 1


def test_metrics_for_down_move_are_direction_signed():
    frame = _mk_frame([100.0, 99.875, 99.75])
    eps = build_episodes(frame, B_ticks=16, x_ticks=2, max_horizon_minutes=15, directions=(-1,))
    fwd = _frame(
        [100.0, 99.875, 99.75],
        [99.625, 99.5, 100.0],
        is_trade=[True, False, True, False, True, False],
    )
    pm = compute_path_metrics(eps, fwd, R_ticks=4, C_ticks=4, H_minutes=15)
    row = pm.iloc[0]
    assert row["outcome"] == CONTINUATION
    # End mid 100.0 vs m_t* 99.75: directional return -16 ticks (adverse).
    assert row["forward_return_bps"] == pytest.approx(-16 * TICK / 99.75 * 1e4, rel=1e-6)
    # MFE = max favorable = down to 99.5 (-16 ticks); MAE = max adverse =
    # up to 100.0 (+16 ticks above m_t*).
    assert row["mfe_ticks"] == pytest.approx(16.0)
    assert row["mae_ticks"] == pytest.approx(16.0)
    # Continuation barrier (99.6875) touched at row 3 (99.625): 1 LOB event,
    # 0 trades in that row.
    assert row["time_to_barrier_wall_s"] == 10.0
    assert row["time_to_barrier_lob_events"] == 1
    assert row["time_to_barrier_trades"] == 0


def test_trade_clock_requires_is_trade_column():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.25, 100.5])
    pm = compute_path_metrics(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = pm.iloc[0]
    assert row["outcome"] == CONTINUATION
    assert row["time_to_barrier_wall_s"] == 10.0
    assert row["time_to_barrier_lob_events"] == 1
    assert np.isnan(row["time_to_barrier_trades"])
    assert row["time_to_barrier_price_changes"] == 1


def test_price_change_clock_counts_distinct_mid_prints():
    eps = _episode()
    # C=8 -> continuation barrier at 100.25; first three forward prints stay
    # below it and only the fourth print touches it.
    frame = _frame(
        [100.0, 100.0625, 100.125],
        [100.1875, 100.1875, 100.1875, 100.25],
    )
    pm = compute_path_metrics(eps, frame, R_ticks=4, C_ticks=8, H_minutes=15)
    row = pm.iloc[0]
    assert row["outcome"] == CONTINUATION
    assert row["time_to_barrier_lob_events"] == 4
    # Two distinct mid prints in the window: t* mid -> 100.1875, then
    # 100.1875 -> 100.25.
    assert row["time_to_barrier_price_changes"] == 2


def test_unresolved_metrics_are_nan_with_bounded_excursions():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.15625, 100.09375, 100.125])
    pm = compute_path_metrics(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = pm.iloc[0]
    assert row["outcome"] == OSCILLATORY_UNRESOLVED
    assert np.isnan(row["time_to_barrier_wall_s"])
    assert np.isnan(row["time_to_barrier_lob_events"])
    assert np.isnan(row["time_to_barrier_trades"])
    assert np.isnan(row["time_to_barrier_price_changes"])
    assert row["mfe_ticks"] == pytest.approx(2.0)
    assert row["mae_ticks"] == pytest.approx(2.0)


def test_ambiguous_metrics_report_the_burst_time():
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
    pm = compute_path_metrics(eps, frame, R_ticks=4, C_ticks=4, H_minutes=15)
    row = pm.iloc[0]
    assert row["outcome"] == UNRESOLVED_AMBIGUOUS
    assert row["time_to_barrier_wall_s"] == 10.0
    assert row["time_to_barrier_lob_events"] == 1
    assert row["mfe_ticks"] == pytest.approx(4.0)
    assert row["mae_ticks"] == pytest.approx(4.0)


def test_censored_episode_has_no_path_summaries():
    eps = _episode()
    frame = _mk_frame([100.0, 100.0625, 100.125, 100.5])
    row = compute_path_metrics(eps, frame).iloc[0]
    assert row["outcome"] == CENSORED_INCOMPLETE_HORIZON
    assert np.isnan(row["forward_return_bps"])
    assert np.isnan(row["mae_ticks"])
    assert np.isnan(row["mfe_ticks"])


def test_validation_errors():
    eps = _episode()
    frame = _frame([100.0, 100.0625, 100.125], [100.25])
    with pytest.raises(ValueError, match="missing required columns"):
        compute_path_metrics(eps, frame.drop(columns=["sequence"]))
    with pytest.raises(ValueError, match="positive"):
        compute_path_metrics(eps, frame, H_minutes=0)