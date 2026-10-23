"""Detached payload hashing and non-self-referential result publication."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from scripts.autoresearch.schemas import (
    ResultBundleV1,
    RunIdentity,
    ScorecardV2,
)

from .preregister import canonical_bytes, digest_bytes, digest_object
from .state import StateStore


def build_result_bundle(
    result_payload: Mapping[str, Any],
    *,
    identity: RunIdentity,
    base_spec_hash: str,
    effective_spec_hash: str,
    code_manifest_hash: str,
    decision: str | None = None,
    headline_cell: str | None = None,
) -> tuple[ResultBundleV1, str, str]:
    payload = dict(result_payload)
    payload_hash = digest_object(payload)
    identity_hash = digest_object(asdict(identity))
    scorecard = None
    if decision is not None:
        if headline_cell is None:
            raise ValueError("predictive result requires a headline cell")
        scorecard = asdict(ScorecardV2(
            identity.experiment, identity.hypothesis_family, identity.split,
            decision, headline_cell, identity_hash, payload_hash,
            base_spec_hash, effective_spec_hash,
        ))
    bundle = ResultBundleV1(
        payload, scorecard, base_spec_hash, effective_spec_hash,
        code_manifest_hash, identity_hash,
    )
    envelope_hash = digest_bytes(canonical_bytes(asdict(bundle)) + b"\n")
    return bundle, payload_hash, envelope_hash


def publish_result_bundle(
    store: StateStore,
    run_id: int,
    path: str | Path,
    bundle: ResultBundleV1,
    *,
    payload_hash: str,
    envelope_hash: str,
) -> str:
    receipt = {
        "schema_version": 1,
        "result_payload_hash": payload_hash,
        "envelope_hash": envelope_hash,
        "base_spec_hash": bundle.base_spec_hash,
        "effective_spec_hash": bundle.effective_spec_hash,
        "code_manifest_hash": bundle.code_manifest_hash,
        "run_identity_hash": bundle.run_identity_hash,
    }
    return store.publish_and_complete(run_id, path, asdict(bundle), receipt)
