#!/usr/bin/env python3
"""Holdout validation for the TBBO-backed Ridge residual signal."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
FEATURES = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models"
MODEL = "ridge"
HORIZONS = [15, 30]
THRESHOLDS = [1.0, 1.5, 2.0]


def _load() -> pd.DataFrame:
    pred = pd.read_csv(PREDICTIONS, parse_dates=["bucket_ts"])
    feat = pd.read_csv(FEATURES, parse_dates=["bucket_ts"])
    pred["bucket_ts"] = pd.to_datetime(pred["bucket_ts"], utc=True)
    feat["bucket_ts"] = pd.to_datetime(feat["bucket_ts"], utc=True)
    data = pred[pred["model"] == MODEL].merge(
        feat[["bucket_ts", "et_date", "agg_rate", "agg_accel", "displayed_depth_mean", "agg_5m", "mom_5m"]],
        on=["bucket_ts", "et_date"],
        how="left",
    )
    data.sort_values(["et_date", "bucket_ts"], inplace=True)
    return data


def _add_forward_changes(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date"):
        d = day.sort_values("bucket_ts").copy()
        for horizon in HORIZONS:
            d[f"future_change_{horizon}m"] = d["price"].shift(-horizon) - d["price"]
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def _add_daily_residual_z(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["residual_z_day"] = out.groupby("et_date")["residual"].transform(
        lambda x: (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) else np.nan)
    )
    out["abs_residual_z"] = out["residual_z_day"].abs()
    out["signal_direction"] = -np.sign(out["residual"])
    out["flow_decelerating"] = np.sign(out["agg_rate"]) != np.sign(out["agg_accel"])
    return out


def _eval_rule(df: pd.DataFrame, threshold: float, horizon: int, condition: str | None = None) -> Dict[str, Any]:
    mask = df["abs_residual_z"] >= threshold
    if condition == "flow_decelerating":
        mask &= df["flow_decelerating"].fillna(False)
    subset = df[mask].dropna(subset=[f"future_change_{horizon}m", "residual", "signal_direction"])
    if subset.empty:
        return {
            "threshold": threshold,
            "horizon": horizon,
            "condition": condition or "large_residual",
            "n": 0,
        }
    future = subset[f"future_change_{horizon}m"]
    direction = subset["signal_direction"]
    realized = np.sign(future)
    nonzero = realized != 0
    hit_rate = float((direction[nonzero] == realized[nonzero]).mean()) if nonzero.any() else np.nan
    directional = direction * future
    return {
        "threshold": threshold,
        "horizon": horizon,
        "condition": condition or "large_residual",
        "n": int(len(subset)),
        "coverage": float(len(subset) / len(df)),
        "hit_rate": hit_rate,
        "avg_directional_pnl_points": float(directional.mean()),
        "median_directional_pnl_points": float(directional.median()),
        "p25_directional_pnl_points": float(directional.quantile(0.25)),
        "p75_directional_pnl_points": float(directional.quantile(0.75)),
        "corr_residual_future_change": float(subset["residual"].corr(future)) if subset["residual"].nunique() > 1 else np.nan,
        "avg_abs_residual": float(subset["residual"].abs().mean()),
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = _add_daily_residual_z(_add_forward_changes(_load()))
    dates = sorted(df["et_date"].unique().tolist())
    split_idx = max(1, int(len(dates) * float(args.train_frac)))
    select_dates = dates[:split_idx]
    holdout_dates = dates[split_idx:]
    select = df[df["et_date"].isin(select_dates)].copy()
    holdout = df[df["et_date"].isin(holdout_dates)].copy()

    rows: List[Dict[str, Any]] = []
    for sample_name, sample in [("selection", select), ("holdout", holdout)]:
        for condition in [None, "flow_decelerating"]:
            for threshold in THRESHOLDS:
                for horizon in HORIZONS:
                    item = _eval_rule(sample, threshold, horizon, condition)
                    item["sample"] = sample_name
                    rows.append(item)

    stats = pd.DataFrame(rows)
    holdout_stats = stats[(stats["sample"] == "holdout") & (stats["n"] >= int(args.min_n))].copy()
    best = holdout_stats.sort_values(["avg_directional_pnl_points", "hit_rate"], ascending=False)
    stats_path = output_dir / "holdout_signal_tests.csv"
    dataset_path = output_dir / "holdout_signal_dataset.csv"
    summary_path = output_dir / "holdout_signal_summary.json"
    stats.to_csv(stats_path, index=False)
    df.to_csv(dataset_path, index=False)
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "dates": dates,
        "selection_dates": select_dates,
        "holdout_dates": holdout_dates,
        "train_frac": float(args.train_frac),
        "thresholds": THRESHOLDS,
        "horizons": HORIZONS,
        "best_holdout_rules": best.head(20).to_dict(orient="records"),
        "all_rules": stats.to_dict(orient="records"),
        "artifacts": {
            "stats": str(stats_path),
            "dataset": str(dataset_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Thresholds were pre-specified from prior conditional research: 1.0, 1.5, 2.0 residual z-score.",
            "Holdout is later ET dates only; no random split.",
            "No transaction costs, slippage, or execution delay are included.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Holdout-test Ridge residual signal thresholds.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--train-frac", type=float, default=0.6)
    parser.add_argument("--min-n", type=int, default=30)
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({
        "ok": summary["ok"],
        "selection_dates": summary["selection_dates"],
        "holdout_dates": summary["holdout_dates"],
        "best_holdout_rules": summary["best_holdout_rules"][:10],
        "artifacts": summary["artifacts"],
    }, indent=2))


if __name__ == "__main__":
    main()
