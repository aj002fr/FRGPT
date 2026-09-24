"""Selected-row streaming + checkpointed daily-store build slice (X0).

This is the *selected-row streaming and per-day store build* slice of the X0
data build (PROGRAM.md §1.2, §2; ``config/frozen/data_store.yaml``
``builder.order``/``builder.checkpointed``/``builder.front_contract_selection``).
The volume prepass + causal day plan live in :mod:`.x0_volume`; this module
consumes the resulting eligible plan and turns raw monthly MBP-1 CSV packs
into one derived Parquet per trading date under an approved store root.

Pipeline per monthly CSV (``.csv`` or ``.csv.zst``), newest-first:

1. One DuckDB pass per file: ``read_csv_auto`` with explicit column types,
   a deterministic zero-based ``source_row_number`` assigned in the scan
   before the plan join, ET trading-date assignment (18:00 ET boundary, same
   convention as :func:`x0_volume.scan_monthly_volume_csv`), and a **left join
   against the eligible causal day plan** so only rows of the date's selected
   front contract survive (raw-symbol aliases such as ``TYH6 COMDTY`` resolve
   to the canonical ``ZNH6`` contract).      The query streams Arrow record
    batches from a chunked zstd/CSV reader; a month is never materialized.
2. Source order is explicit: ``source_row_number`` is assigned in CSV
   insertion order.  DuckDB does **not** full-sort the month (that OOMs on
   real MBP-1).  :func:`stream_selected_days` buffers at most one in-flight
   date, optionally spills completed date parts, then yields days newest-first
   with ``source_row_number`` ascending *within* each date.  File provenance
   is carried through the boundary merge, and both the per-file and merged Q1
   gates evaluate their contribution in true original archive order.
3. A trading date can span two adjacent monthly files (its session starts
   the prior day 18:00 ET).  The builder holds at most one current day plus
   the single cross-file boundary carry (each file's earliest trading date)
   until the next-older file has been scanned, then merges the two
   contributions with no duplicate or lost rows and emits days in descending
   order.
4. For every complete day, Q1-Q6 run on the canonical raw rows
   (``(ts_event, sequence)`` sorted) with a **causal trailing-20 earlier-date
   event-count median** (from the volume prepass, never same-day or future
   information) and **adjacent-quarter documented roll dates** (Q5).  Crossed
   rows are dropped before :func:`derive_day_frame`, which receives
   ``trading_date`` and the per-day ``source_order`` so both survive into
   every output row.  QC and plan eligibility are combined: a day is
   research-eligible only when both pass.
5. Outputs are atomic: one Parquet per trading date under the supplied store
   root, and JSON checkpoints under the supplied analysis root.  Checkpoints
   record builder, source and output SHA-256s, the selected contract, counts,
   QC and status; a day is resumed only when its recorded builder, source
   hashes and output hash still match.  Both roots enforce containment (``artifacts.ensure_under``).

Scope guardrails (binding):

* Never computes episodes, features, labels, or holdout statistics.  X0 may
  build and day-level-QC holdout-period days, but nothing beyond that; every
  checkpoint declares ``holdout_opened: false``.
* Dry runs scan and QC but write no Parquet and no checkpoint.
* All APIs are typed, pure-or-streaming and deterministic; no module-level
  mutable state is kept.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

from ..artifacts import atomic_write_json, ensure_under, sha256_file
from .front_contract import DEFAULT_REFERENCE_DATE, Contract, parse_contract
from .mbp1_store_builder import derive_day_frame
from .qc import check_timestamp_monotonicity, run_daily_qc
from .x0_volume import (
    PLAN_COLUMNS,
    VOLUME_COLUMNS,
    VolumePrepassError,
    databento_price_sql,
    databento_timestamp_sql,
    documented_roll_window_dates,
)

PROGRAM_ID = "move_exhaustion_v2"
SLICE_ID = "x0_selected_row_store_build"
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_FILENAME = "build_state.json"

DEFAULT_BATCH_SIZE = 65_536
TRAILING_MEDIAN_WINDOW = 20  # Q4: trailing-20-day causal median
DEFAULT_DUCKDB_MEMORY_LIMIT = "4GB"
DEFAULT_DUCKDB_TEMP_NAME = ".duckdb_tmp"

# Builder identity: checkpoint and per-day records carry the SHA-256 of this
# module so a changed builder invalidates stale resumes instead of reusing
# derived output produced by an older implementation.
BUILDER_SHA256 = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()

ET_ZONE = "America/New_York"
ET_SESSION_HOUR = 18  # events at or after 18:00 ET belong to the next date
LOOKUP_TABLE_NAME = "x0_plan_lookup"
PLAN_LOOKUP_COLUMNS = ("trading_date", "raw_symbol")

# Databento MBP-1 columns the store build scans.  The real GLBX MBP-1 CSV
# carries these 14 columns (verified against a surviving 2026-03 fragment);
# extra Databento columns (rtype, flags, ts_in_delta, ...) are intentionally
# not part of the research store's documented surface.
REQUIRED_SCAN_COLUMNS: Tuple[str, ...] = (
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
)

_SCAN_TYPES: Dict[str, str] = {
    "ts_recv": "VARCHAR",
    "ts_event": "VARCHAR",
    "action": "VARCHAR",
    "side": "VARCHAR",
    "price": "DOUBLE",
    "size": "BIGINT",
    "sequence": "BIGINT",
    "bid_px_00": "DOUBLE",
    "ask_px_00": "DOUBLE",
    "bid_sz_00": "BIGINT",
    "ask_sz_00": "BIGINT",
    "bid_ct_00": "BIGINT",
    "ask_ct_00": "BIGINT",
    "symbol": "VARCHAR",
}

_CSV_SUFFIXES = (".csv.zst", ".csv")
_MONTH_RE = re.compile(r"20\d{2}(0[1-9]|1[0-2])")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

__all__ = [
    "BUILDER_SHA256",
    "CHECKPOINT_FILENAME",
    "CHECKPOINT_SCHEMA_VERSION",
    "DayGroup",
    "DEFAULT_BATCH_SIZE",
    "PLAN_LOOKUP_COLUMNS",
    "PROGRAM_ID",
    "REQUIRED_SCAN_COLUMNS",
    "SLICE_ID",
    "TRAILING_MEDIAN_WINDOW",
    "X0BuildError",
    "build_selected_row_store",
    "merge_daily_contributions",
    "month_key",
    "plan_lookup",
    "selected_row_arrow_batches",
    "selected_row_frames",
    "stream_selected_days",
    "trailing_event_count_medians",
    "validate_source_files",
]


class X0BuildError(RuntimeError):
    """Raised when the X0 store build cannot proceed (fail closed)."""


# ---------------------------------------------------------------------------
# Source files: naming, order, containment
# ---------------------------------------------------------------------------

def month_key(path: str | Path) -> Tuple[int, int]:
    """Resolve the ``(year, month)`` key from a monthly MBP-1 CSV filename.

    Accepts both Databento monthly names (``glbx-mdp3-20260301-20260331.mbp-1.csv.zst``)
    and synthetic ``GLBX-202401.csv(.zst)`` names.  Exactly one distinct
    ``YYYYMM`` token must appear in the stem; otherwise the file is rejected
    (fail closed, e.g. a multi-month or unparseable name).
    """
    name = Path(path).name.lower()
    stem = name
    for suffix in _CSV_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    distinct = sorted({match.group(0) for match in _MONTH_RE.finditer(stem)})
    if len(distinct) != 1:
        raise X0BuildError(
            f"cannot resolve a unique YYYYMM month key from filename {Path(path).name!r}"
        )
    token = distinct[0]
    return int(token[:4]), int(token[4:6])


def _previous_month(key: Tuple[int, int]) -> Tuple[int, int]:
    year, month = key
    if month == 1:
        return year - 1, 12
    return year, month - 1


def validate_source_files(
    paths: Sequence[str | Path],
    *,
    source_root: str | Path | None = None,
) -> List[Tuple[Path, Tuple[int, int]]]:
    """Validate an ordered monthly CSV list and return ``(resolved_path, month_key)``.

    Rules (all fail closed with :class:`X0BuildError`):

    * every path exists, is a file, and has a ``.csv`` / ``.csv.zst`` suffix
      with a resolvable month key;
    * no duplicate resolved path and no duplicate month key;
    * the month keys are strictly descending **and contiguous** (each file is
      exactly one month older than its predecessor), because a non-contiguous
      gap would silently lose rows of a trading date split across the gap;
    * when ``source_root`` is supplied, every source resolves under it.
    """
    if not paths:
        raise X0BuildError("at least one monthly CSV source is required")
    root = Path(source_root).resolve() if source_root is not None else None
    resolved: List[Tuple[Path, Tuple[int, int]]] = []
    seen_paths = set()
    seen_keys = set()
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            raise X0BuildError(f"monthly CSV not found: {path}")
        name = path.name.lower()
        if not name.endswith(_CSV_SUFFIXES):
            raise X0BuildError(
                f"unsupported monthly CSV suffix: {path.name!r} "
                "(expected .csv or .csv.zst)"
            )
        resolved_path = path.resolve()
        if resolved_path in seen_paths:
            raise X0BuildError(f"duplicate source path: {path}")
        if root is not None:
            try:
                ensure_under(resolved_path, root)
            except ValueError as exc:
                raise X0BuildError(
                    f"source {resolved_path} is outside approved source root {root}"
                ) from exc
        key = month_key(path)
        if key in seen_keys:
            raise X0BuildError(
                f"duplicate month key {key[0]:04d}-{key[1]:02d} "
                f"across source files: {path}"
            )
        seen_paths.add(resolved_path)
        seen_keys.add(key)
        resolved.append((resolved_path, key))

    for index in range(1, len(resolved)):
        previous = resolved[index - 1][1]
        current = resolved[index][1]
        expected = _previous_month(previous)
        if current != expected:
            raise X0BuildError(
                "invalid file order: expected month "
                f"{expected[0]:04d}-{expected[1]:02d} after "
                f"{previous[0]:04d}-{previous[1]:02d}, got "
                f"{current[0]:04d}-{current[1]:02d} "
                "(files must be contiguous and newest-first)"
            )
    return resolved


# ---------------------------------------------------------------------------
# DuckDB streaming scan + plan join
# ---------------------------------------------------------------------------

def _quote_ident(name: str) -> str:
    if not isinstance(name, str) or _IDENT_RE.fullmatch(name) is None:
        raise X0BuildError(f"invalid SQL identifier {name!r}")
    return f'"{name}"'


def configure_duckdb(
    con: duckdb.DuckDBPyConnection,
    *,
    temp_directory: str | Path | None = None,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
) -> None:
    """Force UTC, cap RAM, and spill sorts to ``temp_directory`` (C: store)."""
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute("SET max_temp_directory_size = '250GB'")
    if temp_directory is None:
        return
    path = Path(temp_directory)
    path.mkdir(parents=True, exist_ok=True)
    posix = path.resolve().as_posix().replace("'", "''")
    con.execute(f"SET temp_directory = '{posix}'")


def _scan_select_list() -> str:
    parts: List[str] = []
    for name in REQUIRED_SCAN_COLUMNS:
        ident = _quote_ident(name)
        if name in ("ts_event", "ts_recv"):
            parts.append(f"{databento_timestamp_sql(ident)} AS {ident}")
        elif name in ("price", "bid_px_00", "ask_px_00"):
            parts.append(f"{databento_price_sql(ident)} AS {ident}")
        else:
            parts.append(ident)
    return ", ".join(parts)


def _selected_rows_sql() -> str:
    columns = _scan_select_list()
    types = ", ".join(
        f"{_quote_ident(name)}: '{dtype}'" for name, dtype in _SCAN_TYPES.items()
    )
    return f"""
WITH parsed AS (
    SELECT {columns},
           row_number() OVER () - 1 AS source_row_number
    FROM read_csv_auto(?, header=true, types={{ {types} }}) AS src
),
local AS (
    SELECT parsed.*,
           (ts_event::TIMESTAMPTZ) AT TIME ZONE '{ET_ZONE}' AS local_ts
    FROM parsed
),
dated AS (
    SELECT {columns}, source_row_number,
           strftime(
               CASE
                   WHEN EXTRACT(HOUR FROM local_ts) >= {ET_SESSION_HOUR}
                   THEN CAST(local_ts AS DATE) + INTERVAL '1 day'
                   ELSE CAST(local_ts AS DATE)
               END,
               '%Y-%m-%d'
           ) AS trading_date
    FROM local
)
SELECT dated.*
FROM dated
WHERE LEFT(UPPER(TRIM(CAST(dated.symbol AS VARCHAR))), 2) IN ('ZN', 'TY')
"""


_SELECTED_ROWS_SQL = _selected_rows_sql()


@contextmanager
def _open_mbp1_text(path: Path):
    """Open a monthly ``.csv`` or ``.csv.zst`` as a text stream."""
    if path.name.lower().endswith(".csv.zst"):
        raw = path.open("rb")
        try:
            reader = zstandard.ZstdDecompressor().stream_reader(raw)
            text = io.TextIOWrapper(reader, encoding="utf-8", newline="")
            try:
                yield text
            finally:
                text.close()
        finally:
            raw.close()
        return
    with path.open("r", encoding="utf-8", newline="") as text:
        yield text


def _parse_ts_series(series: pd.Series) -> pd.Series:
    """Parse Databento epoch-ns integers or ISO timestamp strings as UTC."""
    text = series.astype(str).str.strip()
    if text.empty:
        return pd.to_datetime(series, utc=True)
    sample = text.iloc[0]
    if sample.isdigit():
        return pd.to_datetime(pd.to_numeric(text, errors="coerce"), unit="ns", utc=True)
    return pd.to_datetime(text, utc=True)


def _scale_price_series(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    return values.where(values.abs() < 1_000_000.0, values / 1_000_000_000.0)


def _trading_date_series(ts_utc: pd.Series) -> pd.Series:
    local = ts_utc.dt.tz_convert(ET_ZONE)
    midnight = local.dt.normalize()
    shifted = midnight + pd.to_timedelta((local.dt.hour >= ET_SESSION_HOUR).astype("int64"), unit="D")
    return shifted.dt.strftime("%Y-%m-%d")


def selected_row_arrow_batches(
    path: str | Path,
    lookup: pd.DataFrame,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    temp_directory: str | Path | None = None,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
) -> Iterator[pa.RecordBatch]:
    """Stream one monthly CSV's selected rows as Arrow record batches.

    Chunked zstd/CSV read (not a DuckDB full-file materialize). Assigns a
    deterministic zero-based ``source_row_number`` before the plan filter,
    ET trading dates, and keeps only the selected front-contract aliases.
    ``temp_directory`` / ``memory_limit`` are accepted for call-site
    compatibility; day-level spill is handled by :func:`stream_selected_days`.
    """
    del temp_directory, memory_limit
    source = Path(path)
    if not source.is_file():
        raise X0BuildError(f"monthly CSV not found: {source}")
    month_key(source)  # naming guard (fail closed)
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
    if (
        not isinstance(lookup, pd.DataFrame)
        or list(lookup.columns) != list(PLAN_LOOKUP_COLUMNS)
        or not lookup.columns.is_unique
    ):
        raise X0BuildError(
            "lookup must be a plan_lookup() DataFrame with columns "
            f"{list(PLAN_LOOKUP_COLUMNS)}"
        )
    if lookup.duplicated(["trading_date", "raw_symbol"]).any():
        raise X0BuildError("plan lookup contains duplicate (trading_date, raw_symbol) keys")

    lookup_keys = set(
        zip(lookup["trading_date"].astype(str), lookup["raw_symbol"].astype(str))
    )
    source_row = 0
    try:
        with _open_mbp1_text(source) as text:
            chunks = pd.read_csv(
                text,
                usecols=list(REQUIRED_SCAN_COLUMNS),
                chunksize=batch_size,
            )
            for chunk in chunks:
                n_rows = int(len(chunk))
                if n_rows == 0:
                    continue
                chunk = chunk.copy()
                chunk["source_row_number"] = range(source_row, source_row + n_rows)
                source_row += n_rows
                chunk["ts_event"] = _parse_ts_series(chunk["ts_event"])
                if "ts_recv" in chunk.columns:
                    chunk["ts_recv"] = _parse_ts_series(chunk["ts_recv"])
                for column in ("price", "bid_px_00", "ask_px_00"):
                    if column in chunk.columns:
                        chunk[column] = _scale_price_series(chunk[column])
                chunk["trading_date"] = _trading_date_series(chunk["ts_event"])
                symbols = chunk["symbol"].astype(str).str.upper().str.strip()
                mask = [
                    (date, symbol) in lookup_keys
                    for date, symbol in zip(chunk["trading_date"].astype(str), symbols)
                ]
                filtered = chunk.loc[mask]
                if filtered.empty:
                    continue
                yield pa.RecordBatch.from_pandas(filtered, preserve_index=False)
    except X0BuildError:
        raise
    except Exception as exc:
        raise X0BuildError(
            f"{source.name}: selected-row scan failed: {exc}"
        ) from exc


def selected_row_frames(
    path: str | Path,
    lookup: pd.DataFrame,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Iterator[pd.DataFrame]:
    """Like :func:`selected_row_arrow_batches` but yielding one pandas frame per batch."""
    for batch in selected_row_arrow_batches(path, lookup, batch_size=batch_size):
        yield batch.to_pandas()


# ---------------------------------------------------------------------------
# Streaming day grouping + cross-file boundary merge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DayGroup:
    """One trading date's selected rows from one source file.

    ``is_earliest_of_source`` is True only for the final group of a source
    file (the file's earliest trading date): the only group that can continue
    into the next-older file.
    """

    trading_date: str
    frame: pd.DataFrame
    is_earliest_of_source: bool


def stream_selected_days(
    batches: Iterator[pa.RecordBatch],
    *,
    source_label: str = "<stream>",
    spill_directory: str | Path | None = None,
) -> Iterator[DayGroup]:
    """Group a file-order selected-row stream into newest-first day groups.

    Real Databento monthly CSVs are chronological, so DuckDB streams in
    ``source_row_number`` order without a full-month sort (which OOMs).
    This function buffers at most one in-flight date, spills completed
    date parts when ``spill_directory`` is set, then yields dates
    newest-first so :func:`merge_daily_contributions` keeps its contract.
    The final yielded group is the source file's earliest date
    (``is_earliest_of_source=True``).
    """
    parts: Dict[str, List[pd.DataFrame]] = {}
    spill_root = Path(spill_directory) if spill_directory is not None else None
    spill_counts: Dict[str, int] = {}
    current_date: Optional[str] = None
    current_frames: List[pd.DataFrame] = []

    def flush_current() -> None:
        nonlocal current_date, current_frames
        if current_date is None or not current_frames:
            current_date = None
            current_frames = []
            return
        frame = pd.concat(current_frames, ignore_index=True)
        if spill_root is not None:
            spill_root.mkdir(parents=True, exist_ok=True)
            index = spill_counts.get(current_date, 0)
            spill_counts[current_date] = index + 1
            _atomic_write_parquet(
                frame, spill_root / f"{current_date}__{index:04d}.parquet"
            )
        else:
            parts.setdefault(current_date, []).append(frame)
        current_date = None
        current_frames = []

    for batch in batches:
        if batch.num_rows == 0:
            continue
        missing = {"trading_date", "source_row_number"} - set(batch.schema.names)
        if missing:
            raise X0BuildError(
                f"{source_label}: stream batches must carry trading_date and "
                f"source_row_number, missing {sorted(missing)}"
            )
        frame = batch.to_pandas()
        dates = frame["trading_date"].astype(str).to_numpy()
        size = frame.shape[0]
        boundaries = [0]
        boundaries.extend(
            int(index) for index in np.flatnonzero(dates[1:] != dates[:-1]) + 1
        )
        boundaries.append(size)
        for start, end in zip(boundaries, boundaries[1:]):
            segment_date = str(dates[start])
            if current_date is None:
                current_date = segment_date
                current_frames = [frame.iloc[start:end].copy()]
            elif segment_date == current_date:
                current_frames.append(frame.iloc[start:end].copy())
            else:
                flush_current()
                current_date = segment_date
                current_frames = [frame.iloc[start:end].copy()]
    flush_current()

    if spill_root is not None:
        dates = sorted(
            {path.name.split("__", 1)[0] for path in spill_root.glob("*.parquet")},
            reverse=True,
        )
    else:
        dates = sorted(parts, reverse=True)

    for index, trading_date in enumerate(dates):
        if spill_root is not None:
            files = sorted(spill_root.glob(f"{trading_date}__*.parquet"))
            if not files:
                continue
            frame = pd.concat(
                [pd.read_parquet(path) for path in files], ignore_index=True
            )
        else:
            frame = pd.concat(parts[trading_date], ignore_index=True)
        if "source_row_number" in frame.columns:
            frame = frame.sort_values(
                ["source_row_number"], kind="stable"
            ).reset_index(drop=True)
        yield DayGroup(
            trading_date,
            frame,
            is_earliest_of_source=index == len(dates) - 1,
        )


def merge_daily_contributions(
    file_groups: Sequence[Iterator[DayGroup]],
    *,
    source_labels: Sequence[str],
) -> Iterator[Tuple[str, Sequence[Tuple[int, pd.DataFrame]]]]:
    """Stream complete ``(trading_date, chunks)`` days from per-file day groups.

    Each file iterator must yield :class:`DayGroup` items in strictly
    descending ``trading_date`` order.  At most one completed day (the current
    group) plus the single cross-file boundary carry is held: a file's
    earliest date is carried until the next-older file has been scanned, then
    merged with the same date there (older-file contribution first, i.e.
    chronological), or emitted alone once a strictly older date proves it is
    complete.  An intermediate file with no selected rows does not finalize
    the carry, and no day is lost or emitted twice.

    Fail closed on an impossible trading-date increase within a file and on a
    boundary carry older than the next file's first selected date.
    """
    if not file_groups:
        raise X0BuildError("at least one source day stream is required")
    if len(source_labels) != len(file_groups):
        raise X0BuildError("source_labels must have one label per source day stream")

    carry_date: Optional[str] = None
    carry_index: Optional[int] = None
    carry_frame: Optional[pd.DataFrame] = None
    last_index = len(file_groups) - 1

    for file_index, groups in enumerate(file_groups):
        previous_date: Optional[str] = None
        for group in groups:
            if not isinstance(group, DayGroup):
                raise X0BuildError(
                    f"{source_labels[file_index]}: merge_daily_contributions "
                    "requires DayGroup items"
                )
            if previous_date is not None and group.trading_date >= previous_date:
                raise X0BuildError(
                    f"{source_labels[file_index]}: day groups are not strictly "
                    f"descending ({previous_date!r} -> {group.trading_date!r})"
                )
            previous_date = group.trading_date

            if carry_date is not None and group.trading_date > carry_date:
                raise X0BuildError(
                    f"impossible boundary: carry {carry_date!r} from "
                    f"{source_labels[carry_index]} is older than the first "
                    f"selected date {group.trading_date!r} of "
                    f"{source_labels[file_index]}"
                )
            if carry_date is not None and group.trading_date == carry_date:
                yield (
                    group.trading_date,
                    [(carry_index, carry_frame), (file_index, group.frame)],
                )
                carry_date = carry_index = carry_frame = None
            else:
                if carry_date is not None:
                    # group.trading_date < carry_date: no older file can still
                    # contribute to the carry, so it is complete.
                    yield (carry_date, [(carry_index, carry_frame)])
                    carry_date = carry_index = carry_frame = None
                if group.is_earliest_of_source and file_index != last_index:
                    carry_date = group.trading_date
                    carry_index = file_index
                    carry_frame = group.frame
                else:
                    yield (group.trading_date, [(file_index, group.frame)])
        if file_index == last_index and carry_date is not None:
            # The oldest file contributed no selected rows; the final carry is
            # complete and must not be lost.
            yield (carry_date, [(carry_index, carry_frame)])
            carry_date = carry_index = carry_frame = None


# ---------------------------------------------------------------------------
# Plan lookup: eligible days -> selected-contract raw-symbol aliases
# ---------------------------------------------------------------------------

def _raw_symbol_aliases(contract: Contract) -> Tuple[str, ...]:
    r"""All raw MBP-1 symbol spellings matching one canonical contract.

    Mirrors the ``front_contract`` outright regex ``^(ZN|TY)[\s-]*(H|M|U|Z)
    (\d{1,2})(?:\s+COMDTY)?$`` for the one- and two-digit-year forms.
    """
    month = contract.month_code
    year_forms = (str(contract.year % 10), f"{contract.year % 100:02d}")
    aliases = set()
    for root in ("ZN", "TY"):
        for digits in year_forms:
            for separator in ("", " ", "-"):
                base = f"{root}{separator}{month}{digits}"
                aliases.add(base)
                aliases.add(f"{base} COMDTY")
    return tuple(sorted(aliases))


def plan_lookup(
    plan: pd.DataFrame,
    *,
    reference_date: date | str | None = None,
) -> pd.DataFrame:
    """Expand an eligible causal day plan into ``(trading_date, raw_symbol)`` keys.

    Every plan row with ``research_eligible=True`` contributes all raw symbol
    aliases of its selected front contract (``ZNH6``, ``ZNH6 COMDTY``,
    ``TYH6 COMDTY``, two-digit-year forms, ...).  Ineligible rows (bootstrap
    and no-winner days) contribute nothing, so the DuckDB join keeps only the
    selected contract on eligible days.
    """
    plan = _validate_plan(plan)
    rows: List[Dict[str, str]] = []
    for _, row in plan.iterrows():
        if not bool(row["research_eligible"]):
            continue
        selected = row["selected_contract"]
        if selected is None or pd.isna(selected):
            continue
        contract = parse_contract(str(selected), reference_date=reference_date)
        if contract is None:
            raise X0BuildError(
                f"plan selected_contract {selected!r} is not a parseable "
                "ZN/TY quarterly outright"
            )
        for alias in _raw_symbol_aliases(contract):
            rows.append({"trading_date": str(row["trading_date"]), "raw_symbol": alias})
    lookup = pd.DataFrame(rows, columns=list(PLAN_LOOKUP_COLUMNS))
    if lookup.duplicated(["trading_date", "raw_symbol"]).any():
        raise X0BuildError("plan lookup alias collision (internal invariant)")
    lookup = (
        lookup.sort_values(["trading_date", "raw_symbol"], kind="stable")
        .reset_index(drop=True)
    )
    return lookup


# ---------------------------------------------------------------------------
# Causal trailing-20 event-count median (Q4)
# ---------------------------------------------------------------------------

def _iso_date_string(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def trailing_event_count_medians(
    plan: pd.DataFrame,
    volume: pd.DataFrame,
    *,
    window: int = TRAILING_MEDIAN_WINDOW,
) -> Dict[str, Optional[float]]:
    """Causal trailing-``window`` median of daily selected-contract event counts.

    For every plan date ``D`` (ascending), the Q4 median is the median of the
    selected contract's ``event_count`` over the most recent up-to-``window``
    eligible dates **strictly before** ``D`` (from the volume prepass).  No
    same-day or future information is used, and dates whose selected contract
    has no volume row simply do not enter the window.  Warmup dates with no
    prior count map to ``None`` (Q4 passes unflagged).
    """
    plan = _validate_plan(plan)
    volume = _validate_volume(volume)
    if not isinstance(window, int) or window <= 0:
        raise ValueError(f"window must be a positive int, got {window!r}")

    eligible = plan.loc[
        plan["research_eligible"] & plan["selected_contract"].notna(),
        ["trading_date", "selected_contract"],
    ].copy()
    eligible["trading_date"] = eligible["trading_date"].map(_iso_date_string)
    eligible["selected_contract"] = eligible["selected_contract"].astype(str)

    work = volume.copy()
    work["trading_date"] = work["trading_date"].map(_iso_date_string)
    work = work.merge(eligible, on="trading_date", how="inner")
    work = work[work["contract"].astype(str) == work["selected_contract"]]
    if work.duplicated(["trading_date", "selected_contract"]).any():
        raise X0BuildError(
            "volume contains duplicate rows for a (trading_date, selected contract)"
        )
    counts = work.set_index("trading_date")["event_count"].astype("int64")

    medians: Dict[str, Optional[float]] = {}
    prior_counts: List[int] = []
    for trading_date in plan["trading_date"].map(_iso_date_string):
        medians[trading_date] = (
            float(np.median(prior_counts[-window:])) if prior_counts else None
        )
        if trading_date in counts.index:
            prior_counts.append(int(counts.loc[trading_date]))
    return medians


# ---------------------------------------------------------------------------
# Plan / volume validation + deterministic hashing
# ---------------------------------------------------------------------------

def _validate_plan(plan: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(plan, pd.DataFrame):
        raise X0BuildError("plan must be a pandas DataFrame")
    missing = [column for column in PLAN_COLUMNS if column not in plan.columns]
    if missing:
        raise X0BuildError(f"plan missing columns: {sorted(missing)}")
    if not plan.columns.is_unique:
        raise X0BuildError("plan has duplicate column names")
    dates = [str(value) for value in plan["trading_date"]]
    if any(current <= previous for previous, current in zip(dates, dates[1:])):
        raise X0BuildError(
            "plan trading dates must be strictly ascending and unique "
            "(build_day_plan contract)"
        )
    return plan


def _validate_volume(volume: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(volume, pd.DataFrame):
        raise X0BuildError("volume must be a pandas DataFrame")
    missing = [column for column in VOLUME_COLUMNS if column not in volume.columns]
    if missing:
        raise X0BuildError(f"volume missing columns: {sorted(missing)}")
    if not volume.columns.is_unique:
        raise X0BuildError("volume has duplicate column names")
    return volume


def _json_safe(value: Any) -> Any:
    """Recursively convert numpy/pandas/Timestamp values to JSON-safe primitives."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _json_sha(payload: Any) -> str:
    text = json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan_canonical(plan: pd.DataFrame) -> List[Dict[str, Any]]:
    return [
        {column: _json_safe(row[column]) for column in PLAN_COLUMNS}
        for _, row in plan.iterrows()
    ]


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _load_checkpoint(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise X0BuildError(f"checkpoint is not readable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise X0BuildError(f"checkpoint is not a JSON object: {path}")
    return payload


def _validate_checkpoint(
    prior: Dict[str, Any],
    *,
    store_root: Path,
    analysis_root: Path,
    checkpoint_name: str,
    files: Sequence[Path],
    plan_sha256: str,
    medians_sha256: str,
) -> None:
    if prior.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise X0BuildError(
            "checkpoint schema_version mismatch; cannot resume "
            f"(expected {CHECKPOINT_SCHEMA_VERSION}, got {prior.get('schema_version')!r})"
        )
    if prior.get("program") != PROGRAM_ID or prior.get("slice") != SLICE_ID:
        raise X0BuildError(
            "checkpoint belongs to a different program/slice; cannot resume"
        )
    if prior.get("dry_run") is not False:
        raise X0BuildError("checkpoint claims a dry run; refusing to resume")
    if Path(str(prior.get("store_root", ""))).resolve() != store_root:
        raise X0BuildError("checkpoint store_root mismatch; cannot resume")
    if Path(str(prior.get("analysis_root", ""))).resolve() != analysis_root:
        raise X0BuildError("checkpoint analysis_root mismatch; cannot resume")
    if prior.get("checkpoint_file") != checkpoint_name:
        raise X0BuildError("checkpoint filename mismatch; cannot resume")
    if prior.get("holdout_opened") is not False:
        raise X0BuildError("checkpoint reports holdout access; refusing to resume")
    if prior.get("plan_sha256") != plan_sha256:
        raise X0BuildError(
            "checkpoint plan hash mismatch (plan changed); cannot resume"
        )
    if prior.get("medians_sha256") != medians_sha256:
        raise X0BuildError(
            "checkpoint trailing-median hash mismatch (volume/plan changed); "
            "cannot resume"
        )
    if list(prior.get("sources") or []) != [str(path) for path in files]:
        raise X0BuildError(
            "checkpoint source list/order mismatch; cannot resume"
        )


def _checkpoint_payload(
    *,
    store_root: Path,
    analysis_root: Path,
    checkpoint_name: str,
    files: Sequence[Path],
    keys: Sequence[Tuple[int, int]],
    source_hashes: Mapping[str, str],
    builder_sha256: str,
    plan_sha256: str,
    medians_sha256: str,
    days: Mapping[str, Mapping[str, Any]],
    emission_order: Sequence[str],
    stamp: datetime,
) -> Dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "program": PROGRAM_ID,
        "slice": SLICE_ID,
        "dry_run": False,
        "store_root": str(store_root),
        "analysis_root": str(analysis_root),
        "checkpoint_file": checkpoint_name,
        "sources": [str(path) for path in files],
        "source_month_keys": [list(key) for key in keys],
        "source_hashes": dict(source_hashes),
        "builder_sha256": builder_sha256,
        "plan_sha256": plan_sha256,
        "medians_sha256": medians_sha256,
        "days": {trading_date: days[trading_date] for trading_date in sorted(days)},
        "emission_order": list(emission_order),
        "holdout_opened": False,
        "updated_at_utc": stamp.isoformat(),
    }


def _day_resumable(
    prior: Optional[Mapping[str, Any]],
    contributing_files: Sequence[str],
    source_hashes: Mapping[str, str],
    *,
    builder_sha256: str,
    prior_builder_sha256: Optional[str],
    store_root: Path,
    trading_date: str,
) -> bool:
    """True when a prior day record's source, builder, and output hashes match."""
    if not prior or prior.get("status") not in {"built", "rebuilt", "resumed"}:
        return False
    if prior.get("builder_sha256") != builder_sha256:
        return False
    if prior_builder_sha256 is not None and prior_builder_sha256 != builder_sha256:
        return False
    recorded = prior.get("source_file_hashes") or {}
    if sorted(str(key) for key in recorded) != sorted(contributing_files):
        return False
    for path in contributing_files:
        if recorded.get(path) != source_hashes.get(path):
            return False
    output_path = prior.get("output_path")
    output_sha256 = prior.get("output_sha256")
    if not output_path or not output_sha256:
        return False
    path = Path(str(output_path))
    expected_output = (store_root / f"{trading_date}.parquet").resolve()
    if path.resolve() != expected_output:
        return False
    if not path.is_file():
        return False
    return sha256_file(path) == output_sha256


# ---------------------------------------------------------------------------
# Atomic outputs
# ---------------------------------------------------------------------------

def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> Tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, temp, compression="snappy")
        os.replace(temp, path)
        return int(table.nbytes), int(path.stat().st_size)
    finally:
        if temp.exists():
            temp.unlink()


# ---------------------------------------------------------------------------
# Per-day emit: QC, crossed-row drop, derive, atomic write
# ---------------------------------------------------------------------------

def _emit_day(
    trading_date: str,
    chunks: Sequence[Tuple[int, pd.DataFrame]],
    *,
    files: Sequence[Path],
    plan_row: Mapping[str, Any],
    trailing_median: Optional[float],
    store_root: Path,
    reference_date: date | str | None,
    known_halts: Sequence[Tuple[Any, Any]],
    dry_run: bool,
    stamp: datetime,
    source_hashes: Mapping[str, str],
) -> Dict[str, Any]:
    ordered = sorted(
        chunks,
        key=lambda item: (item[0], int(item[1]["source_row_number"].min())),
    )
    by_file: Dict[int, List[pd.DataFrame]] = {}
    for file_index, frame in ordered:
        by_file.setdefault(file_index, []).append(frame)

    per_file_q1: Dict[str, Dict[str, Any]] = {}
    for file_index, frames in by_file.items():
        contribution = (
            pd.concat(frames, ignore_index=True)
            .sort_values("source_row_number", kind="stable")
            .reset_index(drop=True)
        )
        per_file_q1[str(files[file_index])] = _json_safe(
            check_timestamp_monotonicity(contribution)
        )

    merged = pd.concat(
        [
            frame.assign(_source_file_index=file_index)
            for file_index, frame in ordered
        ],
        ignore_index=True,
    ).sort_values(
        ["_source_file_index", "source_row_number"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    merged["source_order"] = np.arange(len(merged), dtype=np.int64)
    merged_q1 = _json_safe(check_timestamp_monotonicity(merged))
    canonical = (
        merged.sort_values(["ts_event", "sequence", "source_order"], kind="stable")
        .drop(columns=["_source_file_index", "source_row_number"])
        .reset_index(drop=True)
    )

    selected = str(plan_row["selected_contract"])
    previous = plan_row.get("previous_selected_contract")
    share = plan_row.get("selection_share")
    roll_windows: List[date] = []
    if previous is not None and not pd.isna(previous) and str(previous) != selected:
        try:
            roll_windows = documented_roll_window_dates(
                str(previous), selected, reference_date=reference_date
            )
        except VolumePrepassError as exc:
            raise X0BuildError(
                f"{trading_date}: cannot resolve Q5 roll window "
                f"{previous!r} -> {selected!r}"
            ) from exc

    qc = run_daily_qc(
        canonical,
        trading_date,
        trailing_median=trailing_median,
        chosen_contract=selected,
        prior_winner=(
            None
            if previous is None or pd.isna(previous)
            else str(previous)
        ),
        prior_winner_share=(
            None
            if share is None or pd.isna(share)
            else float(share)
        ),
        documented_roll_windows=roll_windows,
        known_halts=known_halts,
    )
    q1_bad = sorted(
        path
        for path, record in per_file_q1.items()
        if record.get("passed") is not True
    )
    qc["per_file_q1"] = per_file_q1
    qc["pre_canonical_q1"] = merged_q1
    if q1_bad:
        # Diagnostic only: the written parquet is canonical (ts, sequence,
        # source_order). Frozen Q1 is that order, not Databento file order.
        qc["per_file_q1_failed_files"] = q1_bad
    if qc["checks"]["Q1"]["passed"] is not True:
        qc["qc_pass"] = False
        qc["research_eligible"] = False

    bid = pd.to_numeric(canonical["bid_px_00"], errors="coerce")
    ask = pd.to_numeric(canonical["ask_px_00"], errors="coerce")
    good_book = bid.notna() & ask.notna() & (bid < ask)
    clean = canonical.loc[good_book].reset_index(drop=True)
    derived = derive_day_frame(
        clean, trading_date=trading_date, contract=selected
    )

    n_crossed = int((~good_book).sum())
    output_path = store_root / f"{trading_date}.parquet"
    try:
        ensure_under(output_path, store_root)
    except ValueError as exc:
        raise X0BuildError(
            f"output {output_path} escapes approved store root {store_root}"
        ) from exc

    output_sha256: Optional[str] = None
    output_bytes: Optional[int] = None
    if not dry_run:
        estimated_uncompressed_bytes, output_bytes = _atomic_write_parquet(
            derived, output_path
        )
        output_sha256 = sha256_file(output_path)
    else:
        estimated_uncompressed_bytes = int(
            pa.Table.from_pandas(derived, preserve_index=False).nbytes
        )

    contributing_files = sorted(
        {str(files[file_index]) for file_index, _ in ordered}
    )
    return {
        "trading_date": trading_date,
        "status": "built",
        "builder_sha256": BUILDER_SHA256,
        "selected_contract": selected,
        "output_path": str(output_path.resolve()),
        "output_sha256": output_sha256,
        "output_bytes": output_bytes,
        "estimated_uncompressed_bytes": estimated_uncompressed_bytes,
        "source_files": contributing_files,
        "source_file_hashes": {
            path: source_hashes.get(path) for path in contributing_files
        },
        "n_rows": int(len(canonical)),
        "n_crossed_dropped": n_crossed,
        "n_rows_after_crossed_drop": int(len(clean)),
        "trailing_median": (
            None if trailing_median is None else float(trailing_median)
        ),
        "qc": _json_safe(qc),
        "qc_pass": bool(qc["qc_pass"]),
        "plan_eligible": True,
        "research_eligible": bool(qc["research_eligible"]),
        "plan": {
            "selected_contract": selected,
            "previous_selected_contract": (
                None if previous is None or pd.isna(previous) else str(previous)
            ),
            "selection_share": (
                None if share is None or pd.isna(share) else float(share)
            ),
            "contract_changed": bool(plan_row.get("contract_changed", False)),
            "selection_source_date": plan_row.get("selection_source_date"),
        },
        "built_at_utc": stamp.isoformat(),
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_selected_row_store(
    sources: Sequence[str | Path],
    plan: pd.DataFrame,
    volume: pd.DataFrame,
    *,
    store_root: str | Path,
    analysis_root: str | Path,
    source_root: str | Path | None = None,
    reference_date: date | str | None = None,
    known_halts: Sequence[Tuple[Any, Any]] = (),
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    checkpoint_name: str = CHECKPOINT_FILENAME,
    now: Optional[Callable[[], datetime]] = None,
) -> Dict[str, Any]:
    """Build the selected-row per-day store from monthly MBP-1 CSVs.

    Parameters
    ----------
    sources
        Monthly ``.csv`` / ``.csv.zst`` files, **newest-first and
        contiguous** (validated by :func:`validate_source_files`).
    plan
        Eligible causal day plan from :func:`x0_volume.build_day_plan`
        (:data:`x0_volume.PLAN_COLUMNS`).
    volume
        Per-(trading_date, contract) volume prepass aggregates
        (:data:`x0_volume.VOLUME_COLUMNS`), used for the causal Q4
        trailing-20 median.
    store_root
        Approved store root; one ``<trading_date>.parquet`` per built day.
    analysis_root
        Approved analysis root; JSON checkpoints live here.
    source_root
        Optional approved source root; every source must resolve under it.
    reference_date
        Contract year-resolution reference (defaults to the frozen batch
        midpoint, :data:`front_contract.DEFAULT_REFERENCE_DATE`).
    known_halts
        Q3 ``(start, end)`` UTC halt intervals.
    batch_size
        Arrow record-batch size for the streaming scan.
    dry_run
        Scan + QC only; writes no Parquet and no checkpoint.
    checkpoint_name
        Checkpoint filename under ``analysis_root`` (default ``build_state.json``).
    now
        Clock for audit timestamps (injectable for deterministic tests).

    Returns a summary dict with ``days`` (per-date records incl. QC, hashes,
    status), ``emission_order`` (descending), skipped dates, and hashes.
    """
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
    stamp = (now or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(stamp, datetime):
        raise ValueError("now() must return a datetime")

    files_keys = validate_source_files(sources, source_root=source_root)
    files = [path for path, _ in files_keys]
    keys = [key for _, key in files_keys]

    store_root_resolved = Path(store_root).resolve()
    analysis_root_resolved = Path(analysis_root).resolve()
    checkpoint_path = (analysis_root_resolved / checkpoint_name).resolve()
    try:
        ensure_under(checkpoint_path, analysis_root_resolved)
    except ValueError as exc:
        raise X0BuildError(
            f"checkpoint {checkpoint_path} escapes approved analysis root "
            f"{analysis_root_resolved}"
        ) from exc
    if not dry_run:
        store_root_resolved.mkdir(parents=True, exist_ok=True)
        analysis_root_resolved.mkdir(parents=True, exist_ok=True)

    plan = _validate_plan(plan)
    volume = _validate_volume(volume)
    lookup = plan_lookup(plan, reference_date=reference_date)
    medians = trailing_event_count_medians(
        plan, volume, window=TRAILING_MEDIAN_WINDOW
    )
    plan_sha256 = _json_sha(_plan_canonical(plan))
    medians_sha256 = _json_sha(medians)
    builder_sha256 = BUILDER_SHA256

    prior: Optional[Dict[str, Any]] = None
    if not dry_run:
        prior = _load_checkpoint(checkpoint_path)
        if prior is not None:
            _validate_checkpoint(
                prior,
                store_root=store_root_resolved,
                analysis_root=analysis_root_resolved,
                checkpoint_name=checkpoint_name,
                files=files,
                plan_sha256=plan_sha256,
                medians_sha256=medians_sha256,
            )
    prior_days = prior.get("days") if prior is not None else {}
    prior_builder_sha256 = (
        prior.get("builder_sha256") if prior is not None else None
    )

    source_hashes: Dict[str, str] = (
        {} if dry_run else {str(path): sha256_file(path) for path in files}
    )

    plan_by_date = {
        str(row["trading_date"]): row for _, row in plan.iterrows()
    }
    skipped_days: Dict[str, Dict[str, Any]] = {}
    for trading_date, row in plan_by_date.items():
        if not bool(row["research_eligible"]):
            skipped_days[trading_date] = {
                "trading_date": trading_date,
                "status": "skipped",
                "builder_sha256": builder_sha256,
                "selected_contract": None,
                "output_path": None,
                "output_sha256": None,
                "output_bytes": None,
                "estimated_uncompressed_bytes": 0,
                "source_files": [],
                "source_file_hashes": {},
                "n_rows": 0,
                "n_crossed_dropped": 0,
                "n_rows_after_crossed_drop": 0,
                "trailing_median": None,
                "qc": None,
                "qc_pass": False,
                "plan_eligible": False,
                "research_eligible": False,
                "plan": {
                    "selected_contract": None,
                    "previous_selected_contract": (
                        None
                        if row["previous_selected_contract"] is None
                        or pd.isna(row["previous_selected_contract"])
                        else str(row["previous_selected_contract"])
                    ),
                    "selection_share": None,
                    "contract_changed": False,
                    "selection_source_date": row["selection_source_date"],
                },
                "reason": str(row["reason"]),
                "built_at_utc": stamp.isoformat(),
            }

    days: Dict[str, Dict[str, Any]] = {}
    emission_order: List[str] = []

    def complete_day(
        trading_date: str,
        chunks: Sequence[Tuple[int, pd.DataFrame]],
    ) -> None:
        """Resume-or-build one complete day and append it to the checkpoint."""
        contributing_files = sorted(
            {str(files[file_index]) for file_index, _ in chunks}
        )
        prior_day = prior_days.get(trading_date)
        resumable = _day_resumable(
            prior_day,
            contributing_files,
            source_hashes,
            builder_sha256=builder_sha256,
            prior_builder_sha256=prior_builder_sha256,
            store_root=store_root_resolved,
            trading_date=trading_date,
        )
        if resumable:
            record = dict(prior_day)  # type: ignore[arg-type]
            record["status"] = "resumed"
            record["resumed_at_utc"] = stamp.isoformat()
        else:
            plan_row = plan_by_date[trading_date]
            record = _emit_day(
                trading_date,
                chunks,
                files=files,
                plan_row=plan_row,
                trailing_median=medians.get(trading_date),
                store_root=store_root_resolved,
                reference_date=reference_date,
                known_halts=known_halts,
                dry_run=dry_run,
                stamp=stamp,
                source_hashes=source_hashes,
            )
            if prior_day is not None:
                record["status"] = "rebuilt"
        days[trading_date] = record
        emission_order.append(trading_date)
        if not dry_run:
            atomic_write_json(
                checkpoint_path,
                _checkpoint_payload(
                    store_root=store_root_resolved,
                    analysis_root=analysis_root_resolved,
                    checkpoint_name=checkpoint_name,
                    files=files,
                    keys=keys,
                    source_hashes=source_hashes,
                    builder_sha256=builder_sha256,
                    plan_sha256=plan_sha256,
                    medians_sha256=medians_sha256,
                    days={**skipped_days, **days},
                    emission_order=emission_order,
                    stamp=stamp,
                ),
            )

    duckdb_tmp = (
        None
        if dry_run
        else store_root_resolved / DEFAULT_DUCKDB_TEMP_NAME
    )
    file_groups = [
        stream_selected_days(
            selected_row_arrow_batches(
                path,
                lookup,
                batch_size=batch_size,
                temp_directory=duckdb_tmp,
            ),
            source_label=str(path),
            spill_directory=(
                None if duckdb_tmp is None else duckdb_tmp / f"days_{file_index}"
            ),
        )
        for file_index, (path, _key) in enumerate(files_keys)
    ]
    try:
        for trading_date, chunks in merge_daily_contributions(
            file_groups,
            source_labels=[str(path) for path in files],
        ):
            complete_day(trading_date, chunks)
    finally:
        if duckdb_tmp is not None:
            shutil.rmtree(duckdb_tmp, ignore_errors=True)

    all_days = {**skipped_days, **days}
    statuses = [record["status"] for record in days.values()]
    estimated_uncompressed_bytes = sum(
        int(record.get("estimated_uncompressed_bytes", 0) or 0)
        for record in days.values()
    )
    output_bytes = sum(
        int(record.get("output_bytes", 0) or 0) for record in days.values()
    )
    return {
        "program": PROGRAM_ID,
        "slice": SLICE_ID,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "dry_run": bool(dry_run),
        "sources": [str(path) for path in files],
        "source_month_keys": [list(key) for key in keys],
        "source_hashes": dict(source_hashes),
        "builder_sha256": builder_sha256,
        "store_root": str(store_root_resolved),
        "analysis_root": str(analysis_root_resolved),
        "checkpoint_path": (
            None if dry_run else str(checkpoint_path)
        ),
        "plan_sha256": plan_sha256,
        "medians_sha256": medians_sha256,
        "days": {trading_date: all_days[trading_date] for trading_date in sorted(all_days)},
        "emission_order": list(emission_order),
        "n_days_built": statuses.count("built"),
        "n_days_rebuilt": statuses.count("rebuilt"),
        "n_days_resumed": statuses.count("resumed"),
        "n_days_skipped": len(skipped_days),
        "estimated_uncompressed_bytes": estimated_uncompressed_bytes,
        "output_bytes": None if dry_run else output_bytes,
        "holdout_opened": False,
        "completed_at_utc": stamp.isoformat(),
    }
