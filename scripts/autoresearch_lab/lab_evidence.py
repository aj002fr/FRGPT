"""Dashboard-ready projections of immutable campaign evidence."""

from __future__ import annotations

from typing import Any

from .lab_integrity import integrity_snapshot
from .lab_readers import load_campaign_artifacts


def _gate(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passes": bool(passed), "detail": detail}


def predictive_gates(cell: dict[str, Any]) -> list[dict[str, Any]]:
    utility = cell.get("utility") or {}
    return [
        _gate(
            "Magnitude",
            cell["effect"] >= 0.005 and cell["relative_brier_skill"] >= 0.02,
            f"Δ={cell['effect']:.6f}; relative skill={cell['relative_brier_skill']:.4f}",
        ),
        _gate("Holm p", cell["p_adjusted"] <= 0.05, f"p={cell['p_adjusted']:.4f}"),
        _gate("CI lower", cell["ci_low"] > 0, f"lower={cell['ci_low']:.6f}"),
        _gate("Shift null", cell["shift_p"] <= 0.05, f"p={cell['shift_p']:.4f}"),
        _gate(
            "Monthly consistency",
            cell["month_count"] >= 4 and cell["positive_month_fraction"] >= 0.60,
            f"{cell['positive_month_fraction']:.0%} positive across {cell['month_count']} months",
        ),
        _gate(
            "Concentration",
            cell["positive_effect_concentration"] <= 0.25,
            f"largest-date share={cell['positive_effect_concentration']:.1%}",
        ),
        _gate(
            "Utility",
            bool(utility.get("passes")),
            f"coverage={utility.get('coverage', 0):.1%}; "
            f"n={utility.get('selected_episodes', 0)}; "
            f"lift={utility.get('target_rate_lift', 0):.1%}; "
            f"CI lower={utility.get('ci_low', 0):.3f}",
        ),
        _gate(
            "Required slices",
            bool(cell.get("stability_passes")),
            "No material or significant registered-slice reversal required",
        ),
    ]


def _d001_cells(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for raw in bundle["result_payload"]["map"]["cells"]:
        proxy, bucket, session, volatility, horizon = raw["cell_id"].split("|")
        cells.append(
            {
                **raw,
                "proxy": proxy,
                "proxy_bucket": bucket,
                "session": session,
                "volatility_bucket": volatility,
                "horizon_minutes": int(horizon),
            }
        )
    return cells


def _predictive_experiment(
    artifacts: dict[str, Any], experiment: str
) -> dict[str, Any]:
    results = artifacts["results"]
    discovery = results[f"{experiment}_discovery"]
    validation = results[f"{experiment}_validation"]
    quarantine = results[f"{experiment}_quarantine"]
    validation_payload = validation["result_payload"]
    cells = []
    for cell in validation_payload["evaluation"]["cells"]:
        cells.append({**cell, "gates": predictive_gates(cell)})
    return {
        "experiment_id": experiment,
        "discovery": discovery["result_payload"],
        "validation": {**validation_payload, "evaluation": {"cells": cells}},
        "quarantine": quarantine["result_payload"],
        "scorecard": validation["scorecard"],
        "hashes": {
            "base_spec_hash": validation["base_spec_hash"],
            "effective_spec_hash": validation["effective_spec_hash"],
            "code_manifest_hash": validation["code_manifest_hash"],
            "run_identity_hash": validation["run_identity_hash"],
        },
    }


def evidence_payload() -> dict[str, Any]:
    artifacts = load_campaign_artifacts()
    results = artifacts["results"]
    split = artifacts["manifests"]["split_manifest_v2"]
    d001 = results["D001_discovery"]
    return {
        "success": True,
        "label": "FROZEN CAMPAIGN EVIDENCE / READ-ONLY",
        "branching": results["research_branching_v1"],
        "experiments": {
            "D001": {
                "result": d001["result_payload"],
                "cells": _d001_cells(d001),
                "nominations": d001["result_payload"]["map"]["nominations"],
                "hashes": {
                    "base_spec_hash": d001["base_spec_hash"],
                    "effective_spec_hash": d001["effective_spec_hash"],
                    "code_manifest_hash": d001["code_manifest_hash"],
                    "run_identity_hash": d001["run_identity_hash"],
                },
            },
            "E001": _predictive_experiment(artifacts, "E001"),
            "E002": _predictive_experiment(artifacts, "E002"),
        },
        "cohorts": {
            "discovery": len(split["discovery_dates"]),
            "validation": len(split["validation_dates"]),
            "quarantine": len(split["historical_quarantine_dates"]),
            "excluded": len(split["excluded_dates"]),
            "prospective": split["prospective_confirmation"],
        },
        "ledger": artifacts["ledger"],
        "integrity": integrity_snapshot(),
        "preflight": artifacts["preflight"],
        "registry": artifacts["registry"],
    }
