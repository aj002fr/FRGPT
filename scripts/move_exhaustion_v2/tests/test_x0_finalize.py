"""Synthetic tests for the X0 finalization slice.

Covers :mod:`scripts.move_exhaustion_v2.data.x0_finalize`:

* day eligibility with manual-review admission (valid dated per-date
  APPROVE records with reviewer + rationale; reject/absent review excludes);
* the symmetric 5-date purge at both split boundaries;
* the partial final-holdout end (last Q3-passing fully covered session
  <= 2026-04-01) and fail-closed behavior without any holdout session;
* the 2022/2023 annual gates and the pooled 2024-through-partial-2026 gate;
* Q7/Q8 and disk-budget gate blocking (missing pilot / headroom overflow);
* hash/path tamper detection (output, source, builder, escape, divergent
  frozen artifact) and idempotent identical reruns;
* the no-episodes/features/holdout-statistics scope guardrail.

All inputs are synthetic and under ``tmp_path``.  The real CME calendar is
replaced per-test by an injected expected-session set so gate arithmetic is
fast and deterministic; the frozen windows come from the real
``config/frozen/*.yaml`` mappings (read-only).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import pandas as pd
import pytest
import yaml

from scripts.move_exhaustion_v2.data.x0_build import BUILDER_SHA256
from scripts.move_exhaustion_v2.data.x0_finalize import (
    X0FinalizeError,
    finalize_x0,
)

PKG_ROOT = Path(__file__).resolve().parents[1]


def _cfg(name: str) -> dict:
    path = PKG_ROOT / "config" / "frozen" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


DATA_STORE_CFG = _cfg("data_store.yaml")
SPLITS_CFG = _cfg("splits.yaml")

D_2022 = [
    "2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07",
    "2022-01-10", "2022-01-11", "2022-01-12", "2022-01-13", "2022-01-14",
]
D_2023 = [
    "2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06", "2023-01-09",
    "2023-01-10", "2023-01-11", "2023-01-12", "2023-01-13", "2023-01-17",
]
D_POOL = [
    "2024-01-02", "2024-01-03", "2024-02-01", "2024-02-02",
    "2025-01-02", "2025-01-03", "2025-02-03",
    "2026-01-02", "2026-01-05", "2026-01-06",
]
PASS_DATES = D_2022 + D_2023 + D_POOL

D12_2022 = [
    "2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07",
    "2022-01-10", "2022-01-11", "2022-01-12", "2022-01-13", "2022-01-14",
    "2022-01-18", "2022-01-19",
]
D12_2025 = [
    "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08",
    "2025-01-09", "2025-01-10", "2025-01-13", "2025-01-14", "2025-01-15",
    "2025-01-16", "2025-01-17",
]
D12_2026 = [
    "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
    "2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15",
    "2026-03-02", "2026-03-27",
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_calendar(monkeypatch):
    """Replace qc.expected_open_sessions with an injected session set."""

    def apply(expected_dates: Sequence[str]) -> List[str]:
        expected = sorted(set(expected_dates))

        def fake(start: Any, end: Any) -> List[Dict[str, Any]]:
            lo = pd.Timestamp(start).date()
            hi = pd.Timestamp(end).date()
            return [
                {"trading_date": d, "kind": "full"}
                for d in expected
                if lo <= date.fromisoformat(d) <= hi
            ]

        monkeypatch.setattr(
            "scripts.move_exhaustion_v2.data.qc.expected_open_sessions", fake
        )
        return expected

    return apply


def _source_file(tmp_path: Path) -> Path:
    path = tmp_path / "GLBX-202201.csv"
    path.write_bytes(b"monthly-mbp1-stub\n")
    return path


def _day_record(
    trading_date: str,
    *,
    store_root: Path,
    source: Path,
    source_hash: str,
    q3_pass: bool = True,
    q4: bool = True,
    q5: bool = True,
    q6: bool = True,
    plan_eligible: bool = True,
) -> Dict[str, Any]:
    parquet = store_root / f"{trading_date}.parquet"
    parquet.write_bytes(f"x0-parquet-stub-{trading_date}".encode("ascii"))
    checks = {
        "Q1": {"passed": True},
        "Q2": {"passed": True},
        "Q3": {"passed": q3_pass, "skipped": False},
        "Q4": {"passed": q4, "flag_only": True},
        "Q5": {"passed": q5, "flag_only": True},
        "Q6": {"passed": q6, "flag_only": True},
    }
    qc_pass = q3_pass and q4 and q5 and q6
    return {
        "trading_date": trading_date,
        "status": "built",
        "builder_sha256": BUILDER_SHA256,
        "selected_contract": "ZNH6",
        "output_path": str(parquet.resolve()),
        "output_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "source_files": [str(source)],
        "source_file_hashes": {str(source): source_hash},
        "n_rows": 1,
        "qc_pass": qc_pass,
        "plan_eligible": plan_eligible,
        "research_eligible": q3_pass,
        "qc": {
            "trading_date": trading_date,
            "qc_pass": qc_pass,
            "research_eligible": q3_pass,
            "checks": checks,
        },
        "built_at_utc": "2026-08-12T00:00:00Z",
    }


def _skipped_record(trading_date: str) -> Dict[str, Any]:
    return {
        "trading_date": trading_date,
        "status": "skipped",
        "builder_sha256": BUILDER_SHA256,
        "selected_contract": None,
        "output_path": None,
        "output_sha256": None,
        "source_files": [],
        "source_file_hashes": {},
        "n_rows": 0,
        "qc": None,
        "qc_pass": False,
        "plan_eligible": False,
        "research_eligible": False,
        "built_at_utc": "2026-08-12T00:00:00Z",
    }


def _build_result(
    tmp_path: Path,
    dates: Sequence[str],
    *,
    store_name: str = "store",
    analysis_name: str = "analysis",
    flags: Mapping[str, Mapping[str, bool]] | None = None,
    extra_days: Mapping[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    store_root = tmp_path / store_name
    analysis_root = tmp_path / analysis_name
    store_root.mkdir(parents=True, exist_ok=True)
    analysis_root.mkdir(parents=True, exist_ok=True)
    source = _source_file(tmp_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    flags = flags or {}
    days: Dict[str, Any] = {}
    for trading_date in dates:
        day_flags = flags.get(trading_date, {})
        days[trading_date] = _day_record(
            trading_date,
            store_root=store_root,
            source=source,
            source_hash=source_hash,
            q3_pass=day_flags.get("q3_pass", True),
            q4=day_flags.get("q4", True),
            q5=day_flags.get("q5", True),
            q6=day_flags.get("q6", True),
            plan_eligible=day_flags.get("plan_eligible", True),
        )
    if extra_days:
        days.update(extra_days)
    return {
        "program": "move_exhaustion_v2",
        "slice": "x0_selected_row_store_build",
        "days": days,
        "sources": [str(source)],
        "source_hashes": {str(source): source_hash},
        "builder_sha256": BUILDER_SHA256,
        "plan_sha256": "a" * 64,
        "medians_sha256": "b" * 64,
        "store_root": str(store_root),
        "analysis_root": str(analysis_root),
        "holdout_opened": False,
        "completed_at_utc": "2026-08-12T00:00:00Z",
    }


def _parity_report(
    dates: Sequence[str],
    *,
    q7_pass: bool = True,
    q8_pass: bool = True,
    corr: float = 1.0,
    sign_flips: int = 0,
    max_diff: float = 0.0,
) -> Dict[str, Any]:
    return {
        "program": "move_exhaustion_v2",
        "slice": "x0_parity",
        "schema_version": 1,
        "holdout_opened": False,
        "store_dates": sorted(dates),
        "matrix_et_dates": sorted(dates),
        "overlap_dates": sorted(dates),
        "q7": {
            "passed": q7_pass,
            "sign_flips": sign_flips,
            "global_corr": corr,
            "overlap_count": len(dates),
            "overlap_dates": sorted(dates),
        },
        "q8": {
            "passed": q8_pass,
            "global_max_abs_diff": max_diff,
            "overlap_count": len(dates),
            "overlap_dates": sorted(dates),
        },
        "semantics_audit": {
            "store_dates": sorted(dates),
            "n_store_days": len(dates),
            "total_rows": len(dates),
            "event_class": {},
            "action": {},
            "side": {},
            "trade_side": {},
            "note": "",
        },
        "note": "",
    }


def _pilot(*, projected: float = 100_000.0, free: float = 1_000_000.0) -> Dict[str, Any]:
    return {
        "actual_parquet_bytes": 1_000.0,
        "actual_days": 5,
        "projected_full_store_bytes": projected,
        "free_bytes": free,
    }


def _run(
    tmp_path: Path,
    build: Mapping[str, Any],
    parity: Mapping[str, Any],
    fake_calendar,
    *,
    run_id: str = "test_run",
    reviews: Sequence[Mapping[str, Any]] = (),
    pilot: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    fake_calendar(PASS_DATES)
    analysis_root = Path(str(build["analysis_root"]))
    store_root = Path(str(build["store_root"]))
    return finalize_x0(
        build,
        parity,
        DATA_STORE_CFG,
        SPLITS_CFG,
        reviews,
        pilot,
        analysis_root=analysis_root,
        store_root=store_root,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Manual review admission
# ---------------------------------------------------------------------------

def test_manual_review_admission_and_rejection(tmp_path, fake_calendar):
    build = _build_result(
        tmp_path,
        D_2022 + ["2026-01-02", "2026-01-05", "2026-01-06"],
        flags={
            "2022-01-06": {"q4": False, "q5": False, "q6": False},
            "2022-01-07": {"q4": False},
            "2022-01-10": {"q3_pass": False},
        },
    )
    parity = _parity_report(sorted(build["days"]))
    reviews = [
        {
            "trading_date": "2022-01-06",
            "review_date": "2026-08-12",
            "decision": "APPROVE",
            "reviewer": "QA Lead",
            "rationale": "Manual check of raw feed confirms the low event count.",
        },
        {
            "trading_date": "2022-01-07",
            "review_date": "2026-08-12",
            "decision": "APPROVE",
            "reviewer": "QA Lead",
            "rationale": "   ",  # empty rationale -> rejected
        },
        {
            "trading_date": "2022-01-10",
            "review_date": "2026-08-12",
            "decision": "approve",  # case-insensitive
            "reviewer": "Ops",
            "rationale": "Known exchange halt absorbed by Q3; session fully covered.",
        },
        {
            "trading_date": "2022-01-11",
            "review_date": "2026-08-12",
            "decision": "REJECT",
            "reviewer": "QA Lead",
            "rationale": "Feed gap cannot be justified.",
        },
    ]
    out = _run(tmp_path, build, parity, fake_calendar, reviews=reviews)

    eligible = json.loads(
        (Path(str(build["analysis_root"])) / "eligible_dates.json").read_text(
            encoding="utf-8"
        )
    )["days"]
    assert eligible["2022-01-06"]["eligible"] is True
    assert eligible["2022-01-06"]["review_approved"] is True
    assert eligible["2022-01-06"]["reviewer"] == "QA Lead"
    # Missing rationale -> no valid APPROVE -> review flags exclude the day.
    assert eligible["2022-01-07"]["eligible"] is False
    assert "review flags" in eligible["2022-01-07"]["reason"]
    # Q3 hard failure cannot be overridden by an APPROVE record.
    assert eligible["2022-01-10"]["eligible"] is False
    assert eligible["2022-01-10"]["reason"] == "Q1-Q3 failure"
    assert len(out["rejected_review_records"]) == 2  # empty rationale + REJECT
    assert "2022-01-06" in out["eligible_dates"]
    assert "2022-01-07" not in out["eligible_dates"]


def test_duplicate_valid_manual_review_fails_closed(tmp_path, fake_calendar):
    build = _build_result(tmp_path, D_2022 + ["2026-01-02"])
    parity = _parity_report(sorted(build["days"]))
    reviews = [
        {
            "trading_date": "2022-01-03",
            "review_date": "2026-08-12",
            "decision": "APPROVE",
            "reviewer": "A",
            "rationale": "ok",
        },
        {
            "trading_date": "2022-01-03",
            "review_date": "2026-08-12",
            "decision": "APPROVE",
            "reviewer": "B",
            "rationale": "also ok",
        },
    ]
    with pytest.raises(X0FinalizeError, match="duplicate valid manual review"):
        _run(tmp_path, build, parity, fake_calendar, reviews=reviews)


# ---------------------------------------------------------------------------
# Symmetric purges and partial holdout end
# ---------------------------------------------------------------------------

def test_symmetric_five_date_purges_at_both_boundaries(tmp_path, fake_calendar):
    dates = D12_2022 + D12_2025 + D12_2026
    build = _build_result(tmp_path, dates)
    fake_calendar(dates)
    out = finalize_x0(
        build,
        _parity_report(dates),
        DATA_STORE_CFG,
        SPLITS_CFG,
        [],
        _pilot(),
        analysis_root=Path(str(build["analysis_root"])),
        store_root=Path(str(build["store_root"])),
        run_id="purge",
    )
    splits = out["splits"]["splits"]
    assert splits["discovery"]["dates"] == D12_2022[:7]
    assert splits["discovery"]["purged"] == D12_2022[-5:]
    assert splits["validation"]["dates"] == D12_2025[5:7]
    assert splits["validation"]["purged_head"] == D12_2025[:5]
    assert splits["validation"]["purged_tail"] == D12_2025[-5:]
    assert splits["final_holdout"]["dates"] == D12_2026[5:]
    assert splits["final_holdout"]["purged"] == D12_2026[:5]
    assert splits["final_holdout"]["locked"] is True
    boundaries = out["splits"]["embargo"]["boundaries"]
    assert boundaries[0]["purged_earlier_tail"] == D12_2022[-5:]
    assert boundaries[0]["purged_later_head"] == D12_2025[:5]
    assert boundaries[1]["purged_earlier_tail"] == D12_2025[-5:]
    assert boundaries[1]["purged_later_head"] == D12_2026[:5]
    assert len(out["eligible_dates"]) == 36


def test_partial_holdout_end_uses_last_q3_passing_session(tmp_path, fake_calendar):
    holdout_dates = ["2026-01-02", "2026-03-02", "2026-03-27"]
    build = _build_result(tmp_path, D_2022 + holdout_dates)
    fake_calendar(D_2022 + D_2023 + D_POOL)
    out = finalize_x0(
        build,
        _parity_report(sorted(build["days"])),
        DATA_STORE_CFG,
        SPLITS_CFG,
        [],
        _pilot(),
        analysis_root=Path(str(build["analysis_root"])),
        store_root=Path(str(build["store_root"])),
        run_id="holdout_end",
    )
    assert out["holdout_end"] == "2026-03-27"
    assert out["splits"]["splits"]["final_holdout"]["window"] == [
        "2026-01-01",
        "2026-03-27",
    ]


def test_no_q3_passing_holdout_session_fails_closed(tmp_path, fake_calendar):
    build = _build_result(tmp_path, D_2022)
    fake_calendar(D_2022)
    with pytest.raises(X0FinalizeError, match="no Q3-passing fully covered session"):
        finalize_x0(
            build,
            _parity_report(D_2022),
            DATA_STORE_CFG,
            SPLITS_CFG,
            [],
            _pilot(),
            analysis_root=Path(str(build["analysis_root"])),
            store_root=Path(str(build["store_root"])),
            run_id="no_holdout",
        )


# ---------------------------------------------------------------------------
# Annual and pooled gates; Q7/Q8; disk budget
# ---------------------------------------------------------------------------

def test_annual_and_pooled_gates_pass(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    out = _run(
        tmp_path, build, _parity_report(PASS_DATES), fake_calendar, pilot=_pilot()
    )
    assert out["verdict"] == "PASS"
    assert all(out["pass_criteria"].values())
    assert out["annual_gates"]["2022"]["meets"] is True
    assert out["annual_gates"]["2023"]["meets"] is True
    assert out["annual_gates"]["pooled_2024_through_partial_2026"]["meets"] is True
    assert out["disk_budget"]["passed"] is True
    assert out["holdout_end"] == "2026-01-06"


def test_annual_and_pooled_gates_fail(tmp_path, fake_calendar):
    # 2022 coverage drops to 8/10 = 0.80 by omitting two sessions; pooled
    # hard-gate pass rate drops via two Q3-failing days.
    dates = [d for d in PASS_DATES if d not in ("2022-01-13", "2022-01-14")]
    build = _build_result(
        tmp_path,
        dates,
        flags={
            "2024-02-01": {"q3_pass": False},
            "2025-02-03": {"q3_pass": False},
        },
    )
    out = _run(tmp_path, build, _parity_report(sorted(build["days"])), fake_calendar)
    assert out["verdict"] == "FAIL"
    assert not out["pass_criteria"]["years_2022_2023"]
    assert not out["pass_criteria"]["pooled_2024_through_partial_2026"]
    assert any("2022 gate fail" in reason for reason in out["reasons"])
    assert any("pooled 2024" in reason for reason in out["reasons"])


def test_q7_sign_flips_block_pass(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    parity = _parity_report(
        PASS_DATES, q7_pass=False, corr=0.8, sign_flips=4
    )
    out = _run(tmp_path, build, parity, fake_calendar)
    assert out["verdict"] == "FAIL"
    assert not out["pass_criteria"]["q7_global_corr"]
    assert not out["pass_criteria"]["q7_exact_sign_check"]


def test_q8_price_parity_blocks_pass(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    parity = _parity_report(
        PASS_DATES, q8_pass=False, max_diff=0.125  # 8 ticks
    )
    out = _run(tmp_path, build, parity, fake_calendar)
    assert out["verdict"] == "FAIL"
    assert not out["pass_criteria"]["q8_price_parity"]


def test_missing_pilot_disk_record_blocks_pass(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    out = _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar, pilot=None)
    assert out["verdict"] == "FAIL"
    assert out["disk_budget"]["passed"] is False
    assert any("missing pilot disk record" in reason for reason in out["reasons"])


def test_disk_headroom_overflow_blocks_pass(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    pilot = _pilot(projected=950_000.0, free=1_000_000.0)  # > 800k headroom limit
    out = _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar, pilot=pilot)
    assert out["verdict"] == "FAIL"
    assert out["disk_budget"]["passed"] is False
    assert any("headroom" in reason for reason in out["reasons"])
    disk = json.loads(
        (Path(str(build["analysis_root"])) / "disk_budget.json").read_text(
            encoding="utf-8"
        )
    )
    assert disk["pilot"]["recomputed_projection_bytes"] > 0
    assert disk["pilot"]["expected_total_sessions"] > 0


# ---------------------------------------------------------------------------
# Hash / path tampering and divergence
# ---------------------------------------------------------------------------

def test_tampered_output_parquet_blocks(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    parquet = Path(str(build["store_root"])) / "2022-01-03.parquet"
    parquet.write_bytes(b"tampered")
    with pytest.raises(X0FinalizeError, match="output parquet hash mismatch"):
        _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar)


def test_tampered_source_file_blocks(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    source = Path(build["sources"][0])
    source.write_bytes(b"tampered source")
    with pytest.raises(X0FinalizeError, match="build source hash mismatch"):
        _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar)


def test_builder_hash_mismatch_blocks(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    build = dict(build)
    build["builder_sha256"] = "0" * 64
    with pytest.raises(X0FinalizeError, match="builder hash"):
        _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar)


def test_output_path_escape_blocks(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    build = dict(build)
    record = dict(build["days"]["2022-01-03"])
    evil = tmp_path / "evil.parquet"
    evil.write_bytes(b"evil")
    record["output_path"] = str(evil)
    build["days"] = {**build["days"], "2022-01-03": record}
    with pytest.raises(X0FinalizeError, match="output path is unexpected"):
        _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar)


def test_divergent_existing_frozen_artifact_fails_closed(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    parity = _parity_report(PASS_DATES)
    _run(tmp_path, build, parity, fake_calendar)
    eligible_path = Path(str(build["analysis_root"])) / "eligible_dates.json"
    payload = json.loads(eligible_path.read_text(encoding="utf-8"))
    payload["dates"].append("2099-01-01")
    eligible_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(X0FinalizeError, match="diverges"):
        _run(tmp_path, build, parity, fake_calendar)


def test_holdout_opened_true_in_checkpoint_blocks(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    build = dict(build)
    build["holdout_opened"] = True
    with pytest.raises(X0FinalizeError, match="holdout access"):
        _run(tmp_path, build, _parity_report(PASS_DATES), fake_calendar)


# ---------------------------------------------------------------------------
# Idempotence and scope guardrails
# ---------------------------------------------------------------------------

def test_identical_rerun_is_idempotent(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    parity = _parity_report(PASS_DATES)
    first = _run(
        tmp_path, build, parity, fake_calendar, run_id="rerun", pilot=_pilot()
    )
    assert first["verdict"] == "PASS"
    before = {
        relative: Path(info["path"]).read_bytes()
        for relative, info in first["artifacts"].items()
    }
    second = _run(
        tmp_path, build, parity, fake_calendar, run_id="rerun", pilot=_pilot()
    )
    assert second["verdict"] == "PASS"
    assert sorted(info["status"] for info in second["artifacts"].values()) == [
        "identical"
    ] * len(second["artifacts"])
    for relative, info in second["artifacts"].items():
        assert Path(info["path"]).read_bytes() == before[relative]


def test_accepts_checkpoint_path(tmp_path, fake_calendar):
    build = _build_result(tmp_path, PASS_DATES)
    checkpoint = Path(str(build["analysis_root"])) / "build_state.json"
    checkpoint.write_text(json.dumps(build), encoding="utf-8")
    fake_calendar(PASS_DATES)
    out = finalize_x0(
        checkpoint,
        _parity_report(PASS_DATES),
        DATA_STORE_CFG,
        SPLITS_CFG,
        [],
        _pilot(),
        analysis_root=Path(str(build["analysis_root"])),
        store_root=Path(str(build["store_root"])),
        run_id="from_path",
    )
    assert out["verdict"] == "PASS"
    manifest = json.loads(
        (
            Path(str(build["analysis_root"]))
            / "experiments"
            / "X0_from_path"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["inputs"]["build_checkpoint_path"] == str(checkpoint.resolve())
    assert manifest["holdout_opened"] is False


def test_no_episodes_features_or_holdout_statistics(tmp_path, fake_calendar):
    from scripts.move_exhaustion_v2.data import x0_finalize

    tree = ast.parse(inspect.getsource(x0_finalize))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(name.name.split(".")[0] for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert not ({"episodes", "features", "labels"} & imported_roots), (
        "x0_finalize must never import episode/feature/label computation"
    )

    build = _build_result(tmp_path, PASS_DATES)
    _run(
        tmp_path, build, _parity_report(PASS_DATES), fake_calendar, pilot=_pilot()
    )
    analysis = Path(str(build["analysis_root"]))
    json_artifacts = [
        "eligible_dates.json",
        "split_manifest.json",
        "parity_report.json",
        "disk_budget.json",
        "mbp1_semantics.json",
        "experiments/X0_test_run/manifest.json",
        "experiments/X0_test_run/metrics.json",
        "experiments/X0_test_run/conclusion.json",
    ]
    for relative in json_artifacts:
        payload = json.loads((analysis / relative).read_text(encoding="utf-8"))
        assert payload["holdout_opened"] is False, relative
    assert (analysis / "qc" / "daily_qc.csv").is_file()
    assert (analysis / "manifests" / "store_manifest.csv").is_file()
    prior_excluded = {
        date.fromisoformat(value)
        for value in SPLITS_CFG["prior_program_holdout_dates_excluded_entirely"]
    }
    eligible = {
        date.fromisoformat(value)
        for value in json.loads(
            (analysis / "eligible_dates.json").read_text(encoding="utf-8")
        )["dates"]
    }
    assert not (eligible & prior_excluded)
    conclusion = json.loads(
        (analysis / "experiments" / "X0_test_run" / "conclusion.json").read_text(
            encoding="utf-8"
        )
    )
    assert conclusion["verdict"] in ("PASS", "FAIL")
    assert conclusion["holdout_opened"] is False

