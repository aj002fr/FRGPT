from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.autoresearch.fixed_env.approval import (
    create_approval,
    record_manual_approval,
)
from scripts.autoresearch.fixed_env.state import StateStore
from scripts.autoresearch.schemas import PreprocessingIdentity, RunIdentity

H = "a" * 64


def identity(
    experiment: str = "E999",
    family: str = "FAM_TEST",
    split: str = "validation",
    candidate_hash: str = H,
) -> RunIdentity:
    return RunIdentity(experiment, family, split, candidate_hash, H, H, H)


def preprocessing() -> PreprocessingIdentity:
    return PreprocessingIdentity("E999", "FAM_TEST", "discovery", H, H, H, H)


def test_reservation_is_unique_under_concurrency(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")

    def reserve() -> str:
        try:
            store.reserve(identity())
            return "reserved"
        except RuntimeError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == ["rejected", "reserved"]


def test_crash_retry_only_for_identical_failed_no_result(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    first = store.reserve(identity())
    store.fail_no_result(first.run_id, "synthetic crash before statistics")
    retry = store.reserve(identity())
    assert retry.run_id == first.run_id
    assert retry.attempt == 2

    different = store.reserve(identity(candidate_hash="b" * 64))
    assert different.run_id != first.run_id
    store.complete(retry.run_id, "c" * 64, {"decision": "inconclusive"})
    with pytest.raises(RuntimeError):
        store.reserve(identity())
    with pytest.raises(RuntimeError):
        store.fail_no_result(retry.run_id, "too late")


def test_preprocessing_reservation_seals_state_and_closes_after_five(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    first = store.reserve_preprocessing(preprocessing())
    sealed = store.seal_preprocessing(first.run_id, "b" * 64)
    assert store.get_run(sealed.run_id)["state_hash"] == "b" * 64
    resumed_job = store.reserve_preprocessing(preprocessing())
    assert resumed_job.status == "RESUME_SEALED"
    resumed_run = store.seal_preprocessing(resumed_job.run_id, "b" * 64)
    assert resumed_run.run_id == sealed.run_id
    with pytest.raises(RuntimeError, match="cannot be changed"):
        store.seal_preprocessing(resumed_job.run_id, "c" * 64)

    capped = PreprocessingIdentity("E998", "FAM_CAP", "discovery", H, H, H, H)
    reservation = store.reserve_preprocessing(capped)
    for attempt in range(1, 6):
        store.fail_preprocessing_no_result(reservation.run_id, "implementation failure")
        if attempt < 5:
            reservation = store.reserve_preprocessing(capped)
    with pytest.raises(RuntimeError, match="CLOSED_INVALID"):
        store.reserve_preprocessing(capped)


def test_sealed_preprocessing_can_retry_only_before_statistics(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    first_job = store.reserve_preprocessing(preprocessing())
    first_run = store.seal_preprocessing(first_job.run_id, "b" * 64)
    store.reopen_preprocessing_after_run_failure(first_run.run_id, "publish failed")
    retry_job = store.reserve_preprocessing(preprocessing())
    retry_run = store.seal_preprocessing(retry_job.run_id, "b" * 64)
    assert retry_run.run_id == first_run.run_id
    assert store.get_run(retry_run.run_id)["status"] == "RESERVED"


def test_atomic_publication_completion_and_receipt_chain(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite", output_roots=[tmp_path])
    output = tmp_path / "runs" / "result.json"
    first = store.reserve(identity())
    first_receipt = store.publish_and_complete(
        first.run_id, output, {"effect": 0.0}, {"decision": "inconclusive"},
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {"effect": 0.0}
    row = store.get_run(first.run_id)
    assert row["status"] == "COMPLETED"
    assert row["stats_emitted"] == 1
    assert len(first_receipt) == 64

    second = store.reserve(identity(experiment="E998"))
    second_receipt = store.publish_and_complete(
        second.run_id, output.with_name("result_2.json"),
        {"effect": 0.1}, {"decision": "screen_pass"},
    )
    assert second_receipt != first_receipt
    leftovers = list(output.parent.glob(f".{output.name}.*"))
    assert leftovers == []
    third = store.reserve(identity(experiment="E997"))
    with pytest.raises(FileExistsError, match="immutable"):
        store.publish_and_complete(
            third.run_id, output, {"effect": 0.2}, {"decision": "inconclusive"},
        )
    orphan_output = output.with_name("orphan.json")
    orphan_output.write_bytes(b'{"effect":0.3}\n')
    fourth = store.reserve(identity(experiment="E996"))
    receipt = store.publish_and_complete(
        fourth.run_id, orphan_output, {"effect": 0.3}, {"decision": "inconclusive"},
    )
    assert len(receipt) == 64
    assert store.get_run(fourth.run_id)["status"] == "COMPLETED"


def test_confirmation_retires_cohort_for_whole_family(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    confirmation = identity(split="confirmation")
    store.retire_confirmation_cohort(
        confirmation, cutoff="2026-07-20", manifest_hash=H,
        approval_hash="b" * 64,
    )
    child = identity(experiment="E998", family="FAM_TEST", split="confirmation")
    with pytest.raises(RuntimeError):
        store.retire_confirmation_cohort(
            child, cutoff="2026-08-20", manifest_hash="c" * 64,
            approval_hash="d" * 64,
        )


def test_alpha_family_ledger_persists_across_experiment_ids(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.register_alpha_family("ALPHA_TEST", 0.05, "E999")
    store.link_alpha_experiment("ALPHA_TEST", "E998")
    ledger = StateStore(tmp_path / "state.sqlite").get_alpha_family("ALPHA_TEST")
    assert ledger["allocated_alpha"] == pytest.approx(0.05)
    assert ledger["experiments"] == ["E998", "E999"]
    with pytest.raises(RuntimeError, match="do not reset"):
        store.register_alpha_family("ALPHA_TEST", 0.04, "E997")
    with pytest.raises(RuntimeError, match="already linked"):
        store.register_alpha_family("ALPHA_OTHER", 0.05, "E999")
    with pytest.raises(RuntimeError, match="already linked"):
        store.link_alpha_experiment("ALPHA_TEST", "E999")


def test_manual_approval_is_hash_bound_and_one_shot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    confirmation = identity(split="confirmation")
    approval = create_approval(
        confirmation,
        primary_claim_hash=H,
        confirmation_cutoff="2026-07-20",
        date_manifest_hash=H,
        approver="research-owner",
        approved_at="2026-07-21T16:00:00Z",
    )
    persisted_hash = record_manual_approval(store, approval)
    assert len(persisted_hash) == 64
    reopened = StateStore(tmp_path / "state.sqlite")
    assert reopened.get_approval("FAM_TEST", "E999") == approval
    with pytest.raises(RuntimeError, match="already recorded"):
        record_manual_approval(store, approval)

    changed = create_approval(
        RunIdentity("E999", "FAM_TEST", "confirmation", "b" * 64, H, H, H),
        primary_claim_hash=H,
        confirmation_cutoff="2026-07-20",
        date_manifest_hash=H,
        approver="research-owner",
        approved_at="2026-07-21T16:00:00Z",
    )
    with pytest.raises(RuntimeError, match="already recorded"):
        record_manual_approval(store, changed)


def test_begin_confirmation_binds_spec_reserves_then_ledgers_exposure(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    confirmation = RunIdentity(
        "E999", "FAM_TEST", "confirmation", H, H, H, H, "b" * 64,
    )
    approval = create_approval(
        confirmation, primary_claim_hash=H,
        confirmation_cutoff="2026-07-20", date_manifest_hash=H,
        approver="research-owner", approved_at="2026-07-21T16:00:00Z",
    )
    record_manual_approval(store, approval)
    reserved = store.begin_confirmation(
        confirmation, approval, primary_claim_hash=H,
        confirmation_cutoff="2026-07-20", date_manifest_hash=H,
        target="TARGET", trading_dates=["2026-07-21"],
    )
    assert store.get_run(reserved.run_id)["status"] == "RESERVED"
    exposures = store.get_exposures("FAM_TEST", "TARGET")
    assert exposures[0]["run_id"] == reserved.run_id

    changed_spec = RunIdentity(
        "E998", "FAM_OTHER", "confirmation", H, H, H, H, "c" * 64,
    )
    with pytest.raises(PermissionError, match="spec_hash"):
        store.begin_confirmation(
            changed_spec, approval, primary_claim_hash=H,
            confirmation_cutoff="2026-07-20", date_manifest_hash=H,
            target="TARGET", trading_dates=["2026-07-22"],
        )


def test_historical_reservation_atomically_precedes_exposure_and_retries(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    validation = identity()
    reserved = store.reserve_historical_with_exposure(
        validation, target="BOUNDED", trading_dates=["2025-09-01", "2025-09-02"],
    )
    assert store.get_run(reserved.run_id)["status"] == "RESERVED"
    assert [row["trading_date"] for row in store.get_exposures("FAM_TEST", "BOUNDED")] == [
        "2025-09-01", "2025-09-02",
    ]
    with pytest.raises(RuntimeError, match="already RESERVED"):
        store.reserve_historical_with_exposure(
            validation, target="BOUNDED",
            trading_dates=["2025-09-01", "2025-09-02"],
        )
    store.fail_no_result(reserved.run_id, "synthetic pre-outcome crash")
    retry = store.reserve_historical_with_exposure(
        validation, target="BOUNDED", trading_dates=["2025-09-01", "2025-09-02"],
    )
    store.complete(retry.run_id, H, {"status": "completed"})
    assert store.completed_validation(validation)["status"] == "COMPLETED"

    quarantine = identity(split="quarantine")
    with pytest.raises(RuntimeError, match="previously exposed"):
        store.reserve_historical_with_exposure(
            quarantine, target="BOUNDED", trading_dates=["2025-09-02"],
        )

