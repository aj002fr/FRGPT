from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.autoresearch.fixed_env.candidate_runner import run_candidate

ROOT = Path(__file__).parents[1]
CANDIDATES = ROOT / "scripts" / "autoresearch" / "candidates"
H = "a" * 64


def timestamps(count: int) -> list[str]:
    start = datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc)
    return [
        (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        for index in range(count)
    ]


def test_e001_emits_finite_registered_features_after_warmup() -> None:
    rows = []
    for index, timestamp in enumerate(timestamps(70)):
        rows.append({
            "formation_ts": timestamp, "price": 100 + (-1) ** index,
            "day_open_price": 100.0, "agg_cum_daily": 0.0,
            "TY_agg_5m": 0.0, "FV_agg_5m": 0.0, "TU_agg_5m": 0.0,
            "US_agg_5m": 0.0, "mom_5m": 0.0,
        })
    features = {
        "flip_count", "absolute_residual", "residual_age",
        "residual_change", "recent_price_momentum",
    }
    parsed, state, _ = run_candidate(
        CANDIDATES / "e001.py", rows, split="discovery",
        source_roots=[CANDIDATES], allowed_input_columns=set(rows[0]),
        registered_feature_map=features,
        config={"window_minutes": 15, "deadband_ticks": 1.0, "dwell_minutes": 1},
        contract_hash=H,
        declared_grid={
            "window_minutes": [15], "deadband_ticks": [1.0], "dwell_minutes": [1],
        },
    )
    assert len(parsed) == 10
    assert parsed[-1].features["flip_count"] > 0
    assert state["candidate_state"]["window_minutes"] == 15


def test_discovery_score_transform_reuses_training_state() -> None:
    rows = [{
        "formation_ts": timestamp, "price": 100 + index / 100,
        "day_open_price": 100.0, "agg_cum_daily": float(index),
        "TY_agg_5m": 0.0, "FV_agg_5m": 0.0, "TU_agg_5m": 0.0,
        "US_agg_5m": 0.0, "mom_5m": 0.0,
    } for index, timestamp in enumerate(timestamps(70))]
    features = {
        "flip_count", "absolute_residual", "residual_age",
        "residual_change", "recent_price_momentum",
    }
    kwargs = {
        "source_roots": [CANDIDATES], "allowed_input_columns": set(rows[0]),
        "registered_feature_map": features,
        "config": {"window_minutes": 15, "deadband_ticks": 1.0, "dwell_minutes": 1},
        "contract_hash": H,
        "declared_grid": {
            "window_minutes": [15], "deadband_ticks": [1.0], "dwell_minutes": [1],
        },
    }
    _, state, _ = run_candidate(CANDIDATES / "e001.py", rows, split="discovery", **kwargs)
    _, unchanged, _ = run_candidate(
        CANDIDATES / "e001.py", rows, split="discovery",
        operation="transform", frozen_state=state, **kwargs,
    )
    assert unchanged == state
    _, quarantined, _ = run_candidate(
        CANDIDATES / "e001.py", rows, split="quarantine",
        operation="transform", frozen_state=state, **kwargs,
    )
    assert quarantined == state


def test_e002_never_emits_direction() -> None:
    rows = []
    for index, timestamp in enumerate(timestamps(40)):
        row = {
            "formation_ts": timestamp, "price": 100 + index / 100,
            "day_open_price": 100.0, "agg_cum_daily": float(index),
            "TY_agg_5m": float(index), "buysell_ratio": 0.1,
            "size_imbalance_mean": 0.2, "microprice_dev_mean": 0.01,
            "realized_vol": 0.1, "mom_5m": 0.01, "trade_intensity": 10.0,
            "spread_mean": 1 / 64, "minutes_since_open": float(index),
        }
        rows.append(row)
    features = {
        "formation_gap", "price_led_score", "absolute_gap",
        "gap_age", "recent_price_momentum",
    }
    parsed, _, _ = run_candidate(
        CANDIDATES / "e002.py", rows, split="discovery",
        source_roots=[CANDIDATES], allowed_input_columns=set(rows[0]),
        registered_feature_map=features,
        config={"ridge_lambda": 1.0, "formation_lookback_minutes": 5},
        contract_hash=H,
        declared_grid={"ridge_lambda": [1.0], "formation_lookback_minutes": [5]},
    )
    assert len(parsed) == 35
    assert all(row.direction is None for row in parsed)


def test_d001_freezes_quantile_boundaries() -> None:
    rows = [{
        "formation_ts": timestamp, "price": 100.0,
        "buysell_ratio": float(index), "large_trade_share": index / 20,
        "trade_intensity": float(index + 1), "realized_vol": index / 100,
        "minutes_since_open": float(index),
    } for index, timestamp in enumerate(timestamps(12))]
    features = {
        "buysell_ratio", "large_trade_share",
        "trade_intensity", "realized_vol_context",
    }
    parsed, state, _ = run_candidate(
        CANDIDATES / "d001.py", rows, split="discovery",
        source_roots=[CANDIDATES], allowed_input_columns=set(rows[0]),
        registered_feature_map=features, config={"proxy": "buysell_ratio"},
        contract_hash=H, declared_grid={"proxy": ["buysell_ratio"]},
    )
    assert len(parsed) == 12
    assert state["candidate_state"]["boundaries"]["buysell_ratio"][0] < 2
