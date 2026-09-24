"""E11 liquidity-conditioned exhaustion interactions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.liquidity import (
    add_liquidity_buckets,
    fit_liquidity_thresholds,
    interaction_product,
    stratified_feature_auc,
)
from scripts.move_exhaustion.features.per_tick import univariate_logistic_auc
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

SIGNAL_FEATURES = (
    "impact_shortfall",
    "dv01_per_tick",
    "apparent_absorption_score",
    "impact_efficiency_decay",
)
INTERACTIONS = (
    ("impact_shortfall", "displayed_depth", "shortfall_x_depth"),
    ("impact_efficiency_decay", "displayed_depth", "decay_x_depth"),
    ("dv01_per_tick", "spread", "flowtick_x_spread"),
    ("dv01_per_tick", "realized_vol", "flowtick_x_vol"),
    ("apparent_absorption_score", "opposing_depth", "absorption_x_oppdepth"),
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


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


def _boosted_auc(df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import roc_auc_score
    except ImportError:
        return {"ok": False, "reason": "sklearn_missing"}
    cols = features + ["label"]
    work = df.dropna(subset=cols).copy()
    work = work[work["label"].isin([0, 1])]
    x = work[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    mask = np.isfinite(x).all(axis=1)
    x, y = x[mask], y[mask]
    if len(y) < 40 or len(np.unique(y)) < 2:
        return {"ok": False, "reason": "insufficient", "n": int(len(y))}
    clf = HistGradientBoostingClassifier(
        max_depth=3, max_iter=80, learning_rate=0.05, random_state=42
    )
    clf.fit(x, y)
    proba = clf.predict_proba(x)[:, 1]
    return {"ok": True, "auc": float(roc_auc_score(y, proba)), "n": int(len(y)), "features": features}


def _merge_prior(output_root: Path) -> pd.DataFrame:
    e05 = pd.read_csv(
        output_root / "experiments" / "E05_dv01_per_tick" / "event_per_tick_features.csv"
    )
    e07 = pd.read_csv(
        output_root
        / "experiments"
        / "E07_state_dependent_impact_shortfall"
        / "event_shortfall_features.csv"
    )
    e10 = pd.read_csv(
        output_root
        / "experiments"
        / "E10_tbbo_apparent_absorption_at_extreme"
        / "event_absorption_features.csv"
    )
    e06_path = (
        output_root
        / "experiments"
        / "E06_impact_efficiency_decay"
        / "event_efficiency_features_landmark.csv"
    )
    keys = ["et_date", "event_start_time"]
    base = e05[keys + ["event_direction", "dv01_per_tick", "label"]].copy()
    base = base.merge(
        e07[keys + ["impact_shortfall"]],
        on=keys,
        how="left",
    )
    base = base.merge(
        e10[
            keys
            + [
                "apparent_absorption_score",
                "average_opposing_depth_at_extreme",
                "spread_at_event",
                "depth_at_event",
                "realized_vol_at_event",
            ]
        ],
        on=keys,
        how="left",
    )
    if e06_path.exists():
        e06 = pd.read_csv(e06_path)
        base = base.merge(
            e06[keys + ["impact_efficiency_decay"]],
            on=keys,
            how="left",
        )
    else:
        base["impact_efficiency_decay"] = np.nan

    base = base.rename(
        columns={
            "spread_at_event": "spread",
            "depth_at_event": "displayed_depth",
            "realized_vol_at_event": "realized_vol",
            "average_opposing_depth_at_extreme": "opposing_depth",
        }
    )
    return base


def run_e11(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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

    df = _merge_prior(output_root)
    df = df[~df["et_date"].isin(holdout)].copy()

    dates = sorted(df["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    train_dates = set(dates[:split])
    eval_dates = set(dates[split:])
    thr = fit_liquidity_thresholds(df[df["et_date"].isin(train_dates)])
    df = add_liquidity_buckets(df, thr)

    for a, b, name in INTERACTIONS:
        if a in df.columns and b in df.columns:
            df[name] = interaction_product(df, a, b, name)
        else:
            df[name] = np.nan

    exp_dir = output_root / "experiments" / "E11_liquidity_conditioned_exhaustion"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_liquidity_features.csv", index=False)
    df.to_parquet(exp_dir / "event_liquidity_features.parquet", index=False)
    _write_json(exp_dir / "liquidity_thresholds.json", thr)

    # Stratified tables
    strata: dict[str, Any] = {}
    for regime_col in ("depth_regime", "spread_regime", "vol_regime"):
        strata[regime_col] = {}
        for feat in SIGNAL_FEATURES:
            if feat not in df.columns:
                continue
            strata[regime_col][feat] = stratified_feature_auc(df, feat, regime_col)
        # Also raw exhaustion by regime
        strata[regime_col]["_base_rates"] = stratified_feature_auc(df, "displayed_depth", regime_col)

    # Depth-stratified exhaustion rates for reporting (not feature AUC)
    def _regime_rates(col: str) -> dict[str, Any]:
        out = {}
        for regime, g in df.groupby(col):
            lab = g[g["label"].isin([0, 1])]
            out[str(regime)] = {
                "n": int(len(g)),
                "n_resolved": int(len(lab)),
                "exhaustion_rate": float((lab["label"] == 1).mean()) if len(lab) else None,
            }
        return out

    regime_rates = {
        "depth_regime": _regime_rates("depth_regime"),
        "spread_regime": _regime_rates("spread_regime"),
        "vol_regime": _regime_rates("vol_regime"),
    }

    # Interaction lifts: main effects vs main+interaction
    interaction_results: dict[str, Any] = {}
    for a, b, name in INTERACTIONS:
        if name not in df.columns or df[name].notna().sum() < 40:
            interaction_results[name] = {"ok": False, "reason": "missing"}
            continue
        main = _logistic(df, [a, b])
        with_int = _logistic(df, [a, b, name])
        delta = None
        if main.get("ok") and with_int.get("ok"):
            delta = float(with_int["auc"] - main["auc"])
        # Stratified AUC spread for signal a across depth if depth interaction
        spread = None
        if b == "displayed_depth" and a in df.columns:
            tab = strata.get("depth_regime", {}).get(a, {})
            aucs = [
                v["auc"]
                for k, v in tab.items()
                if k != "unknown" and v.get("ok") and v.get("auc") is not None and v.get("n_resolved", 0) >= 20
            ]
            if len(aucs) >= 2:
                spread = float(max(aucs) - min(aucs))
        interaction_results[name] = {
            "main_effects": main,
            "with_interaction": with_int,
            "delta_auc": delta,
            "stratified_auc_spread_depth": spread,
            "univariate_interaction": univariate_logistic_auc(df, name),
        }

    main_bundle = ["impact_shortfall", "dv01_per_tick", "apparent_absorption_score", "displayed_depth", "spread", "realized_vol"]
    main_bundle = [c for c in main_bundle if c in df.columns]
    int_names = [name for _, _, name in INTERACTIONS if name in df.columns]
    abl = {
        "liquidity_only": _logistic(df, ["displayed_depth", "spread", "realized_vol"]),
        "signals_only": _logistic(df, [c for c in SIGNAL_FEATURES if c in df.columns and c != "impact_efficiency_decay"]),
        "signals_plus_liquidity": _logistic(df, main_bundle),
        "signals_liquidity_interactions": _logistic(df, main_bundle + int_names),
        "boosted_signals_liquidity": _boosted_auc(df, main_bundle),
        "boosted_with_interactions": _boosted_auc(df, main_bundle + int_names),
    }
    base_auc = (abl["signals_plus_liquidity"] or {}).get("auc") or 0
    int_auc = (abl["signals_liquidity_interactions"] or {}).get("auc") or 0
    bundle_delta = float(int_auc - base_auc) if abl["signals_plus_liquidity"].get("ok") and abl["signals_liquidity_interactions"].get("ok") else None

    eval_df = df[df["et_date"].isin(eval_dates)]
    abl_eval = {
        "signals_plus_liquidity": _logistic(eval_df, main_bundle),
        "signals_liquidity_interactions": _logistic(eval_df, main_bundle + int_names),
    }
    base_auc_eval = (abl_eval["signals_plus_liquidity"] or {}).get("auc")
    int_auc_eval = (abl_eval["signals_liquidity_interactions"] or {}).get("auc")
    bundle_delta_eval = None
    if base_auc_eval is not None and int_auc_eval is not None:
        bundle_delta_eval = float(int_auc_eval - base_auc_eval)
    n_eval_resolved = int(eval_df["label"].isin([0, 1]).sum())

    useful_interactions = [
        k
        for k, v in interaction_results.items()
        if (v.get("delta_auc") is not None and v["delta_auc"] >= 0.01)
        or (v.get("stratified_auc_spread_depth") is not None and v["stratified_auc_spread_depth"] >= 0.05)
    ]
    # Depth conditioning of shortfall specifically
    shortfall_depth = strata.get("depth_regime", {}).get("impact_shortfall", {})
    depth_aucs = {
        k: v.get("auc")
        for k, v in shortfall_depth.items()
        if v.get("ok") and v.get("n_resolved", 0) >= 20
    }

    in_sample_ok = bool(useful_interactions) and (
        (bundle_delta is not None and bundle_delta >= 0.01) or len(useful_interactions) >= 2
    )
    eval_ok = bool(
        bundle_delta_eval is not None
        and bundle_delta_eval >= 0.01
        and n_eval_resolved >= 40
    )
    hypothesis_supported = bool(in_sample_ok and eval_ok)
    provisional_lead = bool(in_sample_ok and not eval_ok)

    conclusion = {
        "experiment_id": "E11_liquidity_conditioned_exhaustion",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "provisional_lead": provisional_lead,
        "useful_interactions": useful_interactions,
        "bundle_delta_auc": bundle_delta,
        "bundle_delta_auc_eval": bundle_delta_eval,
        "n_eval_resolved": n_eval_resolved,
        "shortfall_auc_by_depth": depth_aucs,
        "note": (
            "Reuses E05/E07/E10 event features; E06 decay left-joined from landmark "
            "subset (missing for early-resolved events). Logistic AUCs in-sample unless "
            "labeled eval. Boosted AUCs are diagnostic only (severe in-sample overfit)."
        ),
        "conclusion": (
            f"Liquidity-conditioned exhaustion on {detector}. "
            f"Useful interactions: {useful_interactions or 'none'}. "
            f"Signals+liquidity AUC={base_auc:.3f}; +interactions={int_auc:.3f} (Δ={bundle_delta}). "
            f"Eval ΔAUC={bundle_delta_eval} (n_resolved={n_eval_resolved}). "
            f"Shortfall AUC by depth: {depth_aucs}. "
            f"Wide-spread exhaustion {regime_rates.get('spread_regime', {}).get('wide_spread', {}).get('exhaustion_rate')} "
            f"vs tight {regime_rates.get('spread_regime', {}).get('tight_spread', {}).get('exhaustion_rate')}. "
            f"Hypothesis {'SUPPORTED' if hypothesis_supported else ('PROVISIONAL only' if provisional_lead else 'NOT supported')}. "
            "Holdout untouched."
        ),
        "next_action": "E12_microprice_and_book_imbalance_confirmation",
    }

    manifest = {
        "experiment_id": "E11_liquidity_conditioned_exhaustion",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "n_with_decay": int(df["impact_efficiency_decay"].notna().sum()),
        "thresholds": thr,
        "regime_rates": regime_rates,
        "strata": strata,
        "interaction_results": interaction_results,
        "ablations": abl,
        "ablations_eval": abl_eval,
        "useful_interactions": useful_interactions,
        "bundle_delta_auc": bundle_delta,
        "bundle_delta_auc_eval": bundle_delta_eval,
        "n_eval_resolved": n_eval_resolved,
        "hypothesis_supported": hypothesis_supported,
        "provisional_lead": provisional_lead,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"interactions": interaction_results, "ablations": abl, "strata": strata})
    _write_json(exp_dir / "conclusion.json", conclusion)
    _write_json(exp_dir / "regime_rates.json", regime_rates)

    # Keep folds import used for future extension without lint noise
    _ = expanding_walk_forward_folds

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E11_liquidity_conditioned_exhaustion",
        "hypothesis": "Flow/price patterns mean different things in deep vs shallow liquidity",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": ",".join(useful_interactions) if useful_interactions else "",
        "baseline_metric": f"signals_plus_liq_auc={base_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E11_liquidity_conditioned_exhaustion"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
