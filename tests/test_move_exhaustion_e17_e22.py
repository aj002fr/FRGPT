"""Unit tests for E17–E22 ML helpers (not full pipeline)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.tree import DecisionTreeClassifier

from scripts.move_exhaustion.reporting.ml_common import (
    expand_hazard_intervals,
    extract_tree_rules,
    quantile_coverage,
)


def test_extract_tree_rules_returns_leaf_conditions() -> None:
    x = np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]] * 3)
    y = np.array([0, 0, 0, 1, 1, 1] * 3)
    clf = DecisionTreeClassifier(max_depth=2, min_samples_leaf=5, random_state=42)
    clf.fit(x, y)
    rules = extract_tree_rules(clf, ["feat_a"])
    assert rules
    assert all("conditions" in r and "exhaustion_rate" in r for r in rules)
    assert any(len(r["conditions"]) >= 0 for r in rules)


def test_expand_hazard_intervals_stops_at_first_barrier() -> None:
    idx = pd.date_range("2026-04-04 12:00", periods=20, freq="1min", tz="UTC")
    prices = pd.Series(110.0 + np.arange(20) * (1 / 64), index=idx)
    # Down move: reversal (up) hits when price rises; continuation when falls further
    event = {
        "event_id": "t1",
        "et_date": "2026-04-04",
        "event_start_time": idx[0].isoformat(),
        "event_direction": -1,
    }
    rows = expand_hazard_intervals(event, prices, horizon_minutes=15, bin_minutes=1)
    assert rows
    assert rows[0]["event_age_min"] == 1.0
    assert sum(r["y_exhaustion"] + r["y_continuation"] for r in rows) <= 1


def test_expand_hazard_intervals_censors_at_horizon() -> None:
    idx = pd.date_range("2026-04-04 12:00", periods=20, freq="1min", tz="UTC")
    prices = pd.Series([110.0] * 20, index=idx)
    event = {
        "event_id": "t2",
        "et_date": "2026-04-04",
        "event_start_time": idx[0].isoformat(),
        "event_direction": 1,
    }
    rows = expand_hazard_intervals(event, prices, horizon_minutes=5, bin_minutes=1)
    assert len(rows) == 5
    assert rows[-1]["censored_horizon"] == 1


def test_quantile_coverage() -> None:
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    pred = np.array([1.5, 2.5, 3.5, 4.5, 5.5])
    cov = quantile_coverage(y, pred)
    assert cov == 1.0
    cov_low = quantile_coverage(y, np.array([0.5, 0.5, 0.5, 0.5, 0.5]))
    assert cov_low == 0.0
