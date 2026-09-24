"""Append-only exploratory search journal and multiplicity counters."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.autoresearch.fixed_env.preregister import canonical_bytes, digest_object

from .lab_readers import PROJECT_ROOT

LAB_ROOT = PROJECT_ROOT / "workspace" / "analysis" / "autoresearch_lab"
JOURNAL_ROOT = LAB_ROOT / "journal"
JOURNAL_PATH = JOURNAL_ROOT / "searches.jsonl"
SESSION_ID = uuid.uuid4().hex
_LOCK = threading.Lock()
_SESSION_COUNT = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _journal_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    source = str(clean.pop("source", "") or "")
    clean["source_sha256"] = digest_object({"source": source}) if source else None
    return clean


def append_search(
    *,
    job_id: str,
    payload: Mapping[str, Any],
    status: str,
    result: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    global _SESSION_COUNT
    record = {
        "schema_version": 1,
        "recorded_at": _utc_now(),
        "session_id": SESSION_ID,
        "session_ordinal": _SESSION_COUNT + 1,
        "job_id": job_id,
        "status": status,
        "request": _journal_payload(payload),
        "request_sha256": digest_object(_journal_payload(payload)),
        "result_sha256": digest_object(result) if result is not None else None,
        "search_digest": result.get("search_digest") if result else None,
        "error": error,
    }
    JOURNAL_ROOT.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with JOURNAL_PATH.open("ab") as handle:
            handle.write(canonical_bytes(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        _SESSION_COUNT += 1
    return record


def journal_rows() -> list[dict[str, Any]]:
    if not JOURNAL_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with JOURNAL_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("journal row must be a JSON object")
                rows.append(value)
    return rows


def search_counts() -> dict[str, Any]:
    rows = journal_rows()
    completed = [row for row in rows if row.get("status") == "completed"]
    distinct = {
        (
            row.get("request_sha256"),
            row.get("search_digest"),
        )
        for row in completed
    }
    return {
        "session_id": SESSION_ID,
        "session_searches": _SESSION_COUNT,
        "historical_attempts": len(rows),
        "historical_completed": len(completed),
        "distinct_completed_searches": len(distinct),
        "journal": str(JOURNAL_PATH),
        "multiplicity_warning": (
            "Every completed discovery search increases researcher degrees of freedom; "
            "no displayed p-value is confirmatory."
        ),
    }


def journal_excerpt(search_digest: str) -> list[dict[str, Any]]:
    return [
        row
        for row in journal_rows()
        if row.get("search_digest") == search_digest
    ]
