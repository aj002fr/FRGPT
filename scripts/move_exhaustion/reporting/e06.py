"""E06 impact-efficiency decay vs exhaustion."""

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
from scripts.move_exhaustion.features.impact_efficiency import (
    classify_efficiency_regime,
    fit_decay_thresholds,
    horizon_efficiency_bundle,
    impact_efficiency_decay_features,
)
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

LANDMARK_MINUTES = 5
DECAY_FEATURES = (
    "impact_efficiency_decay",
    "impact_efficiency_ratio",
    "flow_per_tick_increase",
    "impact_efficiency_1m",
    "impact_efficiency_5m",
)
ABLATIONS = {
    "price_only": ["price_progress_1m"],
    "flow_only": ["directional_flow_1m"],
    "price_plus_flow": ["price_progress_1m", "directional_flow_1m"],
    "price_plus_marginal_efficiency": ["price_progress_1m", "impact_efficiency_decay"],
}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _multivariate_logistic_auc(df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    cols = features + ["label"]
    work = df.dropna(subset=cols).copy()
    work = work[work["label"].isin([0, 1])]
    if len(work) < 40 or work["label"].nunique() < 2:
        return {"ok": False, "reason": "insufficient_resolved", "n": int(len(work)), "features": features}
    x = work[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    if not np.isfinite(x).all():
        mask = np.isfinite(x).all(axis=1)
        x, y = x[mask], y[mask]
    if len(y) < 40 or len(np.unique(y)) < 2:
        return {"ok": False, "reason": "insufficient_after_nan", "n": int(len(y)), "features": features}
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    xs = (x - mu) / sd
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
    except ImportError:
        return {"ok": False, "reason": "sklearn_missing", "features": features}
    clf = LogisticRegression(C=1.0, max_iter=500)
    clf.fit(xs, y)
    proba = clf.predict_proba(xs)[:, 1]
    auc = float(roc_auc_score(y, proba)) if len(np.unique(y)) > 1 else None
    return {
        "ok": True,
        "model": "logistic",
        "features": features,
        "coefs_standardized": {f: float(c) for f, c in zip(features, clf.coef_.ravel())},
        "auc": auc,
        "n": int(len(y)),
        "positive_rate": float(y.mean()),
    }


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


def _regime_stats(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for regime, g in df.groupby("efficiency_regime"):
        labels = pd.to_numeric(g["label"], errors="coerce")
        resolved = g[labels.isin([0, 1])]
        n_res = len(resolved)
        out[str(regime)] = {
            "n": int(len(g)),
            "n_resolved": n_res,
            "exhaustion_rate": float((resolved["label"] == 1).mean()) if n_res else None,
        }
    return out


def run_e06(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        start = _ts(event["event_start_time"])
        obs = start + pd.Timedelta(minutes=LANDMARK_MINUTES)
        lab = first_barrier_label(
            prices,
            start_ts=start,
            direction=int(event["event_direction"]),
            R_ticks=4,
            C_ticks=4,
            H=pd.Timedelta(minutes=15),
            tick_size=TICK_SIZE["TY"],
        )
        hit_ts = lab.get("hit_ts")
        resolved_before_obs = False
        if hit_ts is not None and lab.get("label") in (0, 1):
            resolved_before_obs = _ts(hit_ts) <= obs

        # Horizon efficiencies at landmark (causal)
        horizons = horizon_efficiency_bundle(panel, event, observation_time=obs)
        decay = impact_efficiency_decay_features(
            panel, event, observation_time=obs, landmark_minutes=LANDMARK_MINUTES
        )
        rows.append(
            {
                **event,
                **horizons,
                **{k: v for k, v in decay.items() if k != "observation_time"},
                "observation_time": str(obs),
                "label": lab.get("label"),
                "hit_ts": str(hit_ts) if hit_ts is not None else None,
                "resolved_before_obs": resolved_before_obs,
            }
        )
    df = pd.DataFrame(rows)
    # Landmark analysis: drop events already resolved by observation time
    landmark = df[~df["resolved_before_obs"]].copy()
    # Guard against tautology: already-retracing path almost is the exhaustion label
    extending = landmark[~landmark["already_retracing"].astype(bool)].copy()


    exp_dir = output_root / "experiments" / "E06_impact_efficiency_decay"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_efficiency_features_all.csv", index=False)
    landmark.to_csv(exp_dir / "event_efficiency_features_landmark.csv", index=False)
    landmark.to_parquet(exp_dir / "event_efficiency_features_landmark.parquet", index=False)
    extending.to_csv(exp_dir / "event_efficiency_features_extending.csv", index=False)

    def _analyze(sample: pd.DataFrame, tag: str) -> dict[str, Any]:
        dates_s = sorted(sample["et_date"].dropna().unique().tolist())
        if not dates_s:
            return {"empty": True, "tag": tag}
        split_s = max(1, int(0.7 * len(dates_s)))
        train_dates_s = set(dates_s[:split_s])
        eval_dates_s = set(dates_s[split_s:])
        thr_s = fit_decay_thresholds(
            sample.loc[sample["et_date"].isin(train_dates_s), "impact_efficiency_decay"]
        )
        sample = sample.copy()
        sample["efficiency_regime"] = sample["impact_efficiency_decay"].map(
            lambda d: classify_efficiency_regime(d if pd.notna(d) else None, thr_s)
        )
        eval_s = sample[sample["et_date"].isin(eval_dates_s)]
        by_regime_eval_s = _regime_stats(eval_s)
        by_regime_all_s = _regime_stats(sample)
        rates_s = [
            v["exhaustion_rate"]
            for v in by_regime_eval_s.values()
            if v.get("exhaustion_rate") is not None and v.get("n_resolved", 0) >= 10
        ]
        spread_s = (max(rates_s) - min(rates_s)) if len(rates_s) >= 2 else None

        feature_results_s: dict[str, Any] = {}
        for feat in DECAY_FEATURES:
            curve = decile_exhaustion_curve(sample, feat)
            curve.to_csv(exp_dir / f"decile_{tag}_{feat}.csv", index=False)
            mono = _monotonic_score(curve)
            logistic = univariate_logistic_auc(sample, feat)
            folds = expanding_walk_forward_folds(
                dates_s, train_min=min(30, max(15, len(dates_s) // 2)), val_block=5
            )
            fold_coefs = []
            for fold in folds:
                res = univariate_logistic_auc(
                    sample[sample["et_date"].isin(fold["train_dates"])], feat
                )
                if res.get("ok"):
                    fold_coefs.append(res.get("coef_standardized", res.get("corr")))
            signs = [np.sign(c) for c in fold_coefs if c is not None and np.isfinite(c) and c != 0]
            sign_stable = len(signs) >= 2 and len(set(signs)) == 1
            feature_results_s[feat] = {
                "decile_curve": curve.to_dict(orient="records"),
                "monotonicity": mono,
                "logistic": logistic,
                "fold_coefs": fold_coefs,
                "sign_stable_across_folds": sign_stable,
            }

        ablation_s = {
            name: _multivariate_logistic_auc(sample, feats) for name, feats in ABLATIONS.items()
        }
        base_auc_s = (ablation_s.get("price_plus_flow") or {}).get("auc")
        marg_auc_s = (ablation_s.get("price_plus_marginal_efficiency") or {}).get("auc")
        incremental_s = None
        if base_auc_s is not None and marg_auc_s is not None:
            incremental_s = float(marg_auc_s - base_auc_s)

        # Decay hypothesis: declining efficiency → higher exhaustion
        # Prefer decreasing monotonicity on decay (more negative decay → more exhaustion)
        # OR increasing exhaustion in extremely_declining vs improving
        decay_mono = feature_results_s.get("impact_efficiency_decay", {}).get("monotonicity", {})
        decay_success = bool(
            decay_mono.get("ok")
            and decay_mono.get("decreasing")
            and feature_results_s["impact_efficiency_decay"].get("sign_stable_across_folds")
            and (feature_results_s["impact_efficiency_decay"]["logistic"].get("auc") or 0) >= 0.53
        )
        ext_decl = (by_regime_eval_s.get("extremely_declining") or {}).get("exhaustion_rate")
        improv = (by_regime_eval_s.get("improving") or {}).get("exhaustion_rate")
        regime_direction_ok = (
            ext_decl is not None and improv is not None and ext_decl >= improv + 0.05
        )

        successes_s = [
            f
            for f, r in feature_results_s.items()
            if r["monotonicity"].get("ok")
            and (r["monotonicity"].get("increasing") or r["monotonicity"].get("decreasing"))
            and r.get("sign_stable_across_folds")
            and (r["logistic"].get("auc") or 0) >= 0.53
        ]
        leads_s = [
            f
            for f, r in feature_results_s.items()
            if r["monotonicity"].get("ok") and abs(r["monotonicity"].get("spearman") or 0) >= 0.3
        ]
        useful_spread_s = bool(spread_s is not None and spread_s >= 0.08)
        hyp_s = bool(
            decay_success
            or (regime_direction_ok and useful_spread_s)
            or (incremental_s is not None and incremental_s >= 0.01 and useful_spread_s)
        )
        best_s = None
        if successes_s:
            best_s = max(successes_s, key=lambda f: feature_results_s[f]["logistic"].get("auc") or 0)
        elif leads_s:
            best_s = max(
                leads_s, key=lambda f: abs(feature_results_s[f]["monotonicity"].get("spearman") or 0)
            )
        return {
            "tag": tag,
            "n": int(len(sample)),
            "thresholds": thr_s,
            "by_regime_eval": by_regime_eval_s,
            "by_regime_all": by_regime_all_s,
            "regime_spread_eval": spread_s,
            "useful_spread": useful_spread_s,
            "regime_direction_ok": regime_direction_ok,
            "feature_results": feature_results_s,
            "ablation_results": ablation_s,
            "ablation_incremental_auc_vs_price_flow": incremental_s,
            "success_features": successes_s,
            "lead_features": leads_s,
            "best_feature": best_s,
            "decay_hypothesis_supported": hyp_s,
            "decay_success": decay_success,
        }

    landmark_res = _analyze(landmark, "landmark")
    extending_res = _analyze(extending, "extending")
    # Primary verdict uses still-extending subsample (non-tautological)
    primary = extending_res if not extending_res.get("empty") else landmark_res
    hypothesis_supported = bool(primary.get("decay_hypothesis_supported"))

    conclusion = {
        "experiment_id": "E06_impact_efficiency_decay",
        "status": "completed",
        "event_universe": detector,
        "landmark_minutes": LANDMARK_MINUTES,
        "hypothesis_supported": hypothesis_supported,
        "primary_sample": primary.get("tag"),
        "success_features": primary.get("success_features"),
        "lead_features": primary.get("lead_features"),
        "best_feature": primary.get("best_feature"),
        "regime_spread_eval": primary.get("regime_spread_eval"),
        "ablation_incremental_auc_vs_price_flow": primary.get(
            "ablation_incremental_auc_vs_price_flow"
        ),
        "tautology_note": (
            "Full landmark sample includes already-retracing paths whose negative "
            "efficiency nearly encodes the exhaustion label. Primary verdict uses "
            "still-extending subsample (window_progress>=0)."
        ),
        "auc_note": "Reported AUCs are in-sample logistic fits (same as E05); not OOS.",
        "grid_note": (
            "1m panel: impact_efficiency_5s/30s approximate to 1-bar (same as 1m). "
            "Decay uses early vs recent thirds of [event_start, start+5m]; "
            "events resolved before landmark excluded."
        ),
        "landmark_diag": {
            "n": landmark_res.get("n"),
            "decay_hypothesis_supported": landmark_res.get("decay_hypothesis_supported"),
            "best_feature": landmark_res.get("best_feature"),
            "regime_spread_eval": landmark_res.get("regime_spread_eval"),
        },
        "conclusion": (
            f"Impact-efficiency decay on {detector} at t+{LANDMARK_MINUTES}m. "
            f"Primary (still-extending) sample n={primary.get('n')}: "
            f"decay hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'}. "
            f"Strict successes: {primary.get('success_features') or 'none'}. "
            f"Soft leads: {primary.get('lead_features') or 'none'}. "
            f"Eval regime spread: {primary.get('regime_spread_eval')}; "
            f"regime direction (declining>improving): {primary.get('regime_direction_ok')}. "
            f"Ablation ΔAUC (price+decay vs price+flow): "
            f"{primary.get('ablation_incremental_auc_vs_price_flow')}. "
            "Holdout untouched."
        ),
        "next_action": "E07_state_dependent_impact_shortfall",
    }

    manifest = {
        "experiment_id": "E06_impact_efficiency_decay",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "landmark_minutes": LANDMARK_MINUTES,
        "n_events_all": int(len(df)),
        "n_events_landmark": int(len(landmark)),
        "n_events_extending": int(len(extending)),
        "n_resolved_before_obs": int(df["resolved_before_obs"].sum()),
        "n_already_retracing": int(landmark["already_retracing"].astype(bool).sum()),
        "primary_sample": primary.get("tag"),
        "thresholds": primary.get("thresholds"),
        "by_regime_eval": primary.get("by_regime_eval"),
        "by_regime_all": primary.get("by_regime_all"),
        "regime_spread_eval": primary.get("regime_spread_eval"),
        "useful_spread": primary.get("useful_spread"),
        "regime_direction_ok": primary.get("regime_direction_ok"),
        "feature_results": primary.get("feature_results"),
        "ablation_results": primary.get("ablation_results"),
        "ablation_incremental_auc_vs_price_flow": primary.get(
            "ablation_incremental_auc_vs_price_flow"
        ),
        "success_features": primary.get("success_features"),
        "lead_features": primary.get("lead_features"),
        "best_feature": primary.get("best_feature"),
        "hypothesis_supported": hypothesis_supported,
        "landmark_analysis": {
            k: landmark_res.get(k)
            for k in (
                "n",
                "by_regime_eval",
                "regime_spread_eval",
                "ablation_incremental_auc_vs_price_flow",
                "success_features",
                "lead_features",
                "best_feature",
                "decay_hypothesis_supported",
            )
        },
        "extending_analysis": {
            k: extending_res.get(k)
            for k in (
                "n",
                "by_regime_eval",
                "regime_spread_eval",
                "ablation_incremental_auc_vs_price_flow",
                "success_features",
                "lead_features",
                "best_feature",
                "decay_hypothesis_supported",
                "regime_direction_ok",
            )
        },
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(
        exp_dir / "metrics.json",
        {
            "extending": {
                "features": extending_res.get("feature_results"),
                "ablations": extending_res.get("ablation_results"),
            },
            "landmark": {
                "features": landmark_res.get("feature_results"),
                "ablations": landmark_res.get("ablation_results"),
            },
        },
    )
    _write_json(exp_dir / "conclusion.json", conclusion)
    _write_json(exp_dir / "by_regime_eval.json", primary.get("by_regime_eval") or {})

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E06_impact_efficiency_decay",
        "hypothesis": "Weaker recent vs early impact raises exhaustion probability",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": primary.get("best_feature")
        or (f"spread={primary.get('regime_spread_eval')}"),
        "baseline_metric": (
            f"price_plus_flow_auc="
            f"{(primary.get('ablation_results') or {}).get('price_plus_flow', {}).get('auc')}"
        ),
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        # Drop prior E06 rows so re-runs don't duplicate
        reg = reg[reg["experiment_id"] != "E06_impact_efficiency_decay"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
