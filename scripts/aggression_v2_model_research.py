#!/usr/bin/env python3
"""Walk-forward model research for TBBO-backed aggression V2 features."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LassoCV, LinearRegression, RidgeCV
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


INPUT_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models"
MIN_ROWS_PER_DAY = 500
MIN_TRAIN_DAYS = 5
TARGET_COL = "target_price_delta"
PRICE_COL = "price"
DATE_COL = "et_date"
FEATURE_SET = [
    "agg_cum_daily",
    "agg_5m",
    "agg_30m",
    "buysell_ratio",
    "trade_intensity",
    "avg_trade_size",
    "large_trade_share",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "US_agg_5m",
    "spread_mean",
    "size_imbalance_mean",
    "displayed_depth_mean",
    "trade_size_vs_depth",
    "depth_adjusted_signed_flow",
    "minutes_since_open",
]


def _load_matrix(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    df.sort_values("bucket_ts", inplace=True)
    return df


def _quality_filter(df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, Any]]:
    by_day = df.groupby(DATE_COL).agg(
        rows=(PRICE_COL, "count"),
        price_min=(PRICE_COL, "min"),
        price_max=(PRICE_COL, "max"),
        target_std=(TARGET_COL, "std"),
    )
    clean_dates = by_day[
        (by_day["rows"] >= MIN_ROWS_PER_DAY)
        & (by_day["price_min"] > 80)
        & (by_day["price_max"] < 150)
        & (by_day["target_std"] > 0.01)
        & (by_day["target_std"] < 1.0)
    ].index.tolist()
    out = df[df[DATE_COL].isin(clean_dates)].copy()
    out = out.replace([np.inf, -np.inf], np.nan)
    required = [TARGET_COL, PRICE_COL, DATE_COL, "bucket_ts", *FEATURE_SET]
    out = out.dropna(subset=required).copy()
    return out, {
        "day_quality": by_day.reset_index().to_dict(orient="records"),
        "clean_dates": clean_dates,
        "excluded_dates": [d for d in by_day.index.tolist() if d not in clean_dates],
        "rows_after_quality_filter": int(len(out)),
    }


def _models() -> Dict[str, Any]:
    return {
        "ols_baseline": Pipeline([
            ("scale", StandardScaler()),
            ("model", LinearRegression()),
        ]),
        "ridge": Pipeline([
            ("scale", StandardScaler()),
            ("model", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0])),
        ]),
        "lasso": Pipeline([
            ("scale", StandardScaler()),
            ("model", LassoCV(alphas=[0.0001, 0.001, 0.01, 0.1, 1.0], cv=3, max_iter=20000, random_state=7)),
        ]),
        "random_forest": RandomForestRegressor(
            n_estimators=150,
            max_depth=5,
            min_samples_leaf=25,
            random_state=7,
            n_jobs=-1,
        ),
    }


def _selected_features(model_name: str) -> List[str]:
    if model_name == "ols_baseline":
        return ["agg_cum_daily"]
    return FEATURE_SET


def _coef_rows(model_name: str, estimator: Any, features: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(estimator, Pipeline):
        model = estimator.named_steps["model"]
        if hasattr(model, "coef_"):
            for feature, coef in zip(features, model.coef_):
                rows.append({"model": model_name, "feature": feature, "value": float(coef), "kind": "standardized_coef"})
        if hasattr(model, "alpha_"):
            rows.append({"model": model_name, "feature": "__alpha__", "value": float(model.alpha_), "kind": "hyperparameter"})
    elif hasattr(estimator, "feature_importances_"):
        for feature, value in zip(features, estimator.feature_importances_):
            rows.append({"model": model_name, "feature": feature, "value": float(value), "kind": "rf_importance"})
    return rows


def _residual_signal_stats(pred: pd.DataFrame, horizons: List[int]) -> Dict[str, Any]:
    stats: Dict[str, Any] = {}
    for horizon in horizons:
        rows = []
        for _, day in pred.groupby(DATE_COL):
            d = day.sort_values("bucket_ts").copy()
            d["future_change"] = d[PRICE_COL].shift(-horizon) - d[PRICE_COL]
            rows.append(d)
        data = pd.concat(rows).dropna(subset=["residual", "future_change"])
        if data.empty:
            stats[f"{horizon}m"] = {"n": 0}
            continue
        corr = float(data["residual"].corr(data["future_change"]))
        hit_rate = float((np.sign(-data["residual"]) == np.sign(data["future_change"])).mean())
        slope = float(np.polyfit(data["residual"], data["future_change"], deg=1)[0]) if data["residual"].nunique() > 1 else np.nan
        stats[f"{horizon}m"] = {
            "n": int(len(data)),
            "corr_residual_future_change": corr,
            "mean_reversion_hit_rate": hit_rate,
            "slope_future_change_vs_residual": slope,
        }
    return stats


def run_research(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = _load_matrix(Path(args.input))
    clean, quality = _quality_filter(df)
    dates = sorted(clean[DATE_COL].unique().tolist())
    model_defs = _models()
    predictions: List[pd.DataFrame] = []
    coef_rows: List[Dict[str, Any]] = []
    train_metrics: List[Dict[str, Any]] = []

    for test_idx in range(MIN_TRAIN_DAYS, len(dates)):
        train_dates = dates[:test_idx]
        test_date = dates[test_idx]
        train = clean[clean[DATE_COL].isin(train_dates)]
        test = clean[clean[DATE_COL] == test_date]
        for model_name, estimator in model_defs.items():
            features = _selected_features(model_name)
            train_x = train[features].astype(float)
            train_y = train[TARGET_COL].astype(float)
            test_x = test[features].astype(float)
            estimator.fit(train_x, train_y)
            train_pred = estimator.predict(train_x)
            pred_delta = estimator.predict(test_x)
            pred = test[["bucket_ts", DATE_COL, PRICE_COL, "day_open_price", TARGET_COL]].copy()
            pred["model"] = model_name
            pred["predicted_delta"] = pred_delta
            pred["fair_value"] = pred["day_open_price"] + pred["predicted_delta"]
            pred["residual"] = pred[PRICE_COL] - pred["fair_value"]
            pred["train_end_date"] = train_dates[-1]
            predictions.append(pred)
            train_metrics.append({
                "model": model_name,
                "test_date": test_date,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "train_r2": float(r2_score(train_y, train_pred)),
                "train_rmse": float(mean_squared_error(train_y, train_pred) ** 0.5),
            })
            coef_rows.extend(_coef_rows(model_name, estimator, features))

    pred_df = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    metrics = []
    for model_name, group in pred_df.groupby("model"):
        y = group[TARGET_COL]
        yhat = group["predicted_delta"]
        resid = group["residual"]
        metrics.append({
            "model": model_name,
            "oos_rows": int(len(group)),
            "oos_dates": sorted(group[DATE_COL].unique().tolist()),
            "oos_r2": float(r2_score(y, yhat)),
            "oos_rmse": float(mean_squared_error(y, yhat) ** 0.5),
            "residual_mean": float(resid.mean()),
            "residual_std": float(resid.std(ddof=1)),
            "signal": _residual_signal_stats(group, [1, 5, 15]),
        })

    metrics_df = pd.DataFrame(metrics).sort_values("oos_rmse") if metrics else pd.DataFrame()
    coef_df = pd.DataFrame(coef_rows)
    train_df = pd.DataFrame(train_metrics)
    pred_path = output_dir / "walkforward_predictions.csv"
    metrics_path = output_dir / "model_metrics.json"
    coef_path = output_dir / "model_coefficients_importance.csv"
    train_path = output_dir / "train_fold_metrics.csv"
    pred_df.to_csv(pred_path, index=False)
    coef_df.to_csv(coef_path, index=False)
    train_df.to_csv(train_path, index=False)
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "rows_raw": int(len(df)),
        "rows_clean": int(len(clean)),
        "clean_dates": dates,
        "quality": quality,
        "features": FEATURE_SET,
        "model_metrics": metrics_df.to_dict(orient="records"),
        "artifacts": {
            "predictions": str(pred_path),
            "metrics": str(metrics_path),
            "coefficients_importance": str(coef_path),
            "train_fold_metrics": str(train_path),
        },
        "assumptions": {
            "target": "price - day_open_price",
            "validation": "walk-forward by ET date",
            "ols_baseline_features": ["agg_cum_daily"],
            "rf_role": "benchmark only, not primary signal",
        },
    }
    metrics_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TBBO-backed aggression V2 model research.")
    parser.add_argument("--input", default=str(INPUT_MATRIX))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    summary = run_research(args)
    print(json.dumps({
        "ok": summary["ok"],
        "rows_clean": summary["rows_clean"],
        "clean_dates": summary["clean_dates"],
        "model_metrics": summary["model_metrics"],
        "artifacts": summary["artifacts"],
    }, indent=2))


if __name__ == "__main__":
    main()
