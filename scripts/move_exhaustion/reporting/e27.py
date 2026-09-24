"""E27 abstention / selective prediction on E18 F7 probabilities."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    append_registry,
    apply_calibration,
    chronological_split,
    classification_metrics,
    feature_set_columns,
    load_event_universe,
    prune_features,
    resolved_mask,
    standardize_fit_transform,
    write_json,
)

TOP_PCTS = (0.05, 0.10, 0.20, 0.30, 1.0)


def _slice_metrics(y: np.ndarray, p: np.ndarray, pct: float) -> dict[str, Any]:
    n = len(y)
    if pct >= 1.0:
        mask = np.ones(n, dtype=bool)
    else:
        k = max(1, int(n * pct))
        order = np.argsort(-p)
        mask = np.zeros(n, dtype=bool)
        mask[order[:k]] = True
    ys, ps = y[mask], p[mask]
    out = classification_metrics(ys, ps) if len(ys) >= 5 and len(np.unique(ys)) >= 2 else {
        "ok": False,
        "n": int(len(ys)),
        "precision": float(ys.mean()) if len(ys) else None,
    }
    out["selected_pct"] = pct
    out["n_selected"] = int(mask.sum())
    out["precision"] = float(ys.mean()) if len(ys) else None
    # Proxy net ticks if traded fade on selected: +4 on exhaustion, -4 on continuation
    if len(ys):
        out["net_ticks_if_traded_proxy"] = float(np.where(ys == 1, 4.0, -4.0).mean())
    else:
        out["net_ticks_if_traded_proxy"] = None
    return out


def run_e27(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, _ = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E27_abstention_selective_prediction"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    feats = prune_features(train_df, feature_set_columns("F7", df))
    tr = train_df[resolved_mask(train_df)].copy()
    ev = eval_df[resolved_mask(eval_df)].copy()
    x_tr = tr[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = (tr["label"].to_numpy(dtype=int) == 1).astype(int)
    x_ev = ev[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_ev = (ev["label"].to_numpy(dtype=int) == 1).astype(int)
    m_tr = np.isfinite(x_tr).all(axis=1)
    m_ev = np.isfinite(x_ev).all(axis=1)
    x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
    x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]
    ev = ev.iloc[np.where(m_ev)[0]].copy()

    xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
    clf = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_SEED)
    clf.fit(xs_tr, y_tr)
    p_tr = clf.predict_proba(xs_tr)[:, 1]
    p_raw = clf.predict_proba(xs_ev)[:, 1]
    p_cal = apply_calibration(y_tr, p_tr, p_raw, "platt")

    by_pct = {f"top_{int(pct*100)}pct" if pct < 1 else "all": _slice_metrics(y_ev, p_cal, pct) for pct in TOP_PCTS}
    top10 = by_pct["top_10pct"]
    all_m = by_pct["all"]
    lift = None
    if top10.get("precision") is not None and all_m.get("precision") is not None:
        lift = float(top10["precision"] - all_m["precision"])

    # Regime concentration among top-decile
    k = max(1, int(0.10 * len(y_ev)))
    top_idx = np.argsort(-p_cal)[:k]
    top_events = ev.iloc[top_idx]
    regime_cols = [c for c in ("is_opening_period", "is_late_session", "is_high_volatility", "is_roll_period") if c in top_events.columns]
    regime_conc = {c: float(pd.to_numeric(top_events[c], errors="coerce").fillna(0).mean()) for c in regime_cols}

    promising = bool(lift is not None and lift >= 0.05 and (top10.get("n_selected") or 0) >= 5)
    verdict = "research-promising" if promising else "weak_selectivity"
    conclusion_text = (
        f"Selective prediction on {detector} E18 F7+Platt. "
        f"All precision={all_m.get('precision')}; top-decile precision={top10.get('precision')} "
        f"(n={top10.get('n_selected')}); lift={lift}. "
        f"Top-decile net-ticks proxy={top10.get('net_ticks_if_traded_proxy')}. "
        f"Verdict={verdict}. Holdout untouched."
    )

    metrics = {"by_pct": by_pct, "top10_vs_all_precision_lift": lift, "top10_regime_concentration": regime_conc}
    write_json(exp_dir / "metrics.json", metrics)
    pd.DataFrame(
        [{"event_id": ev.iloc[i]["event_id"], "et_date": ev.iloc[i]["et_date"], "p_cal": float(p_cal[i]), "y": int(y_ev[i])} for i in range(len(y_ev))]
    ).to_csv(exp_dir / "eval_predictions.csv", index=False)

    conclusion = {
        "experiment_id": "E27_abstention_selective_prediction",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": promising,
        "top10_precision": top10.get("precision"),
        "all_precision": all_m.get("precision"),
        "lift": lift,
        "conclusion": conclusion_text,
        "next_action": "E28_feature_family_ablation",
    }
    manifest = {
        "experiment_id": "E27_abstention_selective_prediction",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "verdict": verdict,
        "hypothesis_supported": promising,
        "metrics": metrics,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    append_registry(
        output_root,
        {
            "experiment_id": "E27_abstention_selective_prediction",
            "hypothesis": "Top-decile confidence improves precision vs all",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": f"top10_prec={top10.get('precision')}",
            "baseline_metric": f"all_prec={all_m.get('precision')}",
            "incremental_improvement": promising,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "E28_feature_family_ablation",
        },
        experiment_id="E27_abstention_selective_prediction",
    )
    return manifest
