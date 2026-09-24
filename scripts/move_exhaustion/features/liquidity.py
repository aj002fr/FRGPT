"""E11 liquidity regime buckets and interaction helpers."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def fit_liquidity_thresholds(train: pd.DataFrame) -> dict[str, float]:
    """Train-only tercile / median cuts for depth, spread, vol."""
    depth = pd.to_numeric(train["displayed_depth"], errors="coerce").dropna()
    spread = pd.to_numeric(train["spread"], errors="coerce").dropna()
    vol = pd.to_numeric(train["realized_vol"], errors="coerce").dropna()
    return {
        "depth_lo": float(depth.quantile(0.33)) if len(depth) else 0.0,
        "depth_hi": float(depth.quantile(0.67)) if len(depth) else 0.0,
        "spread_med": float(spread.quantile(0.50)) if len(spread) else 0.0,
        "vol_med": float(vol.quantile(0.50)) if len(vol) else 0.0,
        "n_train": float(len(train)),
    }


def depth_bucket(depth: float | None, thr: dict[str, float]) -> str:
    if depth is None or not np.isfinite(depth):
        return "unknown"
    if depth <= thr["depth_lo"]:
        return "low_depth"
    if depth >= thr["depth_hi"]:
        return "high_depth"
    return "normal_depth"


def spread_bucket(spread: float | None, thr: dict[str, float]) -> str:
    if spread is None or not np.isfinite(spread):
        return "unknown"
    return "tight_spread" if spread <= thr["spread_med"] else "wide_spread"


def vol_bucket(vol: float | None, thr: dict[str, float]) -> str:
    if vol is None or not np.isfinite(vol):
        return "unknown"
    return "low_volatility" if vol <= thr["vol_med"] else "high_volatility"


def add_liquidity_buckets(df: pd.DataFrame, thr: dict[str, float]) -> pd.DataFrame:
    out = df.copy()
    out["depth_regime"] = [
        depth_bucket(v if pd.notna(v) else None, thr)
        for v in pd.to_numeric(out["displayed_depth"], errors="coerce")
    ]
    out["spread_regime"] = [
        spread_bucket(v if pd.notna(v) else None, thr)
        for v in pd.to_numeric(out["spread"], errors="coerce")
    ]
    out["vol_regime"] = [
        vol_bucket(v if pd.notna(v) else None, thr)
        for v in pd.to_numeric(out["realized_vol"], errors="coerce")
    ]
    return out


def stratified_feature_auc(df: pd.DataFrame, feature: str, regime_col: str) -> dict[str, Any]:
    from scripts.move_exhaustion.features.per_tick import univariate_logistic_auc

    out: dict[str, Any] = {}
    for regime, g in df.groupby(regime_col):
        res = univariate_logistic_auc(g, feature)
        labels = g[g["label"].isin([0, 1])]
        out[str(regime)] = {
            "n": int(len(g)),
            "n_resolved": int(len(labels)),
            "exhaustion_rate": float((labels["label"] == 1).mean()) if len(labels) else None,
            "feature_median": float(pd.to_numeric(g[feature], errors="coerce").median()),
            "auc": res.get("auc"),
            "coef": res.get("coef_standardized"),
            "ok": res.get("ok"),
        }
    return out


def interaction_product(df: pd.DataFrame, a: str, b: str, name: str) -> pd.Series:
    x = pd.to_numeric(df[a], errors="coerce")
    y = pd.to_numeric(df[b], errors="coerce")
    # Standardize before product to keep scale sane
    xs = (x - x.mean()) / (x.std(ddof=0) if x.std(ddof=0) and x.std(ddof=0) > 0 else 1.0)
    ys = (y - y.mean()) / (y.std(ddof=0) if y.std(ddof=0) and y.std(ddof=0) > 0 else 1.0)
    return (xs * ys).rename(name)
