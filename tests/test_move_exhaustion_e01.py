"""Tests for E01 event detectors / episode builder."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.events.episode_builder import build_events, label_events
from scripts.move_exhaustion.events.shock_detector import annotate_move_features, detect_triggers


def _synth_panel(n: int = 200) -> pd.DataFrame:
    idx = pd.date_range("2026-03-10 06:00", periods=n, freq="1min", tz="UTC")
    # Quiet then a sharp +8 tick move
    price = np.full(n, 110.0)
    price[80:88] = 110.0 + np.arange(1, 9) * (1 / 64)
    price[88:] = price[87]
    residual = (price - 110.0) * 2.0
    agg = np.zeros(n)
    agg[80:88] = 50.0
    return pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-03-10",
            "price": price,
            "day_open_price": 110.0,
            "residual": residual,
            "residual_z": residual / 0.02,
            "agg_1m": agg,
            "agg_5m": pd.Series(agg).rolling(5, min_periods=1).sum().to_numpy(),
            "trade_intensity": np.full(n, 10.0),
        }
    )


def test_detector_a_fires_after_first_crossing_not_before() -> None:
    panel = annotate_move_features(_synth_panel())
    fires = detect_triggers(panel, detector="A_price_shock", move_z_threshold=2.0)
    assert not fires.empty
    first = pd.Timestamp(fires["bucket_ts"].iloc[0])
    # Should not fire in the quiet prefix
    assert first >= pd.Timestamp("2026-03-10 07:20", tz="UTC")


def test_build_events_dedupes_same_day() -> None:
    panel = annotate_move_features(_synth_panel())
    fires = detect_triggers(panel, detector="A_price_shock", move_z_threshold=1.5)
    events = build_events(panel, fires, max_horizon_minutes=30)
    assert len(events) >= 1
    # One active event at a time → at most a handful on this synthetic day
    assert len(events) <= 5
    assert events["event_id"].is_unique


def test_label_events_assigns_barrier_codes() -> None:
    panel = annotate_move_features(_synth_panel())
    fires = detect_triggers(panel, detector="C_v2_residual_shock", residual_z_threshold=2.0)
    events = build_events(panel, fires)
    labeled = label_events(events, panel, R_ticks=2, C_ticks=8, H_minutes=15)
    assert "label" in labeled.columns
    assert set(labeled["label"].dropna().unique()).issubset({-1, 0, 1})
