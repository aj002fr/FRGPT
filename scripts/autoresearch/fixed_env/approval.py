"""Hash-bound confirmation approvals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Mapping, Protocol

from scripts.autoresearch.schemas import Approval, RunIdentity

from .preregister import digest_object


class ApprovalStore(Protocol):
    def persist_approval(self, approval: Approval) -> str: ...


def create_approval(
    identity: RunIdentity,
    *,
    primary_claim_hash: str,
    confirmation_cutoff: str,
    date_manifest_hash: str,
    approver: str,
    approved_at: str | None = None,
) -> Approval:
    if identity.split != "confirmation":
        raise ValueError("approval identity must bind the confirmation split")
    timestamp = approved_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return Approval(
        hypothesis_family=identity.hypothesis_family,
        experiment=identity.experiment,
        candidate_hash=identity.candidate_hash,
        state_hash=identity.state_hash,
        evaluator_hash=identity.evaluator_hash,
        data_hash=identity.data_hash,
        primary_claim_hash=primary_claim_hash,
        confirmation_cutoff=confirmation_cutoff,
        date_manifest_hash=date_manifest_hash,
        spec_hash=identity.spec_hash,
        approver=approver,
        approved_at=timestamp,
    )


def approval_hash(approval: Approval) -> str:
    return digest_object(asdict(approval))


def record_manual_approval(store: ApprovalStore, approval: Approval) -> str:
    """Persist one explicit, immutable approval for a family/experiment pair."""
    return store.persist_approval(approval)


def verify_approval(
    approval: Approval,
    identity: RunIdentity,
    *,
    primary_claim_hash: str,
    confirmation_cutoff: str,
    date_manifest_hash: str,
) -> None:
    expected: Mapping[str, str] = {
        "hypothesis_family": identity.hypothesis_family,
        "experiment": identity.experiment,
        "candidate_hash": identity.candidate_hash,
        "state_hash": identity.state_hash,
        "evaluator_hash": identity.evaluator_hash,
        "data_hash": identity.data_hash,
        "primary_claim_hash": primary_claim_hash,
        "spec_hash": identity.spec_hash,
        "confirmation_cutoff": confirmation_cutoff,
        "date_manifest_hash": date_manifest_hash,
    }
    mismatches = [
        name for name, value in expected.items()
        if getattr(approval, name) != value
    ]
    if identity.split != "confirmation" or mismatches:
        raise PermissionError(f"approval binding mismatch: {mismatches}")

