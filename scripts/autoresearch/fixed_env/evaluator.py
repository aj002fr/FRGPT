"""Fixed evaluator: sole owner of targets, sampling, costs, and baselines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import minimize

from .statistics import (
    ClusteredResult,
    paired_date_cluster_bootstrap,
    studentized_date_cluster_wild_bootstrap,
)


@dataclass(frozen=True)
class FrozenLogit:
    feature_names: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    optimizer: str
    iterations: int
    gradient_max: float


def _matrix(rows: Sequence[Mapping[str, float]], names: Sequence[str]) -> np.ndarray:
    matrix = np.asarray([[row[name] for name in names] for row in rows], dtype=float)
    if matrix.ndim != 2:
        raise ValueError("features must be a matrix")
    return matrix


def _impute(matrix: np.ndarray, medians: np.ndarray) -> np.ndarray:
    if np.any(~np.isfinite(medians)):
        raise ValueError("every feature needs at least one finite discovery value")
    return np.where(np.isfinite(matrix), matrix, medians)


def fit_logit(
    rows: Sequence[Mapping[str, float]],
    target: Sequence[int],
    feature_names: Sequence[str],
    *,
    split: str,
) -> FrozenLogit:
    if split != "discovery":
        raise PermissionError("models may fit on discovery only")
    x = _matrix(rows, feature_names)
    y = np.asarray(target, dtype=float)
    if y.shape != (len(x),) or not set(np.unique(y)).issubset({0.0, 1.0}):
        raise ValueError("target must be aligned binary data")
    medians = np.nanmedian(np.where(np.isfinite(x), x, np.nan), axis=0)
    x = _impute(x, medians)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales == 0] = 1.0
    z = (x - means) / scales

    def objective(params: np.ndarray) -> float:
        logits = np.clip(params[0] + z @ params[1:], -35, 35)
        return float(np.mean(np.logaddexp(0, logits) - y * logits)
                     + 1e-6 * np.sum(params[1:] ** 2))

    result = minimize(objective, np.zeros(z.shape[1] + 1), method="L-BFGS-B")
    gradient_max = float(np.max(np.abs(result.jac)))
    if (
        not np.isfinite(result.fun)
        or not np.isfinite(result.x).all()
        or (not result.success and gradient_max > 1e-5)
    ):
        raise RuntimeError(
            f"discovery logit fit failed: {result.message}; gradient={gradient_max:.3g}"
        )
    return FrozenLogit(
        tuple(feature_names), tuple(medians), tuple(means), tuple(scales),
        tuple(result.x[1:]), float(result.x[0]), "L-BFGS-B",
        int(result.nit), gradient_max,
    )


def transform_logit(
    rows: Sequence[Mapping[str, float]], state: FrozenLogit
) -> np.ndarray:
    x = _matrix(rows, state.feature_names)
    x = _impute(x, np.asarray(state.medians))
    z = (x - np.asarray(state.means)) / np.asarray(state.scales)
    logits = np.clip(state.intercept + z @ np.asarray(state.coefficients), -35, 35)
    return 1.0 / (1.0 + np.exp(-logits))


@dataclass(frozen=True)
class PairedEvaluation:
    baseline_brier: float
    augmented_brier: float
    inference: ClusteredResult


def evaluate_brier(
    dates: Sequence[object],
    target: Sequence[int],
    baseline_probability: Sequence[float],
    augmented_probability: Sequence[float],
    *,
    repetitions: int = 999,
    seed_key: object = "evaluation",
) -> PairedEvaluation:
    y = np.asarray(target, dtype=float)
    baseline = np.asarray(baseline_probability, dtype=float)
    augmented = np.asarray(augmented_probability, dtype=float)
    if not (y.shape == baseline.shape == augmented.shape):
        raise ValueError("evaluation vectors must align")
    if np.any((baseline < 0) | (baseline > 1) | (augmented < 0) | (augmented > 1)):
        raise ValueError("probabilities must be in [0,1]")
    baseline_loss = (baseline - y) ** 2
    augmented_loss = (augmented - y) ** 2
    inference = paired_date_cluster_bootstrap(
        dates, baseline_loss, augmented_loss,
        repetitions=repetitions, seed_key=seed_key,
    )
    return PairedEvaluation(
        float(np.mean(baseline_loss)), float(np.mean(augmented_loss)), inference,
    )


def delayed_signed_target(
    formation_price: Sequence[float],
    delayed_exit_price: Sequence[float],
    *,
    threshold: float,
    cost: float,
) -> np.ndarray:
    """Evaluator-owned executable binary target after delay and round-trip cost."""
    if threshold < 0 or cost < 0:
        raise ValueError("threshold and cost must be non-negative")
    entry = np.asarray(formation_price, dtype=float)
    exit_ = np.asarray(delayed_exit_price, dtype=float)
    if entry.shape != exit_.shape:
        raise ValueError("price vectors must align")
    return ((exit_ - entry - cost) >= threshold).astype(int)


@dataclass(frozen=True)
class CommonAnchors:
    timestamps_ns: np.ndarray
    dates: np.ndarray
    source_indices: Mapping[str, np.ndarray]
    coverage_mask: np.ndarray


def construct_common_anchors(
    dates: Sequence[object],
    timestamps_ns: Sequence[int],
    source_available: Mapping[str, Sequence[bool]],
    *,
    horizon_ns: int,
    session_close_ns: Mapping[str, int] | Sequence[int],
    session_open_ns: Mapping[str, int] | Sequence[int] | None = None,
    minimum_future_coverage: float = 0.80,
    horizon_tolerance_ns: int = 2 * 60 * 1_000_000_000,
) -> CommonAnchors:
    """Build date-grouped nanosecond anchors with common source and censor coverage."""
    day = np.asarray(dates).astype(str)
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    if day.ndim != 1 or day.shape != ts.shape or horizon_ns <= 0:
        raise ValueError("dates/timestamps must align and horizon must be positive")
    if not 0 < minimum_future_coverage <= 1 or horizon_tolerance_ns < 0:
        raise ValueError("invalid future coverage requirement")
    if np.any(np.diff(ts[np.argsort(ts, kind="stable")]) == 0):
        raise ValueError("timestamps must be globally unique")
    available = {
        name: np.asarray(mask, dtype=bool) for name, mask in source_available.items()
    }
    if not available or any(mask.shape != ts.shape for mask in available.values()):
        raise ValueError("every source mask must align")

    def boundary(value: Mapping[str, int] | Sequence[int], label: str) -> np.ndarray:
        if isinstance(value, Mapping):
            try:
                return np.asarray([value[item] for item in day], dtype=np.int64)
            except KeyError as exc:
                raise ValueError(f"missing {label} for date {exc.args[0]}") from exc
        result = np.asarray(value, dtype=np.int64)
        if result.shape != ts.shape:
            raise ValueError(f"{label} must align")
        return result

    close = boundary(session_close_ns, "session close")
    open_ = (
        np.full(ts.shape, np.iinfo(np.int64).min)
        if session_open_ns is None else boundary(session_open_ns, "session open")
    )
    coverage = (ts >= open_) & (ts + horizon_ns <= close)
    for mask in available.values():
        coverage &= mask
    minute_ns = 60 * 1_000_000_000
    expected = int(np.ceil(horizon_ns / minute_ns))
    required = int(np.ceil(minimum_future_coverage * expected))
    common = np.logical_and.reduce(list(available.values()))
    for label in np.unique(day):
        indices = np.flatnonzero(day == label)
        common_times = np.sort(ts[indices][common[indices]])
        if not len(common_times):
            coverage[indices] = False
            continue
        starts = np.searchsorted(common_times, ts[indices], side="right")
        endpoints = ts[indices] + horizon_ns
        stops = np.searchsorted(common_times, endpoints, side="right")
        enough_observations = (stops - starts) >= required
        near_horizon_start = np.searchsorted(
            common_times, endpoints - horizon_tolerance_ns, side="left",
        )
        near_horizon = near_horizon_start < stops
        coverage[indices] &= enough_observations & near_horizon
    selected = np.flatnonzero(coverage)
    source_indices = {name: selected.copy() for name in available}
    return CommonAnchors(ts[selected], day[selected], source_indices, coverage)


common_anchor_construction = construct_common_anchors


@dataclass(frozen=True)
class OuterFoldContract:
    selected_cells: tuple[str, ...]
    common_mask: np.ndarray
    selection_fold: str
    evaluation_fold: str


def outer_fold_selection_contract(
    cell_scores: Mapping[str, float],
    cell_masks: Mapping[str, Sequence[bool]],
    baseline_mask: Sequence[bool],
    *,
    selection_fold: str,
    evaluation_fold: str,
    max_cells: int = 1,
) -> OuterFoldContract:
    """Freeze selection on one outer fold and a paired common evaluation sample."""
    if selection_fold == evaluation_fold or max_cells <= 0:
        raise ValueError("outer selection and evaluation folds must differ")
    selected = tuple(
        sorted(cell_scores, key=lambda name: (-float(cell_scores[name]), name))[:max_cells]
    )
    if not selected:
        raise ValueError("no selectable cells")
    common = np.asarray(baseline_mask, dtype=bool).copy()
    for name in selected:
        mask = np.asarray(cell_masks[name], dtype=bool)
        if mask.shape != common.shape:
            raise ValueError("all common-sample masks must align")
        common &= mask
    if not common.any():
        raise ValueError("outer-fold common sample is empty")
    return OuterFoldContract(selected, common, selection_fold, evaluation_fold)


def calibration_intercept_slope(
    target: Sequence[int], probability: Sequence[float]
) -> tuple[float, float]:
    y = np.asarray(target, dtype=float)
    p = np.asarray(probability, dtype=float)
    if y.shape != p.shape or not set(np.unique(y)).issubset({0.0, 1.0}):
        raise ValueError("target must be aligned binary data")
    if np.any((p < 0) | (p > 1)):
        raise ValueError("probabilities must be in [0,1]")
    logits = np.log(np.clip(p, 1e-9, 1 - 1e-9) / np.clip(1 - p, 1e-9, 1))

    def objective(params: np.ndarray) -> float:
        fitted = np.clip(params[0] + params[1] * logits, -35, 35)
        return float(np.mean(np.logaddexp(0, fitted) - y * fitted))

    result = minimize(objective, np.array([0.0, 1.0]), method="BFGS")
    if not np.isfinite(result.fun):
        raise RuntimeError("calibration fit failed")
    return float(result.x[0]), float(result.x[1])


def relative_brier_score(
    target: Sequence[int], probability: Sequence[float], reference_probability: Sequence[float]
) -> float:
    y = np.asarray(target, dtype=float)
    candidate = np.asarray(probability, dtype=float)
    reference = np.asarray(reference_probability, dtype=float)
    if not (y.shape == candidate.shape == reference.shape):
        raise ValueError("Brier vectors must align")
    reference_loss = float(np.mean((reference - y) ** 2))
    if reference_loss <= 0:
        raise ValueError("reference Brier loss must be positive")
    return float(1 - np.mean((candidate - y) ** 2) / reference_loss)


def net_utility(
    realized_move: Sequence[float],
    position: Sequence[float],
    *,
    cost_per_unit: float = 0.0,
) -> np.ndarray:
    move = np.asarray(realized_move, dtype=float)
    exposure = np.asarray(position, dtype=float)
    if move.shape != exposure.shape or cost_per_unit < 0:
        raise ValueError("utility vectors must align and cost must be non-negative")
    turnover = np.abs(np.diff(exposure, prepend=0.0))
    return exposure * move - cost_per_unit * turnover


def stability_summary(effects: Sequence[float]) -> Mapping[str, float | int]:
    values = np.asarray(effects, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("effects must be a non-empty finite vector")
    signs = np.sign(values)
    dominant = 1 if np.count_nonzero(signs > 0) >= np.count_nonzero(signs < 0) else -1
    return {
        "n": int(len(values)),
        "mean": float(values.mean()),
        "minimum": float(values.min()),
        "maximum": float(values.max()),
        "dominant_sign_fraction": float(np.mean(signs == dominant)),
    }


def evaluator_overlapping_cell_masks(
    raw_masks: Mapping[str, Sequence[bool]],
    *,
    priority: Sequence[str] | None = None,
) -> Mapping[str, np.ndarray]:
    """Validate evaluator-owned masks while preserving ALL/subgroup overlap."""
    if not raw_masks:
        return {}
    order = tuple(priority or sorted(raw_masks))
    if len(order) != len(raw_masks) or set(order) != set(raw_masks):
        raise ValueError("priority must name every cell exactly once")
    shape = np.asarray(next(iter(raw_masks.values())), dtype=bool).shape
    resolved: dict[str, np.ndarray] = {}
    for name in order:
        mask = np.asarray(raw_masks[name], dtype=bool)
        if mask.shape != shape:
            raise ValueError("cell masks must align")
        resolved[name] = mask.copy()
    return resolved


def evaluator_cell_masks(
    raw_masks: Mapping[str, Sequence[bool]],
    *,
    priority: Sequence[str] | None = None,
) -> Mapping[str, np.ndarray]:
    """Backward-compatible mask validator; overlaps are intentionally retained."""
    return evaluator_overlapping_cell_masks(raw_masks, priority=priority)


def evaluate_brier_studentized(
    dates: Sequence[object],
    target: Sequence[int],
    baseline_probability: Sequence[float],
    augmented_probability: Sequence[float],
    *,
    repetitions: int = 999,
    seed_key: object = "evaluation-studentized",
) -> PairedEvaluation:
    y = np.asarray(target, dtype=float)
    baseline = np.asarray(baseline_probability, dtype=float)
    augmented = np.asarray(augmented_probability, dtype=float)
    if not (y.shape == baseline.shape == augmented.shape):
        raise ValueError("evaluation vectors must align")
    inference = studentized_date_cluster_wild_bootstrap(
        dates, (baseline - y) ** 2, (augmented - y) ** 2,
        repetitions=repetitions, seed_key=seed_key,
    )
    return PairedEvaluation(
        float(np.mean((baseline - y) ** 2)),
        float(np.mean((augmented - y) ** 2)),
        inference,
    )

