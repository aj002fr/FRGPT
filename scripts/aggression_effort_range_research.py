#!/usr/bin/env python3
"""Offline holdout research for V3 flow-effort exhaustion/breakout labels."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_flow_effort import (  # noqa: E402
    TICK_SIZE,
    FlowEffortConfig,
    annotate_flow_effort,
)


FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_effort_range"
MODEL = "ridge"
HORIZONS = (15, 30)


def _load(feature_path: Path, prediction_path: Path, model: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    features = pd.read_csv(feature_path, parse_dates=["bucket_ts"])
    predictions = pd.read_csv(prediction_path, parse_dates=["bucket_ts"])
    features["bucket_ts"] = pd.to_datetime(features["bucket_ts"], utc=True)
    predictions["bucket_ts"] = pd.to_datetime(predictions["bucket_ts"], utc=True)
    features = features.set_index("bucket_ts").sort_index()
    predictions = predictions[predictions["model"] == model].set_index("bucket_ts").sort_index()
    return features, predictions


def _add_forward_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        for horizon in HORIZONS:
            future_price = d["price"].shift(-horizon)
            d[f"future_change_{horizon}m"] = future_price - d["price"]
            direction = np.sign(d["break_direction"].replace(0, np.nan))
            d[f"breakout_hit_{horizon}m"] = (
                (direction * d[f"future_change_{horizon}m"] >= 4.0 * TICK_SIZE)
                & (~d["false_break"].fillna(False))
            )
            range_mid = (d["frozen_range_high"].fillna(d["range_high"]) + d["frozen_range_low"].fillna(d["range_low"])) / 2.0
            price_to_mid_now = (d["price"] - range_mid).abs()
            price_to_mid_future = (future_price - range_mid).abs()
            residual_resolved = d["residual_z"].abs().shift(-horizon) < 1.0 if "residual_z" in d else False
            d[f"exhaustion_hit_{horizon}m"] = (price_to_mid_future < price_to_mid_now) | residual_resolved
        parts.append(d)
    return pd.concat(parts).sort_index()


def _label_rows(df: pd.DataFrame, config: FlowEffortConfig) -> pd.DataFrame:
    out = df.copy()
    abs_z = out.get("residual_z", pd.Series(np.nan, index=out.index)).abs()
    exhaustion_c = out["in_range"] & out["high_effort"] & out["gap_widening"]
    exhaustion_b = exhaustion_c & (out["absorption"] | out["flow_decelerating"])
    exhaustion_a = exhaustion_b & (abs_z >= 1.5)
    exhaustion_aplus = exhaustion_a & out["cross_tenor_disagrees"] & out["range_volume_high"]
    breakout_c = (out["range_break"] | out["post_break_active"]) & (out["effort_ratio"] < config.effort_ratio_breakout)
    breakout_b = breakout_c & out["cross_tenor_aligned"]
    breakout_a = breakout_b & (~out["wide_spread"].fillna(False))
    out["rule_exhaustion_c"] = exhaustion_c
    out["rule_exhaustion_b"] = exhaustion_b
    out["rule_exhaustion_a"] = exhaustion_a
    out["rule_exhaustion_aplus"] = exhaustion_aplus
    out["rule_breakout_c"] = breakout_c
    out["rule_breakout_b"] = breakout_b
    out["rule_breakout_a"] = breakout_a
    out["rule_false_break"] = (out["range_break"] | out["post_break_active"]) & (out["effort_ratio"] >= 0.8)
    out["rule_naive_range_edge"] = out["range_break"].fillna(False)
    out["rule_v2_z2"] = abs_z >= 2.0
    return out


def _chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[set[str], set[str]]:
    ordered = sorted(dates)
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac))) if len(ordered) > 1 else len(ordered)
    return set(ordered[:cut]), set(ordered[cut:])


def _rule_stats(df: pd.DataFrame, rule: str, label_type: str, horizon: int) -> dict[str, Any]:
    subset = df[df[rule].fillna(False)].dropna(subset=["price"])
    if subset.empty:
        return {"rule": rule, "label_type": label_type, "horizon": horizon, "n": 0}
    if label_type == "breakout":
        hit_col = f"breakout_hit_{horizon}m"
        pnl_col = f"future_change_{horizon}m"
        direction = np.sign(subset["break_direction"].replace(0, np.nan))
        directional = direction * subset[pnl_col]
    elif label_type == "false_break":
        hit_col = "false_break"
        directional = pd.Series(np.nan, index=subset.index)
    else:
        hit_col = f"exhaustion_hit_{horizon}m"
        range_mid = (subset["frozen_range_high"].fillna(subset["range_high"]) + subset["frozen_range_low"].fillna(subset["range_low"])) / 2.0
        direction = np.sign(range_mid - subset["price"])
        directional = direction * subset[f"future_change_{horizon}m"]
    hits = subset[hit_col].fillna(False)
    return {
        "rule": rule,
        "label_type": label_type,
        "horizon": int(horizon),
        "n": int(len(subset)),
        "hit_rate": float(hits.mean()) if len(subset) else np.nan,
        "median_directional_move": float(directional.median()) if directional.notna().any() else np.nan,
        "avg_directional_move": float(directional.mean()) if directional.notna().any() else np.nan,
        "median_effort_ratio": float(subset["effort_ratio"].median()) if subset["effort_ratio"].notna().any() else np.nan,
        "median_effort_per_tick": float(subset["effort_per_tick"].median()) if subset["effort_per_tick"].notna().any() else np.nan,
    }


def _evaluate_config(features: pd.DataFrame, predictions: pd.DataFrame, config: FlowEffortConfig) -> dict[str, Any]:
    annotated = annotate_flow_effort(features, predictions, config)
    if annotated.empty:
        return {"ok": False, "reason": "no annotated rows"}
    if "et_date" not in annotated:
        annotated["et_date"] = annotated.index.tz_convert("America/New_York").strftime("%Y-%m-%d")
    labeled = _label_rows(_add_forward_outcomes(annotated), config)
    _, holdout_dates = _chronological_split(sorted(labeled["et_date"].dropna().unique().tolist()))
    holdout = labeled[labeled["et_date"].isin(holdout_dates)].copy()
    rules: list[tuple[str, str]] = [
        ("rule_exhaustion_c", "exhaustion"),
        ("rule_exhaustion_b", "exhaustion"),
        ("rule_exhaustion_a", "exhaustion"),
        ("rule_exhaustion_aplus", "exhaustion"),
        ("rule_breakout_c", "breakout"),
        ("rule_breakout_b", "breakout"),
        ("rule_breakout_a", "breakout"),
        ("rule_false_break", "false_break"),
        ("rule_naive_range_edge", "breakout"),
        ("rule_v2_z2", "exhaustion"),
    ]
    rows = []
    for rule, label_type in rules:
        for horizon in HORIZONS:
            rows.append(_rule_stats(holdout, rule, label_type, horizon))
    stats = pd.DataFrame(rows)
    eligible = stats[stats["n"] >= 30].copy()
    best = eligible.sort_values(["hit_rate", "n"], ascending=False).head(10).to_dict(orient="records")
    return {
        "ok": True,
        "config": config.__dict__,
        "rows": int(len(labeled)),
        "dates": sorted(labeled["et_date"].dropna().unique().tolist()),
        "holdout_dates": sorted(holdout_dates),
        "stats": stats,
        "best": best,
        "annotated": labeled,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    features, predictions = _load(Path(args.features), Path(args.predictions), args.model)
    configs = [
        FlowEffortConfig(range_window=rw, range_max_ticks=rt, effort_ratio_breakout=er)
        for rw in args.range_windows
        for rt in args.range_ticks
        for er in args.effort_ratios
    ]
    all_stats = []
    summaries = []
    first_annotated: pd.DataFrame | None = None
    for config in configs:
        result = _evaluate_config(features, predictions, config)
        if not result.get("ok"):
            summaries.append({"ok": False, "config": config.__dict__, "reason": result.get("reason")})
            continue
        stats = result["stats"].copy()
        for key, value in config.__dict__.items():
            stats[key] = value
        all_stats.append(stats)
        summaries.append({
            "ok": True,
            "config": config.__dict__,
            "rows": result["rows"],
            "holdout_dates": result["holdout_dates"],
            "best": result["best"],
        })
        if first_annotated is None:
            first_annotated = result["annotated"]

    stats_df = pd.concat(all_stats, ignore_index=True) if all_stats else pd.DataFrame()
    stats_path = output_dir / "effort_range_rule_stats.csv"
    summary_path = output_dir / "effort_range_summary.json"
    dataset_path = output_dir / "effort_range_annotated_sample.csv"
    stats_df.to_csv(stats_path, index=False)
    if first_annotated is not None:
        first_annotated.tail(20000).to_csv(dataset_path, index=True)

    eligible = stats_df[stats_df["n"] >= args.min_n].copy() if not stats_df.empty else pd.DataFrame()
    global_best = eligible.sort_values(["hit_rate", "n"], ascending=False).head(20).to_dict(orient="records") if not eligible.empty else []
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "min_n": int(args.min_n),
        "configs_tested": len(configs),
        "global_best": global_best,
        "config_summaries": summaries,
        "artifacts": {
            "stats": str(stats_path),
            "summary": str(summary_path),
            "annotated_sample": str(dataset_path),
        },
        "notes": [
            "Rules are research-only and use chronological holdout by et_date.",
            "Live V3 should remain display-only until these results are reviewed.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest V3 flow-effort exhaustion/breakout rules.")
    parser.add_argument("--features", default=str(FEATURE_MATRIX))
    parser.add_argument("--predictions", default=str(PREDICTIONS))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--min-n", type=int, default=30)
    parser.add_argument("--range-windows", type=int, nargs="+", default=[30])
    parser.add_argument("--range-ticks", type=float, nargs="+", default=[4.0])
    parser.add_argument("--effort-ratios", type=float, nargs="+", default=[0.6])
    parser.add_argument("--full-sweep", action="store_true", help="Run the slower 3x3x4 parameter sweep.")
    args = parser.parse_args()
    if args.full_sweep:
        args.range_windows = [15, 30, 60]
        args.range_ticks = [3.0, 4.0, 6.0]
        args.effort_ratios = [0.4, 0.5, 0.6, 0.8]
    summary = run(args)
    print(json.dumps({
        "ok": summary["ok"],
        "configs_tested": summary["configs_tested"],
        "top": summary["global_best"][:5],
        "artifacts": summary["artifacts"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()

