"""E18 multivariate exhaustion classification."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    FEATURE_SETS,
    append_registry,
    apply_calibration,
    chronological_split,
    classification_metrics,
    feature_set_columns,
    load_event_universe,
    prune_features,
    resolved_mask,
    standardize_fit_transform,
    write_json,
)

CALIBRATION_METHODS = ("none", "platt", "isotonic")
LOGISTIC_C_GRID = (0.1, 1.0, 10.0)
HGB_DEPTHS = (2, 3)


def _prepare_xy(df: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    work = df[resolved_mask(df)].copy()
    x = work[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    mask = np.isfinite(x).all(axis=1)
    return x[mask], y[mask], work.iloc[np.where(mask)[0]]


def _fit_logistic(x_tr: np.ndarray, y_tr: np.ndarray, x_ev: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    best_auc = -1.0
    best_proba = None
    best_meta: dict[str, Any] = {}
    xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
    for c in LOGISTIC_C_GRID:
        clf = LogisticRegression(C=c, max_iter=1000, random_state=RANDOM_SEED)
        clf.fit(xs_tr, y_tr)
        proba = clf.predict_proba(xs_ev)[:, 1]
        m = classification_metrics(y_tr, clf.predict_proba(xs_tr)[:, 1])
        if m.get("roc_auc", -1) > best_auc:
            best_auc = m.get("roc_auc", -1)
            best_proba = proba
            best_meta = {"C": c, "train": m}
    return best_proba, best_meta


def _fit_hgb(x_tr: np.ndarray, y_tr: np.ndarray, x_ev: np.ndarray, depth: int) -> tuple[np.ndarray, dict[str, Any]]:
    min_leaf = max(5, int(0.05 * len(y_tr)))
    clf = HistGradientBoostingClassifier(
        max_depth=depth,
        min_samples_leaf=min_leaf,
        learning_rate=0.05,
        max_iter=250,
        random_state=RANDOM_SEED,
    )
    clf.fit(x_tr, y_tr)
    proba = clf.predict_proba(x_ev)[:, 1]
    return proba, {"max_depth": depth, "train": classification_metrics(y_tr, clf.predict_proba(x_tr)[:, 1])}


def _multiclass_eval(train_df: pd.DataFrame, eval_df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    tr = train_df.copy()
    tr["label3"] = pd.to_numeric(tr["label"], errors="coerce").astype("Int64")
    ev = eval_df.copy()
    ev["label3"] = pd.to_numeric(ev["label"], errors="coerce").astype("Int64")
    tr = tr[tr["label3"].isin([-1, 0, 1])]
    ev = ev[ev["label3"].isin([-1, 0, 1])]
    feats = prune_features(tr, features)
    if len(feats) < 2:
        return {"ok": False, "reason": "insufficient_features"}
    x_tr = tr[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = tr["label3"].to_numpy(dtype=int)
    x_ev = ev[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_ev = ev["label3"].to_numpy(dtype=int)
    m_tr = np.isfinite(x_tr).all(axis=1)
    m_ev = np.isfinite(x_ev).all(axis=1)
    x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
    x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]
    if len(y_tr) < 50 or len(np.unique(y_tr)) < 2:
        return {"ok": False, "reason": "insufficient_train"}
    xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
    clf = LogisticRegression(C=1.0, max_iter=1000)
    clf.fit(xs_tr, y_tr)
    pred = clf.predict(xs_ev)
    return {
        "ok": True,
        "features": feats,
        "n_train": int(len(y_tr)),
        "n_eval": int(len(y_ev)),
        "eval_accuracy": float(accuracy_score(y_ev, pred)),
        "class_distribution_eval": {str(k): int(v) for k, v in zip(*np.unique(y_ev, return_counts=True))},
    }


def run_e18(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E18_multivariate_exhaustion_classification"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {}
    best_binary: dict[str, Any] | None = None
    baseline_brier = None

    for fset in list(FEATURE_SETS.keys()) + ["F7"]:
        cols = feature_set_columns(fset, df)
        feats = prune_features(train_df, cols)
        if len(feats) < 2:
            results[fset] = {"ok": False, "reason": "insufficient_features"}
            continue
        x_tr, y_tr, _ = _prepare_xy(train_df, feats)
        x_ev, y_ev, _ = _prepare_xy(eval_df, feats)
        if len(y_tr) < 40 or len(y_ev) < 10:
            results[fset] = {"ok": False, "reason": "insufficient_split"}
            continue

        fset_res: dict[str, Any] = {"features": feats, "models": {}}
        # Logistic
        log_proba, log_meta = _fit_logistic(x_tr, y_tr, x_ev)
        fset_res["models"]["logistic_ridge"] = log_meta

        # HistGBM depths
        for d in HGB_DEPTHS:
            hgb_proba, hgb_meta = _fit_hgb(x_tr, y_tr, x_ev, d)
            fset_res["models"][f"hist_gbm_d{d}"] = hgb_meta

        # Pick best model on train Brier for calibration sweep
        candidates = {"logistic_ridge": log_proba}
        for d in HGB_DEPTHS:
            candidates[f"hist_gbm_d{d}"], _ = _fit_hgb(x_tr, y_tr, x_ev, d)

        # Use logistic as primary for calibration comparison
        primary = log_proba
        p_tr = LogisticRegression(C=1.0, max_iter=1000).fit(
            standardize_fit_transform(x_tr, None)[0], y_tr
        ).predict_proba(standardize_fit_transform(x_tr, None)[0])[:, 1]

        cal_results = {}
        for method in CALIBRATION_METHODS:
            p_cal = apply_calibration(y_tr, p_tr, primary, method)
            cal_results[method] = classification_metrics(y_ev, p_cal)
        fset_res["calibration"] = cal_results
        fset_res["binary_eval_none"] = cal_results.get("none", {})
        fset_res["multiclass"] = _multiclass_eval(train_df, eval_df, feats)

        if fset == "F0" and cal_results.get("none", {}).get("brier") is not None:
            baseline_brier = cal_results["none"]["brier"]
        results[fset] = fset_res

        brier = (cal_results.get("none") or {}).get("brier")
        if brier is not None and (
            best_binary is None or brier < (best_binary.get("brier") or 1e9)
        ):
            best_binary = {
                "feature_set": fset,
                "brier": brier,
                "pr_auc": cal_results["none"].get("pr_auc"),
                "roc_auc": cal_results["none"].get("roc_auc"),
                "precision_at_top10pct": cal_results["none"].get("precision_at_top10pct"),
            }

    # GAM skipped if pygam not installed
    gam_status = "skipped_not_installed"
    try:
        import pygam  # noqa: F401

        gam_status = "skipped_by_design_use_logistic_hgb"
    except ImportError:
        pass

    lgb_status = "skipped_not_installed"
    try:
        import lightgbm  # noqa: F401

        lgb_status = "skipped_by_design_use_hist_gbm"
    except ImportError:
        pass

    best_brier = (best_binary or {}).get("brier")
    rel_improve = None
    if baseline_brier and best_brier is not None and baseline_brier > 0:
        rel_improve = float((baseline_brier - best_brier) / baseline_brier)
    top_prec = (best_binary or {}).get("precision_at_top10pct")
    base_rate = None
    if results.get("F0", {}).get("binary_eval_none"):
        base_rate = results["F0"]["binary_eval_none"].get("base_rate")
    prec_lift = None
    if top_prec is not None and base_rate is not None:
        prec_lift = float(top_prec - base_rate)

    verdict = "research-promising" if (
        rel_improve is not None and rel_improve >= 0.05
    ) or (prec_lift is not None and prec_lift >= 0.05) else "rejected"
    hypothesis_supported = verdict == "research-promising"

    conclusion = {
        "experiment_id": "E18_multivariate_exhaustion_classification",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "best_binary": best_binary,
        "baseline_brier_F0": baseline_brier,
        "relative_brier_improvement": rel_improve,
        "gam_status": gam_status,
        "lightgbm_status": lgb_status,
        "conclusion": (
            f"Multivariate classifiers on {detector}. Best={best_binary}. "
            f"Relative Brier improvement vs F0={rel_improve}. Verdict={verdict}. "
            "GAM/LightGBM skipped per environment. Holdout untouched."
        ),
        "next_action": "E19_discrete_time_exhaustion_hazard",
    }

    manifest = {
        "experiment_id": "E18_multivariate_exhaustion_classification",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "results": results,
        "best_binary": best_binary,
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "gam_status": gam_status,
        "lightgbm_status": lgb_status,
        "grid_note": "1m grid; 5s/15s/30s hazard bins unavailable",
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(exp_dir / "metrics.json", {"results": results, "best": best_binary})

    append_registry(
        output_root,
        {
            "experiment_id": "E18_multivariate_exhaustion_classification",
            "hypothesis": "Multivariate models beat price-only baseline on Brier/PR-AUC",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": str(best_binary),
            "baseline_metric": f"F0_brier={baseline_brier}",
            "incremental_improvement": bool(hypothesis_supported),
            "holdout_used": False,
            "conclusion": conclusion["conclusion"],
            "next_action": conclusion["next_action"],
        },
        experiment_id="E18_multivariate_exhaustion_classification",
    )
    return manifest
