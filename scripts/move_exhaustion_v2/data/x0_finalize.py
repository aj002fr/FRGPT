"""X0 finalization: eligibility, QC artifacts, split freeze, and gates.

Consumes a completed :mod:`.x0_build` checkpoint/result, the parity report
from :mod:`.x0_parity`, the frozen ``config/frozen/data_store.yaml`` and
``splits.yaml`` mappings, dated per-date manual review records, and an
actual recent-month pilot disk record; materializes the X0 deliverables
under a supplied analysis root.

Semantics (all binding, per PROGRAM.md §2/§3/§6 X0, §10 addenda and
``config/frozen/*``):

* day eligibility = plan pass AND Q1-Q3 pass AND (Q4-Q6 all pass OR a valid
  dated per-date manual ``APPROVE`` record with reviewer and nonempty
  rationale); reject/absent review excludes a review-flagged day;
* the symmetric 5-date purge is applied at both split boundaries (final 5
  eligible dates of the earlier split and first 5 of the later split);
* the final holdout ends at the last Q3-passing fully covered session on or
  before the frozen bound (2026-04-01) and stays ``holdout_opened: false``;
* 2022 and 2023 are evaluated separately at >=95% expected-session coverage
  and >=95% Q1-Q6 pass rate; the frozen pooled 2024-through-partial-2026
  criterion is evaluated against expected sessions; X0 PASS additionally
  requires the Q7/Q8 global gates, the exact Q7 sign check, and the disk
  budget with 20% headroom;
* every output/source/builder hash is verified and every artifact path is
  contained under the approved roots; writes are atomic and idempotent and
  fail closed when an existing artifact diverges;
* no episodes, features, labels, or holdout statistics are computed and the
  holdout is never unlocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..artifacts import ensure_under, sha256_file, utc_run_id
from .mbp1_store_builder import DEFAULT_TICK_SIZE
from .qc import aggregate_annual_coverage
from .x0_build import BUILDER_SHA256

PROGRAM_ID = "move_exhaustion_v2"
SLICE_ID = "x0_finalize"
SCHEMA_VERSION = 1

REVIEW_GATES = ("Q4", "Q5", "Q6")
HARD_GATES = ("Q1", "Q2", "Q3")
ALL_GATES = HARD_GATES + REVIEW_GATES

DEFAULT_HEADROOM_FRACTION = 0.20
DEFAULT_MIN_COVERAGE = 0.95
DEFAULT_MIN_Q1_Q6_PASS_RATE = 0.95
DEFAULT_Q7_MIN_CORR = 0.95

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

__all__ = [
    "BUILDER_SHA256",
    "DEFAULT_HEADROOM_FRACTION",
    "DEFAULT_MIN_COVERAGE",
    "DEFAULT_MIN_Q1_Q6_PASS_RATE",
    "PROGRAM_ID",
    "SCHEMA_VERSION",
    "SLICE_ID",
    "X0FinalizeError",
    "compute_day_eligibility",
    "compute_holdout_end",
    "evaluate_disk_budget",
    "evaluate_pass",
    "finalize_x0",
    "materialize_splits",
    "validate_build_result",
    "validate_config_mappings",
    "validate_manual_reviews",
    "validate_parity_report",
]


class X0FinalizeError(RuntimeError):
    """Raised when X0 finalization cannot proceed (fail closed)."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise X0FinalizeError(message)


def _as_date(value: Any, label: str) -> date:
    if isinstance(value, pd.Timestamp):
        value = value.date()
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError) as exc:
        raise X0FinalizeError(f"{label} is not a valid date: {value!r}") from exc


def _iso(value: Any, label: str) -> str:
    return _as_date(value, label).isoformat()


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_sha(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _write_json_artifact(path: Path, payload: Any) -> str:
    """Atomic JSON write that is idempotent and fails closed on divergence."""
    try:
        ensure_under(path, path.parents[0])
    except ValueError as exc:
        raise X0FinalizeError(f"artifact {path} escapes its artifact root") from exc
    text = _json_text(payload)
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return "identical"
        raise X0FinalizeError(
            f"existing artifact diverges from this run (refusing to overwrite): {path}"
        )
    _atomic_write_text(path, text)
    return "written"


def _write_csv_artifact(path: Path, text: str) -> str:
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return "identical"
        raise X0FinalizeError(
            f"existing artifact diverges from this run (refusing to overwrite): {path}"
        )
    _atomic_write_text(path, text)
    return "written"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _load_build_checkpoint(
    build_checkpoint: Path | Mapping[str, Any],
) -> Tuple[Dict[str, Any], Optional[str]]:
    if isinstance(build_checkpoint, Mapping):
        return dict(build_checkpoint), None
    path = Path(build_checkpoint)
    if not path.is_file():
        raise X0FinalizeError(f"build checkpoint not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise X0FinalizeError(f"build checkpoint is not readable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise X0FinalizeError(f"build checkpoint is not a JSON object: {path}")
    return payload, str(path.resolve())


def validate_build_result(
    build_result: Mapping[str, Any],
    *,
    store_root: Path,
    analysis_root: Path,
) -> Dict[str, Any]:
    """Verify the completed build result's hashes, status, and containment.

    Fail closed unless every source file exists and matches its recorded
    SHA-256, the builder hash matches the current builder module, every
    built day's parquet exists at the recorded path with a matching SHA-256,
    all paths stay under the approved roots, and the checkpoint never
    reports holdout access.
    """
    _require(isinstance(build_result, dict), "build result must be a mapping")
    required = (
        "program",
        "slice",
        "days",
        "sources",
        "source_hashes",
        "builder_sha256",
        "plan_sha256",
        "medians_sha256",
        "store_root",
        "analysis_root",
        "holdout_opened",
    )
    missing = [key for key in required if key not in build_result]
    _require(not missing, f"build result missing keys: {sorted(missing)}")
    _require(
        build_result["holdout_opened"] is False,
        "build result reports holdout access; refusing to finalize",
    )
    _require(
        build_result["builder_sha256"] == BUILDER_SHA256,
        "build result builder hash does not match the current x0_build module",
    )
    _require(
        Path(str(build_result["store_root"])).resolve() == store_root,
        "build result store_root does not match the supplied store_root",
    )
    _require(
        Path(str(build_result["analysis_root"])).resolve() == analysis_root,
        "build result analysis_root does not match the supplied analysis_root",
    )
    days = build_result["days"]
    _require(isinstance(days, Mapping) and days, "build result days must be non-empty")

    sources = list(build_result["sources"])
    source_hashes = dict(build_result["source_hashes"])
    _require(
        sorted(sources) == sorted(source_hashes),
        "build result source list and source_hashes disagree",
    )
    for raw in sources:
        path = Path(raw)
        _require(path.is_file(), f"build source no longer exists: {path}")
        _require(
            sha256_file(path) == source_hashes.get(raw),
            f"build source hash mismatch: {path}",
        )

    for trading_date, record in days.items():
        _require(isinstance(record, Mapping), f"day record {trading_date} is not a mapping")
        status = record.get("status")
        _require(
            status in {"built", "rebuilt", "resumed", "skipped"},
            f"day {trading_date} has unknown status {status!r}",
        )
        if status == "skipped":
            continue
        output_path = record.get("output_path")
        output_sha256 = record.get("output_sha256")
        _require(
            output_path and output_sha256,
            f"day {trading_date} built record lacks output path/hash",
        )
        path = Path(str(output_path)).resolve()
        expected = (store_root / f"{trading_date}.parquet").resolve()
        _require(path == expected, f"day {trading_date} output path is unexpected: {path}")
        try:
            ensure_under(path, store_root)
        except ValueError as exc:
            raise X0FinalizeError(
                f"day {trading_date} output escapes store root: {path}"
            ) from exc
        _require(path.is_file(), f"day {trading_date} output parquet missing: {path}")
        _require(
            sha256_file(path) == output_sha256,
            f"day {trading_date} output parquet hash mismatch (tampered)",
        )
        qc = record.get("qc")
        _require(isinstance(qc, dict), f"day {trading_date} built record lacks QC")
        checks = qc.get("checks")
        _require(isinstance(checks, dict), f"day {trading_date} QC lacks checks")
        for gate in ALL_GATES:
            check = checks.get(gate)
            _require(
                isinstance(check, dict) and "passed" in check,
                f"day {trading_date} QC gate {gate} is malformed",
            )
    return build_result  # type: ignore[return-value]


def validate_parity_report(
    parity_report: Mapping[str, Any],
    *,
    store_dates: Sequence[str],
) -> Dict[str, Any]:
    """Validate the parity report surface and scope before freezing it."""
    _require(isinstance(parity_report, dict), "parity report must be a mapping")
    _require(
        parity_report.get("holdout_opened") is False,
        "parity report declares holdout access",
    )
    for section in ("q7", "q8", "semantics_audit"):
        _require(
            isinstance(parity_report.get(section), dict),
            f"parity report missing {section} section",
        )
    q7 = parity_report["q7"]
    q8 = parity_report["q8"]
    for key in ("passed", "sign_flips", "global_corr", "overlap_dates"):
        _require(key in q7, f"parity q7 missing {key!r}")
    for key in ("passed", "global_max_abs_diff", "overlap_dates"):
        _require(key in q8, f"parity q8 missing {key!r}")
    report_dates = [str(value) for value in parity_report.get("store_dates", [])]
    _require(
        set(report_dates) <= set(store_dates),
        "parity report references store dates absent from the build result",
    )
    overlap = [str(value) for value in q7["overlap_dates"]]
    _require(
        set(overlap) <= set(report_dates),
        "parity q7 overlap dates are not a subset of store dates",
    )
    return parity_report  # type: ignore[return-value]


def validate_config_mappings(
    data_store_cfg: Mapping[str, Any],
    splits_cfg: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Validate the frozen config mappings consumed by finalization."""
    _require(isinstance(data_store_cfg, dict), "data_store_cfg must be a mapping")
    _require(isinstance(splits_cfg, dict), "splits_cfg must be a mapping")
    for section in ("qc_gates", "x0_pass_criterion", "session", "mbp1_semantics"):
        _require(
            isinstance(data_store_cfg.get(section), dict),
            f"data_store_cfg missing {section}",
        )
    _require("splits" in splits_cfg, "splits_cfg missing splits")
    for name in ("discovery", "validation", "final_holdout"):
        window = splits_cfg["splits"].get(name)
        _require(isinstance(window, dict), f"splits_cfg missing split {name}")
        _require("start" in window and "end" in window, f"split {name} lacks start/end")
        _iso(window["start"], f"split {name} start")
        _iso(window["end"], f"split {name} end")
    _require(
        isinstance(splits_cfg.get("embargo_trading_days"), int)
        and splits_cfg["embargo_trading_days"] >= 1,
        "splits_cfg embargo_trading_days must be a positive int",
    )
    _require(
        splits_cfg.get("holdout_opened") is False,
        "splits_cfg reports holdout opened",
    )
    return data_store_cfg, splits_cfg  # type: ignore[return-value]


def validate_manual_reviews(
    manual_reviews: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Validate dated per-date manual review records.

    A valid record requires ``trading_date``, ``decision`` (APPROVE,
    case-insensitive), a nonempty ``reviewer``, a nonempty ``rationale``,
    and a parseable ``review_date``.  Invalid records are rejected (they do
    not admit a date); duplicate valid records for one date fail closed.
    """
    valid_by_date: Dict[str, Dict[str, Any]] = {}
    normalized: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for index, record in enumerate(manual_reviews):
        label = f"manual review record {index}"
        if not isinstance(record, dict):
            rejected.append({"record": record, "reason": f"{label} is not a mapping"})
            continue
        missing = [
            field
            for field in ("trading_date", "decision", "reviewer", "rationale", "review_date")
            if not record.get(field)
        ]
        if missing:
            rejected.append(
                {"record": record, "reason": f"{label} missing fields: {sorted(missing)}"}
            )
            continue
        try:
            trading_date = _iso(record["trading_date"], f"{label} trading_date")
            review_date = _iso(record["review_date"], f"{label} review_date")
        except X0FinalizeError as exc:
            rejected.append({"record": record, "reason": str(exc)})
            continue
        decision = str(record["decision"]).strip().upper()
        reviewer = str(record["reviewer"]).strip()
        rationale = str(record["rationale"]).strip()
        if decision != "APPROVE":
            rejected.append(
                {"record": record, "reason": f"{label} decision is not APPROVE"}
            )
            continue
        if not reviewer:
            rejected.append({"record": record, "reason": f"{label} reviewer is empty"})
            continue
        if not rationale:
            rejected.append({"record": record, "reason": f"{label} rationale is empty"})
            continue
        if trading_date in valid_by_date:
            raise X0FinalizeError(
                f"duplicate valid manual review records for {trading_date}"
            )
        item = {
            "trading_date": trading_date,
            "review_date": review_date,
            "decision": decision,
            "reviewer": reviewer,
            "rationale": rationale,
        }
        valid_by_date[trading_date] = item
        normalized.append(item)
    normalized.sort(key=lambda item: (item["trading_date"], item["review_date"], item["reviewer"]))
    return valid_by_date, normalized, rejected


def validate_pilot_disk_record(
    pilot: Mapping[str, Any] | None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate the pilot disk record (fail closed on any missing field)."""
    errors: List[str] = []
    if pilot is None:
        return None, ["missing pilot disk record"]
    if not isinstance(pilot, dict):
        return None, ["pilot disk record is not a mapping"]
    required = (
        "actual_parquet_bytes",
        "actual_days",
        "projected_full_store_bytes",
        "free_bytes",
    )
    missing = [key for key in required if key not in pilot]
    if missing:
        return None, [f"pilot disk record missing fields: {sorted(missing)}"]
    try:
        actual_bytes = float(pilot["actual_parquet_bytes"])
        actual_days = float(pilot["actual_days"])
        projected = float(pilot["projected_full_store_bytes"])
        free_bytes = float(pilot["free_bytes"])
    except (TypeError, ValueError) as exc:
        return None, [f"pilot disk record has non-numeric fields: {exc}"]
    if actual_bytes < 0 or actual_days < 1 or projected <= 0 or free_bytes <= 0:
        return None, [
            "pilot disk record fields must satisfy bytes>=0, days>=1, "
            "projected>0, free>0"
        ]
    return {
        "actual_parquet_bytes": actual_bytes,
        "actual_days": int(actual_days),
        "bytes_per_day": actual_bytes / int(actual_days),
        "projected_full_store_bytes": projected,
        "free_bytes": free_bytes,
    }, errors


# ---------------------------------------------------------------------------
# Eligibility, splits, gates
# ---------------------------------------------------------------------------

def compute_day_eligibility(
    days: Mapping[str, Mapping[str, Any]],
    valid_reviews: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Per-day eligibility: plan pass + Q1-Q3 + (Q4-Q6 or manual APPROVE)."""
    out: Dict[str, Dict[str, Any]] = {}
    for trading_date in sorted(days):
        record = days[trading_date]
        plan_eligible = bool(record.get("plan_eligible"))
        base = {
            "trading_date": trading_date,
            "status": str(record.get("status", "unknown")),
            "plan_eligible": plan_eligible,
        }
        qc = record.get("qc")
        if not isinstance(qc, dict) or not isinstance(qc.get("checks"), dict):
            out[trading_date] = {
                **base,
                "qc_pass": False,
                "research_eligible": False,
                "hard_gates_pass": False,
                "review_gates_pass": False,
                "review_flags": [],
                "review_approved": False,
                "eligible": False,
                "reason": "not built or missing QC record",
            }
            continue
        checks = qc["checks"]

        def _gate(name: str) -> bool:
            check = checks.get(name)
            return isinstance(check, dict) and check.get("passed") is True

        q1 = _gate("Q1")
        q2 = _gate("Q2")
        q3 = _gate("Q3")
        q3_skipped = isinstance(checks.get("Q3"), dict) and (
            checks["Q3"].get("skipped") is True
        )
        q3 = q3 and not q3_skipped
        q4, q5, q6 = _gate("Q4"), _gate("Q5"), _gate("Q6")
        hard_gates_pass = q1 and q2 and q3
        review_flags = [
            gate for gate, passed in (("Q4", q4), ("Q5", q5), ("Q6", q6)) if not passed
        ]
        review_approved = trading_date in valid_reviews
        review_gates_pass = not review_flags
        eligible = (
            plan_eligible and hard_gates_pass and (review_gates_pass or review_approved)
        )
        review = valid_reviews.get(trading_date)
        out[trading_date] = {
            **base,
            "qc_pass": bool(qc.get("qc_pass")),
            "research_eligible": bool(qc.get("research_eligible")),
            "q1_pass": q1,
            "q2_pass": q2,
            "q3_pass": q3,
            "q4_pass": q4,
            "q5_pass": q5,
            "q6_pass": q6,
            "hard_gates_pass": hard_gates_pass,
            "review_gates_pass": review_gates_pass,
            "review_flags": review_flags,
            "review_approved": review_approved,
            "reviewer": review["reviewer"] if review else None,
            "review_date": review["review_date"] if review else None,
            "eligible": eligible,
            "reason": (
                None
                if eligible
                else "Q1-Q3 failure"
                if not hard_gates_pass
                else "plan ineligible"
                if not plan_eligible
                else "review flags without valid manual APPROVE"
            ),
        }
    return out


def compute_holdout_end(
    days: Mapping[str, Mapping[str, Any]],
    splits_cfg: Mapping[str, Any],
) -> str:
    """Last Q3-passing fully covered session on or before the frozen bound."""
    window = splits_cfg["splits"]["final_holdout"]
    start = _iso(window["start"], "holdout start")
    bound = _iso(window["end"], "holdout bound")
    candidates: List[str] = []
    for trading_date, record in days.items():
        if not (start <= trading_date <= bound):
            continue
        qc = record.get("qc")
        if not isinstance(qc, dict):
            continue
        checks = qc.get("checks")
        q3 = checks.get("Q3") if isinstance(checks, dict) else None
        if (
            isinstance(q3, dict)
            and q3.get("passed") is True
            and q3.get("skipped") is not True
        ):
            candidates.append(trading_date)
    _require(candidates, f"no Q3-passing fully covered session in holdout window up to {bound}")
    return max(candidates)


def materialize_splits(
    eligible_dates: Sequence[str],
    holdout_end: str,
    splits_cfg: Mapping[str, Any],
    data_store_cfg: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply the symmetric embargo purge and freeze the three split lists."""
    embargo = int(splits_cfg["embargo_trading_days"])
    splits = splits_cfg["splits"]
    discovery_window = (
        _iso(splits["discovery"]["start"], "discovery start"),
        _iso(splits["discovery"]["end"], "discovery end"),
    )
    validation_window = (
        _iso(splits["validation"]["start"], "validation start"),
        _iso(splits["validation"]["end"], "validation end"),
    )
    holdout_start = _iso(splits["final_holdout"]["start"], "holdout start")

    def _in_window(dates: Sequence[str], window: Tuple[str, str]) -> List[str]:
        lo, hi = window
        return [d for d in dates if lo <= d <= hi]

    discovery_eligible = _in_window(eligible_dates, discovery_window)
    validation_eligible = _in_window(eligible_dates, validation_window)
    holdout_eligible = _in_window(
        eligible_dates, (holdout_start, holdout_end)
    )

    discovery_purged = discovery_eligible[-embargo:]
    discovery_kept = discovery_eligible[: len(discovery_eligible) - embargo] if embargo else discovery_eligible
    validation_purged_head = validation_eligible[:embargo]
    validation_purged_tail = validation_eligible[-embargo:]
    validation_kept = (
        validation_eligible[embargo : len(validation_eligible) - embargo]
        if embargo
        else validation_eligible
    )
    holdout_purged = holdout_eligible[:embargo]
    holdout_kept = holdout_eligible[embargo:] if embargo else holdout_eligible

    return {
        "schema_version": SCHEMA_VERSION,
        "program": PROGRAM_ID,
        "status": "frozen_by_x0_finalize",
        "holdout_opened": False,
        "embargo": {
            "trading_days": embargo,
            "policy": "symmetric: final 5 eligible dates of the earlier split and "
            "first 5 eligible dates of the later split at each boundary",
            "boundaries": [
                {
                    "earlier": "discovery",
                    "later": "validation",
                    "purged_earlier_tail": discovery_purged,
                    "purged_later_head": validation_purged_head,
                },
                {
                    "earlier": "validation",
                    "later": "final_holdout",
                    "purged_earlier_tail": validation_purged_tail,
                    "purged_later_head": holdout_purged,
                },
            ],
        },
        "splits": {
            "discovery": {
                "window": list(discovery_window),
                "dates": discovery_kept,
                "purged": discovery_purged,
                "sha256": _json_sha(discovery_kept),
            },
            "validation": {
                "window": list(validation_window),
                "dates": validation_kept,
                "purged": validation_purged_head + validation_purged_tail,
                "purged_head": validation_purged_head,
                "purged_tail": validation_purged_tail,
                "sha256": _json_sha(validation_kept),
            },
            "final_holdout": {
                "window": [holdout_start, holdout_end],
                "dates": holdout_kept,
                "purged": holdout_purged,
                "locked": True,
                "end_rule": (
                    "last Q3-passing fully covered session on or before "
                    f"{splits['final_holdout']['end']}"
                ),
                "sha256": _json_sha(holdout_kept),
            },
        },
        "hashes": {
            "eligible_dates_sha256": _json_sha(sorted(eligible_dates)),
            "data_store_yaml_sha256": _json_sha(data_store_cfg),
            "splits_yaml_sha256": _json_sha(splits_cfg),
        },
    }


def _qc_records(build_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "trading_date": trading_date,
            "qc_pass": bool(record["qc"]["qc_pass"]),
            "research_eligible": bool(record["qc"]["research_eligible"]),
        }
        for trading_date, record in sorted(build_result["days"].items())
        if isinstance(record.get("qc"), dict)
    ]


def evaluate_annual_gates(
    build_result: Mapping[str, Any],
    splits_cfg: Mapping[str, Any],
    holdout_end: str,
    *,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_q1_q6_pass_rate: float = DEFAULT_MIN_Q1_Q6_PASS_RATE,
) -> Dict[str, Any]:
    """2022/2023 annual gates and the pooled 2024-through-partial-2026 gate."""
    records = _qc_records(build_result)
    annual: Dict[str, Dict[str, Any]] = {}
    for year in (2022, 2023):
        agg = aggregate_annual_coverage(
            records,
            years=[year],
            min_expected_coverage=min_coverage,
            min_q1_q6_pass_rate=min_q1_q6_pass_rate,
        )
        block = dict(agg["per_year"][str(year)])
        block["meets"] = bool(
            block["coverage_ratio"] >= min_coverage
            and block["q1_q6_pass_rate"] >= min_q1_q6_pass_rate
        )
        annual[str(year)] = block

    pooled = aggregate_annual_coverage(
        records,
        expected_start="2024-01-01",
        expected_end=holdout_end,
        min_expected_coverage=min_coverage,
        min_q1_q6_pass_rate=min_q1_q6_pass_rate,
    )
    pooled_block = {
        "window": ["2024-01-01", holdout_end],
        "expected_open_sessions": pooled["total_expected_open_sessions"],
        "built_sessions": pooled["total_built_sessions"],
        "coverage_ratio": pooled["overall_coverage_ratio"],
        "q1_q6_pass_rate": pooled["overall_q1_q6_pass_rate"],
        "meets": bool(
            pooled["overall_coverage_ratio"] >= min_coverage
            and pooled["overall_q1_q6_pass_rate"] >= min_q1_q6_pass_rate
        ),
    }
    return {
        "2022": annual["2022"],
        "2023": annual["2023"],
        "pooled_2024_through_partial_2026": pooled_block,
        "min_expected_coverage": min_coverage,
        "min_q1_q6_pass_rate": min_q1_q6_pass_rate,
        "meets_annual_gates": bool(
            annual["2022"]["meets"] and annual["2023"]["meets"]
        ),
    }


def evaluate_disk_budget(
    pilot: Mapping[str, Any] | None,
    *,
    expected_total_sessions: int,
    headroom_fraction: float = DEFAULT_HEADROOM_FRACTION,
) -> Dict[str, Any]:
    """Evaluate the pilot disk record against the 20% headroom rule."""
    if not (0.0 <= float(headroom_fraction) < 1.0):
        raise ValueError(
            f"headroom_fraction must be in [0, 1), got {headroom_fraction!r}"
        )
    normalized, errors = validate_pilot_disk_record(pilot)
    if errors:
        return {
            "schema_version": SCHEMA_VERSION,
            "holdout_opened": False,
            "pilot": None,
            "passed": False,
            "reasons": errors,
            "headroom_fraction": headroom_fraction,
        }
    assert normalized is not None
    bytes_per_day = normalized["actual_parquet_bytes"] / normalized["actual_days"]
    recomputed_projection = bytes_per_day * expected_total_sessions
    max_allowed = normalized["free_bytes"] * (1.0 - headroom_fraction)
    projected = normalized["projected_full_store_bytes"]
    passed = projected <= max_allowed
    reasons: List[str] = []
    if not passed:
        reasons.append(
            f"projected full-store bytes {projected:.0f} exceed free-bytes "
            f"headroom limit {max_allowed:.0f} "
            f"(free {normalized['free_bytes']:.0f} * "
            f"(1 - {headroom_fraction}))"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "holdout_opened": False,
        "pilot": {
            **normalized,
            "recomputed_projection_bytes": recomputed_projection,
            "expected_total_sessions": expected_total_sessions,
            "max_allowed_bytes": max_allowed,
        },
        "passed": passed,
        "reasons": reasons,
        "headroom_fraction": headroom_fraction,
    }


def evaluate_pass(
    annual_gates: Mapping[str, Any],
    parity_report: Mapping[str, Any],
    disk_budget: Mapping[str, Any],
    *,
    tick_size: float = DEFAULT_TICK_SIZE,
    q7_min_corr: float = DEFAULT_Q7_MIN_CORR,
) -> Tuple[str, Dict[str, bool], List[str]]:
    """X0 verdict: PASS only when every frozen criterion is met."""
    q7 = parity_report["q7"]
    q8 = parity_report["q8"]
    q7_corr = q7.get("global_corr")
    q8_max = q8.get("global_max_abs_diff")
    criteria = {
        "years_2022_2023": bool(annual_gates["meets_annual_gates"]),
        "pooled_2024_through_partial_2026": bool(
            annual_gates["pooled_2024_through_partial_2026"]["meets"]
        ),
        "q7_global_corr": bool(
            q7.get("passed") is True
            and q7_corr is not None
            and q7_corr > q7_min_corr
        ),
        "q7_exact_sign_check": bool(q7.get("passed") is True and q7.get("sign_flips") == 0),
        "q8_price_parity": bool(
            q8.get("passed") is True
            and q8_max is not None
            and q8_max <= tick_size
        ),
        "disk_budget": bool(disk_budget.get("passed") is True),
    }
    reasons: List[str] = []
    for year in ("2022", "2023"):
        block = annual_gates[year]
        if not block["meets"]:
            reasons.append(
                f"{year} gate fail: coverage {block['coverage_ratio']:.4f} / "
                f"Q1-Q6 pass rate {block['q1_q6_pass_rate']:.4f}"
            )
    pooled = annual_gates["pooled_2024_through_partial_2026"]
    if not pooled["meets"]:
        reasons.append(
            "pooled 2024-through-partial-2026 gate fail: coverage "
            f"{pooled['coverage_ratio']:.4f} / Q1-Q6 pass rate "
            f"{pooled['q1_q6_pass_rate']:.4f}"
        )
    if not criteria["q7_global_corr"]:
        reasons.append(f"Q7 global correlation not > {q7_min_corr}: {q7_corr!r}")
    if not criteria["q7_exact_sign_check"]:
        reasons.append(f"Q7 exact sign check failed ({q7.get('sign_flips')} sign flips)")
    if not criteria["q8_price_parity"]:
        reasons.append(f"Q8 price parity failed: max abs diff {q8_max!r} > tick {tick_size}")
    if not criteria["disk_budget"]:
        reasons.extend(str(reason) for reason in disk_budget.get("reasons", []))
    verdict = "PASS" if all(criteria.values()) else "FAIL"
    return verdict, criteria, reasons


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def finalize_x0(
    build_checkpoint: Path | Mapping[str, Any],
    parity_report: Mapping[str, Any],
    data_store_cfg: Mapping[str, Any],
    splits_cfg: Mapping[str, Any],
    manual_reviews: Sequence[Mapping[str, Any]],
    pilot_disk_record: Mapping[str, Any] | None,
    *,
    analysis_root: str | Path,
    store_root: str | Path | None = None,
    run_id: str | None = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_q1_q6_pass_rate: float = DEFAULT_MIN_Q1_Q6_PASS_RATE,
    q7_min_corr: float = DEFAULT_Q7_MIN_CORR,
    tick_size: float = DEFAULT_TICK_SIZE,
    headroom_fraction: float = DEFAULT_HEADROOM_FRACTION,
) -> Dict[str, Any]:
    """Finalize X0: verify inputs, materialize artifacts, and freeze splits.

    Writes, under the supplied ``analysis_root`` only:

    * ``qc/daily_qc.csv``, ``manifests/store_manifest.csv``
    * ``eligible_dates.json``, ``split_manifest.json``
    * ``parity_report.json``, ``disk_budget.json``, ``mbp1_semantics.json``
    * ``experiments/X0_<run_id>/{manifest,metrics,conclusion}.json``

    Every write is atomic (temp + replace) and idempotent; an existing
    artifact whose content diverges from this run fails closed.
    """
    analysis_root_resolved = Path(analysis_root).resolve()
    build_payload, checkpoint_path = _load_build_checkpoint(build_checkpoint)
    if store_root is not None:
        store_root_resolved = Path(store_root).resolve()
    else:
        recorded_store_root = build_payload.get("store_root")
        _require(
            recorded_store_root,
            "build result missing store_root and no store_root supplied",
        )
        store_root_resolved = Path(str(recorded_store_root)).resolve()
    validate_build_result(
        build_payload,
        store_root=store_root_resolved,
        analysis_root=analysis_root_resolved,
    )
    validate_parity_report(
        parity_report,
        store_dates=sorted(build_payload["days"]),
    )
    data_store_cfg, splits_cfg = validate_config_mappings(data_store_cfg, splits_cfg)
    valid_reviews, normalized_reviews, rejected_reviews = validate_manual_reviews(
        manual_reviews
    )

    run_id = (run_id or utc_run_id("X0")[3:]).strip()
    if not _RUN_ID_RE.fullmatch(run_id):
        raise X0FinalizeError(f"invalid run_id {run_id!r}")
    run_dir = f"X0_{run_id}"

    eligibility = compute_day_eligibility(build_payload["days"], valid_reviews)
    eligible_dates = [d for d, rec in eligibility.items() if rec["eligible"]]
    holdout_end = compute_holdout_end(build_payload["days"], splits_cfg)
    splits = materialize_splits(
        eligible_dates, holdout_end, splits_cfg, data_store_cfg
    )

    annual_gates = evaluate_annual_gates(
        build_payload,
        splits_cfg,
        holdout_end,
        min_coverage=min_coverage,
        min_q1_q6_pass_rate=min_q1_q6_pass_rate,
    )
    expected_total = (
        int(annual_gates["2022"]["expected_open_sessions"])
        + int(annual_gates["2023"]["expected_open_sessions"])
        + int(
            annual_gates["pooled_2024_through_partial_2026"][
                "expected_open_sessions"
            ]
        )
    )
    disk_budget = evaluate_disk_budget(
        pilot_disk_record,
        expected_total_sessions=expected_total,
        headroom_fraction=headroom_fraction,
    )
    verdict, criteria, reasons = evaluate_pass(
        annual_gates,
        parity_report,
        disk_budget,
        tick_size=tick_size,
        q7_min_corr=q7_min_corr,
    )

    completed_at_utc = str(
        build_payload.get("completed_at_utc") or build_payload.get("updated_at_utc") or ""
    )
    q7 = parity_report["q7"]
    q8 = parity_report["q8"]

    # ---- payloads ---------------------------------------------------------
    daily_qc_rows: List[Dict[str, Any]] = []
    for trading_date in sorted(build_payload["days"]):
        record = build_payload["days"][trading_date]
        qc = record.get("qc")
        if not isinstance(qc, dict):
            continue
        checks = qc["checks"]
        day = eligibility[trading_date]
        daily_qc_rows.append(
            {
                "trading_date": trading_date,
                "status": record.get("status"),
                "plan_eligible": day["plan_eligible"],
                "q1_pass": day["q1_pass"],
                "q2_pass": day["q2_pass"],
                "q3_pass": day["q3_pass"],
                "q4_pass": day["q4_pass"],
                "q5_pass": day["q5_pass"],
                "q6_pass": day["q6_pass"],
                "qc_pass": day["qc_pass"],
                "research_eligible": day["research_eligible"],
                "review_flags": "|".join(day["review_flags"]),
                "review_approved": day["review_approved"],
                "eligible": day["eligible"],
                "n_rows": int(record.get("n_rows", 0)),
                "selected_contract": record.get("selected_contract"),
                "reason": day["reason"],
            }
        )
    daily_qc_df = pd.DataFrame(
        daily_qc_rows,
        columns=[
            "trading_date",
            "status",
            "plan_eligible",
            "q1_pass",
            "q2_pass",
            "q3_pass",
            "q4_pass",
            "q5_pass",
            "q6_pass",
            "qc_pass",
            "research_eligible",
            "review_flags",
            "review_approved",
            "eligible",
            "n_rows",
            "selected_contract",
            "reason",
        ],
    ).sort_values("trading_date", kind="stable").reset_index(drop=True)
    daily_qc_text = daily_qc_df.to_csv(index=False, lineterminator="\n")

    store_rows: List[Dict[str, Any]] = []
    for trading_date in sorted(build_payload["days"]):
        record = build_payload["days"][trading_date]
        day = eligibility[trading_date]
        source_files = sorted(record.get("source_files") or [])
        store_rows.append(
            {
                "trading_date": trading_date,
                "status": record.get("status"),
                "builder_sha256": record.get("builder_sha256"),
                "selected_contract": record.get("selected_contract"),
                "output_path": record.get("output_path"),
                "output_sha256": record.get("output_sha256"),
                "source_files": "|".join(str(path) for path in source_files),
                "n_rows": int(record.get("n_rows", 0)),
                "n_crossed_dropped": int(record.get("n_crossed_dropped", 0)),
                "n_rows_after_crossed_drop": int(
                    record.get("n_rows_after_crossed_drop", 0)
                ),
                "trailing_median": record.get("trailing_median"),
                "plan_eligible": day["plan_eligible"],
                "qc_pass": day["qc_pass"],
                "research_eligible": day["research_eligible"],
                "eligible": day["eligible"],
                "built_at_utc": record.get("built_at_utc"),
            }
        )
    store_manifest_df = pd.DataFrame(
        store_rows,
        columns=[
            "trading_date",
            "status",
            "builder_sha256",
            "selected_contract",
            "output_path",
            "output_sha256",
            "source_files",
            "n_rows",
            "n_crossed_dropped",
            "n_rows_after_crossed_drop",
            "trailing_median",
            "plan_eligible",
            "qc_pass",
            "research_eligible",
            "eligible",
            "built_at_utc",
        ],
    ).sort_values("trading_date", kind="stable").reset_index(drop=True)
    store_manifest_text = store_manifest_df.to_csv(index=False, lineterminator="\n")

    eligible_payload = {
        "schema_version": SCHEMA_VERSION,
        "program": PROGRAM_ID,
        "holdout_opened": False,
        "dates": eligible_dates,
        "count": len(eligible_dates),
        "eligibility_rule": (
            "plan pass AND Q1-Q3 pass AND (Q4-Q6 all pass OR valid dated "
            "per-date manual APPROVE with reviewer and rationale)"
        ),
        "days": eligibility,
    }

    semantics_payload = {
        "schema_version": SCHEMA_VERSION,
        "program": PROGRAM_ID,
        "semantics": data_store_cfg["mbp1_semantics"],
        "counts": parity_report["semantics_audit"],
        "holdout_opened": False,
        "note": (
            "store-wide day-level MBP-1 semantics audit; no episodes, "
            "features, or holdout statistics"
        ),
    }

    experiment_payloads = {
        "manifest": {
            "schema_version": SCHEMA_VERSION,
            "experiment": "X0",
            "run_id": run_id,
            "program": PROGRAM_ID,
            "slice": SLICE_ID,
            "holdout_opened": False,
            "inputs": {
                "build_checkpoint_path": checkpoint_path,
                "plan_sha256": build_payload["plan_sha256"],
                "medians_sha256": build_payload["medians_sha256"],
                "builder_sha256": build_payload["builder_sha256"],
                "sources": list(build_payload["sources"]),
                "source_hashes": dict(build_payload["source_hashes"]),
                "parity_report_sha256": _json_sha(parity_report),
                "data_store_yaml_sha256": _json_sha(data_store_cfg),
                "splits_yaml_sha256": _json_sha(splits_cfg),
                "manual_review_records": normalized_reviews,
                "manual_reviews_sha256": _json_sha(normalized_reviews),
                "pilot_disk_record_sha256": (
                    _json_sha(pilot_disk_record)
                    if pilot_disk_record is not None
                    else None
                ),
            },
            "outputs": {
                "qc_daily_qc_csv": str((analysis_root_resolved / "qc" / "daily_qc.csv")),
                "store_manifest_csv": str(
                    analysis_root_resolved / "manifests" / "store_manifest.csv"
                ),
                "eligible_dates_json": str(
                    analysis_root_resolved / "eligible_dates.json"
                ),
                "split_manifest_json": str(
                    analysis_root_resolved / "split_manifest.json"
                ),
                "parity_report_json": str(
                    analysis_root_resolved / "parity_report.json"
                ),
                "disk_budget_json": str(analysis_root_resolved / "disk_budget.json"),
                "mbp1_semantics_json": str(
                    analysis_root_resolved / "mbp1_semantics.json"
                ),
                "experiment_dir": str(
                    analysis_root_resolved / "experiments" / run_dir
                ),
            },
            "completed_at_utc": completed_at_utc,
        },
        "metrics": {
            "schema_version": SCHEMA_VERSION,
            "experiment": "X0",
            "run_id": run_id,
            "holdout_opened": False,
            "annual_gates": annual_gates,
            "q7": {
                "passed": q7["passed"],
                "global_corr": q7["global_corr"],
                "sign_flips": q7["sign_flips"],
                "overlap_count": q7["overlap_count"],
                "overlap_dates": q7["overlap_dates"],
            },
            "q8": {
                "passed": q8["passed"],
                "global_max_abs_diff": q8["global_max_abs_diff"],
                "tick_size": tick_size,
            },
            "disk_budget": disk_budget,
            "eligible_dates_count": len(eligible_dates),
            "split_counts": {
                name: len(block["dates"]) for name, block in splits["splits"].items()
            },
            "holdout_end": holdout_end,
            "verdict": verdict,
        },
        "conclusion": {
            "schema_version": SCHEMA_VERSION,
            "experiment": "X0",
            "run_id": run_id,
            "verdict": verdict,
            "pass_criteria": criteria,
            "reasons": reasons,
            "holdout_opened": False,
            "summary": (
                "X0 store build, QC, and split freeze "
                f"{'PASS' if verdict == 'PASS' else 'does not PASS'}."
            ),
        },
    }

    # ---- writes -----------------------------------------------------------
    artifacts: Dict[str, str] = {}
    experiment_root = analysis_root_resolved / "experiments" / run_dir
    targets = [
        ("qc/daily_qc.csv", daily_qc_text, "csv"),
        ("manifests/store_manifest.csv", store_manifest_text, "csv"),
        ("eligible_dates.json", eligible_payload, "json"),
        ("split_manifest.json", splits, "json"),
        ("parity_report.json", parity_report, "json"),
        ("disk_budget.json", disk_budget, "json"),
        ("mbp1_semantics.json", semantics_payload, "json"),
        (f"experiments/{run_dir}/manifest.json", experiment_payloads["manifest"], "json"),
        (f"experiments/{run_dir}/metrics.json", experiment_payloads["metrics"], "json"),
        (f"experiments/{run_dir}/conclusion.json", experiment_payloads["conclusion"], "json"),
    ]
    for relative, payload, kind in targets:
        path = (analysis_root_resolved / relative).resolve()
        try:
            ensure_under(path, analysis_root_resolved)
        except ValueError as exc:
            raise X0FinalizeError(
                f"artifact {path} escapes analysis root {analysis_root_resolved}"
            ) from exc
        if kind == "csv":
            status = _write_csv_artifact(path, str(payload))
        else:
            status = _write_json_artifact(path, payload)
        artifacts[relative] = {"path": str(path), "status": status}

    return {
        "program": PROGRAM_ID,
        "slice": SLICE_ID,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "verdict": verdict,
        "pass_criteria": criteria,
        "reasons": reasons,
        "holdout_opened": False,
        "analysis_root": str(analysis_root_resolved),
        "store_root": str(store_root_resolved),
        "eligible_dates_count": len(eligible_dates),
        "eligible_dates": eligible_dates,
        "holdout_end": holdout_end,
        "splits": splits,
        "annual_gates": annual_gates,
        "disk_budget": disk_budget,
        "rejected_review_records": rejected_reviews,
        "artifacts": artifacts,
    }
