"""E23 MBP-1 refill experiment — unavailable in this environment."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.reporting.ml_common import append_registry, write_json

MBP1_DIR = Path(r"D:\FRDBs\FutMBP\GLBX-20260403-CBPK5YKK7C-mbp1")


def run_e23(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    exp_dir = output_root / "experiments" / "E23_mbp1_refill"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    exists = MBP1_DIR.exists()
    n_parquet = len(list(MBP1_DIR.glob("**/*.parquet"))) if exists else 0
    unavailable = (not exists) or n_parquet == 0

    conclusion_text = (
        "MBP-1 refill experiment UNAVAILABLE. "
        f"Folder {MBP1_DIR} exists={exists} but contains {n_parquet} parquet files. "
        "No refill_ratio / refill_latency / touch-survival features were fabricated. "
        "Incremental value over TBBO cannot be measured. Holdout untouched."
    )
    conclusion = {
        "experiment_id": "E23_mbp1_refill",
        "status": "unavailable",
        "verdict": "unavailable",
        "hypothesis_supported": False,
        "data_available": False,
        "mbp1_dir": str(MBP1_DIR),
        "n_parquet": n_parquet,
        "conclusion": conclusion_text,
        "next_action": "E24_mbp10_book_shape",
    }
    manifest = {
        "experiment_id": "E23_mbp1_refill",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "unavailable",
        "holdout_used": False,
        "verdict": "unavailable",
        "hypothesis_supported": False,
        "mbp1_dir": str(MBP1_DIR),
        "folder_exists": exists,
        "n_parquet": n_parquet,
        "features_computed": False,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(
        exp_dir / "metrics.json",
        {"unavailable": True, "n_parquet": n_parquet, "reason": "zero_parquet_in_mbp1_folder"},
    )
    append_registry(
        output_root,
        {
            "experiment_id": "E23_mbp1_refill",
            "hypothesis": "Fast opposing-side MBP-1 refill predicts exhaustion",
            "data_tier": "MBP-1",
            "status": "unavailable",
            "primary_metric": "n_parquet=0",
            "baseline_metric": "TBBO_only",
            "incremental_improvement": False,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "E24_mbp10_book_shape",
        },
        experiment_id="E23_mbp1_refill",
    )
    assert unavailable
    return manifest
