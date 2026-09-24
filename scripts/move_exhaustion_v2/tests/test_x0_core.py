"""Focused synthetic tests for the X0 data core (Move Exhaustion V2).

Covers the pure, synthetic-testable surface only: session bounds/calendar,
ZN quarterly outright parsing + causal front-contract selection, MBP-1 event
semantics (incl. modify decomposition), QC gates Q1-Q6 + annual coverage
aggregation, and the MBP-1 per-day store derivation (pre-event depth from
the previous row, mid/spread/clocks, reset re-seeding).  No archive, store,
network or config mutation is touched.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from scripts.move_exhaustion_v2.data.event_semantics import (
    ADD_ASK,
    ADD_BID,
    ADD_UNKNOWN,
    AGG_BUY,
    AGG_SELL,
    AGG_UNKNOWN,
    CXL_ASK,
    CXL_BID,
    CXL_UNKNOWN,
    MOD_ADD_LIKE,
    MOD_CXL_ADD,
    MOD_CXL_LIKE,
    MOD_UNKNOWN,
    RESET,
    EventSemanticsError,
    classify_event,
    decompose_modify,
    l1_state,
)
from scripts.move_exhaustion_v2.data.front_contract import (
    Contract,
    FrontContractError,
    contract_volume_audit,
    filter_outrights,
    is_quarterly_outright,
    parse_contract,
    select_front_contract,
)
from scripts.move_exhaustion_v2.data.mbp1_store_builder import (
    PRE_EVENT_DEPTH_COLUMNS,
    REQUIRED_RAW_COLUMNS,
    derive_day_frame,
)
from scripts.move_exhaustion_v2.data.qc import (
    aggregate_annual_coverage,
    check_book_resets,
    check_crossed_book,
    check_event_count_sanity,
    check_roll_audit,
    check_session_coverage,
    check_timestamp_monotonicity,
    run_daily_qc,
)
from scripts.move_exhaustion_v2.data.session import (
    KIND_CLOSED,
    KIND_PARTIAL,
    expected_open_dates,
    expected_open_sessions,
    expected_session,
    session_bounds,
)

TICK = 0.015625  # 1/64 point (frozen episodes.yaml)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_frame(
    events,
    *,
    trading_date="2024-01-02",
    start_et="2024-01-01 18:00:00",
    step_s=10,
):
    """Raw MBP-1 rows from event dicts; ts/sequence generated deterministically."""
    ts = pd.date_range(
        pd.Timestamp(start_et, tz="America/New_York").tz_convert("UTC"),
        periods=len(events),
        freq=f"{step_s}s",
        tz="UTC",
    )
    rows = []
    for i, ev in enumerate(events):
        rows.append(
            {
                "ts_event": ts[i],
                "sequence": i + 1,
                "trading_date": trading_date,
                "action": ev.get("action", "A"),
                "side": ev.get("side", "B"),
                "price": ev.get("price", np.nan),
                "size": ev.get("size", 1),
                "bid_px_00": ev.get("bid_px_00", np.nan),
                "ask_px_00": ev.get("ask_px_00", np.nan),
                "bid_sz_00": ev.get("bid_sz_00", np.nan),
                "ask_sz_00": ev.get("ask_sz_00", np.nan),
                "bid_ct_00": ev.get("bid_ct_00", np.nan),
                "ask_ct_00": ev.get("ask_ct_00", np.nan),
            }
        )
    return pd.DataFrame(rows)


def _session_times(start_utc, end_utc, step_minutes=30):
    times = []
    cursor = pd.Timestamp(start_utc)
    cursor = cursor.tz_localize("UTC") if cursor.tzinfo is None else cursor.tz_convert("UTC")
    end = pd.Timestamp(end_utc)
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    while cursor <= end:
        times.append(cursor)
        cursor += timedelta(minutes=step_minutes)
    return times


def _book_row(bid=100.0, ask=100.015625, bid_sz=5, ask_sz=5):
    return {
        "bid_px_00": bid,
        "ask_px_00": ask,
        "bid_sz_00": bid_sz,
        "ask_sz_00": ask_sz,
    }


# ---------------------------------------------------------------------------
# session.py
# ---------------------------------------------------------------------------

def test_session_bounds_winter_18_to_17_et():
    start, end = session_bounds("2024-01-02")
    assert start == datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert end == datetime(2024, 1, 2, 22, 0, tzinfo=timezone.utc)


def test_session_bounds_respect_dst():
    # 2024-06-03 is EDT; 18:00 ET = 22:00 UTC, 17:00 ET = 21:00 UTC.
    start, end = session_bounds("2024-06-03")
    assert start == datetime(2024, 6, 2, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2024, 6, 3, 21, 0, tzinfo=timezone.utc)


def test_expected_session_weekend_and_good_friday_closed():
    sat = expected_session("2024-01-06")
    assert sat["kind"] == KIND_CLOSED and sat["reason"] == "weekend"
    assert sat["expected_last_trade_utc"] is None
    gf = expected_session("2024-03-29")
    assert gf["kind"] == KIND_CLOSED and gf["reason"] == "cme_calendar_closed"


def test_expected_session_partial_holidays_close_at_13_et():
    for trading_date in ("2024-01-15", "2024-11-28"):
        schedule = expected_session(trading_date)
        assert schedule["kind"] == KIND_PARTIAL
        assert schedule["reason"] == "cme_calendar_modified_hours"
        expected_last = datetime.fromisoformat(schedule["expected_last_trade_utc"].isoformat())
        assert expected_last.hour == 18  # 13:00 ET = 18:00 UTC (EST)


def test_good_friday_schedule_is_year_specific():
    assert expected_session("2024-03-29")["kind"] == KIND_CLOSED
    special = expected_session("2023-04-07")
    assert special["kind"] == KIND_PARTIAL
    assert special["expected_last_trade_utc"].hour == 15


def test_expected_open_sessions_range_excludes_weekends():
    opened = expected_open_sessions("2024-01-01", "2024-01-07")
    dates = [str(s["trading_date"]) for s in opened]
    assert dates == ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def test_expected_open_dates_2024_count_and_holiday_handling():
    dates = expected_open_dates(2024)
    assert len(dates) == 259
    assert "2024-03-29" not in dates  # Good Friday
    assert "2024-01-15" in dates      # MLK partial
    assert "2024-01-01" not in dates  # New Year's Day closed


# ---------------------------------------------------------------------------
# front_contract.py
# ---------------------------------------------------------------------------

def test_parse_contract_accepts_cme_bloomberg_and_2digit_years():
    ref = date(2024, 1, 1)
    assert parse_contract("ZNH6", reference_date=ref) == Contract(month=3, year=2026)
    assert parse_contract("ZNM6", reference_date=ref) == Contract(month=6, year=2026)
    assert parse_contract("ZNU6", reference_date=ref) == Contract(month=9, year=2026)
    assert parse_contract("ZNZ6", reference_date=ref) == Contract(month=12, year=2026)
    assert parse_contract("ZNH26", reference_date=ref) == Contract(month=3, year=2026)
    assert parse_contract("TYH6 COMDTY", reference_date=ref) == Contract(month=3, year=2026)
    assert parse_contract("ZN H6", reference_date=ref) == Contract(month=3, year=2026)
    assert parse_contract("ZNZ4", reference_date=ref) == Contract(month=12, year=2024)


def test_parse_contract_rejects_non_outrights_and_foreign_roots():
    ref = date(2024, 1, 1)
    for bad in ("ZNF6", "ZNG6", "ZNZ6-ZNH7", "ZBM6", "TY-F6", "", None, "garbage"):
        assert parse_contract(bad, reference_date=ref) is None, bad
        assert is_quarterly_outright(bad, reference_date=ref) is False, bad


def test_parse_contract_fails_closed_on_ambiguous_year():
    assert parse_contract("ZNH04", reference_date=date(2024, 1, 1)) is None


def test_filter_outrights_keeps_only_zn_quarterlies():
    frame = pd.DataFrame(
        {
            "symbol": ["ZNH6", "ZNZ6", "ZNZ6-ZNH7", "ZBM6", "ZNF6"],
            "size": [1, 2, 3, 4, 5],
        }
    )
    kept, report = filter_outrights(frame, reference_date=date(2024, 1, 1))
    assert list(kept["symbol"]) == ["ZNH6", "ZNZ6"]
    assert list(kept["contract"]) == ["ZNH6", "ZNZ6"]
    assert report["rows_out"] == 2
    assert report["rows_dropped"] == 3
    assert report["excluded_by_reason"]["calendar_spread"] == 1
    assert report["excluded_by_reason"]["non_quarterly_or_foreign"] == 2
    assert sum(report["excluded_by_reason"].values()) == report["rows_dropped"]


def test_contract_volume_audit_counts_only_trade_rows():
    frame = pd.DataFrame(
        {
            "symbol": ["ZNH6", "ZNH6", "ZNZ6", "ZNH6", "ZNF6"],
            "action": ["T", "T", "T", "A", "T"],
            "size": [10, 5, 3, 999, 4],
        }
    )
    audit = contract_volume_audit(frame, reference_date=date(2024, 1, 1))
    assert audit["total_t_volume"] == 18.0
    assert audit["trade_rows"] == 3
    assert [c["contract"] for c in audit["candidates"]] == ["ZNH6", "ZNZ6"]
    assert audit["candidates"][0]["t_volume"] == 15.0


def test_contract_volume_audit_tie_break_earliest_expiry_then_symbol():
    frame = pd.DataFrame(
        {
            "symbol": ["ZNM6", "ZNH6", "ZNZ6", "ZNU6"],
            "action": ["T", "T", "T", "T"],
            "size": [7, 7, 7, 7],
        }
    )
    audit = contract_volume_audit(frame, reference_date=date(2024, 1, 1))
    assert [c["contract"] for c in audit["candidates"]] == [
        "ZNH6", "ZNM6", "ZNU6", "ZNZ6",
    ]


def test_select_front_contract_bootstrap_is_ineligible():
    result = select_front_contract(None, trading_date="2024-01-02")
    assert result["research_eligible"] is False
    assert result["selected"] is None
    assert result["mode"] == "bootstrap_no_prior_session"


def test_select_front_contract_fails_closed_without_prior_winner():
    with pytest.raises(FrontContractError):
        select_front_contract({"candidates": [], "total_t_volume": 0.0},
                              trading_date="2024-01-03")
    with pytest.raises(FrontContractError):
        select_front_contract(
            {"candidates": [{"contract": "ZNH6", "expiry_key": [2026, 3], "t_volume": 0.0}],
             "total_t_volume": 0.0},
            trading_date="2024-01-03",
        )


def test_select_front_contract_uses_prior_session_winner():
    audit = {
        "trading_date": "2024-01-02",
        "candidates": [
            {"contract": "ZNH6", "expiry_key": [2026, 3], "t_volume": 15.0},
            {"contract": "ZNZ6", "expiry_key": [2026, 12], "t_volume": 3.0},
        ],
        "total_t_volume": 18.0,
    }
    result = select_front_contract(audit, trading_date="2024-01-03")
    assert result["selected"] == "ZNH6"
    assert result["research_eligible"] is True
    assert result["source_date"] == "2024-01-02"
    assert result["prior_share"] == pytest.approx(15.0 / 18.0)


# ---------------------------------------------------------------------------
# event_semantics.py
# ---------------------------------------------------------------------------

def test_classify_event_action_side_mapping():
    assert classify_event("T", "B") == AGG_BUY
    assert classify_event("T", "A") == AGG_SELL
    assert classify_event("F", "B") == AGG_BUY
    assert classify_event("F", "A") == AGG_SELL
    assert classify_event("A", "B") == ADD_BID
    assert classify_event("A", "A") == ADD_ASK
    assert classify_event("C", "B") == CXL_BID
    assert classify_event("C", "A") == CXL_ASK
    assert classify_event("R", "N") == RESET
    assert classify_event("r", "n") == RESET  # case-insensitive
    assert classify_event("T", "N") == AGG_UNKNOWN
    assert classify_event("A", "N") == ADD_UNKNOWN
    assert classify_event("C", "N") == CXL_UNKNOWN
    assert classify_event("M", "N") == MOD_UNKNOWN


def test_classify_event_fails_closed_on_bad_sides_and_actions():
    with pytest.raises(EventSemanticsError):
        classify_event("X", "B")
    with pytest.raises(EventSemanticsError):
        classify_event("T", "Z")


def test_decompose_modify_size_up_down_and_price_change():
    prev = {"bid_px": 100.0, "ask_px": 100.015625, "bid_sz": 5, "ask_sz": 5}
    curr_up = {**prev, "bid_sz": 7}
    curr_down = {**prev, "bid_sz": 3}
    curr_moved = {**prev, "bid_px": 100.015625}
    assert decompose_modify(prev, curr_up, "B") == MOD_ADD_LIKE
    assert decompose_modify(prev, curr_down, "B") == MOD_CXL_LIKE
    assert decompose_modify(prev, curr_moved, "B") == MOD_CXL_ADD
    ask_up = {**prev, "ask_sz": 9}
    assert decompose_modify(prev, ask_up, "A") == MOD_ADD_LIKE
    prev_count = {**prev, "bid_ct": 7}
    curr_count = {**prev_count, "bid_ct": 5}
    assert decompose_modify(prev_count, curr_count, "B") == MOD_CXL_LIKE


def test_decompose_modify_noop_is_mod_unknown_unestablished_fails_closed():
    prev = {"bid_px": 100.0, "ask_px": 100.015625, "bid_sz": 5, "ask_sz": 5}
    assert decompose_modify(prev, dict(prev), "B") == MOD_UNKNOWN
    with pytest.raises(EventSemanticsError):
        decompose_modify({"bid_px": np.nan, "ask_px": 100.015625, "bid_sz": 5, "ask_sz": 5},
                         prev, "B")
    with pytest.raises(EventSemanticsError):
        classify_event("M", "B")  # no L1 states supplied


def test_classify_event_modify_uses_prev_curr_l1():
    prev = l1_state(pd.Series(_book_row(bid=100.0, bid_sz=5)))
    curr = l1_state(pd.Series(_book_row(bid=100.0, bid_sz=8)))
    assert classify_event("M", "B", prev_l1=prev, curr_l1=curr) == MOD_ADD_LIKE


# ---------------------------------------------------------------------------
# qc.py — Q1..Q6
# ---------------------------------------------------------------------------

def _monotonic_frame(n=5):
    return pd.DataFrame(
        {
            "ts_event": pd.date_range("2024-01-01 23:00", periods=n, freq="1s", tz="UTC"),
            "sequence": list(range(1, n + 1)),
            "bid_px_00": [100.0] * n,
            "ask_px_00": [100.015625] * n,
            "action": ["A"] * n,
        }
    )


def test_q1_passes_monotonic_and_fails_out_of_order():
    frame = _monotonic_frame()
    assert check_timestamp_monotonicity(frame)["passed"] is True
    bad = frame.iloc[::-1].copy()
    result = check_timestamp_monotonicity(bad)
    assert result["passed"] is False
    assert result["n_violations"] == len(bad) - 1


def test_q1_allows_equal_event_keys_but_rejects_decreasing_sequence():
    frame = pd.DataFrame(
        {
            "ts_event": [pd.Timestamp("2024-01-01 23:00", tz="UTC")] * 3,
            "sequence": [1, 2, 2],
        }
    )
    result = check_timestamp_monotonicity(frame)
    assert result["passed"] is True
    assert result["n_duplicate_key_pairs"] == 1

    frame.loc[2, "sequence"] = 1
    assert check_timestamp_monotonicity(frame)["passed"] is False


def test_q2_crossed_fraction_threshold():
    n = 10000
    rows = [_book_row() for _ in range(n)]
    rows[0] = _book_row(bid=100.02, ask=100.0)  # crossed
    frame = pd.DataFrame(rows)
    boundary = check_crossed_book(frame)
    assert boundary["passed"] is False
    assert boundary["fraction_crossed"] == pytest.approx(0.0001)
    assert boundary["crossed_rows"] == [0]

    rows.append(_book_row())  # one crossed in 10,001 rows is strictly <0.01%
    assert check_crossed_book(pd.DataFrame(rows))["passed"] is True


def test_q2_locked_book_counts_as_crossed():
    frame = pd.DataFrame([_book_row(bid=100.0, ask=100.0)])
    result = check_crossed_book(frame)
    assert result["passed"] is False
    assert result["n_crossed"] == 1


def test_q3_full_session_coverage_passes_and_gap_fails():
    start, end = session_bounds("2024-01-02")
    times = _session_times(start, end, step_minutes=30)
    frame = pd.DataFrame(
        {
            "ts_event": times,
            "sequence": range(1, len(times) + 1),
            "action": ["A"] * len(times),
            "bid_px_00": [100.0] * len(times),
            "ask_px_00": [100.015625] * len(times),
        }
    )
    ok = check_session_coverage(frame, "2024-01-02")
    assert ok["passed"] is True

    # 23:30 -> 00:30 becomes a 60-minute gap (skip the 00:00 row).
    gap_times = times[:2] + times[3:]
    gap_frame = pd.DataFrame(
        {
            "ts_event": gap_times,
            "sequence": range(1, len(gap_times) + 1),
            "action": ["A"] * len(gap_times),
            "bid_px_00": [100.0] * len(gap_times),
            "ask_px_00": [100.015625] * len(gap_times),
        }
    )
    gap = check_session_coverage(gap_frame, "2024-01-02")
    assert gap["passed"] is False
    assert gap["n_gap_violations"] == 1


def test_q3_known_halt_absorbs_gap():
    start, end = session_bounds("2024-01-02")
    times = _session_times(start, end, step_minutes=30)
    gap_times = times[:2] + times[3:]
    frame = pd.DataFrame(
        {
            "ts_event": gap_times,
            "sequence": range(1, len(gap_times) + 1),
            "action": ["A"] * len(gap_times),
            "bid_px_00": [100.0] * len(gap_times),
            "ask_px_00": [100.015625] * len(gap_times),
        }
    )
    # Events bracket a 30-minute halt inside a 60-minute observed gap; after
    # subtracting the halt, the effective gap is exactly the allowed 30 min.
    halt = (
        times[1] + timedelta(minutes=15),
        times[2] + timedelta(minutes=15),
    )
    result = check_session_coverage(frame, "2024-01-02", known_halts=[halt])
    assert result["passed"] is True
    assert result["max_effective_gap_minutes"] == 30.0


def test_q3_partial_holiday_uses_13_et_expected_last_trade():
    schedule = expected_session("2024-11-28")
    start = schedule["start_utc"]
    last = schedule["expected_last_trade_utc"]
    times = _session_times(start, last, step_minutes=30)
    frame = pd.DataFrame(
        {
            "ts_event": times,
            "sequence": range(1, len(times) + 1),
            "action": ["A"] * len(times),
            "bid_px_00": [100.0] * len(times),
            "ask_px_00": [100.015625] * len(times),
        }
    )
    assert check_session_coverage(frame, "2024-11-28")["passed"] is True
    # Globex last-event at 17:00 ET is still a valid electronic-session end
    # even when the cash calendar is an early close.
    globex_close = frame.iloc[:-1].copy()
    globex_close = pd.concat(
        [
            globex_close,
            pd.DataFrame(
                {
                    "ts_event": [pd.Timestamp("2024-11-28 22:00", tz="UTC")],
                    "sequence": [len(times)],
                    "action": ["A"],
                    "bid_px_00": [100.0],
                    "ask_px_00": [100.015625],
                }
            ),
        ],
        ignore_index=True,
    )
    assert check_session_coverage(globex_close, "2024-11-28")["passed"] is True
    # Hours after the next 18:00 ET open is not a valid session end.
    late = frame.iloc[:-1].copy()
    late = pd.concat(
        [
            late,
            pd.DataFrame(
                {
                    "ts_event": [pd.Timestamp("2024-11-29 02:00", tz="UTC")],
                    "sequence": [len(times)],
                    "action": ["A"],
                    "bid_px_00": [100.0],
                    "ask_px_00": [100.015625],
                }
            ),
        ],
        ignore_index=True,
    )
    assert check_session_coverage(late, "2024-11-28")["passed"] is False


def test_q3_first_event_late_fails():
    start, end = session_bounds("2024-01-02")
    times = _session_times(start, end, step_minutes=30)
    late = times[2:]  # first event 60 minutes after session start (tolerance is 30)
    frame = pd.DataFrame(
        {
            "ts_event": late,
            "sequence": range(1, len(late) + 1),
            "action": ["A"] * len(late),
            "bid_px_00": [100.0] * len(late),
            "ask_px_00": [100.015625] * len(late),
        }
    )
    result = check_session_coverage(frame, "2024-01-02")
    assert result["passed"] is False
    assert result["reason"] is not None


def test_q4_event_count_band_and_warmup():
    frame = _monotonic_frame(n=1000)
    assert check_event_count_sanity(frame, 1000.0)["passed"] is True
    assert check_event_count_sanity(frame, 100.0)["passed"] is True    # 10x upper bound
    assert check_event_count_sanity(frame, 20000.0)["passed"] is False  # below 0.1x
    assert check_event_count_sanity(frame, 1.0)["passed"] is False      # above 10x
    warm = check_event_count_sanity(frame, None)
    assert warm["passed"] is True and warm["flagged"] is False


def test_q5_roll_continuity_and_share():
    assert check_roll_audit(
        chosen_contract="ZNH6", prior_winner="ZNH6", prior_winner_share=0.8
    )["passed"] is True
    low_share = check_roll_audit(
        chosen_contract="ZNH6", prior_winner="ZNH6", prior_winner_share=0.5
    )
    assert low_share["passed"] is False and low_share["flagged"] is True
    changed = check_roll_audit(
        chosen_contract="ZNM6", prior_winner="ZNH6", prior_winner_share=0.8,
        trading_date="2024-03-15", documented_roll_windows=["2024-03-15"],
    )
    assert changed["passed"] is True  # documented roll window + share rule
    off_window = check_roll_audit(
        chosen_contract="ZNM6", prior_winner="ZNH6", prior_winner_share=0.8,
        trading_date="2024-02-01", documented_roll_windows=["2024-03-15"],
    )
    assert off_window["passed"] is False


def test_q6_reset_threshold():
    frame = pd.DataFrame({"action": ["R"] * 5 + ["A"]})
    assert check_book_resets(frame)["passed"] is True
    frame6 = pd.DataFrame({"action": ["R"] * 6})
    result = check_book_resets(frame6)
    assert result["passed"] is False and result["flagged"] is True
    assert result["n_resets"] == 6


def test_run_daily_qc_full_pass_and_hard_fail():
    start, end = session_bounds("2024-01-02")
    times = _session_times(start, end, step_minutes=30)
    frame = pd.DataFrame(
        {
            "ts_event": times,
            "sequence": range(1, len(times) + 1),
            "action": ["A"] * len(times),
            "bid_px_00": [100.0] * len(times),
            "ask_px_00": [100.015625] * len(times),
        }
    )
    record = run_daily_qc(
        frame, "2024-01-02",
        chosen_contract="ZNH6", prior_winner="ZNH6", prior_winner_share=0.8,
    )
    assert record["qc_pass"] is True
    assert record["research_eligible"] is True

    out_of_order = frame.iloc[::-1].copy()
    bad = run_daily_qc(
        out_of_order, "2024-01-02",
        chosen_contract="ZNH6", prior_winner="ZNH6", prior_winner_share=0.8,
    )
    assert bad["qc_pass"] is False
    assert bad["research_eligible"] is False
    assert bad["checks"]["Q1"]["passed"] is False


def test_run_daily_qc_bootstrap_day_fails_q5():
    start, end = session_bounds("2024-01-02")
    times = _session_times(start, end, step_minutes=30)
    frame = pd.DataFrame(
        {
            "ts_event": times,
            "sequence": range(1, len(times) + 1),
            "action": ["A"] * len(times),
            "bid_px_00": [100.0] * len(times),
            "ask_px_00": [100.015625] * len(times),
        }
    )
    record = run_daily_qc(frame, "2024-01-02")
    assert record["checks"]["Q5"]["passed"] is False
    assert record["qc_pass"] is False
    assert record["research_eligible"] is True  # Q1-Q3 pass; Q5 is a review flag


def test_aggregate_annual_coverage_and_oq6_thresholds():
    opened = expected_open_sessions("2023-01-02", "2023-01-31")
    dates = [str(s["trading_date"]) for s in opened]
    assert len(dates) == 21
    results = [
        {"trading_date": d, "qc_pass": True, "research_eligible": True}
        for d in dates
    ]
    full = aggregate_annual_coverage(
        results, years=[2023], expected_start="2023-01-02", expected_end="2023-01-31"
    )
    assert full["per_year"]["2023"]["coverage_ratio"] == 1.0
    assert full["per_year"]["2023"]["q1_q6_pass_rate"] == 1.0
    assert full["meets_all_year_thresholds"] is True

    partial = aggregate_annual_coverage(
        results[:-3], years=[2023],
        expected_start="2023-01-02", expected_end="2023-01-31",
    )
    cov = partial["per_year"]["2023"]["coverage_ratio"]
    assert cov == pytest.approx(18 / 21)
    assert partial["per_year"]["2023"]["meets_year_threshold"] is False
    assert partial["meets_all_year_thresholds"] is False


def test_aggregate_annual_coverage_flags_unexpected_days():
    results = [
        {"trading_date": "2023-01-03", "qc_pass": True, "research_eligible": True},
        {"trading_date": "2023-01-01", "qc_pass": True, "research_eligible": True},
    ]
    agg = aggregate_annual_coverage(
        results, years=[2023], expected_start="2023-01-02", expected_end="2023-01-31"
    )
    assert agg["unexpected_days"] == ["2023-01-01"]


# ---------------------------------------------------------------------------
# mbp1_store_builder.py
# ---------------------------------------------------------------------------

def test_pre_event_depth_columns_exact_surface():
    assert set(PRE_EVENT_DEPTH_COLUMNS) == {
        "pre_bid_sz", "pre_ask_sz", "pre_bid_px", "pre_ask_px",
    }


def test_derive_day_frame_previous_row_book_and_events():
    frame = _raw_frame(
        [
            {"action": "A", "side": "B", **_book_row()},
            {"action": "T", "side": "B", "price": 100.0, "size": 2, **_book_row()},
            {"action": "M", "side": "B", **_book_row(bid_sz=7)},
        ]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert list(out["event_class"]) == [ADD_BID, AGG_BUY, MOD_ADD_LIKE]
    assert np.isnan(out[["pre_bid_px", "pre_ask_px", "pre_bid_sz", "pre_ask_sz"]].iloc[0]).all()
    assert out["pre_bid_px"].iloc[1] == 100.0
    assert out["pre_ask_px"].iloc[1] == 100.015625
    assert out["pre_bid_sz"].iloc[1] == 5
    assert out["pre_ask_sz"].iloc[1] == 5
    # Row 3 pre depth = row 2's post-event book (size still 5, not 7).
    assert out["pre_bid_sz"].iloc[2] == 5
    assert out["mid"].tolist() == [100.0078125] * 3
    assert out["spread_ticks"].tolist() == [1.0] * 3
    assert out["mid_change"].tolist() == [0, 0, 0]
    assert out["n_lob_events"].tolist() == [1, 1, 2]
    assert out["n_trades"].tolist() == [0, 1, 1]
    assert out["n_price_changes"].tolist() == [0, 0, 0]
    assert out["wall_clock"].tolist() == [0.0, 10.0, 20.0]


def test_derive_day_frame_treats_databento_fill_as_trade():
    frame = _raw_frame(
        [
            {"action": "A", "side": "B", **_book_row()},
            {"action": "F", "side": "A", "price": 100.0, "size": 3, **_book_row()},
        ]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert list(out["event_class"]) == [ADD_BID, AGG_SELL]
    assert out["n_trades"].tolist() == [0, 1]
    assert out["n_lob_events"].tolist() == [1, 1]


def test_derive_day_frame_noop_modify_is_mod_unknown():
    frame = _raw_frame(
        [
            {"action": "A", "side": "B", **_book_row()},
            {"action": "M", "side": "B", **_book_row()},
        ]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert list(out["event_class"]) == [ADD_BID, MOD_UNKNOWN]


def test_derive_day_frame_reset_reseeds_book_state():
    frame = _raw_frame(
        [
            {"action": "A", "side": "B", **_book_row()},
            {"action": "R", "side": "N", **_book_row(bid=100.5, ask=100.515625, bid_sz=3, ask_sz=3)},
            {"action": "A", "side": "B", **_book_row(bid=100.5, ask=100.515625, bid_sz=4, ask_sz=4)},
        ]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert list(out["event_class"]) == [ADD_BID, RESET, ADD_BID]
    # Row 2 (reset) pre depth = row 1's book; row 3 pre depth = reset row's book.
    assert out["pre_bid_px"].iloc[1] == 100.0
    assert out["pre_bid_px"].iloc[2] == 100.5
    assert out["pre_bid_sz"].iloc[2] == 3
    assert out["mid"].iloc[1] == pytest.approx(100.5078125)
    assert out["mid_change"].tolist() == [0, 1, 0]  # reset row moved mid; next row same mid
    assert out["n_lob_events"].tolist() == [1, 2, 3]
    assert out["n_trades"].tolist() == [0, 0, 0]
    assert out["n_price_changes"].tolist() == [0, 1, 1]  # reset row established new quote


def test_derive_day_frame_retains_valid_unknown_side_events():
    frame = _raw_frame(
        [
            {"action": action, "side": "N", **_book_row()}
            for action in ("A", "C", "M", "T")
        ]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert list(out["event_class"]) == [
        ADD_UNKNOWN,
        CXL_UNKNOWN,
        MOD_UNKNOWN,
        AGG_UNKNOWN,
    ]
    assert out["n_trades"].iloc[-1] == 1


def test_derive_day_frame_marks_initial_modify_without_prior_state_unknown():
    frame = _raw_frame(
        [{"action": "M", "side": "B", "bid_ct_00": 5, **_book_row()}]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert out["event_class"].tolist() == [MOD_UNKNOWN]


def test_derive_day_frame_price_change_clock_and_mid_change():
    frame = _raw_frame(
        [
            {"action": "A", "side": "B", **_book_row()},
            {"action": "A", "side": "B", **_book_row(bid=100.015625, ask=100.03125)},
        ]
    )
    out = derive_day_frame(frame, trading_date="2024-01-02")
    assert out["mid_change"].tolist() == [0, 1]
    assert out["n_price_changes"].tolist() == [0, 1]


def test_derive_day_frame_canonicalizes_row_order():
    rows = [
        {"action": "A", "side": "B", **_book_row(bid_sz=9)},
        {"action": "A", "side": "B", **_book_row()},
        {"action": "A", "side": "B", **_book_row(bid_sz=11)},
    ]
    frame = _raw_frame(rows)
    shuffled = frame.iloc[[1, 0, 2]].copy()
    out = derive_day_frame(shuffled, trading_date="2024-01-02")
    assert list(out["ts_event"]) == sorted(out["ts_event"])
    assert out["pre_bid_sz"].iloc[1] == 9  # previous row in canonical order


def test_derive_day_frame_preserves_extra_columns_and_contract_stamp():
    frame = _raw_frame([{"action": "A", "side": "B", **_book_row()}])
    frame["ts_recv"] = frame["ts_event"] + pd.Timedelta(milliseconds=5)
    frame["extra"] = "x"
    out = derive_day_frame(frame, trading_date="2024-01-02", contract="ZNH6")
    assert list(out["ts_recv"]) == list(frame["ts_recv"])
    assert list(out["extra"]) == ["x"]
    assert list(out["contract"]) == ["ZNH6"]


def test_derive_day_frame_fails_closed_on_bad_semantics():
    frame = _raw_frame([{"action": "X", "side": "B", **_book_row()}])
    with pytest.raises(EventSemanticsError):
        derive_day_frame(frame, trading_date="2024-01-02")


def test_derive_day_frame_empty_input_has_derived_schema():
    empty = pd.DataFrame(columns=list(REQUIRED_RAW_COLUMNS))
    out = derive_day_frame(empty, trading_date="2024-01-02")
    assert out.empty
    assert {"event_class", "mid", "mid_change", "spread_ticks"} | set(
        PRE_EVENT_DEPTH_COLUMNS
    ) <= set(out.columns)
