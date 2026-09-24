from __future__ import annotations

import numpy as np
import pytest

from scripts.autoresearch.fixed_env.evaluator import (
    construct_common_anchors,
    evaluate_brier,
    evaluator_cell_masks,
    evaluator_overlapping_cell_masks,
)
from scripts.autoresearch.fixed_env.outcomes import (
    breakout_hold_label,
    e002_close_first_passage,
    future_window,
    range_label,
    signed_return,
)
from scripts.autoresearch.fixed_env.statistics import (
    enforce_sample_gates,
    equal_date_effect,
    holm,
    non_overlapping_anchors,
    paired_date_cluster_bootstrap,
    permute_within_dates,
    repeated_cluster_permutation_pvalue,
    stable_seed,
)


def synthetic_losses(effect: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dates = np.repeat([f"2026-01-{day:02d}" for day in range(1, 31)], 8)
    rng = np.random.default_rng(1234)
    baseline = rng.uniform(0.12, 0.32, len(dates))
    augmented = baseline - effect
    return dates, baseline, augmented


def test_paired_equal_date_clustered_planted_signal_and_null() -> None:
    dates, baseline, augmented = synthetic_losses(0.04)
    result = paired_date_cluster_bootstrap(
        dates, baseline, augmented, repetitions=499, seed_key="planted",
    )
    assert result.effect == pytest.approx(0.04)
    assert result.ci_low > 0
    p_value, null = repeated_cluster_permutation_pvalue(
        dates, baseline, augmented, repetitions=499, seed_key="planted",
    )
    assert result.p_one_sided == p_value
    assert p_value < 0.01
    assert len(null) == 499
    repeated = paired_date_cluster_bootstrap(
        dates, baseline, augmented, repetitions=499, seed_key="planted",
    )
    assert repeated.p_one_sided == result.p_one_sided

    null_result = paired_date_cluster_bootstrap(
        dates, baseline, baseline, repetitions=199, seed_key="null",
    )
    null_p, _ = repeated_cluster_permutation_pvalue(
        dates, baseline, baseline, repetitions=199, seed_key="null",
    )
    assert null_result.effect == 0
    assert null_result.p_one_sided == null_p
    assert null_p == 1.0


def test_equal_date_estimand_not_row_weighted() -> None:
    dates = ["A"] * 100 + ["B"]
    baseline = [1.0] * 100 + [0.0]
    augmented = [0.0] * 100 + [1.0]
    assert equal_date_effect(dates, baseline, augmented) == pytest.approx(0.0)
    assert np.mean(np.asarray(baseline) - augmented) > 0.9


def test_holm_stable_seed_and_cluster_permutations_are_deterministic() -> None:
    assert holm([0.01, 0.04, 0.02], alpha=0.05) == [True, True, True]
    assert holm([0.01, 0.06, 0.03], alpha=0.05) == [True, False, False]
    assert stable_seed("E999", "validation") == stable_seed("E999", "validation")
    assert stable_seed("E999", "validation") != stable_seed("E999", "confirmation")
    dates = ["A"] * 5 + ["B"] * 5
    values = np.arange(10)
    first = permute_within_dates(values, dates, seed_key="fixed")
    second = permute_within_dates(values, dates, seed_key="fixed")
    np.testing.assert_array_equal(first, second)
    assert set(first[:5]) == set(range(5))
    assert set(first[5:]) == set(range(5, 10))


def test_sampling_and_minimum_cluster_gates() -> None:
    assert non_overlapping_anchors([0, 2, 5, 9, 10], horizon=5) == [0, 5, 10]
    enforce_sample_gates(
        [f"D{i}" for i in range(25)], [1] * 25,
        min_dates=25, min_positive=25,
    )
    with pytest.raises(ValueError):
        enforce_sample_gates(["A"] * 100, [1] * 100)


def test_evaluator_jointly_compares_paired_brier_losses() -> None:
    dates = np.repeat([f"D{i}" for i in range(25)], 4)
    target = np.tile([0, 0, 1, 1], 25)
    baseline = np.full(100, 0.5)
    augmented = np.where(target == 1, 0.8, 0.2)
    result = evaluate_brier(
        dates, target, baseline, augmented, repetitions=199, seed_key="brier",
    )
    assert result.augmented_brier < result.baseline_brier
    assert result.inference.ci_low > 0


def test_outcomes_use_strictly_future_paths_and_costs() -> None:
    timestamps = np.array([0, 60, 120, 180], dtype=np.int64)
    prices = np.array([100.0, 101.0, 102.0, 99.0])
    selected_ts, selected_prices = future_window(timestamps, prices, 60, 120)
    np.testing.assert_array_equal(selected_ts, [120, 180])
    np.testing.assert_array_equal(selected_prices, [102.0, 99.0])
    assert signed_return(100, 101, direction=1, cost=0.25) == 0.75
    assert signed_return(100, 99, direction=-1, cost=0.25) == 0.75
    assert breakout_hold_label([101, 102, 102], 100, breakout=1, hold_fraction=0.5) == "UP_HOLD"
    assert breakout_hold_label([101, 99], 100, breakout=1, hold_fraction=0.5) == "REVERSAL"
    assert range_label([100.2, 99.9], 100, threshold=0.5) == "RANGE"


def test_e002_converges_before_full_adverse_close_passage() -> None:
    minute = 60 * 1_000_000_000
    timestamps = np.arange(1, 4) * minute
    assert e002_close_first_passage(
        timestamps, np.array([99.6, 101.0, 101.5]), 100.0, formation_gap=2.0,
    ) == 1  # Exact 50% convergence equality crosses first.
    assert e002_close_first_passage(
        timestamps, np.array([98.0, 101.0, 102.0]), 100.0, formation_gap=2.0,
    ) == 0  # Exact 100% adverse equality crosses first.
    assert e002_close_first_passage(
        timestamps, np.array([100.2, 100.4, 100.9]), 100.0, formation_gap=2.0,
    ) == 0
    assert e002_close_first_passage(
        timestamps, np.array([100.4, 99.0, 98.0]), 100.0, formation_gap=-2.0,
    ) == 1


def test_common_anchors_require_80pct_and_near_horizon_observation() -> None:
    minute = 60 * 1_000_000_000
    timestamps = np.arange(0, 41) * minute
    dates = np.repeat("D1", len(timestamps))
    available = np.ones(len(timestamps), dtype=bool)
    complete = construct_common_anchors(
        dates, timestamps, {"common": available}, horizon_ns=30 * minute,
        session_close_ns={"D1": 40 * minute},
    )
    assert 0 in complete.timestamps_ns

    available[10:17] = False  # Anchor 0 has 23/30 future rows: below 80%.
    anchors = construct_common_anchors(
        dates, timestamps, {"common": available}, horizon_ns=30 * minute,
        session_close_ns={"D1": 40 * minute},
    )
    assert 0 not in anchors.timestamps_ns

    sparse = np.ones(len(timestamps), dtype=bool)
    sparse[28:31] = False  # 27/30 rows but none within two minutes of horizon.
    anchors = construct_common_anchors(
        dates, timestamps, {"common": sparse}, horizon_ns=30 * minute,
        session_close_ns={"D1": 40 * minute},
    )
    assert 0 not in anchors.timestamps_ns


def test_evaluator_masks_preserve_all_and_subgroup_overlap() -> None:
    raw = {"ALL": [True, True, True], "SUBGROUP": [False, True, True]}
    masks = evaluator_overlapping_cell_masks(raw, priority=["ALL", "SUBGROUP"])
    np.testing.assert_array_equal(masks["ALL"], raw["ALL"])
    np.testing.assert_array_equal(masks["SUBGROUP"], raw["SUBGROUP"])
    legacy = evaluator_cell_masks(raw, priority=["ALL", "SUBGROUP"])
    np.testing.assert_array_equal(legacy["SUBGROUP"], raw["SUBGROUP"])
    with pytest.raises(ValueError, match="exactly once"):
        evaluator_cell_masks(raw, priority=["ALL", "ALL"])

