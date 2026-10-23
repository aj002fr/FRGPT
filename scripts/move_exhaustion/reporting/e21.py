"""E21 additional-extension quantile model."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import QuantileRegressor

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    append_registry,
    chronological_split,
    feature_set_columns,
    load_event_universe,
    pinball_loss,
    prune_features,
    quantile_coverage,
    standardize_fit_transform,
    write_json,
)

QUANTILES = (0.5, 0.8, 0.9)
TARGET = "extension_ticks_15m"
REGRESSOR_FEATURES = (
    "abs_move_z_5m",
    "abs_residual_z",
    "flow_z_5m",
    "impact_efficiency_decay",
    "dv01_per_tick",
    "curve_directional_flow_factor",
    "displayed_depth",
    "spread",
)


def _fit_quantile_linear(
    x_tr: np.ndarray, y_tr: np.ndarray, x_ev: np.ndarray, q: float
) -> np.ndarray:
    xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
    reg = QuantileRegressor(quantile=q, alpha=1.0, solver="highs")
    reg.fit(xs_tr, y_tr)
    return reg.predict(xs_ev)


def _fit_quantile_hgb(x_tr: np.ndarray, y_tr: np.ndarray, x_ev: np.ndarray, q: float) -> np.ndarray:
    reg = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=q,
        max_depth=3,
        min_samples_leaf=max(5, int(0.05 * len(y_tr))),
        random_state=RANDOM_SEED,
    )
    reg.fit(x_tr, y_tr)
    return reg.predict(x_ev)


def run_e21(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    df = df[df[TARGET].notna()].copy()
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E21_additional_extension_quantile_model"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    feats = prune_features(train_df, [c for c in REGRESSOR_FEATURES if c in df.columns])
    if len(feats) < 2:
        feats = prune_features(train_df, feature_set_columns("F3", df))

    x_tr = train_df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = train_df[TARGET].to_numpy(dtype=float)
    x_ev = eval_df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_ev = eval_df[TARGET].to_numpy(dtype=float)
    m_tr = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
    m_ev = np.isfinite(x_ev).all(axis=1) & np.isfinite(y_ev)
    x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
    x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]

    results: dict[str, Any] = {"linear": {}, "hgb": {}}
    for q in QUANTILES:
        pred_lin = _fit_quantile_linear(x_tr, y_tr, x_ev, q)
        pred_hgb = _fit_quantile_hgb(x_tr, y_tr, x_ev, q)
        results["linear"][str(q)] = {
            "pinball": pinball_loss(y_ev, pred_lin, q),
            "coverage": quantile_coverage(y_ev, pred_lin),
            "mean_pred": float(np.mean(pred_lin)),
            "mean_actual": float(np.mean(y_ev)),
        }
        results["hgb"][str(q)] = {
            "pinball": pinball_loss(y_ev, pred_hgb, q),
            "coverage": quantile_coverage(y_ev, pred_hgb),
            "mean_pred": float(np.mean(pred_hgb)),
            "mean_actual": float(np.mean(y_ev)),
        }

    cov80 = results["linear"]["0.8"]["coverage"]
    rejected_coverage = cov80 < 0.70  # materially below 80%
    verdict = "rejected" if rejected_coverage else "research-promising"
    hypothesis_supported = verdict == "research-promising"

    conclusion = {
        "experiment_id": "E21_additional_extension_quantile_model",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "target": TARGET,
        "coverage_80_linear": cov80,
        "rejected_poor_coverage": rejected_coverage,
        "results": results,
        "conclusion": (
            f"Extension quantiles on {detector}. 80% coverage (linear)={cov80:.1%} "
            f"(need ~80%). Verdict={verdict}. Holdout untouched."
        ),
        "next_action": "E22_reversal_magnitude_model",
    }

    manifest = {
        "experiment_id": "E21_additional_extension_quantile_model",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_train": int(len(y_tr)),
        "n_eval": int(len(y_ev)),
        "features": feats,
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
            "experiment_id": "E21_additional_extension_quantile_model",
            "hypothesis": "Quantile extension bounds achieve ~80% coverage OOS",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": f"coverage_80={cov80}",
            "baseline_metric": f"pinball_80={results['linear']['0.8']['pinball']}",
            "incremental_improvement": bool(hypothesis_supported),
            "holdout_used": False,
            "conclusion": conclusion["conclusion"],
            "next_action": conclusion["next_action"],
        },
        experiment_id="E21_additional_extension_quantile_model",
    )
    return manifest
