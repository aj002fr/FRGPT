"""Evaluator-owned future outcome helpers."""

from __future__ import annotations

import numpy as np

NS_PER_MINUTE = 60 * 1_000_000_000


def _finite_path(path: np.ndarray) -> np.ndarray:
    values = np.asarray(path, dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
        raise ValueError("path must be a non-empty finite vector")
    return values


def future_window(
    timestamps_ns: np.ndarray,
    prices: np.ndarray,
    formation_ns: int,
    horizon_ns: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return observations strictly after formation through the horizon."""
    timestamps = np.asarray(timestamps_ns, dtype=np.int64)
    values = np.asarray(prices, dtype=float)
    if timestamps.ndim != 1 or values.ndim != 1 or len(timestamps) != len(values):
        raise ValueError("timestamps and prices must be aligned vectors")
    if np.any(np.diff(timestamps) <= 0):
        raise ValueError("timestamps must be unique and increasing")
    mask = (timestamps > formation_ns) & (timestamps <= formation_ns + horizon_ns)
    return timestamps[mask], values[mask]


def signed_return(
    formation_price: float,
    exit_price: float,
    *,
    direction: int = 1,
    cost: float = 0.0,
) -> float:
    if direction not in {-1, 1} or cost < 0:
        raise ValueError("direction must be +/-1 and cost non-negative")
    return direction * (float(exit_price) - float(formation_price)) - cost


def path_extrema(path: np.ndarray, anchor_price: float) -> tuple[float, float]:
    values = np.asarray(path, dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("path must contain finite prices")
    moves = values - float(anchor_price)
    return float(moves.max()), float(moves.min())


def breakout_hold_label(
    path: np.ndarray,
    anchor_price: float,
    *,
    breakout: float,
    hold_fraction: float,
) -> str:
    """Classify a path as UP_HOLD, DOWN_HOLD, REVERSAL, or OTHER."""
    if breakout <= 0 or not 0 < hold_fraction <= 1:
        raise ValueError("invalid path thresholds")
    values = np.asarray(path, dtype=float)
    if values.size < 2:
        return "OTHER"
    moves = values - float(anchor_price)
    up = np.flatnonzero(moves >= breakout)
    down = np.flatnonzero(moves <= -breakout)
    if up.size and down.size:
        return "REVERSAL"
    if up.size:
        return "UP_HOLD" if np.mean(moves[up[0]:] > 0) >= hold_fraction else "OTHER"
    if down.size:
        return "DOWN_HOLD" if np.mean(moves[down[0]:] < 0) >= hold_fraction else "OTHER"
    return "OTHER"


def range_label(path: np.ndarray, anchor_price: float, threshold: float) -> str:
    maximum, minimum = path_extrema(path, anchor_price)
    return "RANGE" if max(abs(maximum), abs(minimum)) < threshold else "RESOLVED"


def e001_bounded_4t_30m(
    close_path: np.ndarray,
    anchor_price: float,
    *,
    tick_size: float = 1 / 64,
) -> int:
    """E001 BOUNDED_4T_30M: all observed closes remain strictly inside +/-4 ticks."""
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    moves = np.abs(_finite_path(close_path) - float(anchor_price))
    return int(np.all(moves < 4 * tick_size))


def close_first_passage(
    close_timestamps_ns: np.ndarray,
    close_path: np.ndarray,
    anchor_price: float,
    *,
    barrier: float,
) -> dict[str, int | str | None]:
    """Close-based first passage; intrabar highs/lows are intentionally ignored."""
    timestamps = np.asarray(close_timestamps_ns, dtype=np.int64)
    values = _finite_path(close_path)
    if timestamps.shape != values.shape or np.any(np.diff(timestamps) <= 0):
        raise ValueError("close timestamps and path must align and increase")
    if barrier <= 0:
        raise ValueError("barrier must be positive")
    moves = values - float(anchor_price)
    up = np.flatnonzero(moves >= barrier)
    down = np.flatnonzero(moves <= -barrier)
    up_index = int(up[0]) if len(up) else None
    down_index = int(down[0]) if len(down) else None
    if up_index is None and down_index is None:
        return {"label": "NO_PASSAGE", "passage_ts_ns": None, "index": None}
    if down_index is None or (up_index is not None and up_index < down_index):
        return {"label": "UP_FIRST", "passage_ts_ns": int(timestamps[up_index]), "index": up_index}
    if up_index is None or down_index < up_index:
        return {
            "label": "DOWN_FIRST", "passage_ts_ns": int(timestamps[down_index]),
            "index": down_index,
        }
    return {"label": "TIE", "passage_ts_ns": int(timestamps[up_index]), "index": up_index}


def e002_close_first_passage(
    close_timestamps_ns: np.ndarray,
    close_path: np.ndarray,
    anchor_price: float,
    *,
    formation_gap: float,
) -> int:
    """E002: 50% convergence toward frozen FV before 100% adverse passage."""
    timestamps = np.asarray(close_timestamps_ns, dtype=np.int64)
    values = _finite_path(close_path)
    gap = float(formation_gap)
    if timestamps.shape != values.shape or np.any(np.diff(timestamps) <= 0):
        raise ValueError("close timestamps and path must align and increase")
    if not np.isfinite(gap) or gap == 0:
        raise ValueError("formation_gap must be finite and non-zero")
    directed_move = np.sign(gap) * (values - float(anchor_price))
    convergence = np.flatnonzero(directed_move >= 0.5 * abs(gap))
    adverse = np.flatnonzero(directed_move <= -abs(gap))
    convergence_index = int(convergence[0]) if len(convergence) else None
    adverse_index = int(adverse[0]) if len(adverse) else None
    return int(
        convergence_index is not None
        and (adverse_index is None or convergence_index < adverse_index)
    )

