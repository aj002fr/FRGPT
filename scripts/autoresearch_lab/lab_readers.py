"""Read-only campaign artifact and ledger readers for the lab."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ROOT = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_autoresearch_campaign"
)
RESULTS_ROOT = CAMPAIGN_ROOT / "results"
MANIFESTS_ROOT = CAMPAIGN_ROOT / "manifests"
INTEGRITY_ROOT = CAMPAIGN_ROOT / "integrity"
REGISTRY_ROOT = PROJECT_ROOT / "scripts" / "autoresearch" / "registry"
STATE_DB = CAMPAIGN_ROOT / "state" / "campaign.sqlite"
DECLARED_STATE_DB = CAMPAIGN_ROOT / "state" / "campaign_state.sqlite"

RESULT_NAMES = (
    "D001_discovery.json",
    "E001_discovery.json",
    "E002_discovery.json",
    "E001_validation.json",
    "E002_validation.json",
    "E001_quarantine.json",
    "E002_quarantine.json",
    "research_branching_v1.json",
)
CANONICAL_MANIFESTS = (
    "spec_manifest.json",
    "split_manifest_v2.json",
    "exposure_manifest_v2.json",
    "event_manifest.json",
    "roll_manifest_v1.json",
    "execution_manifest_v3_7.json",
    "execution_manifest_v4_2.json",
    "execution_manifest_v5.json",
)


def _contained(path: str | Path, root: Path) -> Path:
    resolved = Path(path).resolve(strict=True)
    allowed = root.resolve(strict=True)
    if resolved != allowed and allowed not in resolved.parents:
        raise PermissionError(f"path escapes read-only root: {resolved}")
    return resolved


def load_json(path: str | Path, root: Path = CAMPAIGN_ROOT) -> dict[str, Any]:
    resolved = _contained(path, root)
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {resolved}")
    return value


def load_yaml(path: str | Path, root: Path = REGISTRY_ROOT) -> dict[str, Any]:
    resolved = _contained(path, root)
    value = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected YAML mapping: {resolved}")
    return value


def open_ledger_readonly(path: str | Path = STATE_DB) -> sqlite3.Connection:
    """Open the live campaign ledger read-only, without SQLite immutable mode."""
    resolved = _contained(path, CAMPAIGN_ROOT)
    if resolved.as_posix().startswith("//"):
        uri = f"file:{resolved}?mode=ro"
    else:
        uri = f"file:{quote(resolved.as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _rows(connection: sqlite3.Connection, sql: str, args: Iterable[Any] = ()):
    return [dict(row) for row in connection.execute(sql, tuple(args)).fetchall()]


def load_ledger() -> dict[str, Any]:
    with open_ledger_readonly() as connection:
        runs = _rows(
            connection,
            "SELECT run_id,experiment,hypothesis_family,split,status,attempt,"
            "stats_emitted,output_hash,error,created_at,updated_at "
            "FROM runs ORDER BY created_at,experiment",
        )
        exposures = _rows(
            connection,
            "SELECT trading_date,target,hypothesis_family,experiment,split,run_id "
            "FROM exposure_ledger ORDER BY trading_date,target",
        )
        receipts = _rows(
            connection,
            "SELECT run_id,previous_hash,receipt_hash FROM receipts ORDER BY rowid",
        )
        preprocessing = _rows(
            connection,
            "SELECT job_id,experiment,split,status,state_hash,run_id "
            "FROM preprocessing_jobs ORDER BY created_at,job_id",
        )
    split_counts: dict[str, dict[str, int]] = {}
    for row in exposures:
        bucket = split_counts.setdefault(
            str(row["split"]), {"rows": 0, "dates": 0, "targets": 0}
        )
        bucket["rows"] += 1
    for split, bucket in split_counts.items():
        matching = [row for row in exposures if row["split"] == split]
        bucket["dates"] = len({row["trading_date"] for row in matching})
        bucket["targets"] = len({row["target"] for row in matching})
    return {
        "database": str(STATE_DB),
        "runs": runs,
        "exposures": exposures,
        "receipts": receipts,
        "preprocessing_jobs": preprocessing,
        "split_counts": split_counts,
    }


def load_registry() -> dict[str, Any]:
    experiments = {
        experiment: load_yaml(REGISTRY_ROOT / "experiments" / f"{experiment}.yaml")
        for experiment in ("D001", "E001", "E002")
    }
    contracts = {
        experiment: load_yaml(
            REGISTRY_ROOT / "execution_contracts" / f"{experiment}.yaml"
        )
        for experiment in ("D001", "E001", "E002")
    }
    return {
        "hypotheses": load_yaml(REGISTRY_ROOT / "hypotheses.yaml"),
        "experiments": experiments,
        "contracts": contracts,
    }


def load_campaign_artifacts() -> dict[str, Any]:
    results = {
        name.removesuffix(".json"): load_json(RESULTS_ROOT / name)
        for name in RESULT_NAMES
    }
    manifests = {
        name.removesuffix(".json"): load_json(MANIFESTS_ROOT / name)
        for name in CANONICAL_MANIFESTS
    }
    authorizations = {
        "discovery": load_json(MANIFESTS_ROOT / "discovery_authorization_v8.json"),
        "validation": load_json(MANIFESTS_ROOT / "validation_authorization_v3.json"),
        "quarantine": load_json(MANIFESTS_ROOT / "quarantine_authorization_v1.json"),
    }
    preflight = {
        path.stem: load_json(path)
        for path in sorted(INTEGRITY_ROOT.glob("preflight_*.json"))
    }
    return {
        "results": results,
        "manifests": manifests,
        "authorizations": authorizations,
        "preflight": preflight,
        "registry": load_registry(),
        "ledger": load_ledger(),
    }
