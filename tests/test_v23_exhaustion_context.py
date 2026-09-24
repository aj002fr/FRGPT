"""Tests for V2.3 exhaustion-context annotations (display-only)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v23.exhaustion_context import (
    STRATEGY_STATUS,
    annotate_exhaustion_panel,
    latest_exhaustion_context,
)


def _panel(n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2026-04-04 10:00", periods=n, freq="1min", tz="UTC")
    # quiet then a directional push
    price = 110.0 + np.concatenate([np.zeros(n - 10), np.arange(10) * (1 / 64)])
    df = pd.DataFrame(
        {
            "price": price,
            "fair_value": price - 0.02,
            "residual": 0.02,
            "residual_z": np.concatenate([np.zeros(n - 5), np.full(5, 2.5)]),
            "agg_5m": np.concatenate([np.zeros(n - 10), np.full(10, 500.0)]),
            "FV_agg_5m": np.concatenate([np.zeros(n - 10), np.full(10, 200.0)]),
            "TU_agg_5m": np.concatenate([np.zeros(n - 10), np.full(10, 150.0)]),
            "US_agg_5m": np.concatenate([np.zeros(n - 10), np.full(10, 180.0)]),
            "spread_mean": 0.015625,
            "displayed_depth_mean": 200.0,
            "size_imbalance_mean": np.concatenate([np.zeros(n - 5), np.full(5, -0.4)]),
            "microprice_dev_mean": np.concatenate([np.zeros(n - 5), np.full(5, -0.01)]),
        },
        index=idx,
    )
    return df


def test_strategy_status_label() -> None:
    assert "NOT AFTER-COST VALIDATED" in STRATEGY_STATUS


def test_annotate_produces_context_columns() -> None:
    panel = annotate_exhaustion_panel(_panel())
    for col in (
        "move_event",
        "v4_align",
        "wide_spread",
        "book_fading",
        "curve_wide",
        "stand_aside",
        "exh_prob",
        "exh_state",
    ):
        assert col in panel.columns


def test_opening_stand_aside() -> None:
    # 01:30 ET = 05:30 UTC in EDT
    idx = pd.date_range("2026-04-04 05:00", periods=40, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "price": 110.0,
            "fair_value": 110.0,
            "residual": 0.0,
            "residual_z": 0.0,
            "agg_5m": 0.0,
            "spread_mean": 0.01,
            "displayed_depth_mean": 100.0,
            "size_imbalance_mean": 0.0,
            "microprice_dev_mean": 0.0,
        },
        index=idx,
    )
    ctx = latest_exhaustion_context(annotate_exhaustion_panel(df))
    assert ctx["stand_aside"] is True
    assert ctx["exh_state"] == "STAND_ASIDE"


def test_roll_calendar_proxy() -> None:
    idx = pd.date_range("2026-03-05 14:00", periods=40, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "price": 110.0,
            "fair_value": 110.0,
            "residual": 0.0,
            "residual_z": 0.0,
            "agg_5m": 0.0,
            "spread_mean": 0.01,
            "displayed_depth_mean": 100.0,
            "size_imbalance_mean": 0.0,
            "microprice_dev_mean": 0.0,
        },
        index=idx,
    )
    ctx = latest_exhaustion_context(annotate_exhaustion_panel(df))
    assert ctx["stand_aside"] is True
    assert "roll_period" in ctx["stand_aside_reason"]
