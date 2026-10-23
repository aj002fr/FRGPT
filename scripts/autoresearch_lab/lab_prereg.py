"""Preregistration authoring without holdout execution or campaign writes."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import yaml

from scripts.autoresearch.fixed_env.preregister import (
    atomic_write,
    canonical_spec_hash,
    digest_file,
    digest_object,
    effective_spec_hash,
    freeze_json,
)
from scripts.autoresearch.schemas import (
    ExecutionContractV1,
    PredictiveExperimentSpecV2,
    require_id,
)

from .lab_journal import journal_excerpt
from .lab_readers import (
    MANIFESTS_ROOT,
    PROJECT_ROOT,
    STATE_DB,
    load_json,
    load_registry,
    open_ledger_readonly,
)

PREREG_ROOT = (
    PROJECT_ROOT / "workspace" / "analysis" / "autoresearch_lab" / "preregistrations"
)


def _hypothesis(hypothesis_id: str) -> dict[str, Any]:
    registry = load_registry()["hypotheses"]["hypotheses"]
    matches = [row for row in registry if row["hypothesis_id"] == hypothesis_id]
    if not matches:
        raise ValueError("hypothesis_id is not registered")
    hypothesis = matches[0]
    if hypothesis["status"] == "blocked":
        raise PermissionError(f"{hypothesis_id} is blocked: {hypothesis['notes']}")
    return hypothesis


def _cohort_dates(split: str) -> tuple[str, ...]:
    manifest = load_json(MANIFESTS_ROOT / "split_manifest_v2.json")
    keys = {
        "validation": "validation_dates",
        "quarantine": "historical_quarantine_dates",
        "prospective": None,
    }
    if split not in keys:
        raise ValueError("holdout_split must be validation, quarantine, or prospective")
    key = keys[split]
    if key is None:
        return tuple(manifest["prospective_confirmation"]["dates"])
    return tuple(manifest[key])


def exposure_precheck(
    *,
    hypothesis_family: str,
    target: str,
    holdout_split: str,
) -> dict[str, Any]:
    require_id(hypothesis_family, "hypothesis_family")
    dates = _cohort_dates(holdout_split)
    if not dates:
        return {
            "available": False,
            "split": holdout_split,
            "date_count": 0,
            "exposed_rows": [],
            "reason": "No dates currently exist in the requested holdout cohort.",
            "database": str(STATE_DB),
        }
    placeholders = ",".join("?" for _ in dates)
    sql = (
        "SELECT trading_date,target,hypothesis_family,experiment,split,run_id "
        f"FROM exposure_ledger WHERE trading_date IN ({placeholders}) "
        "AND target=? AND hypothesis_family=? ORDER BY trading_date"
    )
    with open_ledger_readonly() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                sql, (*dates, target, hypothesis_family)
            ).fetchall()
        ]
    return {
        "available": not rows,
        "split": holdout_split,
        "date_count": len(dates),
        "exposed_rows": rows,
        "reason": (
            "Cohort is untouched for this target and semantic family."
            if not rows
            else f"{len(rows)} date-target-family exposures already exist."
        ),
        "database": str(STATE_DB),
    }


def _outcome_contract(result: Mapping[str, Any], claim: str) -> dict[str, Any]:
    outcome = result["outcome"]
    name = str(outcome["name"]).upper()
    return {
        "name": name,
        "definition": claim,
        "horizon_minutes": int(outcome["horizon_minutes"]),
        "censoring": (
            "Date/session anchors spaced by the horizon; require at least 80 percent "
            "future-minute coverage and an endpoint within two minutes; no session crossing."
        ),
        "boundary_policy": (
            "Parameters and exact-touch behavior are frozen in the linked execution contract."
        ),
    }


def _analysis_contract(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": {
            "baseline_features": list(result["baseline_model"]["feature_names"]),
            "augmented_feature": result["treatment_feature"],
            "metric": "equal-date baseline Brier minus augmented Brier",
            "discovery_search_digest": result["search_digest"],
        },
        "inference": {
            "alpha": 0.05,
            "multiplicity": "Holm over every registered cell with fixed denominator",
            "discovery_resamples": 999,
            "validation_confirmation_resamples": 9999,
            "serial_falsification": "within-session circular shift of at least 30 minutes",
        },
        "power": {
            "absolute_sesoi_brier": 0.005,
            "relative_brier_skill_minimum": 0.02,
            "minimum_dates": 25,
            "minimum_class_count_each": 30,
            "target_power": 0.80,
        },
        "utility": {
            "minimum_coverage": 0.10,
            "minimum_selected_episodes": 30,
            "minimum_target_rate_lift": 0.08,
            "required_ci": "clustered lower confidence bound above zero",
        },
        "diagnostics": [
            "Report calibration, per-date effects, monthly signs, and concentration.",
            "Keep all discovery searching and holdout evidence explicitly separated.",
        ],
    }


def build_spec(
    request: Mapping[str, Any],
    result: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    experiment_id = require_id(str(request["experiment_id"]), "experiment_id")
    if experiment_id in {"D001", "E001", "E002"}:
        raise ValueError("a preregistration needs a new experiment_id")
    hypothesis = _hypothesis(str(request["hypothesis_id"]))
    claim = str(request.get("claim") or "").strip()
    if not claim:
        raise ValueError("claim is required")
    execution = load_json(MANIFESTS_ROOT / "execution_manifest_v5.json")
    cells = {"ALL": "Every eligible anchor under the frozen candidate and outcome."}
    if any(
        value not in (None, "", "ALL", "any")
        for value in (result.get("slice") or {}).values()
    ):
        cells["FILTERED"] = json.dumps(result["slice"], sort_keys=True)
    hashes = {
        "data_hash": execution["data_hash"],
        "split_hash": execution["split_hash"],
        "exposure_hash": execution["exposure_hash"],
        "spec_hash": "0" * 64,
        "evaluator_hash": execution["code_files"][
            "scripts/autoresearch/fixed_env/evaluator.py"
        ],
        "candidate_code_hash": result["candidate_source_sha256"],
        "state_code_hash": result["candidate_source_sha256"],
    }
    spec = {
        "schema_version": 2,
        "experiment_id": experiment_id,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "hypothesis_family": hypothesis["family"],
        "status": "frozen",
        "hashes": hashes,
        "candidate": {
            "allowed_inputs": list(result["allowed_inputs"]),
            "registered_feature_map": dict(result["feature_map"]),
        },
        "design": {
            "grids": {
                name: [value] for name, value in result["config"].items()
            },
            "selection_rule": (
                "Single configuration selected from the linked, fully journaled discovery "
                "search; no holdout retuning or adaptive cell selection."
            ),
            "cells": cells,
            "cells_may_overlap": True,
        },
        "outcome": _outcome_contract(result, claim),
        "analysis": _analysis_contract(result),
        "attempt_cap": 5,
        "confirmation": {
            "eligible": True,
            "policy": (
                "Prospective-only confirmation after an independent power audit and separate "
                "spec-bound owner approval; no second cohort."
            ),
            "prospective_only": True,
            "approval_required": True,
        },
    }
    spec["hashes"]["spec_hash"] = canonical_spec_hash(spec)
    PredictiveExperimentSpecV2.from_dict(spec)
    contract = _execution_contract(spec, result)
    return spec, contract


def _execution_contract(
    spec: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    contract = {
        "schema_version": 1,
        "experiment_id": spec["experiment_id"],
        "base_spec_hash": spec["hashes"]["spec_hash"],
        "kind": "predictive",
        "candidate": {
            "source_sha256": result["candidate_source_sha256"],
            "config": result["config"],
            "allowed_inputs": result["allowed_inputs"],
            "outputs": sorted(result["feature_map"]),
            "fit_split": "discovery",
            "transform_only_on_holdout": True,
        },
        "evaluator": {
            "baseline_features": list(result["baseline_model"]["feature_names"]),
            "augmented_features": [result["treatment_feature"]],
            "imputation": "discovery_median",
            "scaling": "discovery_mean_std_unit_for_constant",
            "logit_l2": 0.000001,
            "registered_cells": list(spec["design"]["cells"]),
        },
        "selection": {
            "source_search_digest": result["search_digest"],
            "rule": "configuration frozen from append-only discovery search journal",
            "further_selection": False,
            "embargo_minutes": 30,
        },
        "outcome": {
            **result["outcome"],
            "anchor_spacing_minutes": int(result["outcome"]["horizon_minutes"]),
            "minimum_future_coverage": 0.8,
            "endpoint_tolerance_minutes": 2,
        },
    }
    ExecutionContractV1.from_dict(contract)
    effective_spec_hash(contract["base_spec_hash"], contract)
    return contract


def _freeze_yaml(path: Path, value: Mapping[str, Any]) -> str:
    content = yaml.safe_dump(
        dict(value), sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"frozen snapshot differs: {path}")
        return digest_file(path)
    return atomic_write(path, content)


def freeze_preregistration(request: Mapping[str, Any]) -> dict[str, Any]:
    result = request.get("search_result")
    if not isinstance(result, Mapping):
        raise ValueError("search_result is required")
    if (
        result.get("split") != "discovery"
        or result.get("evidentiary_status") != "non_evidentiary"
    ):
        raise PermissionError("only a non-evidentiary discovery result may be frozen")
    excerpt = journal_excerpt(str(result.get("search_digest") or ""))
    if not excerpt:
        raise ValueError("search result is absent from the append-only journal")
    expected = digest_object(result)
    if not any(row.get("result_sha256") == expected for row in excerpt):
        raise ValueError("search result differs from its journaled digest")
    spec, contract = build_spec(request, result)
    holdout = str(request.get("holdout_split") or "prospective")
    precheck = exposure_precheck(
        hypothesis_family=spec["hypothesis_family"],
        target=spec["outcome"]["name"],
        holdout_split=holdout,
    )
    if precheck["exposed_rows"]:
        raise PermissionError(precheck["reason"])
    base_hash = spec["hashes"]["spec_hash"]
    effective_hash = effective_spec_hash(base_hash, contract)
    destination = PREREG_ROOT / f"{spec['experiment_id']}_{base_hash[:12]}"
    destination.mkdir(parents=True, exist_ok=True)
    spec_hash = _freeze_yaml(destination / "spec.yaml", spec)
    contract_hash = _freeze_yaml(destination / "execution_contract.yaml", contract)
    freeze_json(destination / "journal_excerpt.json", excerpt)
    scaffold = _manifest_scaffold(
        spec,
        contract,
        effective_hash,
        spec_hash,
        contract_hash,
        holdout,
        precheck,
    )
    freeze_json(destination / "manifest_scaffold.json", scaffold)
    summary = {
        "schema_version": 1,
        "status": "FROZEN_PREREGISTRATION_NOT_AUTHORIZED",
        "experiment_id": spec["experiment_id"],
        "base_spec_hash": base_hash,
        "effective_spec_hash": effective_hash,
        "directory": str(destination),
        "spec_file_sha256": spec_hash,
        "execution_contract_file_sha256": contract_hash,
        "holdout_precheck": precheck,
        "execution_available": False,
        "execution_blocker": (
            "The frozen executor admits only D001/E001/E002 and no manifest or "
            "authorization record admits this new experiment."
        ),
        "future_cli_template": scaffold["future_cli_template"],
    }
    freeze_json(destination / "preregistration.json", summary)
    return summary


def _manifest_scaffold(
    spec: Mapping[str, Any],
    contract: Mapping[str, Any],
    effective_hash: str,
    spec_file_hash: str,
    contract_file_hash: str,
    holdout_split: str,
    precheck: Mapping[str, Any],
) -> dict[str, Any]:
    current = MANIFESTS_ROOT / "execution_manifest_v5.json"
    future_name = f"execution_manifest_{spec['experiment_id'].lower()}_v1.json"
    authorization = f"{spec['experiment_id'].lower()}_authorization_v1.json"
    command = {
        "validation": "run-validation",
        "quarantine": "run-quarantine",
    }.get(holdout_split)
    cli = (
        "py -3 -m scripts.autoresearch.fixed_env.execution "
        f"{command} --experiment {spec['experiment_id']} "
        f"--manifest {future_name} --authorization-record {authorization}"
        if command
        else "UNAVAILABLE: the frozen executor has no prospective-confirmation CLI"
    )
    return {
        "schema_version": 1,
        "append_only": True,
        "status": "SCAFFOLD_ONLY_NOT_AUTHORIZED",
        "canonical_name": future_name,
        "previous_manifest_name": current.name,
        "previous_manifest_hash": digest_file(current),
        "authorized_experiments": [],
        "authorized_scope": "none",
        "validation_authorized": False,
        "real_outcomes_authorized": False,
        "experiment": {
            "experiment_id": spec["experiment_id"],
            "base_spec_hash": spec["hashes"]["spec_hash"],
            "effective_spec_hash": effective_hash,
            "spec_file_sha256": spec_file_hash,
            "execution_contract_file_sha256": contract_file_hash,
            "candidate_source_sha256": contract["candidate"]["source_sha256"],
        },
        "holdout_precheck": dict(precheck),
        "required_before_execution": [
            "Explicit owner approval for a hash-bound manifest and authorization record.",
            "Separate approved code change admitting the new experiment ID.",
            "Independent power audit and a non-empty untouched cohort.",
            "Manifest-chain verification after every code or contract change.",
        ],
        "future_cli_template": cli,
        "cli_status": "blocked_until_executor_admission_and_owner_authorization",
    }
