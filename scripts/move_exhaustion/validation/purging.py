"""Causality and purge helpers."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def assert_feature_causal(
    compute_fn,
    frame: pd.DataFrame,
    ts_col: str,
    feature_col: str,
    mutate_after_ts,
) -> bool:
    """Recompute after mutating rows after t; feature at t must be unchanged."""
    base = compute_fn(frame)
    if base.empty or feature_col not in base.columns:
        raise ValueError(f"compute_fn must return column {feature_col}")
    t = base[ts_col].iloc[len(base) // 2]
    base_val = base.loc[base[ts_col] == t, feature_col].iloc[0]
    perturbed = mutate_after_ts(frame.copy(), t)
    redo = compute_fn(perturbed)
    redo_val = redo.loc[redo[ts_col] == t, feature_col].iloc[0]
    if pd.isna(base_val) and pd.isna(redo_val):
        return True
    return bool(np.isclose(float(base_val), float(redo_val), equal_nan=True))


def purge_boundary_dates(
    dates: Iterable[str],
    *,
    train_end: str,
    val_start: str,
    purge_days: int,
) -> tuple[list[str], list[str]]:
    """Remove purge_days on each side of the train/validation date boundary."""
    ordered = sorted(str(d) for d in dates)
    if train_end not in ordered or val_start not in ordered:
        raise ValueError("train_end and val_start must appear in dates")
    i_train = ordered.index(train_end)
    i_val = ordered.index(val_start)
    drop = set(ordered[max(0, i_train - purge_days + 1) : i_train + 1])
    drop |= set(ordered[i_val : min(len(ordered), i_val + purge_days)])
    train = [d for d in ordered[: i_train + 1] if d not in drop]
    valid = [d for d in ordered[i_val:] if d not in drop]
    return train, valid


def expanding_walk_forward_folds(
    dates: list[str],
    *,
    train_min: int = 30,
    val_block: int = 5,
) -> list[dict[str, list[str]]]:
    """Expanding chronological folds: train first N, validate next val_block."""
    ordered = sorted(dates)
    folds = []
    start = train_min
    while start + val_block <= len(ordered):
        train = ordered[:start]
        valid = ordered[start : start + val_block]
        folds.append({"train_dates": train, "val_dates": valid, "fold_id": len(folds) + 1})
        start += val_block
    return folds
