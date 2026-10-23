"""Synthetic tests for the X0 Q7/Q8 parity + semantics audit slice.

Covers :mod:`scripts.move_exhaustion_v2.data.x0_parity`:

* per-date UTC 1-minute binning with AGG_BUY = +size / AGG_SELL = -size /
  everything else (incl. AGG_UNKNOWN) = 0;
* minute-gap filling and the within-date trailing 5-minute signed sum;
* Q7 alignment (global/per-date Pearson correlation), exact opposite-sign
  (sign-flip) detection with examples, and fail-closed degenerate overlap;
* Q8 last-mid-per-minute vs matrix price at the 1/64-point tick bound;
* store-wide event/action/side semantics counts for the MBP-1 audit;
* derived-store parquet path input and trading-date consistency checks;
* the no-episodes/features/holdout-statistics guardrail.

All inputs are synthetic DataFrames (or tiny parquet files under
``tmp_path``); no archive, store, network, or frozen config is touched.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest

from scripts.move_exhaustion_v2.data.mbp1_store_builder import DEFAULT_TICK_SIZE
from scripts.move_exhaustion_v2.data.x0_parity import (
    AGG_BUY,
    AGG_SELL,
    AGG_UNKNOWN,
    MIN_MATCHED_MINUTES,
    ParityError,
    TICK_SIZE,
    aggregate_semantics,
    fill_minute_gaps,
    list_store_day_paths,
    load_store_days,
    run_parity_report,
    run_parity_report_from_store,
    store_minute_frame,
    validate_tbbo_matrix,
)

DATE = "2026-01-27"
START_UTC = f"{DATE} 06:00:00"
MID = 111.71875


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minute_rows(
    minutes: Sequence[Tuple[str, float]],
    *,
    trading_date: str = DATE,
    start: str = START_UTC,
) -> pd.DataFrame:
    """One event per minute: (event_class, size)."""
    rows = []
    for index, (event_class, size) in enumerate(minutes):
        ts = pd.Timestamp(start, tz="UTC") + pd.Timedelta(minutes=index)
        side = {
            "AGG_BUY": "B",
            "AGG_SELL": "A",
            "ADD_BID": "B",
            "ADD_ASK": "A",
            "CXL_BID": "B",
            "CXL_ASK": "A",
        }.get(event_class, "N")
        action = (
            "T"
            if event_class.startswith("AGG")
            else "A"
            if event_class.startswith("ADD")
            else "C"
            if event_class.startswith("CXL")
            else "M"
            if event_class.startswith("MOD")
            else "R"
        )
        rows.append(
            {
                "trading_date": trading_date,
                "ts_event": ts,
                "sequence": index + 1,
                "event_class": event_class,
                "size": float(size),
                "mid": MID,
                "action": action,
                "side": side,
            }
        )
    return pd.DataFrame(rows)


def _matrix_from_store(
    day: pd.DataFrame,
    *,
    price: float | Sequence[float] | None = None,
    ty_agg_5m: Sequence[float] | None = None,
) -> pd.DataFrame:
    """Matrix whose TY_agg_5m matches the store's trailing 5-minute sums."""
    filled = fill_minute_gaps(store_minute_frame(day))
    matrix = filled[["bucket_ts"]].copy()
    matrix["et_date"] = str(day["trading_date"].iloc[0])
    if price is None:
        matrix["price"] = MID
    elif isinstance(price, (int, float)):
        matrix["price"] = float(price)
    else:
        matrix["price"] = list(price)
    matrix["TY_agg_5m"] = (
        filled["trailing_5m"] if ty_agg_5m is None else list(ty_agg_5m)
    )
    return matrix


def _standard_day(n: int = 12) -> pd.DataFrame:
    return _minute_rows(
        [(AGG_BUY if i % 2 == 0 else AGG_SELL, 5.0 if i % 2 == 0 else 3.0) for i in range(n)]
    )


# ---------------------------------------------------------------------------
# Binning, gap filling, trailing 5-minute sum
# ---------------------------------------------------------------------------

def test_store_minute_binning_signs_and_zero_unknown():
    day = _minute_rows(
        [
            (AGG_BUY, 5.0),
            (AGG_SELL, 3.0),
            (AGG_UNKNOWN, 10.0),  # retained but contributes zero to flow
            (AGG_BUY, 2.0),
        ]
    )
    minutes = store_minute_frame(day)
    assert list(minutes["signed_flow"]) == [5.0, -3.0, 0.0, 2.0]
    assert list(minutes["n_events"]) == [1, 1, 1, 1]
    assert list(minutes["last_mid"]) == [MID] * 4


def test_fill_minute_gaps_zero_fills_flow_and_forward_fills_mid():
    day = _standard_day(6)
    day = day.drop(day.index[3]).reset_index(drop=True)  # missing minute 06:03
    filled = fill_minute_gaps(store_minute_frame(day))
    assert len(filled) == 6
    assert filled.loc[3, "signed_flow"] == 0.0
    assert filled.loc[3, "n_events"] == 0
    assert filled.loc[3, "last_mid"] == MID  # forward-filled


def test_trailing_5m_is_inclusive_current_plus_previous_four():
    day = _standard_day(12)
    filled = fill_minute_gaps(store_minute_frame(day))
    trailing = filled["trailing_5m"].tolist()
    assert trailing[0] == 5.0
    assert trailing[1] == 2.0  # 5 - 3
    assert trailing[4] == pytest.approx(5 - 3 + 5 - 3 + 5)  # minutes 0..4
    assert trailing[5] == pytest.approx(-3 + 5 - 3 + 5 - 3)  # minutes 1..5
    assert trailing[11] == pytest.approx(-3 + 5 - 3 + 5 - 3)  # minutes 7..11


# ---------------------------------------------------------------------------
# Q7 parity
# ---------------------------------------------------------------------------

def test_q7_alignment_passes_with_perfect_correlation():
    day = _standard_day(12)
    matrix = _matrix_from_store(day)
    report = run_parity_report(
        {DATE: day}, matrix, min_matched_minutes=5
    )
    q7 = report["q7"]
    assert q7["passed"] is True
    assert q7["overlap_dates"] == [DATE]
    assert q7["overlap_count"] == 1
    assert q7["global_matched_minutes"] == 12
    assert q7["global_corr"] == pytest.approx(1.0)
    assert q7["per_date_corr"] == {DATE: pytest.approx(1.0)}
    assert q7["sign_flips"] == 0
    assert q7["sign_flip_examples"] == []


def test_q7_clips_trailing_window_to_matrix_session():
    overnight = _minute_rows([(AGG_SELL, 80.0)], start=f"{DATE} 05:00:00")
    rth = _standard_day(12)
    day = pd.concat([overnight, rth], ignore_index=True)
    matrix = _matrix_from_store(rth)
    report = run_parity_report({DATE: day}, matrix, min_matched_minutes=5)
    assert report["q7"]["passed"] is True
    assert report["q7"]["sign_flips"] == 0
    assert report["q8"]["passed"] is True


def test_sparse_matrix_day_is_excluded_from_comparable_overlap():
    dense = _standard_day(12)
    sparse = _standard_day(12)
    sparse["trading_date"] = "2026-01-28"
    sparse["ts_event"] = sparse["ts_event"] + pd.Timedelta(days=1)
    sparse_matrix = _matrix_from_store(sparse).iloc[:2]
    matrix = pd.concat([_matrix_from_store(dense), sparse_matrix], ignore_index=True)
    report = run_parity_report(
        {DATE: dense, "2026-01-28": sparse},
        matrix,
        min_matched_minutes=5,
    )
    assert report["q7"]["overlap_dates"] == [DATE]
    assert report["q8"]["per_date_matched_minutes"]["2026-01-28"] == 2
    assert report["q7"]["passed"] is True
    assert report["q8"]["overlap_dates"] == [DATE]
    assert report["q8"]["passed"] is True


def test_q7_sign_flips_fail_closed_with_examples():
    day = _standard_day(12)
    filled = fill_minute_gaps(store_minute_frame(day))
    flipped = filled["trailing_5m"].tolist()
    for index in (2, 5, 8):
        flipped[index] = -flipped[index]  # exact opposite sign
    matrix = _matrix_from_store(day, ty_agg_5m=flipped)
    report = run_parity_report(
        {DATE: day}, matrix, min_matched_minutes=5, sign_flip_example_limit=2
    )
    q7 = report["q7"]
    assert q7["passed"] is False
    assert q7["sign_flips"] == 3
    assert len(q7["sign_flip_examples"]) == 2
    assert q7["sign_flip_examples"][0]["et_date"] == DATE
    assert any(
        reason.startswith("exact opposite-sign pairs") for reason in q7["reasons"]
    )


def test_q7_gap_filled_trailing_sum_matches_matrix():
    day = _standard_day(8).drop(_standard_day(8).index[3]).reset_index(drop=True)
    matrix = _matrix_from_store(day)
    report = run_parity_report({DATE: day}, matrix, min_matched_minutes=5)
    assert report["q7"]["passed"] is True
    assert report["q7"]["global_corr"] == pytest.approx(1.0)


def test_q7_missing_and_degenerate_overlap_fails_closed():
    day = _standard_day(12)
    matrix = _matrix_from_store(day)
    empty = run_parity_report({}, matrix, min_matched_minutes=5)
    assert empty["q7"]["passed"] is False
    assert "no 2026 overlap" in empty["q7"]["reasons"][0]

    # Zero-variance matrix -> correlation undefined -> fail closed.
    flat = _matrix_from_store(day, ty_agg_5m=[0.0] * 12)
    degenerate = run_parity_report({DATE: day}, flat, min_matched_minutes=5)
    assert degenerate["q7"]["passed"] is False
    assert "undefined" in degenerate["q7"]["reasons"][0]

    # Too few matched minutes.
    tiny = run_parity_report({DATE: day}, matrix, min_matched_minutes=10_000)
    assert tiny["q7"]["passed"] is False


def test_q7_per_date_correlation_reported_per_date():
    day_a = _standard_day(10)
    day_b = _standard_day(10)
    day_b["trading_date"] = "2026-01-28"
    day_b["ts_event"] = day_b["ts_event"] + pd.Timedelta(days=1)
    matrix = pd.concat(
        [_matrix_from_store(day_a), _matrix_from_store(day_b)], ignore_index=True
    )
    report = run_parity_report(
        {DATE: day_a, "2026-01-28": day_b}, matrix, min_matched_minutes=5
    )
    assert report["overlap_dates"] == ["2026-01-27", "2026-01-28"]
    assert report["q7"]["per_date_corr"] == {
        "2026-01-27": pytest.approx(1.0),
        "2026-01-28": pytest.approx(1.0),
    }
    assert report["q7"]["global_corr"] == pytest.approx(1.0)


def test_q7_ignores_non_2026_overlap_dates():
    day_2025 = _standard_day(12)
    day_2025["trading_date"] = "2025-01-27"
    day_2025["ts_event"] = day_2025["ts_event"] - pd.Timedelta(days=365)
    day_2026 = _standard_day(12)
    matrix = pd.concat(
        [_matrix_from_store(day_2025), _matrix_from_store(day_2026)],
        ignore_index=True,
    )
    report = run_parity_report(
        {"2025-01-27": day_2025, DATE: day_2026},
        matrix,
        min_matched_minutes=5,
    )
    assert "2025-01-27" in report["overlap_dates"]
    assert DATE in report["overlap_dates"]
    assert report["q7"]["overlap_dates"] == [DATE]
    assert "2025-01-27" not in report["q7"]["per_date_corr"]
    assert report["q7"]["passed"] is True

    only_2025 = run_parity_report(
        {"2025-01-27": day_2025},
        _matrix_from_store(day_2025),
        min_matched_minutes=5,
    )
    assert only_2025["q7"]["passed"] is False
    assert only_2025["q7"]["overlap_dates"] == []
    assert only_2025["q8"]["passed"] is True


def test_last_mid_uses_source_order_when_event_keys_tie():
    ts = pd.Timestamp("2026-01-27 06:00:00", tz="UTC")
    day = pd.DataFrame(
        [
            {
                "trading_date": DATE,
                "ts_event": ts,
                "sequence": 1,
                "source_order": 1,
                "event_class": AGG_BUY,
                "size": 1.0,
                "mid": MID + TICK_SIZE,
                "action": "T",
                "side": "B",
            },
            {
                "trading_date": DATE,
                "ts_event": ts,
                "sequence": 1,
                "source_order": 0,
                "event_class": AGG_BUY,
                "size": 1.0,
                "mid": MID,
                "action": "T",
                "side": "B",
            },
        ]
    )
    minutes = store_minute_frame(day)
    assert list(minutes["last_mid"]) == [MID + TICK_SIZE]


# ---------------------------------------------------------------------------
# Q8 price parity
# ---------------------------------------------------------------------------

def test_q8_passes_within_tick_and_fails_beyond():
    day = _standard_day(12)
    matrix = _matrix_from_store(day)
    report = run_parity_report({DATE: day}, matrix, min_matched_minutes=5)
    q8 = report["q8"]
    assert q8["passed"] is True
    assert q8["global_max_abs_diff"] == 0.0
    assert q8["per_date_max_abs_diff"] == {DATE: 0.0}

    prices = [MID] * 12
    prices[6] = MID + 2 * TICK_SIZE  # two ticks off
    bad = run_parity_report(
        {DATE: day},
        _matrix_from_store(day, price=prices),
        min_matched_minutes=5,
    )
    assert bad["q8"]["passed"] is False
    assert bad["q8"]["global_max_abs_diff"] is None
    assert bad["q8"]["mismatch_max_abs_diff"] == pytest.approx(2 * TICK_SIZE)
    assert bad["q8"]["excluded_price_mismatch_dates"] == [DATE]
    assert any("tick" in reason for reason in bad["q8"]["reasons"])


def test_q8_uses_last_mid_of_each_minute():
    day = _minute_rows(
        [
            (AGG_BUY, 1.0),
            (AGG_BUY, 1.0),
            (AGG_BUY, 1.0),
        ]
    )
    # Second event in minute 0 moves the mid; minute 1 has no event (gap).
    day = pd.concat(
        [
            day.iloc[:1],
            pd.DataFrame(
                [
                    {
                        "trading_date": DATE,
                        "ts_event": pd.Timestamp("2026-01-27 06:00:30", tz="UTC"),
                        "sequence": 2,
                        "event_class": AGG_BUY,
                        "size": 1.0,
                        "mid": MID + TICK_SIZE,
                        "action": "T",
                        "side": "B",
                    }
                ]
            ),
            day.iloc[2:],
        ],
        ignore_index=True,
    ).sort_values(["ts_event", "sequence"]).reset_index(drop=True)
    matrix = _matrix_from_store(day, price=[MID + TICK_SIZE, MID + TICK_SIZE, MID])
    report = run_parity_report({DATE: day}, matrix, min_matched_minutes=3)
    assert report["q8"]["passed"] is True
    assert report["q8"]["global_max_abs_diff"] == 0.0


def test_price_mismatch_dates_are_excluded_from_q7_and_q8_gates():
    """A date whose 1m price series is not the same panel is not overlap.

    Sign flips on that date must not fail Q7 once the date is excluded.
    """
    good = _standard_day(12)
    mismatch = _standard_day(12)
    mismatch["trading_date"] = "2026-01-28"
    mismatch["ts_event"] = mismatch["ts_event"] + pd.Timedelta(days=1)
    filled = fill_minute_gaps(store_minute_frame(mismatch))
    flipped = [-value for value in filled["trailing_5m"].tolist()]
    prices = [MID + 5 * TICK_SIZE] * 12
    matrix = pd.concat(
        [
            _matrix_from_store(good),
            _matrix_from_store(mismatch, price=prices, ty_agg_5m=flipped),
        ],
        ignore_index=True,
    )
    report = run_parity_report(
        {DATE: good, "2026-01-28": mismatch},
        matrix,
        min_matched_minutes=5,
    )
    assert report["q8"]["passed"] is True
    assert report["q8"]["overlap_dates"] == [DATE]
    assert report["q8"]["excluded_price_mismatch_dates"] == ["2026-01-28"]
    assert report["q8"]["mismatch_max_abs_diff"] == pytest.approx(5 * TICK_SIZE)
    assert report["q7"]["passed"] is True
    assert report["q7"]["overlap_dates"] == [DATE]
    assert report["q7"]["sign_flips"] == 0


def test_q8_missing_overlap_fails_closed():
    day = _standard_day(12)
    matrix = _matrix_from_store(day)
    report = run_parity_report(
        {DATE: day}, matrix, min_matched_minutes=10_000
    )
    assert report["q8"]["passed"] is False
    assert any("matched minutes" in reason for reason in report["q8"]["reasons"])


# ---------------------------------------------------------------------------
# Matrix validation and semantics audit
# ---------------------------------------------------------------------------

def test_validate_tbbo_matrix_rejects_bad_surface():
    day = _standard_day(4)
    matrix = _matrix_from_store(day)
    with pytest.raises(ParityError, match="required columns"):
        validate_tbbo_matrix(matrix.drop(columns=["price"]))
    dup = pd.concat([matrix, matrix.iloc[:1]], ignore_index=True)
    with pytest.raises(ParityError, match="duplicate"):
        validate_tbbo_matrix(dup)
    bad_ts = matrix.copy()
    bad_ts["bucket_ts"] = bad_ts["bucket_ts"].astype(object)
    bad_ts.iloc[0, bad_ts.columns.get_loc("bucket_ts")] = "not-a-timestamp"
    with pytest.raises(ParityError, match="invalid"):
        validate_tbbo_matrix(bad_ts)


def test_semantics_audit_counts_event_action_side():
    day = _minute_rows(
        [
            (AGG_BUY, 5.0),
            (AGG_SELL, 3.0),
            (AGG_UNKNOWN, 10.0),
            ("ADD_BID", 0.0),
            ("CXL_ASK", 0.0),
        ]
    )
    audit = aggregate_semantics({DATE: day})
    assert audit["n_store_days"] == 1
    assert audit["total_rows"] == 5
    assert audit["event_class"][AGG_BUY] == 1
    assert audit["event_class"][AGG_SELL] == 1
    assert audit["event_class"][AGG_UNKNOWN] == 1
    assert audit["event_class"]["ADD_BID"] == 1
    assert audit["event_class"]["CXL_ASK"] == 1
    assert audit["action"] == {"A": 1, "C": 1, "M": 0, "R": 0, "T": 3}
    assert audit["side"] == {"A": 2, "B": 2, "N": 1}
    assert audit["trade_side"] == {"A": 1, "B": 1, "N": 1}


def test_unknown_side_flow_is_zero_in_trailing_sum():
    day = _minute_rows([(AGG_UNKNOWN, 10.0), (AGG_UNKNOWN, 10.0)])
    filled = fill_minute_gaps(store_minute_frame(day))
    assert list(filled["trailing_5m"]) == [0.0, 0.0]


# ---------------------------------------------------------------------------
# Path inputs and scope guardrails
# ---------------------------------------------------------------------------

def test_run_parity_report_accepts_store_parquet_paths(tmp_path):
    day = _standard_day(6)
    day.to_parquet(tmp_path / f"{DATE}.parquet")
    matrix = _matrix_from_store(day)
    report = run_parity_report(
        [tmp_path / f"{DATE}.parquet"], matrix, min_matched_minutes=5
    )
    assert report["store_dates"] == [DATE]
    assert report["q7"]["passed"] is True
    assert report["q8"]["passed"] is True


def test_list_store_day_paths_skips_pilot_subdir(tmp_path):
    (tmp_path / "2026-03-02.parquet").write_bytes(b"")
    nested = tmp_path / "pilot_202603"
    nested.mkdir()
    (nested / "2026-03-03.parquet").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    found = list_store_day_paths(tmp_path)
    assert list(found) == ["2026-03-02"]


def test_run_parity_report_from_store_skips_non_overlap_days(tmp_path):
    overlap = _standard_day(6)
    other = _standard_day(6)
    other["trading_date"] = "2025-06-02"
    overlap.to_parquet(tmp_path / f"{DATE}.parquet")
    other.to_parquet(tmp_path / "2025-06-02.parquet")
    matrix = _matrix_from_store(overlap)
    report = run_parity_report_from_store(
        tmp_path, matrix, min_matched_minutes=5
    )
    assert report["n_store_parquets"] == 2
    assert report["store_dates"] == [DATE]
    assert report["q7"]["passed"] is True
    assert report["q8"]["passed"] is True


def test_store_day_parquet_with_conflicting_dates_fails(tmp_path):
    day = _standard_day(4)
    day = pd.concat(
        [day, day.assign(trading_date="2026-01-28")], ignore_index=True
    )
    day.to_parquet(tmp_path / "mixed.parquet")
    with pytest.raises(ParityError, match="more than one trading_date"):
        load_store_days([tmp_path / "mixed.parquet"])


def test_no_episodes_features_or_holdout_statistics():
    from scripts.move_exhaustion_v2.data import x0_parity

    tree = ast.parse(inspect.getsource(x0_parity))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(name.name.split(".")[0] for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert not ({"episodes", "features", "labels"} & imported_roots), (
        "x0_parity must never import episode/feature/label computation"
    )

    day = _standard_day(6)
    report = run_parity_report(
        {DATE: day}, _matrix_from_store(day), min_matched_minutes=5
    )
    assert report["holdout_opened"] is False
    assert "episodes" not in str(report["q7"])
    assert "episodes" not in str(report["q8"])

