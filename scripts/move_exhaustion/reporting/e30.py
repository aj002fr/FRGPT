"""E30 stability and drift diagnostics (no holdout scoring)."""

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
    chronological_split,
    classification_metrics,
    feature_set_columns,
    load_event_universe,
    prune_features,
    resolved_mask,
    standardize_fit_transform,
    write_json,
)
from scripts.move_exhaustion.simulation.trading import date_pnl_concentration


def _psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float | None:
    expected = expected[np.isfinite(expected)]
    actual = actual[np.isfinite(actual)]
    if len(expected) < 20 or len(actual) < 20:
        return None
    qs = np.linspace(0, 1, bins + 1)
    breaks = np.unique(np.quantile(expected, qs))
    if len(breaks) < 3:
        return None
    e_counts = np.histogram(expected, bins=breaks)[0].astype(float)
    a_counts = np.histogram(actual, bins=breaks)[0].astype(float)
    e_pct = (e_counts + 1e-6) / (e_counts.sum() + 1e-6 * len(e_counts))
    a_pct = (a_counts + 1e-6) / (a_counts.sum() + 1e-6 * len(a_counts))
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


def run_e30(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, holdout = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    df = df[~df["et_date"].isin(holdout)].copy()
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = df[df["et_date"].isin(train_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E30_stability_and_drift"
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
    p_ev = clf.predict_proba(xs_ev)[:, 1]
    ev = ev.copy()
    ev["p_exh"] = p_ev
    ev["y"] = y_ev

    # Slice performance
    ev["week"] = pd.to_datetime(ev["et_date"]).dt.to_period("W").astype(str)
    ev["month"] = pd.to_datetime(ev["et_date"]).dt.to_period("M").astype(str)
    if "event_start_time" in ev.columns:
        ev["tod_hour"] = pd.to_datetime(ev["event_start_time"], utc=True, errors="coerce").dt.hour
    else:
        ev["tod_hour"] = -1

    def _group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, g in frame.groupby(key):
            if len(g) < 5 or g["y"].nunique() < 2:
                out[str(k)] = {"n": int(len(g)), "ok": False}
                continue
            out[str(k)] = classification_metrics(g["y"].to_numpy(), g["p_exh"].to_numpy())
        return out

    by_week = _group_metrics(ev, "week")
    by_month = _group_metrics(ev, "month")
    by_tod = _group_metrics(ev, "tod_hour")
    by_vol = {}
    if "is_high_volatility" in ev.columns:
        by_vol = _group_metrics(ev, "is_high_volatility")
    by_detector = {}
    if "event_detector" in ev.columns:
        by_detector = _group_metrics(ev, "event_detector")
    by_size = {}
    if "abs_move_z_5m" in ev.columns:
        try:
            ev["move_size_bin"] = pd.qcut(
                pd.to_numeric(ev["abs_move_z_5m"], errors="coerce"),
                3,
                duplicates="drop",
            )
            by_size = _group_metrics(ev.dropna(subset=["move_size_bin"]), "move_size_bin")
        except (ValueError, TypeError):
            by_size = {"ok": False, "reason": "qcut_failed"}

    # Feature PSI train vs eval for top features
    psi_rows = {}
    for feat in feats[:20]:
        psi_rows[feat] = _psi(
            pd.to_numeric(tr[feat], errors="coerce").to_numpy(),
            pd.to_numeric(ev[feat], errors="coerce").to_numpy(),
        )
    high_psi = {k: v for k, v in psi_rows.items() if v is not None and v > 0.25}

    # Prediction drift
    p_tr = clf.predict_proba(xs_tr)[:, 1]
    pred_drift = {
        "train_mean_p": float(p_tr.mean()),
        "eval_mean_p": float(p_ev.mean()),
        "train_std_p": float(p_tr.std()),
        "eval_std_p": float(p_ev.std()),
        "mean_shift": float(p_ev.mean() - p_tr.mean()),
    }

    # Calibration by period
    cal_by_month = {}
    for m, g in ev.groupby("month"):
        if len(g) < 8:
            continue
        cal_by_month[str(m)] = {
            "n": int(len(g)),
            "base_rate": float(g["y"].mean()),
            "mean_p": float(g["p_exh"].mean()),
            "gap": float(g["p_exh"].mean() - g["y"].mean()),
        }

    # E26 PnL concentration if artifact exists
    e26_path = output_root / "experiments" / "E26_trading_policy_simulation" / "eval_trades.parquet"
    e26_conc = None
    if e26_path.exists():
        trades = pd.read_parquet(e26_path)
        needed = {"policy", "exit_mode", "latency_bars", "entered", "net_ticks", "et_date"}
        if needed.issubset(trades.columns):
            sub = trades[
                (trades["policy"] == "P2")
                & (trades["exit_mode"] == "fixed_15m")
                & (trades["latency_bars"] == 1)
                & (trades["entered"] == True)  # noqa: E712
            ]
            if sub.empty:
                sub = trades[trades["entered"] == True]  # noqa: E712
            if len(sub):
                by_date = sub.groupby("et_date")["net_ticks"].sum()
                e26_conc = date_pnl_concentration(by_date)

    # Instability flags
    aucs = [v.get("roc_auc") for v in by_week.values() if v.get("ok") and v.get("roc_auc") is not None]
    auc_spread = float(max(aucs) - min(aucs)) if len(aucs) >= 2 else None
    flags = {
        "high_feature_psi": bool(high_psi),
        "large_week_auc_spread": bool(auc_spread is not None and auc_spread > 0.25),
        "e26_date_concentration": bool((e26_conc or {}).get("flag_gt_20pct")),
        "prediction_mean_shift_gt_0_1": bool(abs(pred_drift["mean_shift"]) > 0.10),
    }
    unstable = any(flags.values())
    verdict = "unstable" if unstable else "stable_enough"
    conclusion_text = (
        f"Stability/drift on {detector} eval. Flags={flags}. "
        f"Week AUC spread={auc_spread}. High-PSI features={list(high_psi)[:5]}. "
        f"E26 conc={e26_conc}. Verdict={verdict}. Holdout untouched."
    )
    metrics = {
        "by_week": by_week,
        "by_month": by_month,
        "by_tod_hour": by_tod,
        "by_vol": by_vol,
        "by_detector": by_detector,
        "by_move_size": by_size,
        "feature_psi": psi_rows,
        "high_psi_features": high_psi,
        "prediction_drift": pred_drift,
        "calibration_by_month": cal_by_month,
        "e26_date_concentration": e26_conc,
        "flags": flags,
        "week_auc_spread": auc_spread,
    }
    write_json(exp_dir / "metrics.json", metrics)
    conclusion = {
        "experiment_id": "E30_stability_and_drift",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": not unstable,
        "flags": flags,
        "conclusion": conclusion_text,
        "next_action": "final_research_report",
    }
    manifest = {
        "experiment_id": "E30_stability_and_drift",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "verdict": verdict,
        "metrics": metrics,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    append_registry(
        output_root,
        {
            "experiment_id": "E30_stability_and_drift",
            "hypothesis": "Signal stable across week/TOD/vol without date concentration",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": str(flags),
            "baseline_metric": f"auc_spread={auc_spread}",
            "incremental_improvement": not unstable,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "final_research_report",
        },
        experiment_id="E30_stability_and_drift",
    )
    return manifest
