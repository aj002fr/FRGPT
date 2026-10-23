"""Tests for E10 apparent absorption features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.absorption import (
    apparent_absorption_features,
    apparent_absorption_score,
    fit_absorption_z_stats,
)


def test_absorption_when_stuck_at_high() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=12, freq="1min", tz="UTC")
    # Rise then sit at extreme with continued buy flow
    price = [110.0 + i * (1 / 64) for i in range(6)] + [110.0 + 5 / 64] * 6
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": price,
            "agg_1m": 150.0,
            "trade_intensity": 25.0,
            "displayed_depth_mean": 400.0,
            "size_imbalance_mean": -0.2,
            "spread_mean": 0.015625,
            "realized_vol": 0.01,
            "minutes_since_open": np.arange(12) + 60,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = apparent_absorption_features(panel, event)
    assert feats["bars_at_extreme"] is not None and feats["bars_at_extreme"] >= 5
    assert feats["directional_dv01_at_extreme"] is not None and feats["directional_dv01_at_extreme"] > 0
    assert feats["seconds_at_extreme"] == feats["bars_at_extreme"] * 60
    assert feats["fraction_of_trades_without_price_advance"] is not None
    assert feats["fraction_of_trades_without_price_advance"] > 0.3


def test_absorption_score_z() -> None:
    train = pd.DataFrame(
        {
            "directional_dv01_at_extreme": [1.0, 2.0, 3.0, 4.0],
            "fraction_of_trades_without_price_advance": [0.2, 0.4, 0.6, 0.8],
            "seconds_at_extreme": [60.0, 120.0, 180.0, 240.0],
            "new_extreme_rate": [0.4, 0.3, 0.2, 0.1],
        }
    )
    zstats = fit_absorption_z_stats(train)
    row = train.iloc[-1].to_dict()
    score = apparent_absorption_score(row, zstats)
    assert score is not None and score > 0
