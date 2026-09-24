"""E10 TBBO apparent absorption at the extreme vs exhaustion."""

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
from scripts.move_exhaustion.features.absorption import (
    apparent_absorption_features,
    apparent_absorption_score,
    fit_absorption_z_stats,
)
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

FEATURES = (
    "trades_at_extreme",
    "volume_at_extreme",
    "directional_dv01_at_extreme",
    "average_opposing_depth_at_extreme",
    "maximum_opposing_depth_at_extreme",
    "fraction_of_trades_without_price_advance",
    "seconds_at_extreme",
    "new_extreme_rate",
    "apparent_absorption_score",
)
CONTROLS = (
    "spread_at_event",
    "depth_at_event",
    "realized_vol_at_event",
    "minutes_since_open",
    "initial_move_ticks",
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


def run_e10(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        feats = apparent_absorption_features(panel, event)
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
    train_dates = set(dates[:split])
    eval_dates = set(dates[split:])
    zstats = fit_absorption_z_stats(df[df["et_date"].isin(train_dates)])
    df["apparent_absorption_score"] = df.apply(
        lambda r: apparent_absorption_score(r.to_dict(), zstats), axis=1
    )

    exp_dir = output_root / "experiments" / "E10_tbbo_apparent_absorption_at_extreme"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_absorption_features.csv", index=False)
    df.to_parquet(exp_dir / "event_absorption_features.parquet", index=False)
    _write_json(exp_dir / "absorption_z_stats.json", zstats)

    eval_df = df[df["et_date"].isin(eval_dates)]
    feature_results: dict[str, Any] = {}
    for feat in FEATURES:
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

    controls_only = _logistic(df, list(CONTROLS))
    score_only = _logistic(df, ["apparent_absorption_score"])
    score_plus_controls = _logistic(df, ["apparent_absorption_score", *CONTROLS])
    components_plus_controls = _logistic(
        df,
        [
            "directional_dv01_at_extreme",
            "fraction_of_trades_without_price_advance",
            "seconds_at_extreme",
            "new_extreme_rate",
            *CONTROLS,
        ],
    )

    score_auc = (score_only or {}).get("auc") or 0
    ctrl_auc = (controls_only or {}).get("auc") or 0
    both_auc = (score_plus_controls or {}).get("auc") or 0
    incremental = float(both_auc - ctrl_auc) if score_plus_controls.get("ok") and controls_only.get("ok") else None
    # Score coef in controlled model
    score_coef_controlled = (score_plus_controls.get("coefs") or {}).get("apparent_absorption_score")

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
    # Hypothesis direction: higher absorption → higher exhaustion (increasing mono / +coef)
    score_mono = feature_results.get("apparent_absorption_score", {}).get("monotonicity", {})
    uni_coef = (feature_results.get("apparent_absorption_score", {}).get("logistic") or {}).get(
        "coef_standardized"
    )
    direction_ok = bool(score_mono.get("increasing") or (uni_coef is not None and uni_coef > 0))
    # Survive controls: incremental AUC >= 1pp and score coef same sign as univariate
    score_coef_controlled = (score_plus_controls.get("coefs") or {}).get("apparent_absorption_score")
    survives_controls = bool(
        incremental is not None
        and incremental >= 0.01
        and score_coef_controlled is not None
        and uni_coef is not None
        and np.sign(score_coef_controlled) == np.sign(uni_coef)
        and uni_coef != 0
    )
    hypothesis_supported = bool(
        direction_ok
        and ("apparent_absorption_score" in successes or survives_controls)
    )
    opposite_stable_lead = bool(
        (not direction_ok)
        and ("apparent_absorption_score" in successes or "directional_dv01_at_extreme" in successes)
        and feature_results.get("apparent_absorption_score", {}).get("sign_stable_across_folds")
    )

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E10_tbbo_apparent_absorption_at_extreme",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "direction_ok": direction_ok,
        "opposite_stable_lead": opposite_stable_lead,
        "survives_controls": survives_controls,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "control_ablation": {
            "controls_only_auc": ctrl_auc,
            "score_only_auc": score_auc,
            "score_plus_controls_auc": both_auc,
            "incremental_auc": incremental,
            "score_coef_controlled": score_coef_controlled,
            "univariate_coef": uni_coef,
        },
        "grid_note": (
            "1m panel: seconds_at_extreme = bars_without_new_extreme × 60; "
            "volume/dv01 at extreme from directional agg on non-advance bars; "
            "opposing depth from displayed_depth × imbalance proxy."
        ),
        "conclusion": (
            f"TBBO apparent absorption on {detector}. "
            f"Strict successes: {successes or 'none'}. Soft leads: {leads or 'none'}. "
            f"Score AUC={score_auc:.3f} (coef={uni_coef}); controls={ctrl_auc:.3f}; "
            f"score+controls={both_auc:.3f} (Δ={incremental}). "
            f"Hypothesized direction (absorption→exhaustion): {direction_ok}. "
            f"Opposite stable lead (more at-extreme flow→continuation): {opposite_stable_lead}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'}. "
            "Hand-crafted score is descriptive only. Holdout untouched."
        ),
        "next_action": "E11_liquidity_conditioned_exhaustion",
    }

    manifest = {
        "experiment_id": "E10_tbbo_apparent_absorption_at_extreme",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "feature_results": feature_results,
        "controls_only": controls_only,
        "score_only": score_only,
        "score_plus_controls": score_plus_controls,
        "components_plus_controls": components_plus_controls,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "direction_ok": direction_ok,
        "opposite_stable_lead": opposite_stable_lead,
        "survives_controls": survives_controls,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(
        exp_dir / "metrics.json",
        {
            "features": feature_results,
            "controls": {
                "controls_only": controls_only,
                "score_only": score_only,
                "score_plus_controls": score_plus_controls,
                "components_plus_controls": components_plus_controls,
            },
        },
    )
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E10_tbbo_apparent_absorption_at_extreme",
        "hypothesis": "Same-direction activity at extreme without advance signals absorption/exhaustion",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best or "",
        "baseline_metric": f"controls_auc={ctrl_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E10_tbbo_apparent_absorption_at_extreme"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
