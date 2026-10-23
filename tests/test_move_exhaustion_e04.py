"""Tests for E04 flow-dominance categories."""

from __future__ import annotations

import pandas as pd

from scripts.move_exhaustion.features.flow_categories import (
    classify_flow_dominance,
    event_flow_price_features,
    fit_category_thresholds,
)


def test_classify_aggression_vs_absorption() -> None:
    thr = {
        "flow_hi": 10.0,
        "flow_lo": 2.0,
        "eff_hi": 0.5,
        "eff_lo": 0.1,
        "progress_hi": 3.0,
        "n_train": 100,
    }
    assert (
        classify_flow_dominance(
            {"abs_flow": 20.0, "impact_efficiency": 1.0, "directional_progress_ticks": 5.0},
            thr,
        )
        == "aggression_dominant"
    )
    assert (
        classify_flow_dominance(
            {"abs_flow": 20.0, "impact_efficiency": 0.05, "directional_progress_ticks": 1.0},
            thr,
        )
        == "apparent_absorption_dominant"
    )
    assert (
        classify_flow_dominance(
            {"abs_flow": 1.0, "impact_efficiency": 2.0, "directional_progress_ticks": 5.0},
            thr,
        )
        == "price_without_flow"
    )


def test_thresholds_from_train_only() -> None:
    df = pd.DataFrame(
        {
            "abs_flow": [1, 2, 3, 10, 20, 30],
            "impact_efficiency": [0.1, 0.2, 0.3, 0.8, 0.9, 1.0],
            "directional_progress_ticks": [1, 2, 2, 4, 5, 6],
        }
    )
    thr = fit_category_thresholds(df)
    assert thr["flow_hi"] >= thr["flow_lo"]
    assert thr["eff_hi"] >= thr["eff_lo"]


def test_event_features_causal_shape() -> None:
    idx = pd.date_range("2026-04-03 10:00", periods=20, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-03",
            "price": 110.0 + (1 / 64) * pd.Series(range(20)),
            "agg_5m": 5.0,
            "agg_1m": 1.0,
        }
    )
    event = {
        "et_date": "2026-04-03",
        "event_start_time": idx[10].isoformat(),
        "event_direction": 1,
    }
    feats = event_flow_price_features(panel, event)
    assert feats["directional_progress_ticks"] is not None
    assert feats["abs_flow"] is not None
