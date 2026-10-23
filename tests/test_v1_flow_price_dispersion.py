"""Focused tests for V1 flow-price dispersion research helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_v1_flow_price_dispersion_research import (
    chronological_splits,
    decluster_episodes,
    fit_causal_v1_residual,
    add_forward_regime_outcomes,
    learn_regime_cutoffs,
    apply_regime_labels,
)


def _synthetic_day(et_date: str, n: int = 120, trend: bool = False) -> pd.DataFrame:
    idx = pd.date_range(f"{et_date} 14:00", periods=n, freq="1min", tz="UTC")
    t = np.arange(n, dtype=float)
    if trend:
        price = 110.0 + t * (1.0 / 64.0)
        flow = t * 1000.0
    else:
        price = 110.0 + ((t % 10) - 5) * (0.25 / 64.0)
        flow = np.sin(t / 8.0) * 5000.0
    return pd.DataFrame(
        {
            "et_date": et_date,
            "price": price,
            "day_open_price": price[0],
            "agg_cum_daily": flow,
            "agg_1m": np.diff(flow, prepend=flow[0]),
            "mom_5m": 0.0,
            "realized_vol": 0.01,
            "minutes_since_open": t,
            "session_sin": 0.0,
            "session_cos": 1.0,
            "buysell_ratio": 0.0,
            "agg_5m": 0.0,
            "agg_30m": 0.0,
            "agg_accel": 0.0,
            "trade_intensity": 10.0,
            "FV_agg_5m": 0.0,
            "TU_agg_5m": 0.0,
            "TY_agg_5m": 0.0,
            "US_agg_5m": 0.0,
            "vol_per_min": 10.0,
            "spread_mean": 0.015625,
            "large_trade_share": 0.2,
            "avg_trade_size": 5.0,
        },
        index=idx,
    )


def test_causal_v1_residual_uses_prior_days_only() -> None:
    d1 = _synthetic_day("2026-01-27", trend=True)
    d2 = _synthetic_day("2026-01-28", trend=False)
    raw = pd.concat([d1, d2]).sort_index()
    out = fit_causal_v1_residual(raw)
    day2 = out[out["et_date"] == "2026-01-28"]
    betas = day2["v1_beta"].dropna()
    assert len(betas) > 10
    assert float(betas.std()) < 1e-12
    assert day2["v1_residual"].notna().any()


def test_forward_outcomes_are_session_censored() -> None:
    day = _synthetic_day("2026-01-27", n=80, trend=True)
    day = fit_causal_v1_residual(day)
    out = add_forward_regime_outcomes(day)
    assert out["fwd_net_ticks_30m"].iloc[-30:].isna().all()
    assert out["fwd_net_ticks_30m"].iloc[:-30].notna().any()


def test_decluster_enforces_stride() -> None:
    day = _synthetic_day("2026-01-27", n=60)
    mask = pd.Series(True, index=day.index)
    episodes = decluster_episodes(day, mask, stride_min=15)
    gaps = episodes.index.to_series().diff().dt.total_seconds().div(60.0).dropna()
    assert (gaps >= 15).all()


def test_regime_labels_leave_ambiguous() -> None:
    trend = fit_causal_v1_residual(_synthetic_day("2026-01-27", n=150, trend=True))
    chop = fit_causal_v1_residual(_synthetic_day("2026-01-28", n=150, trend=False))
    panel = pd.concat([trend, chop]).sort_index()
    panel = add_forward_regime_outcomes(panel)
    cutoffs = learn_regime_cutoffs(panel, ["2026-01-27", "2026-01-28"], horizon=30)
    labeled = apply_regime_labels(panel, cutoffs)
    assert set(labeled["future_regime"].dropna().unique()).issubset(
        {"RANGE", "TREND_UP", "TREND_DOWN", "AMBIGUOUS"}
    )


def test_split_locks_final() -> None:
    dates = [f"2026-{m:02d}-{d:02d}" for m in (1, 2, 3, 4, 5, 6) for d in range(1, 28)]
    splits = chronological_splits(dates)
    assert splits["final"]
    assert not set(splits["final"]) & set(splits["discovery"])
    assert not set(splits["final"]) & set(splits["validation"])
    assert max(splits["validation"]) < min(splits["final"])
