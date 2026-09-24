"""E13 fair-value and residual dynamics vs exhaustion / fade outcomes."""

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
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.features.residual_dynamics import (
    attractive_reversion_flag,
    residual_dynamics_features,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.simulation.v2_baselines import simulate_v2_trade
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

CONTINUOUS = (
    "residual_z",
    "residual_z_velocity",
    "fair_value_velocity",
    "price_velocity",
    "price_share_so_far",
    "abs_residual_z",
    "gap_closure_ticks",
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
    for cat, g in df.groupby("dynamics_category"):
        labels = pd.to_numeric(g["label"], errors="coerce")
        resolved = g[labels.isin([0, 1])]
        n_res = len(resolved)
        fade = pd.to_numeric(g.get("fade_net_ticks"), errors="coerce")
        out[str(cat)] = {
            "n": int(len(g)),
            "n_resolved": n_res,
            "exhaustion_rate": float((resolved["label"] == 1).mean()) if n_res else None,
            "mean_fade_net_ticks": float(fade.mean()) if fade.notna().any() else None,
            "median_price_share": float(
                pd.to_numeric(g["price_share_so_far"], errors="coerce").median()
            ),
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


def run_e13(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        feats = residual_dynamics_features(panel, event)
        feats["attractive_reversion"] = attractive_reversion_flag(feats)
        lab = first_barrier_label(
            prices,
            start_ts=_ts(event["event_start_time"]),
            direction=int(event["event_direction"]),
            R_ticks=4,
            C_ticks=4,
            H=pd.Timedelta(minutes=15),
            tick_size=TICK_SIZE["TY"],
        )
        # Immediate fade vs event direction (category comparison; not selective V4 entry)
        fade = simulate_v2_trade(
            panel,
            entry_ts=_ts(event["event_start_time"]),
            event_direction=int(event["event_direction"]),
            exit_mode="fixed_30m",
        )
        rows.append(
            {
                **event,
                **feats,
                "label": lab.get("label"),
                "fade_net_ticks": fade.get("net_ticks"),
                "fade_entered": fade.get("entered"),
            }
        )
    df = pd.DataFrame(rows)

    dates = sorted(df["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    eval_dates = set(dates[split:])
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E13_fair_value_and_residual_dynamics"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_residual_dynamics.csv", index=False)
    df.to_parquet(exp_dir / "event_residual_dynamics.parquet", index=False)

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

    # Static residual_z vs dynamics bundle
    static = _logistic(df, ["abs_residual_z"])
    dynamics = _logistic(
        df,
        [
            "abs_residual_z",
            "residual_z_velocity",
            "fair_value_velocity",
            "price_velocity",
            "price_share_so_far",
        ],
    )
    static_auc = (static or {}).get("auc") or 0
    dyn_auc = (dynamics or {}).get("auc") or 0
    incremental = float(dyn_auc - static_auc) if static.get("ok") and dynamics.get("ok") else None

    # Attractive category vs FV-chasing
    def _fade_mean(frame: pd.DataFrame, mask: pd.Series) -> float | None:
        s = pd.to_numeric(frame.loc[mask, "fade_net_ticks"], errors="coerce").dropna()
        return float(s.mean()) if len(s) else None

    attr = df["attractive_reversion"].astype(bool)
    chase = df["dynamics_category"] == "fair_value_chasing_price"
    attr_fade = _fade_mean(df, attr)
    chase_fade = _fade_mean(df, chase)
    attr_exh = None
    chase_exh = None
    if attr.any():
        lab = df.loc[attr & df["label"].isin([0, 1]), "label"]
        attr_exh = float((lab == 1).mean()) if len(lab) else None
    if chase.any():
        lab = df.loc[chase & df["label"].isin([0, 1]), "label"]
        chase_exh = float((lab == 1).mean()) if len(lab) else None

    successes = [
        f
        for f, r in feature_results.items()
        if f != "residual_z"  # dynamics beyond static level
        and r["monotonicity"].get("ok")
        and (r["monotonicity"].get("increasing") or r["monotonicity"].get("decreasing"))
        and r.get("sign_stable_across_folds")
        and (r["logistic"].get("auc") or 0) >= 0.53
    ]
    leads = [
        f
        for f, r in feature_results.items()
        if r["monotonicity"].get("ok") and abs(r["monotonicity"].get("spearman") or 0) >= 0.3
    ]

    # Attractive should beat FV-chasing on fade PnL or exhaustion
    attractive_better = False
    if attr_fade is not None and chase_fade is not None:
        attractive_better = attr_fade >= chase_fade + 0.1
    if attr_exh is not None and chase_exh is not None and attr_exh >= chase_exh + 0.05:
        attractive_better = True

    hypothesis_supported = bool(
        (incremental is not None and incremental >= 0.01)
        and (bool(successes) or attractive_better)
    )

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E13_fair_value_and_residual_dynamics",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "incremental_auc_vs_static_residual": incremental,
        "attractive_reversion": {
            "n": int(attr.sum()),
            "exhaustion_rate": attr_exh,
            "mean_fade_net_ticks": attr_fade,
        },
        "fair_value_chasing": {
            "n": int(chase.sum()),
            "exhaustion_rate": chase_exh,
            "mean_fade_net_ticks": chase_fade,
        },
        "attractive_better_than_chasing": attractive_better,
        "model_disagreement_note": "Single ridge FV on panel — multi-model disagreement unavailable.",
        "conclusion": (
            f"Fair-value/residual dynamics on {detector}. "
            f"Dynamics vs static |z| ΔAUC={incremental}. "
            f"Strict dynamics successes: {successes or 'none'}. "
            f"Attractive reversion n={int(attr.sum())} fade={attr_fade} exh={attr_exh}; "
            f"FV-chasing n={int(chase.sum())} fade={chase_fade} exh={chase_exh}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'}: "
            "gap-closure path matters beyond static residual. Holdout untouched."
        ),
        "next_action": "E14_cross_tenor_price_confirmation",
    }

    manifest = {
        "experiment_id": "E13_fair_value_and_residual_dynamics",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "by_category_all": by_cat_all,
        "by_category_eval": by_cat_eval,
        "feature_results": feature_results,
        "static_residual": static,
        "dynamics_bundle": dynamics,
        "incremental_auc_vs_static_residual": incremental,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "attractive_better_than_chasing": attractive_better,
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
        "experiment_id": "E13_fair_value_and_residual_dynamics",
        "hypothesis": "Residual gap-closure path beats static residual_z for exhaustion/fade",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best or "",
        "baseline_metric": f"static_abs_z_auc={static_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E13_fair_value_and_residual_dynamics"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
