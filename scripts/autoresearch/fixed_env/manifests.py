"""Outcome-blind split and event manifests for preregistration."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .data_access import open_sqlite_readonly
from .preregister import digest_file, freeze_json

EASTERN = ZoneInfo("America/New_York")
DISCOVERY = (date(2024, 7, 1), date(2025, 8, 31))
VALIDATION = (date(2025, 9, 1), date(2026, 1, 23))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("preregistered_at must be UTC")
    return parsed.astimezone(timezone.utc)


def _date_list(values: Sequence[str]) -> list[date]:
    parsed = [date.fromisoformat(str(value)) for value in values]
    if parsed != sorted(set(parsed)):
        raise ValueError("research_dates must be unique and sorted")
    return parsed


def split_payload(
    publication: Mapping[str, Any], *, preregistered_at: str
) -> dict[str, Any]:
    """Classify eligible dates without inspecting any experiment outcome."""
    instant = _parse_utc(preregistered_at)
    research_dates = _date_list(publication.get("research_dates", ()))
    if not research_dates:
        raise ValueError("publication contains no research_dates")
    preregistered_et_date = instant.astimezone(EASTERN).date()
    discovery = [d.isoformat() for d in research_dates if DISCOVERY[0] <= d <= DISCOVERY[1]]
    validation = [d.isoformat() for d in research_dates if VALIDATION[0] <= d <= VALIDATION[1]]
    quarantine = [
        d.isoformat() for d in research_dates
        if VALIDATION[1] < d <= preregistered_et_date
    ]
    return {
        "schema_version": 1,
        "publication_version": publication.get("version"),
        "publication_matrix_sha256": publication.get("matrix_sha256"),
        "preregistered_at": preregistered_at,
        "discovery_dates": discovery,
        "validation_dates": validation,
        "historical_quarantine_dates": quarantine,
        "prospective_confirmation": {
            "rule": "first research-eligible complete session strictly after preregistration",
            "strictly_after_utc": preregistered_at,
            "dates": [],
            "outcomes_must_remain_unloaded": True,
        },
        "excluded_dates": publication.get("excluded_dates", []),
    }


def freeze_split_manifest(
    publication_manifest: str | Path,
    destination: str | Path,
    *,
    preregistered_at: str,
) -> str:
    source = Path(publication_manifest)
    publication = json.loads(source.read_text(encoding="utf-8"))
    payload = split_payload(publication, preregistered_at=preregistered_at)
    payload["publication_manifest_sha256"] = digest_file(source)
    return freeze_json(destination, payload)


def event_payload(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_sha256: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    events: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        local = datetime.fromisoformat(str(row["event_date"]))
        if local.tzinfo is None:
            local = local.replace(tzinfo=EASTERN)
        utc = local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        key = (utc, str(row["event_name"]))
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "event_ts_utc": utc,
            "event_name": str(row["event_name"]),
            "importance": "high",
            "country": "United States",
        })
    events.sort(key=lambda item: (item["event_ts_utc"], item["event_name"]))
    return {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "source_table": "economic_events",
        "filters": {
            "country": "United States",
            "importance": "high",
            "start_date": start_date,
            "end_date": end_date,
        },
        "blackout_minutes": {"before": 10, "after": 30},
        "events": events,
    }


def freeze_event_manifest(
    database: str | Path,
    destination: str | Path,
    *,
    start_date: str,
    end_date: str,
) -> str:
    db = Path(database).resolve(strict=True)
    with open_sqlite_readonly(db, [db.parent]) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT event_name,event_date FROM economic_events "
            "WHERE country='United States' AND lower(importance)='high' "
            "AND event_date>=? AND event_date<? ORDER BY event_date,event_name",
            (start_date, end_date),
        ).fetchall()
    payload = event_payload(
        [dict(row) for row in rows],
        source_sha256=digest_file(db),
        start_date=start_date,
        end_date=end_date,
    )
    return freeze_json(destination, payload)
