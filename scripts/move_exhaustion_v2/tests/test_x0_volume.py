"""Synthetic tests for the X0 volume prepass + front-selection planning slice.

Covers :mod:`scripts.move_exhaustion_v2.data.x0_volume`: the DuckDB monthly
scan (ZN/TY quarterly-outright filter, ET trading-date assignment incl.
midnight/month-boundary/DST, ``trade_volume``/``event_count`` aggregation,
``.csv.zst`` input, determinism), the deterministic cross-file merge, the
causal day plan (bootstrap, immediate-prior source, tie breaks, absent
winner, strictly >50% share, no lookahead), and the documented roll-window
helper.  All inputs are temporary CSVs; no archive, store, network or frozen
config is touched.
"""

from datetime import date
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pandas as pd
import pytest
import zstandard

from scripts.move_exhaustion_v2.data.front_contract import Contract
from scripts.move_exhaustion_v2.data.x0_volume import (
    PLAN_COLUMNS,
    ROLL_AFTER_DAYS,
    ROLL_BEFORE_DAYS,
    VOLUME_COLUMNS,
    VolumePrepassError,
    build_day_plan,
    documented_roll_window_dates,
    merge_monthly_aggregates,
    scan_monthly_volume_csv,
)

RAW_COLUMNS = [
    "ts_event",
    "sequence",
    "symbol",
    "action",
    "side",
    "price",
    "size",
    "bid_px_00",
    "ask_px_00",
    "bid_sz_00",
    "ask_sz_00",
]

_EXPIRY: Dict[str, Tuple[int, int]] = {
    "ZNH6": (2026, 3),
    "ZNM6": (2026, 6),
    "ZNU6": (2026, 9),
    "ZNZ6": (2026, 12),
    "ZNH7": (2027, 3),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    ts_event: str,
    symbol: str,
    *,
    action: str = "T",
    side: str = "B",
    size: int | None = 1,
    sequence: int = 1,
) -> dict:
    """One Databento-like MBP-1 row (naive UTC ts_event, 9-digit nanos)."""
    return {
        "ts_event": ts_event,
        "sequence": sequence,
        "symbol": symbol,
        "action": action,
        "side": side,
        "price": 100.0 if action == "T" else float("nan"),
        "size": size,
        "bid_px_00": 100.0,
        "ask_px_00": 100.015625,
        "bid_sz_00": 5,
        "ask_sz_00": 5,
    }


def _write_csv(tmp_path: Path, name: str, rows: Sequence[dict]) -> Path:
    path = tmp_path / name
    pd.DataFrame(rows, columns=RAW_COLUMNS).to_csv(path, index=False)
    return path


def _write_csv_zst(tmp_path: Path, name: str, rows: Sequence[dict]) -> Path:
    csv_path = _write_csv(tmp_path, name + ".csv", rows)
    zst_path = tmp_path / (name + ".csv.zst")
    zst_path.write_bytes(
        zstandard.ZstdCompressor().compress(csv_path.read_bytes())
    )
    return zst_path


def _volume_table(
    rows: Sequence[Tuple[str, str, int, int]],
) -> pd.DataFrame:
    """Volume aggregate frame from (trading_date, contract, volume, events)."""
    data = []
    for trading_date, contract, trade_volume, event_count in rows:
        expiry_year, expiry_month = _EXPIRY[contract]
        data.append(
            {
                "trading_date": trading_date,
                "contract": contract,
                "expiry_year": expiry_year,
                "expiry_month": expiry_month,
                "trade_volume": trade_volume,
                "event_count": event_count,
            }
        )
    return pd.DataFrame(data, columns=list(VOLUME_COLUMNS)).astype(
        {
            "expiry_year": "int64",
            "expiry_month": "int64",
            "trade_volume": "int64",
            "event_count": "int64",
        }
    )


def _expect_scan(rows: Sequence[dict]) -> pd.DataFrame:
    """Expected scan frame builder for simple single-contract rows."""
    out = pd.DataFrame(rows, columns=list(VOLUME_COLUMNS))
    return out.astype(
        {
            "expiry_year": "int64",
            "expiry_month": "int64",
            "trade_volume": "int64",
            "event_count": "int64",
        }
    )


# ---------------------------------------------------------------------------
# Column surface
# ---------------------------------------------------------------------------

def test_volume_and_plan_column_surfaces_exact():
    assert VOLUME_COLUMNS == (
        "trading_date",
        "contract",
        "expiry_year",
        "expiry_month",
        "trade_volume",
        "event_count",
    )
    assert PLAN_COLUMNS == (
        "trading_date",
        "selection_source_date",
        "selected_contract",
        "selection_share",
        "previous_selected_contract",
        "contract_changed",
        "research_eligible",
        "reason",
    )


# ---------------------------------------------------------------------------
# DuckDB monthly scan
# ---------------------------------------------------------------------------

def test_scan_filters_quarterly_outrights_and_aggregates(tmp_path):
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-02T14:00:00.000000000", "ZNH6", size=10),
            _row("2024-01-02T14:05:00.000000000", "ZNH6", size=5),
            _row("2024-01-02T14:10:00.000000000", "ZNH6", action="A", size=999),
            _row("2024-01-02T15:00:00.000000000", "ZNM6", size=3),
            _row("2024-01-02T16:00:00.000000000", "ZNZ6-ZNH7", size=99),
            _row("2024-01-02T17:00:00.000000000", "ZBM6", size=11),
            _row("2024-01-02T18:00:00.000000000", "ZNF6", size=7),
            _row("2024-01-02T19:00:00.000000000", "TYH6 COMDTY", size=2),
        ],
    )
    out = scan_monthly_volume_csv(path)
    expected = _expect_scan(
        [
            {
                "trading_date": "2024-01-02",
                "contract": "ZNH6",
                "expiry_year": 2026,
                "expiry_month": 3,
                "trade_volume": 17,  # 10 + 5 + 2 (TY alias canonicalized)
                "event_count": 4,    # 3 ZNH6 rows + 1 TYH6 COMDTY row
            },
            {
                "trading_date": "2024-01-02",
                "contract": "ZNM6",
                "expiry_year": 2026,
                "expiry_month": 6,
                "trade_volume": 3,
                "event_count": 1,
            },
        ]
    )
    pd.testing.assert_frame_equal(out, expected)


def test_scan_assigns_et_trading_date_at_midnight_month_boundary_and_dst(tmp_path):
    path = _write_csv(
        tmp_path,
        "GLBX-2024.csv",
        [
            _row("2024-01-01T22:59:59.000000000", "ZNH6", size=5),  # 17:59:59 ET -> 01-01
            _row("2024-01-01T23:00:00.000000000", "ZNH6", size=7),  # 18:00:00 ET -> 01-02
            _row("2024-01-31T23:00:00.000000000", "ZNH6", size=9),  # 18:00:00 ET 01-31 -> 02-01
            _row("2024-02-01T22:00:00.000000000", "ZNH6", size=3),  # 17:00:00 ET 02-01 -> 02-01
            _row("2024-06-02T22:00:00.000000000", "ZNH6", size=1),  # 18:00:00 EDT -> 06-03
            _row("2024-06-03T21:00:00.000000000", "ZNH6", size=2),  # 17:00:00 EDT -> 06-03
        ],
    )
    out = scan_monthly_volume_csv(path)
    expected = _expect_scan(
        [
            {
                "trading_date": "2024-01-01",
                "contract": "ZNH6",
                "expiry_year": 2026,
                "expiry_month": 3,
                "trade_volume": 5,
                "event_count": 1,
            },
            {
                "trading_date": "2024-01-02",
                "contract": "ZNH6",
                "expiry_year": 2026,
                "expiry_month": 3,
                "trade_volume": 7,
                "event_count": 1,
            },
            {
                "trading_date": "2024-02-01",
                "contract": "ZNH6",
                "expiry_year": 2026,
                "expiry_month": 3,
                "trade_volume": 12,
                "event_count": 2,
            },
            {
                "trading_date": "2024-06-03",
                "contract": "ZNH6",
                "expiry_year": 2026,
                "expiry_month": 3,
                "trade_volume": 3,
                "event_count": 2,
            },
        ]
    )
    pd.testing.assert_frame_equal(out, expected)


def test_scan_accepts_databento_epoch_nanosecond_ts_event(tmp_path):
    ts_ns = int(pd.Timestamp("2024-01-02T14:00:00Z").value)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [_row(str(ts_ns), "ZNH6", size=8)],
    )
    out = scan_monthly_volume_csv(path)
    assert list(out["trading_date"]) == ["2024-01-02"]
    assert int(out["trade_volume"].iloc[0]) == 8
    assert int(out["event_count"].iloc[0]) == 1


def test_scan_zstd_input_matches_plain_csv(tmp_path):
    rows = [
        _row("2024-01-02T14:00:00.000000000", "ZNH6", size=4),
        _row("2024-01-02T15:00:00.000000000", "ZNM6", size=2),
    ]
    plain = scan_monthly_volume_csv(_write_csv(tmp_path, "GLBX-202401.csv", rows))
    compressed = scan_monthly_volume_csv(_write_csv_zst(tmp_path, "GLBX-202401", rows))
    pd.testing.assert_frame_equal(plain, compressed)


def test_scan_empty_and_no_valid_symbols_return_schema_only(tmp_path):
    header_only = _write_csv(tmp_path, "GLBX-empty.csv", [])
    out = scan_monthly_volume_csv(header_only)
    assert out.empty
    assert list(out.columns) == list(VOLUME_COLUMNS)
    assert out["trade_volume"].dtype == "int64"
    assert out["event_count"].dtype == "int64"

    no_valid = _write_csv(
        tmp_path,
        "GLBX-novalid.csv",
        [
            _row("2024-01-02T14:00:00.000000000", "ZNZ6-ZNH7", size=9),
            _row("2024-01-02T15:00:00.000000000", "ZBM6", size=9),
        ],
    )
    out2 = scan_monthly_volume_csv(no_valid)
    assert out2.empty
    assert list(out2.columns) == list(VOLUME_COLUMNS)


def test_scan_fails_closed_on_missing_file_and_bad_suffix(tmp_path):
    with pytest.raises(VolumePrepassError):
        scan_monthly_volume_csv(tmp_path / "missing.csv")
    bad = tmp_path / "GLBX-202401.txt"
    bad.write_text("ts_event\n2024-01-01T00:00:00.000000000\n", encoding="utf-8")
    with pytest.raises(VolumePrepassError):
        scan_monthly_volume_csv(bad)


@pytest.mark.parametrize("bad_size", [None, 0, -1])
def test_scan_fails_closed_on_invalid_trade_size(tmp_path, bad_size):
    path = _write_csv(
        tmp_path,
        "GLBX-invalid-size.csv",
        [_row("2024-01-02T14:00:00.000000000", "ZNH6", size=bad_size)],
    )
    with pytest.raises(VolumePrepassError, match="missing/nonpositive"):
        scan_monthly_volume_csv(path)


def test_scan_is_deterministic(tmp_path):
    rows = [
        _row("2024-01-02T14:00:00.000000000", "ZNH6", size=4),
        _row("2024-01-02T15:00:00.000000000", "ZNM6", size=2),
    ]
    path = _write_csv(tmp_path, "GLBX-202401.csv", rows)
    first = scan_monthly_volume_csv(path)
    second = scan_monthly_volume_csv(path)
    pd.testing.assert_frame_equal(first, second)


# ---------------------------------------------------------------------------
# Deterministic monthly merge
# ---------------------------------------------------------------------------

def test_merge_combines_dates_spanning_adjacent_files_order_invariant(tmp_path):
    jan = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-31T23:00:00.000000000", "ZNH6", size=9),   # -> 2024-02-01
            _row("2024-01-31T23:30:00.000000000", "ZNM6", size=2),   # -> 2024-02-01
        ],
    )
    feb = _write_csv(
        tmp_path,
        "GLBX-202402.csv",
        [
            _row("2024-02-01T14:00:00.000000000", "ZNH6", size=3),    # -> 2024-02-01
            _row("2024-02-01T15:00:00.000000000", "ZNM6", action="A", size=999),
        ],
    )
    a = scan_monthly_volume_csv(jan)
    b = scan_monthly_volume_csv(feb)
    merged = merge_monthly_aggregates([a, b])
    expected = _expect_scan(
        [
            {
                "trading_date": "2024-02-01",
                "contract": "ZNH6",
                "expiry_year": 2026,
                "expiry_month": 3,
                "trade_volume": 12,
                "event_count": 2,
            },
            {
                "trading_date": "2024-02-01",
                "contract": "ZNM6",
                "expiry_year": 2026,
                "expiry_month": 6,
                "trade_volume": 2,
                "event_count": 2,
            },
        ]
    )
    pd.testing.assert_frame_equal(merged, expected)
    pd.testing.assert_frame_equal(merge_monthly_aggregates([b, a]), merged)
    pd.testing.assert_frame_equal(merge_monthly_aggregates([]), expected.iloc[0:0])


def test_merge_fails_closed_on_inconsistent_expiry_metadata():
    a = _volume_table([("2024-01-02", "ZNH6", 10, 2)])
    b = a.copy()
    b["expiry_year"] = 2027
    with pytest.raises(VolumePrepassError):
        merge_monthly_aggregates([a, b])


# ---------------------------------------------------------------------------
# Causal day plan
# ---------------------------------------------------------------------------

def test_plan_bootstrap_then_causal_selection(tmp_path):
    volume = _volume_table(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-02", "ZNM6", 20, 5),
            ("2024-01-03", "ZNH6", 80, 8),
            ("2024-01-03", "ZNM6", 30, 4),
            ("2024-01-04", "ZNH6", 70, 7),
            ("2024-01-05", "ZNH6", 60, 6),
        ]
    )
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    plan = build_day_plan(volume, sessions)
    assert list(plan.columns) == list(PLAN_COLUMNS)

    bootstrap = plan.iloc[0]
    assert bootstrap["trading_date"] == "2024-01-02"
    assert bootstrap["research_eligible"] == False
    assert bootstrap["selected_contract"] is None
    assert bootstrap["selection_source_date"] is None
    assert pd.isna(bootstrap["selection_share"])
    assert bootstrap["previous_selected_contract"] is None
    assert bootstrap["contract_changed"] == False
    assert "bootstrap" in bootstrap["reason"]

    d3 = plan.iloc[1]
    assert d3["trading_date"] == "2024-01-03"
    assert d3["selection_source_date"] == "2024-01-02"
    assert d3["selected_contract"] == "ZNH6"
    assert d3["selection_share"] == pytest.approx(100 / 120)
    assert d3["previous_selected_contract"] is None
    assert d3["contract_changed"] == False
    assert d3["research_eligible"] == True
    assert "ZNH6" in d3["reason"] and "0.833333" in d3["reason"]

    d4 = plan.iloc[2]
    assert d4["selection_source_date"] == "2024-01-03"
    assert d4["selected_contract"] == "ZNH6"
    assert d4["selection_share"] == pytest.approx(80 / 110)
    assert d4["previous_selected_contract"] == "ZNH6"
    assert d4["contract_changed"] == False

    d5 = plan.iloc[3]
    assert d5["selection_source_date"] == "2024-01-04"
    assert d5["selected_contract"] == "ZNH6"
    assert d5["selection_share"] == pytest.approx(1.0)
    assert d5["contract_changed"] == False


def test_plan_detects_contract_change(tmp_path):
    volume = _volume_table(
        [
            ("2024-01-02", "ZNH6", 90, 9),
            ("2024-01-02", "ZNM6", 10, 1),
            ("2024-01-03", "ZNH6", 40, 4),
            ("2024-01-03", "ZNM6", 60, 6),  # winner flips to ZNM6
            ("2024-01-04", "ZNH6", 45, 5),
            ("2024-01-04", "ZNM6", 55, 6),
            ("2024-01-05", "ZNM6", 70, 7),
            ("2024-01-06", "ZNM6", 60, 6),
        ]
    )
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-06"]
    plan = build_day_plan(volume, sessions)
    assert plan.iloc[1]["selected_contract"] == "ZNH6"
    changed = plan.iloc[2]
    assert changed["selected_contract"] == "ZNM6"
    assert changed["previous_selected_contract"] == "ZNH6"
    assert changed["contract_changed"] == True
    assert changed["selection_share"] == pytest.approx(0.6)
    assert plan.iloc[3]["contract_changed"] == False
    assert plan.iloc[3]["previous_selected_contract"] == "ZNM6"
    assert plan.iloc[4]["selected_contract"] == "ZNM6"


def test_plan_tie_break_earliest_expiry_then_symbol(tmp_path):
    volume = _volume_table(
        [
            ("2024-01-02", "ZNH6", 50, 5),
            ("2024-01-02", "ZNM6", 50, 5),
            ("2024-01-02", "ZNU6", 50, 5),
            ("2024-01-03", "ZNH6", 60, 6),
            ("2024-01-04", "ZNH6", 60, 6),
            # Year-boundary tie: ZNZ6 (2026-12) must beat ZNH7 (2027-03).
            ("2024-01-05", "ZNZ6", 50, 5),
            ("2024-01-05", "ZNH7", 50, 5),
            ("2024-01-06", "ZNZ6", 60, 6),
            ("2024-01-07", "ZNZ6", 60, 6),
        ]
    )
    sessions = [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
        "2024-01-06",
        "2024-01-07",
    ]
    # Default 0.50 gate: a three-way tie (share 1/3) is not strictly > 0.50.
    default_plan = build_day_plan(volume, sessions)
    tie_day = default_plan.iloc[1]
    assert tie_day["research_eligible"] == False
    assert "not strictly greater" in tie_day["reason"]

    # Lowered gate isolates the tie-break: earliest expiry wins.
    tie_plan = build_day_plan(volume, sessions, prior_min_share=0.30)
    assert tie_plan.iloc[1]["selected_contract"] == "ZNH6"
    assert tie_plan.iloc[1]["selection_share"] == pytest.approx(50 / 150)
    assert tie_plan.iloc[4]["selected_contract"] == "ZNZ6"
    assert tie_plan.iloc[4]["selection_share"] == pytest.approx(50 / 100)


def test_plan_absent_winner_current_day_is_ineligible(tmp_path):
    volume = _volume_table(
        [
            ("2024-01-02", "ZNH6", 80, 8),
            ("2024-01-02", "ZNM6", 20, 2),
            ("2024-01-03", "ZNM6", 100, 10),  # ZNH6 absent
            ("2024-01-04", "ZNH6", 10, 1),
            ("2024-01-04", "ZNM6", 90, 9),
        ]
    )
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    plan = build_day_plan(volume, sessions)
    absent = plan.iloc[1]
    assert absent["research_eligible"] == False
    assert absent["selected_contract"] is None
    assert pd.isna(absent["selection_share"])
    assert "not present" in absent["reason"]
    # Next day recovers from the 01-03 winner.
    recovered = plan.iloc[2]
    assert recovered["research_eligible"] == True
    assert recovered["selected_contract"] == "ZNM6"
    assert recovered["selection_share"] == pytest.approx(1.0)


def test_plan_requires_share_strictly_greater_than_50_percent(tmp_path):
    volume = _volume_table(
        [
            ("2024-01-02", "ZNH6", 50, 5),
            ("2024-01-02", "ZNM6", 50, 5),  # exactly 0.50 -> not eligible
            ("2024-01-03", "ZNH6", 51, 6),
            ("2024-01-03", "ZNM6", 49, 5),  # 0.51 -> eligible
            ("2024-01-04", "ZNH6", 55, 6),
            ("2024-01-04", "ZNM6", 45, 5),
        ]
    )
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    plan = build_day_plan(volume, sessions)
    exact_half = plan.iloc[1]
    assert exact_half["research_eligible"] == False
    assert exact_half["selected_contract"] is None
    assert "0.500000" in exact_half["reason"] and "strictly" in exact_half["reason"]

    above_half = plan.iloc[2]
    assert above_half["research_eligible"] == True
    assert above_half["selected_contract"] == "ZNH6"
    assert above_half["selection_share"] == pytest.approx(0.51)


def test_plan_no_lookahead(tmp_path):
    base = _volume_table(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-02", "ZNM6", 10, 1),
            ("2024-01-03", "ZNH6", 60, 6),
            ("2024-01-03", "ZNM6", 40, 4),
            ("2024-01-04", "ZNH6", 55, 6),
            ("2024-01-04", "ZNM6", 45, 5),
            ("2024-01-05", "ZNH6", 50, 5),
            ("2024-01-05", "ZNZ6", 10, 1),
        ]
    )
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    plan_before = build_day_plan(base, sessions)

    mutated = pd.concat(
        [
            base,
            _volume_table([("2024-01-04", "ZNZ6", 2000, 50)]),
        ],
        ignore_index=True,
    )
    plan_after = build_day_plan(mutated, sessions)
    # Rows for 01-02..01-04 must be byte-identical: selection uses only the
    # immediately prior session, never same-day or future information.
    pd.testing.assert_frame_equal(plan_before.iloc[:3], plan_after.iloc[:3])
    # The 01-05 row (driven by 01-04's winner) legitimately changes.
    assert plan_before.iloc[3]["selected_contract"] == "ZNH6"
    assert plan_after.iloc[3]["selected_contract"] == "ZNZ6"


def test_plan_validates_sessions_and_min_share(tmp_path):
    volume = _volume_table([("2024-01-02", "ZNH6", 10, 1)])
    with pytest.raises(ValueError):
        build_day_plan(volume, [])
    with pytest.raises(ValueError):
        build_day_plan(volume, ["2024-01-03", "2024-01-02"])
    with pytest.raises(ValueError):
        build_day_plan(volume, ["2024-01-02", "2024-01-02"])
    with pytest.raises(ValueError):
        build_day_plan(volume, ["2024-01-02"], prior_min_share=0.0)
    with pytest.raises(ValueError):
        build_day_plan(volume, ["2024-01-02"], prior_min_share=1.0)


# ---------------------------------------------------------------------------
# Documented roll windows
# ---------------------------------------------------------------------------

def test_roll_window_adjacent_quarter_inclusive_bounds():
    ref = date(2024, 1, 1)
    window = documented_roll_window_dates("ZNH6", "ZNM6", reference_date=ref)
    assert window[0] == date(2026, 5, 11)   # 2026-06-01 - 21 days
    assert window[-1] == date(2026, 6, 11)  # 2026-06-01 + 10 days
    assert len(window) == ROLL_BEFORE_DAYS + 1 + ROLL_AFTER_DAYS
    assert window == sorted(window)

    # Same window when passing Contract objects instead of symbols.
    object_window = documented_roll_window_dates(
        Contract(month=3, year=2026),
        Contract(month=6, year=2026),
        reference_date=ref,
    )
    assert object_window == window


def test_roll_window_crosses_year_boundary():
    window = documented_roll_window_dates(
        "ZNZ6", "ZNH7", reference_date=date(2024, 1, 1)
    )
    assert window[0] == date(2027, 2, 8)    # 2027-03-01 - 21 days
    assert window[-1] == date(2027, 3, 11)  # 2027-03-01 + 10 days


def test_roll_window_non_adjacent_pairs_never_permitted():
    ref = date(2024, 1, 1)
    assert documented_roll_window_dates("ZNH6", "ZNZ6", reference_date=ref) == []
    assert documented_roll_window_dates("ZNH6", "ZNH6", reference_date=ref) == []
    assert documented_roll_window_dates("ZNM6", "ZNH6", reference_date=ref) == []


def test_roll_window_fails_closed_on_unparseable_contracts():
    ref = date(2024, 1, 1)
    with pytest.raises(VolumePrepassError):
        documented_roll_window_dates("ZNZ6-ZNH7", "ZNM6", reference_date=ref)
    with pytest.raises(ValueError):
        documented_roll_window_dates(
            "ZNH6", "ZNM6", reference_date=ref, days_before=-1
        )
