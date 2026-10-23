#!/usr/bin/env python3
"""V3 history worker process.

Protocol: one JSON job per stdin line. Each reply is one JSON header line on stdout, followed by
`length` body bytes. Everything else (prints, logging) goes to stderr. Exits when stdin closes.
The 5053 client orders the jobs (serve ahead of build) and sends one at a time.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import os
import sys
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard_v3 import history_store  # noqa: E402

logger = logging.getLogger("v3.history_worker")


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def encode_json(payload: Any) -> bytes:
    try:
        return json.dumps(payload, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
    except ValueError:
        return json.dumps(_finite(payload), separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")


def encode_body(payload: Any) -> Tuple[bytes, str]:
    """gzip body and a content ETag of the uncompressed JSON."""
    raw = encode_json(payload)
    return gzip.compress(raw, compresslevel=6, mtime=0), hashlib.sha1(raw).hexdigest()[:20]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value))


class HistoryWorker:
    def __init__(self, root: Path | None = None):
        self.root = root
        self.frames = history_store.SessionFrameCache()
        self.slopes: Dict[str, history_store.SlopeCache] = {}

    def _slope_cache(self, view: str) -> history_store.SlopeCache:
        cache = self.slopes.get(view)
        if cache is None:
            cache = self.slopes[view] = history_store.SlopeCache(view, self.root)
        else:
            cache.reload()
        return cache

    def handle(self, job: Dict[str, Any]) -> Tuple[Dict[str, Any], bytes]:
        kind = job.get("job")
        if kind == "ping":
            return {"pid": os.getpid()}, b""
        if kind == "serve":
            payload = history_store.serve_history(
                job["view"],
                _parse(job["start"]),
                _parse(job["end"]),
                frame_cache=self.frames,
                root=self.root,
            )
            body, etag = encode_body(payload)
            return {
                "etag": etag,
                "sessions": len(payload.get("sessions") or []),
                "rows": (payload.get("resolution") or {}).get("rows", 0),
            }, body
        if kind == "build":
            view = job["view"]
            meta = history_store.build_session(
                view,
                _parse(job["session"]),
                slopes=self._slope_cache(view),
                root=self.root,
            )
            return {
                "built": bool(meta.get("ok")),
                "label": meta.get("label"),
                "reason": meta.get("reason"),
                "rows": meta.get("rows"),
                "warnings": len(meta.get("warnings") or []),
            }, b""
        if kind == "missing":
            return {
                "missing": {
                    view: [start.isoformat() for start in history_store.missing_sessions(view, root=self.root)]
                    for view in history_store.HISTORY_VIEWS
                }
            }, b""
        if kind == "sessions":
            view = job["view"]
            sessions = history_store.list_sessions(view, root=self.root)
            body = encode_json({
                "ok": True,
                "view": view,
                "view_label": history_store.view_label(view),
                "sessions": sessions,
                "quick_ranges": history_store.quick_ranges(sessions),
            })
            return {}, body
        raise ValueError(f"Unknown history job: {kind!r}")


def serve_forever(worker: HistoryWorker, source: BinaryIO, out: BinaryIO) -> None:
    for line in source:
        line = line.strip()
        if not line:
            continue
        started = time_module.perf_counter()
        job: Dict[str, Any] = {}
        try:
            job = json.loads(line)
            header, body = worker.handle(job)
            header["ok"] = True
        except history_store.HistoryRangeError as exc:
            header, body = {"ok": False, "code": "range", "error": str(exc)}, b""
        except Exception as exc:
            logger.exception("V3 history job failed: %s", job.get("job"))
            header, body = {"ok": False, "code": "error", "error": f"{type(exc).__name__}: {exc}"}, b""
        header["id"] = job.get("id")
        header["length"] = len(body)
        header["elapsed_ms"] = round(1000 * (time_module.perf_counter() - started))
        out.write(json.dumps(header).encode("utf-8") + b"\n")
        if body:
            out.write(body)
        out.flush()


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s | %(levelname)-7s | [history-worker] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    else:
        os.nice(5)
    root = os.environ.get("V3_HISTORY_ROOT")
    serve_forever(HistoryWorker(Path(root) if root else None), sys.stdin.buffer, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
