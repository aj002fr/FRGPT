"""Unit tests for E23–E30 helpers (not full pipelines)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.reporting.ml_common import family_columns, FEATURE_FAMILIES
from scripts.move_exhaustion.simulation.trading import (
    apply_latency,
    date_pnl_concentration,
    executable_entry_price,
)


def test_family_columns_matches_substring() -> None:
    available = [
        "move_z_5m",
        "flow_z_5m",
        "signed_flow_z_5m",
        "residual_z",
        "curve_directional_flow_factor",
        "is_opening_period",
        "unrelated_feature",
    ]
    flow = family_columns("flow", available)
    assert "flow_z_5m" in flow
    assert "signed_flow_z_5m" in flow
    assert "unrelated_feature" not in flow
    assert set(FEATURE_FAMILIES.keys()) >= {
        "price",
        "flow",
        "liquidity",
        "V2",
        "cross_tenor",
        "regime",
    }


def test_apply_latency_adds_minutes() -> None:
    ts = pd.Timestamp("2026-04-06 14:00:00", tz="UTC")
    assert apply_latency(ts, 0) == ts
    assert apply_latency(ts, 1) == ts + pd.Timedelta(minutes=1)


def test_executable_entry_uses_bid_ask_not_mid() -> None:
    idx = pd.date_range("2026-04-06 14:00", periods=3, freq="1min", tz="UTC")
    panel = pd.DataFrame(
        {
            "et_date": ["2026-04-06"] * 3,
            "bucket_ts": idx,
            "price": [110.0, 110.0, 110.0],
            "spread_mean": [0.03125, 0.03125, 0.03125],  # 2 ticks
        }
    )
    # Up event → fade short → bid = mid - half_spread
    short = executable_entry_price(
        panel, entry_ts=idx[1], event_direction=1, et_date="2026-04-06"
    )
    assert short is not None
    assert short["fade_sign"] == -1
    assert short["entry_price"] < short["mid"]
    # Down event → fade long → ask
    long = executable_entry_price(
        panel, entry_ts=idx[1], event_direction=-1, et_date="2026-04-06"
    )
    assert long is not None
    assert long["fade_sign"] == 1
    assert long["entry_price"] > long["mid"]


def test_date_pnl_concentration_flags_over_20pct() -> None:
    nets = pd.Series({"2026-04-06": 10.0, "2026-04-07": 1.0, "2026-04-08": 1.0})
    out = date_pnl_concentration(nets)
    assert out["flag_gt_20pct"] is True
    assert out["top_date"] == "2026-04-06"
    flat = pd.Series({"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0, "e": 1.0, "f": 1.0})
    out2 = date_pnl_concentration(flat)
    assert out2["flag_gt_20pct"] is False
