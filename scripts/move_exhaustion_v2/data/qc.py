"""Pure X0 QC gates Q1-Q6 (PROGRAM.md §2, config/frozen/data_store.yaml).

Each check is a pure function over one day's derived frame (or a small set
of scalar inputs) and returns a dict with at least ``check``, ``passed`` and
``flag_only``.  ``flag_only`` marks review-style gates (Q4/Q5/Q6) whose
failure does not by itself exclude a day, per the frozen on-failure column;
hard gates (Q1/Q2/Q3) always set ``flag_only=False``.

:func:`run_daily_qc` combines the six gates into a per-day record with
``qc_pass`` (all six pass) and ``research_eligible`` (Q1 & Q2 & Q3 pass,
per PROGRAM.md §2).  :func:`aggregate_annual_coverage` applies the
OQ-6 rule (≥95% expected-session coverage and ≥95% Q1-Q6 pass rate per year
for 2022-2023) and reports per-year coverage/pass rates.

No module-level state: every function is chunk-friendly for per-day or
per-month orchestration.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .session import (
    KIND_CLOSED,
    _as_date,
    expected_open_sessions,
    expected_session,
    session_bounds,
)

START_TOLERANCE_MINUTES = 30  # Q3
END_TOLERANCE_MINUTES = 30    # Q3
MAX_INTRADAY_GAP_MINUTES = 30  # Q3
MAX_CROSSED_FRACTION = 0.0001  # Q2: 0.01%
EVENT_COUNT_LOWER_FACTOR = 0.1  # Q4
EVENT_COUNT_UPPER_FACTOR = 10.0  # Q4
PRIOR_SESSION_MIN_SHARE = 0.5   # Q5
MAX_RESETS_PER_DAY = 5          # Q6


def _utc_ns(value: Any) -> int:
    """UTC epoch nanoseconds for a datetime/Timestamp (naive -> UTC)."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.value)


def _minutes_apart(left: Any, right: Any) -> float:
    a = pd.Timestamp(left)
    b = pd.Timestamp(right)
    if a.tzinfo is None:
        a = a.tz_localize("UTC")
    else:
        a = a.tz_convert("UTC")
    if b.tzinfo is None:
        b = b.tz_localize("UTC")
    else:
        b = b.tz_convert("UTC")
    return abs((a - b).total_seconds()) / 60.0


def last_event_within_session_end(
    last: Any,
    trading_date: date | str,
    expected_last: Any,
    *,
    tolerance_minutes: float = END_TOLERANCE_MINUTES,
) -> bool:
    """True when ``last`` is within tolerance of a valid session end.

    The derived store is sliced at the 18:00 ET boundary, so a full Globex
    day ends near 17:00 ET or just before the next 18:00 ET open (the
    17:00–18:00 ET maintenance window).  pandas-market-calendars early
    closes (13:00 ET holidays) remain valid ends; they do not invalidate a
    day whose last event is at the electronic-session close.
    """
    trading = _as_date(trading_date)
    nominal_end = session_bounds(trading)[1]
    next_open = session_bounds(trading + timedelta(days=1))[0]
    return (
        _minutes_apart(last, expected_last) <= tolerance_minutes
        or _minutes_apart(last, nominal_end) <= tolerance_minutes
        or _minutes_apart(last, next_open) <= tolerance_minutes
    )


def check_timestamp_monotonicity(
    frame: pd.DataFrame,
    *,
    ts_col: str = "ts_event",
    seq_col: str = "sequence",
) -> Dict[str, Any]:
    """Q1: lexicographically non-decreasing ``(ts_event, sequence)``.

    Equal pairs are valid multi-record CME events and are counted separately
    as a duplicate-key diagnostic; they are not exact-row duplicates.
    """
    result: Dict[str, Any] = {
        "check": "Q1",
        "flag_only": False,
        "n_rows": int(len(frame)),
    }
    if frame.empty:
        return {**result, "passed": False, "n_violations": 0,
                "first_violation_row": None, "reason": "empty frame"}
    missing = [c for c in (ts_col, seq_col) if c not in frame.columns]
    if missing:
        raise KeyError(f"Q1 missing columns: {sorted(missing)}")
    ts = pd.to_datetime(frame[ts_col], utc=True, errors="coerce")
    seq = pd.to_numeric(frame[seq_col], errors="coerce")
    if ts.isna().any() or seq.isna().any():
        n_invalid = int(ts.isna().sum() + seq.isna().sum())
        first = int(frame.index[ts.isna() | seq.isna()][0])
        return {**result, "passed": False, "n_violations": n_invalid,
                "first_violation_row": first,
                "reason": "invalid (unparseable) timestamps or sequences"}
    ts_ns = ts.to_numpy(dtype="datetime64[ns]").astype("int64")
    seq_n = seq.to_numpy(dtype="float64")
    ts_ok = ts_ns[1:] > ts_ns[:-1]
    same_ts = ts_ns[1:] == ts_ns[:-1]
    seq_ok = seq_n[1:] >= seq_n[:-1]
    duplicate_keys = same_ts & (seq_n[1:] == seq_n[:-1])
    violations = ~(ts_ok | (same_ts & seq_ok))
    n_violations = int(violations.sum())
    first = None
    if n_violations:
        first = int(frame.index[np.flatnonzero(violations)[0] + 1])
    return {**result, "passed": n_violations == 0, "n_violations": n_violations,
            "n_duplicate_key_pairs": int(duplicate_keys.sum()),
            "first_violation_row": first,
            "reason": None if n_violations == 0 else "non-monotonic (ts, sequence)"}


def check_crossed_book(
    frame: pd.DataFrame,
    *,
    bid_px_col: str = "bid_px_00",
    ask_px_col: str = "ask_px_00",
    max_crossed_fraction: float = MAX_CROSSED_FRACTION,
    max_logged_rows: int = 1_000,
) -> Dict[str, Any]:
    """Q2: ``bid_px_00 < ask_px_00`` for >99.99% of rows (<=0.01% crossed).

    Missing prices count as crossed (conservative).  Crossed/locked row
    indices are returned for logging/dropping by the caller.
    """
    result: Dict[str, Any] = {
        "check": "Q2",
        "flag_only": False,
        "n_rows": int(len(frame)),
    }
    if frame.empty:
        return {**result, "passed": False, "n_crossed": 0, "fraction_crossed": 0.0,
                "crossed_rows": [], "reason": "empty frame"}
    missing = [c for c in (bid_px_col, ask_px_col) if c not in frame.columns]
    if missing:
        raise KeyError(f"Q2 missing columns: {sorted(missing)}")
    bid = pd.to_numeric(frame[bid_px_col], errors="coerce")
    ask = pd.to_numeric(frame[ask_px_col], errors="coerce")
    present = bid.notna() & ask.notna()
    crossed = present & ~(bid < ask)  # crossed or locked
    bad = crossed | ~present
    bad_indices = [int(i) for i in frame.index[bad]]
    n_crossed = len(bad_indices)
    fraction = n_crossed / len(frame)
    passed = fraction < max_crossed_fraction
    return {
        **result,
        "passed": passed,
        "n_crossed": n_crossed,
        "fraction_crossed": fraction,
        "crossed_rows": bad_indices[:max_logged_rows],
        "crossed_rows_truncated": n_crossed > max_logged_rows,
        "max_crossed_fraction": max_crossed_fraction,
        "reason": None if passed
        else "crossed/locked book fraction exceeds 0.01%",
    }


def check_session_coverage(
    frame: pd.DataFrame,
    trading_date: date | str,
    *,
    ts_col: str = "ts_event",
    start_tolerance_minutes: float = START_TOLERANCE_MINUTES,
    end_tolerance_minutes: float = END_TOLERANCE_MINUTES,
    max_gap_minutes: float = MAX_INTRADAY_GAP_MINUTES,
    known_halts: Sequence[Tuple[Any, Any]] = (),
) -> Dict[str, Any]:
    """Q3: first/last event near session bounds; no intra-session gap > 30 min.

    Uses ``expected_session`` so partial holidays compare the last event to
    the 13:00 ET expected last trade rather than the nominal 17:00 ET end.
    ``known_halts`` is a sequence of ``(start, end)`` UTC intervals; a gap
    fully contained in one halt does not count.  Assumes the frame is in
    canonical (ts_event) order (Q1 enforces that).
    """
    schedule = expected_session(trading_date)
    start_utc = schedule["start_utc"]
    expected_last = schedule["expected_last_trade_utc"]
    result: Dict[str, Any] = {
        "check": "Q3",
        "flag_only": False,
        "expected_kind": schedule["kind"],
        "expected_reason": schedule["reason"],
        "n_rows": int(len(frame)),
    }
    if schedule["kind"] == KIND_CLOSED:
        if frame.empty:
            return {**result, "passed": True, "skipped": True,
                    "reason": "expected-closed session with no data"}
        return {**result, "passed": False, "skipped": False,
                "reason": "unexpected data for expected-closed session"}
    if frame.empty:
        return {**result, "passed": False, "skipped": False,
                "reason": "empty frame for expected open session"}
    if ts_col not in frame.columns:
        raise KeyError(f"Q3 missing column {ts_col!r}")
    ts = pd.to_datetime(frame[ts_col], utc=True, errors="coerce")
    if ts.isna().any():
        return {**result, "passed": False, "skipped": False,
                "reason": "invalid timestamps in session coverage check"}
    start_tol = timedelta(minutes=float(start_tolerance_minutes))
    end_tol = timedelta(minutes=float(end_tolerance_minutes))
    first = ts.iloc[0]
    last = ts.iloc[-1]
    first_delta = (first - start_utc).total_seconds() / 60.0
    last_delta = (expected_last - last).total_seconds() / 60.0
    bounds_ok = (
        abs(first_delta) <= start_tolerance_minutes
        and last_event_within_session_end(
            last,
            trading_date,
            expected_last,
            tolerance_minutes=end_tolerance_minutes,
        )
    )

    start_ns = _utc_ns(start_utc)
    end_ns = _utc_ns(expected_last)
    ts_ns = ts.to_numpy(dtype="datetime64[ns]")
    pair_starts = ts_ns[:-1]
    pair_ends = ts_ns[1:]
    inside = (pair_starts.astype("int64") >= start_ns) & (
        pair_ends.astype("int64") <= end_ns
    )
    gap_s = np.diff(ts_ns.astype("int64")) / 1e9
    gap_s = gap_s[inside]
    pair_starts = pair_starts[inside]
    pair_ends = pair_ends[inside]
    max_gap_s = float(max_gap_minutes) * 60.0
    effective_gap_s = gap_s.copy()
    pair_start_ns = pair_starts.astype("int64")
    pair_end_ns = pair_ends.astype("int64")
    for halt in known_halts:
        h0 = _utc_ns(halt[0])
        h1 = _utc_ns(halt[1])
        if h1 < h0:
            raise ValueError("known halt end precedes start")
        overlap_ns = np.maximum(
            0,
            np.minimum(pair_end_ns, h1) - np.maximum(pair_start_ns, h0),
        )
        effective_gap_s -= overlap_ns / 1e9
    effective_gap_s = np.maximum(effective_gap_s, 0.0)
    exceeds = effective_gap_s > max_gap_s
    n_gap_violations = int(exceeds.sum())
    max_gap = float(gap_s.max()) / 60.0 if len(gap_s) else None
    passed = bounds_ok and n_gap_violations == 0
    reason = None
    if not bounds_ok:
        reason = "first/last event outside 30-minute session-bound tolerance"
    elif n_gap_violations:
        reason = "intra-session gap exceeds 30 minutes outside known halts"
    return {
        **result,
        "passed": passed,
        "skipped": False,
        "first_ts": first,
        "last_ts": last,
        "first_delta_minutes": first_delta,
        "last_delta_minutes": last_delta,
        "max_intraday_gap_minutes": max_gap,
        "max_effective_gap_minutes": (
            float(effective_gap_s.max()) / 60.0 if len(effective_gap_s) else None
        ),
        "n_gap_violations": n_gap_violations,
        "known_halts": list(known_halts),
        "reason": reason,
    }


def check_event_count_sanity(
    frame: pd.DataFrame,
    trailing_median: Optional[float],
    *,
    lower_factor: float = EVENT_COUNT_LOWER_FACTOR,
    upper_factor: float = EVENT_COUNT_UPPER_FACTOR,
) -> Dict[str, Any]:
    """Q4: daily row count within [0.1x, 10x] of the trailing-20-day median."""
    n_rows = int(len(frame))
    result: Dict[str, Any] = {
        "check": "Q4",
        "flag_only": True,
        "n_rows": n_rows,
        "trailing_median": trailing_median,
    }
    if (
        trailing_median is None
        or not np.isfinite(float(trailing_median))
        or float(trailing_median) <= 0
    ):
        return {**result, "passed": True, "flagged": False,
                "reason": "no valid trailing-20-day median (warmup); not flagged"}
    median = float(trailing_median)
    lo, hi = lower_factor * median, upper_factor * median
    in_range = lo <= n_rows <= hi
    return {
        **result,
        "passed": in_range,
        "flagged": not in_range,
        "lower_bound": lo,
        "upper_bound": hi,
        "reason": None if in_range
        else "daily row count outside [0.1x, 10x] of trailing median",
    }


def check_roll_audit(
    *,
    chosen_contract: str,
    prior_winner: str,
    prior_winner_share: float,
    trading_date: date | str | None = None,
    documented_roll_windows: Sequence[date | str] = (),
) -> Dict[str, Any]:
    """Q5: front-contract continuity + >50% prior-session T-volume share.

    A contract change is only acceptable on a documented roll-window date;
    by construction a changed contract cannot also hold >50% of the prior
    session's T-volume, so this is a review gate (``flag_only=True``).
    """
    share = float(prior_winner_share)
    share_ok = share > PRIOR_SESSION_MIN_SHARE
    same = str(chosen_contract) == str(prior_winner)
    window_dates = {str(d) for d in documented_roll_windows}
    in_window = str(trading_date) in window_dates if trading_date is not None else False
    passed = (same and share_ok) or ((not same) and in_window and share_ok)
    return {
        "check": "Q5",
        "flag_only": True,
        "flagged": not passed,
        "passed": passed,
        "chosen_contract": str(chosen_contract),
        "prior_winner": str(prior_winner),
        "prior_winner_share": share,
        "contract_change": not same,
        "in_documented_roll_window": bool(in_window),
        "reason": None if passed else (
            "front contract changed outside documented roll windows or prior winner share <= 50%"
        ),
    }


def check_book_resets(
    frame: pd.DataFrame,
    *,
    action_col: str = "action",
    max_resets: int = MAX_RESETS_PER_DAY,
) -> Dict[str, Any]:
    """Q6: count of reset/clear actions; >5 resets flags the day for review."""
    if action_col not in frame.columns:
        raise KeyError(f"Q6 missing action column {action_col!r}")
    n_resets = int((frame[action_col].astype(str).str.upper() == "R").sum())
    passed = n_resets <= max_resets
    return {
        "check": "Q6",
        "flag_only": True,
        "flagged": not passed,
        "passed": passed,
        "n_resets": n_resets,
        "max_resets": max_resets,
        "reason": None if passed
        else f"{n_resets} resets exceed review threshold {max_resets}",
    }


def run_daily_qc(
    frame: pd.DataFrame,
    trading_date: date | str,
    *,
    trailing_median: Optional[float] = None,
    chosen_contract: Optional[str] = None,
    prior_winner: Optional[str] = None,
    prior_winner_share: Optional[float] = None,
    documented_roll_windows: Sequence[date | str] = (),
    known_halts: Sequence[Tuple[Any, Any]] = (),
) -> Dict[str, Any]:
    """Run Q1-Q6 for one day; return the day's QC record.

    ``qc_pass`` requires all six gates to pass.  ``research_eligible``
    follows the frozen on-failure table: Q1/Q2/Q3 failures exclude a day;
    Q4/Q5/Q6 are review flags.
    """
    checks: Dict[str, Dict[str, Any]] = {
        "Q1": check_timestamp_monotonicity(frame),
        "Q2": check_crossed_book(frame),
        "Q3": check_session_coverage(frame, trading_date, known_halts=known_halts),
    }
    if chosen_contract is None or prior_winner is None or prior_winner_share is None:
        checks["Q5"] = {
            "check": "Q5",
            "flag_only": True,
            "flagged": True,
            "passed": False,
            "chosen_contract": chosen_contract,
            "prior_winner": prior_winner,
            "prior_winner_share": prior_winner_share,
            "contract_change": None,
            "in_documented_roll_window": None,
            "reason": "missing front-contract inputs (bootstrap day or no prior session)",
        }
    else:
        checks["Q5"] = check_roll_audit(
            chosen_contract=chosen_contract,
            prior_winner=prior_winner,
            prior_winner_share=prior_winner_share,
            trading_date=trading_date,
            documented_roll_windows=documented_roll_windows,
        )
    checks["Q4"] = check_event_count_sanity(frame, trailing_median)
    checks["Q6"] = check_book_resets(frame)

    qc_pass = all(check["passed"] for check in checks.values())
    q3 = checks["Q3"]
    research_eligible = (
        checks["Q1"]["passed"]
        and checks["Q2"]["passed"]
        and checks["Q3"]["passed"]
        and q3.get("skipped") is not True
    )
    return {
        "trading_date": str(_as_date(trading_date)),
        "n_rows": int(len(frame)),
        "checks": checks,
        "qc_pass": bool(qc_pass),
        "research_eligible": bool(research_eligible),
    }


def aggregate_annual_coverage(
    daily_results: Sequence[Mapping[str, Any]],
    *,
    years: Optional[Sequence[int]] = None,
    expected_start: date | str | None = None,
    expected_end: date | str | None = None,
    min_expected_coverage: float = 0.95,
    min_q1_q6_pass_rate: float = 0.95,
) -> Dict[str, Any]:
    """Annual expected-session coverage and Q1-Q6 pass rates (OQ-6 rule).

    ``daily_results`` are :func:`run_daily_qc` records.  Expected open
    sessions come from :func:`session.expected_open_sessions` over each
    year's calendar (or over ``expected_start``..``expected_end`` when
    supplied, for partial-year windows such as 2026 Q1).  A day counts
    toward Q1-Q6 pass rate only if it was expected-open and built.
    """
    results_by_date = {str(r["trading_date"]): r for r in daily_results}
    result_years = sorted({date.fromisoformat(d).year for d in results_by_date})
    expected_by_year: Dict[int, Dict[str, Any]] = {}
    if expected_start is not None or expected_end is not None:
        if expected_start is None or expected_end is None:
            raise ValueError("expected_start and expected_end must be supplied together")
        span = expected_open_sessions(expected_start, expected_end)
        for session in span:
            year = date.fromisoformat(str(session["trading_date"])).year
            expected_by_year.setdefault(year, {})[str(session["trading_date"])] = session
        if years is None:
            years = sorted(set(result_years) | set(expected_by_year))
        else:
            years = sorted(set(years) | set(expected_by_year))
    else:
        if years is None:
            years = result_years
        for year in years:
            expected_by_year[year] = {
                str(s["trading_date"]): s
                for s in expected_open_sessions(date(year, 1, 1), date(year, 12, 31))
            }

    per_year: Dict[str, Dict[str, Any]] = {}
    all_eligible: List[str] = []
    expected_all = {d for year in expected_by_year.values() for d in year}
    unexpected_days: List[str] = sorted(
        d for d in results_by_date if d not in expected_all
    )
    for year in sorted(expected_by_year):
        expected = expected_by_year[year]
        built_dates = [d for d in expected if d in results_by_date]
        built = len(built_dates)
        missing = sorted(set(expected) - set(results_by_date))
        # Annual 95% rate is the hard-gate (Q1-Q3) pass rate. Q4/Q5/Q6 are
        # review flags (on_failure: manual review) and do not fail the year.
        qc_pass_dates = [
            d for d in built_dates if results_by_date[d].get("research_eligible") is True
        ]
        eligible_dates = sorted(
            d for d in built_dates if results_by_date[d].get("research_eligible") is True
        )
        coverage = built / len(expected) if expected else 0.0
        pass_rate = len(qc_pass_dates) / built if built else 0.0
        per_year[str(year)] = {
            "year": year,
            "expected_open_sessions": len(expected),
            "built_sessions": built,
            "missing_sessions": missing,
            "coverage_ratio": coverage,
            "qc_pass_days": len(qc_pass_dates),
            "q1_q6_pass_rate": pass_rate,
            "eligible_dates": eligible_dates,
            "meets_expected_coverage": coverage >= min_expected_coverage,
            "meets_q1_q6_pass_rate": pass_rate >= min_q1_q6_pass_rate,
            "meets_year_threshold": (
                coverage >= min_expected_coverage and pass_rate >= min_q1_q6_pass_rate
            ),
        }
        all_eligible.extend(eligible_dates)

    total_expected = sum(len(v) for v in expected_by_year.values())
    total_built = sum(v["built_sessions"] for v in per_year.values())
    total_pass = sum(v["qc_pass_days"] for v in per_year.values())
    return {
        "per_year": per_year,
        "years": sorted(expected_by_year),
        "min_expected_coverage": min_expected_coverage,
        "min_q1_q6_pass_rate": min_q1_q6_pass_rate,
        "total_expected_open_sessions": total_expected,
        "total_built_sessions": total_built,
        "overall_coverage_ratio": total_built / total_expected if total_expected else 0.0,
        "overall_q1_q6_pass_rate": total_pass / total_built if total_built else 0.0,
        "meets_all_year_thresholds": bool(per_year) and all(
            v["meets_year_threshold"] for v in per_year.values()
        ),
        "unexpected_days": unexpected_days,
        "eligible_dates": sorted(all_eligible),
        "q7_q8_scope": (
            "global overlap gates against the frozen TBBO matrix; not part of "
            "this per-year Q1-Q6 aggregation"
        ),
    }
