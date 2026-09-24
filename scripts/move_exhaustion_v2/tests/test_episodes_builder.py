"""Synthetic tests for the price-only episode builder (X1 core)."""

import numpy as np
import pandas as pd
import pytest

from scripts.move_exhaustion_v2.episodes.builder import (
    CONSUMED_COLUMNS,
    build_episodes,
)

from conftest import TICK, _mk_frame


def test_consumed_columns_are_exactly_the_flow_free_surface():
    assert list(CONSUMED_COLUMNS) == ["trading_date", "ts_event", "sequence", "mid"]
    flow_markers = {"agg", "flow", "add_", "cxl", "cancel", "size", "volume", "depth"}
    offenders = {c for c in CONSUMED_COLUMNS for m in flow_markers if m in c.lower()}
    assert not offenders


def test_flow_columns_cannot_change_episode_output():
    frame = _mk_frame([100.0, 100.015625, 99.984375, 100.125])
    with_flow = frame.assign(
        aggressive_flow=[10_000, -9_000, 8_000, -7_000],
        displayed_depth=[1, 1_000_000, 2, 2_000_000],
        trade_size=[999, 1, 999, 1],
    )
    expected = build_episodes(frame)
    actual = build_episodes(with_flow)
    pd.testing.assert_frame_equal(actual, expected)


def test_qualifies_on_first_B_touch_without_failing_pullback():
    # anchor 100 -> +1 tick -> -1 tick from extreme -> +8 ticks at t=30s.
    frame = _mk_frame(
        [100.0, 100.015625, 99.984375, 100.125],
        step_s=10,
    )
    eps = build_episodes(frame, B_ticks=8, x_ticks=2, max_horizon_minutes=15)
    assert len(eps) == 1
    row = eps.iloc[0]
    assert row["direction"] == 1
    assert row["anchor_ts_event"] == pd.Timestamp("2024-01-02 09:30:00", tz="UTC")
    assert row["t_star_ts_event"] == pd.Timestamp("2024-01-02 09:30:30", tz="UTC")
    assert row["move_ticks"] == 8.0
    assert row["max_pullback_ticks"] == 2.0  # 2-tick adverse excursion from extreme, <= x
    assert row["time_to_B_wall_s"] == 30.0
    assert row["time_to_B_lob_events"] == 3
    assert row["price_change_count"] == 3
    assert row["move_bps"] == pytest.approx(12.5, rel=1e-6)


def test_pullback_exactly_x_does_not_fail_but_strictly_exceeding_does():
    # Pullback exactly x=2 ticks: still qualifies; max pullback = 2.0.
    frame_exact = _mk_frame(
        [100.0, 100.09375, 100.0625, 100.125],  # +6, -2 from extreme, +8
        step_s=10,
    )
    eps_exact = build_episodes(frame_exact, B_ticks=8, x_ticks=2, max_horizon_minutes=15)
    assert len(eps_exact) == 1
    assert eps_exact.iloc[0]["max_pullback_ticks"] == 2.0

    # Pullback just over x=2 ticks (2.001): candidate fails and restarts at
    # the current event; no episode is emitted from the first run.
    frame_over = _mk_frame(
        [100.0, 100.09375, 100.0624375, 100.125, 100.15625, 100.1875],
        step_s=10,
    )
    eps_over = build_episodes(frame_over, B_ticks=8, x_ticks=2, max_horizon_minutes=15)
    assert len(eps_over) == 1
    row = eps_over.iloc[0]
    # Restart anchored at the pullback event (sequence 2), then +8.004 ticks.
    assert row["anchor_sequence"] == 2
    assert row["anchor_mid"] == pytest.approx(100.0624375)
    assert row["t_star_sequence"] == 5
    assert row["move_ticks"] == pytest.approx(8.004, abs=1e-3)
    assert row["max_pullback_ticks"] == 0.0


def test_directions_are_independent_candidates():
    # -1 qualifies while the +1 candidate survives independently.
    frame = _mk_frame(
        [100.0, 99.875, 100.125, 99.75, 100.0, 100.0625, 100.125],
        step_s=300,
    )
    eps = build_episodes(frame, B_ticks=8, x_ticks=2, max_horizon_minutes=15)
    assert set(eps["direction"]) == {-1, 1}
    down = eps[eps["direction"] == -1].iloc[0]
    up = eps[eps["direction"] == 1].iloc[0]
    assert down["anchor_ts_event"] == pd.Timestamp("2024-01-02 09:30:00", tz="UTC")
    assert down["t_star_ts_event"] == pd.Timestamp("2024-01-02 09:35:00", tz="UTC")
    assert up["anchor_ts_event"] == pd.Timestamp("2024-01-02 09:35:00", tz="UTC")
    assert up["t_star_ts_event"] == pd.Timestamp("2024-01-02 09:40:00", tz="UTC")


def test_same_direction_block_does_not_suppress_opposite_direction():
    # First +1 episode qualifies at t=180s (B=4, x=2, H=5min).
    frame = _mk_frame(
        [100.0, 100.0625, 100.125, 100.0, 99.75, 99.625],
        step_s=120,
    )
    eps = build_episodes(
        frame,
        B_ticks=4,
        x_ticks=2,
        max_horizon_minutes=5,
        min_anchor_separation_minutes=5,
    )
    assert len(eps) == 2
    assert list(eps["direction"]) == [1, -1]
    assert eps.iloc[0]["t_star_ts_event"] == pd.Timestamp("2024-01-02 09:32:00", tz="UTC")
    assert eps.iloc[1]["anchor_ts_event"] == pd.Timestamp("2024-01-02 09:34:00", tz="UTC")
    assert eps.iloc[1]["t_star_ts_event"] == pd.Timestamp("2024-01-02 09:36:00", tz="UTC")


def test_minimum_anchor_separation_is_enforced():
    # H=0 makes the horizon block inert; separation alone must keep anchors
    # >= 5 minutes apart. Episode 2 would qualify at 09:30:40 (30s after the
    # first t*) and must be suppressed.
    frame = _mk_frame(
        [100.0, 100.0625, 100.046875, 100.109375],
        step_s=30,
    )
    eps = build_episodes(
        frame,
        B_ticks=4,
        x_ticks=2,
        max_horizon_minutes=0.0,
        min_anchor_separation_minutes=5,
    )
    assert len(eps) == 1
    assert eps.iloc[0]["t_star_ts_event"] == pd.Timestamp("2024-01-02 09:30:30", tz="UTC")
    assert eps.attrs["suppressed_qualifications"] >= 1


def test_flat_and_noisy_paths_produce_no_episodes():
    flat = _mk_frame([100.0] * 50, step_s=5)
    assert build_episodes(flat, B_ticks=8, x_ticks=2).empty

    # Oscillation within +/- 2 ticks never reaches B=8.
    noisy = _mk_frame([100.0, 100.015625, 99.984375] * 30, step_s=5)
    assert build_episodes(noisy, B_ticks=8, x_ticks=2).empty


def test_output_is_deterministic_under_row_shuffle():
    rng = np.random.default_rng(42)
    mids = [100.0 + TICK * v for v in rng.integers(-6, 6, size=200)]
    frame = _mk_frame(mids, step_s=2)
    shuffled = frame.sample(frac=1.0, random_state=7).reset_index(drop=True)
    a = build_episodes(frame, B_ticks=6, x_ticks=2, max_horizon_minutes=30)
    b = build_episodes(shuffled, B_ticks=6, x_ticks=2, max_horizon_minutes=30)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_multiple_sessions_are_processed_independently():
    day1 = _mk_frame([100.0, 100.0625, 100.125], step_s=10, trading_date="2024-01-02")
    day2 = _mk_frame([100.0, 100.0625, 100.125], step_s=10, trading_date="2024-01-03")
    frame = pd.concat([day1, day2], ignore_index=True)
    eps = build_episodes(frame, B_ticks=4, x_ticks=2, max_horizon_minutes=0)
    assert len(eps) == 2
    assert set(eps["trading_date"].dt.date) == {
        pd.Timestamp("2024-01-02").date(),
        pd.Timestamp("2024-01-03").date(),
    }


def test_validation_errors():
    frame = _mk_frame([100.0, 100.0625, 100.125])
    with pytest.raises(ValueError, match="missing required columns"):
        build_episodes(frame.drop(columns=["mid"]))
    with pytest.raises(ValueError, match="positive"):
        build_episodes(frame, B_ticks=0)
    with pytest.raises(ValueError, match="nonnegative"):
        build_episodes(frame, max_horizon_minutes=-1)
    with pytest.raises(ValueError, match="directions"):
        build_episodes(frame, directions=(0,))