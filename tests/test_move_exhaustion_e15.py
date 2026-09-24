"""Tests for E15 duration-normalised cross-tenor flow confirmation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.cross_tenor import cross_tenor_flow_confirmation_features


def test_curve_wide_regime() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=6, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(6) * (1 / 64),
            "TY_agg_5m": 850.0,  # ~10 contracts at DV01=85
            "FV_agg_5m": 420.0,  # ~10
            "TU_agg_5m": 360.0,  # ~10
            "US_agg_5m": 1250.0,  # ~10
            "agg_5m": 850.0,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = cross_tenor_flow_confirmation_features(panel, event)
    assert feats["flow_regime"] == "curve_wide"
    assert feats["n_confirming_tenors_norm"] == 3.0
    assert abs(feats["target_directional_flow"] - 10.0) < 1e-6
    assert abs(feats["curve_directional_flow_factor"] - 10.0) < 1e-6
    assert abs(feats["target_residual_flow"]) < 1e-6


def test_target_only_regime() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=6, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0 + np.arange(6) * (1 / 64),
            "TY_agg_5m": 850.0,
            "FV_agg_5m": -42.0,
            "TU_agg_5m": -36.0,
            "US_agg_5m": -125.0,
            "agg_5m": 850.0,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = cross_tenor_flow_confirmation_features(panel, event)
    assert feats["flow_regime"] == "target_only"
    assert feats["n_confirming_tenors_norm"] == 0.0
    assert feats["target_residual_flow"] > 0


def test_lagged_neighbouring_flow_causal() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=6, freq="1min", tz="UTC")
    # Neighbors quiet now, strong lag-1
    fv = [0.0] * 6
    fv[-2] = 420.0
    fv[-1] = 0.0
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": 110.0,
            "TY_agg_5m": 85.0,
            "FV_agg_5m": fv,
            "TU_agg_5m": 0.0,
            "US_agg_5m": 0.0,
            "agg_5m": 85.0,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = cross_tenor_flow_confirmation_features(panel, event)
    assert abs(feats["lagged_neighbouring_flow"] - 10.0) < 1e-6
