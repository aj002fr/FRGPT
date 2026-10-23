"""Read-only loaders for V2 TBBO panel + ridge fair-value residuals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import (
    DV01_PATH,
    EPS,
    MIN_POINTS_FOR_Z,
    MIN_ROWS_PER_DAY,
    PRIOR_OBSERVATIONS,
    TBBO_MATRIX,
    V2_MODEL,
    WALKFORWARD_PREDS,
)


def load_dv01(path: Path = DV01_PATH) -> dict[str, float]:
    if not path.exists():
        return {"FV": 42.0, "TU": 36.0, "TY": 85.0, "US": 125.0}
    return {str(k): float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def load_tbbo_matrix(path: Path = TBBO_MATRIX) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    df.sort_values(["et_date", "bucket_ts"], inplace=True)
    return df


def load_walkforward_predictions(path: Path = WALKFORWARD_PREDS, model: str = V2_MODEL) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    return df[df["model"] == model].copy()


def add_causal_residual_z(df: pd.DataFrame) -> pd.DataFrame:
    """Prior-blend residual z within each et_date using only past days for history."""
    out = df.copy()
    parts: list[pd.DataFrame] = []
    historical_by_day: list[pd.Series] = []
    for et_date in sorted(out["et_date"].dropna().unique().tolist()):
        day = out[out["et_date"] == et_date].sort_values("bucket_ts").copy()
        resid = pd.to_numeric(day["residual"], errors="coerce")
        if historical_by_day:
            hist = pd.concat(historical_by_day).dropna()
            mu_hist = float(hist.mean()) if len(hist) else 0.0
            day_stds = [
                float(s.dropna().std(ddof=1))
                for s in historical_by_day
                if s.dropna().shape[0] >= 2
            ]
            day_stds = [v for v in day_stds if np.isfinite(v) and v > EPS]
            sigma_hist = float(np.median(day_stds)) if day_stds else float(hist.std(ddof=1) or 1.0)
        else:
            # Non-informative prior on the first sample day — never use full-day
            # residual std (that would leak future bars within the day).
            mu_hist = 0.0
            sigma_hist = 1.0
        if not np.isfinite(sigma_hist) or sigma_hist <= EPS:
            sigma_hist = 1.0
        expanding_mean = resid.expanding(min_periods=MIN_POINTS_FOR_Z).mean()
        expanding_std = resid.expanding(min_periods=MIN_POINTS_FOR_Z).std(ddof=1)
        count = resid.expanding(min_periods=1).count()
        weight = PRIOR_OBSERVATIONS / (PRIOR_OBSERVATIONS + count)
        today_mean = expanding_mean.where(expanding_mean.notna(), mu_hist)
        today_std = expanding_std.where(expanding_std.notna(), sigma_hist).clip(lower=EPS)
        blended_mean = weight * mu_hist + (1.0 - weight) * today_mean
        blended_var = weight * (sigma_hist**2) + (1.0 - weight) * (today_std**2)
        day["resid_mean"] = blended_mean
        day["resid_std"] = np.sqrt(blended_var).replace(0.0, np.nan)
        day["residual_z"] = (resid - day["resid_mean"]) / day["resid_std"]
        day["z_method"] = "prior_blend_v1"
        parts.append(day)
        historical_by_day.append(resid)
    return pd.concat(parts, ignore_index=True).sort_values(["et_date", "bucket_ts"])


def join_v2_panel(
    features: pd.DataFrame | None = None,
    preds: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Inner-join ridge walk-forward fair value onto TBBO features; add residual_z."""
    features = load_tbbo_matrix() if features is None else features.copy()
    preds = load_walkforward_predictions() if preds is None else preds.copy()

    by_day = features.groupby("et_date").agg(
        rows=("price", "count"),
        price_min=("price", "min"),
        price_max=("price", "max"),
        target_std=("target_price_delta", "std"),
    )
    clean_dates = by_day[
        (by_day["rows"] >= MIN_ROWS_PER_DAY)
        & (by_day["price_min"] > 80)
        & (by_day["price_max"] < 150)
        & (by_day["target_std"] > 0.01)
        & (by_day["target_std"] < 1.0)
    ].index.tolist()

    keep = [
        c
        for c in features.columns
        if c
        not in {
            "fair_value",
            "residual",
            "predicted_delta",
            "train_end_date",
            "residual_z",
        }
    ]
    merged = preds[
        ["bucket_ts", "et_date", "fair_value", "residual", "predicted_delta", "train_end_date"]
    ].merge(features[keep], on=["bucket_ts", "et_date"], how="inner")
    merged = merged[merged["et_date"].isin(clean_dates)].copy()
    merged.sort_values(["et_date", "bucket_ts"], inplace=True)
    merged.replace([np.inf, -np.inf], np.nan, inplace=True)
    merged = add_causal_residual_z(merged)
    meta = {
        "rows_features_raw": int(len(features)),
        "rows_predictions_raw": int(len(preds)),
        "rows_merged": int(len(merged)),
        "n_clean_dates": int(len(clean_dates)),
        "clean_dates": clean_dates,
        "excluded_dates": [d for d in by_day.index.tolist() if d not in clean_dates],
        "v2_model": V2_MODEL,
        "session_convention": "legacy_v2_01_et",
        "decision_grid": "1min",
    }
    return merged, meta
