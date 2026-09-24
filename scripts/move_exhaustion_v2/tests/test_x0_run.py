"""Synthetic tests for the X0 volume/pilot runner helpers.

All inputs are temporary files under ``tmp_path``.  No live archive, D: path,
network, or holdout access.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from scripts.move_exhaustion_v2.data.x0_build import month_key
from scripts.move_exhaustion_v2.data.x0_run import (
    DEFAULT_PILOT_MONTH,
    X0RunError,
    assert_not_d_drive,
    build_pilot_disk_record,
    list_monthly_csv_sources,
    load_batch_bounds,
    newest_first_sources,
    parse_month,
    pilot_month_sources,
    previous_month_key,
    scan_or_resume_monthly,
    volume_dir,
)
from scripts.move_exhaustion_v2.data.x0_volume import VOLUME_COLUMNS


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


def _touch_csv(root: Path, name: str) -> Path:
    path = root / name
    path.write_bytes(b"")
    return path


def _write_tiny_csv(path: Path) -> Path:
    rows = [
        {
            "ts_event": "2026-02-03T15:00:00.000000000",
            "sequence": 1,
            "symbol": "ZNH6",
            "action": "T",
            "side": "B",
            "price": 100.0,
            "size": 4,
            "bid_px_00": 100.0,
            "ask_px_00": 100.015625,
            "bid_sz_00": 5,
            "ask_sz_00": 5,
        }
    ]
    pd.DataFrame(rows, columns=RAW_COLUMNS).to_csv(path, index=False)
    return path


def test_parse_month_and_previous_key():
    assert parse_month("2026-03") == (2026, 3)
    assert previous_month_key((2026, 3)) == (2026, 2)
    assert previous_month_key((2026, 1)) == (2025, 12)
    with pytest.raises(X0RunError, match="YYYY-MM"):
        parse_month("202603")
    with pytest.raises(X0RunError, match="YYYY-MM"):
        parse_month("2026-13")


def test_default_pilot_month_is_last_full_authorized_month():
    assert DEFAULT_PILOT_MONTH == (2026, 3)
    start, end = load_batch_bounds()
    assert start == date(2022, 1, 1)
    assert end == date(2026, 4, 1)


def test_month_key_reads_databento_monthly_and_april_stub_names(tmp_path):
    monthly = _touch_csv(tmp_path, "glbx-mdp3-20260301-20260331.mbp-1.csv.zst")
    stub = _touch_csv(tmp_path, "glbx-mdp3-20260401.mbp-1.csv.zst")
    assert month_key(monthly) == (2026, 3)
    assert month_key(stub) == (2026, 4)


def test_list_monthly_csv_sources_skips_json_and_sorts_oldest_first(tmp_path):
    archive = tmp_path / "job"
    archive.mkdir()
    _touch_csv(archive, "glbx-mdp3-20260301-20260331.mbp-1.csv.zst")
    _touch_csv(archive, "glbx-mdp3-20260201-20260228.mbp-1.csv.zst")
    (archive / "metadata.json").write_text("{}", encoding="utf-8")
    names = [path.name for path in list_monthly_csv_sources(archive)]
    assert names == [
        "glbx-mdp3-20260201-20260228.mbp-1.csv.zst",
        "glbx-mdp3-20260301-20260331.mbp-1.csv.zst",
    ]


def test_pilot_month_sources_are_newest_first_and_include_previous(tmp_path):
    archive = tmp_path / "job"
    archive.mkdir()
    feb = _touch_csv(archive, "glbx-mdp3-20260201-20260228.mbp-1.csv.zst")
    mar = _touch_csv(archive, "glbx-mdp3-20260301-20260331.mbp-1.csv.zst")
    _touch_csv(archive, "glbx-mdp3-20260401.mbp-1.csv.zst")
    sources = list_monthly_csv_sources(archive)
    pair = pilot_month_sources(sources, 2026, 3)
    assert pair == [mar.resolve(), feb.resolve()]


def test_newest_first_sources_reverses_oldest_first_listing(tmp_path):
    archive = tmp_path / "job"
    archive.mkdir()
    feb = _touch_csv(archive, "glbx-mdp3-20260201-20260228.mbp-1.csv.zst")
    mar = _touch_csv(archive, "glbx-mdp3-20260301-20260331.mbp-1.csv.zst")
    jan = _touch_csv(archive, "glbx-mdp3-20260101-20260131.mbp-1.csv.zst")
    sources = list_monthly_csv_sources(archive)
    assert newest_first_sources(sources) == [
        mar.resolve(),
        feb.resolve(),
        jan.resolve(),
    ]
    with pytest.raises(X0RunError, match="no monthly CSV sources"):
        newest_first_sources([])


def test_pilot_month_sources_fail_closed_without_previous_month(tmp_path):
    archive = tmp_path / "job"
    archive.mkdir()
    _touch_csv(archive, "glbx-mdp3-20220101-20220131.mbp-1.csv.zst")
    with pytest.raises(X0RunError, match="previous month"):
        pilot_month_sources(list_monthly_csv_sources(archive), 2022, 1)


def test_assert_not_d_drive_rejects_d():
    with pytest.raises(X0RunError, match="must not be on D:"):
        assert_not_d_drive(Path(r"D:\FRDBs\forbidden"), label="store root")


def test_scan_or_resume_monthly_writes_then_skips(tmp_path):
    source = _write_tiny_csv(tmp_path / "glbx-mdp3-20260201-20260228.mbp-1.csv")
    volume_root = volume_dir(tmp_path / "analysis")
    index = {
        source.name: {
            "filename": source.name,
            "size": source.stat().st_size,
            "sha256": "abc",
            "status": "verified",
        }
    }
    first, status_first = scan_or_resume_monthly(source, volume_root, file_index=index)
    assert status_first == "scanned"
    assert list(first.columns) == list(VOLUME_COLUMNS)
    assert len(first) == 1
    second, status_second = scan_or_resume_monthly(source, volume_root, file_index=index)
    assert status_second == "resumed"
    pd.testing.assert_frame_equal(first, second)


def test_build_pilot_disk_record_counts_only_target_month():
    build = {
        "days": {
            "2026-02-27": {"status": "built", "output_bytes": 9_000},
            "2026-03-02": {"status": "built", "output_bytes": 100},
            "2026-03-03": {"status": "built", "output_bytes": 300},
            "2026-03-04": {"status": "skipped", "output_bytes": None},
        }
    }
    record = build_pilot_disk_record(
        build,
        year=2026,
        month=3,
        expected_total_sessions=10,
        free_bytes=1_000_000,
    )
    assert record["actual_days"] == 2
    assert record["actual_parquet_bytes"] == 400
    assert record["projected_full_store_bytes"] == 2000.0
    assert record["holdout_opened"] is False
    assert "2026-02-27" not in record["day_output_bytes"]


def test_build_pilot_disk_record_fails_when_month_empty():
    with pytest.raises(X0RunError, match="no parquet days"):
        build_pilot_disk_record(
            {"days": {"2026-02-27": {"status": "built", "output_bytes": 9}}},
            year=2026,
            month=3,
            expected_total_sessions=10,
            free_bytes=1_000_000,
        )


def test_evaluate_disk_budget_uses_pilot_record():
    from scripts.move_exhaustion_v2.data.x0_finalize import evaluate_disk_budget

    record = build_pilot_disk_record(
        {
            "days": {
                "2026-03-02": {"status": "built", "output_bytes": 100},
                "2026-03-03": {"status": "built", "output_bytes": 100},
            }
        },
        year=2026,
        month=3,
        expected_total_sessions=10,
        free_bytes=10_000,
    )
    passed = evaluate_disk_budget(record, expected_total_sessions=10)
    assert passed["passed"] is True
    failed = evaluate_disk_budget(
        {**record, "projected_full_store_bytes": 9_500, "free_bytes": 10_000},
        expected_total_sessions=10,
    )
    assert failed["passed"] is False
