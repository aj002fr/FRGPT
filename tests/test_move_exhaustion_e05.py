"""Tests for E05 per-tick features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    per_tick_features,
    univariate_logistic_auc,
)


def test_per_tick_positive_when_flow_and_progress() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=20, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(20) * (1 / 64),
            "agg_5m": 850.0,
            "agg_1m": 170.0,
            "trade_intensity": 20.0,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[10].isoformat(),
        "event_direction": 1,
    }
    feats = per_tick_features(panel, event)
    assert feats["dv01_per_tick"] is not None and feats["dv01_per_tick"] > 0
    assert feats["contracts_per_tick"] is not None and feats["contracts_per_tick"] > 0


def test_decile_and_logistic_smoke() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    # Higher x → more exhaustion
    y = (x + rng.normal(scale=0.5, size=200) > 0).astype(int)
    df = pd.DataFrame({"dv01_per_tick": x, "label": y})
    # map to 0/1 barrier labels
    df["label"] = df["label"].map({1: 1, 0: 0})
    curve = decile_exhaustion_curve(df, "dv01_per_tick")
    assert not curve.empty
    res = univariate_logistic_auc(df, "dv01_per_tick")
    assert res["ok"] is True
    assert (res.get("auc") or 0) > 0.5 or res.get("coef_standardized", 0) != 0
