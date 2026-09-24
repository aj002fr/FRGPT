"""X0 volume prepass and one-month store-pilot runner (Move Exhaustion V2).

Writes only under the frozen C: store root and
``workspace/analysis/move_exhaustion_v2/``.  Does not write D:, open holdout,
or touch live dashboards.

CLI::

    py -3 scripts/move_exhaustion_v2/data/x0_run.py volume
    py -3 scripts/move_exhaustion_v2/data/x0_run.py pilot [--month 2026-03]
    py -3 scripts/move_exhaustion_v2/data/x0_run.py store
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.move_exhaustion_v2.artifacts import (  # noqa: E402
    ANALYSIS_ROOT,
    SOURCE_ROOT,
    STORE_ROOT,
    atomic_write_json,
    ensure_under,
)
from scripts.move_exhaustion_v2.data.session import expected_open_sessions  # noqa: E402
from scripts.move_exhaustion_v2.data.event_semantics import EventSemanticsError  # noqa: E402
from scripts.move_exhaustion_v2.data.x0_build import (  # noqa: E402
    CHECKPOINT_FILENAME,
    X0BuildError,
    build_selected_row_store,
    month_key,
)
from scripts.move_exhaustion_v2.data.x0_finalize import (  # noqa: E402
    DEFAULT_HEADROOM_FRACTION,
    evaluate_disk_budget,
)
from scripts.move_exhaustion_v2.data.x0_volume import (  # noqa: E402
    VOLUME_COLUMNS,
    VolumePrepassError,
    build_day_plan,
    merge_monthly_aggregates,
    scan_monthly_volume_csv,
)

PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PKG_ROOT / "config" / "frozen" / "data_store.yaml"
DEFAULT_MANIFESTS_DIR = ANALYSIS_ROOT / "manifests"
DEFAULT_JOB_ID = "GLBX-20260811-4QVSETBK57"
DEFAULT_PILOT_MONTH = (2026, 3)  # last full month in the authorized batch
BATCH_START = date(2022, 1, 1)
BATCH_END = date(2026, 4, 1)  # inclusive; batch end_exclusive is 2026-04-02
_MONTH_ARG_RE = re.compile(r"^(\d{4})-(\d{2})$")
_CSV_SUFFIXES = (".csv.zst", ".csv")


class X0RunError(RuntimeError):
    """Raised when the X0 runner cannot proceed (fail closed)."""


def _is_d_drive(path: Path) -> bool:
    """True for D: and for UNC mappings of that volume (e.g. \\\\host\\d\\...)."""
    raw = Path(path)
    if raw.drive.upper() == "D:":
        return True
    text = str(raw)
    if len(text) >= 2 and text[0].upper() == "D" and text[1] == ":":
        return True
    resolved = raw.resolve()
    if resolved.drive.upper() == "D:":
        return True
    anchor = resolved.anchor.replace("/", "\\").rstrip("\\").lower()
    return anchor.endswith("\\d")


def assert_not_d_drive(path: Path, *, label: str) -> Path:
    """Reject any D: write/read root (user-directed 2026-08-12)."""
    raw = Path(path)
    if _is_d_drive(raw):
        raise X0RunError(f"{label} must not be on D: ({raw})")
    return raw.resolve()


def parse_month(value: str) -> Tuple[int, int]:
    match = _MONTH_ARG_RE.fullmatch(str(value).strip())
    if match is None:
        raise X0RunError(f"month must be YYYY-MM, got {value!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12):
        raise X0RunError(f"month must be YYYY-MM, got {value!r}")
    return year, month


def previous_month_key(key: Tuple[int, int]) -> Tuple[int, int]:
    year, month = key
    if month == 1:
        return year - 1, 12
    return year, month - 1


def load_batch_bounds(config_path: Path = DEFAULT_CONFIG_PATH) -> Tuple[date, date]:
    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    batch = payload["source"]["authorized_replacement_batch"]
    start = date.fromisoformat(str(batch["start_inclusive"]))
    end_exclusive = date.fromisoformat(str(batch["end_exclusive"]))
    return start, end_exclusive - timedelta(days=1)


def job_archive_dir(source_root: Path, job_id: str) -> Path:
    return assert_not_d_drive(Path(source_root) / job_id, label="archive dir")


def list_monthly_csv_sources(archive_dir: Path) -> List[Path]:
    """Monthly ``.csv`` / ``.csv.zst`` files, oldest-first by month key."""
    root = Path(archive_dir)
    if not root.is_dir():
        raise X0RunError(f"archive directory not found: {root}")
    found: List[Tuple[Tuple[int, int], Path]] = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        if not name.endswith(_CSV_SUFFIXES):
            continue
        found.append((month_key(path), path.resolve()))
    if not found:
        raise X0RunError(f"no monthly CSV sources under {root}")
    found.sort(key=lambda item: (item[0], item[1].name))
    return [path for _key, path in found]


def load_verified_file_index(manifests_dir: Path, job_id: str) -> Dict[str, Dict[str, Any]]:
    path = Path(manifests_dir) / f"files_{job_id}.json"
    if not path.is_file():
        raise X0RunError(f"file-state index not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files", payload)
    if not isinstance(files, dict):
        raise X0RunError(f"corrupt file-state index: {path}")
    return {str(name): dict(record) for name, record in files.items()}


def volume_dir(analysis_root: Path) -> Path:
    root = assert_not_d_drive(analysis_root, label="analysis root")
    out = ensure_under((root / "x0_volume").resolve(), root)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, temp, compression="snappy")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _monthly_artifact_paths(volume_root: Path, filename: str) -> Tuple[Path, Path]:
    safe = Path(filename).name
    monthly = volume_root / "monthly"
    return monthly / f"{safe}.volume.parquet", monthly / f"{safe}.meta.json"


def scan_or_resume_monthly(
    path: Path,
    volume_root: Path,
    *,
    file_index: Mapping[str, Mapping[str, Any]],
    now: Optional[Callable[[], datetime]] = None,
) -> Tuple[pd.DataFrame, str]:
    """Scan one monthly CSV or resume a matching per-file aggregate."""
    source = Path(path)
    parquet_path, meta_path = _monthly_artifact_paths(volume_root, source.name)
    record = file_index.get(source.name)
    if record is None:
        raise X0RunError(f"no verified file-state record for {source.name}")
    if record.get("status") != "verified":
        raise X0RunError(
            f"{source.name} file-state status is {record.get('status')!r}, not verified"
        )
    expected_size = int(record["size"])
    expected_sha = str(record["sha256"])
    actual_size = source.stat().st_size
    if actual_size != expected_size:
        raise X0RunError(
            f"{source.name} size {actual_size} != verified {expected_size}"
        )
    if parquet_path.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (
            meta.get("filename") == source.name
            and int(meta.get("size", -1)) == expected_size
            and str(meta.get("sha256")) == expected_sha
        ):
            frame = pd.read_parquet(parquet_path)
            missing = [column for column in VOLUME_COLUMNS if column not in frame.columns]
            if missing:
                raise X0RunError(
                    f"resumed volume artifact missing columns {missing}: {parquet_path}"
                )
            return frame[list(VOLUME_COLUMNS)], "resumed"
    frame = scan_monthly_volume_csv(source)
    stamp = (now or (lambda: datetime.now(timezone.utc)))()
    _atomic_write_parquet(frame, parquet_path)
    atomic_write_json(
        meta_path,
        {
            "filename": source.name,
            "size": expected_size,
            "sha256": expected_sha,
            "n_rows": int(len(frame)),
            "scanned_at_utc": stamp.isoformat(),
            "holdout_opened": False,
        },
    )
    return frame, "scanned"


def run_volume_prepass(
    *,
    archive_dir: Path,
    analysis_root: Path,
    file_index: Mapping[str, Mapping[str, Any]],
    start: date = BATCH_START,
    end: date = BATCH_END,
    sessions: Optional[Sequence[date | str]] = None,
    now: Optional[Callable[[], datetime]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Scan every monthly CSV, merge, and write the causal day plan."""
    volume_root = volume_dir(analysis_root)
    sources = list_monthly_csv_sources(archive_dir)
    frames: List[pd.DataFrame] = []
    statuses: Dict[str, str] = {}
    for index, source in enumerate(sources, start=1):
        if progress is not None:
            progress(f"volume {index}/{len(sources)} {source.name}")
        frame, status = scan_or_resume_monthly(
            source, volume_root, file_index=file_index, now=now
        )
        frames.append(frame)
        statuses[source.name] = status
        atomic_write_json(
            volume_root / "progress.json",
            {
                "completed": index,
                "total": len(sources),
                "last_file": source.name,
                "last_status": status,
                "holdout_opened": False,
            },
        )
    volume = merge_monthly_aggregates(frames)
    if sessions is None:
        sessions = [
            str(row["trading_date"])
            for row in expected_open_sessions(start, end)
        ]
    plan = build_day_plan(volume, sessions)
    _atomic_write_parquet(volume, volume_root / "volume.parquet")
    _atomic_write_parquet(plan, volume_root / "day_plan.parquet")
    stamp = (now or (lambda: datetime.now(timezone.utc)))()
    summary = {
        "n_source_files": len(sources),
        "n_scanned": sum(1 for status in statuses.values() if status == "scanned"),
        "n_resumed": sum(1 for status in statuses.values() if status == "resumed"),
        "n_volume_rows": int(len(volume)),
        "n_plan_rows": int(len(plan)),
        "n_research_eligible": int(plan["research_eligible"].sum()) if len(plan) else 0,
        "session_start": start.isoformat(),
        "session_end": end.isoformat(),
        "statuses": statuses,
        "holdout_opened": False,
        "completed_at_utc": stamp.isoformat(),
    }
    atomic_write_json(volume_root / "summary.json", summary)
    return {
        "volume": volume,
        "plan": plan,
        "summary": summary,
        "volume_root": str(volume_root),
    }


def load_volume_artifacts(analysis_root: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    volume_root = volume_dir(analysis_root)
    volume_path = volume_root / "volume.parquet"
    plan_path = volume_root / "day_plan.parquet"
    if not volume_path.is_file() or not plan_path.is_file():
        raise X0RunError(
            f"volume prepass artifacts missing under {volume_root}; run `volume` first"
        )
    return pd.read_parquet(volume_path), pd.read_parquet(plan_path)


def pilot_month_sources(
    sources: Sequence[Path],
    year: int,
    month: int,
) -> List[Path]:
    """Newest-first contiguous pair: target month plus the previous month."""
    by_key = {month_key(path): Path(path) for path in sources}
    target = (year, month)
    previous = previous_month_key(target)
    if target not in by_key:
        raise X0RunError(f"no monthly CSV for pilot month {year:04d}-{month:02d}")
    if previous not in by_key:
        raise X0RunError(
            f"pilot month {year:04d}-{month:02d} needs previous month "
            f"{previous[0]:04d}-{previous[1]:02d} for the 18:00 ET session split"
        )
    return [by_key[target], by_key[previous]]


def newest_first_sources(sources: Sequence[Path]) -> List[Path]:
    """Newest-first contiguous monthly CSVs (required by the selected-row merge)."""
    ordered = list(sources)
    if not ordered:
        raise X0RunError("no monthly CSV sources")
    return list(reversed(ordered))


def pilot_store_root(store_root: Path, year: int, month: int) -> Path:
    root = assert_not_d_drive(store_root, label="store root")
    out = ensure_under((root / f"pilot_{year:04d}{month:02d}").resolve(), root)
    return out


def target_month_prefix(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}-"


def build_pilot_disk_record(
    build: Mapping[str, Any],
    *,
    year: int,
    month: int,
    expected_total_sessions: int,
    free_bytes: int,
) -> Dict[str, Any]:
    """Disk-budget record from target-month parquet only (not the prior-month carry)."""
    prefix = target_month_prefix(year, month)
    days = []
    for trading_date, record in (build.get("days") or {}).items():
        if not str(trading_date).startswith(prefix):
            continue
        if record.get("status") in {"skipped", None}:
            continue
        output_bytes = record.get("output_bytes")
        if output_bytes is None:
            continue
        days.append((str(trading_date), int(output_bytes)))
    if not days:
        raise X0RunError(
            f"pilot month {year:04d}-{month:02d} produced no parquet days"
        )
    actual_bytes = sum(size for _date, size in days)
    actual_days = len(days)
    projected = (actual_bytes / actual_days) * int(expected_total_sessions)
    return {
        "pilot_month": f"{year:04d}-{month:02d}",
        "actual_parquet_bytes": int(actual_bytes),
        "actual_days": int(actual_days),
        "projected_full_store_bytes": float(projected),
        "free_bytes": int(free_bytes),
        "day_output_bytes": {trading_date: size for trading_date, size in days},
        "holdout_opened": False,
    }


def run_pilot(
    *,
    archive_dir: Path,
    analysis_root: Path,
    store_root: Path,
    year: int = DEFAULT_PILOT_MONTH[0],
    month: int = DEFAULT_PILOT_MONTH[1],
    start: date = BATCH_START,
    end: date = BATCH_END,
    source_root: Path | None = None,
    disk_usage: Optional[Callable[[str], Any]] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Dict[str, Any]:
    """Build one recent month into an isolated pilot store and gate disk budget."""
    volume, plan = load_volume_artifacts(analysis_root)
    archive = assert_not_d_drive(archive_dir, label="archive dir")
    src_root = assert_not_d_drive(
        source_root if source_root is not None else archive.parent,
        label="source root",
    )
    sources = pilot_month_sources(list_monthly_csv_sources(archive), year, month)
    pilot_root = pilot_store_root(store_root, year, month)
    analysis = assert_not_d_drive(analysis_root, label="analysis root")
    checkpoint_name = f"pilot_{year:04d}{month:02d}_build_state.json"
    build = build_selected_row_store(
        sources,
        plan,
        volume,
        store_root=pilot_root,
        analysis_root=analysis,
        source_root=src_root,
        checkpoint_name=checkpoint_name,
        now=now,
    )
    sessions = expected_open_sessions(start, end)
    usage = (disk_usage or shutil.disk_usage)(str(pilot_root))
    free_bytes = int(getattr(usage, "free", usage[2]))
    record = build_pilot_disk_record(
        build,
        year=year,
        month=month,
        expected_total_sessions=len(sessions),
        free_bytes=free_bytes,
    )
    disk = evaluate_disk_budget(
        record,
        expected_total_sessions=len(sessions),
        headroom_fraction=DEFAULT_HEADROOM_FRACTION,
    )
    stamp = (now or (lambda: datetime.now(timezone.utc)))()
    payload = {
        "pilot_month": f"{year:04d}-{month:02d}",
        "store_root": str(pilot_root),
        "checkpoint_name": checkpoint_name,
        "n_days_built": build.get("n_days_built"),
        "n_days_resumed": build.get("n_days_resumed"),
        "n_days_skipped": build.get("n_days_skipped"),
        "output_bytes": build.get("output_bytes"),
        "disk_record": record,
        "disk_budget": disk,
        "holdout_opened": False,
        "completed_at_utc": stamp.isoformat(),
    }
    atomic_write_json(analysis / f"pilot_{year:04d}{month:02d}_disk.json", payload)
    if not disk["passed"]:
        raise X0RunError(
            "pilot disk-budget FAIL: " + "; ".join(disk.get("reasons") or ["headroom"])
        )
    return payload


def run_full_store(
    *,
    archive_dir: Path,
    analysis_root: Path,
    store_root: Path,
    source_root: Path | None = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Dict[str, Any]:
    """Build the full selected-row store after a passing one-month disk pilot."""
    volume, plan = load_volume_artifacts(analysis_root)
    archive = assert_not_d_drive(archive_dir, label="archive dir")
    src_root = assert_not_d_drive(
        source_root if source_root is not None else archive.parent,
        label="source root",
    )
    dest = assert_not_d_drive(store_root, label="store root")
    analysis = assert_not_d_drive(analysis_root, label="analysis root")
    sources = newest_first_sources(list_monthly_csv_sources(archive))
    build = build_selected_row_store(
        sources,
        plan,
        volume,
        store_root=dest,
        analysis_root=analysis,
        source_root=src_root,
        checkpoint_name=CHECKPOINT_FILENAME,
        now=now,
    )
    stamp = (now or (lambda: datetime.now(timezone.utc)))()
    payload = {
        "store_root": str(dest),
        "checkpoint_name": CHECKPOINT_FILENAME,
        "n_sources": len(sources),
        "n_days_built": build.get("n_days_built"),
        "n_days_rebuilt": build.get("n_days_rebuilt"),
        "n_days_resumed": build.get("n_days_resumed"),
        "n_days_skipped": build.get("n_days_skipped"),
        "output_bytes": build.get("output_bytes"),
        "holdout_opened": False,
        "completed_at_utc": stamp.isoformat(),
    }
    atomic_write_json(analysis / "store_build_summary.json", payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="X0 volume prepass, store pilot, and full store")
    parser.add_argument("--job-id", default=DEFAULT_JOB_ID)
    parser.add_argument("--source-root", default=str(SOURCE_ROOT))
    parser.add_argument("--store-root", default=str(STORE_ROOT))
    parser.add_argument("--analysis-root", default=str(ANALYSIS_ROOT))
    parser.add_argument("--manifests-dir", default=str(DEFAULT_MANIFESTS_DIR))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("volume", help="scan monthly CSVs and write the causal day plan")
    sub.add_parser("store", help="build the full selected-row store (after a passing pilot)")
    pilot = sub.add_parser("pilot", help="build one recent month and gate disk budget")
    pilot.add_argument(
        "--month",
        default=f"{DEFAULT_PILOT_MONTH[0]:04d}-{DEFAULT_PILOT_MONTH[1]:02d}",
        help="YYYY-MM (default 2026-03, last full month in the authorized batch)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    source_root = assert_not_d_drive(Path(args.source_root), label="source root")
    store_root = assert_not_d_drive(Path(args.store_root), label="store root")
    analysis_root = assert_not_d_drive(Path(args.analysis_root), label="analysis root")
    start, end = load_batch_bounds(Path(args.config))
    archive_dir = job_archive_dir(source_root, args.job_id)
    try:
        if args.cmd == "volume":
            file_index = load_verified_file_index(Path(args.manifests_dir), args.job_id)
            result = run_volume_prepass(
                archive_dir=archive_dir,
                analysis_root=analysis_root,
                file_index=file_index,
                start=start,
                end=end,
                progress=lambda message: print(message, flush=True),
            )
            summary = result["summary"]
            print(
                f"volume_files={summary['n_source_files']} "
                f"scanned={summary['n_scanned']} resumed={summary['n_resumed']} "
                f"plan_rows={summary['n_plan_rows']} "
                f"eligible={summary['n_research_eligible']}",
                flush=True,
            )
            return 0
        if args.cmd == "store":
            payload = run_full_store(
                archive_dir=archive_dir,
                analysis_root=analysis_root,
                store_root=store_root,
                source_root=source_root,
            )
            print(
                f"store_days_built={payload['n_days_built']} "
                f"resumed={payload['n_days_resumed']} "
                f"skipped={payload['n_days_skipped']} "
                f"gib={(payload['output_bytes'] or 0) / (1024 ** 3):.3f}",
                flush=True,
            )
            return 0
        year, month = parse_month(args.month)
        payload = run_pilot(
            archive_dir=archive_dir,
            analysis_root=analysis_root,
            store_root=store_root,
            year=year,
            month=month,
            start=start,
            end=end,
            source_root=source_root,
        )
        disk = payload["disk_budget"]
        record = payload["disk_record"]
        print(
            f"pilot_month={payload['pilot_month']} "
            f"actual_days={record['actual_days']} "
            f"actual_gib={record['actual_parquet_bytes'] / (1024 ** 3):.3f} "
            f"projected_gib={record['projected_full_store_bytes'] / (1024 ** 3):.3f} "
            f"disk_pass={disk['passed']}",
            flush=True,
        )
        return 0
    except (X0RunError, X0BuildError, VolumePrepassError, EventSemanticsError) as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
