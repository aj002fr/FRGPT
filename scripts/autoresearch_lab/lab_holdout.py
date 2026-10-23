"""Read-only explanations of why no holdout can be opened from the lab."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .lab_prereg import PREREG_ROOT
from .lab_readers import load_campaign_artifacts, load_json


def _registered_status(artifacts: dict[str, Any], experiment: str) -> dict[str, Any]:
    runs = [
        row for row in artifacts["ledger"]["runs"]
        if row["experiment"] == experiment
    ]
    branch = artifacts["results"]["research_branching_v1"]["branches"][experiment]
    reasons: list[str] = []
    if experiment == "D001":
        reasons.extend(
            [
                "D001 is descriptive and confirmation.eligible=false.",
                "The frozen validation CLI explicitly rejects D001.",
                "Its nominations require new predictive specs and independent cohorts.",
            ]
        )
    else:
        completed = sorted(
            {row["split"] for row in runs if row["status"] == "COMPLETED"}
        )
        reasons.extend(
            [
                f"Completed immutable run identities already exist for: {', '.join(completed)}.",
                f"Branching action is {branch['action']}; retuning on exposed dates is false.",
                "No confirmation manifest or spec-bound owner approval exists.",
                "The prospective confirmation cohort currently contains zero dates.",
            ]
        )
    return {
        "experiment_id": experiment,
        "can_execute": False,
        "status": "unavailable",
        "reasons": reasons,
        "completed_runs": runs,
    }


def _draft_status(path: Path) -> dict[str, Any]:
    summary = load_json(path / "preregistration.json", PREREG_ROOT)
    return {
        "experiment_id": summary["experiment_id"],
        "can_execute": False,
        "status": "frozen_draft_unavailable",
        "reasons": [
            summary["execution_blocker"],
            summary["holdout_precheck"]["reason"],
            "The emitted manifest is a non-authorizing scaffold.",
        ],
        "directory": str(path),
    }


def holdout_availability() -> dict[str, Any]:
    artifacts = load_campaign_artifacts()
    registered = [
        _registered_status(artifacts, experiment)
        for experiment in ("D001", "E001", "E002")
    ]
    drafts = []
    if PREREG_ROOT.exists():
        for path in sorted(PREREG_ROOT.iterdir()):
            if path.is_dir() and (path / "preregistration.json").exists():
                drafts.append(_draft_status(path))
    hypotheses = artifacts["registry"]["hypotheses"]["hypotheses"]
    blocked = [
        {
            "hypothesis_id": row["hypothesis_id"],
            "family": row["family"],
            "reason": row["notes"],
        }
        for row in hypotheses
        if row["status"] == "blocked"
    ]
    return {
        "success": True,
        "execution_endpoint_present": False,
        "all_holdouts_guarded": True,
        "registered": registered,
        "drafts": drafts,
        "blocked_hypotheses": blocked,
        "global_reasons": [
            "The lab exposes no validation, quarantine, or confirmation execution endpoint.",
            "Only discovery_matrix().load(split='discovery') is available to lab compute.",
            "Any future holdout requires an append-only authorized manifest outside this lab.",
        ],
    }
