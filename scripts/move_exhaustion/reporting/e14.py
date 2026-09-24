"""E14 cross-tenor confirmation vs exhaustion (flow proxy on TY panel)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, TICK_SIZE
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.features.cross_tenor import cross_tenor_features
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

CONTINUOUS = (
    "curve_flow_breadth",
    "number_of_confirming_tenors",
    "number_of_confirming_tenors_lag1",
    "target_minus_flow_level",
    "target_minus_slope_curvature_fit",
    "target_flow",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _monotonic_score(curve: pd.DataFrame) -> dict[str, Any]:
    if curve.empty or len(curve) < 3:
        return {"ok": False, "spearman": None}
    x = curve["decile"].to_numpy(dtype=float)
    y = curve["exhaustion_rate"].to_numpy(dtype=float)
    if np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return {"ok": False, "spearman": 0.0}
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    spe = float(np.corrcoef(rx, ry)[0, 1])
    return {"ok": True, "spearman": spe, "increasing": spe > 0.3, "decreasing": spe < -0.3}


def _category_stats(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cat, g in df.groupby("confirmation_category"):
        labels = pd.to_numeric(g["label"], errors="coerce")
        resolved = g[labels.isin([0, 1])]
        n_res = len(resolved)
        out[str(cat)] = {
            "n": int(len(g)),
            "n_resolved": n_res,
            "exhaustion_rate": float((resolved["label"] == 1).mean()) if n_res else None,
            "continuation_rate": float((resolved["label"] == 0).mean()) if n_res else None,
        }
    return out


def _logistic(df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    cols = features + ["label"]
    work = df.dropna(subset=cols).copy()
    work = work[work["label"].isin([0, 1])]
    if len(work) < 40 or work["label"].nunique() < 2:
        return {"ok": False, "reason": "insufficient", "n": int(len(work)), "features": features}
    x = work[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    mask = np.isfinite(x).all(axis=1)
    x, y = x[mask], y[mask]
    if len(y) < 40 or len(np.unique(y)) < 2:
        return {"ok": False, "reason": "insufficient_after_nan", "n": int(len(y))}
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    xs = (x - mu) / sd
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    clf = LogisticRegression(C=1.0, max_iter=500)
    clf.fit(xs, y)
    proba = clf.predict_proba(xs)[:, 1]
    return {
        "ok": True,
        "features": features,
        "auc": float(roc_auc_score(y, proba)),
        "n": int(len(y)),
        "coefs": {f: float(c) for f, c in zip(features, clf.coef_.ravel())},
    }


def run_e14(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    e00 = json.loads(
        (output_root / "experiments" / "E00_data_causality_audit" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    e01 = json.loads(
        (output_root / "experiments" / "E01_event_detector_comparison" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    holdout = set(e00.get("holdout_dates") or [])
    detector = e01.get("primary_detector_candidate") or "B_price_plus_flow"
    events = pd.read_csv(
        output_root / "experiments" / "E01_event_detector_comparison" / f"events_{detector}.csv"
    )
    events = events[~events["et_date"].isin(holdout)].copy()

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]

    rows = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        feats = cross_tenor_features(panel, event)
        lab = first_barrier_label(
            prices,
            start_ts=_ts(event["event_start_time"]),
            direction=int(event["event_direction"]),
            R_ticks=4,
            C_ticks=4,
            H=pd.Timedelta(minutes=15),
            tick_size=TICK_SIZE["TY"],
        )
        rows.append({**event, **feats, "label": lab.get("label")})
    df = pd.DataFrame(rows)

    dates = sorted(df["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    eval_dates = set(dates[split:])
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E14_cross_tenor_price_confirmation"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_cross_tenor_features.csv", index=False)
    df.to_parquet(exp_dir / "event_cross_tenor_features.parquet", index=False)

    by_cat_all = _category_stats(df)
    by_cat_eval = _category_stats(eval_df)

    feature_results: dict[str, Any] = {}
    for feat in CONTINUOUS:
        curve = decile_exhaustion_curve(df, feat)
        curve.to_csv(exp_dir / f"decile_{feat}.csv", index=False)
        mono = _monotonic_score(curve)
        logistic = univariate_logistic_auc(df, feat)
        folds = expanding_walk_forward_folds(
            dates, train_min=min(30, max(15, len(dates) // 2)), val_block=5
        )
        fold_coefs = []
        for fold in folds:
            res = univariate_logistic_auc(df[df["et_date"].isin(fold["train_dates"])], feat)
            if res.get("ok"):
                fold_coefs.append(res.get("coef_standardized", res.get("corr")))
        signs = [np.sign(c) for c in fold_coefs if c is not None and np.isfinite(c) and c != 0]
        feature_results[feat] = {
            "decile_curve": curve.to_dict(orient="records"),
            "monotonicity": mono,
            "logistic": logistic,
            "logistic_eval_dates": univariate_logistic_auc(eval_df, feat),
            "fold_coefs": fold_coefs,
            "sign_stable_across_folds": len(signs) >= 2 and len(set(signs)) == 1,
        }

    # Hypothesis: more confirmation → less exhaustion (more continuation)
    # So breadth/confirming tenors should have negative coef / decreasing mono vs exhaustion
    confirm_feats = ("curve_flow_breadth", "number_of_confirming_tenors")
    successes = [
        f
        for f, r in feature_results.items()
        if r["monotonicity"].get("ok")
        and (r["monotonicity"].get("increasing") or r["monotonicity"].get("decreasing"))
        and r.get("sign_stable_across_folds")
        and (r["logistic"].get("auc") or 0) >= 0.53
    ]
    leads = [
        f
        for f, r in feature_results.items()
        if r["monotonicity"].get("ok") and abs(r["monotonicity"].get("spearman") or 0) >= 0.3
    ]
    direction_ok = False
    for f in confirm_feats:
        coef = (feature_results.get(f, {}).get("logistic") or {}).get("coef_standardized")
        mono = feature_results.get(f, {}).get("monotonicity", {})
        if coef is not None and coef < 0 and mono.get("decreasing"):
            direction_ok = True
        if coef is not None and coef < 0 and f in successes:
            direction_ok = True

    # Category contrast: all confirm vs target alone
    def _rate(stats: dict[str, Any], key: str) -> float | None:
        return (stats.get(key) or {}).get("exhaustion_rate")

    alone = _rate(by_cat_eval, "target_moves_alone")
    allc = _rate(by_cat_eval, "all_tenors_confirm_flow")
    # Fall back to all-sample if eval thin
    if alone is None or allc is None:
        alone = _rate(by_cat_all, "target_moves_alone")
        allc = _rate(by_cat_all, "all_tenors_confirm_flow")
    contrast = None
    contrast_ok = False
    if alone is not None and allc is not None:
        contrast = float(alone - allc)  # alone should have higher exhaustion
        n_alone = (by_cat_all.get("target_moves_alone") or {}).get("n_resolved") or 0
        n_all = (by_cat_all.get("all_tenors_confirm_flow") or {}).get("n_resolved") or 0
        contrast_ok = contrast >= 0.08 and n_alone >= 15 and n_all >= 15

    ty_only = _logistic(df, ["target_flow"])
    with_curve = _logistic(df, ["target_flow", "curve_flow_breadth", "number_of_confirming_tenors"])
    ty_auc = (ty_only or {}).get("auc") or 0
    curve_auc = (with_curve or {}).get("auc") or 0
    incremental = float(curve_auc - ty_auc) if ty_only.get("ok") and with_curve.get("ok") else None

    hypothesis_supported = bool(
        (direction_ok and bool(successes))
        or contrast_ok
        or (incremental is not None and incremental >= 0.01 and direction_ok)
    )

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E14_cross_tenor_price_confirmation",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "price_features_available": False,
        "proxy_note": (
            "No neighbour mid prices on frozen TY TBBO panel. "
            "Tested flow-confirmation proxy (FV/TU/US agg_5m). "
            "Return PCA / curve_price_breadth unavailable."
        ),
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "direction_ok": direction_ok,
        "alone_vs_all_confirm_exhaustion_spread": contrast,
        "contrast_ok": contrast_ok,
        "incremental_auc_vs_ty_flow": incremental,
        "conclusion": (
            f"Cross-tenor confirmation on {detector} (FLOW PROXY). "
            f"Price breadth/PCA unavailable. "
            f"Strict successes: {successes or 'none'}. Soft leads: {leads or 'none'}. "
            f"Alone-vs-all exhaustion spread: {contrast} (ok={contrast_ok}). "
            f"Delta AUC vs TY flow alone: {incremental}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'} "
            "under flow-proxy interpretation (neighbours confirm -> continuation). "
            "Holdout untouched."
        ),
        "next_action": "E15_cross_tenor_flow_confirmation",
    }

    manifest = {
        "experiment_id": "E14_cross_tenor_price_confirmation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "price_features_available": False,
        "by_category_all": by_cat_all,
        "by_category_eval": by_cat_eval,
        "feature_results": feature_results,
        "ty_flow_only": ty_only,
        "with_curve": with_curve,
        "incremental_auc_vs_ty_flow": incremental,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "direction_ok": direction_ok,
        "contrast_ok": contrast_ok,
        "alone_vs_all_confirm_exhaustion_spread": contrast,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"features": feature_results, "by_category": by_cat_all})
    _write_json(exp_dir / "conclusion.json", conclusion)
    _write_json(exp_dir / "by_category_eval.json", by_cat_eval)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E14_cross_tenor_price_confirmation",
        "hypothesis": "Neighbour tenor confirmation raises continuation (flow proxy)",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best or "",
        "baseline_metric": f"ty_flow_auc={ty_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E14_cross_tenor_price_confirmation"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
