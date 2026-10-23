"""E07 state-dependent impact shortfall vs exhaustion."""

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
from scripts.move_exhaustion.features.impact_shortfall import (
    build_bar_impact_frame,
    event_impact_shortfall,
    fit_all_impact_models,
    mark_event_bars,
    select_best_by_oos_rmse,
)
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

SHORTFALL_FEATURES = (
    "impact_shortfall",
    "impact_shortfall_z",
    "cum_impact_shortfall",
)
COMPARE_FEATURES = SHORTFALL_FEATURES + (
    "residual_z_at_event",
    "actual_directional_progress",
    "expected_directional_progress",
)
# Cumulative shortfall uses post-start path → near-label leakage; diagnostic only.
CONTAMINATED_FEATURES = {"cum_impact_shortfall"}


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


def _multivariate_logistic_auc(df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
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


def run_e07(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
    bars = build_bar_impact_frame(panel)
    event_mask = mark_event_bars(bars, events, pad_minutes=15)
    bars["is_event_bar"] = event_mask.to_numpy()

    dates = sorted(bars["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    train_dates = set(dates[:split])
    oos_dates = set(dates[split:])

    train_ne = bars[(bars["et_date"].isin(train_dates)) & (~bars["is_event_bar"])]
    oos_ne = bars[(bars["et_date"].isin(oos_dates)) & (~bars["is_event_bar"])]

    bundles = fit_all_impact_models(train_ne, oos_ne)
    best = select_best_by_oos_rmse(bundles)
    if best is None:
        raise RuntimeError("No impact model produced OOS regression metrics")

    model_table = [
        {
            "name": b.name,
            "n_train": b.n_train,
            "n_oos": b.n_oos,
            "train_rmse": b.train_rmse,
            "oos_rmse": b.oos_rmse,
            "oos_mae": b.oos_mae,
            "scale": b.scale,
            "selected": b.name == best.name,
        }
        for b in bundles
    ]

    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]
    # Score events with every successful model for comparison; primary = best OOS RMSE
    per_model_frames: dict[str, pd.DataFrame] = {}
    for bundle in bundles:
        if bundle.oos_rmse is None or "failed" in bundle.name:
            continue
        rows = []
        for _, ev in events.iterrows():
            event = ev.to_dict()
            feats = event_impact_shortfall(panel, event, bundle)
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
        per_model_frames[bundle.name] = pd.DataFrame(rows)

    primary_df = per_model_frames[best.name]
    exp_dir = output_root / "experiments" / "E07_state_dependent_impact_shortfall"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    primary_df.to_csv(exp_dir / "event_shortfall_features.csv", index=False)
    primary_df.to_parquet(exp_dir / "event_shortfall_features.parquet", index=False)
    _write_json(exp_dir / "impact_model_selection.json", {"models": model_table, "selected": best.name})

    eval_df = primary_df[primary_df["et_date"].isin(oos_dates)].copy()
    train_ev = primary_df[primary_df["et_date"].isin(train_dates)].copy()

    feature_results: dict[str, Any] = {}
    for feat in COMPARE_FEATURES:
        curve = decile_exhaustion_curve(primary_df, feat)
        curve.to_csv(exp_dir / f"decile_{feat}.csv", index=False)
        mono = _monotonic_score(curve)
        logistic = univariate_logistic_auc(primary_df, feat)
        folds = expanding_walk_forward_folds(
            sorted(primary_df["et_date"].dropna().unique().tolist()),
            train_min=min(30, max(15, len(dates) // 2)),
            val_block=5,
        )
        fold_coefs = []
        for fold in folds:
            res = univariate_logistic_auc(
                primary_df[primary_df["et_date"].isin(fold["train_dates"])], feat
            )
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

    # Cross-model: shortfall AUC on eval events
    model_exhaustion: dict[str, Any] = {}
    for name, frame in per_model_frames.items():
        ev = frame[frame["et_date"].isin(oos_dates)]
        model_exhaustion[name] = {
            "impact_shortfall": univariate_logistic_auc(ev, "impact_shortfall"),
            "cum_impact_shortfall": univariate_logistic_auc(ev, "cum_impact_shortfall"),
            "n_eval_events": int(len(ev)),
        }

    ablations = {
        "shortfall_only": _multivariate_logistic_auc(primary_df, ["impact_shortfall"]),
        "shortfall_z_only": _multivariate_logistic_auc(primary_df, ["impact_shortfall_z"]),
        "cum_shortfall_only": _multivariate_logistic_auc(primary_df, ["cum_impact_shortfall"]),
        "residual_z_only": _multivariate_logistic_auc(primary_df, ["residual_z_at_event"]),
        "actual_progress_only": _multivariate_logistic_auc(
            primary_df, ["actual_directional_progress"]
        ),
        "expected_progress_only": _multivariate_logistic_auc(
            primary_df, ["expected_directional_progress"]
        ),
        "actual_plus_expected": _multivariate_logistic_auc(
            primary_df, ["actual_directional_progress", "expected_directional_progress"]
        ),
        "shortfall_plus_residual": _multivariate_logistic_auc(
            primary_df, ["impact_shortfall", "residual_z_at_event"]
        ),
        "shortfall_plus_actual": _multivariate_logistic_auc(
            primary_df, ["impact_shortfall", "actual_directional_progress"]
        ),
    }

    sf = feature_results["impact_shortfall"]
    rz = feature_results["residual_z_at_event"]
    act = feature_results["actual_directional_progress"]
    sf_auc = (sf["logistic"].get("auc") or 0) if sf["logistic"].get("ok") else 0
    rz_auc = (rz["logistic"].get("auc") or 0) if rz["logistic"].get("ok") else 0
    act_auc = (act["logistic"].get("auc") or 0) if act["logistic"].get("ok") else 0
    sf_eval = (sf.get("logistic_eval_dates") or {}).get("auc") or 0
    act_eval = (act.get("logistic_eval_dates") or {}).get("auc") or 0
    corr_sf_act = float(
        pd.to_numeric(primary_df["impact_shortfall"], errors="coerce").corr(
            pd.to_numeric(primary_df["actual_directional_progress"], errors="coerce")
        )
    )
    beats_residual = sf_auc >= rz_auc + 0.01
    beats_actual = sf_auc >= act_auc + 0.02
    collinear_with_actual = bool(np.isfinite(corr_sf_act) and abs(corr_sf_act) >= 0.85)

    successes = [
        f
        for f, r in feature_results.items()
        if f.startswith("impact")
        and f not in CONTAMINATED_FEATURES
        and r["monotonicity"].get("ok")
        and (r["monotonicity"].get("increasing") or r["monotonicity"].get("decreasing"))
        and r.get("sign_stable_across_folds")
        and (r["logistic"].get("auc") or 0) >= 0.53
    ]
    fixed_name = "fixed_aggression_scaling"
    fixed_auc = (
        (model_exhaustion.get(fixed_name) or {}).get("impact_shortfall", {}).get("auc") or 0
    )
    selected_eval_auc = (
        (model_exhaustion.get(best.name) or {}).get("impact_shortfall", {}).get("auc") or 0
    )
    beats_fixed = selected_eval_auc >= fixed_auc + 0.01
    # Clean support: incremental over actual progress and not nearly -move-size
    hypothesis_supported = (
        bool(successes)
        and beats_actual
        and not collinear_with_actual
        and (beats_residual or beats_fixed)
    )
    provisional_lead = bool(successes) and (beats_residual or beats_fixed) and not hypothesis_supported

    best_feat = None
    if successes:
        best_feat = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    else:
        scored = [
            (f, r["logistic"].get("auc") or 0)
            for f, r in feature_results.items()
            if r["logistic"].get("ok") and f not in CONTAMINATED_FEATURES
        ]
        if scored:
            best_feat = max(scored, key=lambda t: t[1])[0]

    conclusion = {
        "experiment_id": "E07_state_dependent_impact_shortfall",
        "status": "completed",
        "event_universe": detector,
        "selected_impact_model": best.name,
        "selection_criterion": "oos_rmse_non_event_bars",
        "hypothesis_supported": hypothesis_supported,
        "provisional_lead": provisional_lead,
        "success_features": successes,
        "best_feature": best_feat,
        "shortfall_auc_insample": sf_auc,
        "shortfall_auc_eval_dates": sf_eval,
        "actual_progress_auc_insample": act_auc,
        "actual_progress_auc_eval_dates": act_eval,
        "residual_z_auc_insample": rz_auc,
        "beats_residual_z": beats_residual,
        "beats_actual_progress": beats_actual,
        "collinear_with_actual": collinear_with_actual,
        "beats_fixed_scaling_on_eval": beats_fixed,
        "corr_shortfall_vs_actual": corr_sf_act,
        "contamination_note": (
            "cum_impact_shortfall uses post-start path and is diagnostic-only. "
            "Start-time impact_shortfall correlates strongly with -actual progress; "
            "require incremental AUC over actual_directional_progress and |corr|<0.85 "
            "for clean support."
        ),
        "auc_note": "Feature AUCs are in-sample unless labeled eval_dates. Eval resolved n is small.",
        "conclusion": (
            f"State-dependent impact shortfall on {detector}. "
            f"Best OOS impact model: {best.name} (oos_rmse={best.oos_rmse}). "
            f"Start-time shortfall AUC={sf_auc:.3f} vs actual_progress={act_auc:.3f} "
            f"vs residual_z={rz_auc:.3f} (corr(shortfall,actual)={corr_sf_act:.3f}, "
            f"collinear={collinear_with_actual}). "
            f"Eval-date shortfall AUC={sf_eval} (n_resolved small). "
            f"Beats actual(+2pp): {beats_actual}; beats residual: {beats_residual}; "
            f"beats fixed scaling on eval: {beats_fixed}. "
            f"Strict successes (ex-cum): {successes or 'none'}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else ('PROVISIONAL only' if provisional_lead else 'NOT supported')}. "
            "Holdout untouched."
        ),
        "next_action": "E08_flow_persistence_and_termination",
    }

    manifest = {
        "experiment_id": "E07_state_dependent_impact_shortfall",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(primary_df)),
        "n_train_non_event_bars": int(len(train_ne)),
        "n_oos_non_event_bars": int(len(oos_ne)),
        "train_dates": sorted(train_dates),
        "oos_dates": sorted(oos_dates),
        "impact_models": model_table,
        "selected_impact_model": best.name,
        "feature_results": feature_results,
        "model_exhaustion_eval": model_exhaustion,
        "ablations": ablations,
        "success_features": successes,
        "best_feature": best_feat,
        "hypothesis_supported": hypothesis_supported,
        "provisional_lead": provisional_lead,
        "beats_residual_z": beats_residual,
        "beats_actual_progress": beats_actual,
        "collinear_with_actual": collinear_with_actual,
        "beats_fixed_scaling_on_eval": beats_fixed,
        "corr_shortfall_vs_actual": corr_sf_act,
        "n_train_events": int(len(train_ev)),
        "n_eval_events": int(len(eval_df)),
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"features": feature_results, "ablations": ablations})
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E07_state_dependent_impact_shortfall",
        "hypothesis": "Flow-conditioned impact shortfall beats fixed scaling for exhaustion",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best_feat or best.name,
        "baseline_metric": f"residual_z_auc={rz_auc}; fixed_eval_auc={fixed_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E07_state_dependent_impact_shortfall"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
