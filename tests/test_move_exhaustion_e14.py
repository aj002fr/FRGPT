"""Tests for E14 cross-tenor flow features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.cross_tenor import cross_tenor_features


def test_all_neighbors_confirm() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=6, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(6) * (1 / 64),
            "TY_agg_5m": 500.0,
            "FV_agg_5m": 200.0,
            "TU_agg_5m": 150.0,
            "US_agg_5m": 180.0,
            "agg_5m": 500.0,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = cross_tenor_features(panel, event)
    assert feats["number_of_confirming_tenors"] == 3.0
    assert feats["curve_flow_breadth"] == 1.0
    assert feats["confirmation_category"] == "all_tenors_confirm_flow"
    assert feats["price_features_available"] is False


def test_target_alone() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=6, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(6) * (1 / 64),
            "TY_agg_5m": 500.0,
            "FV_agg_5m": -200.0,
            "TU_agg_5m": -150.0,
            "US_agg_5m": -180.0,
            "agg_5m": 500.0,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = cross_tenor_features(panel, event)
    assert feats["number_of_confirming_tenors"] == 0.0
    assert feats["confirmation_category"] == "target_moves_alone"
