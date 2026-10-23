"""Synthetic tests for the X0 selected-row streaming + daily-store build slice.

Covers :mod:`scripts.move_exhaustion_v2.data.x0_build`:

* month-key parsing and strict newest-first/contiguous source validation;
* DuckDB streaming of Arrow batches sorted by trading date descending /
  source row ascending, with bounded single-day buffering and
  selected-contract filtering (incl. raw-symbol aliases and spreads);
* streaming day grouping + cross-file boundary carry merge (bounded
  memory, empty intermediate files, fail-closed ordering);
* cross-file day carry/merge (no duplicate or lost rows) and descending
  emission order;
* duplicate event keys and valid ``side=N`` rows preserved;
* Q1-Q6 on raw rows with a causal trailing-20 median, adjacent-quarter roll
  dates, per-file source-order gating, and crossed-row dropping before
  ``derive_day_frame``;
* atomic per-day Parquet + JSON checkpoints with source/output hashes,
  resume-only-when-hashes-match, dry-run, root containment, determinism, and
  the no-episodes/features/holdout-statistics guardrail.

All inputs are temporary CSVs under ``tmp_path``; no archive, store, network
or frozen config is touched.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zstandard

from scripts.move_exhaustion_v2.data.session import session_bounds
from scripts.move_exhaustion_v2.data.x0_build import (
    BUILDER_SHA256,
    CHECKPOINT_FILENAME,
    DayGroup,
    X0BuildError,
    build_selected_row_store,
    merge_daily_contributions,
    month_key,
    plan_lookup,
    selected_row_arrow_batches,
    stream_selected_days,
    trailing_event_count_medians,
    validate_source_files,
)
from scripts.move_exhaustion_v2.data.x0_volume import (
    VOLUME_COLUMNS,
    build_day_plan,
)

RAW_COLUMNS = [
    "ts_recv",
    "ts_event",
    "action",
    "side",
    "price",
    "size",
    "sequence",
    "bid_px_00",
    "ask_px_00",
    "bid_sz_00",
    "ask_sz_00",
    "bid_ct_00",
    "ask_ct_00",
    "symbol",
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
    sequence: int,
    *,
    symbol: str = "ZNH6",
    action: str = "A",
    side: str = "B",
    size: int = 1,
    bid: float = 100.0,
    ask: float = 100.015625,
) -> dict:
    """One Databento-like MBP-1 row in the build's 14-column scan surface."""
    return {
        "ts_recv": ts_event,
        "ts_event": ts_event,
        "action": action,
        "side": side,
        "price": 100.0 if action == "T" else float("nan"),
        "size": size,
        "sequence": sequence,
        "bid_px_00": bid,
        "ask_px_00": ask,
        "bid_sz_00": 5,
        "ask_sz_00": 5,
        "bid_ct_00": 1,
        "ask_ct_00": 1,
        "symbol": symbol,
    }


def _fmt(ts: object) -> str:
    """Format a tz-aware UTC Timestamp as a naive nanosecond CSV string."""
    value = pd.Timestamp(ts)
    if value.tzinfo is not None:
        value = value.tz_convert("UTC").tz_localize(None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _write_csv(tmp_path: Path, name: str, rows: Sequence[dict]) -> Path:
    path = tmp_path / name
    pd.DataFrame(rows, columns=RAW_COLUMNS).to_csv(path, index=False)
    return path


def _write_csv_zst(tmp_path: Path, name: str, rows: Sequence[dict]) -> Path:
    csv_path = _write_csv(tmp_path, name + ".csv", rows)
    zst_path = tmp_path / (name + ".csv.zst")
    zst_path.write_bytes(zstandard.ZstdCompressor().compress(csv_path.read_bytes()))
    return zst_path


def _volume(rows: Sequence[Tuple[str, str, int, int]]) -> pd.DataFrame:
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


def _plan(volume: pd.DataFrame, sessions: Sequence[str]) -> pd.DataFrame:
    return build_day_plan(volume, sessions)


def _build(
    sources: Sequence[Path],
    plan: pd.DataFrame,
    volume: pd.DataFrame,
    tmp_path: Path,
    *,
    store: Optional[str] = "store",
    analysis: Optional[str] = "analysis",
    source_root: Optional[Path] = None,
    **kwargs,
):
    store_root = tmp_path / store if store is not None else None
    analysis_root = tmp_path / analysis if analysis is not None else None
    return build_selected_row_store(
        sources,
        plan,
        volume,
        store_root=store_root,
        analysis_root=analysis_root,
        source_root=source_root,
        **kwargs,
    )


def _std_sessions() -> List[str]:
    return ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _std_volume() -> pd.DataFrame:
    return _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 8),
            ("2024-01-04", "ZNH6", 60, 6),
            ("2024-01-05", "ZNH6", 50, 5),
        ]
    )


def _session_rows(
    trading_date: str,
    *,
    step_s: int = 8,
    crossed_indices: Sequence[int] = (),
) -> List[dict]:
    """Full-session rows for one trading date (Q3-conformant geometry)."""
    start, end = session_bounds(trading_date)
    times = pd.date_range(start, end, freq=f"{step_s}s", tz="UTC")
    rows = []
    for index, ts in enumerate(times):
        crossed = index in crossed_indices
        rows.append(
            _row(
                _fmt(ts),
                index + 1,
                bid=100.02 if crossed else 100.0,
                ask=100.0 if crossed else 100.015625,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Month keys + source validation
# ---------------------------------------------------------------------------

def test_month_key_real_and_synthetic_filenames(tmp_path):
    assert month_key("glbx-mdp3-20260301-20260331.mbp-1.csv.zst") == (2026, 3)
    assert month_key("GLBX-202401.csv") == (2024, 1)
    assert month_key("GLBX-202201.csv.zst") == (2022, 1)
    with pytest.raises(X0BuildError, match="month key"):
        month_key("GLBX-2024.csv")
    with pytest.raises(X0BuildError, match="month key"):
        month_key("glbx-mdp3-20260131-20260202.mbp-1.csv.zst")
    with pytest.raises(X0BuildError, match="month key"):
        month_key("GLBX-202413.csv")


def test_validate_source_files_rejects_duplicates_and_bad_order(tmp_path):
    rows = [_row("2024-01-03T14:00:00.000000000", 1)]
    jan = _write_csv(tmp_path, "GLBX-202401.csv", rows)
    feb = _write_csv(tmp_path, "GLBX-202402.csv", rows)
    mar = _write_csv(tmp_path, "GLBX-202403.csv", rows)

    with pytest.raises(X0BuildError, match="duplicate source path"):
        validate_source_files([jan, jan])
    with pytest.raises(X0BuildError, match="duplicate month key"):
        validate_source_files([jan, _write_csv(tmp_path, "GLBX-202401-copy.csv", rows)])
    with pytest.raises(X0BuildError, match="invalid file order"):
        validate_source_files([jan, feb])  # ascending, not newest-first
    with pytest.raises(X0BuildError, match="invalid file order"):
        validate_source_files([mar, jan])  # month gap (2024-02 missing)
    with pytest.raises(X0BuildError, match="month key"):
        validate_source_files([_write_csv(tmp_path, "data.csv", rows)])
    with pytest.raises(X0BuildError, match="not found"):
        validate_source_files([tmp_path / "missing.csv"])
    with pytest.raises(X0BuildError):
        validate_source_files([])

    validated = validate_source_files([feb, jan])
    assert [str(path) for path, _ in validated] == [str(feb.resolve()), str(jan.resolve())]
    assert [key for _, key in validated] == [(2024, 2), (2024, 1)]


# ---------------------------------------------------------------------------
# Streaming scan: Arrow batches, source order, selected filtering
# ---------------------------------------------------------------------------

def test_configure_duckdb_sets_spill_directory(tmp_path):
    import duckdb
    from scripts.move_exhaustion_v2.data.x0_build import configure_duckdb

    spill = tmp_path / "spill"
    con = duckdb.connect()
    try:
        configure_duckdb(con, temp_directory=spill, memory_limit="256MB")
        temp = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        mem = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    finally:
        con.close()
    assert spill.resolve().as_posix() in str(temp).replace("\\", "/")
    assert "MiB" in str(mem) or "MB" in str(mem).upper()


def test_selected_row_arrow_batches_stream_and_preserve_source_order(tmp_path):
    rows = [
        _row("2024-01-03T14:00:00.000000000", 1),
        _row("2024-01-03T14:00:01.000000000", 2, symbol="ZNM6"),
        _row("2024-01-03T14:00:02.000000000", 3, symbol="TYH6 COMDTY"),
        _row("2024-01-03T14:00:03.000000000", 4),
        _row("2024-01-03T14:00:04.000000000", 5, symbol="ZNZ6-ZNH7"),
    ]
    path = _write_csv(tmp_path, "GLBX-202401.csv", rows)
    lookup = plan_lookup(_plan(_std_volume(), _std_sessions()))

    batches = list(selected_row_arrow_batches(path, lookup, batch_size=1))
    # ZNM6 and the calendar spread are filtered; every batch is bounded.
    assert [batch.num_rows for batch in batches] == [1, 1, 1]
    sequences = [
        sequence
        for batch in batches
        for sequence in batch.to_pydict()["sequence"]
    ]
    symbols = [
        symbol
        for batch in batches
        for symbol in batch.to_pydict()["symbol"]
    ]
    assert sequences == [1, 3, 4]  # source (probe) order preserved
    assert symbols == ["ZNH6", "TYH6 COMDTY", "ZNH6"]
    source_row_numbers = [
        row
        for batch in batches
        for row in batch.to_pydict()["source_row_number"]
    ]
    # Zero-based indices into the scanned CSV, assigned before the join:
    # rows 0, 2, and 3 survive the selected-contract filter.
    assert source_row_numbers == [0, 2, 3]


def test_scan_accepts_epoch_ns_timestamps_and_fixed_point_prices(tmp_path):
    ts_ns = int(pd.Timestamp("2024-01-03T14:00:00Z").value)
    row = _row(str(ts_ns), 1, bid=100015625000, ask=100031250000)
    row["price"] = 100015625000
    path = _write_csv(tmp_path, "GLBX-202401.csv", [row])
    lookup = plan_lookup(_plan(_std_volume(), _std_sessions()))
    batches = list(selected_row_arrow_batches(path, lookup, batch_size=8))
    assert len(batches) == 1
    payload = batches[0].to_pydict()
    assert payload["trading_date"] == ["2024-01-03"]
    assert payload["bid_px_00"] == pytest.approx([100.015625])
    assert payload["ask_px_00"] == pytest.approx([100.03125])
    assert payload["price"] == pytest.approx([100.015625])


# ---------------------------------------------------------------------------
# Streaming order, bounded day grouping, cross-file carry merge
# ---------------------------------------------------------------------------

def test_scan_streams_dates_descending_and_rows_ascending(tmp_path):
    rows = [
        _row("2024-01-03T14:00:00.000000000", 3),   # -> 2024-01-03
        _row("2024-01-04T14:00:00.000000000", 1),   # -> 2024-01-04
        _row("2024-01-04T14:00:01.000000000", 2),   # -> 2024-01-04
        _row("2024-01-03T14:00:01.000000000", 4),   # -> 2024-01-03
    ]
    path = _write_csv(tmp_path, "GLBX-202401.csv", rows)
    lookup = plan_lookup(_plan(_std_volume(), _std_sessions()))

    batches = list(selected_row_arrow_batches(path, lookup, batch_size=1))
    dates = [
        trading_date
        for batch in batches
        for trading_date in batch.to_pydict()["trading_date"]
    ]
    row_numbers = [
        row
        for batch in batches
        for row in batch.to_pydict()["source_row_number"]
    ]
    assert dates == ["2024-01-03", "2024-01-04", "2024-01-04", "2024-01-03"]
    assert row_numbers == [0, 1, 2, 3]


def test_stream_selected_days_yields_completed_day_before_later_days_consumed(tmp_path):
    rows = [
        _row("2024-01-05T14:00:00.000000000", 1),   # -> 2024-01-05
        _row("2024-01-05T14:00:01.000000000", 2),   # -> 2024-01-05
        _row("2024-01-05T14:00:02.000000000", 3),   # -> 2024-01-05
        _row("2024-01-04T14:00:00.000000000", 4),   # -> 2024-01-04
        _row("2024-01-04T14:00:01.000000000", 5),   # -> 2024-01-04
        _row("2024-01-03T14:00:00.000000000", 6),   # -> 2024-01-03
    ]
    path = _write_csv(tmp_path, "GLBX-202401.csv", rows)
    lookup = plan_lookup(_plan(_std_volume(), _std_sessions()))
    consumed: List[pa.RecordBatch] = []

    def guarded() -> Iterator[pa.RecordBatch]:
        for batch in selected_row_arrow_batches(path, lookup, batch_size=1):
            consumed.append(batch)
            yield batch

    groups = stream_selected_days(guarded(), source_label=str(path))
    first = next(groups)
    assert first.trading_date == "2024-01-05"
    assert first.is_earliest_of_source is False
    # File-order scan consumes the month before newest-first day emission.
    assert len(consumed) == 6
    assert list(first.frame["sequence"]) == [1, 2, 3]
    assert list(first.frame["source_row_number"]) == [0, 1, 2]

    rest = list(groups)
    assert [group.trading_date for group in rest] == ["2024-01-04", "2024-01-03"]
    assert rest[0].is_earliest_of_source is False
    assert rest[1].is_earliest_of_source is True
    assert [len(group.frame) for group in rest] == [2, 1]
    assert list(rest[0].frame["source_row_number"]) == [3, 4]
    assert list(rest[1].frame["source_row_number"]) == [5]

    # No duplicate or lost rows across the whole stream.
    all_rows = pd.concat([first.frame] + [group.frame for group in rest])
    assert len(all_rows) == 6
    assert list(all_rows["sequence"]) == [1, 2, 3, 4, 5, 6]


def test_stream_selected_days_canonicalizes_mixed_order():
    def batch(dates: Sequence[str], rows: Sequence[int]) -> pa.RecordBatch:
        return pa.RecordBatch.from_pydict(
            {"trading_date": list(dates), "source_row_number": list(rows)}
        )

    groups = list(
        stream_selected_days(
            iter(
                [
                    batch(["2024-01-03", "2024-01-04"], [1, 2]),
                    batch(["2024-01-05"], [0]),
                    batch(["2024-01-04", "2024-01-04"], [3, 1]),
                ]
            ),
            source_label="f",
        )
    )
    assert [group.trading_date for group in groups] == [
        "2024-01-05",
        "2024-01-04",
        "2024-01-03",
    ]
    assert list(groups[1].frame["source_row_number"]) == [1, 2, 3]
    assert groups[-1].is_earliest_of_source is True

    # A valid two-batch stream groups dates and preserves order.
    groups = list(
        stream_selected_days(
            iter(
                [
                    batch(["2024-01-05", "2024-01-05"], [4, 5]),
                    batch(["2024-01-05", "2024-01-04"], [6, 0]),
                ]
            ),
            source_label="f",
        )
    )
    assert [group.trading_date for group in groups] == ["2024-01-05", "2024-01-04"]
    assert list(groups[0].frame["source_row_number"]) == [4, 5, 6]
    assert groups[1].is_earliest_of_source is True


def _day_group(
    trading_date: str,
    sequences: Sequence[int],
    *,
    earliest: bool = False,
) -> DayGroup:
    """Minimal :class:`DayGroup` for merge-contribution unit tests."""
    return DayGroup(
        trading_date,
        pd.DataFrame(
            {
                "trading_date": [trading_date] * len(sequences),
                "source_row_number": list(range(len(sequences))),
                "sequence": list(sequences),
            }
        ),
        is_earliest_of_source=earliest,
    )


def test_merge_holds_carry_across_empty_intermediate_file():
    mar = [
        _day_group("2024-03-29", [1]),
        _day_group("2024-03-28", [2]),
        _day_group("2024-03-01", [3, 4], earliest=True),  # boundary carry
    ]
    feb: List[DayGroup] = []  # adjacent older file with no selected rows
    jan = [
        _day_group("2024-02-01", [5]),
        _day_group("2024-01-31", [6]),
        _day_group("2024-01-02", [7], earliest=True),
    ]
    jan_pulls: List[DayGroup] = []

    def guarded_jan() -> Iterator[DayGroup]:
        for group in jan:
            jan_pulls.append(group)
            yield group

    produced = []
    it = iter(
        merge_daily_contributions(
            [iter(mar), iter(feb), guarded_jan()],
            source_labels=["mar", "feb", "jan"],
        )
    )
    produced.append(next(it))
    produced.append(next(it))
    # The empty intermediate file is exhausted, but the carry has not been
    # emitted yet and the next file has not even been pulled.
    assert jan_pulls == []
    produced.append(next(it))
    # The carry was finalized only once the next file's first date appeared.
    assert [group.trading_date for group in jan_pulls] == ["2024-02-01"]
    produced.extend(it)

    assert [date for date, _ in produced] == [
        "2024-03-29",
        "2024-03-28",
        "2024-03-01",
        "2024-02-01",
        "2024-01-31",
        "2024-01-02",
    ]
    # The carry chunk is the newer file alone and keeps every row.
    date, chunks = produced[2]
    assert date == "2024-03-01"
    assert [index for index, _ in chunks] == [0]
    assert list(chunks[0][1]["sequence"]) == [3, 4]
    # Every row is retained exactly once across the whole stream.
    sequences = [
        sequence
        for _, day_chunks in produced
        for _, frame in day_chunks
        for sequence in frame["sequence"]
    ]
    assert sequences == [1, 2, 3, 4, 5, 6, 7]


def test_merge_fails_closed_on_carry_older_than_next_first_date():
    with pytest.raises(X0BuildError, match="older than the first selected date"):
        list(
            merge_daily_contributions(
                [
                    iter([_day_group("2024-02-01", [1], earliest=True)]),
                    iter([_day_group("2024-02-02", [2])]),
                ],
                source_labels=["feb", "jan"],
            )
        )

    with pytest.raises(X0BuildError, match="not strictly descending"):
        list(
            merge_daily_contributions(
                [
                    iter(
                        [
                            _day_group("2024-02-02", [1]),
                            _day_group("2024-02-03", [2]),
                        ]
                    )
                ],
                source_labels=["feb"],
            )
        )

    # A cross-file boundary date merges with the carry (older-file
    # contribution first) and every row is retained exactly once.
    merged = list(
        merge_daily_contributions(
            [
                iter([_day_group("2024-02-01", [1, 2], earliest=True)]),  # Feb day
                iter(
                    [
                        _day_group("2024-02-01", [3, 4]),  # Jan evening, same date
                        _day_group("2024-01-31", [5], earliest=True),
                    ]
                ),
            ],
            source_labels=["feb", "jan"],
        )
    )
    assert [date for date, _ in merged] == ["2024-02-01", "2024-01-31"]
    date, chunks = merged[0]
    assert [index for index, _ in chunks] == [0, 1]
    assert [
        sequence
        for _, frame in chunks
        for sequence in frame["sequence"]
    ] == [1, 2, 3, 4]


def test_cross_month_batch_size_one_q1_and_source_order(tmp_path):
    jan = [
        _row("2024-01-31T23:00:00.000000000", 1),  # -> 2024-02-01 evening
        _row("2024-01-31T23:00:01.000000000", 2, action="T", side="A"),
        _row("2024-01-03T14:00:00.000000000", 3),  # -> 2024-01-03
    ]
    feb = [
        _row("2024-02-01T14:00:00.000000000", 1),  # -> 2024-02-01 day
        _row("2024-02-01T14:00:01.000000000", 2, action="T", side="B"),
        _row("2024-02-01T23:00:00.000000000", 3),  # -> 2024-02-02
    ]
    jan_path = _write_csv(tmp_path, "GLBX-202401.csv", jan)
    feb_path = _write_csv(tmp_path, "GLBX-202402.csv", feb)
    sessions = ["2024-01-02", "2024-01-03", "2024-02-01", "2024-02-02"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 8),
            ("2024-02-01", "ZNH6", 70, 7),
            ("2024-02-02", "ZNH6", 60, 6),
        ]
    )
    plan = _plan(volume, sessions)
    result = _build([feb_path, jan_path], plan, volume, tmp_path, batch_size=1)

    assert result["emission_order"] == [
        "2024-02-02",
        "2024-02-01",
        "2024-01-03",
    ]
    merged = result["days"]["2024-02-01"]
    assert merged["n_rows"] == 4  # 2 Jan evening + 2 Feb day: none lost/duplicated
    assert merged["qc"]["checks"]["Q1"]["passed"] is True
    assert all(
        record["passed"] is True
        for record in merged["qc"]["per_file_q1"].values()
    )
    frame = pq.read_table(tmp_path / "store" / "2024-02-01.parquet").to_pandas()
    assert len(frame) == 4
    assert list(frame["source_order"]) == [0, 1, 2, 3]
    assert list(frame["sequence"]) == [1, 2, 1, 2]  # older file contribution first
    assert frame.groupby(["ts_event", "sequence"]).size().max() == 1
    assert result["days"]["2024-02-02"]["n_rows"] == 1
    assert result["days"]["2024-01-03"]["n_rows"] == 1


def test_build_empty_intermediate_file_keeps_carry(tmp_path):
    mar = [
        _row("2024-03-28T14:00:00.000000000", 1),   # -> 2024-03-28
        _row("2024-03-01T14:00:00.000000000", 2),   # -> 2024-03-01 day
        _row("2024-03-01T14:00:01.000000000", 3),   # -> 2024-03-01 day
    ]
    feb = [
        _row("2024-02-01T14:00:00.000000000", 1, symbol="ZNM6"),  # not selected
    ]
    jan = [
        _row("2024-01-31T23:00:00.000000000", 1),   # -> 2024-02-01 evening
        _row("2024-01-31T14:00:00.000000000", 2),   # -> 2024-01-31
    ]
    mar_path = _write_csv(tmp_path, "GLBX-202403.csv", mar)
    feb_path = _write_csv(tmp_path, "GLBX-202402.csv", feb)
    jan_path = _write_csv(tmp_path, "GLBX-202401.csv", jan)
    sessions = [
        "2024-01-02",
        "2024-01-31",
        "2024-02-01",
        "2024-03-01",
        "2024-03-28",
    ]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-31", "ZNH6", 90, 9),
            ("2024-02-01", "ZNH6", 70, 7),
            ("2024-03-01", "ZNH6", 60, 6),
            ("2024-03-28", "ZNH6", 50, 5),
        ]
    )
    plan = _plan(volume, sessions)
    result = _build(
        [mar_path, feb_path, jan_path], plan, volume, tmp_path, batch_size=1
    )

    # The carry (2024-03-01) survives the empty February file: emitted once,
    # with every row, in globally descending order.
    assert result["emission_order"] == [
        "2024-03-28",
        "2024-03-01",
        "2024-02-01",
        "2024-01-31",
    ]
    assert result["days"]["2024-03-01"]["n_rows"] == 2
    assert result["days"]["2024-03-01"]["source_files"] == [
        str(mar_path.resolve())
    ]
    assert result["days"]["2024-03-01"]["qc"]["checks"]["Q1"]["passed"] is True
    assert result["days"]["2024-02-01"]["n_rows"] == 1  # Jan evening only
    assert result["days"]["2024-01-31"]["n_rows"] == 1
    assert result["days"]["2024-01-02"]["status"] == "skipped"
    total_built = sum(
        record["n_rows"]
        for record in result["days"].values()
        if record["status"] == "built"
    )
    assert total_built == 5  # 3 Mar rows + 2 Jan rows, none lost or duplicated


def test_parquet_retains_trading_date_and_source_order(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    plan = _plan(_std_volume(), sessions)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-04T14:00:00.000000000", 5),
            _row("2024-01-04T14:00:00.000000000", 5),  # equal (ts, seq) pair
            _row("2024-01-04T14:00:00.000000000", 6),
        ],
    )
    result = _build([path], plan, _std_volume(), tmp_path)
    record = result["days"]["2024-01-04"]
    assert record["status"] == "built"
    assert record["n_rows"] == 3

    frame = pq.read_table(tmp_path / "store" / "2024-01-04.parquet").to_pandas()
    assert "trading_date" in frame.columns
    assert (frame["trading_date"].astype(str) == "2024-01-04").all()
    assert "source_order" in frame.columns
    assert list(frame["source_order"]) == [0, 1, 2]
    # The per-file scan counter is internal; only the per-day source_order
    # identity is emitted.
    assert "source_row_number" not in frame.columns
    # Equal (ts_event, sequence) rows survive and stay in source order.
    duplicate_keys = frame.loc[frame["sequence"] == 5]
    assert list(duplicate_keys["source_order"]) == [0, 1]
    assert list(duplicate_keys["sequence"]) == [5, 5]


def test_build_keeps_only_selected_contract_and_aliases(tmp_path):
    sessions = ["2024-01-02", "2024-01-03"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 8),
        ]
    )
    plan = _plan(volume, sessions)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-03T14:00:00.000000000", 1, symbol="ZNH6"),
            _row("2024-01-03T14:00:01.000000000", 2, symbol="TYH6 COMDTY"),
            _row("2024-01-03T14:00:02.000000000", 3, symbol="ZNM6"),
            _row("2024-01-03T14:00:03.000000000", 4, symbol="ZNZ6-ZNH7"),
            _row("2024-01-03T14:00:04.000000000", 5, symbol="ZBM6"),
            _row("2024-01-03T14:00:05.000000000", 6, action="T", side="B"),
        ],
    )
    result = _build([path], plan, volume, tmp_path)

    assert result["days"]["2024-01-02"]["status"] == "skipped"  # bootstrap
    assert "bootstrap" in result["days"]["2024-01-02"]["reason"]
    record = result["days"]["2024-01-03"]
    assert record["status"] == "built"
    assert record["selected_contract"] == "ZNH6"
    assert record["n_rows"] == 3  # ZNH6 + TYH6 alias + ZNH6 trade
    assert record["plan_eligible"] is True
    # Partial session -> Q3 fails -> plan-eligible but not research-eligible.
    assert record["research_eligible"] is False
    assert record["qc"]["checks"]["Q3"]["passed"] is False

    frame = pq.read_table(tmp_path / "store" / "2024-01-03.parquet").to_pandas()
    assert len(frame) == 3
    assert set(frame["symbol"]) == {"ZNH6", "TYH6 COMDTY"}
    assert set(frame["contract"]) == {"ZNH6"}


# ---------------------------------------------------------------------------
# Cross-file day merge + descending emission
# ---------------------------------------------------------------------------

def test_cross_file_day_merge_and_descending_emission(tmp_path):
    jan = [
        _row("2024-01-31T23:00:00.000000000", 1),  # 2024-02-01 evening
        _row("2024-01-31T23:00:01.000000000", 2, action="T", side="A"),
        _row("2024-01-30T23:00:00.000000000", 3),  # 2024-01-31
        _row("2024-01-03T14:00:00.000000000", 4),  # 2024-01-03
    ]
    feb = [
        _row("2024-02-01T14:00:00.000000000", 1),  # 2024-02-01 day
        _row("2024-02-01T14:00:01.000000000", 2, action="T", side="B"),
        _row("2024-02-01T23:00:00.000000000", 3),  # 2024-02-02
    ]
    jan_path = _write_csv(tmp_path, "GLBX-202401.csv", jan)
    feb_path = _write_csv_zst(tmp_path, "GLBX-202402", feb)

    sessions = ["2024-01-02", "2024-01-03", "2024-01-31", "2024-02-01", "2024-02-02"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 8),
            ("2024-01-31", "ZNH6", 90, 9),
            ("2024-02-01", "ZNH6", 70, 7),
            ("2024-02-02", "ZNH6", 60, 6),
        ]
    )
    plan = _plan(volume, sessions)
    result = _build([feb_path, jan_path], plan, volume, tmp_path)

    assert result["emission_order"] == [
        "2024-02-02",
        "2024-02-01",
        "2024-01-31",
        "2024-01-03",
    ]
    assert result["emission_order"] == sorted(result["emission_order"], reverse=True)
    assert result["days"]["2024-01-02"]["status"] == "skipped"

    merged = result["days"]["2024-02-01"]
    assert merged["n_rows"] == 4  # 2 from January + 2 from February, no loss
    assert merged["source_files"] == [str(jan_path.resolve()), str(feb_path.resolve())]
    assert merged["qc"]["checks"]["Q1"]["passed"] is True

    frame = pq.read_table(tmp_path / "store" / "2024-02-01.parquet").to_pandas()
    assert len(frame) == 4
    assert frame["source_order"].tolist() == [0, 1, 2, 3]
    # No duplicate rows and none lost across the file boundary.
    assert frame.groupby(["ts_event", "sequence"]).size().max() == 1
    stamps = set(pd.to_datetime(frame["ts_event"]).dt.strftime("%Y-%m-%dT%H:%M:%S"))
    assert "2024-01-31T23:00:00" in stamps  # evening portion (January file)
    assert "2024-02-01T14:00:00" in stamps  # day portion (February file)


# ---------------------------------------------------------------------------
# Event keys, neutral sides, crossed rows, per-file order gate
# ---------------------------------------------------------------------------

def test_duplicate_event_keys_preserved(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 10351),
            ("2024-01-04", "ZNH6", 60, 3),
        ]
    )
    plan = _plan(volume, sessions)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-04T14:00:00.000000000", 5),
            _row("2024-01-04T14:00:00.000000000", 5),  # equal (ts, seq): valid
            _row("2024-01-04T14:00:00.000000000", 6),
        ],
    )
    result = _build([path], plan, volume, tmp_path)
    record = result["days"]["2024-01-04"]
    assert record["n_rows"] == 3
    assert record["qc"]["checks"]["Q1"]["passed"] is True
    assert record["qc"]["checks"]["Q1"]["n_duplicate_key_pairs"] == 1
    assert pq.read_table(tmp_path / "store" / "2024-01-04.parquet").num_rows == 3
    assert record["trailing_median"] == 10351.0  # causal median from 2024-01-03


def test_side_n_rows_retained_as_unknown_classes(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    plan = _plan(_std_volume(), sessions)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-04T14:00:00.000000000", 1, action="A", side="N"),
            _row("2024-01-04T14:00:01.000000000", 2, action="C", side="N"),
            _row("2024-01-04T14:00:02.000000000", 3, action="M", side="N"),
            _row("2024-01-04T14:00:03.000000000", 4, action="T", side="N", size=7),
        ],
    )
    result = _build([path], plan, _std_volume(), tmp_path)
    record = result["days"]["2024-01-04"]
    assert record["n_rows"] == 4
    frame = pq.read_table(tmp_path / "store" / "2024-01-04.parquet").to_pandas()
    assert list(frame["event_class"]) == [
        "ADD_UNKNOWN",
        "CXL_UNKNOWN",
        "MOD_UNKNOWN",
        "AGG_UNKNOWN",
    ]
    assert frame["n_trades"].iloc[-1] == 1


def test_crossed_rows_dropped_before_derive_and_q2_threshold(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 10351),
            ("2024-01-04", "ZNH6", 60, 10351),
        ]
    )
    plan = _plan(volume, sessions)

    # 1 crossed row in 10351 is strictly < 0.01% -> Q2 passes; row still dropped.
    ok_rows = _session_rows("2024-01-04", crossed_indices=(5,))
    path_ok = _write_csv(tmp_path, "GLBX-202401.csv", ok_rows)
    result_ok = _build([path_ok], plan, volume, tmp_path)
    record_ok = result_ok["days"]["2024-01-04"]
    assert record_ok["n_rows"] == len(ok_rows)
    assert record_ok["n_crossed_dropped"] == 1
    assert record_ok["n_rows_after_crossed_drop"] == len(ok_rows) - 1
    assert record_ok["qc"]["checks"]["Q2"]["passed"] is True
    assert record_ok["qc_pass"] is True
    assert record_ok["research_eligible"] is True
    frame_ok = pq.read_table(tmp_path / "store" / "2024-01-04.parquet").to_pandas()
    assert len(frame_ok) == len(ok_rows) - 1
    assert (frame_ok["bid_px_00"] < frame_ok["ask_px_00"]).all()
    assert not (frame_ok["bid_px_00"] == 100.02).any()

    # 2 crossed rows in 10351 is >= 0.01% -> day excluded but still written.
    bad_rows = _session_rows("2024-01-04", crossed_indices=(5, 100))
    path_bad = _write_csv(tmp_path, "GLBX-202401-b.csv", bad_rows)
    result_bad = _build([path_bad], plan, volume, tmp_path, store="store_bad", analysis="analysis_bad")
    record_bad = result_bad["days"]["2024-01-04"]
    assert record_bad["qc"]["checks"]["Q2"]["passed"] is False
    assert record_bad["qc_pass"] is False
    assert record_bad["research_eligible"] is False
    assert record_bad["status"] == "built"
    assert record_bad["n_rows_after_crossed_drop"] == len(bad_rows) - 2
    frame_bad = pq.read_table(tmp_path / "store_bad" / "2024-01-04.parquet").to_pandas()
    assert len(frame_bad) == len(bad_rows) - 2


def test_per_file_source_order_gate_flags_day(tmp_path):
    sessions = ["2024-01-02", "2024-01-03"]
    plan = _plan(_volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)]), sessions)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-03T14:00:02.000000000", 3),  # out of source order
            _row("2024-01-03T14:00:00.000000000", 1),
            _row("2024-01-03T14:00:01.000000000", 2),
        ],
    )
    result = _build([path], plan, _volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)]), tmp_path)
    record = result["days"]["2024-01-03"]
    assert record["status"] == "built"
    # Canonical (ts, sequence) Q1 passes; Databento file order is diagnostic.
    assert record["qc"]["checks"]["Q1"]["passed"] is True
    assert record["qc"]["per_file_q1_failed_files"]
    # The store is still canonicalized by (ts_event, sequence).
    frame = pq.read_table(tmp_path / "store" / "2024-01-03.parquet").to_pandas()
    assert list(frame["sequence"]) == [1, 2, 3]


def test_q5_roll_window_uses_adjacent_quarter_dates(tmp_path):
    def run(sessions: Sequence[str]) -> dict:
        volume = _volume(
            [
                (sessions[0], "ZNH6", 80, 8),
                (sessions[0], "ZNM6", 20, 2),
                (sessions[1], "ZNH6", 20, 2),
                (sessions[1], "ZNM6", 80, 8),  # winner flips to ZNM6
                (sessions[2], "ZNM6", 60, 6),
            ]
        )
        plan = _plan(volume, sessions)
        path = _write_csv(
            tmp_path,
            f"GLBX-{sessions[2][:4]}{sessions[2][5:7]}.csv",
            [_row(f"{sessions[2]}T14:00:00.000000000", 1, symbol="ZNM6")],
        )
        return _build([path], plan, volume, tmp_path, store=f"store_{sessions[2]}", analysis=f"analysis_{sessions[2]}")["days"][sessions[2]]

    in_window = run(["2026-05-18", "2026-05-19", "2026-05-20"])
    assert in_window["plan"]["contract_changed"] is True
    assert in_window["qc"]["checks"]["Q5"]["passed"] is True
    assert in_window["qc"]["checks"]["Q5"]["in_documented_roll_window"] is True

    off_window = run(["2026-06-15", "2026-06-16", "2026-06-17"])
    assert off_window["plan"]["contract_changed"] is True
    assert off_window["qc"]["checks"]["Q5"]["passed"] is False
    assert off_window["qc"]["checks"]["Q5"]["in_documented_roll_window"] is False


# ---------------------------------------------------------------------------
# Causal trailing-20 median (Q4)
# ---------------------------------------------------------------------------

def test_trailing_median_is_causal(tmp_path):
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 10351),
            ("2024-01-04", "ZNH6", 60, 6),
            ("2024-01-05", "ZNH6", 50, 5),
        ]
    )
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    plan = _plan(volume, sessions)

    medians = trailing_event_count_medians(plan, volume)
    assert medians["2024-01-03"] is None  # warmup: 01-02 is ineligible
    assert medians["2024-01-04"] == 10351.0
    assert medians["2024-01-05"] == pytest.approx((10351 + 6) / 2)

    # Future-date mutation never changes earlier medians.
    future = volume.copy()
    future.loc[future["trading_date"] == "2024-01-05", "event_count"] = 999_999
    medians_future = trailing_event_count_medians(plan, future)
    assert medians_future["2024-01-04"] == 10351.0
    assert medians_future["2024-01-05"] == pytest.approx((10351 + 6) / 2)

    # A date's own count never enters its own median.
    own = volume.copy()
    own.loc[own["trading_date"] == "2024-01-04", "event_count"] = 777
    medians_own = trailing_event_count_medians(plan, own)
    assert medians_own["2024-01-04"] == 10351.0
    assert medians_own["2024-01-05"] == pytest.approx((10351 + 777) / 2)


# ---------------------------------------------------------------------------
# Checkpoints: resume, hash invalidation, plan-change fail-closed
# ---------------------------------------------------------------------------

def test_resume_skips_matching_days_and_hash_change_rebuilds(tmp_path):
    jan = [
        _row("2024-01-03T14:00:00.000000000", 1),       # -> 2024-01-03
        _row("2024-01-30T23:00:00.000000000", 2),       # -> 2024-01-31
        _row("2024-01-31T23:00:00.000000000", 3),       # -> 2024-02-01 evening
        _row("2024-01-31T23:00:01.000000000", 4, action="T", side="A"),  # -> 2024-02-01 evening
    ]
    feb = [
        _row("2024-02-01T14:00:00.000000000", 1),  # -> 2024-02-01 day
        _row("2024-02-01T23:00:00.000000000", 2),  # -> 2024-02-02
    ]
    jan_path = _write_csv(tmp_path, "GLBX-202401.csv", jan)
    feb_path = _write_csv(tmp_path, "GLBX-202402.csv", feb)
    sessions = ["2024-01-02", "2024-01-03", "2024-01-31", "2024-02-01", "2024-02-02"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 8),
            ("2024-01-31", "ZNH6", 90, 9),
            ("2024-02-01", "ZNH6", 70, 7),
            ("2024-02-02", "ZNH6", 60, 6),
        ]
    )
    plan = _plan(volume, sessions)

    first = _build([feb_path, jan_path], plan, volume, tmp_path)
    assert first["n_days_built"] == 4
    assert first["n_days_skipped"] == 1
    first_hashes = {
        trading_date: record["output_sha256"]
        for trading_date, record in first["days"].items()
        if record["status"] == "built"
    }

    second = _build([feb_path, jan_path], plan, volume, tmp_path)
    assert second["n_days_resumed"] == 4
    assert second["n_days_built"] == 0
    for trading_date in first_hashes:
        assert second["days"][trading_date]["status"] == "resumed"
        assert second["days"][trading_date]["output_sha256"] == first_hashes[trading_date]

    # Mutate only the January file: every day sourced from it must rebuild;
    # the February-only day must still resume.
    jan_mutated = [
        _row("2024-01-03T14:00:00.000000000", 1),
        _row("2024-01-30T23:00:00.000000000", 2, size=9),   # -> 2024-01-31 changes
        _row("2024-01-31T23:00:00.000000000", 3, size=8),   # -> 2024-02-01 changes
        _row("2024-01-31T23:00:01.000000000", 4, action="T", side="A"),
    ]
    _write_csv(tmp_path, "GLBX-202401.csv", jan_mutated)
    third = _build([feb_path, jan_path], plan, volume, tmp_path)
    assert third["days"]["2024-02-02"]["status"] == "resumed"
    assert third["days"]["2024-02-02"]["output_sha256"] == first_hashes["2024-02-02"]
    for trading_date in ("2024-01-03", "2024-01-31", "2024-02-01"):
        assert third["days"][trading_date]["status"] == "rebuilt"
    assert third["days"]["2024-01-03"]["output_sha256"] == first_hashes["2024-01-03"]
    assert third["days"]["2024-01-31"]["output_sha256"] != first_hashes["2024-01-31"]
    assert third["days"]["2024-02-01"]["output_sha256"] != first_hashes["2024-02-01"]


def test_resume_fails_closed_on_plan_change(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    volume = _volume(
        [
            ("2024-01-02", "ZNH6", 100, 10),
            ("2024-01-03", "ZNH6", 80, 8),
            ("2024-01-04", "ZNH6", 60, 6),
        ]
    )
    plan = _plan(volume, sessions)
    path = _write_csv(tmp_path, "GLBX-202401.csv", [_row("2024-01-04T14:00:00.000000000", 1)])
    _build([path], plan, volume, tmp_path)

    mutated = plan.copy()
    mutated.loc[mutated["trading_date"] == "2024-01-04", "selected_contract"] = "ZNM6"
    with pytest.raises(X0BuildError, match="cannot resume"):
        _build([path], mutated, volume, tmp_path)


def test_resume_rebuilds_when_checkpoint_output_path_is_unsafe(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    plan = _plan(_std_volume(), sessions)
    path = _write_csv(
        tmp_path, "GLBX-202401.csv", [_row("2024-01-04T14:00:00.000000000", 1)]
    )
    first = _build([path], plan, _std_volume(), tmp_path)
    assert first["days"]["2024-01-04"]["status"] == "built"
    first_hash = first["days"]["2024-01-04"]["output_sha256"]

    checkpoint_path = tmp_path / "analysis" / CHECKPOINT_FILENAME
    expected_path = str((tmp_path / "store" / "2024-01-04.parquet").resolve())

    # A recorded path outside the store root must not be trusted.
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["days"]["2024-01-04"]["output_path"] = str(
        tmp_path / "outside" / "2024-01-04.parquet"
    )
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    second = _build([path], plan, _std_volume(), tmp_path)
    assert second["days"]["2024-01-04"]["status"] == "rebuilt"
    assert second["days"]["2024-01-04"]["output_path"] == expected_path
    assert second["days"]["2024-01-04"]["output_sha256"] == first_hash

    # A recorded path inside the store root but with the wrong name also
    # must not be trusted.
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["days"]["2024-01-04"]["output_path"] = str(
        tmp_path / "store" / "different.parquet"
    )
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    third = _build([path], plan, _std_volume(), tmp_path)
    assert third["days"]["2024-01-04"]["status"] == "rebuilt"
    assert third["days"]["2024-01-04"]["output_path"] == expected_path
    assert third["days"]["2024-01-04"]["output_sha256"] == first_hash


def test_builder_sha256_recorded_and_mismatch_rebuilds(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    plan = _plan(_std_volume(), sessions)
    path = _write_csv(
        tmp_path, "GLBX-202401.csv", [_row("2024-01-04T14:00:00.000000000", 1)]
    )
    first = _build([path], plan, _std_volume(), tmp_path)
    assert first["days"]["2024-01-04"]["builder_sha256"] == BUILDER_SHA256

    checkpoint_path = tmp_path / "analysis" / CHECKPOINT_FILENAME
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["builder_sha256"] == BUILDER_SHA256
    assert checkpoint["days"]["2024-01-04"]["builder_sha256"] == BUILDER_SHA256

    # A stale builder hash must invalidate day resumes and rebuild, not fail
    # the whole checkpoint (frozen plan/median/source mismatches still do).
    checkpoint["builder_sha256"] = "stale-builder"
    checkpoint["days"]["2024-01-04"]["builder_sha256"] = "stale-builder"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    second = _build([path], plan, _std_volume(), tmp_path)
    assert second["days"]["2024-01-04"]["status"] == "rebuilt"
    assert second["days"]["2024-01-04"]["builder_sha256"] == BUILDER_SHA256
    refreshed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert refreshed["builder_sha256"] == BUILDER_SHA256
    assert refreshed["days"]["2024-01-04"]["builder_sha256"] == BUILDER_SHA256


# ---------------------------------------------------------------------------
# Dry run + containment
# ---------------------------------------------------------------------------

def test_dry_run_scans_and_qc_without_writes(tmp_path):
    sessions = ["2024-01-02", "2024-01-03"]
    plan = _plan(_volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)]), sessions)
    path = _write_csv(tmp_path, "GLBX-202401.csv", [_row("2024-01-03T14:00:00.000000000", 1)])

    result = _build(
        [path],
        plan,
        _volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)]),
        tmp_path,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["checkpoint_path"] is None
    assert result["source_hashes"] == {}
    record = result["days"]["2024-01-03"]
    assert record["status"] == "built"
    assert record["output_sha256"] is None
    assert record["output_bytes"] is None
    assert record["estimated_uncompressed_bytes"] > 0
    assert result["estimated_uncompressed_bytes"] == record["estimated_uncompressed_bytes"]
    assert result["output_bytes"] is None
    assert record["qc"]["checks"]["Q1"]["passed"] is True
    assert not (tmp_path / "store").exists()
    assert not (tmp_path / "analysis").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob(CHECKPOINT_FILENAME))


def test_containment_enforced(tmp_path):
    rows = [_row("2024-01-03T14:00:00.000000000", 1)]
    path = _write_csv(tmp_path, "GLBX-202401.csv", rows)
    sessions = ["2024-01-02", "2024-01-03"]
    volume = _volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)])
    plan = _plan(volume, sessions)

    with pytest.raises(X0BuildError, match="escapes approved analysis root"):
        build_selected_row_store(
            [path],
            plan,
            volume,
            store_root=tmp_path / "store",
            analysis_root=tmp_path / "analysis",
            checkpoint_name="../escape.json",
        )
    with pytest.raises(X0BuildError, match="outside approved source root"):
        _build([path], plan, volume, tmp_path, source_root=tmp_path / "other")


# ---------------------------------------------------------------------------
# Determinism, checkpoint contents, scope guardrails
# ---------------------------------------------------------------------------

def test_output_deterministic_and_chunk_invariant(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    plan = _plan(_std_volume(), sessions)
    path = _write_csv(
        tmp_path,
        "GLBX-202401.csv",
        [
            _row("2024-01-04T14:00:00.000000000", 1),
            _row("2024-01-04T14:00:01.000000000", 2, action="T", side="B"),
            _row("2024-01-04T14:00:02.000000000", 3),
            _row("2024-01-04T14:00:03.000000000", 4, action="C", side="A"),
            _row("2024-01-04T14:00:04.000000000", 5),
        ],
    )

    default = _build([path], plan, _std_volume(), tmp_path, store="s1", analysis="a1")
    chunked = _build(
        [path], plan, _std_volume(), tmp_path, store="s2", analysis="a2", batch_size=2
    )
    again = _build([path], plan, _std_volume(), tmp_path, store="s3", analysis="a3")

    for trading_date in default["emission_order"]:
        hashes = {
            tag: result["days"][trading_date]["output_sha256"]
            for tag, result in (("default", default), ("chunked", chunked), ("again", again))
        }
        assert hashes["default"] == hashes["chunked"] == hashes["again"]


def test_checkpoint_records_hashes_counts_qc_status(tmp_path):
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    plan = _plan(_std_volume(), sessions)
    path = _write_csv(tmp_path, "GLBX-202401.csv", [_row("2024-01-04T14:00:00.000000000", 1)])
    result = _build([path], plan, _std_volume(), tmp_path)

    checkpoint = json.loads(
        (tmp_path / "analysis" / CHECKPOINT_FILENAME).read_text(encoding="utf-8")
    )
    assert checkpoint["holdout_opened"] is False
    assert checkpoint["source_hashes"] == result["source_hashes"]
    record = checkpoint["days"]["2024-01-04"]
    assert record["status"] == "built"
    assert record["selected_contract"] == "ZNH6"
    assert record["source_file_hashes"]
    assert record["output_sha256"]
    assert (tmp_path / "store" / "2024-01-04.parquet").is_file()
    assert record["n_rows"] == 1
    assert record["qc"]["qc_pass"] is False  # partial session -> Q3 fail
    assert record["qc"]["checks"]["Q3"]["passed"] is False
    assert record["research_eligible"] is False


def test_no_episodes_features_or_holdout_statistics(tmp_path):
    from scripts.move_exhaustion_v2.data import x0_build

    tree = ast.parse(inspect.getsource(x0_build))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(name.name.split(".")[0] for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert not ({"episodes", "features", "labels"} & imported_roots), (
        "x0_build must never import episode/feature/label computation"
    )

    sessions = ["2024-01-02", "2024-01-03"]
    plan = _plan(_volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)]), sessions)
    path = _write_csv(tmp_path, "GLBX-202401.csv", [_row("2024-01-03T14:00:00.000000000", 1)])
    result = _build([path], plan, _volume([("2024-01-02", "ZNH6", 100, 10), ("2024-01-03", "ZNH6", 80, 8)]), tmp_path)
    assert result["holdout_opened"] is False
    for record in result["days"].values():
        assert not any(
            key.startswith(("episode", "feature", "label")) for key in record
        )
