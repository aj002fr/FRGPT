"""Tests for E07 impact-shortfall features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.impact_shortfall import (
    build_bar_impact_frame,
    event_impact_shortfall,
    fit_fixed_aggression,
    fit_ridge,
    mark_event_bars,
)


def _panel(n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2026-04-04 10:00", periods=n, freq="1min", tz="UTC")
    rng = np.random.default_rng(0)
    flow = rng.normal(0, 200, size=n)
    # Price responds partially to flow
    dpx = 0.00002 * flow + rng.normal(0, 0.002, size=n)
    price = 110.0 + np.cumsum(dpx)
    return pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": price,
            "agg_1m": flow,
            "spread_mean": 0.015625,
            "displayed_depth_mean": 500.0,
            "realized_vol": 0.01,
            "trade_intensity": 20.0,
            "minutes_since_open": np.arange(n),
            "residual_z": rng.normal(size=n),
        }
    )


def test_fixed_and_ridge_fit() -> None:
    bars = build_bar_impact_frame(_panel())
    train = bars.dropna(subset=["y_move_ticks", "agg_1m"])
    fixed = fit_fixed_aggression(train)
    ridge = fit_ridge(train)
    assert fixed.scale is not None
    assert ridge.n_train >= 50
    x = train[["agg_1m"]].to_numpy(dtype=float)[:5]
    pred = fixed.predict(x)
    assert len(pred) == 5
    assert np.isfinite(pred).all()


def test_event_shortfall_finite() -> None:
    panel = _panel()
    bars = build_bar_impact_frame(panel)
    model = fit_ridge(bars.dropna(subset=["y_move_ticks"]))
    event = {
        "et_date": "2026-04-04",
        "event_start_time": panel["bucket_ts"].iloc[40].isoformat(),
        "event_direction": 1,
    }
    feats = event_impact_shortfall(panel, event, model)
    assert feats["impact_shortfall"] is not None
    assert np.isfinite(feats["impact_shortfall"])


def test_mark_event_bars() -> None:
    panel = _panel(30)
    events = pd.DataFrame(
        [
            {
                "et_date": "2026-04-04",
                "event_start_time": panel["bucket_ts"].iloc[10].isoformat(),
            }
        ]
    )
    mask = mark_event_bars(panel, events, pad_minutes=5)
    assert int(mask.sum()) >= 5
