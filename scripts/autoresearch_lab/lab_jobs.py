"""In-memory background job registry for exploratory sandbox runs."""

from __future__ import annotations

import threading
import traceback
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from .lab_journal import append_search
from .lab_sandbox import run_sandbox

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def submit_job(payload: Mapping[str, Any]) -> dict[str, Any]:
    if str(payload.get("split") or "discovery") != "discovery":
        raise PermissionError("only discovery sandbox jobs can be submitted")
    job_id = uuid.uuid4().hex
    record = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "percent": 0,
        "submitted_at": _utc_now(),
        "started_at": None,
        "finished_at": None,
        "payload": deepcopy(dict(payload)),
        "result": None,
        "error": None,
    }
    with _LOCK:
        _JOBS[job_id] = record
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return public_job(job_id)


def _update(job_id: str, **values: Any) -> None:
    with _LOCK:
        _JOBS[job_id].update(values)


def _run(job_id: str) -> None:
    with _LOCK:
        payload = deepcopy(_JOBS[job_id]["payload"])
    _update(job_id, status="running", stage="starting", started_at=_utc_now())

    def progress(stage: str, percent: int) -> None:
        _update(job_id, stage=stage, percent=max(0, min(99, int(percent))))

    try:
        result = run_sandbox(payload, progress=progress)
        append_search(
            job_id=job_id,
            payload=payload,
            status="completed",
            result=result,
        )
        _update(
            job_id,
            status="completed",
            stage="completed",
            percent=100,
            result=result,
            finished_at=_utc_now(),
        )
    except Exception as exc:
        try:
            append_search(
                job_id=job_id,
                payload=payload,
                status="failed",
                error=str(exc),
            )
        except Exception:
            pass
        _update(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc),
            traceback=traceback.format_exc()[-4000:],
            finished_at=_utc_now(),
        )


def public_job(job_id: str) -> dict[str, Any]:
    with _LOCK:
        if job_id not in _JOBS:
            raise KeyError(job_id)
        return deepcopy(_JOBS[job_id])


def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        jobs = [deepcopy(value) for value in _JOBS.values()]
    return sorted(jobs, key=lambda item: item["submitted_at"], reverse=True)
