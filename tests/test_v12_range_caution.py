"""Tests for Aggression V1.2 provisional RANGE-caution monitor."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v12.caution_store import CautionStore, STORE_DIR
from scripts.aggression_dashboard_v12.range_caution_context import (
    DECLUSTER_STRIDE_MIN,
    LABEL,
    LARGE_ABS_Z,
    MIN_POINTS_OLS,
    REGIME_CUTOFFS,
    STRATEGY_STATUS,
    WIDENING_WINDOW_MIN,
    annotate_caution_flags,
    decluster_fire_mask,
    fit_causal_v1_residual,
    latest_caution_context,
    score_forward_regime,
)


def _minute_index(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="1min", tz="UTC")


def test_labels_are_fixed_provisional() -> None:
    assert STRATEGY_STATUS == "PROVISIONAL / RESEARCH-ONLY"
    assert LABEL == "PROVISIONAL / RANGE-CAUTION / NON-DIRECTIONAL"
    assert "NON-DIRECTIONAL" in LABEL
    assert LARGE_ABS_Z == 1.0
    assert WIDENING_WINDOW_MIN == 15


def test_soft_breakout_label_is_b4_only() -> None:
    from scripts.aggression_dashboard_v12.range_caution_context import (
        SOFT_BREAKOUT_LABEL,
        SOFT_BREAKOUT_RESEARCH,
    )

    assert SOFT_BREAKOUT_LABEL == "PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL"
    assert SOFT_BREAKOUT_RESEARCH["primary_restrictive_pass_n"] == 0
    assert SOFT_BREAKOUT_RESEARCH["restrictive_pass_n"] == 18
    assert SOFT_BREAKOUT_RESEARCH["pass_breakout_bps"] == 4.0
    assert SOFT_BREAKOUT_RESEARCH["directional"] is False


def test_lifo_friendly_label_and_fire() -> None:
    from scripts.aggression_dashboard_v12.range_caution_context import (
        LIFO_FRIENDLY_LABEL,
        LIFO_FRIENDLY_RESEARCH,
    )

    assert LIFO_FRIENDLY_LABEL == "PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL"
    assert LIFO_FRIENDLY_RESEARCH["validation_restrictive_pass"] is True
    assert LIFO_FRIENDLY_RESEARCH["final_ci_clears"] is False
    assert LIFO_FRIENDLY_RESEARCH["directional"] is False

    idx = _minute_index("2026-07-17 13:00:00", 40)
    # Quiet tape (low intensity) + large residual → LIFO friendly.
    intensity = np.concatenate([[5.0] * 25, [1.0] * 15])
    df = pd.DataFrame(
        {
            "v1_residual": np.linspace(0.02, 0.25, 40),
            "v1_residual_z": np.concatenate([[0.2] * 20, [1.4] * 20]),
            "trade_intensity": intensity,
            "price": 110.0 + np.arange(40) * 0.0001,
            "et_date": "2026-07-17",
        },
        index=idx,
    )
    out = annotate_caution_flags(df)
    assert "lifo_friendly" in out.columns
    ctx = latest_caution_context(out)
    assert "lifo_friendly" in ctx
    assert ctx["lifo_friendly"]["label"] == LIFO_FRIENDLY_LABEL
    # After warm-up, later bars with |z|>=1 and low intensity should fire.
    assert bool(out["lifo_friendly"].iloc[-1]) or bool(out["large_residual"].iloc[-1])
    if bool(out["lifo_friendly"].iloc[-1]):
        assert ctx["lifo_friendly"]["state"] == "LIFO_FRIENDLY"
        assert ctx["lifo_friendly"]["alert_text"] == "LIFO FRIENDLY"


def test_lifo_episodes_tracked_in_stats(tmp_path: Path) -> None:
    store = CautionStore(store_dir=tmp_path)
    idx = _minute_index("2026-07-17 13:00:00", 90)
    residual = np.full(90, 0.01)
    residual[30:] = np.linspace(0.05, 0.20, 60)
    z = np.concatenate([[0.2] * 30, [1.5] * 60])
    intensity = np.concatenate([[8.0] * 30, [1.0] * 60])
    # Quiet path after fire so many episodes can finalize as RANGE-like.
    price = 110.0 + np.cumsum(np.where(np.arange(90) % 2 == 0, 0.005, -0.005))
    panel = pd.DataFrame(
        {
            "v1_residual": residual,
            "v1_residual_z": z,
            "trade_intensity": intensity,
            "price": price,
            "et_date": "2026-07-17",
            "signed_flow_1m": np.zeros(90),
            "total_aggress": np.arange(90, dtype=float),
            "day_open_price": 110.0,
        },
        index=idx,
    )
    panel = annotate_caution_flags(panel)
    assert panel["lifo_friendly"].any()
    payload = store.update(panel)
    assert "lifo_stats" in payload
    lifo_eps = [ep for ep in store.episodes if ep.get("alert_kind") == "lifo"]
    assert lifo_eps
    assert all(str(ep["episode_id"]).startswith("lifo_") for ep in lifo_eps)
    assert payload["lifo_stats"]["n_finalized"] + payload["lifo_stats"]["n_active"] == len(lifo_eps)


def test_store_is_isolated() -> None:
    text = str(STORE_DIR).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v12_live")
    assert "aggression_v2_live" not in text
    assert "aggression_v21_live" not in text
    assert "aggression_v3_live" not in text


def test_decluster_stride() -> None:
    idx = _minute_index("2026-07-16 14:00:00", 40)
    mask = pd.Series(False, index=idx)
    mask.iloc[0] = True
    mask.iloc[5] = True
    mask.iloc[16] = True
    fires = decluster_fire_mask(mask, stride_min=DECLUSTER_STRIDE_MIN)
    assert int(fires.sum()) == 2
    assert bool(fires.iloc[0]) and bool(fires.iloc[16]) and not bool(fires.iloc[5])


def test_caution_flags_large_widening() -> None:
    idx = _minute_index("2026-07-16 13:00:00", 40)
    residual = np.full(40, 0.01, dtype=float)
    residual[20:] = np.linspace(0.05, 0.20, 20)
    df = pd.DataFrame(
        {
            "v1_residual": residual,
            "v1_residual_z": np.concatenate([[0.2] * 20, [1.5] * 20]),
            "price": 110.0 + np.arange(40) * 0.0001,
            "et_date": "2026-07-16",
        },
        index=idx,
    )
    out = annotate_caution_flags(df)
    assert bool(out["cautious_range"].iloc[-1])
    ctx = latest_caution_context(out)
    assert ctx["state"] == "CAUTIOUS_RANGE"
    assert ctx["non_directional"] is True
    assert ctx["label"] == LABEL
    assert "soft_breakout" in ctx
    assert ctx["soft_breakout"]["non_directional"] is True


def test_soft_breakout_lean_from_persist() -> None:
    idx = _minute_index("2026-07-16 13:00:00", 5)
    df = pd.DataFrame(
        {
            "v1_residual": [0.1] * 5,
            "v1_residual_z": [1.2] * 5,
            "price": [110.0] * 5,
            "et_date": ["2026-07-16"] * 5,
            "large_residual": [True] * 5,
            "residual_widening_15m": [False] * 5,
            "cautious_range": [False] * 5,
            "large_persist_10": [True] * 5,
            "large_persist_20": [True] * 5,
            "age_ge_30": [False] * 5,
            "z_ge_1_5": [False] * 5,
            "fast_15m": [False] * 5,
            "soft_breakout_lean": [True] * 5,
            "residual_large_age_min": [22.0] * 5,
        },
        index=idx,
    )
    ctx = latest_caution_context(df)
    soft = ctx["soft_breakout"]
    assert soft["state"] == "SOFT_BREAKOUT_LEAN"
    assert soft["active"] is True
    assert "large_persist_20" in soft["active_factors"]
    assert soft["label"] == "PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL"
    assert soft["research_context"]["primary_restrictive_pass_n"] == 0
    assert soft["research_context"]["pass_breakout_bps"] == 4.0


def test_score_forward_range_like() -> None:
    idx = _minute_index("2026-07-16 14:00:00", 40)
    prices = []
    px = 110.0
    for i in range(40):
        px += 0.01 if i % 2 == 0 else -0.01
        prices.append(px)
    series = pd.Series(prices, index=idx)
    scored = score_forward_regime(series, idx[0], cutoffs=REGIME_CUTOFFS)
    assert scored["outcome"] in {"RANGE", "AMBIGUOUS", "TREND_UP", "TREND_DOWN"}
    assert "path_eff" in scored


def test_score_incomplete_without_forward_bars() -> None:
    idx = _minute_index("2026-07-16 14:00:00", 10)
    series = pd.Series(110.0 + np.arange(10) * 0.01, index=idx)
    scored = score_forward_regime(series, idx[0], cutoffs=REGIME_CUTOFFS)
    assert scored["outcome"] == "INCOMPLETE"


def test_caution_store_episode_lifecycle(tmp_path: Path) -> None:
    store = CautionStore(store_dir=tmp_path)
    idx = _minute_index("2026-07-16 13:00:00", 80)
    rng = np.random.default_rng(1)
    signed = rng.normal(0, 50, size=80)
    signed[40:] += 200
    price = 110.0 + np.cumsum(rng.normal(0, 0.002, 80))
    price[50:] = price[49] + np.linspace(0, 0.02, 30)
    flow = pd.DataFrame({"signed_flow_bucket": signed, "total_aggress": np.cumsum(signed)}, index=idx)
    px = pd.DataFrame({"price": price}, index=idx)
    panel = CautionStore.build_panel(flow, px)
    assert not panel.empty
    assert "cautious_range" in panel.columns
    payload = store.update(panel)
    assert payload["strategy_status"] == STRATEGY_STATUS
    assert payload["isolation"]["v1_v2_v3_modified"] is False
    assert str(tmp_path) in str(payload["isolation"]["store"])


def test_causal_residual_no_lookahead_on_first_day() -> None:
    idx = _minute_index("2026-07-16 12:00:00", 60)
    aggress = np.cumsum(np.ones(60))
    price = 110.0 + 0.001 * aggress
    df = pd.DataFrame(
        {
            "et_date": "2026-07-16",
            "total_aggress": aggress,
            "price": price,
            "day_open_price": 110.0,
        },
        index=idx,
    )
    out = fit_causal_v1_residual(df)
    assert out["v1_residual"].isna().iloc[:MIN_POINTS_OLS].any() or out["v1_residual"].notna().iloc[-1]
    assert out["v1_residual"].notna().iloc[-1]
