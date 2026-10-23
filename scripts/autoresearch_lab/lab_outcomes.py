"""Parameterized discovery outcomes composed from frozen outcome primitives."""

from __future__ import annotations

from typing import Any

import numpy as np

from scripts.autoresearch.fixed_env.outcomes import (
    NS_PER_MINUTE,
    close_first_passage,
    future_window,
    path_extrema,
    range_label,
)

TICK_SIZE = 1 / 64
OUTCOME_NAMES = (
    "bounded_excursion",
    "convergence_first_passage",
    "directional_first_passage",
)


def bounded_excursion(
    close_path: np.ndarray,
    anchor_price: float,
    *,
    threshold_ticks: float = 4.0,
    tick_size: float = TICK_SIZE,
) -> int:
    if threshold_ticks <= 0 or tick_size <= 0:
        raise ValueError("threshold_ticks and tick_size must be positive")
    label = range_label(close_path, anchor_price, threshold_ticks * tick_size)
    return int(label == "RANGE")


def convergence_first_passage(
    close_timestamps_ns: np.ndarray,
    close_path: np.ndarray,
    anchor_price: float,
    *,
    formation_gap: float,
    convergence_fraction: float = 0.5,
    adverse_fraction: float = 1.0,
) -> int:
    if not 0 < convergence_fraction <= 1 or adverse_fraction <= 0:
        raise ValueError("invalid convergence/adverse fractions")
    timestamps = np.asarray(close_timestamps_ns, dtype=np.int64)
    values = np.asarray(close_path, dtype=float)
    path_extrema(values, anchor_price)
    if timestamps.shape != values.shape or np.any(np.diff(timestamps) <= 0):
        raise ValueError("timestamps and close path must align and increase")
    gap = float(formation_gap)
    if not np.isfinite(gap) or gap == 0:
        raise ValueError("formation_gap must be finite and non-zero")
    directed = np.sign(gap) * (values - float(anchor_price))
    favorable = np.flatnonzero(directed >= convergence_fraction * abs(gap))
    adverse = np.flatnonzero(directed <= -adverse_fraction * abs(gap))
    first_good = int(favorable[0]) if len(favorable) else None
    first_bad = int(adverse[0]) if len(adverse) else None
    return int(first_good is not None and (first_bad is None or first_good < first_bad))


def directional_first_passage(
    close_timestamps_ns: np.ndarray,
    close_path: np.ndarray,
    anchor_price: float,
    *,
    barrier_ticks: float = 4.0,
    direction: int = 1,
    tick_size: float = TICK_SIZE,
) -> int:
    if direction not in {-1, 1} or barrier_ticks <= 0 or tick_size <= 0:
        raise ValueError("direction must be +/-1 and barriers must be positive")
    passage = close_first_passage(
        close_timestamps_ns,
        close_path,
        anchor_price,
        barrier=barrier_ticks * tick_size,
    )
    expected = "UP_FIRST" if direction == 1 else "DOWN_FIRST"
    return int(passage["label"] == expected)


def slice_future(
    timestamps_ns: np.ndarray,
    prices: np.ndarray,
    formation_ns: int,
    *,
    horizon_minutes: int,
) -> tuple[np.ndarray, np.ndarray]:
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    return future_window(
        timestamps_ns,
        prices,
        int(formation_ns),
        int(horizon_minutes) * NS_PER_MINUTE,
    )


def evaluate_outcome(
    name: str,
    timestamps_ns: np.ndarray,
    close_path: np.ndarray,
    anchor_price: float,
    *,
    formation_gap: float | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    values = dict(params or {})
    if name == "bounded_excursion":
        return bounded_excursion(close_path, anchor_price, **values)
    if name == "convergence_first_passage":
        if formation_gap is None:
            raise ValueError("formation_gap is required for convergence outcome")
        return convergence_first_passage(
            timestamps_ns,
            close_path,
            anchor_price,
            formation_gap=formation_gap,
            **values,
        )
    if name == "directional_first_passage":
        return directional_first_passage(
            timestamps_ns, close_path, anchor_price, **values
        )
    raise ValueError(f"unsupported lab outcome: {name}")
