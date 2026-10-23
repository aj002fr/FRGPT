"""Focused tests for offline short-horizon V2 fair-value helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_v2_short_horizon_fv_research import (
    TICK_SIZE,
    add_derived_columns,
    apply_hysteresis,
    construction_specs,
    fade_hit_rate,
    iter_walkforward_folds,
    lean_from_residual,
    locked_holdout_dates,
    minutes_until_not_against,
    path_adverse_ticks,
    flip_metrics,
)


def _two_days(n: int = 40) -> pd.DataFrame:
    frames = []
    for i, date in enumerate(("2026-01-05", "2026-01-06")):
        ts = pd.date_range(f"{date} 14:00", periods=n, freq="1min", tz="UTC")
        t = np.arange(n, dtype=float)
        price = 110.0 + (t + 10 * i) * TICK_SIZE
        frames.append(pd.DataFrame({
            "bucket_ts": ts,
            "et_date": date,
            "price": price,
            "day_open_price": price[0],
            "target_price_delta": price - price[0],
            "agg_1m": np.linspace(1.0, 2.0, n) + i,
            "agg_5m": 5.0,
            "agg_30m": 30.0,
            "agg_cum_daily": t,
            "buysell_ratio": 0.0,
            "trade_intensity": 10.0,
            "avg_trade_size": 4.0,
            "large_trade_share": 0.2,
            "realized_vol": 0.01,
            "mom_5m": 0.0,
            "FV_agg_5m": 0.0,
            "TU_agg_5m": 0.0,
            "US_agg_5m": 0.0,
            "spread_mean": TICK_SIZE,
            "size_imbalance_mean": 0.0,
            "displayed_depth_mean": 20.0,
            "trade_size_vs_depth": 0.1,
            "depth_adjusted_signed_flow": 0.0,
            "minutes_since_open": t,
        }))
    return pd.concat(frames, ignore_index=True)


def test_forward_targets_are_same_day_and_censored() -> None:
    raw = _two_days(20)
    out = add_derived_columns(raw)
    d1 = out[out["et_date"] == "2026-01-05"].sort_values("bucket_ts")
    d2 = out[out["et_date"] == "2026-01-06"].sort_values("bucket_ts")
    assert d1["y_A_5m"].iloc[-5:].isna().all()
    assert d1["y_A_5m"].iloc[:-5].notna().all()
    expected = d1["price"].iloc[5] - d1["price"].iloc[0]
    assert abs(float(d1["y_A_5m"].iloc[0]) - float(expected)) < 1e-12
    # no leak from day 2 into day 1 last bars
    assert d1["future_price_5m"].iloc[-1] != d2["price"].iloc[0]
    assert pd.isna(d1["future_price_5m"].iloc[-1])


def test_local_anchor_has_no_lookahead() -> None:
    raw = _two_days(20)
    out = add_derived_columns(raw)
    d1 = out[out["et_date"] == "2026-01-05"].sort_values("bucket_ts")
    assert d1["local_anchor_5m"].iloc[:5].isna().all()
    assert abs(float(d1["local_anchor_5m"].iloc[5]) - float(d1["price"].iloc[0])) < 1e-12
    assert abs(float(d1["y_B_5m"].iloc[5]) - float(d1["price"].iloc[5] - d1["price"].iloc[0])) < 1e-12
    # later anchor is a past price, not a future one
    assert float(d1["local_anchor_5m"].iloc[10]) == float(d1["price"].iloc[5])


def test_b1_derived_columns() -> None:
    raw = _two_days(20)
    out = add_derived_columns(raw)
    d1 = out[out["et_date"] == "2026-01-05"].sort_values("bucket_ts")
    assert d1["realized_vol_5"].iloc[:5].isna().all()
    assert d1["realized_vol_5"].iloc[5:].notna().all()
    assert d1["agg_cum_5m"].iloc[:4].isna().all()
    expected = float(d1["agg_1m"].iloc[:5].sum())
    assert abs(float(d1["agg_cum_5m"].iloc[4]) - expected) < 1e-9
    assert d1["minutes_since_anchor_5m"].iloc[:5].isna().all()
    assert (d1["minutes_since_anchor_5m"].iloc[5:] == 5.0).all()


def test_walkforward_uses_prior_dates_only() -> None:
    dates = [f"2026-01-{i:02d}" for i in range(1, 12)]
    folds = iter_walkforward_folds(dates, min_train_days=5)
    assert folds[0] == (dates[:5], dates[5])
    for train, test in folds:
        assert test not in train
        assert max(train) < test
    hold = locked_holdout_dates(dates, 0.2)
    assert hold == dates[-3:] or hold[-1] == dates[-1]
    assert set(hold).issubset(set(dates))


def test_deadzone_flip_counts() -> None:
    resid = pd.Series([
        2 * TICK_SIZE, 2 * TICK_SIZE, 0.0, -2 * TICK_SIZE, -2 * TICK_SIZE, 2 * TICK_SIZE
    ])
    lean = lean_from_residual(resid)
    assert list(lean) == [1.0, 1.0, 0.0, -1.0, -1.0, 1.0]
    stats = flip_metrics(lean)
    assert stats["flips"] == 3
    assert stats["median_lean_duration_min"] == 2.0


def test_hysteresis_respects_min_hold() -> None:
    n = 12
    residual = pd.Series([2.0] * 3 + [-2.0] * 9)
    z = pd.Series([2.0] * 3 + [-2.0] * 9)
    held = apply_hysteresis(residual, z, enter=1.0, exit_=0.5, min_hold=5)
    # first 3 bars enter rich; next bars want to flip but min-hold is 5, so still rich through bar 5
    assert (held.iloc[:5] == 1.0).all()
    assert (held.iloc[5:] == -1.0).all()
    instant = apply_hysteresis(residual, z, enter=1.0, exit_=0.5, min_hold=0)
    assert instant.iloc[0] == 1.0
    assert instant.iloc[3] == -1.0


def test_path_adverse_and_resolve_helpers() -> None:
    prices = np.array([100.0, 100.0 + TICK_SIZE, 100.0 + 4 * TICK_SIZE, 100.0 + 2 * TICK_SIZE])
    # fade short: direction -1, price rises 4 ticks at step 2
    adv = path_adverse_ticks(prices, 0, 3, -1.0)
    assert abs(adv - 4.0) < 1e-9
    assert np.isnan(path_adverse_ticks(prices, 2, 3, -1.0))
    residuals = np.array([2 * TICK_SIZE, 2 * TICK_SIZE, 0.0, -2 * TICK_SIZE])
    assert minutes_until_not_against(residuals, 0, 1.0, 10) == 2.0
    hit = fade_hit_rate(
        pd.Series([1.0, -1.0] * 5),
        pd.Series([-0.1, 0.2] * 5),
    )
    assert hit == 1.0


def test_construction_grid_is_frozen() -> None:
    specs = construction_specs()
    ids = [s.id for s in specs]
    assert ids[0] == "session"
    assert "A_h5" in ids and "A_h30" not in ids
    assert "B1_W10" in ids and "B0_W60" not in ids
    assert len([s for s in specs if s.family == "A"]) == 6
    assert len([s for s in specs if s.family == "B"]) == 12
