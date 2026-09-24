from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.autoresearch.fixed_env.manifests import (
    event_payload,
    freeze_event_manifest,
    freeze_split_manifest,
    split_payload,
)


def test_split_payload_is_outcome_blind_and_confirmation_is_empty() -> None:
    publication = {
        "version": "vTEST",
        "matrix_sha256": "a" * 64,
        "research_dates": [
            "2024-07-01", "2025-08-31", "2025-09-01",
            "2026-01-23", "2026-01-26", "2026-07-21",
        ],
        "excluded_dates": [{"trading_date": "2026-07-17"}],
    }
    result = split_payload(
        publication, preregistered_at="2026-07-22T13:00:00Z"
    )
    assert result["discovery_dates"] == ["2024-07-01", "2025-08-31"]
    assert result["validation_dates"] == ["2025-09-01", "2026-01-23"]
    assert result["historical_quarantine_dates"] == ["2026-01-26", "2026-07-21"]
    assert result["prospective_confirmation"]["dates"] == []
    assert result["prospective_confirmation"]["outcomes_must_remain_unloaded"]


def test_split_manifest_rejects_unsorted_or_non_utc_input(tmp_path: Path) -> None:
    publication = tmp_path / "publication.json"
    publication.write_text(
        json.dumps({"research_dates": ["2025-01-02", "2025-01-01"]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique and sorted"):
        freeze_split_manifest(
            publication, tmp_path / "split.json",
            preregistered_at="2026-07-22T13:00:00Z",
        )
    with pytest.raises(ValueError, match="UTC"):
        split_payload(
            {"research_dates": ["2025-01-01"]},
            preregistered_at="2026-07-22T13:00:00",
        )


def test_event_payload_deduplicates_and_converts_et_to_utc() -> None:
    rows = [
        {"event_name": "CPI", "event_date": "2025-01-15T08:30:00"},
        {"event_name": "CPI", "event_date": "2025-01-15T08:30:00"},
        {"event_name": "FOMC", "event_date": "2025-01-29T14:00:00"},
    ]
    result = event_payload(
        rows, source_sha256="b" * 64,
        start_date="2025-01-01", end_date="2025-02-01",
    )
    assert [row["event_ts_utc"] for row in result["events"]] == [
        "2025-01-15T13:30:00Z", "2025-01-29T19:00:00Z",
    ]
    assert result["blackout_minutes"] == {"before": 10, "after": 30}


def test_freeze_event_manifest_reads_database_readonly(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE economic_events("
            "event_name TEXT,country TEXT,importance TEXT,event_date TEXT)"
        )
        connection.executemany(
            "INSERT INTO economic_events VALUES (?,?,?,?)",
            [
                ("CPI", "United States", "high", "2025-01-15T08:30:00"),
                ("Minor", "United States", "low", "2025-01-16T10:00:00"),
                ("Foreign", "Canada", "high", "2025-01-17T08:30:00"),
            ],
        )
    destination = tmp_path / "event_manifest.json"
    digest = freeze_event_manifest(
        database, destination,
        start_date="2025-01-01", end_date="2025-02-01",
    )
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert len(digest) == 64
    assert [row["event_name"] for row in payload["events"]] == ["CPI"]
    with pytest.raises(FileExistsError):
        freeze_event_manifest(
            database, destination,
            start_date="2025-01-01", end_date="2025-03-01",
        )
