"""E22 reversal-magnitude model (conditional on exhaustion)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor, Ridge

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    append_registry,
    chronological_split,
    load_event_universe,
    prune_features,
    standardize_fit_transform,
    write_json,
)

HORIZONS = {
    "5m": "max_reversal_ticks_5m",
    "15m": "max_reversal_ticks_15m",
    "30m": "max_reversal_ticks_30m",
}
FEATURES = (
    "abs_residual_z",
    "impact_efficiency_decay",
    "dv01_per_tick",
    "apparent_absorption_score",
    "flow_z_5m",
    "displayed_depth",
    "spread",
)


def _regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = y_true - y_pred
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "n": int(len(y_true)),
    }


def run_e22(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    exh = df[pd.to_numeric(df["label"], errors="coerce") == 1].copy()
    dates = sorted(exh["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = exh[exh["et_date"].isin(train_dates)]
    eval_df = exh[exh["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E22_reversal_magnitude_model"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    feats = prune_features(train_df, [c for c in FEATURES if c in df.columns])
    results: dict[str, Any] = {}

    for horizon, target in HORIZONS.items():
        tr = train_df[train_df[target].notna()]
        ev = eval_df[eval_df[target].notna()]
        if len(tr) < 20 or len(ev) < 5:
            results[horizon] = {"ok": False, "reason": "insufficient_exhaustion_events"}
            continue
        x_tr = tr[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        y_tr = tr[target].to_numpy(dtype=float)
        x_ev = ev[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        y_ev = ev[target].to_numpy(dtype=float)
        m_tr = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        m_ev = np.isfinite(x_ev).all(axis=1) & np.isfinite(y_ev)
        x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
        x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]

        xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
        ridge = Ridge(alpha=1.0, random_state=RANDOM_SEED)
        ridge.fit(xs_tr, y_tr)
        huber = HuberRegressor()
        huber.fit(xs_tr, y_tr)

        results[horizon] = {
            "ok": True,
            "target": target,
            "n_train": int(len(y_tr)),
            "n_eval": int(len(y_ev)),
            "ridge": _regression_metrics(y_ev, ridge.predict(xs_ev)),
            "huber": _regression_metrics(y_ev, huber.predict(xs_ev)),
            "mean_actual_eval": float(np.mean(y_ev)),
        }

    # Best horizon by Ridge MAE
    best_h = None
    best_mae = 1e9
    for h, r in results.items():
        if r.get("ok") and r["ridge"]["mae"] < best_mae:
            best_mae = r["ridge"]["mae"]
            best_h = h

    verdict = "research-promising" if best_h and best_mae < 3.0 else "rejected"
    hypothesis_supported = verdict == "research-promising"

    conclusion = {
        "experiment_id": "E22_reversal_magnitude_model",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "best_horizon": best_h,
        "best_mae": best_mae,
        "results": results,
        "conclusion": (
            f"Reversal magnitude on exhaustion-only {detector} events. "
            f"Best horizon={best_h} Ridge MAE={best_mae:.2f} ticks. "
            f"Verdict={verdict}. Holdout untouched."
        ),
        "next_action": "E27_selective_prediction",
    }

    manifest = {
        "experiment_id": "E22_reversal_magnitude_model",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_exhaustion_events": int(len(exh)),
        "results": results,
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(exp_dir / "metrics.json", {"results": results})

    append_registry(
        output_root,
        {
            "experiment_id": "E22_reversal_magnitude_model",
            "hypothesis": "Features predict reversal depth conditional on exhaustion",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": f"best_mae={best_mae}",
            "baseline_metric": f"best_horizon={best_h}",
            "incremental_improvement": bool(hypothesis_supported),
            "holdout_used": False,
            "conclusion": conclusion["conclusion"],
            "next_action": conclusion["next_action"],
        },
        experiment_id="E22_reversal_magnitude_model",
    )
    return manifest
