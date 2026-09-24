"""Tests for E13 residual dynamics features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.residual_dynamics import (
    attractive_reversion_flag,
    classify_dynamics,
    residual_dynamics_features,
)


def test_price_reverting_stable_fv() -> None:
    idx = pd.date_range("2026-04-04 10:00", periods=8, freq="1min", tz="UTC")
    # Price elevated vs FV then reverts; FV flat. Keep |z|>=2 at end.
    price = [110.20, 110.18, 110.16, 110.14, 110.12, 110.10, 110.08, 110.06]
    fv = [110.00] * 8
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-04",
            "price": price,
            "fair_value": fv,
            "residual": np.array(price) - np.array(fv),
            "residual_z": (np.array(price) - np.array(fv)) / 0.02,
        }
    )
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    feats = residual_dynamics_features(panel, event)
    assert feats["dynamics_category"] == "price_reverting_toward_stable_fair_value"
    assert feats["price_share_so_far"] is not None
    assert feats["price_share_so_far"] > 0.5
    assert feats["abs_residual_z"] is not None and feats["abs_residual_z"] >= 2.0
    assert attractive_reversion_flag(feats) is True


def test_classify_fv_chasing() -> None:
    cat = classify_dynamics(
        r0=0.05,
        price_vel_ticks=0.0,
        fv_vel_ticks=0.05,
        gap_closure_ticks=0.2,
    )
    assert cat == "fair_value_chasing_price"
