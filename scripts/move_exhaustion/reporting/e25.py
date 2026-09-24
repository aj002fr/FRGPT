"""E25 MBO queue / hidden-liquidity experiment — unavailable."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.reporting.ml_common import append_registry, write_json


def run_e25(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    exp_dir = output_root / "experiments" / "E25_mbo_queue_hidden_liquidity"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    conclusion_text = (
        "MBO queue/hidden-liquidity experiment UNAVAILABLE. "
        "No historical MBO store is present in this environment. "
        "Cannot compare TBBO apparent absorption vs MBP-1 refill vs MBO queue absorption. "
        "Holdout untouched."
    )
    conclusion = {
        "experiment_id": "E25_mbo_queue_hidden_liquidity",
        "status": "unavailable",
        "verdict": "unavailable",
        "hypothesis_supported": False,
        "data_available": False,
        "conclusion": conclusion_text,
        "next_action": "E26_trading_policy_simulation",
    }
    manifest = {
        "experiment_id": "E25_mbo_queue_hidden_liquidity",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "unavailable",
        "holdout_used": False,
        "verdict": "unavailable",
        "hypothesis_supported": False,
        "features_computed": False,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    write_json(exp_dir / "metrics.json", {"unavailable": True, "reason": "no_mbo_store"})
    append_registry(
        output_root,
        {
            "experiment_id": "E25_mbo_queue_hidden_liquidity",
            "hypothesis": "MBO queue replenishment identifies absorption better than TBBO",
            "data_tier": "MBO",
            "status": "unavailable",
            "primary_metric": "unavailable",
            "baseline_metric": "TBBO_apparent_absorption",
            "incremental_improvement": False,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "E26_trading_policy_simulation",
        },
        experiment_id="E25_mbo_queue_hidden_liquidity",
    )
    return manifest
