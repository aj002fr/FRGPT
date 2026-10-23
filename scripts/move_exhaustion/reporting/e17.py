"""E17 interaction-only exhaustion rules (shallow decision trees)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.tree import DecisionTreeClassifier

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    append_registry,
    assign_tree_leaf,
    chronological_split,
    extract_tree_rules,
    load_event_universe,
    prune_features,
    resolved_mask,
    write_json,
)

TREE_FEATURES = (
    "impact_efficiency_decay",
    "impact_efficiency_1m",
    "impact_efficiency_5m",
    "dv01_per_tick",
    "directional_flow_1m",
    "target_residual_flow",
    "curve_directional_flow_factor",
    "curve_flow_breadth_norm",
    "residual_z_velocity",
    "fair_value_velocity",
    "apparent_absorption_score",
    "large_trade_share",
    "small_trade_intensity",
    "fraction_of_trades_without_price_advance",
    "displayed_depth",
    "spread",
    "signed_residual_z",
    "abs_residual_z",
    "flow_z_5m",
)


def _tree_candidates(train_df: pd.DataFrame, features: list[str], depth: int) -> dict[str, Any]:
    work = train_df[resolved_mask(train_df)].copy()
    feats = prune_features(work, features)
    if len(feats) < 2:
        return {"ok": False, "reason": "insufficient_features", "depth": depth}
    x = work[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    mask = np.isfinite(x).all(axis=1)
    x, y = x[mask], y[mask]
    n = len(y)
    min_leaf = max(5, int(0.05 * n))
    if n < 40 or len(np.unique(y)) < 2:
        return {"ok": False, "reason": "insufficient_train", "n": n, "depth": depth}
    clf = DecisionTreeClassifier(
        max_depth=depth,
        min_samples_leaf=min_leaf,
        random_state=RANDOM_SEED,
        class_weight="balanced",
    )
    clf.fit(x, y)
    rules = extract_tree_rules(clf, feats)
    return {"ok": True, "depth": depth, "clf": clf, "features": feats, "rules": rules, "n_train": n}


def _leaf_eval(
    clf: DecisionTreeClassifier,
    features: list[str],
    df: pd.DataFrame,
) -> dict[str, Any]:
    work = df[resolved_mask(df)].copy()
    x = work[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    mask = np.isfinite(x).all(axis=1)
    x, y, work = x[mask], y[mask], work.iloc[np.where(mask)[0]]
    if len(y) == 0:
        return {"ok": False, "reason": "empty_eval"}
    leaves = assign_tree_leaf(clf, x)
    base = float(y.mean())
    leaf_stats = []
    for leaf in np.unique(leaves):
        idx = leaves == leaf
        n = int(idx.sum())
        rate = float(y[idx].mean()) if n else 0.0
        leaf_stats.append({"leaf_id": int(leaf), "n": n, "exhaustion_rate": rate, "lift_pp": rate - base})
    leaf_stats.sort(key=lambda r: (-r["exhaustion_rate"], -r["n"]))
    top = leaf_stats[0] if leaf_stats else None
    # calibration on eval predictions
    proba = clf.predict_proba(x)[:, 1]
    try:
        frac, mean_pred = calibration_curve(y, proba, n_bins=min(5, len(y) // 10 + 1), strategy="quantile")
        cal = {"fraction_pos": frac.tolist(), "mean_pred": mean_pred.tolist()}
    except Exception:
        cal = {"ok": False}
    return {
        "ok": True,
        "n_eval": int(len(y)),
        "base_rate": base,
        "leaf_stats": leaf_stats,
        "top_leaf": top,
        "calibration": cal,
    }


def run_e17(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    events, detector, _ = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E17_interaction_only_exhaustion_rules"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    available = [f for f in TREE_FEATURES if f in df.columns]
    models: dict[str, Any] = {}
    best: dict[str, Any] | None = None
    for depth in (2, 3):
        cand = _tree_candidates(train_df, available, depth)
        key = f"depth_{depth}"
        if not cand.get("ok"):
            models[key] = cand
            continue
        ev = _leaf_eval(cand["clf"], cand["features"], eval_df)
        tr = _leaf_eval(cand["clf"], cand["features"], train_df)
        # OOS stability: sign of top-leaf lift matches train
        stable = False
        if tr.get("top_leaf") and ev.get("top_leaf"):
            stable = (tr["top_leaf"]["lift_pp"] >= 0) == (ev["top_leaf"]["lift_pp"] >= 0)
        entry = {
            "depth": depth,
            "features": cand["features"],
            "rules": cand["rules"],
            "n_train": cand["n_train"],
            "train_eval": tr,
            "eval": ev,
            "sign_stable_top_leaf": stable,
        }
        models[key] = entry
        if ev.get("top_leaf") and (
            best is None
            or (ev["top_leaf"].get("lift_pp") or -1e9) > (best.get("eval", {}).get("top_leaf", {}).get("lift_pp") or -1e9)
        ):
            best = entry

    top = (best or {}).get("eval", {}).get("top_leaf") or {}
    base = (best or {}).get("eval", {}).get("base_rate")
    lift = top.get("lift_pp")
    n_top = top.get("n") or 0
    verdict = "research-promising" if (
        lift is not None and lift >= 0.05 and n_top >= 15
    ) else "rejected"
    hypothesis_supported = verdict == "research-promising"

    rules_df = []
    for k, m in models.items():
        if not m.get("rules"):
            continue
        for i, r in enumerate(m["rules"]):
            rules_df.append(
                {
                    "model": k,
                    "rule_id": i,
                    "conditions": " AND ".join(r["conditions"]),
                    "n_train": r["n_samples"],
                    "train_exhaustion_rate": r["exhaustion_rate"],
                }
            )
    if rules_df:
        pd.DataFrame(rules_df).to_csv(exp_dir / "extracted_rules.csv", index=False)

    conclusion = {
        "experiment_id": "E17_interaction_only_exhaustion_rules",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "event_universe": detector,
        "best_depth": (best or {}).get("depth"),
        "top_leaf_eval": top,
        "eval_base_rate": base,
        "top_leaf_lift_pp": lift,
        "sign_stable_top_leaf": (best or {}).get("sign_stable_top_leaf"),
        "conclusion": (
            f"Shallow trees on {detector} (1m grid). Best depth={ (best or {}).get('depth') }; "
            f"eval top-leaf lift={lift * 100:.1f}pp (n={n_top}, need >=5pp & n>=15). "
            f"Verdict={verdict}. Holdout untouched."
        ),
        "next_action": "E18_multivariate_exhaustion_classification",
    }

    manifest = {
        "experiment_id": "E17_interaction_only_exhaustion_rules",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "models": {k: {kk: vv for kk, vv in v.items() if kk != "clf"} for k, v in models.items()},
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "best_model": best,
        "grid_note": df["grid_note"].iloc[0] if "grid_note" in df.columns else None,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(exp_dir / "metrics.json", {"models": models, "best": best})

    append_registry(
        output_root,
        {
            "experiment_id": "E17_interaction_only_exhaustion_rules",
            "hypothesis": "Interpretable interaction rules lift exhaustion precision",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": f"top_leaf_lift_pp={lift}",
            "baseline_metric": f"eval_base_rate={base}",
            "incremental_improvement": bool(hypothesis_supported),
            "holdout_used": False,
            "conclusion": conclusion["conclusion"],
            "next_action": conclusion["next_action"],
        },
        experiment_id="E17_interaction_only_exhaustion_rules",
    )
    return manifest
