"""Tests for E11 liquidity helpers."""

from __future__ import annotations

import pandas as pd

from scripts.move_exhaustion.features.liquidity import (
    add_liquidity_buckets,
    depth_bucket,
    fit_liquidity_thresholds,
    interaction_product,
)


def test_buckets_and_interaction() -> None:
    df = pd.DataFrame(
        {
            "displayed_depth": [10, 50, 100, 200, 300],
            "spread": [0.01, 0.01, 0.02, 0.03, 0.04],
            "realized_vol": [0.001, 0.002, 0.003, 0.004, 0.005],
            "impact_shortfall": [1.0, 0.5, 0.0, -0.5, -1.0],
        }
    )
    thr = fit_liquidity_thresholds(df)
    assert depth_bucket(10, thr) == "low_depth"
    assert depth_bucket(300, thr) == "high_depth"
    out = add_liquidity_buckets(df, thr)
    assert set(out["depth_regime"]) <= {"low_depth", "normal_depth", "high_depth", "unknown"}
    prod = interaction_product(df, "impact_shortfall", "displayed_depth", "x")
    assert len(prod) == 5
    assert prod.notna().all()
