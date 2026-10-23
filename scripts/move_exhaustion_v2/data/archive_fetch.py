"""Resumable Databento batch-archive acquisition for Move Exhaustion V2 (X0).

Scope (PROGRAM.md §1 / §10 addendum 2026-08-11): exactly one authorized
``$0`` batch job for ZN.FUT / GLBX.MDP3 / mbp-1 / parent symbology /
2022-01-01 inclusive -> 2026-04-02 exclusive, monthly CSV+Zstd. This module:

* exposes an ``estimate`` / ``submit`` / ``status`` / ``download`` / ``verify``
  CLI;
* submits only the exact frozen batch config (no CLI overrides);
* rechecks cost before and after submission and refuses anything nonzero;
* reads ``DATABENTO_API_KEY`` from the environment or ``config/keys.env``
  without ever printing it;
* persists job/file state atomically only under
  ``workspace/analysis/move_exhaustion_v2/manifests``;
* downloads under ``C:/FRDBs/FutMBP/move_exhaustion_v2_source/<job_id>`` with
  per-file resume via size+SHA256 from ``batch.list_files``, quarantining any
  mismatched legacy/partial file so it is never treated as input;
* enforces a 20% free-space headroom guard on the destination drive.

Network code is confined to ``_build_client`` and ``http_fetch``; every
function that talks to Databento accepts an injectable client or fetch
callable so unit tests make no network calls.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONFIG_PATH = PKG_ROOT / "config" / "frozen" / "data_store.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / "config" / "keys.env"
DEFAULT_MANIFESTS_DIR = (
    REPO_ROOT / "workspace" / "analysis" / "move_exhaustion_v2" / "manifests"
)
DEFAULT_HEADROOM = 0.20
MAX_AUTHORIZED_COST_USD = 0.0

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ArchiveFetchError(Exception):
    """Base class for archive-acquisition failures (fail closed)."""


class ConfigError(ArchiveFetchError):
    """Frozen batch config is missing, malformed, or deviates from approval."""


class ApiKeyError(ArchiveFetchError):
    """DATABENTO_API_KEY could not be resolved without being exposed."""


class CostExceededError(ArchiveFetchError):
    """Estimated or confirmed batch cost is nonzero (authorized batch is $0)."""


class HeadroomError(ArchiveFetchError):
    """Download would leave less than the required free-space headroom."""


class ChecksumError(ArchiveFetchError):
    """Downloaded bytes failed size/SHA256 verification."""


class JobStateError(ArchiveFetchError):
    """Job is not in the state required for the requested operation."""


_REQUIRED_BATCH_FIELDS = (
    "dataset",
    "symbols",
    "stype_in",
    "schema",
    "start_inclusive",
    "end_exclusive",
    "encoding",
    "compression",
    "split_duration",
    "one_shot_only",
    "estimated_cost_usd_at_approval",
)


def _utc_now(now: Callable[[], datetime] | None = None) -> datetime:
    return now() if now is not None else datetime.now(timezone.utc)


def _utc_slug(now: Callable[[], datetime] | None = None) -> str:
    return _utc_now(now).strftime("%Y%m%dT%H%M%S%fZ")


# ---------------------------------------------------------------------------
# Key + config loading
# ---------------------------------------------------------------------------


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse simple ``KEY=VALUE`` lines. Never logs or returns secrets."""
    path = Path(path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    quote_chars = "\"'"
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip(quote_chars)
    return values


def load_api_key(env_path: Path = DEFAULT_ENV_PATH) -> str:
    """Resolve DATABENTO_API_KEY from the environment or keys.env.

    The key value is never printed, logged, or embedded in exception text.
    """
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        key = _load_env_file(env_path).get("DATABENTO_API_KEY")
    key = (key or "").strip().strip("\"'")
    if not key:
        raise ApiKeyError(
            f"DATABENTO_API_KEY not found in environment or {env_path}",
        )
    return key


def load_batch_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load the frozen data-store config and the authorized batch block."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(f"frozen config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        batch = raw["source"]["authorized_replacement_batch"]
        archive_root = raw["source"]["mbp1_archive_root"]
    except (KeyError, TypeError) as exc:
        raise ConfigError(
            f"{config_path}: missing source/authorized_replacement_batch fields",
        ) from exc
    if not isinstance(batch, dict):
        raise ConfigError(f"{config_path}: authorized_replacement_batch is not a mapping")
    validate_batch_config(batch)
    return {
        "batch": dict(batch),
        "archive_root": str(archive_root),
        "config_path": str(config_path),
    }


def validate_batch_config(batch: Mapping[str, Any]) -> None:
    """Fail closed unless the batch matches the approved X0 authorization."""
    missing = [k for k in _REQUIRED_BATCH_FIELDS if k not in batch]
    if missing:
        raise ConfigError(
            f"authorized batch missing required fields: {', '.join(missing)}",
        )
    if batch["one_shot_only"] is not True:
        raise ConfigError("authorized batch one_shot_only must be true")
    if batch["dataset"] != "GLBX.MDP3":
        raise ConfigError(f"unauthorized dataset: {batch['dataset']!r}")
    if batch["symbols"] != "ZN.FUT":
        raise ConfigError(f"unauthorized symbols: {batch['symbols']!r}")
    if batch["stype_in"] != "parent":
        raise ConfigError(f"unauthorized stype_in: {batch['stype_in']!r}")
    if batch["schema"] != "mbp-1":
        raise ConfigError(f"unauthorized schema: {batch['schema']!r}")
    if batch["encoding"] != "csv" or batch["compression"] != "zstd":
        raise ConfigError("authorized batch must be CSV+Zstd")
    if batch["split_duration"] != "month":
        raise ConfigError(f"unauthorized split_duration: {batch['split_duration']!r}")
    if parse_cost(batch["estimated_cost_usd_at_approval"]) != 0.0:
        raise ConfigError("authorized batch estimated cost at approval must be $0")
    try:
        if str(batch["start_inclusive"]) >= str(batch["end_exclusive"]):
            raise ConfigError("authorized batch start_inclusive must precede end_exclusive")
    except (TypeError, ValueError) as exc:
        raise ConfigError("authorized batch start/end are not comparable strings") from exc


def batch_fingerprint(batch: Mapping[str, Any]) -> str:
    """Stable SHA-256 over the canonical authorized-batch fields."""
    canonical = json.dumps(
        {k: batch[k] for k in sorted(batch)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Response parsing (fail closed; no secrets ever surfaced)
# ---------------------------------------------------------------------------


def parse_cost(value: Any) -> float:
    """Parse a Databento USD cost value; rejects missing/negative/NaN."""
    if isinstance(value, bool):
        raise ConfigError(f"invalid cost value: {value!r}")
    if isinstance(value, str):
        value = value.strip()
    try:
        cost = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid cost value: {value!r}") from exc
    if math.isnan(cost) or math.isinf(cost) or cost < 0:
        raise ConfigError(f"invalid cost value: {value!r}")
    return cost


def parse_job(job: Mapping[str, Any], *, require_cost: bool = True) -> dict:
    """Normalize a batch job response (submit_job or a list_jobs entry)."""
    job_id = job.get("job_id") or job.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ArchiveFetchError("batch job response missing job_id")
    job_id = validate_job_id(job_id.strip())
    cost_raw = job.get("cost") if job.get("cost") is not None else job.get("cost_usd")
    if cost_raw is None:
        if require_cost:
            raise ArchiveFetchError(
                f"batch job {job_id}: cost missing from response; refusing to assume a cost",
            )
        cost_usd: float | None = None
    else:
        cost_usd = parse_cost(cost_raw)
    state = job.get("state") or job.get("status")
    return {
        "job_id": job_id,
        "cost_usd": cost_usd,
        "state": str(state or "unknown"),
    }


def validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise ArchiveFetchError(f"invalid job id: {job_id!r}")
    return job_id


def _parse_sha256(value: Any) -> str:
    if not isinstance(value, str):
        raise ArchiveFetchError(f"invalid or unsupported file hash: {value!r}")
    algo, sep, digest = value.partition(":")
    if sep and algo.lower() == "sha256" and _SHA256_RE.fullmatch(digest):
        return digest.lower()
    if _SHA256_RE.fullmatch(value):
        return value.lower()
    raise ArchiveFetchError(f"invalid or unsupported file hash: {value!r}")


def _validate_filename(name: str) -> str:
    if (
        not name
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or name.endswith((".part", ".tmp"))
    ):
        raise ArchiveFetchError(f"unsafe batch filename: {name!r}")
    p = Path(name)
    if p.name != name or ".." in p.parts:
        raise ArchiveFetchError(f"unsafe batch filename: {name!r}")
    return name


def parse_file_entry(entry: Mapping[str, Any]) -> dict:
    """Normalize one ``batch.list_files`` entry into a safe file descriptor."""
    try:
        filename = str(entry["filename"])
        size = int(entry["size"])
        urls = entry["urls"]
        https_url = str(urls["https"])
    except (KeyError, TypeError) as exc:
        raise ArchiveFetchError(
            "batch file entry missing filename/size/urls.https",
        ) from exc
    if size < 0:
        raise ArchiveFetchError(f"negative file size for {filename!r}")
    return {
        "filename": _validate_filename(filename),
        "size": size,
        "sha256": _parse_sha256(entry.get("hash")),
        "https_url": https_url,
        "status": str(entry.get("status") or "done"),
    }


def list_remote_files(client: Any, job_id: str) -> list[dict]:
    """Fetch + validate the remote file manifest for a batch job."""
    entries = client.batch.list_files(validate_job_id(job_id)) or []
    files = [parse_file_entry(e) for e in entries]
    names = [f["filename"] for f in files]
    if len(set(names)) != len(names):
        raise ArchiveFetchError("batch file manifest contains duplicate filenames")
    return files


# ---------------------------------------------------------------------------
# Atomic manifest persistence (workspace/analysis/move_exhaustion_v2/manifests)
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _load_json(path: Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def jobs_index_path(manifests_dir: Path) -> Path:
    return Path(manifests_dir) / "jobs.json"


def file_state_path(manifests_dir: Path, job_id: str) -> Path:
    return Path(manifests_dir) / f"files_{validate_job_id(job_id)}.json"


def load_jobs_index(manifests_dir: Path) -> dict:
    data = _load_json(jobs_index_path(manifests_dir), {})
    if not isinstance(data, dict):
        raise ArchiveFetchError(f"corrupt job index: {jobs_index_path(manifests_dir)}")
    data.setdefault("jobs", {})
    return data


def record_job(
    manifests_dir: Path,
    job: Mapping[str, Any],
    batch_fp: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> None:
    """Persist a submitted job in the atomic jobs index (one-shot guard)."""
    job_id = validate_job_id(job["job_id"])
    index = load_jobs_index(manifests_dir)
    jobs = index["jobs"]
    if jobs:
        existing = next(iter(jobs))
        raise ArchiveFetchError(
            f"one-shot authorized batch already submitted as job {existing}",
        )
    record = {
        "job_id": job_id,
        "cost_usd": job["cost_usd"],
        "state": job["state"],
        "submitted_utc": _utc_now(now).isoformat(),
        "batch_fingerprint": batch_fp,
    }
    jobs[job_id] = record
    atomic_write_json(jobs_index_path(manifests_dir), index)


def active_job_id(manifests_dir: Path) -> str:
    index = load_jobs_index(manifests_dir)
    jobs = index["jobs"]
    if not jobs:
        raise ArchiveFetchError(
            f"no recorded batch job under {manifests_dir}; run 'submit' first",
        )
    if len(jobs) > 1:
        raise ArchiveFetchError("multiple recorded jobs; pass --job-id")
    return next(iter(jobs))


def recorded_batch_fingerprint(manifests_dir: Path, job_id: str) -> str:
    index = load_jobs_index(manifests_dir)
    record = index["jobs"].get(validate_job_id(job_id))
    if record is None:
        raise ArchiveFetchError(f"job {job_id} not recorded in job index")
    return str(record.get("batch_fingerprint") or "")


def update_job_state(
    manifests_dir: Path,
    job_id: str,
    state: str,
) -> None:
    job_id = validate_job_id(job_id)
    index = load_jobs_index(manifests_dir)
    record = index["jobs"].get(job_id)
    if record is None:
        raise ArchiveFetchError(f"job {job_id} not recorded in job index")
    record["state"] = state
    atomic_write_json(jobs_index_path(manifests_dir), index)


def _file_state_record(
    remote: Mapping[str, Any],
    status: str,
    *,
    error: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict:
    record = {
        "filename": remote["filename"],
        "size": remote["size"],
        "sha256": remote["sha256"],
        "status": status,
        "updated_utc": _utc_now(now).isoformat(),
    }
    if error:
        record["error"] = error
    return record


def load_file_state(manifests_dir: Path, job_id: str) -> dict[str, dict]:
    data = _load_json(file_state_path(manifests_dir, job_id), {})
    if not isinstance(data, dict):
        raise ArchiveFetchError(f"corrupt file state: {file_state_path(manifests_dir, job_id)}")
    files = data.get("files", {})
    return files if isinstance(files, dict) else {}


def save_file_state(
    manifests_dir: Path,
    job_id: str,
    files: Mapping[str, Mapping[str, Any]],
) -> None:
    job_id = validate_job_id(job_id)
    payload = {"job_id": job_id, "files": dict(files)}
    atomic_write_json(file_state_path(manifests_dir, job_id), payload)


# ---------------------------------------------------------------------------
# Databento client operations (injectable client; no secrets in messages)
# ---------------------------------------------------------------------------


def estimate_cost(client: Any, batch_cfg: Mapping[str, Any]) -> float:
    b = batch_cfg["batch"]
    cost = client.metadata.get_cost(
        dataset=b["dataset"],
        start=b["start_inclusive"],
        end=b["end_exclusive"],
        symbols=b["symbols"],
        schema=b["schema"],
        stype_in=b["stype_in"],
    )
    return parse_cost(cost)


def submit_job(
    client: Any,
    batch_cfg: Mapping[str, Any],
    *,
    max_cost: float = MAX_AUTHORIZED_COST_USD,
) -> dict:
    """Submit exactly the frozen authorized batch; refuse any nonzero cost."""
    b = batch_cfg["batch"]
    validate_batch_config(b)

    estimated = estimate_cost(client, batch_cfg)
    if estimated > max_cost:
        raise CostExceededError(
            f"refusing submit: estimated cost ${estimated:.4f} exceeds "
            f"allowed ${max_cost:.4f}; authorized batch is $0",
        )

    response = client.batch.submit_job(
        dataset=b["dataset"],
        symbols=b["symbols"],
        schema=b["schema"],
        start=b["start_inclusive"],
        end=b["end_exclusive"],
        encoding=b["encoding"],
        compression=b["compression"],
        split_duration=b["split_duration"],
        stype_in=b["stype_in"],
        stype_out="instrument_id",
        delivery="download",
        map_symbols=True,
        pretty_px=False,
        pretty_ts=False,
    )
    job = parse_job(response, require_cost=True)
    if job["cost_usd"] is not None and job["cost_usd"] > max_cost:
        # The job may already exist server-side; we fail closed and never
        # record or download it.
        raise CostExceededError(
            f"job {job['job_id']} was created at cost ${job['cost_usd']:.4f} "
            f"> allowed ${max_cost:.4f}; do not download, contact Databento",
        )
    return job


def get_job_status(client: Any, job_id: str) -> dict:
    """Look up a job's current state via batch.list_jobs."""
    job_id = validate_job_id(job_id)
    jobs = client.batch.list_jobs(states="queued,processing,done,expired") or []
    for entry in jobs:
        parsed = parse_job(entry, require_cost=False)
        if parsed["job_id"] == job_id:
            return parsed
    raise ArchiveFetchError(f"job {job_id} not found via batch.list_jobs")


# ---------------------------------------------------------------------------
# Download / verify (per-file resume via size+SHA256, fail closed)
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_matches(remote: Mapping[str, Any], path: Path) -> bool:
    """True only when size and SHA256 both match the remote manifest."""
    try:
        if Path(path).stat().st_size != int(remote["size"]):
            return False
        expected = remote.get("sha256")
        if expected is None:
            expected = _parse_sha256(remote.get("hash"))
        return sha256_file(path) == expected
    except (OSError, TypeError, ValueError):
        return False


def check_headroom(
    download_root: Path,
    needed_bytes: int,
    *,
    headroom: float = DEFAULT_HEADROOM,
    disk_usage: Callable[[str], Any] | None = None,
) -> None:
    """Require that free space minus headroom covers the projected footprint."""
    if not 0.0 <= headroom < 1.0:
        raise HeadroomError(f"invalid headroom fraction: {headroom!r}")
    if needed_bytes <= 0:
        return
    root = str(download_root)
    usage = disk_usage(root) if disk_usage is not None else shutil.disk_usage(root)
    free = float(usage.free)
    total = float(usage.total)
    max_usable = free * (1.0 - headroom)
    if needed_bytes > max_usable:
        raise HeadroomError(
            f"insufficient free space on {root}: need {needed_bytes} bytes, "
            f"only {max_usable:.0f} usable after {headroom:.0%} headroom "
            f"(free {free:.0f} / total {total:.0f})",
        )


def http_fetch(
    url: str,
    dest: Path,
    *,
    resume_from: int = 0,
    chunk_size: int = 1 << 20,
    api_key: str | None = None,
) -> None:
    """Stream an authenticated Databento URL; append via Range when resuming."""
    if not api_key:
        raise ApiKeyError("authenticated batch download requires an API key")
    dest = Path(dest)
    if resume_from > 0:
        if not dest.exists() or dest.stat().st_size != resume_from:
            raise ArchiveFetchError(
                "partial file size mismatch; restart this file's download",
            )
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {token}"}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            partial = resume_from > 0 and getattr(response, "status", 200) == 206
            mode = "ab" if partial else "wb"
            with open(dest, mode) as fh:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
    except urllib.error.HTTPError as exc:
        if resume_from > 0 and exc.code == 416:
            dest.unlink(missing_ok=True)
            http_fetch(url, dest, resume_from=0, chunk_size=chunk_size)
            return
        raise


def _quarantine(target: Path, now: Callable[[], datetime] | None = None) -> Path:
    """Atomically move a corrupt/legacy file aside (recoverable, never input)."""
    slug = _utc_slug(now)
    dst = target.with_name(f"{target.name}.corrupt-{slug}")
    n = 1
    while dst.exists():
        dst = target.with_name(f"{target.name}.corrupt-{slug}-{n}")
        n += 1
    os.replace(target, dst)
    return dst


def download_one_file(
    remote: Mapping[str, Any],
    job_dir: Path,
    *,
    fetch_file: Callable[..., Any] = http_fetch,
    disk_usage: Callable[[str], Any] | None = None,
    headroom: float = DEFAULT_HEADROOM,
    now: Callable[[], datetime] | None = None,
) -> str:
    """Download one remote file with resume; returns 'skipped' or 'downloaded'."""
    target = Path(job_dir) / remote["filename"]
    if file_matches(remote, target):
        return "skipped"
    if target.exists():
        _quarantine(target, now=now)
    check_headroom(
        job_dir,
        remote["size"],
        headroom=headroom,
        disk_usage=disk_usage,
    )
    part = target.with_name(target.name + ".part")
    resume_from = part.stat().st_size if part.exists() else 0
    if resume_from >= remote["size"]:
        part.unlink(missing_ok=True)
        resume_from = 0
    fetch_file(remote["https_url"], part, resume_from=resume_from)
    if part.stat().st_size != remote["size"]:
        part.unlink(missing_ok=True)
        raise ChecksumError(
            f"download size mismatch for {remote['filename']}; .part discarded",
        )
    if sha256_file(part) != remote["sha256"]:
        part.unlink(missing_ok=True)
        raise ChecksumError(
            f"SHA-256 mismatch for {remote['filename']}; .part discarded",
        )
    os.replace(part, target)
    return "downloaded"


def download_job(
    client: Any,
    job_id: str,
    download_root: Path,
    manifests_dir: Path,
    *,
    fetch_file: Callable[..., Any] = http_fetch,
    disk_usage: Callable[[str], Any] | None = None,
    headroom: float = DEFAULT_HEADROOM,
    now: Callable[[], datetime] | None = None,
) -> dict:
    """Download all remote files for a done job, verifying each before commit."""
    job_id = validate_job_id(job_id)
    job_dir = Path(download_root) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    remote_files = list_remote_files(client, job_id)
    state = load_file_state(manifests_dir, job_id)

    needed = sum(
        r["size"]
        for r in remote_files
        if not file_matches(r, job_dir / r["filename"])
    )
    check_headroom(
        job_dir,
        needed,
        headroom=headroom,
        disk_usage=disk_usage,
    )

    report = {
        "job_id": job_id,
        "files": [],
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
    }
    for remote in sorted(remote_files, key=lambda r: r["filename"]):
        try:
            status = download_one_file(
                remote,
                job_dir,
                fetch_file=fetch_file,
                disk_usage=disk_usage,
                headroom=headroom,
                now=now,
            )
        except Exception as exc:
            state[remote["filename"]] = _file_state_record(
                remote,
                "failed",
                error=str(exc),
                now=now,
            )
            save_file_state(manifests_dir, job_id, state)
            report["failed"] += 1
            raise
        state[remote["filename"]] = _file_state_record(remote, "verified", now=now)
        save_file_state(manifests_dir, job_id, state)
        report["files"].append({"filename": remote["filename"], "status": status})
        if status == "downloaded":
            report["downloaded"] += 1
        else:
            report["skipped"] += 1
    return report


def verify_job(
    client: Any,
    job_id: str,
    download_root: Path,
    manifests_dir: Path,
    *,
    quarantine: bool = True,
    now: Callable[[], datetime] | None = None,
) -> dict:
    """Verify local files against list_files; quarantine anything mismatched."""
    job_id = validate_job_id(job_id)
    job_dir = Path(download_root) / job_id
    remote_files = list_remote_files(client, job_id)
    state = load_file_state(manifests_dir, job_id)
    remote_names = {r["filename"] for r in remote_files}

    report = {
        "job_id": job_id,
        "verified": [],
        "invalid": [],
        "missing": [],
        "extraneous": [],
    }
    for remote in sorted(remote_files, key=lambda r: r["filename"]):
        target = job_dir / remote["filename"]
        if not target.exists():
            state[remote["filename"]] = _file_state_record(remote, "pending", now=now)
            report["missing"].append(remote["filename"])
            continue
        if file_matches(remote, target):
            state[remote["filename"]] = _file_state_record(remote, "verified", now=now)
            report["verified"].append(remote["filename"])
            continue
        quarantine_path = None
        if quarantine:
            quarantine_path = str(_quarantine(target, now=now))
        state[remote["filename"]] = _file_state_record(
            remote,
            "failed",
            error="size or SHA-256 mismatch with remote manifest",
            now=now,
        )
        report["invalid"].append(
            {"filename": remote["filename"], "quarantined_to": quarantine_path},
        )

    if job_dir.exists():
        report["extraneous"] = sorted(
            p.name
            for p in job_dir.iterdir()
            if p.is_file()
            and p.name not in remote_names
            and not p.name.endswith(".part")
            and ".corrupt-" not in p.name
        )
    save_file_state(manifests_dir, job_id, state)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_client(api_key: str) -> Any:
    import databento as db

    return db.Historical(key=api_key)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="frozen data_store.yaml path",
    )
    parser.add_argument(
        "--keys-env",
        default=str(DEFAULT_ENV_PATH),
        help="env file containing DATABENTO_API_KEY",
    )
    parser.add_argument(
        "--manifests-dir",
        default=str(DEFAULT_MANIFESTS_DIR),
        help="atomic job/file state directory (default: workspace/analysis/.../manifests)",
    )
    parser.add_argument(
        "--download-root",
        default=None,
        help="base directory for per-job downloads (default: frozen archive root)",
    )


def _add_job_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--job-id",
        default=None,
        help="batch job id (default: the one recorded job)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archive_fetch.py",
        description="Resumable Databento batch-archive acquisition for Move Exhaustion V2 (X0).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("estimate", help="estimate batch cost (USD)")
    _add_common_args(p)

    p = sub.add_parser("submit", help="submit the frozen authorized $0 batch (one-shot)")
    _add_common_args(p)

    p = sub.add_parser("status", help="show job state and remote file counts")
    _add_common_args(p)
    _add_job_arg(p)

    p = sub.add_parser("download", help="download (resume) all job files")
    _add_common_args(p)
    _add_job_arg(p)
    p.add_argument(
        "--headroom",
        type=float,
        default=DEFAULT_HEADROOM,
        help="minimum fraction of the destination drive kept free (default 0.20)",
    )

    p = sub.add_parser("verify", help="verify local files vs remote manifest")
    _add_common_args(p)
    _add_job_arg(p)
    p.add_argument(
        "--no-quarantine",
        action="store_true",
        help="report mismatches without moving them aside",
    )
    return parser


def _exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, (CostExceededError, ConfigError, HeadroomError, JobStateError)):
        return 2
    return 1


def _refresh_pending_file_state(
    manifests_dir: Path,
    job_id: str,
    remote_files: Sequence[Mapping[str, Any]],
) -> None:
    state = load_file_state(manifests_dir, job_id)
    changed = False
    for remote in remote_files:
        name = remote["filename"]
        if name not in state or state[name].get("status") not in {"verified", "failed"}:
            state[name] = _file_state_record(remote, "pending")
            changed = True
    if changed:
        save_file_state(manifests_dir, job_id, state)


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[str], Any] | None = None,
    disk_usage: Callable[[str], Any] | None = None,
    fetch_file: Callable[..., Any] = http_fetch,
    now: Callable[[], datetime] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command
    try:
        if command == "estimate":
            cfg = load_batch_config(args.config)
            client = (client_factory or _build_client)(load_api_key(args.keys_env))
            cost = estimate_cost(client, cfg)
            print(f"estimated_cost_usd={cost:.4f}")
            if cost > MAX_AUTHORIZED_COST_USD:
                print(
                    "WARNING: cost is nonzero; the authorized batch is $0 and submit will refuse.",
                    file=sys.stderr,
                )
            return 0

        if command == "submit":
            cfg = load_batch_config(args.config)
            manifests = Path(args.manifests_dir)
            index = load_jobs_index(manifests)
            if index["jobs"]:
                existing = next(iter(index["jobs"]))
                print(
                    f"Refusing: one-shot authorized batch already submitted as job {existing}",
                    file=sys.stderr,
                )
                return 2
            client = (client_factory or _build_client)(load_api_key(args.keys_env))
            job = submit_job(client, cfg)
            record_job(
                manifests,
                job,
                batch_fingerprint(cfg["batch"]),
                now=now,
            )
            print(f"submitted job_id={job['job_id']} cost_usd={job['cost_usd']:.4f}")
            return 0

        if command == "status":
            manifests = Path(args.manifests_dir)
            job_id = args.job_id or active_job_id(manifests)
            api_key = load_api_key(args.keys_env)
            client = (client_factory or _build_client)(api_key)
            parsed = get_job_status(client, job_id)
            update_job_state(manifests, job_id, parsed["state"])
            remote_files = (
                list_remote_files(client, job_id)
                if parsed["state"] == "done"
                else []
            )
            _refresh_pending_file_state(manifests, job_id, remote_files)
            print(f"job_id={job_id}")
            print(f"state={parsed['state']}")
            print(f"cost_usd={parsed['cost_usd'] if parsed['cost_usd'] is not None else 'n/a'}")
            print(f"remote_files={len(remote_files)}")
            if remote_files:
                by_status: dict[str, int] = {}
                for f in remote_files:
                    by_status[f["status"]] = by_status.get(f["status"], 0) + 1
                print("remote_file_status=" + ",".join(f"{k}:{v}" for k, v in sorted(by_status.items())))
            return 0

        if command == "download":
            cfg = load_batch_config(args.config)
            manifests = Path(args.manifests_dir)
            job_id = args.job_id or active_job_id(manifests)
            if recorded_batch_fingerprint(manifests, job_id) != batch_fingerprint(cfg["batch"]):
                print(
                    "Refusing: frozen batch config changed since job submission.",
                    file=sys.stderr,
                )
                return 2
            api_key = load_api_key(args.keys_env)
            client = (client_factory or _build_client)(api_key)
            parsed = get_job_status(client, job_id)
            if parsed["state"] != "done":
                print(
                    f"Refusing: job {job_id} state is {parsed['state']}; download requires 'done'.",
                    file=sys.stderr,
                )
                return 2
            root = Path(args.download_root or cfg["archive_root"])
            effective_fetch = fetch_file
            if fetch_file is http_fetch:
                effective_fetch = lambda url, dest, resume_from=0: http_fetch(
                    url,
                    dest,
                    resume_from=resume_from,
                    api_key=api_key,
                )
            report = download_job(
                client,
                job_id,
                root,
                manifests,
                fetch_file=effective_fetch,
                disk_usage=disk_usage,
                headroom=args.headroom,
                now=now,
            )
            print(f"downloaded={report['downloaded']} skipped={report['skipped']}")
            for item in report["files"]:
                print(f"  {item['status']:>10} {item['filename']}")
            return 0

        if command == "verify":
            cfg = load_batch_config(args.config)
            manifests = Path(args.manifests_dir)
            job_id = args.job_id or active_job_id(manifests)
            if recorded_batch_fingerprint(manifests, job_id) != batch_fingerprint(cfg["batch"]):
                print(
                    "Refusing: frozen batch config changed since job submission.",
                    file=sys.stderr,
                )
                return 2
            client = (client_factory or _build_client)(load_api_key(args.keys_env))
            root = Path(args.download_root or cfg["archive_root"])
            report = verify_job(
                client,
                job_id,
                root,
                manifests,
                quarantine=not args.no_quarantine,
                now=now,
            )
            print(
                f"verified={len(report['verified'])} "
                f"invalid={len(report['invalid'])} "
                f"missing={len(report['missing'])} "
                f"extraneous={len(report['extraneous'])}"
            )
            for name in report["missing"]:
                print(f"  MISSING   {name}")
            for item in report["invalid"]:
                print(
                    f"  INVALID   {item['filename']} "
                    f"quarantined_to={item['quarantined_to']}",
                )
            for name in report["extraneous"]:
                print(f"  EXTRANEOUS {name}")
            if report["invalid"] or report["missing"]:
                return 1
            return 0

        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    except ArchiveFetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _exit_code_for(exc)
    except Exception as exc:  # network/IO surprises: fail closed
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
