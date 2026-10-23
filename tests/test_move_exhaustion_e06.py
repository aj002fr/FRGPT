"""Tests for E06 impact-efficiency decay features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.impact_efficiency import (
    classify_efficiency_regime,
    fit_decay_thresholds,
    impact_efficiency_at_horizon,
    impact_efficiency_decay_features,
)


def _panel(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-04-04 10:00", periods=n, freq="1min", tz="UTC")
    # Early: high impact (price moves a lot per flow); recent: weak impact
    price = [110.0]
    for i in range(1, n):
        # first half bigger ticks, second half smaller
        step = (2 / 64) if i < n // 2 else (0.25 / 64)
        price.append(price[-1] + step)
    return pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": price,
            "agg_1m": 100.0,
            "agg_5m": 500.0,
        }
    )


def test_horizon_efficiency_positive() -> None:
    panel = _panel()
    event = {
        "et_date": "2026-04-04",
        "event_start_time": panel["bucket_ts"].iloc[10].isoformat(),
        "event_direction": 1,
    }
    feats = impact_efficiency_at_horizon(panel, event, bars=5)
    assert feats["impact_efficiency"] is not None
    assert feats["directional_progress_ticks"] is not None
    assert feats["directional_progress_ticks"] > 0


def test_decay_negative_when_recent_weaker() -> None:
    panel = _panel(24)
    start = panel["bucket_ts"].iloc[0]
    event = {
        "et_date": "2026-04-04",
        "event_start_time": start.isoformat(),
        "event_direction": 1,
    }
    obs = start + pd.Timedelta(minutes=18)
    feats = impact_efficiency_decay_features(panel, event, observation_time=obs, landmark_minutes=18)
    assert feats["impact_efficiency_decay"] is not None
    # Recent third should have weaker efficiency than early third
    assert feats["impact_efficiency_decay"] < 0
    assert feats["already_retracing"] is False
    assert feats["window_progress_ticks"] is not None and feats["window_progress_ticks"] > 0


def test_regime_thresholds() -> None:
    thr = fit_decay_thresholds(pd.Series([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]))
    assert classify_efficiency_regime(-2.0, thr) == "extremely_declining"
    assert classify_efficiency_regime(2.0, thr) == "improving"
    assert classify_efficiency_regime(None, thr) == "unknown"
