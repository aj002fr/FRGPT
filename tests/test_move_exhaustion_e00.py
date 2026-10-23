"""E00 / causality tests for move-exhaustion package."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import FORBIDDEN_LIVE_GLOBS, OUTPUT_ROOT, TICK_SIZE
from scripts.move_exhaustion.data.loader import add_causal_residual_z
from scripts.move_exhaustion.data.quality import aggressor_sign_validation, assert_no_forbidden_writes
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds, purge_boundary_dates


def test_output_root_isolated() -> None:
    text = str(OUTPUT_ROOT).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v2_move_exhaustion")
    for frag in FORBIDDEN_LIVE_GLOBS:
        assert frag not in text


def test_forbidden_write_guard() -> None:
    try:
        assert_no_forbidden_writes(Path("workspace/analysis/aggression_v2_live/x.csv"))
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_causal_residual_z_ignores_future_mutation() -> None:
    idx = pd.date_range("2026-03-01", periods=40, freq="1min", tz="UTC")
    residual = np.linspace(-0.02, 0.03, 40)
    df = pd.DataFrame(
        {
            "et_date": "2026-03-01",
            "bucket_ts": idx,
            "residual": residual,
            "price": 110.0 + residual,
        }
    )
    base = add_causal_residual_z(df)
    mid = 20
    t = base["bucket_ts"].iloc[mid]
    base_z = float(base.loc[base["bucket_ts"] == t, "residual_z"].iloc[0])
    mutated = df.copy()
    mutated.loc[mid + 1 :, "residual"] = 9.0
    redo = add_causal_residual_z(mutated)
    redo_z = float(redo.loc[redo["bucket_ts"] == t, "residual_z"].iloc[0])
    assert np.isfinite(base_z)
    assert np.isclose(base_z, redo_z)


def test_barrier_reversal_vs_continuation() -> None:
    idx = pd.date_range("2026-03-01 14:00", periods=20, freq="1min", tz="UTC")
    # Up move then reverse
    prices = pd.Series([100.0] + [100.0 + i * (1 / 64) for i in range(1, 6)] + [100.0] * 14, index=idx)
    tick = TICK_SIZE["TY"]
    rev = first_barrier_label(
        prices,
        start_ts=idx[5],
        direction=1,
        R_ticks=2,
        C_ticks=8,
        H=pd.Timedelta(minutes=15),
        tick_size=tick,
    )
    assert rev["label"] == 1
    cont_prices = pd.Series([100.0 + i * (1 / 64) for i in range(20)], index=idx)
    cont = first_barrier_label(
        cont_prices,
        start_ts=idx[0],
        direction=1,
        R_ticks=8,
        C_ticks=4,
        H=pd.Timedelta(minutes=15),
        tick_size=tick,
    )
    assert cont["label"] == 0


def test_purge_and_folds_no_overlap() -> None:
    dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
    # Use ISO-like sortable dates
    dates = [f"2026-02-{d:02d}" for d in range(1, 29)]
    train, valid = purge_boundary_dates(dates, train_end="2026-02-15", val_start="2026-02-16", purge_days=2)
    assert set(train).isdisjoint(set(valid))
    folds = expanding_walk_forward_folds(dates, train_min=10, val_block=3)
    assert folds
    for fold in folds:
        assert set(fold["train_dates"]).isdisjoint(set(fold["val_dates"]))


def test_sign_validation_detects_correct_orientation() -> None:
    idx = pd.date_range("2026-03-01", periods=100, freq="1min", tz="UTC")
    agg = np.where(np.arange(100) % 2 == 0, 1.0, -1.0)
    # Same-bar price change equals current signed flow.
    delta = agg * 0.001
    price = 110.0 + np.cumsum(delta)
    df = pd.DataFrame(
        {
            "et_date": "2026-03-01",
            "bucket_ts": idx,
            "price": price,
            "agg_1m": agg,
        }
    )
    out = aggressor_sign_validation(df)
    assert out["ok"] is True
