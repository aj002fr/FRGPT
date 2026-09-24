#!/usr/bin/env python3
"""Conditional signal tests for TBBO-backed Ridge fair-value residuals."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models"
MODEL = "ridge"
HORIZONS = [5, 15, 30]


def _load_data(feature_path: Path, prediction_path: Path) -> pd.DataFrame:
    features = pd.read_csv(feature_path, parse_dates=["bucket_ts"])
    preds = pd.read_csv(prediction_path, parse_dates=["bucket_ts"])
    features["bucket_ts"] = pd.to_datetime(features["bucket_ts"], utc=True)
    preds["bucket_ts"] = pd.to_datetime(preds["bucket_ts"], utc=True)
    ridge = preds[preds["model"] == MODEL].copy()
    keep = [
        "bucket_ts",
        "et_date",
        "agg_1m",
        "agg_5m",
        "agg_30m",
        "agg_rate",
        "agg_accel",
        "buysell_ratio",
        "displayed_depth_mean",
        "spread_mean",
        "size_imbalance_mean",
        "trade_size_vs_depth",
        "depth_adjusted_signed_flow",
        "realized_vol",
        "mom_5m",
    ]
    merged = ridge.merge(features[keep], on=["bucket_ts", "et_date"], how="left", suffixes=("", "_feature"))
    merged.sort_values(["et_date", "bucket_ts"], inplace=True)
    return merged


def _add_forward_changes(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for _, day in df.groupby("et_date"):
        d = day.sort_values("bucket_ts").copy()
        for horizon in HORIZONS:
            d[f"future_change_{horizon}m"] = d["price"].shift(-horizon) - d["price"]
        out.append(d)
    return pd.concat(out, ignore_index=True)


def _zscore_by_day(df: pd.DataFrame, column: str) -> pd.Series:
    return df.groupby("et_date")[column].transform(
        lambda x: (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) else np.nan)
    )


def _add_conditions(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["residual_z_day"] = _zscore_by_day(out, "residual")
    out["abs_residual_z"] = out["residual_z_day"].abs()
    out["abs_agg_5m_z"] = _zscore_by_day(out, "agg_5m").abs()
    out["abs_future_move_5m"] = out.groupby("et_date")["price"].diff(5).abs()
    out["absorption"] = (
        (out["abs_agg_5m_z"] >= 1.0)
        & (out["abs_future_move_5m"] <= out.groupby("et_date")["abs_future_move_5m"].transform("median"))
    )
    out["flow_decelerating"] = np.sign(out["agg_rate"]) != np.sign(out["agg_accel"])
    out["low_depth"] = out["displayed_depth_mean"] <= out["displayed_depth_mean"].quantile(0.33)
    out["high_depth"] = out["displayed_depth_mean"] >= out["displayed_depth_mean"].quantile(0.67)
    out["wide_spread"] = out["spread_mean"] >= out["spread_mean"].quantile(0.67)
    out["large_residual_1_0"] = out["abs_residual_z"] >= 1.0
    out["large_residual_1_5"] = out["abs_residual_z"] >= 1.5
    out["large_residual_2_0"] = out["abs_residual_z"] >= 2.0
    out["residual_with_absorption"] = out["large_residual_1_0"] & out["absorption"]
    out["residual_with_flow_deceleration"] = out["large_residual_1_0"] & out["flow_decelerating"]
    out["residual_high_depth"] = out["large_residual_1_0"] & out["high_depth"]
    out["residual_low_depth"] = out["large_residual_1_0"] & out["low_depth"]
    out["residual_absorption_deceleration"] = out["large_residual_1_0"] & out["absorption"] & out["flow_decelerating"]
    return out


def _condition_stats(df: pd.DataFrame, condition: str, horizon: int) -> Dict[str, Any]:
    subset = df[df[condition]].dropna(subset=["residual", f"future_change_{horizon}m"])
    if subset.empty:
        return {"condition": condition, "horizon": horizon, "n": 0}
    future = subset[f"future_change_{horizon}m"]
    residual = subset["residual"]
    direction = np.sign(-residual)
    realized = np.sign(future)
    hit_mask = realized != 0
    hit_rate = float((direction[hit_mask] == realized[hit_mask]).mean()) if hit_mask.any() else np.nan
    conditional_mean = float((direction * future).mean())
    unconditional = df.dropna(subset=[f"future_change_{horizon}m"])
    unconditional_mean = float((np.sign(-unconditional["residual"]) * unconditional[f"future_change_{horizon}m"]).mean())
    return {
        "condition": condition,
        "horizon": horizon,
        "n": int(len(subset)),
        "coverage": float(len(subset) / len(df)),
        "corr_residual_future_change": float(residual.corr(future)) if residual.nunique() > 1 else np.nan,
        "mean_reversion_hit_rate": hit_rate,
        "mean_directional_pnl_points": conditional_mean,
        "unconditional_mean_directional_pnl_points": unconditional_mean,
        "avg_abs_residual": float(residual.abs().mean()),
        "avg_future_abs_move": float(future.abs().mean()),
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = _load_data(Path(args.features), Path(args.predictions))
    df = _add_forward_changes(df)
    df = _add_conditions(df)
    conditions = [
        "large_residual_1_0",
        "large_residual_1_5",
        "large_residual_2_0",
        "absorption",
        "flow_decelerating",
        "high_depth",
        "low_depth",
        "wide_spread",
        "residual_with_absorption",
        "residual_with_flow_deceleration",
        "residual_high_depth",
        "residual_low_depth",
        "residual_absorption_deceleration",
    ]
    rows: List[Dict[str, Any]] = []
    for condition in conditions:
        for horizon in HORIZONS:
            rows.append(_condition_stats(df, condition, horizon))
    stats = pd.DataFrame(rows)
    stats_path = output_dir / "conditional_signal_tests.csv"
    data_path = output_dir / "conditional_signal_dataset.csv"
    summary_path = output_dir / "conditional_signal_summary.json"
    stats.to_csv(stats_path, index=False)
    df.to_csv(data_path, index=False)
    filtered = stats[stats["n"] >= int(args.min_n)].copy()
    ranked = filtered.sort_values(["mean_directional_pnl_points", "mean_reversion_hit_rate"], ascending=False)
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "rows": int(len(df)),
        "dates": sorted(df["et_date"].unique().tolist()),
        "horizons": HORIZONS,
        "min_n": int(args.min_n),
        "best_conditions": ranked.head(20).to_dict(orient="records"),
        "all_conditions": stats.to_dict(orient="records"),
        "artifacts": {
            "conditional_stats": str(stats_path),
            "conditional_dataset": str(data_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Directional PnL is sign(-residual) * future price change in decimal price points.",
            "Positive directional PnL means residual mean reversion worked on average.",
            "Conditions are research diagnostics only; no transaction costs/slippage included.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Conditional tests for V2 Ridge residuals.")
    parser.add_argument("--features", default=str(FEATURE_MATRIX))
    parser.add_argument("--predictions", default=str(PREDICTIONS))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--min-n", type=int, default=100)
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({
        "ok": summary["ok"],
        "rows": summary["rows"],
        "dates": summary["dates"],
        "best_conditions": summary["best_conditions"][:10],
        "artifacts": summary["artifacts"],
    }, indent=2))


if __name__ == "__main__":
    main()
