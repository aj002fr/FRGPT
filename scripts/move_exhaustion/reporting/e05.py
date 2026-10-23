"""E05 contracts / DV01 per tick vs exhaustion."""

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
    event_path_per_tick,
    per_tick_features,
    univariate_logistic_auc,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds

FEATURES = (
    "dv01_per_tick",
    "contracts_per_tick",
    "trades_per_tick",
    "event_dv01_per_tick",
    "flow_since_last_new_extreme",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _monotonic_score(curve: pd.DataFrame) -> dict[str, Any]:
    """Spearman-like rank correlation of decile order vs exhaustion rate."""
    if curve.empty or len(curve) < 3:
        return {"ok": False, "spearman": None}
    x = curve["decile"].to_numpy(dtype=float)
    y = curve["exhaustion_rate"].to_numpy(dtype=float)
    if np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return {"ok": False, "spearman": 0.0}
    # Pearson on ranks
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    spe = float(np.corrcoef(rx, ry)[0, 1])
    return {
        "ok": True,
        "spearman": spe,
        "increasing": spe > 0.3,
        "decreasing": spe < -0.3,
    }


def run_e05(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
        feats = per_tick_features(panel, event)
        path = event_path_per_tick(panel, event)
        lab = first_barrier_label(
            prices,
            start_ts=_ts(event["event_start_time"]),
            direction=int(event["event_direction"]),
            R_ticks=4,
            C_ticks=4,
            H=pd.Timedelta(minutes=15),
            tick_size=TICK_SIZE["TY"],
        )
        rows.append({**event, **feats, **path, "label": lab.get("label")})
    df = pd.DataFrame(rows)

    exp_dir = output_root / "experiments" / "E05_dv01_per_tick"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_per_tick_features.csv", index=False)
    df.to_parquet(exp_dir / "event_per_tick_features.parquet", index=False)

    feature_results: dict[str, Any] = {}
    for feat in FEATURES:
        curve = decile_exhaustion_curve(df, feat)
        curve.to_csv(exp_dir / f"decile_{feat}.csv", index=False)
        mono = _monotonic_score(curve)
        logistic = univariate_logistic_auc(df, feat)

        # Fold stability: coef sign on expanding folds
        dates = sorted(df["et_date"].dropna().unique().tolist())
        folds = expanding_walk_forward_folds(dates, train_min=min(30, max(15, len(dates) // 2)), val_block=5)
        fold_coefs = []
        for fold in folds:
            train = df[df["et_date"].isin(fold["train_dates"])]
            res = univariate_logistic_auc(train, feat)
            if res.get("ok"):
                fold_coefs.append(res.get("coef_standardized", res.get("corr")))
        sign_stable = False
        if len(fold_coefs) >= 2:
            signs = [np.sign(c) for c in fold_coefs if c is not None and np.isfinite(c) and c != 0]
            sign_stable = len(signs) >= 2 and len(set(signs)) == 1

        feature_results[feat] = {
            "decile_curve": curve.to_dict(orient="records"),
            "monotonicity": mono,
            "logistic": logistic,
            "fold_coefs": fold_coefs,
            "sign_stable_across_folds": sign_stable,
        }

    # Success: stable interpretable relationship
    successes = [
        f
        for f, r in feature_results.items()
        if r["monotonicity"].get("ok")
        and (r["monotonicity"].get("increasing") or r["monotonicity"].get("decreasing"))
        and r.get("sign_stable_across_folds")
        and (r["logistic"].get("auc") or 0) >= 0.53
    ]
    # Soft lead if mono but not fully stable
    leads = [
        f
        for f, r in feature_results.items()
        if r["monotonicity"].get("ok")
        and abs(r["monotonicity"].get("spearman") or 0) >= 0.3
    ]

    best = None
    if successes:
        best = max(successes, key=lambda f: feature_results[f]["logistic"].get("auc") or 0)
    elif leads:
        best = max(leads, key=lambda f: abs(feature_results[f]["monotonicity"].get("spearman") or 0))

    conclusion = {
        "experiment_id": "E05_dv01_per_tick",
        "status": "completed",
        "event_universe": detector,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "hypothesis_supported": bool(successes),
        "causality_note": (
            "Path features use pre-event 10m lookback only at event start "
            "(forward event-horizon path removed after leak check)."
        ),
        "conclusion": (
            f"DV01/contracts-per-tick study on {detector}. "
            f"Strict success features: {successes or 'none'}. "
            f"Soft leads (|spearman|>=0.3): {leads or 'none'}. "
            f"Best: {best}. "
            f"{'Supports' if successes else 'Does not clearly support'} "
            "rising flow-per-tick → exhaustion on causal start-time features. "
            "Holdout untouched."
        ),
        "next_action": "E06_impact_efficiency_decay",
    }
    manifest = {
        "experiment_id": "E05_dv01_per_tick",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "feature_results": feature_results,
        "success_features": successes,
        "lead_features": leads,
        "best_feature": best,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", feature_results)
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E05_dv01_per_tick",
        "hypothesis": "Higher DV01 per tick raises exhaustion probability",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best,
        "baseline_metric": "",
        "incremental_improvement": bool(successes),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
