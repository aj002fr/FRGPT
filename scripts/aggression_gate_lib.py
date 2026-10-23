#!/usr/bin/env python3
"""Pure statistical gating helpers for offline aggression research.

Guardrail: this module has no imports from V1/V2/V3 apps and performs no I/O
other than the optional synthetic self-check in ``__main__``.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np
from scipy.stats import t as student_t

DEFAULT_COSTS = (0.0, 0.25, 0.5, 1.0)
EPS = 1e-12


def _finite(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float).reshape(-1)
    return arr[np.isfinite(arr)]


def _bootstrap_means(
    values: np.ndarray,
    *,
    n_boot: int,
    seed: int,
    groups: Sequence[Any] | None = None,
) -> np.ndarray:
    """Return deterministic bootstrap means, optionally clustered by group."""
    rng = np.random.default_rng(seed)
    if groups is None:
        if len(values) == 0:
            return np.array([], dtype=float)
        out = np.empty(n_boot, dtype=float)
        chunk = max(1, min(256, n_boot))
        for start in range(0, n_boot, chunk):
            size = min(chunk, n_boot - start)
            draws = rng.integers(0, len(values), size=(size, len(values)))
            out[start : start + size] = values[draws].mean(axis=1)
        return out

    group_arr = np.asarray(list(groups), dtype=object).reshape(-1)
    if len(group_arr) != len(values):
        raise ValueError("groups must have the same length as values before filtering")
    valid = np.isfinite(values)
    vals = values[valid]
    group_arr = group_arr[valid]
    unique = np.unique(group_arr)
    if len(unique) == 0:
        return np.array([], dtype=float)
    group_sums = np.asarray([vals[group_arr == g].sum() for g in unique], dtype=float)
    group_counts = np.asarray([(group_arr == g).sum() for g in unique], dtype=float)
    out = np.empty(n_boot, dtype=float)
    chunk = max(1, min(512, n_boot))
    for start in range(0, n_boot, chunk):
        size = min(chunk, n_boot - start)
        draws = rng.integers(0, len(unique), size=(size, len(unique)))
        sums = group_sums[draws].sum(axis=1)
        counts = group_counts[draws].sum(axis=1)
        out[start : start + size] = sums / np.maximum(counts, 1.0)
    return out[np.isfinite(out)]


def bootstrap_ci(
    x: Iterable[float],
    n_boot: int = 10_000,
    alpha: float = 0.05,
    *,
    seed: int = 7,
    groups: Sequence[Any] | None = None,
) -> dict[str, float | int]:
    """Mean, two-sided percentile CI, and one-sided lower confidence bound."""
    raw = np.asarray(list(x), dtype=float).reshape(-1)
    valid = np.isfinite(raw)
    values = raw[valid]
    filtered_groups = None
    if groups is not None:
        group_arr = np.asarray(list(groups), dtype=object).reshape(-1)
        if len(group_arr) != len(raw):
            raise ValueError("groups must have the same length as x")
        filtered_groups = group_arr[valid]
    if len(values) == 0:
        return {"n": 0, "mean": np.nan, "ci_low": np.nan, "ci_high": np.nan, "one_sided_lower": np.nan}
    means = _bootstrap_means(values, n_boot=n_boot, seed=seed, groups=filtered_groups)
    if len(means) == 0:
        return {"n": int(len(values)), "mean": float(values.mean()), "ci_low": np.nan, "ci_high": np.nan, "one_sided_lower": np.nan}
    return {
        "n": int(len(values)),
        "mean": float(values.mean()),
        "ci_low": float(np.quantile(means, alpha / 2.0)),
        "ci_high": float(np.quantile(means, 1.0 - alpha / 2.0)),
        "one_sided_lower": float(np.quantile(means, alpha)),
    }


def one_sided_p(x: Iterable[float]) -> float:
    """One-sided t-test p-value for H1: mean(x) > 0."""
    values = _finite(x)
    if len(values) < 2:
        return np.nan
    mean = float(values.mean())
    std = float(values.std(ddof=1))
    if std <= EPS:
        return 0.0 if mean > 0 else 1.0
    t_stat = mean / (std / np.sqrt(len(values)))
    return float(student_t.sf(t_stat, df=len(values) - 1))


def cost_sweep(
    gross_ticks: Iterable[float],
    costs: Iterable[float] = DEFAULT_COSTS,
) -> dict[str, float]:
    """Mean net ticks at each round-trip cost."""
    gross = _finite(gross_ticks)
    return {
        f"after_cost_{float(cost):g}": float(gross.mean() - float(cost)) if len(gross) else np.nan
        for cost in costs
    }


def benjamini_hochberg(
    pvals: Iterable[float],
    q: float = 0.10,
) -> dict[str, np.ndarray]:
    """Benjamini-Hochberg adjusted p-values and rejection flags."""
    raw = np.asarray(list(pvals), dtype=float)
    valid_idx = np.flatnonzero(np.isfinite(raw))
    adjusted = np.full(len(raw), np.nan, dtype=float)
    reject = np.zeros(len(raw), dtype=bool)
    if not len(valid_idx):
        return {"adjusted_p": adjusted, "reject": reject}
    pv = raw[valid_idx]
    order = np.argsort(pv)
    ranked = pv[order]
    m = len(ranked)
    adj_sorted = np.minimum.accumulate((ranked * m / np.arange(1, m + 1))[::-1])[::-1]
    adj_sorted = np.clip(adj_sorted, 0.0, 1.0)
    adj = np.empty(m, dtype=float)
    adj[order] = adj_sorted
    adjusted[valid_idx] = adj
    reject[valid_idx] = adj <= q
    return {"adjusted_p": adjusted, "reject": reject}


def classify(cell: dict[str, Any]) -> str:
    """Classify a fully evaluated cell as PASS, PROVISIONAL, or FAIL."""
    mean = float(cell.get("mean_after_cost_ticks", np.nan))
    if not np.isfinite(mean) or mean <= 0.0:
        return "FAIL"
    n = int(cell.get("n", 0) or 0)
    bh_significant = bool(cell.get("bh_significant", False))
    lower = float(cell.get("one_sided_lower", np.nan))
    stability_ok = bool(cell.get("stability_ok", False))
    if n >= 30 and bh_significant and np.isfinite(lower) and lower >= 0.0 and stability_ok:
        return "PASS"
    return "PROVISIONAL"


def _self_check() -> None:
    rng = np.random.default_rng(17)
    positive = rng.normal(0.8, 0.7, size=200)
    flat = rng.normal(0.0, 1.0, size=200)
    ci = bootstrap_ci(positive, n_boot=2_000)
    assert ci["ci_low"] > 0
    assert one_sided_p(positive) < 0.01
    assert one_sided_p(flat) > 0.01
    sweep = cost_sweep(positive, costs=(0.0, 0.5))
    assert sweep["after_cost_0"] > sweep["after_cost_0.5"]
    bh = benjamini_hochberg([0.001, 0.02, 0.5], q=0.10)
    assert bool(bh["reject"][0])
    cell = {
        "n": 200,
        "mean_after_cost_ticks": ci["mean"],
        "one_sided_lower": ci["one_sided_lower"],
        "bh_significant": True,
        "stability_ok": True,
    }
    assert classify(cell) == "PASS"
    print("aggression_gate_lib self-check: ok")


if __name__ == "__main__":
    _self_check()
