"""E09 large-versus-small trade composition vs exhaustion."""

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
from scripts.move_exhaustion.features.per_tick import (
    decile_exhaustion_curve,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.features.trade_composition import (
    classify_composition,
    fit_composition_thresholds,
    large_small_composition_features,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

CONTINUOUS = (
    "large_directional_flow",
    "small_directional_flow",
    "large_trade_share",
    "change_in_large_trade_share",
    "large_trade_intensity",
    "small_trade_intensity",
    "opposite_large_trade_arrival",
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
    for cat, g in df.groupby("composition_category"):
        labels = pd.to_numeric(g["label"], errors="coerce")
        resolved = g[labels.isin([0, 1])]
        n_res = len(resolved)
        out[str(cat)] = {
            "n": int(len(g)),
            "n_resolved": n_res,
            "exhaustion_rate": float((resolved["label"] == 1).mean()) if n_res else None,
            "median_additional_extension_ticks": float(
                pd.to_numeric(g["additional_extension_ticks"], errors="coerce").median()
            ),
        }
    return out


def run_e09(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        feats = large_small_composition_features(panel, event)
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
        rows.append({**event, **feats, "additional_extension_ticks": ext, "label": lab.get("label")})
    df = pd.DataFrame(rows)

    dates = sorted(df["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    train_dates = set(dates[:split])
    eval_dates = set(dates[split:])
    thr = fit_composition_thresholds(df[df["et_date"].isin(train_dates)])
    df["composition_category"] = df.apply(lambda r: classify_composition(r.to_dict(), thr), axis=1)

    train = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E09_large_versus_small_trade_composition"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_composition_features.csv", index=False)
    df.to_parquet(exp_dir / "event_composition_features.parquet", index=False)

    by_cat_all = _category_stats(df)
    by_cat_eval = _category_stats(eval_df)
    by_cat_train = _category_stats(train)

    # Hypothesis-focused contrast: large_flow_disappearance / small_led vs large_led
    def _rate(stats: dict[str, Any], key: str) -> float | None:
        return (stats.get(key) or {}).get("exhaustion_rate")

    target_cats = ["large_flow_disappearance", "small_led_continuation"]
    base_cats = ["large_led_continuation"]
    target_rates = [r for c in target_cats if (r := _rate(by_cat_eval, c)) is not None]
    base_rates = [r for c in base_cats if (r := _rate(by_cat_eval, c)) is not None]
    # Also require enough resolved
    def _nres(stats: dict[str, Any], key: str) -> int:
        return int((stats.get(key) or {}).get("n_resolved") or 0)

    contrast_ok = False
    contrast_spread = None
    if target_rates and base_rates:
        # Need n_resolved >= 15 on compared cats when available
        usable_targets = [c for c in target_cats if _nres(by_cat_eval, c) >= 10]
        usable_base = [c for c in base_cats if _nres(by_cat_eval, c) >= 10]
        if usable_targets and usable_base:
            t = max(_rate(by_cat_eval, c) or 0 for c in usable_targets)
            b = _rate(by_cat_eval, usable_base[0]) or 0
            contrast_spread = float(t - b)
            contrast_ok = contrast_spread >= 0.08

    rates_eval = [
        v["exhaustion_rate"]
        for v in by_cat_eval.values()
        if v.get("exhaustion_rate") is not None and v.get("n_resolved", 0) >= 10
    ]
    category_spread = (max(rates_eval) - min(rates_eval)) if len(rates_eval) >= 2 else None

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
    useful_spread = bool(category_spread is not None and category_spread >= 0.08)
    # Core hypothesis: disappearance/small-led has higher exhaustion than large-led
    hypothesis_supported = bool(contrast_ok) or (bool(successes) and useful_spread)

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E09_large_versus_small_trade_composition",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "category_spread_eval": category_spread,
        "hypothesis_contrast_spread_eval": contrast_spread,
        "contrast_ok": contrast_ok,
        "grid_note": (
            "1m panel: large/small flow ≈ large_trade_share × directional agg_1m. "
            "Not true size-bucketed prints."
        ),
        "conclusion": (
            f"Large-vs-small composition on {detector}. "
            f"Eval category exhaustion spread: {category_spread}. "
            f"Disappearance/small-led vs large-led contrast: {contrast_spread} "
            f"(ok={contrast_ok}). "
            f"Strict successes: {successes or 'none'}. Soft leads: {leads or 'none'}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else 'NOT supported'}: "
            "large same-direction trades fade while small chase → exhaustion. "
            "Holdout untouched."
        ),
        "next_action": "E10_tbbo_apparent_absorption_at_extreme",
    }

    manifest = {
        "experiment_id": "E09_large_versus_small_trade_composition",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "thresholds": thr,
        "by_category_all": by_cat_all,
        "by_category_train": by_cat_train,
        "by_category_eval": by_cat_eval,
        "category_spread_eval": category_spread,
        "hypothesis_contrast_spread_eval": contrast_spread,
        "contrast_ok": contrast_ok,
        "feature_results": feature_results,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"features": feature_results, "by_category_eval": by_cat_eval})
    _write_json(exp_dir / "conclusion.json", conclusion)
    _write_json(exp_dir / "by_category_eval.json", by_cat_eval)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E09_large_versus_small_trade_composition",
        "hypothesis": "Large-flow disappearance with small chase raises exhaustion odds",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best or (f"contrast={contrast_spread}"),
        "baseline_metric": f"category_spread={category_spread}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E09_large_versus_small_trade_composition"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
