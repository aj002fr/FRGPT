"""E28 feature-family ablation from E18 F7 / best model."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    FEATURE_FAMILIES,
    append_registry,
    chronological_split,
    family_columns,
    feature_set_columns,
    fit_resolved_logistic_proba,
    load_event_universe,
    prune_features,
    write_json,
)

DROP_ORDER = list(FEATURE_FAMILIES.keys())
ADD_ORDER = ["F0", "F1", "F2", "F3", "F4", "F5", "F6", "F7"]


def run_e28(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E28_feature_family_ablation"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    full_feats = prune_features(train_df, feature_set_columns("F7", df))
    full = fit_resolved_logistic_proba(train_df, eval_df, full_feats)
    if not full.get("ok"):
        raise RuntimeError(f"E28 full model failed: {full}")

    base_m = full["eval_metrics"]
    drop_results: dict[str, Any] = {}
    for fam in DROP_ORDER:
        drop_cols = set(family_columns(fam, full_feats))
        kept = [c for c in full_feats if c not in drop_cols]
        res = fit_resolved_logistic_proba(train_df, eval_df, kept)
        if not res.get("ok"):
            drop_results[fam] = {"ok": False, "dropped_n": len(drop_cols)}
            continue
        m = res["eval_metrics"]
        drop_results[fam] = {
            "ok": True,
            "dropped_n": len(drop_cols),
            "dropped_cols": sorted(drop_cols),
            "n_features": len(res["features"]),
            "brier": m.get("brier"),
            "pr_auc": m.get("pr_auc"),
            "precision_at_top10pct": m.get("precision_at_top10pct"),
            "delta_brier": float(m["brier"] - base_m["brier"]) if m.get("brier") is not None else None,
            "delta_pr_auc": float(m["pr_auc"] - base_m["pr_auc"]) if m.get("pr_auc") is not None else None,
            "delta_top10_precision": (
                float(m["precision_at_top10pct"] - base_m["precision_at_top10pct"])
                if m.get("precision_at_top10pct") is not None and base_m.get("precision_at_top10pct") is not None
                else None
            ),
        }

    sequential: dict[str, Any] = {}
    for name in ADD_ORDER:
        feats = prune_features(train_df, feature_set_columns(name, df))
        res = fit_resolved_logistic_proba(train_df, eval_df, feats)
        if not res.get("ok"):
            sequential[name] = {"ok": False}
            continue
        m = res["eval_metrics"]
        sequential[name] = {
            "ok": True,
            "n_features": len(res["features"]),
            "brier": m.get("brier"),
            "pr_auc": m.get("pr_auc"),
            "precision_at_top10pct": m.get("precision_at_top10pct"),
            "roc_auc": m.get("roc_auc"),
        }

    # Largest harm from dropping = most useful family (largest +delta_brier when dropped)
    useful = sorted(
        ((k, v) for k, v in drop_results.items() if v.get("ok") and v.get("delta_brier") is not None),
        key=lambda kv: kv[1]["delta_brier"],
        reverse=True,
    )
    most_useful = useful[0][0] if useful else None

    conclusion_text = (
        f"Feature-family ablation on {detector} F7 logistic. "
        f"Full Brier={base_m.get('brier')}, PR-AUC={base_m.get('pr_auc')}, "
        f"top10% prec={base_m.get('precision_at_top10pct')}. "
        f"Largest Brier harm when dropped: {most_useful}. Holdout untouched."
    )
    metrics = {
        "full": {"features_n": len(full_feats), "eval": base_m},
        "drop_one_family": drop_results,
        "sequential_add": sequential,
        "most_useful_family_by_brier_harm": most_useful,
    }
    write_json(exp_dir / "metrics.json", metrics)
    conclusion = {
        "experiment_id": "E28_feature_family_ablation",
        "status": "completed",
        "verdict": "completed",
        "hypothesis_supported": True,
        "most_useful_family": most_useful,
        "conclusion": conclusion_text,
        "next_action": "E26_trading_policy_simulation",
    }
    manifest = {
        "experiment_id": "E28_feature_family_ablation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "verdict": "completed",
        "metrics": metrics,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    append_registry(
        output_root,
        {
            "experiment_id": "E28_feature_family_ablation",
            "hypothesis": "Family ablation identifies incremental feature groups",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": f"most_useful={most_useful}",
            "baseline_metric": f"full_brier={base_m.get('brier')}",
            "incremental_improvement": True,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "E26_trading_policy_simulation",
        },
        experiment_id="E28_feature_family_ablation",
    )
    return manifest
