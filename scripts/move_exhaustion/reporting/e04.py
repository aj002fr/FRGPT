"""E04 flow-dominance category study."""

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
from scripts.move_exhaustion.features.flow_categories import (
    additional_extension_ticks,
    classify_flow_dominance,
    event_flow_price_features,
    fit_category_thresholds,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _category_stats(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"n": 0}
    labels = pd.to_numeric(df["label"], errors="coerce")
    resolved = df[labels.isin([0, 1])]
    n_res = len(resolved)
    n_ex = int((resolved["label"] == 1).sum())
    return {
        "n": int(len(df)),
        "n_resolved": n_res,
        "exhaustion_rate": (n_ex / n_res) if n_res else None,
        "continuation_rate": ((n_res - n_ex) / n_res) if n_res else None,
        "unresolved_rate": float((labels == -1).mean()) if len(labels) else None,
        "median_initial_move_ticks": float(pd.to_numeric(df["initial_move_ticks"], errors="coerce").median()),
        "median_additional_extension_ticks": float(
            pd.to_numeric(df["additional_extension_ticks"], errors="coerce").median()
        ),
        "median_time_to_outcome_min": float(
            pd.to_numeric(df["time_to_outcome_min"], errors="coerce").median()
        ),
        "median_abs_flow": float(pd.to_numeric(df["abs_flow"], errors="coerce").median()),
        "median_impact_efficiency": float(
            pd.to_numeric(df["impact_efficiency"], errors="coerce").median()
        ),
    }


def run_e04(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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

    # Chronological train thresholds: first 70% of dev dates
    dates = sorted(events["et_date"].unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    train_dates = set(dates[:split])
    thr_dates = set(dates[split:])  # used only for reporting split, thresholds from train

    feat_rows = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        feats = event_flow_price_features(panel, event)
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
        time_to = None
        if lab.get("hit_ts"):
            time_to = (_ts(lab["hit_ts"]) - _ts(event["event_start_time"])).total_seconds() / 60.0
        feat_rows.append(
            {
                **event,
                **feats,
                "additional_extension_ticks": ext,
                "label": lab.get("label"),
                "first_barrier": lab.get("first_barrier"),
                "time_to_outcome_min": time_to,
                "split": "train_thresh" if event["et_date"] in train_dates else "eval",
            }
        )
    feat_df = pd.DataFrame(feat_rows)
    thr = fit_category_thresholds(feat_df[feat_df["split"] == "train_thresh"])
    feat_df["category"] = [
        classify_flow_dominance(r, thr) for r in feat_df.to_dict(orient="records")
    ]

    exp_dir = output_root / "experiments" / "E04_flow_dominance_categories"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    feat_df.to_csv(exp_dir / "event_categories.csv", index=False)
    feat_df.to_parquet(exp_dir / "event_categories.parquet", index=False)

    # Report on eval split primarily; also overall for context
    by_cat_eval = {
        cat: _category_stats(g)
        for cat, g in feat_df[feat_df["split"] == "eval"].groupby("category")
    }
    by_cat_all = {cat: _category_stats(g) for cat, g in feat_df.groupby("category")}

    # Usefulness: spread in exhaustion rates across categories on eval
    rates = [
        v["exhaustion_rate"]
        for v in by_cat_eval.values()
        if v.get("exhaustion_rate") is not None and (v.get("n_resolved") or 0) >= 10
    ]
    spread = (max(rates) - min(rates)) if len(rates) >= 2 else None
    useful = bool(spread is not None and spread >= 0.08)

    # Rank categories by exhaustion rate (eval)
    ranked = sorted(
        ((k, v) for k, v in by_cat_eval.items() if v.get("exhaustion_rate") is not None),
        key=lambda kv: kv[1]["exhaustion_rate"],
        reverse=True,
    )

    conclusion = {
        "experiment_id": "E04_flow_dominance_categories",
        "status": "completed",
        "event_universe": detector,
        "useful_spread": useful,
        "exhaustion_rate_spread_eval": spread,
        "highest_exhaustion_category": ranked[0][0] if ranked else None,
        "lowest_exhaustion_category": ranked[-1][0] if ranked else None,
        "conclusion": (
            f"Train-quantile flow-dominance categories on {detector}. "
            f"Eval exhaustion-rate spread={spread}. "
            f"{'Categories separate exhaustion rates (>=8pp)' if useful else 'Weak/no separation on eval (<8pp or thin cells)'}. "
            f"Highest exhaustion: {ranked[0][0] if ranked else None}. "
            "Thresholds fit on first 70% of dev dates only. Holdout untouched. "
            "Full state-dependent impact model deferred to E07."
        ),
        "next_action": "E05_contracts_or_dv01_per_tick",
    }
    manifest = {
        "experiment_id": "E04_flow_dominance_categories",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(feat_df)),
        "train_dates_n": len(train_dates),
        "eval_dates_n": len(thr_dates),
        "thresholds": thr,
        "by_category_eval": by_cat_eval,
        "by_category_all": by_cat_all,
        "useful_spread": useful,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"eval": by_cat_eval, "all": by_cat_all, "thresholds": thr})
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E04_flow_dominance_categories",
        "hypothesis": "Flow vs price impact categories separate exhaustion rates",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": spread,
        "baseline_metric": "",
        "incremental_improvement": useful,
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
