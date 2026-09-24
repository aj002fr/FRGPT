"""E20 competing-risks exhaustion vs continuation hazards."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    LABEL_HORIZON_MINUTES,
    append_registry,
    chronological_split,
    classification_metrics,
    expand_hazard_intervals,
    feature_set_columns,
    load_event_universe,
    load_holdout_dates,
    prune_features,
    resolved_mask,
    standardize_fit_transform,
    write_json,
)
from scripts.move_exhaustion.reporting.e18 import _fit_logistic, _prepare_xy

HAZARD_FEATURES = ("event_age_min", "event_age_min_sq", "abs_residual_z", "impact_efficiency_decay", "dv01_per_tick")


def _cumulative_incidence(hazard_df: pd.DataFrame, cause: str, *, horizon: int) -> float:
    """Average cumulative incidence of cause by horizon across events."""
    incidences = []
    for _, grp in hazard_df.groupby("event_id"):
        ages = grp["event_age_min"].to_numpy(dtype=float)
        y = grp[cause].to_numpy(dtype=int)
        mask = ages <= horizon
        if mask.any() and y[mask].any():
            incidences.append(1.0)
        elif mask.any():
            incidences.append(0.0)
    return float(np.mean(incidences)) if incidences else 0.0


def run_e20(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    holdout = load_holdout_dates(output_root)
    events_df = load_event_feature_matrix(output_root)
    dates = sorted(events_df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]

    exp_dir = output_root / "experiments" / "E20_competing_risks_model"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for _, ev in events_df.iterrows():
        event = ev.to_dict()
        for row in expand_hazard_intervals(event, prices, horizon_minutes=LABEL_HORIZON_MINUTES):
            age = row["event_age_min"]
            rows.append(
                {
                    **row,
                    "event_age_min_sq": age**2,
                    "abs_residual_z": ev.get("abs_residual_z"),
                    "impact_efficiency_decay": ev.get("impact_efficiency_decay"),
                    "dv01_per_tick": ev.get("dv01_per_tick"),
                }
            )
    hz = pd.DataFrame(rows)
    hz_train = hz[hz["et_date"].isin(train_dates)]
    hz_eval = hz[hz["et_date"].isin(eval_dates)]

    feats = prune_features(hz_train, list(HAZARD_FEATURES))
    cause_models: dict[str, Any] = {}
    for cause, ycol in (("exhaustion", "y_exhaustion"), ("continuation", "y_continuation")):
        x_tr = hz_train[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        y_tr = hz_train[ycol].to_numpy(dtype=int)
        x_ev = hz_eval[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        y_ev = hz_eval[ycol].to_numpy(dtype=int)
        m_tr = np.isfinite(x_tr).all(axis=1)
        m_ev = np.isfinite(x_ev).all(axis=1)
        x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
        x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]
        xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
        clf = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_SEED)
        clf.fit(xs_tr, y_tr)
        p_ev = clf.predict_proba(xs_ev)[:, 1]
        cause_models[cause] = classification_metrics(y_ev, p_ev)

    cum_inc = {
        "exhaustion_5m": _cumulative_incidence(hz_eval, "y_exhaustion", horizon=5),
        "exhaustion_15m": _cumulative_incidence(hz_eval, "y_exhaustion", horizon=15),
        "continuation_5m": _cumulative_incidence(hz_eval, "y_continuation", horizon=5),
        "continuation_15m": _cumulative_incidence(hz_eval, "y_continuation", horizon=15),
    }

    unresolved_rate = float((events_df["label"] == -1).mean())

    # Compare to E18 resolved-only classifier on same eval dates
    train_df = events_df[events_df["et_date"].isin(train_dates)]
    eval_df = events_df[events_df["et_date"].isin(eval_dates)]
    fcols = prune_features(train_df, feature_set_columns("F3", events_df))
    x_tr, y_tr, _ = _prepare_xy(train_df, fcols)
    x_ev, y_ev, _ = _prepare_xy(eval_df, fcols)
    e18_proba, _ = _fit_logistic(x_tr, y_tr, x_ev)
    e18_metrics = classification_metrics(y_ev, e18_proba)

    use_competing = unresolved_rate >= 0.15
    verdict = "research-promising" if use_competing and (
        (cause_models.get("exhaustion") or {}).get("pr_auc", 0) > (e18_metrics.get("pr_auc") or 0)
    ) else "rejected"
    if unresolved_rate < 0.15:
        verdict = "rejected_prefer_e18" if e18_metrics.get("pr_auc", 0) > 0.52 else "rejected"
    hypothesis_supported = verdict == "research-promising"

    conclusion = {
        "experiment_id": "E20_competing_risks_model",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "unresolved_rate": unresolved_rate,
        "cumulative_incidence_eval": cum_inc,
        "cause_models": cause_models,
        "e18_resolved_classifier": e18_metrics,
        "conclusion": (
            f"Competing risks on {detector}. Unresolved={unresolved_rate:.1%}. "
            f"Cum incidence={cum_inc}. E18 resolved PR-AUC={e18_metrics.get('pr_auc')}. "
            f"Verdict={verdict}. Holdout untouched."
        ),
        "next_action": "E21_additional_extension_quantile_model",
    }

    manifest = {
        "experiment_id": "E20_competing_risks_model",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "unresolved_rate": unresolved_rate,
        "cumulative_incidence_eval": cum_inc,
        "cause_models": cause_models,
        "e18_comparison": e18_metrics,
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(exp_dir / "metrics.json", manifest)

    append_registry(
        output_root,
        {
            "experiment_id": "E20_competing_risks_model",
            "hypothesis": "Competing risks help when unresolved is common",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": str(cum_inc),
            "baseline_metric": str(e18_metrics),
            "incremental_improvement": bool(hypothesis_supported),
            "holdout_used": False,
            "conclusion": conclusion["conclusion"],
            "next_action": conclusion["next_action"],
        },
        experiment_id="E20_competing_risks_model",
    )
    return manifest
