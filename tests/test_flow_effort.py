from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_flow_effort import (
    TICK_SIZE,
    FlowEffortConfig,
    annotate_flow_effort,
    evaluate_regime,
)


def _frames(price: list[float], flow: list[float], *, curve_sign: int = 0, residual_z: float = 0.0):
    idx = pd.date_range("2026-07-06 13:00", periods=len(price), freq="1min", tz="UTC")
    agg_1m = pd.Series(flow, index=idx, dtype=float)
    features = pd.DataFrame(
        {
            "price": price,
            "agg_1m": agg_1m,
            "agg_cum_daily": agg_1m.cumsum(),
            "agg_5m": agg_1m.rolling(5, min_periods=1).sum(),
            "agg_30m": agg_1m.rolling(30, min_periods=1).sum(),
            "vol_per_min": np.abs(flow),
            "spread_mean": 0.01,
            "FV_agg_5m": curve_sign * np.abs(agg_1m).rolling(5, min_periods=1).sum(),
            "TU_agg_5m": curve_sign * np.abs(agg_1m).rolling(5, min_periods=1).sum(),
            "US_agg_5m": curve_sign * np.abs(agg_1m).rolling(5, min_periods=1).sum(),
        },
        index=idx,
    )
    model_df = pd.DataFrame(
        {
            "price": price,
            "fair_value": np.asarray(price) - 0.01,
            "residual": 0.01,
            "residual_z": residual_z,
        },
        index=idx,
    )
    return features, model_df


def test_flat_price_heavy_late_flow_is_exhaustion() -> None:
    price = [110.0] * 45
    flow = [10.0] * 30 + [100.0, 250.0, 500.0, 900.0, 1200.0] + [1100.0] * 10
    features, model_df = _frames(price, flow, curve_sign=-1, residual_z=1.8)
    result = evaluate_regime(features, model_df, FlowEffortConfig(regime_dwell_buckets=1, event_blackout_minutes=0))
    assert result["regime"] == "EXHAUSTION"
    assert result["exhaustion_tier"] in {"B", "A", "A+"}
    assert result["flags"]["absorption"]


def test_aligned_price_and_curve_flow_is_trend_follow() -> None:
    price = [110.0 + i * TICK_SIZE for i in range(45)]
    flow = [100.0] * 45
    features, model_df = _frames(price, flow, curve_sign=1, residual_z=0.2)
    result = evaluate_regime(features, model_df, FlowEffortConfig(regime_dwell_buckets=1, event_blackout_minutes=0))
    assert result["regime"] == "TREND_FOLLOW"
    assert result["exhaustion_tier"] == "NONE"


def test_range_break_with_lower_effort_is_breakout() -> None:
    price = [110.0 + (0.5 * TICK_SIZE if i % 2 else 0.0) for i in range(32)]
    price += [110.0 + 5 * TICK_SIZE, 110.0 + 6 * TICK_SIZE, 110.0 + 7 * TICK_SIZE]
    flow = [500.0 if i % 2 else -500.0 for i in range(32)] + [50.0, 50.0, 50.0]
    features, model_df = _frames(price, flow, curve_sign=1, residual_z=0.5)
    result = evaluate_regime(features, model_df, FlowEffortConfig(regime_dwell_buckets=1, event_blackout_minutes=0))
    assert result["regime"] == "BREAKOUT"
    assert result["breakout_tier"] in {"C", "B", "A"}


def test_break_then_reentry_is_false_break() -> None:
    price = [110.0 + (0.5 * TICK_SIZE if i % 2 else 0.0) for i in range(32)]
    price += [110.0 + 5 * TICK_SIZE, 110.0 + 0.25 * TICK_SIZE, 110.0]
    flow = [500.0 if i % 2 else -500.0 for i in range(32)] + [50.0, 50.0, 50.0]
    features, model_df = _frames(price, flow, curve_sign=1, residual_z=0.5)
    result = evaluate_regime(features, model_df, FlowEffortConfig(regime_dwell_buckets=1, event_blackout_minutes=0))
    assert result["regime"] == "FALSE_BREAK"


def test_warmup_blocks_early_labels() -> None:
    price = [110.0] * 29
    flow = [1000.0] * 29
    features, model_df = _frames(price, flow, curve_sign=-1, residual_z=2.0)
    result = evaluate_regime(features, model_df, FlowEffortConfig(regime_dwell_buckets=1, event_blackout_minutes=0))
    assert result["regime"] == "WARMUP"


def test_break_bar_does_not_chase_rolling_range() -> None:
    price = [110.0 + (0.5 * TICK_SIZE if i % 2 else 0.0) for i in range(32)]
    price += [110.0 + 8 * TICK_SIZE]
    flow = [500.0 if i % 2 else -500.0 for i in range(32)] + [50.0]
    features, model_df = _frames(price, flow, curve_sign=1, residual_z=0.5)
    annotated = annotate_flow_effort(features, model_df, FlowEffortConfig())
    last = annotated.iloc[-1]
    assert bool(last["range_break"])
    assert pd.notna(last["frozen_range_high"])
    assert last["price"] > last["frozen_range_high"]

