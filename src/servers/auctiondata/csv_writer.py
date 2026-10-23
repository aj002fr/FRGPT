"""CSV merge helpers for Treasury auction rows."""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .schema import (
    API_TO_CSV_FIELD_MAP,
    AUCTION_DATA_DIR,
    formatter_for_api_field,
)


REFERENCE_HEADER_FILE = AUCTION_DATA_DIR / "10y_note_raw.csv"


@dataclass(frozen=True)
class MergeResult:
    csv_path: Path
    added: bool
    updated: bool
    cusip: str
    row: dict[str, str]


def _read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV has no header row: {path}") from exc


def canonical_header() -> list[str]:
    if not REFERENCE_HEADER_FILE.exists():
        raise FileNotFoundError(f"Missing canonical auction CSV header: {REFERENCE_HEADER_FILE}")
    return _read_header(REFERENCE_HEADER_FILE)


def bootstrap_csv_if_missing(csv_path: Path) -> None:
    """Create a raw auction CSV with the canonical Treasury export header."""

    if csv_path.exists():
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(canonical_header())


def api_record_to_csv_row(record: dict, fieldnames: list[str]) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    for api_field, csv_field in API_TO_CSV_FIELD_MAP.items():
        if csv_field not in row:
            continue
        formatter = formatter_for_api_field(api_field)
        row[csv_field] = formatter(record.get(api_field))
    return row


def _auction_sort_key(row: dict[str, str]) -> tuple[datetime, str]:
    raw_date = row.get("Auction Date", "")
    try:
        auction_dt = datetime.strptime(raw_date, "%m/%d/%Y")
    except ValueError:
        auction_dt = datetime.min
    return auction_dt, row.get("CUSIP", "")


def _write_rows_atomic(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{csv_path.name}.", suffix=".tmp", dir=str(csv_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        try:
            os.replace(tmp_path, csv_path)
        except PermissionError:
            # Some SMB shares reject replace-over-existing even when normal writes are allowed.
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def read_existing_cusips(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {str(row.get("CUSIP", "")).strip() for row in reader if str(row.get("CUSIP", "")).strip()}


def _should_update_existing(existing_row: dict[str, str], new_row: dict[str, str]) -> bool:
    for field, new_value in new_row.items():
        old_value = str(existing_row.get(field, "")).strip()
        if str(new_value).strip() and old_value != str(new_value).strip():
            return True
    return False


def merge_auction_into_csv(csv_path: Path, auction_record: dict) -> MergeResult:
    """Append an API auction row to a raw CSV, keyed by CUSIP."""

    bootstrap_csv_if_missing(csv_path)
    fieldnames = _read_header(csv_path)
    new_row = api_record_to_csv_row(auction_record, fieldnames)
    cusip = new_row.get("CUSIP", "").strip()
    if not cusip:
        raise ValueError("Auction row is missing CUSIP")

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [dict(row) for row in reader]

    updated = False
    for idx, row in enumerate(rows):
        if str(row.get("CUSIP", "")).strip() != cusip:
            continue
        if _should_update_existing(row, new_row):
            merged_row = dict(row)
            for field, value in new_row.items():
                if str(value).strip():
                    merged_row[field] = value
            rows[idx] = merged_row
            rows.sort(key=_auction_sort_key, reverse=True)
            _write_rows_atomic(csv_path, fieldnames, rows)
            return MergeResult(csv_path=csv_path, added=False, updated=True, cusip=cusip, row=merged_row)
        return MergeResult(csv_path=csv_path, added=False, updated=False, cusip=cusip, row=new_row)

    rows.append(new_row)
    rows.sort(key=_auction_sort_key, reverse=True)
    _write_rows_atomic(csv_path, fieldnames, rows)
    return MergeResult(csv_path=csv_path, added=True, updated=updated, cusip=cusip, row=new_row)

