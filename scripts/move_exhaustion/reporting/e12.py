"""E12 microprice and book-imbalance confirmation vs exhaustion."""

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
from scripts.move_exhaustion.features.flow_categories import additional_extension_ticks
from scripts.move_exhaustion.features.microprice import microprice_imbalance_features
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

CONTINUOUS = (
    "directional_microprice_signal",
    "directional_book_imbalance",
    "microprice_signal_change",
    "imbalance_change",
    "imbalance_persistence",
)
PATTERNS = (
    "price_continues_microprice_turns",
    "imbalance_reverses_before_price",
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


def _flag_table(df: pd.DataFrame, flag: str) -> dict[str, Any]:
    work = df.dropna(subset=[flag, "label"]).copy()
    work = work[work["label"].isin([0, 1])]
    out: dict[str, Any] = {"feature": flag}
    for val, g in work.groupby(work[flag].astype(bool)):
        out[str(bool(val))] = {
            "n": int(len(g)),
            "exhaustion_rate": float((g["label"] == 1).mean()) if len(g) else None,
            "median_extension": float(
                pd.to_numeric(g["additional_extension_ticks"], errors="coerce").median()
            ),
            "median_time_to_outcome_min": float(
                pd.to_numeric(g["time_to_outcome_min"], errors="coerce").median()
            ),
        }
    if "True" in out and "False" in out:
        t = out["True"].get("exhaustion_rate")
        f = out["False"].get("exhaustion_rate")
        out["spread"] = (t - f) if t is not None and f is not None else None
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


def run_e12(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        feats = microprice_imbalance_features(panel, event)
        ext = additional_extension_ticks(panel, event)
        lab = first_barrier_label(
            prices,
            start_ts=_ts(event["event_start_time"]),
            direction=int(event["event_direction"]),
            R_ticks=4,
            C_ticks=4,
            H=pd.Timedelta(minutes=15),
            tick_size=TICK_SIZE["TY"],
        )
        hit_ts = lab.get("hit_ts")
        ttm = None
        if hit_ts is not None and lab.get("label") in (0, 1):
            ttm = (_ts(hit_ts) - _ts(event["event_start_time"])).total_seconds() / 60.0
        rows.append(
            {
                **event,
                **feats,
                "additional_extension_ticks": ext,
                "time_to_outcome_min": ttm,
                "label": lab.get("label"),
            }
        )
    df = pd.DataFrame(rows)

    dates = sorted(df["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    eval_dates = set(dates[split:])
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E12_microprice_and_book_imbalance_confirmation"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_microprice_features.csv", index=False)
    df.to_parquet(exp_dir / "event_microprice_features.parquet", index=False)

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
        # Also correlate with extension / time-to-outcome on resolved
        resolved = df[df["label"].isin([0, 1])]
        ext_corr = float(
            pd.to_numeric(resolved[feat], errors="coerce").corr(
                pd.to_numeric(resolved["additional_extension_ticks"], errors="coerce")
            )
        )
        ttm_corr = float(
            pd.to_numeric(resolved[feat], errors="coerce").corr(
                pd.to_numeric(resolved["time_to_outcome_min"], errors="coerce")
            )
        )
        feature_results[feat] = {
            "decile_curve": curve.to_dict(orient="records"),
            "monotonicity": mono,
            "logistic": logistic,
            "logistic_eval_dates": univariate_logistic_auc(eval_df, feat),
            "fold_coefs": fold_coefs,
            "sign_stable_across_folds": len(signs) >= 2 and len(set(signs)) == 1,
            "corr_additional_extension": ext_corr,
            "corr_time_to_outcome": ttm_corr,
        }

    pattern_results = {p: _flag_table(df, p) for p in PATTERNS}
    pattern_results_eval = {p: _flag_table(eval_df, p) for p in PATTERNS}

    # Divergence improves over price-only?
    price_only = _logistic(df, ["price_progress_ticks"])
    micro_bundle = _logistic(
        df,
        [
            "directional_microprice_signal",
            "directional_book_imbalance",
            "microprice_signal_change",
            "imbalance_change",
        ],
    )
    price_plus_micro = _logistic(
        df,
        [
            "price_progress_ticks",
            "directional_microprice_signal",
            "directional_book_imbalance",
            "microprice_signal_change",
            "imbalance_change",
        ],
    )
    price_auc = (price_only or {}).get("auc") or 0
    both_auc = (price_plus_micro or {}).get("auc") or 0
    incremental = float(both_auc - price_auc) if price_only.get("ok") and price_plus_micro.get("ok") else None

    # Hypothesis direction: turning against event (negative change / pattern True) → more exhaustion
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
    useful_patterns = []
    for p, tab in pattern_results.items():
        spread = tab.get("spread")
        n_t = (tab.get("True") or {}).get("n") or 0
        n_f = (tab.get("False") or {}).get("n") or 0
        if spread is not None and spread >= 0.08 and n_t >= 15 and n_f >= 15:
            useful_patterns.append(p)

    # Direction check on change features: negative change → exhaustion means decreasing mono? 
    # Actually: microprice_signal_change more negative → higher exhaustion → decreasing spearman of change vs exhaustion rate by decile... 
    # decile high = high change (less negative). If high change → lower exhaustion, spearman negative, and coef on change negative.
    change_dir_ok = False
    for feat in ("microprice_signal_change", "imbalance_change"):
        coef = (feature_results.get(feat, {}).get("logistic") or {}).get("coef_standardized")
        if coef is not None and coef < 0 and feat in successes:
            change_dir_ok = True

    hypothesis_supported = bool(
        (useful_patterns or change_dir_ok or ("directional_microprice_signal" in successes))
        and (incremental is not None and incremental >= 0.01)
    )

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E12_microprice_and_book_imbalance_confirmation",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "useful_patterns": useful_patterns,
        "incremental_auc_vs_price": incremental,
        "ablations": {
            "price_only_auc": price_auc,
            "micro_bundle_auc": (micro_bundle or {}).get("auc"),
            "price_plus_micro_auc": both_auc,
        },
        "conclusion": (
            f"Microprice/imbalance confirmation on {detector}. "
            f"Strict successes: {successes or 'none'}. Soft leads: {leads or 'none'}. "
            f"Useful divergence patterns (>=8pp): {useful_patterns or 'none'}. "
            f"ΔAUC vs price-only: {incremental}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'}: "
            "exhaustion more credible when microprice/imbalance stop supporting the move. "
            "Holdout untouched."
        ),
        "next_action": "E13_fair_value_and_residual_dynamics",
    }

    manifest = {
        "experiment_id": "E12_microprice_and_book_imbalance_confirmation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "feature_results": feature_results,
        "pattern_results": pattern_results,
        "pattern_results_eval": pattern_results_eval,
        "price_only": price_only,
        "micro_bundle": micro_bundle,
        "price_plus_micro": price_plus_micro,
        "incremental_auc_vs_price": incremental,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "useful_patterns": useful_patterns,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(
        exp_dir / "metrics.json",
        {"features": feature_results, "patterns": pattern_results, "ablations": conclusion["ablations"]},
    )
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E12_microprice_and_book_imbalance_confirmation",
        "hypothesis": "Microprice/imbalance turning against the move raises exhaustion odds",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best or "",
        "baseline_metric": f"price_only_auc={price_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E12_microprice_and_book_imbalance_confirmation"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
