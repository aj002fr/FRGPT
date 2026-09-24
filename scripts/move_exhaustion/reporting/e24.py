"""E24 MBP-10 book-shape incremental value vs TBBO F7 (overlap dates only)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, PROJECT_ROOT, RANDOM_SEED
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
from scripts.move_exhaustion.simulation.policies import _ts

MBP10_DIR = Path(r"D:\FRDBs\FutMBP\ZN_outrights_mbp10")
RESEARCH_FEATURES_DIR = Path(r"D:\FRDBs\FutMBP\research_features")
DEPTH_MATRIX = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_depth" / "depth_feature_matrix.csv"
)
MIN_RESOLVED = 30

# Causal-only depth columns (exclude forward refill / fwd RV from depth research matrix).
CAUSAL_DEPTH_COLS = [
    "total_depth_10",
    "bid_depth_10",
    "ask_depth_10",
    "depth_imb_10",
    "depth_z",
    "depth_chg_5m",
    "top_depth_share",
    "imb_persist_5m",
    "cancel_to_add",
    "cancel_imbalance",
]


def _mbp10_dates() -> set[str]:
    dates: set[str] = set()
    if MBP10_DIR.exists():
        for p in MBP10_DIR.glob("*.parquet"):
            raw = p.name.split("-")[2].split(".")[0]
            dates.add(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    return dates


def _research_feature_dates() -> set[str]:
    dates: set[str] = set()
    if RESEARCH_FEATURES_DIR.exists():
        for p in RESEARCH_FEATURES_DIR.glob("*_ZNM6_features.parquet"):
            if p.name.startswith("tmp"):
                continue
            raw = p.name.split("_")[0]
            if len(raw) == 8 and raw.isdigit():
                dates.add(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    return dates


def _asof_depth_features(events: pd.DataFrame, depth: pd.DataFrame) -> pd.DataFrame:
    """Causal backward as-of join of minute depth features at event_start_time."""
    depth = depth.copy()
    depth["bucket_ts"] = pd.to_datetime(depth["bucket_ts"], utc=True, errors="coerce")
    depth = depth.dropna(subset=["bucket_ts"]).sort_values(["et_date", "bucket_ts"])
    rows: list[dict[str, Any]] = []
    for _, ev in events.iterrows():
        et = str(ev["et_date"])
        start = _ts(ev["event_start_time"])
        day = depth[depth["et_date"] == et]
        hist = day[day["bucket_ts"] <= start]
        out: dict[str, Any] = {"event_id": ev["event_id"], "et_date": et}
        if hist.empty:
            for c in CAUSAL_DEPTH_COLS:
                out[f"mbp10_{c}"] = None
            direction = int(ev.get("event_direction") or 1)
            out["mbp10_opposing_depth"] = None
            out["mbp10_book_slope_proxy"] = None
            out["mbp10_signed_imb"] = None
            rows.append(out)
            continue
        snap = hist.iloc[-1]
        for c in CAUSAL_DEPTH_COLS:
            out[f"mbp10_{c}"] = float(pd.to_numeric(snap.get(c), errors="coerce"))
        direction = int(ev.get("event_direction") or 1)
        # Opposing liquidity for an up-move is ask book; for down-move is bid book.
        ask = float(pd.to_numeric(snap.get("ask_depth_10"), errors="coerce"))
        bid = float(pd.to_numeric(snap.get("bid_depth_10"), errors="coerce"))
        out["mbp10_opposing_depth"] = ask if direction > 0 else bid
        imb = float(pd.to_numeric(snap.get("depth_imb_10"), errors="coerce"))
        out["mbp10_signed_imb"] = direction * imb if np.isfinite(imb) else None
        # Slope proxy: top vs deep concentration (higher share → steeper near-touch).
        out["mbp10_book_slope_proxy"] = float(
            pd.to_numeric(snap.get("top_depth_share"), errors="coerce")
        )
        rows.append(out)
    return pd.DataFrame(rows)


def _fit_compare(
    train: pd.DataFrame, eval_df: pd.DataFrame, feats: list[str]
) -> dict[str, Any]:
    feats = prune_features(train, feats)
    if len(feats) < 2:
        return {"ok": False, "reason": "insufficient_features"}
    tr = train[resolved_mask(train)].copy()
    ev = eval_df[resolved_mask(eval_df)].copy()
    x_tr = tr[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = (tr["label"].to_numpy(dtype=int) == 1).astype(int)
    x_ev = ev[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_ev = (ev["label"].to_numpy(dtype=int) == 1).astype(int)
    m_tr = np.isfinite(x_tr).all(axis=1)
    m_ev = np.isfinite(x_ev).all(axis=1)
    x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
    x_ev, y_ev = x_ev[m_ev], y_ev[m_ev]
    if len(y_tr) < 20 or len(y_ev) < 5 or len(np.unique(y_tr)) < 2:
        return {"ok": False, "reason": "insufficient_split", "n_train": int(len(y_tr)), "n_eval": int(len(y_ev))}
    xs_tr, xs_ev, _, _ = standardize_fit_transform(x_tr, x_ev)
    clf = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_SEED)
    clf.fit(xs_tr, y_tr)
    p = clf.predict_proba(xs_ev)[:, 1]
    return {"ok": True, "features": feats, "metrics": classification_metrics(y_ev, p), "n_train": int(len(y_tr)), "n_eval": int(len(y_ev))}


def run_e24(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, holdout = load_event_universe(output_root)
    exp_dir = output_root / "experiments" / "E24_mbp10_book_shape"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    mbp_dates = _mbp10_dates() - holdout
    rf_dates = _research_feature_dates() - holdout
    df = load_event_feature_matrix(output_root)
    df = df[~df["et_date"].isin(holdout)].copy()
    overlap = sorted(set(df["et_date"].astype(str)) & mbp_dates)
    sub = df[df["et_date"].isin(overlap)].copy()
    n_resolved = int(resolved_mask(sub).sum())

    audit = {
        "mbp10_dir": str(MBP10_DIR),
        "mbp10_n_parquet": len(list(MBP10_DIR.glob("*.parquet"))) if MBP10_DIR.exists() else 0,
        "research_features_dir": str(RESEARCH_FEATURES_DIR),
        "research_feature_dates": sorted(rf_dates),
        "overlap_event_dates": overlap,
        "n_events_overlap": int(len(sub)),
        "n_resolved_overlap": n_resolved,
        "min_resolved_gate": MIN_RESOLVED,
        "depth_matrix_source": str(DEPTH_MATRIX),
        "wrote_to_frdbs": False,
    }

    if n_resolved < MIN_RESOLVED or not DEPTH_MATRIX.exists():
        status = "inconclusive"
        verdict = "inconclusive"
        conclusion_text = (
            f"MBP-10 book-shape incremental test INCONCLUSIVE. "
            f"n_resolved on overlap={n_resolved} (need >={MIN_RESOLVED}) "
            f"or depth matrix missing={not DEPTH_MATRIX.exists()}. "
            f"Overlap dates={overlap}. Holdout untouched. No features fabricated."
        )
        manifest = {
            "experiment_id": "E24_mbp10_book_shape",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "verdict": verdict,
            "hypothesis_supported": False,
            "holdout_used": False,
            "audit": audit,
            "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
        }
        write_json(exp_dir / "manifest.json", manifest)
        write_json(
            exp_dir / "conclusion.json",
            {
                "experiment_id": "E24_mbp10_book_shape",
                "status": status,
                "verdict": verdict,
                "hypothesis_supported": False,
                "conclusion": conclusion_text,
                "next_action": "E25_mbo_queue_hidden_liquidity",
            },
        )
        write_json(exp_dir / "metrics.json", {"audit": audit})
        append_registry(
            output_root,
            {
                "experiment_id": "E24_mbp10_book_shape",
                "hypothesis": "MBP-10 depth/imbalance/slope improves vs TBBO F7",
                "data_tier": "MBP-10",
                "status": status,
                "primary_metric": f"n_resolved={n_resolved}",
                "baseline_metric": "F7_TBBO",
                "incremental_improvement": False,
                "holdout_used": False,
                "conclusion": conclusion_text,
                "next_action": "E25_mbo_queue_hidden_liquidity",
            },
            experiment_id="E24_mbp10_book_shape",
        )
        return manifest

    # Read-only depth matrix; causal columns only.
    depth = pd.read_csv(DEPTH_MATRIX)
    depth = depth[depth["et_date"].isin(overlap)].copy()
    depth_feats = _asof_depth_features(sub, depth)
    joined = sub.merge(depth_feats, on=["event_id", "et_date"], how="left")
    joined.to_parquet(exp_dir / "overlap_events_with_mbp10.parquet", index=False)

    dates = sorted(joined["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    train_df = joined[joined["et_date"].isin(train_dates)]
    eval_df = joined[joined["et_date"].isin(eval_dates)]

    tbbo_feats = prune_features(train_df, feature_set_columns("F7", joined))
    mbp_cols = [c for c in joined.columns if c.startswith("mbp10_")]
    mbp_feats = prune_features(train_df, mbp_cols)
    combined = prune_features(train_df, tbbo_feats + mbp_feats)

    base = _fit_compare(train_df, eval_df, tbbo_feats)
    plus = _fit_compare(train_df, eval_df, combined)
    mbp_only = _fit_compare(train_df, eval_df, mbp_feats)

    delta_auc = None
    delta_brier = None
    if base.get("ok") and plus.get("ok"):
        delta_auc = float(plus["metrics"]["roc_auc"] - base["metrics"]["roc_auc"])
        delta_brier = float(base["metrics"]["brier"] - plus["metrics"]["brier"])

    improved = bool(
        delta_auc is not None and delta_auc >= 0.02 and delta_brier is not None and delta_brier > 0
    )
    verdict = "research-promising" if improved else "not_supported"
    conclusion_text = (
        f"MBP-10 incremental on {detector} overlap n_resolved={n_resolved} dates={len(overlap)}. "
        f"TBBO F7 ROC-AUC={(base.get('metrics') or {}).get('roc_auc')}; "
        f"F7+MBP10={(plus.get('metrics') or {}).get('roc_auc')}; "
        f"ΔAUC={delta_auc}; ΔBrier(base-plus)={delta_brier}. "
        f"Verdict={verdict}. Causal as-of only; forward refill cols excluded. Holdout untouched."
    )

    metrics = {
        "audit": audit,
        "tbbo_f7": base,
        "mbp10_only": mbp_only,
        "tbbo_plus_mbp10": plus,
        "delta_auc": delta_auc,
        "delta_brier": delta_brier,
        "mbp_features_used": mbp_feats,
    }
    manifest = {
        "experiment_id": "E24_mbp10_book_shape",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": improved,
        "holdout_used": False,
        "event_universe": detector,
        "metrics": metrics,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(
        exp_dir / "conclusion.json",
        {
            "experiment_id": "E24_mbp10_book_shape",
            "status": "completed",
            "verdict": verdict,
            "hypothesis_supported": improved,
            "delta_auc": delta_auc,
            "delta_brier": delta_brier,
            "conclusion": conclusion_text,
            "next_action": "E25_mbo_queue_hidden_liquidity",
        },
    )
    write_json(exp_dir / "metrics.json", metrics)
    append_registry(
        output_root,
        {
            "experiment_id": "E24_mbp10_book_shape",
            "hypothesis": "MBP-10 depth/imbalance/slope improves vs TBBO F7",
            "data_tier": "MBP-10",
            "status": "completed",
            "primary_metric": f"delta_auc={delta_auc}",
            "baseline_metric": str((base.get("metrics") or {})),
            "incremental_improvement": improved,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "E25_mbo_queue_hidden_liquidity",
        },
        experiment_id="E24_mbp10_book_shape",
    )
    return manifest
