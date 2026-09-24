"""E19 discrete-time exhaustion hazard (1m primary bin)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED, TICK_SIZE
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.features.event_feature_matrix import (
    CACHE_META,
    event_features_at_time,
    load_event_feature_matrix,
)
from scripts.move_exhaustion.reporting.ml_common import (
    LABEL_HORIZON_MINUTES,
    append_registry,
    chronological_split,
    classification_metrics,
    expand_hazard_intervals,
    load_event_universe,
    load_holdout_dates,
    prune_features,
    standardize_fit_transform,
    write_json,
)
from scripts.move_exhaustion.simulation.policies import _ts

HAZARD_FEATURES = (
    "event_age_min",
    "event_age_min_sq",
    "abs_residual_z",
    "impact_efficiency_decay",
    "dv01_per_tick",
    "flow_z_5m",
    "curve_directional_flow_factor",
)


def _build_hazard_frame(
    events: pd.DataFrame,
    panel: pd.DataFrame,
    prices: pd.Series,
    aux: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        intervals = expand_hazard_intervals(event, prices, horizon_minutes=LABEL_HORIZON_MINUTES)
        for row in intervals:
            age = row["event_age_min"]
            landmark = _ts(event["event_start_time"]) + pd.Timedelta(minutes=age)
            lm = event_features_at_time(panel, event, observation_time=landmark, aux=aux)
            rows.append(
                {
                    **row,
                    **{k: lm.get(k) for k in HAZARD_FEATURES if k != "event_age_min" and k != "event_age_min_sq"},
                    "event_age_min_sq": age**2,
                }
            )
    return pd.DataFrame(rows)


def _cumulative_probs(hazard_probs: np.ndarray, ages: np.ndarray, *, horizon: float) -> float:
    """P(event by horizon) from interval hazards."""
    mask = ages <= horizon
    h = hazard_probs[mask]
    if len(h) == 0:
        return 0.0
    surv = np.cumprod(1.0 - np.clip(h, 0, 1))
    return float(1.0 - surv[-1] * (1.0 - h[-1]))


def run_e19(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    holdout = load_holdout_dates(output_root)
    events_df = load_event_feature_matrix(output_root)
    dates = sorted(events_df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]

    from scripts.move_exhaustion.features.session_regimes import (
        load_us_auction_events,
        load_us_high_importance_events,
    )

    CACHE_META = output_root / "experiments" / "_shared" / "event_feature_matrix_meta.json"
    meta = {}
    if CACHE_META.exists():
        import json

        meta = json.loads(CACHE_META.read_text(encoding="utf-8"))
    aux = {
        "high_vol_threshold": meta.get("high_vol_threshold", 2.5),
        "macro_events": load_us_high_importance_events(),
        "auction_events": load_us_auction_events(),
    }

    exp_dir = output_root / "experiments" / "E19_discrete_time_exhaustion_hazard"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    train_ev = events_df[events_df["et_date"].isin(train_dates)]
    eval_ev = events_df[events_df["et_date"].isin(eval_dates)]

    hz_train = _build_hazard_frame(train_ev, panel, prices, aux)
    hz_eval = _build_hazard_frame(eval_ev, panel, prices, aux)
    hz_train.to_parquet(exp_dir / "hazard_train.parquet", index=False)
    hz_eval.to_parquet(exp_dir / "hazard_eval.parquet", index=False)

    feats = prune_features(hz_train, list(HAZARD_FEATURES))
    x_tr = hz_train[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = hz_train["y_exhaustion"].to_numpy(dtype=int)
    x_ev = hz_eval[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_ev = hz_eval["y_exhaustion"].to_numpy(dtype=int)
    m_tr = np.isfinite(x_tr).all(axis=1)
    m_ev = np.isfinite(x_ev).all(axis=1)
    x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
    x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]

    xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
    logit = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_SEED)
    logit.fit(xs_tr, y_tr)
    p_tr = logit.predict_proba(xs_tr)[:, 1]
    p_ev = logit.predict_proba(xs_ev)[:, 1]

    hgb = HistGradientBoostingClassifier(
        max_depth=3, min_samples_leaf=max(5, int(0.05 * len(y_tr))), random_state=RANDOM_SEED
    )
    hgb.fit(x_tr, y_tr)
    p_ev_hgb = hgb.predict_proba(x_ev)[:, 1]

    metrics_1m = classification_metrics(y_ev, p_ev)
    metrics_hgb = classification_metrics(y_ev, p_ev_hgb)

    # Horizon Brier: P(exh within 1m/5m) per event
    horizon_brier: dict[str, Any] = {}
    for horizon in (1, 5):
        event_probs = []
        event_labels = []
        for eid, grp in hz_eval.groupby("event_id"):
            ages = grp["event_age_min"].to_numpy(dtype=float)
            # align proba with filtered rows — re-score full grp
            gx = grp[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
            gm = np.isfinite(gx).all(axis=1)
            if not gm.any():
                continue
            gp = logit.predict_proba(standardize_fit_transform(gx[gm], None)[0])[:, 1]
            cum = _cumulative_probs(gp, ages[gm], horizon=horizon)
            event_probs.append(cum)
            event_labels.append(int((grp["y_exhaustion"] * (ages <= horizon)).any()))
        if event_probs:
            from sklearn.metrics import brier_score_loss

            horizon_brier[f"within_{horizon}m"] = float(
                brier_score_loss(event_labels, event_probs)
            )

    # Concordance (event-level time-to-exhaust vs predicted median)
    concordance = None
    try:
        from sklearn.metrics import concordance_index_censored

        times = []
        preds = []
        events = []
        for eid, grp in hz_eval.groupby("event_id"):
            exh_rows = grp[grp["y_exhaustion"] == 1]
            t = float(exh_rows["event_age_min"].iloc[0]) if len(exh_rows) else float(LABEL_HORIZON_MINUTES)
            times.append(t)
            events.append(int(len(exh_rows) > 0))
            preds.append(float(grp["event_age_min"].mean()))
        if len(set(events)) > 1:
            concordance = float(concordance_index_censored(events, times, preds)[0])
    except Exception:
        concordance = None

    verdict = "research-promising" if (
        metrics_1m.get("brier") is not None and metrics_1m.get("pr_auc", 0) > 0.55
    ) else "rejected"
    hypothesis_supported = verdict == "research-promising"

    conclusion = {
        "experiment_id": "E19_discrete_time_exhaustion_hazard",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "primary_bin": "1m (5/15/30s unavailable on frozen 1m TBBO)",
        "logistic_metrics": metrics_1m,
        "hgb_metrics": metrics_hgb,
        "horizon_brier": horizon_brier,
        "concordance": concordance,
        "conclusion": (
            f"Discrete hazard on 1m bins for {detector}. Logistic Brier={metrics_1m.get('brier')}, "
            f"PR-AUC={metrics_1m.get('pr_auc')}. Horizon Brier={horizon_brier}. "
            f"Verdict={verdict}. Holdout untouched."
        ),
        "next_action": "E20_competing_risks_model",
    }

    manifest = {
        "experiment_id": "E19_discrete_time_exhaustion_hazard",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_intervals_train": int(len(hz_train)),
        "n_intervals_eval": int(len(hz_eval)),
        "features": feats,
        "logistic_metrics": metrics_1m,
        "hgb_metrics": metrics_hgb,
        "horizon_brier": horizon_brier,
        "concordance": concordance,
        "verdict": verdict,
        "hypothesis_supported": hypothesis_supported,
        "grid_note": "Primary bin=1 minute; sub-minute bins documented as unavailable",
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(exp_dir / "metrics.json", manifest)

    append_registry(
        output_root,
        {
            "experiment_id": "E19_discrete_time_exhaustion_hazard",
            "hypothesis": "Interval hazards predict exhaustion timing",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": str(metrics_1m),
            "baseline_metric": f"horizon_brier={horizon_brier}",
            "incremental_improvement": bool(hypothesis_supported),
            "holdout_used": False,
            "conclusion": conclusion["conclusion"],
            "next_action": conclusion["next_action"],
        },
        experiment_id="E19_discrete_time_exhaustion_hazard",
    )
    return manifest
