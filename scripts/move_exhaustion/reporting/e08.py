"""E08 flow persistence and termination vs exhaustion."""

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
from scripts.move_exhaustion.features.flow_persistence import (
    flow_persistence_features,
    termination_score,
)
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

CONTINUOUS_FEATURES = (
    "same_side_run_length",
    "max_same_side_run",
    "sign_autocorr",
    "trade_intensity",
    "intensity_acceleration",
    "same_dir_interarrival",
    "sign_entropy",
    "sign_flip_rate",
    "same_dir_flow_fraction",
    "termination_score",
)
PATTERN_FLAGS = (
    "intensity_peak_passed",
    "same_side_run_break",
    "opposite_flow_emergence",
    "directional_flow_deceleration",
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


def _flag_exhaustion_table(df: pd.DataFrame, flag: str) -> dict[str, Any]:
    work = df.dropna(subset=[flag, "label"]).copy()
    work = work[work["label"].isin([0, 1])]
    out: dict[str, Any] = {"feature": flag}
    for val, g in work.groupby(work[flag].astype(bool)):
        out[str(bool(val))] = {
            "n": int(len(g)),
            "exhaustion_rate": float((g["label"] == 1).mean()) if len(g) else None,
        }
    if "True" in out and "False" in out:
        t = out["True"].get("exhaustion_rate")
        f = out["False"].get("exhaustion_rate")
        out["spread"] = (t - f) if t is not None and f is not None else None
    return out


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


def run_e08(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        feats = flow_persistence_features(panel, event)
        feats["termination_score"] = termination_score(feats)
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

    exp_dir = output_root / "experiments" / "E08_flow_persistence_and_termination"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_persistence_features.csv", index=False)
    df.to_parquet(exp_dir / "event_persistence_features.parquet", index=False)

    dates = sorted(df["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    eval_dates = set(dates[split:])
    eval_df = df[df["et_date"].isin(eval_dates)]

    feature_results: dict[str, Any] = {}
    for feat in CONTINUOUS_FEATURES:
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

    flag_results = {flag: _flag_exhaustion_table(df, flag) for flag in PATTERN_FLAGS}
    flag_results_eval = {flag: _flag_exhaustion_table(eval_df, flag) for flag in PATTERN_FLAGS}

    # Multivariate: persistence bundle vs intensity-only
    persist_cols = [
        "same_side_run_length",
        "sign_flip_rate",
        "sign_entropy",
        "intensity_acceleration",
        "termination_score",
    ]
    ablations = {
        "intensity_only": _multivariate_logistic_auc(df, ["trade_intensity"]),
        "persistence_bundle": _multivariate_logistic_auc(df, persist_cols),
        "termination_score_only": _multivariate_logistic_auc(df, ["termination_score"]),
        "flip_and_entropy": _multivariate_logistic_auc(df, ["sign_flip_rate", "sign_entropy"]),
    }

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
    # Pattern support: at least one flag with >=8pp exhaustion spread and n>=20 each side
    useful_flags = []
    for flag, tab in flag_results.items():
        spread = tab.get("spread")
        n_t = (tab.get("True") or {}).get("n") or 0
        n_f = (tab.get("False") or {}).get("n") or 0
        if spread is not None and abs(spread) >= 0.08 and n_t >= 20 and n_f >= 20:
            useful_flags.append(flag)

    persist_auc = (ablations["persistence_bundle"] or {}).get("auc") or 0
    intens_auc = (ablations["intensity_only"] or {}).get("auc") or 0
    beats_intensity = persist_auc >= intens_auc + 0.01
    hypothesis_supported = bool(successes) and (bool(useful_flags) or beats_intensity)

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E08_flow_persistence_and_termination",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "useful_pattern_flags": useful_flags,
        "persistence_vs_intensity_auc": {
            "persistence_bundle": persist_auc,
            "intensity_only": intens_auc,
            "delta": persist_auc - intens_auc,
        },
        "grid_note": (
            "1m panel approximations of trade-sign runs/intensity. "
            "Hawkes kernels not fit (need trade prints; marked unavailable)."
        ),
        "conclusion": (
            f"Flow persistence/termination on {detector} (pre-event 15m bars). "
            f"Strict successes: {successes or 'none'}. Soft leads: {leads or 'none'}. "
            f"Useful pattern flags (>=8pp, n>=20/side): {useful_flags or 'none'}. "
            f"Persistence bundle AUC={persist_auc:.3f} vs intensity-only={intens_auc:.3f}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'}: "
            "long same-direction run then declining intensity / sign mixing → exhaustion. "
            "Holdout untouched."
        ),
        "next_action": "E09_large_versus_small_trade_composition",
    }

    manifest = {
        "experiment_id": "E08_flow_persistence_and_termination",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "feature_results": feature_results,
        "flag_results": flag_results,
        "flag_results_eval": flag_results_eval,
        "ablations": ablations,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "useful_pattern_flags": useful_flags,
        "hypothesis_supported": hypothesis_supported,
        "hawkes_fit": False,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"features": feature_results, "flags": flag_results, "ablations": ablations})
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E08_flow_persistence_and_termination",
        "hypothesis": "Declining intensity + sign mixing after same-side run signals exhaustion",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best or "",
        "baseline_metric": f"intensity_auc={intens_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E08_flow_persistence_and_termination"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
