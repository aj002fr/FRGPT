"""E00 data + causality audit runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.loader import join_v2_panel, load_tbbo_matrix
from scripts.move_exhaustion.data.quality import (
    aggressor_sign_validation,
    assert_no_forbidden_writes,
    daily_summary,
    row_level_flags,
)
from scripts.move_exhaustion.data.sessions import assert_day_open_matches_first_price
from scripts.move_exhaustion.validation.purging import expanding_walk_forward_folds


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def run_e00(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    """Build joined panel, run quality audits, write E00 artifacts."""
    features = load_tbbo_matrix()
    panel, meta = join_v2_panel(features=features)
    flagged = row_level_flags(panel)
    daily = daily_summary(flagged)
    sign = aggressor_sign_validation(flagged)
    day_open = assert_day_open_matches_first_price(features)
    dates = sorted(flagged["et_date"].dropna().unique().tolist())
    # Spec-style freeze: last ~15% untouched; do not evaluate holdout now.
    n_hold = max(1, int(round(0.15 * len(dates))))
    holdout_dates = dates[-n_hold:]
    dev_dates = dates[:-n_hold]
    folds = expanding_walk_forward_folds(dev_dates, train_min=min(30, max(10, len(dev_dates) // 2)), val_block=5)

    exp_dir = output_root / "experiments" / "E00_data_causality_audit"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_root / "datasets" / "v2_joined_panel_1m.parquet"
    assert_no_forbidden_writes(dataset_path)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    flagged.to_parquet(dataset_path, index=False)
    daily.to_csv(exp_dir / "daily_summary.csv", index=False)

    manifest = {
        "experiment_id": "E00_data_causality_audit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "random_seed": RANDOM_SEED,
        "data_version": {
            "tbbo_matrix": "workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv",
            "walkforward": "workspace/analysis/aggression_v2_tbbo_models/walkforward_predictions.csv",
            "joined_panel": str(dataset_path),
        },
        "join_meta": meta,
        "n_dates": len(dates),
        "dev_dates_n": len(dev_dates),
        "holdout_dates_n": len(holdout_dates),
        "holdout_dates": holdout_dates,
        "holdout_unlocked": False,
        "folds_n": len(folds),
        "sign_validation": sign,
        "day_open_audit": day_open,
        "isolation": {
            "dashboards_untouched": True,
            "outputs_only_under": str(output_root),
        },
        "pass_criteria": {
            "join_nonempty": meta["rows_merged"] > 0,
            "sign_ok": bool(sign.get("ok")),
            "day_open_ok": bool(day_open.get("ok")),
            "holdout_locked": True,
        },
    }
    manifest["status"] = (
        "passed"
        if all(
            [
                manifest["pass_criteria"]["join_nonempty"],
                manifest["pass_criteria"]["sign_ok"],
                manifest["pass_criteria"]["day_open_ok"],
            ]
        )
        else "failed"
    )
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(
        exp_dir / "metrics.json",
        {
            "rows_merged": meta["rows_merged"],
            "n_clean_dates": meta["n_clean_dates"],
            "sign_edge": sign.get("edge"),
            "daily_dates": int(len(daily)),
        },
    )
    conclusion = {
        "experiment_id": "E00_data_causality_audit",
        "conclusion": (
            "Joined V2 ridge residual panel is available for Phase 1 on the legacy "
            "1m / 01:00 ET session convention. Sign check and day_open audit recorded. "
            "Final holdout dates listed but not inspected for modelling."
        ),
        "status": manifest["status"],
        "next_action": "E01_event_detector_comparison",
        "blockers": []
        if manifest["status"] == "passed"
        else ["Fix failed E00 pass criteria before modelling."],
    }
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    registry.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "experiment_id": "E00_data_causality_audit",
        "hypothesis": "Dataset is causal and joinable to V2 residuals",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": manifest["status"],
        "primary_metric": sign.get("edge"),
        "baseline_metric": "",
        "incremental_improvement": "",
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    pd.DataFrame([row]).to_csv(registry, index=False)
    return manifest
