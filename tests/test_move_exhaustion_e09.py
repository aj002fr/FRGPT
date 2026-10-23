"""Tests for E09 trade composition features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.trade_composition import (
    classify_composition,
    fit_composition_thresholds,
    large_small_composition_features,
)


def test_large_share_splits_flow() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=12, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(12) * (1 / 64),
            "agg_1m": 200.0,
            "large_trade_share": [0.8] * 6 + [0.2] * 6,
            "trade_intensity": 40.0,
            "avg_trade_size": [10.0] * 6 + [2.0] * 6,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = large_small_composition_features(panel, event)
    assert feats["large_trade_share"] is not None
    assert feats["change_in_large_trade_share"] is not None
    assert feats["change_in_large_trade_share"] < 0
    assert feats["small_directional_flow"] is not None
    assert feats["small_directional_flow"] > feats["large_directional_flow"]


def test_classify_disappearance() -> None:
    thr = fit_composition_thresholds(
        pd.DataFrame(
            {
                "large_directional_flow": [100.0, 80.0, 10.0, 5.0],
                "small_directional_flow": [5.0, 10.0, 90.0, 100.0],
                "change_in_large_trade_share": [-0.4, -0.3, -0.2, 0.1],
            }
        )
    )
    row = {
        "large_directional_flow": 5.0,
        "small_directional_flow": 100.0,
        "change_in_large_trade_share": -0.35,
        "opposite_large_trade_arrival": 0.0,
        "large_flow_early": 80.0,
        "large_flow_recent": 5.0,
    }
    assert classify_composition(row, thr) in {
        "large_flow_disappearance",
        "small_led_continuation",
    }
