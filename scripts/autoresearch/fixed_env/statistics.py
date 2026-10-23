"""Deterministic, paired, equal-date clustered inference."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


def stable_seed(*parts: object) -> int:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**32)


def _arrays(
    dates: Sequence[object], baseline_loss: Sequence[float], augmented_loss: Sequence[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = np.asarray(dates).astype(str)
    base = np.asarray(baseline_loss, dtype=float)
    aug = np.asarray(augmented_loss, dtype=float)
    if d.ndim != 1 or base.shape != d.shape or aug.shape != d.shape:
        raise ValueError("paired arrays must be aligned vectors")
    if not np.isfinite(base).all() or not np.isfinite(aug).all():
        raise ValueError("losses must be finite")
    return d, base, aug


def equal_date_effect(
    dates: Sequence[object], baseline_loss: Sequence[float], augmented_loss: Sequence[float]
) -> float:
    d, base, aug = _arrays(dates, baseline_loss, augmented_loss)
    unique = np.unique(d)
    if not len(unique):
        raise ValueError("no dates")
    return float(np.mean([np.mean(base[d == day] - aug[d == day]) for day in unique]))


@dataclass(frozen=True)
class ClusteredResult:
    effect: float
    ci_low: float
    ci_high: float
    p_one_sided: float
    n_dates: int
    bootstrap_effects: np.ndarray
    standard_error: float = float("nan")
    t_statistic: float = float("nan")


def _sign_permutation(
    per_date: np.ndarray, *, repetitions: int, seed_key: object
) -> tuple[float, np.ndarray]:
    rng = np.random.default_rng(
        stable_seed(seed_key, "date-cluster-sign", len(per_date), repetitions)
    )
    signs = rng.choice(np.array([-1.0, 1.0]), size=(repetitions, len(per_date)))
    null = (signs * per_date).mean(axis=1)
    observed = float(per_date.mean())
    p_value = float((1 + np.count_nonzero(null >= observed)) / (repetitions + 1))
    return p_value, null


def paired_date_cluster_bootstrap(
    dates: Sequence[object],
    baseline_loss: Sequence[float],
    augmented_loss: Sequence[float],
    *,
    repetitions: int = 999,
    alpha: float = 0.05,
    seed_key: object = "paired-bootstrap",
) -> ClusteredResult:
    d, base, aug = _arrays(dates, baseline_loss, augmented_loss)
    unique = np.unique(d)
    if len(unique) < 2 or repetitions < 99:
        raise ValueError("need >=2 dates and >=99 repetitions")
    per_date = np.array([np.mean(base[d == day] - aug[d == day]) for day in unique])
    rng = np.random.default_rng(stable_seed(seed_key, len(unique), repetitions))
    draws = rng.choice(per_date, size=(repetitions, len(unique)), replace=True).mean(axis=1)
    effect = float(per_date.mean())
    low, high = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    p, _ = _sign_permutation(
        per_date, repetitions=repetitions, seed_key=seed_key,
    )
    return ClusteredResult(effect, float(low), float(high), p, len(unique), draws)


def studentized_date_cluster_wild_bootstrap(
    dates: Sequence[object],
    baseline_loss: Sequence[float],
    augmented_loss: Sequence[float],
    *,
    repetitions: int = 999,
    alpha: float = 0.05,
    seed_key: object = "studentized-wild-bootstrap",
) -> ClusteredResult:
    """Equal-date paired inference using a studentized Rademacher wild bootstrap."""
    d, base, aug = _arrays(dates, baseline_loss, augmented_loss)
    unique = np.unique(d)
    if len(unique) < 2 or repetitions < 99:
        raise ValueError("need >=2 dates and >=99 repetitions")
    effects = np.array([np.mean(base[d == day] - aug[d == day]) for day in unique])
    estimate = float(effects.mean())
    se = float(effects.std(ddof=1) / np.sqrt(len(effects)))
    observed_t = estimate / se if se > 0 else (np.inf if estimate > 0 else 0.0)
    residuals = effects - estimate
    rng = np.random.default_rng(
        stable_seed(seed_key, "studentized-date-cluster-wild", len(unique), repetitions)
    )
    signs = rng.choice(np.array([-1.0, 1.0]), size=(repetitions, len(unique)))
    null_samples = signs * residuals
    null_effects = null_samples.mean(axis=1)
    null_se = null_samples.std(axis=1, ddof=1) / np.sqrt(len(unique))
    null_t = np.divide(
        null_effects, null_se, out=np.zeros_like(null_effects), where=null_se > 0,
    )
    p_value = float((1 + np.count_nonzero(null_t >= observed_t)) / (repetitions + 1))
    critical_low, critical_high = np.quantile(null_t, [alpha / 2, 1 - alpha / 2])
    ci_low = estimate - float(critical_high) * se
    ci_high = estimate - float(critical_low) * se
    return ClusteredResult(
        estimate, ci_low, ci_high, p_value, len(unique), null_effects, se, observed_t,
    )


def repeated_cluster_permutation_pvalue(
    dates: Sequence[object],
    baseline_loss: Sequence[float],
    augmented_loss: Sequence[float],
    *,
    repetitions: int = 999,
    seed_key: object = "cluster-null",
) -> tuple[float, np.ndarray]:
    """Repeated date-cluster sign permutations for the paired sharp null."""
    d, base, aug = _arrays(dates, baseline_loss, augmented_loss)
    unique = np.unique(d)
    observed_by_date = np.array([np.mean(base[d == day] - aug[d == day]) for day in unique])
    if len(unique) < 2 or repetitions < 99:
        raise ValueError("insufficient clusters or repetitions")
    return _sign_permutation(
        observed_by_date, repetitions=repetitions, seed_key=seed_key,
    )


def permute_within_dates(
    values: Sequence[float], dates: Sequence[object], *, seed_key: object
) -> np.ndarray:
    """Permute a candidate feature only within complete-date clusters."""
    result = np.asarray(values).copy()
    date_array = np.asarray(dates).astype(str)
    if result.shape != date_array.shape:
        raise ValueError("values and dates must align")
    rng = np.random.default_rng(stable_seed(seed_key))
    for day in np.unique(date_array):
        indices = np.flatnonzero(date_array == day)
        result[indices] = result[rng.permutation(indices)]
    return result


def holm(p_values: Sequence[float], alpha: float = 0.05) -> list[bool]:
    values = np.asarray(p_values, dtype=float)
    if values.ndim != 1 or np.any((values < 0) | (values > 1)):
        raise ValueError("p-values must be in [0,1]")
    order = np.argsort(values, kind="stable")
    rejected = np.zeros(len(values), dtype=bool)
    for rank, index in enumerate(order):
        if values[index] <= alpha / (len(values) - rank):
            rejected[index] = True
        else:
            break
    return rejected.tolist()


def holm_adjusted_pvalues(
    p_values: Sequence[float], *, denominator: int | None = None
) -> list[float]:
    """Holm adjusted p-values while retaining a pre-registered family size."""
    values = np.asarray(p_values, dtype=float)
    if values.ndim != 1 or np.any(~np.isfinite(values)) or np.any((values < 0) | (values > 1)):
        raise ValueError("p-values must be finite and in [0,1]")
    family_size = len(values) if denominator is None else int(denominator)
    if family_size < len(values) or family_size <= 0:
        raise ValueError("denominator must cover every supplied p-value")
    order = np.argsort(values, kind="stable")
    adjusted = np.empty(len(values), dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (family_size - rank) * float(values[index]))
        adjusted[index] = min(1.0, running)
    return adjusted.tolist()


def holm_with_denominator(
    p_values: Sequence[float], *, alpha: float = 0.05, denominator: int | None = None
) -> tuple[list[float], list[bool], int]:
    family_size = len(p_values) if denominator is None else int(denominator)
    adjusted = holm_adjusted_pvalues(p_values, denominator=family_size)
    return adjusted, [value <= alpha for value in adjusted], family_size


def non_overlapping_anchors(
    timestamps: Iterable[int], horizon: int
) -> list[int]:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    selected: list[int] = []
    for timestamp in sorted(set(int(item) for item in timestamps)):
        if not selected or timestamp - selected[-1] >= horizon:
            selected.append(timestamp)
    return selected


def circular_block_shift(
    values: Sequence[float],
    dates: Sequence[object],
    timestamps_ns: Sequence[int],
    *,
    minimum_shift_ns: int = 30 * 60 * 1_000_000_000,
    seed_key: object = "block-shift",
) -> np.ndarray:
    """Circularly shift each date by a random observed lag of at least 30 minutes."""
    result = np.asarray(values).copy()
    day = np.asarray(dates).astype(str)
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    if result.ndim != 1 or result.shape != day.shape or result.shape != ts.shape:
        raise ValueError("values, dates, and timestamps must align")
    if minimum_shift_ns < 30 * 60 * 1_000_000_000:
        raise ValueError("protocol shifts must be at least 30 minutes")
    rng = np.random.default_rng(stable_seed(seed_key, "circular-block-shift"))
    for label in np.unique(day):
        indices = np.flatnonzero(day == label)
        ordered = indices[np.argsort(ts[indices], kind="stable")]
        if len(ordered) < 2:
            raise ValueError(f"date {label} has no admissible circular shift")
        lags = np.array([
            min(
                int(ts[ordered[offset]] - ts[ordered[0]]),
                int(ts[ordered[-1]] - ts[ordered[-1 - offset]]),
            )
            for offset in range(1, len(ordered))
        ])
        admissible = np.flatnonzero(lags >= minimum_shift_ns) + 1
        if not len(admissible):
            raise ValueError(f"date {label} has less than minimum shift coverage")
        offset = int(rng.choice(admissible))
        result[ordered] = np.roll(result[ordered], offset)
    return result


def enforce_sample_gates(
    dates: Sequence[object],
    outcomes: Sequence[int] | None = None,
    *,
    groups: Sequence[object] | None = None,
    min_dates: int = 25,
    min_positive: int = 30,
    min_negative: int = 0,
    required_power_dates: int | None = None,
) -> None:
    date_count = len(set(str(item) for item in dates))
    required_dates = max(min_dates, required_power_dates or 0)
    if date_count < required_dates:
        raise ValueError("minimum independent-date gate failed")
    if outcomes is not None:
        values = np.asarray(outcomes)
        if values.shape != np.asarray(dates).shape:
            raise ValueError("outcomes and dates must align")
        if np.count_nonzero(values == 1) < min_positive:
            raise ValueError("minimum positive-event gate failed")
        if np.count_nonzero(values == 0) < min_negative:
            raise ValueError("minimum negative-event gate failed")
        if groups is not None:
            labels = np.asarray(groups)
            if labels.shape != values.shape or len(np.unique(labels)) < 2:
                raise ValueError("comparator and candidate groups must align")
            for label in np.unique(labels):
                if np.count_nonzero((labels == label) & (values == 1)) < min_positive:
                    raise ValueError(f"minimum positive-event gate failed for group {label}")
                if np.count_nonzero((labels == label) & (values == 0)) < min_negative:
                    raise ValueError(f"minimum negative-event gate failed for group {label}")


def power_date_requirement(
    minimum_effect: float,
    per_date_standard_deviation: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Normal-approximation independent-date requirement for a one-sided test."""
    if minimum_effect <= 0 or per_date_standard_deviation <= 0:
        raise ValueError("effect and standard deviation must be positive")
    if not 0 < alpha < 0.5 or not 0.5 < power < 1:
        raise ValueError("invalid alpha or power")
    from scipy.stats import norm

    z_alpha = norm.ppf(1 - alpha)
    z_power = norm.ppf(power)
    return int(np.ceil(((z_alpha + z_power) * per_date_standard_deviation / minimum_effect) ** 2))

