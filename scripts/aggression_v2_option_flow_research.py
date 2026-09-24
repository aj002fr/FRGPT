#!/usr/bin/env python3
"""Staged, read-only TY option-flow research runner.

Runs alpha experiments only if the Phase-0 audit and minimum date coverage
pass. Otherwise emits explicit gated artifacts without inspecting a fake pilot.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.indexers import FixedForwardWindowIndexer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.aggression_gate_lib import benjamini_hochberg, bootstrap_ci, one_sided_p  # noqa: E402
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_option_flow"
AUDIT_PATH = OUTPUT_DIR / "data_audit.json"
FEATURE_PATH = OUTPUT_DIR / "option_flow_1m.csv"
TBBO_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
OPTIONS_CONTEXT = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_options_context" / "v2_options_minute_joined.csv"
EXPERIMENT_NAMES = (
    "experiment_01_direct_flow.csv",
    "experiment_02_flow_vs_skew.csv",
    "experiment_03_flow_futures_fv.csv",
    "experiment_04_large_sweep.csv",
    "experiment_05_event_persistence.csv",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(float(value)) else float(value)
    return str(value)


def _empty_outputs(output_dir: Path, reason: str) -> dict[str, str]:
    artifacts = {}
    columns = ["status", "reason", "experiment"]
    for name in EXPERIMENT_NAMES:
        path = output_dir / name
        pd.DataFrame([{"status": "GATED", "reason": reason, "experiment": name}])[columns].to_csv(path, index=False)
        artifacts[name] = str(path)
    leaderboard = output_dir / "candidate_leaderboard.csv"
    final = output_dir / "locked_final_results.csv"
    pd.DataFrame(columns=["candidate", "status", "reason"]).to_csv(leaderboard, index=False)
    pd.DataFrame(columns=["candidate", "status", "reason"]).to_csv(final, index=False)
    artifacts["leaderboard"] = str(leaderboard)
    artifacts["final"] = str(final)
    return artifacts


def _full_panel(features: pd.DataFrame) -> pd.DataFrame:
    matrix = pd.read_csv(
        TBBO_MATRIX,
        usecols=["bucket_ts", "price", "agg_5m", "agg_30m", "realized_vol"],
    )
    matrix["ts"] = pd.to_datetime(matrix["bucket_ts"], errors="coerce", utc=True)
    predictions = pd.read_csv(PREDICTIONS, parse_dates=["bucket_ts"])
    predictions = predictions[predictions["model"] == "ridge"][
        ["bucket_ts", "fair_value", "residual"]
    ].drop_duplicates("bucket_ts", keep="last")
    predictions["ts"] = pd.to_datetime(predictions["bucket_ts"], utc=True)
    panel = matrix.merge(predictions[["ts", "fair_value", "residual"]], on="ts", how="left")
    panel["residual_z"] = panel.groupby(panel["ts"].dt.date)["residual"].transform(
        lambda values: (values - values.expanding(min_periods=30).mean())
        / values.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
    )
    feature_dates = set(features["ts"].dt.date.astype(str))
    panel = panel[panel["ts"].dt.date.astype(str).isin(feature_dates)].copy()
    panel = panel.merge(features, on="ts", how="left", suffixes=("", "_feature"))
    flow_columns = [
        column for column in features.columns
        if column != "ts" and column not in {"price", "fair_value", "residual", "residual_z"}
    ]
    for column in flow_columns:
        if pd.api.types.is_numeric_dtype(features[column]):
            panel[column] = pd.to_numeric(panel[column], errors="coerce").fillna(0.0)
    return panel.sort_values("ts")


def _forward_moves(frame: pd.DataFrame) -> pd.DataFrame:
    parts = []
    frame["et_date"] = frame["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    for _, day in frame.groupby("et_date", sort=False):
        d = day.sort_values("ts").copy()
        for horizon in (5, 15, 30, 60):
            d[f"future_ticks_{horizon}m"] = (d["price"].shift(-horizon) - d["price"]) / (1.0 / 64.0)
            indexer = FixedForwardWindowIndexer(window_size=horizon + 1)
            future_max = d["price"].rolling(indexer, min_periods=horizon + 1).max()
            future_min = d["price"].rolling(indexer, min_periods=horizon + 1).min()
            d[f"future_range_{horizon}m"] = (future_max - future_min) / (1.0 / 64.0)
            d[f"future_abs_{horizon}m"] = d[f"future_ticks_{horizon}m"].abs()
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def _split_dates(data: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    coverage = data.groupby("et_date").agg(rows=("price", "count"), flow_rows=("trade_count", lambda x: int((x > 0).sum())))
    dates = sorted(coverage[(coverage["rows"] >= 300) & (coverage["flow_rows"] >= 30)].index.tolist())
    final_n = max(2, int(np.ceil(len(dates) * 0.20)))
    research = dates[:-final_n]
    validation_n = max(2, int(np.ceil(len(research) * 0.30)))
    return research[:-validation_n], research[-validation_n:], dates[-final_n:]


def _stats(block: pd.DataFrame, values: pd.Series, **keys: Any) -> dict[str, Any]:
    data = pd.DataFrame({"et_date": block["et_date"], "value": values}).dropna()
    if data.empty:
        return {**keys, "n": 0}
    ci = bootstrap_ci(data["value"], groups=data["et_date"])
    daily = data.groupby("et_date")["value"].mean()
    return {
        **keys,
        "n": int(len(data)),
        "cluster_n": int(data["et_date"].nunique()),
        "mean": float(data["value"].mean()),
        "hit_rate": float((data["value"] > 0).mean()),
        "ci_low": ci["ci_low"],
        "ci_high": ci["ci_high"],
        "one_sided_p": one_sided_p(daily),
    }


def _apply_fdr(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    for column in ("one_sided_p", "cluster_n", "ci_low", "ci_high", "mean", "hit_rate"):
        if column not in frame:
            frame[column] = np.nan
    bh = benjamini_hochberg(frame["one_sided_p"], q=0.10)
    frame["bh_adjusted_p"] = bh["adjusted_p"]
    frame["bh_significant"] = bh["reject"]
    frame["pass"] = (
        (frame["n"] >= 30)
        & (frame["cluster_n"] >= 8)
        & (frame["ci_low"] > 0)
        & frame["bh_significant"]
    )
    return frame


def _pilot_experiment(features: pd.DataFrame, output_dir: Path) -> dict[str, str]:
    data = _forward_moves(_full_panel(features.copy()))
    data.to_csv(output_dir / "option_flow_joined_panel.csv", index=False)
    discovery, validation, final = _split_dates(data)
    discovery_data = data[data["et_date"].isin(discovery)]
    validation_data = data[data["et_date"].isin(validation)]
    threshold = float(discovery_data["direction_contract_flow_5m"].abs().quantile(0.90))
    shocks = validation_data[validation_data["direction_contract_flow_5m"].abs() >= threshold].copy()
    quiet = validation_data[validation_data["direction_contract_flow_5m"].abs() < threshold * 0.5].copy()
    rows: list[dict[str, Any]] = []
    for horizon in (5, 15, 30, 60):
        block = shocks.dropna(subset=[f"future_ticks_{horizon}m"])
        direction = np.sign(block["direction_contract_flow_5m"])
        signed = direction * block[f"future_ticks_{horizon}m"]
        baseline_abs = float(quiet[f"future_abs_{horizon}m"].mean())
        baseline_range = float(quiet[f"future_range_{horizon}m"].mean())
        rows.append(_stats(block, signed, experiment="direct_flow", outcome="signed", horizon=horizon, threshold=threshold))
        rows.append(_stats(block, block[f"future_abs_{horizon}m"] - baseline_abs, experiment="direct_flow", outcome="absolute_lift", horizon=horizon, threshold=threshold, baseline=baseline_abs))
        rows.append(_stats(block, block[f"future_range_{horizon}m"] - baseline_range, experiment="direct_flow", outcome="range_lift", horizon=horizon, threshold=threshold, baseline=baseline_range))
    direct = _apply_fdr(pd.DataFrame(rows))
    artifacts = {}
    direct_path = output_dir / EXPERIMENT_NAMES[0]
    direct.to_csv(direct_path, index=False)
    artifacts[EXPERIMENT_NAMES[0]] = str(direct_path)

    # Experiment 2: flow versus subsequent skew on available overlap.
    flow_skew_rows = []
    if OPTIONS_CONTEXT.exists():
        options = pd.read_csv(OPTIONS_CONTEXT, usecols=["ts", "opt_rr50_chg_30m", "opt_skew_index"])
        options["ts"] = pd.to_datetime(options["ts"], utc=True)
        overlap = shocks.merge(options, on="ts", how="inner")
        for target in ("opt_rr50_chg_30m", "opt_skew_index"):
            block = overlap.dropna(subset=[target])
            values = np.sign(block["direction_contract_flow_5m"]) * pd.to_numeric(block[target], errors="coerce")
            flow_skew_rows.append(_stats(block, values, experiment="flow_vs_skew", target=target))
    flow_skew = _apply_fdr(pd.DataFrame(flow_skew_rows))
    path = output_dir / EXPERIMENT_NAMES[1]
    flow_skew.to_csv(path, index=False)
    artifacts[EXPERIMENT_NAMES[1]] = str(path)

    # Experiment 3: option-flow and futures-flow confluence.
    confluence_rows = []
    option_sign = np.sign(shocks["direction_contract_flow_5m"])
    futures_sign = np.sign(shocks["agg_5m"])
    for label, mask in {
        "agree": (option_sign == futures_sign) & (option_sign != 0),
        "disagree": (option_sign == -futures_sign) & (option_sign != 0),
    }.items():
        for horizon in (15, 30, 60):
            block = shocks[mask].dropna(subset=[f"future_ticks_{horizon}m"])
            values = np.sign(block["direction_contract_flow_5m"]) * block[f"future_ticks_{horizon}m"]
            confluence_rows.append(_stats(block, values, experiment="confluence", cell=label, horizon=horizon))
    confluence = _apply_fdr(pd.DataFrame(confluence_rows))
    path = output_dir / EXPERIMENT_NAMES[2]
    confluence.to_csv(path, index=False)
    artifacts[EXPERIMENT_NAMES[2]] = str(path)

    # Experiment 4: large/sweep/ATM/wing magnitude.
    micro_rows = []
    cells = {
        "large": validation_data["large_p90_direction_flow_5m"].abs() > 0,
        "sweep": validation_data["sweep_direction_flow_5m"].abs() > 0,
        "atm_dominant": validation_data["ATM_direction_flow"].abs() > validation_data["WING_direction_flow"].abs(),
        "wing_dominant": validation_data["WING_direction_flow"].abs() > validation_data["ATM_direction_flow"].abs(),
    }
    for cell, mask in cells.items():
        for horizon in (15, 30, 60):
            block = validation_data[mask].dropna(subset=[f"future_range_{horizon}m"])
            baseline = float(validation_data.loc[~mask, f"future_range_{horizon}m"].mean())
            micro_rows.append(_stats(block, block[f"future_range_{horizon}m"] - baseline, experiment="microstructure", cell=cell, horizon=horizon, baseline=baseline))
    micro = _apply_fdr(pd.DataFrame(micro_rows))
    path = output_dir / EXPERIMENT_NAMES[3]
    micro.to_csv(path, index=False)
    artifacts[EXPERIMENT_NAMES[3]] = str(path)

    # Experiment 5: flow persistence (event proximity unavailable for historical pilot).
    persistence_rows = []
    for threshold_streak in (3, 5):
        persistent = validation_data[validation_data["flow_streak"].abs() >= threshold_streak]
        for horizon in (15, 30, 60):
            block = persistent.dropna(subset=[f"future_ticks_{horizon}m"])
            values = np.sign(block["flow_streak"]) * block[f"future_ticks_{horizon}m"]
            persistence_rows.append(_stats(block, values, experiment="persistence", streak=threshold_streak, horizon=horizon))
    persistence = _apply_fdr(pd.DataFrame(persistence_rows))
    path = output_dir / EXPERIMENT_NAMES[4]
    persistence.to_csv(path, index=False)
    artifacts[EXPERIMENT_NAMES[4]] = str(path)

    leaderboard = output_dir / "candidate_leaderboard.csv"
    final_path = output_dir / "locked_final_results.csv"
    candidates = pd.concat(
        [frame.assign(source=name) for frame, name in (
            (direct, "direct"), (flow_skew, "flow_skew"), (confluence, "confluence"),
            (micro, "micro"), (persistence, "persistence"),
        ) if not frame.empty],
        ignore_index=True,
        sort=False,
    )
    candidates.to_csv(leaderboard, index=False)
    eligible = candidates[candidates.get("pass", False) == True] if "pass" in candidates else pd.DataFrame()
    pd.DataFrame(
        [{"candidate": "none", "status": "GATED", "reason": "no validation candidate passed"}]
        if eligible.empty else eligible.head(3).to_dict(orient="records")
    ).to_csv(final_path, index=False)
    artifacts["leaderboard"] = str(leaderboard)
    artifacts["final"] = str(final_path)
    artifacts["split"] = {"discovery": discovery, "validation": validation, "final": final}
    artifacts["eligible_candidates"] = int(len(eligible))
    return artifacts


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    features = pd.read_csv(args.features, parse_dates=["ts"]) if Path(args.features).exists() else pd.DataFrame()
    dates = sorted(features["ts"].dt.date.astype(str).unique().tolist()) if not features.empty else []
    gate_pass = bool(audit.get("gate", {}).get("phase0_pass"))
    enough_dates = len(dates) >= 8
    if not gate_pass or not enough_dates:
        reasons = []
        if not gate_pass:
            reasons.append("Phase-0 transfer/storage gate failed")
        if not enough_dates:
            reasons.append(f"only {len(dates)} cached sample dates; minimum 8 for pilot")
        reason = "; ".join(reasons)
        artifacts = _empty_outputs(output_dir, reason)
        phase1_executed = False
    else:
        artifacts = _pilot_experiment(features, output_dir)
        phase1_executed = True
        pilot_pass = int(artifacts.get("eligible_candidates", 0)) > 0
        reason = "pilot executed; candidate gate passed" if pilot_pass else "pilot executed; no validation candidate passed"
    summary_path = output_dir / "option_flow_research_summary.json"
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase0_gate": audit.get("gate"),
        "phase0_quality": audit.get("quality"),
        "projected_transfer": audit.get("projected"),
        "cached_feature_dates": dates,
        "phase1_executed": phase1_executed,
        "phase1_reason": reason,
        "phase2_expansion_executed": False,
        "phase2_reason": (
            "requires separate explicit user approval"
            if phase1_executed and int(artifacts.get("eligible_candidates", 0)) > 0
            else "Phase-1 candidate gate did not pass"
        ),
        "stop_go": (
            "EXPANSION_REVIEW"
            if phase1_executed and int(artifacts.get("eligible_candidates", 0)) > 0
            else "STOP"
        ),
        "artifacts": artifacts | {"summary": str(summary_path)},
        "isolation": {"read_only_api": True, "modified_live_apps": False, "wrote_live_stores": False},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run staged TY option-flow research.")
    parser.add_argument("--audit", default=str(AUDIT_PATH))
    parser.add_argument("--features", default=str(FEATURE_PATH))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
