"""Tests for offline LIFO literature flags and Track P/F helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_v2_lifo_literature_research import (
    OUTPUT_DIR,
    ols_r2,
    sign_run_length,
    trailing_nu,
    volume_bucket_nu,
)
from scripts.aggression_v2_lifo_zone_research import TICK_SIZE


def test_isolation_output_dir() -> None:
    text = str(OUTPUT_DIR).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v2_lifo_literature")
    assert "aggression_v2_live" not in text
    assert "aggression_v22_live" not in text
    assert "aggression_v27_live" not in text
    assert "aggression_v2_lifo_zone/" not in text + "/"


def test_script_does_not_import_live_apps() -> None:
    text = Path("scripts/aggression_v2_lifo_literature_research.py").read_text(encoding="utf-8")
    assert "aggression_dashboard_v22" not in text
    assert "aggression_dashboard_v27" not in text
    assert "from scripts.aggression_dashboard" not in text


def test_trailing_nu_zero_on_alternating_flow() -> None:
    agg = pd.Series([1.0, -1.0] * 10)
    nu = trailing_nu(agg, window=16)
    assert nu.iloc[15] == 0.0
    one_way = pd.Series([1.0] * 16)
    assert trailing_nu(one_way, window=16).iloc[-1] == 1.0


def test_sign_run_resets_on_flip() -> None:
    agg = pd.Series([1.0, 2.0, 3.0, -1.0, -2.0, 0.0, 4.0])
    run = sign_run_length(agg)
    assert run.tolist() == [1.0, 2.0, 3.0, 1.0, 2.0, 0.0, 1.0]


def test_absorb_bar_high_agg_flat_price() -> None:
    from scripts.aggression_v2_lifo_literature_research import add_literature_flags

    n = 40
    ts = pd.date_range("2026-01-05 10:00", periods=n, freq="1min", tz="UTC")
    price = np.full(n, 110.0)
    price[30] = 110.0
    agg = np.linspace(0.1, 0.1, n)
    agg[30] = 50.0
    df = pd.DataFrame(
        {
            "price": price,
            "agg_1m": agg,
            "trade_intensity": np.full(n, 20.0),
            "vol_per_min": np.full(n, 100.0),
            "mom_5m": np.zeros(n),
            "et_date": "2026-01-05",
            "trail_meat_15m": False,
            "displayed_depth_mean": 10.0,
        },
        index=ts,
    )
    out = add_literature_flags(df)
    assert bool(out["absorb_bar"].iloc[30])
    assert not bool(out["absorb_bar"].iloc[10])


def test_track_p_prefers_same_bar() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = 2.0 * x + rng.normal(scale=0.1, size=200)
    same = ols_r2(pd.Series(y), pd.Series(x))
    lead = ols_r2(pd.Series(y), pd.Series(np.roll(x, 1)))
    assert same > 0.9
    assert same > lead + 0.3


def test_volume_bucket_nu_waits_for_fill() -> None:
    agg = pd.Series([1.0, -1.0, 1.0, -1.0])
    vol = pd.Series([500.0, 500.0, 500.0, 600.0])
    nu = volume_bucket_nu(agg, vol, v_star=2000)
    assert np.isnan(nu.iloc[0])
    assert np.isfinite(nu.iloc[-1])
    assert nu.iloc[-1] == 0.0


def test_tick_size_unchanged() -> None:
    assert TICK_SIZE == 1.0 / 64.0
