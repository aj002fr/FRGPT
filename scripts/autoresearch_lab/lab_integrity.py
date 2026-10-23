"""Digest and provenance checks for frozen campaign artifacts."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from .lab_readers import (
    CAMPAIGN_ROOT,
    DECLARED_STATE_DB,
    MANIFESTS_ROOT,
    PROJECT_ROOT,
    REGISTRY_ROOT,
    RESULTS_ROOT,
    STATE_DB,
    load_json,
)


def digest_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    path: Path,
    expected: str | None,
    category: str,
) -> None:
    exists = path.exists()
    actual = digest_file(path) if exists else None
    checks.append(
        {
            "name": name,
            "category": category,
            "path": str(path),
            "expected": expected,
            "actual": actual,
            "status": "pass" if exists and expected == actual else "fail",
        }
    )


def _spec_checks(checks: list[dict[str, Any]]) -> None:
    spec = load_json(MANIFESTS_ROOT / "spec_manifest.json")
    references = (
        ("split", spec["split_manifest"], spec["split_sha256"]),
        ("event", spec["event_manifest"], spec["event_sha256"]),
        ("exposure", spec["exposure_manifest"], spec["exposure_sha256"]),
    )
    for label, filename, expected in references:
        _check(
            checks,
            name=f"spec_manifest:{label}",
            path=MANIFESTS_ROOT / filename,
            expected=expected,
            category="manifest",
        )
    for filename, metadata in spec["specs"].items():
        _check(
            checks,
            name=f"registered_spec:{filename}",
            path=REGISTRY_ROOT / "experiments" / filename,
            expected=metadata["file_sha256"],
            category="spec",
        )


def _authorization_checks(checks: list[dict[str, Any]]) -> None:
    records = (
        "discovery_authorization_v8.json",
        "validation_authorization_v3.json",
        "quarantine_authorization_v1.json",
    )
    for filename in records:
        record = load_json(MANIFESTS_ROOT / filename)
        _check(
            checks,
            name=f"authorization:{filename}",
            path=MANIFESTS_ROOT / record["manifest"],
            expected=record["manifest_sha256"],
            category="authorization",
        )


def _execution_checks(checks: list[dict[str, Any]]) -> None:
    manifest = load_json(MANIFESTS_ROOT / "execution_manifest_v5.json")
    for relative, expected in manifest["code_files"].items():
        _check(
            checks,
            name=f"code:{relative}",
            path=PROJECT_ROOT / relative,
            expected=expected,
            category="code",
        )
    for experiment, metadata in manifest["discovery_results"].items():
        _check(
            checks,
            name=f"discovery_result:{experiment}",
            path=RESULTS_ROOT / f"{experiment}_discovery.json",
            expected=metadata["sha256"],
            category="result",
        )
    for experiment, metadata in manifest["validation_results"].items():
        _check(
            checks,
            name=f"validation_result:{experiment}",
            path=RESULTS_ROOT / f"{experiment}_validation.json",
            expected=metadata["sha256"],
            category="result",
        )
    _check(
        checks,
        name="execution_chain:v5_previous",
        path=MANIFESTS_ROOT / manifest["previous_manifest_name"],
        expected=manifest["previous_manifest_hash"],
        category="manifest_chain",
    )


def _branch_checks(checks: list[dict[str, Any]]) -> None:
    branching = load_json(RESULTS_ROOT / "research_branching_v1.json")
    for key, expected in branching["input_sha256"].items():
        experiment, split = key.split("_", 1)
        _check(
            checks,
            name=f"branch_input:{key}",
            path=RESULTS_ROOT / f"{experiment}_{split}.json",
            expected=expected,
            category="branching",
        )


def _publication_checks(checks: list[dict[str, Any]]) -> None:
    execution = load_json(MANIFESTS_ROOT / "execution_manifest_v5.json")
    version_root = (
        PROJECT_ROOT
        / "workspace"
        / "analysis"
        / "aggression_autoresearch_data"
        / "versions"
        / execution["data_version"]
    )
    publication_path = version_root / "manifest.json"
    _check(
        checks,
        name="publication_manifest",
        path=publication_path,
        expected=execution["publication_manifest_hash"],
        category="data",
    )
    publication = load_json(publication_path, version_root)
    matrix_path = version_root / "tbbo_feature_matrix.parquet"
    checks.append(
        {
            "name": "publication_matrix_metadata",
            "category": "data",
            "path": str(matrix_path),
            "expected": execution["data_hash"],
            "actual": publication.get("matrix_sha256"),
            "status": (
                "pass"
                if matrix_path.exists()
                and publication.get("matrix_sha256") == execution["data_hash"]
                else "fail"
            ),
            "note": "Metadata binding only; the 64 MB parquet is not browser-loaded.",
        }
    )


def _ledger_finding() -> dict[str, Any]:
    exposure = load_json(MANIFESTS_ROOT / "exposure_manifest_v2.json")
    declared = Path(exposure["ledger_database"])
    same = declared.resolve(strict=False) == STATE_DB.resolve(strict=False)
    return {
        "status": "pass" if same else "warning",
        "declared_database": str(declared),
        "actual_database": str(STATE_DB),
        "declared_exists": DECLARED_STATE_DB.exists(),
        "message": (
            "Manifest ledger path matches runtime ledger."
            if same
            else "Manifest names the empty freeze stub; frozen runners use campaign.sqlite."
        ),
    }


@lru_cache(maxsize=1)
def integrity_snapshot() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    _spec_checks(checks)
    _authorization_checks(checks)
    _execution_checks(checks)
    _branch_checks(checks)
    _publication_checks(checks)
    passed = sum(check["status"] == "pass" for check in checks)
    failed = sum(check["status"] == "fail" for check in checks)
    return {
        "success": failed == 0,
        "status": "pass" if failed == 0 else "fail",
        "passed": passed,
        "failed": failed,
        "checks": checks,
        "ledger_path_finding": _ledger_finding(),
        "campaign_root": str(CAMPAIGN_ROOT),
    }


def verify_single(path: str | Path, expected: str) -> dict[str, Any]:
    """Uncached helper used by tests and one-off tamper checks."""
    actual = digest_file(path) if Path(path).exists() else None
    return {
        "path": str(path),
        "expected": expected,
        "actual": actual,
        "status": "pass" if actual == expected else "fail",
    }
