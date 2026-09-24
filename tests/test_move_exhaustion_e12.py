"""Tests for E12 microprice/imbalance features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.microprice import microprice_imbalance_features


def test_detects_microprice_turn() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=10, freq="1min", tz="UTC")
    # Price drifts up; microprice_dev starts positive then turns negative
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(10) * (0.5 / 64),
            "microprice_dev_last": [0.02, 0.015, 0.01, 0.005, 0.0, -0.005, -0.01, -0.015, -0.02, -0.025],
            "size_imbalance_last": [0.3, 0.2, 0.1, 0.05, 0.0, -0.05, -0.1, -0.2, -0.25, -0.3],
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = microprice_imbalance_features(panel, event)
    assert feats["microprice_signal_change"] is not None
    assert feats["microprice_signal_change"] < 0
    assert feats["imbalance_change"] is not None
    assert feats["imbalance_change"] < 0
    assert feats["price_continues_microprice_turns"] is True
    assert feats["imbalance_reverses_before_price"] is True
