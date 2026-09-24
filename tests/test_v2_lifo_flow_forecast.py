"""Tests for offline past-flow → future-flow and two-stage price maps."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_v2_lifo_flow_forecast_research import (
    OUTPUT_DIR,
    add_flow_price_windows,
    forecast_r2,
    ols_fit,
    ols_r2,
)
from scripts.aggression_v1_flow_price_dispersion_research import TICK_SIZE


def test_isolation_output_dir() -> None:
    text = str(OUTPUT_DIR).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v2_lifo_flow_forecast")
    assert "aggression_v2_live" not in text
    assert "aggression_v27_live" not in text
    assert "aggression_v2_lifo_zone/" not in text + "/"
    assert "aggression_v2_lifo_literature/" not in text + "/"


def test_script_does_not_import_live_apps() -> None:
    text = Path("scripts/aggression_v2_lifo_flow_forecast_research.py").read_text(encoding="utf-8")
    assert "aggression_dashboard_v22" not in text
    assert "aggression_dashboard_v27" not in text
    assert "from scripts.aggression_dashboard" not in text


def test_windows_are_causal() -> None:
    n = 12
    ts = pd.date_range("2026-01-05 10:00", periods=n, freq="1min", tz="UTC")
    agg = np.arange(1.0, n + 1.0)
    price = 110.0 + np.cumsum(np.r_[0.0, np.full(n - 1, TICK_SIZE)])
    df = pd.DataFrame({"agg_1m": agg, "price": price, "et_date": "2026-01-05"}, index=ts)
    out = add_flow_price_windows(df, past_w=(1, 3), future_h=(1, 2))
    assert out["past_flow_1"].iloc[4] == agg[4]
    assert out["past_flow_3"].iloc[4] == agg[2] + agg[3] + agg[4]
    assert out["fwd_flow_1"].iloc[4] == agg[5]
    assert out["fwd_flow_2"].iloc[4] == agg[5] + agg[6]
    assert out["fwd_dp_1"].iloc[4] == 1.0
    assert pd.isna(out["fwd_flow_1"].iloc[-1])
    assert pd.isna(out["fwd_flow_2"].iloc[-2])


def test_forecast_r2_penalizes_wrong_scale() -> None:
    y = pd.Series(np.linspace(0.0, 1.0, 40))
    good = y.copy()
    bad = 10.0 * y
    assert forecast_r2(y, good) > 0.99
    assert forecast_r2(y, bad) < forecast_r2(y, good)


def test_ols_r2_affine_invariant() -> None:
    rng = np.random.default_rng(1)
    x = pd.Series(rng.normal(size=80))
    y = 3.0 * x + rng.normal(scale=0.2, size=80)
    assert abs(ols_r2(y, x) - ols_r2(y, 2.0 * x + 5.0)) < 1e-12


def test_ols_fit_recovers_line() -> None:
    x = pd.Series(np.linspace(-2.0, 2.0, 50))
    y = 1.5 + 2.0 * x
    intercept, slope, r2, n = ols_fit(y, x)
    assert n == 50
    assert abs(intercept - 1.5) < 1e-9
    assert abs(slope - 2.0) < 1e-9
    assert r2 > 0.999
