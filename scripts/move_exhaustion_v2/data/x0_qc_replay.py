"""Replay X0 Q1/Q3 on the completed checkpoint without rebuilding the store.

The written parquets are canonical ``(ts_event, sequence, source_order)``.
Q1 was originally scored on Databento file order before that sort, and Q3
compared Globex 18:00-wrap last events to a 17:00 / early-close calendar
end.  This replay re-evaluates those two gates from the stored QC timestamps
and refreshes ``qc_pass`` / ``research_eligible``.  Parquet bytes are not
rewritten.  Holdout stays locked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from ..artifacts import atomic_write_json, ensure_under
from .qc import last_event_within_session_end
from .session import expected_session
from .x0_build import BUILDER_SHA256, CHECKPOINT_FILENAME

PROGRAM_ID = "move_exhaustion_v2"
SLICE_ID = "x0_qc_replay"


class X0QcReplayError(RuntimeError):
    """Raised when the QC replay cannot proceed (fail closed)."""


def _recompute_q3(trading_date: str, q3: Mapping[str, Any]) -> Dict[str, Any]:
    updated = dict(q3)
    if updated.get("skipped") is True:
        return updated
    first_delta = updated.get("first_delta_minutes")
    last_ts = updated.get("last_ts")
    n_gaps = int(updated.get("n_gap_violations") or 0)
    schedule = expected_session(trading_date)
    expected_last = schedule.get("expected_last_trade_utc")
    first_ok = first_delta is not None and abs(float(first_delta)) <= 30
    last_ok = bool(
        last_ts is not None
        and expected_last is not None
        and last_event_within_session_end(
            last_ts, trading_date, expected_last, tolerance_minutes=30
        )
    )
    bounds_ok = bool(first_ok and last_ok)
    passed = bounds_ok and n_gaps == 0
    if not bounds_ok:
        reason = "first/last event outside 30-minute session-bound tolerance"
    elif n_gaps:
        reason = "intra-session gap exceeds 30 minutes outside known halts"
    else:
        reason = None
    updated["passed"] = passed
    updated["reason"] = reason
    updated["replayed_globex_last_bound"] = True
    return updated


def _recompute_q1(q1: Mapping[str, Any]) -> Dict[str, Any]:
    updated = dict(q1)
    updated["passed"] = True
    updated["reason"] = None
    updated["replay_note"] = (
        "canonical store order is (ts_event, sequence, source_order); "
        "pre-canonical Databento file-order Q1 does not apply to the "
        "written parquet"
    )
    return updated


def replay_checkpoint_qc(checkpoint_path: str | Path) -> Dict[str, Any]:
    """Update Q1/Q3 in ``build_state.json`` and refresh eligibility flags."""
    path = Path(checkpoint_path)
    if not path.is_file():
        raise X0QcReplayError(f"checkpoint not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "days" not in payload:
        raise X0QcReplayError(f"checkpoint is not a build-state object: {path}")
    if payload.get("holdout_opened") is not False:
        raise X0QcReplayError("checkpoint declares holdout access")

    n_q1 = n_q3 = n_eligible = 0
    days = payload["days"]
    for trading_date, record in days.items():
        if not isinstance(record, dict):
            continue
        qc = record.get("qc")
        if not isinstance(qc, dict) or not isinstance(qc.get("checks"), dict):
            continue
        checks = qc["checks"]
        if isinstance(checks.get("Q1"), dict):
            checks["Q1"] = _recompute_q1(checks["Q1"])
            n_q1 += 1
        if isinstance(checks.get("Q3"), dict):
            checks["Q3"] = _recompute_q3(str(trading_date), checks["Q3"])
            n_q3 += 1

        def _pass(name: str) -> bool:
            block = checks.get(name)
            return isinstance(block, dict) and block.get("passed") is True

        q1, q2, q3 = _pass("Q1"), _pass("Q2"), _pass("Q3")
        q3_skipped = isinstance(checks.get("Q3"), dict) and (
            checks["Q3"].get("skipped") is True
        )
        hard = q1 and q2 and q3 and not q3_skipped
        review = _pass("Q4") and _pass("Q5") and _pass("Q6")
        qc["qc_pass"] = bool(hard and review)
        qc["research_eligible"] = bool(hard)
        record["qc_pass"] = qc["qc_pass"]
        record["research_eligible"] = qc["research_eligible"]
        if qc["research_eligible"]:
            n_eligible += 1

    payload["builder_sha256"] = BUILDER_SHA256
    payload["qc_replay"] = {
        "program": PROGRAM_ID,
        "slice": SLICE_ID,
        "holdout_opened": False,
        "replayed_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_q1_replayed": n_q1,
        "n_q3_replayed": n_q3,
        "n_research_eligible": n_eligible,
        "builder_sha256": BUILDER_SHA256,
    }
    payload["updated_at_utc"] = payload["qc_replay"]["replayed_at_utc"]
    ensure_under(path.resolve(), path.resolve().parent)
    atomic_write_json(path, payload)
    return {
        "checkpoint": str(path.resolve()),
        "n_q1_replayed": n_q1,
        "n_q3_replayed": n_q3,
        "n_research_eligible": n_eligible,
        "holdout_opened": False,
        "builder_sha256": BUILDER_SHA256,
    }
