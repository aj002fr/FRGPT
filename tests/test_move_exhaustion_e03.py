"""Tests for E03 V2 residual entry rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.simulation.v2_baselines import (
    entry_v1_abs_z_ge_2,
    entry_v3_cross_inside_2,
    entry_v4_extreme_aligns_fade,
    simulate_v2_trade,
)


def _panel() -> tuple[pd.DataFrame, dict]:
    idx = pd.date_range("2026-04-02 10:00", periods=40, freq="1min", tz="UTC")
    z = np.concatenate([[0.5] * 5, [2.2] * 8, [1.5] * 5, [0.8] * 22])
    price = 110.0 + z * 0.01
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-02",
            "price": price,
            "residual_z": z,
        }
    )
    event = {
        "event_id": "t",
        "et_date": "2026-04-02",
        "event_start_time": idx[4].isoformat(),
        "event_end_time": idx[-1].isoformat(),
        "event_direction": 1,
        "symbol": "TY",
    }
    return panel, event


def test_v1_enters_when_abs_z_hits_2() -> None:
    panel, event = _panel()
    ts = entry_v1_abs_z_ge_2(panel, event, 1)
    assert ts is not None
    assert abs(float(panel.loc[panel["bucket_ts"] == ts, "residual_z"].iloc[0])) >= 2.0


def test_v3_enters_on_cross_inside() -> None:
    panel, event = _panel()
    ts = entry_v3_cross_inside_2(panel, event, 1)
    assert ts is not None
    z = float(panel.loc[panel["bucket_ts"] == ts, "residual_z"].iloc[0])
    assert abs(z) < 2.0


def test_v4_requires_sign_alignment() -> None:
    panel, event = _panel()
    ts = entry_v4_extreme_aligns_fade(panel, event, 1)
    assert ts is not None
    # Opposite event direction should not align with positive residual extreme
    event_down = dict(event, event_direction=-1)
    # May still find nothing if all extremes are positive
    ts_down = entry_v4_extreme_aligns_fade(panel, event_down, -1)
    assert ts_down is None


def test_simulate_fixed_30m() -> None:
    panel, event = _panel()
    entry = entry_v1_abs_z_ge_2(panel, event, 1)
    out = simulate_v2_trade(panel, entry_ts=entry, event_direction=1, exit_mode="fixed_30m")
    assert out["entered"] is True
    assert out["net_ticks"] is not None
