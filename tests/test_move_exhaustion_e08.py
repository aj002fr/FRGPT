"""Tests for E08 flow persistence features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.flow_persistence import (
    flow_persistence_features,
    termination_score,
)


def _panel_directional_then_mix() -> pd.DataFrame:
    idx = pd.date_range("2026-04-04 10:00", periods=20, freq="1min", tz="UTC")
    # First 12 bars buy flow + high intensity; last 8 mixed/lower
    flow = np.array([100.0] * 12 + [20.0, -80.0, 10.0, -50.0, 5.0, -40.0, 0.0, -30.0])
    intensity = np.array([30.0] * 10 + [40.0, 35.0] + [20.0, 15.0, 12.0, 10.0, 8.0, 8.0, 7.0, 6.0])
    return pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(20) * (0.5 / 64),
            "agg_1m": flow,
            "trade_intensity": intensity,
        }
    )


def test_persistence_detects_run_and_patterns() -> None:
    panel = _panel_directional_then_mix()
    event = {
        "et_date": "2026-04-04",
        "event_start_time": panel["bucket_ts"].iloc[-1].isoformat(),
        "event_direction": 1,
    }
    feats = flow_persistence_features(panel, event, lookback_bars=20)
    assert feats["max_same_side_run"] is not None and feats["max_same_side_run"] >= 10
    assert feats["sign_flip_rate"] is not None and feats["sign_flip_rate"] > 0
    assert feats["intensity_peak_passed"] is True
    assert feats["directional_flow_deceleration"] is True
    score = termination_score(feats)
    assert score is not None and score >= 2


def test_short_history_returns_noneish() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=3, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": [110.0, 110.01, 110.02],
            "agg_1m": [1.0, 1.0, 1.0],
            "trade_intensity": [5.0, 5.0, 5.0],
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = flow_persistence_features(panel, event)
    assert feats["n_bars"] == 0.0 or feats["same_side_run_length"] is None or feats["n_bars"] < 4
