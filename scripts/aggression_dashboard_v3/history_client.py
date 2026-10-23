#!/usr/bin/env python3
"""5053 side of V3 history: worker processes, the shared response cache and the market-closed scheduler.

No history computation runs in the live process. It queues jobs for below-normal-priority
worker processes and relays their compressed bytes.
"""

from __future__ import annotations

import heapq
import itertools
import json
import logging
import os
import subprocess
import sys
import threading
import time as time_module
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from scripts.aggression_dashboard_v3.core import parse_dashboard_datetime
from scripts.aggression_dashboard_v3.history_store import HISTORY_VIEWS

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_MODULE = "scripts.aggression_dashboard_v3.history_worker"
# Raise to 2 if many viewers request different ranges at once; each worker is its own process.
HISTORY_SERVE_WORKERS = 1
SERVE_TIMEOUT_SEC = 60.0
SERVE_WAIT_SEC = 90.0
BUILD_TIMEOUT_SEC = 900.0
IDLE_STOP_SEC = 15 * 60.0
SCHEDULER_INTERVAL_SEC = 300.0
SCHEDULER_STARTUP_DELAY_SEC = 60.0
SESSIONS_TTL_SEC = 300.0
RESPONSE_CACHE_ENTRIES = 48
PRIORITY_SERVE = 0
PRIORITY_BUILD = 1
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000


class HistoryWorkerError(RuntimeError):
    """A history job failed. code is 'range' (bad request), 'busy', 'timeout', 'crash' or 'error'."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def worker_command() -> List[str]:
    return [sys.executable, "-m", WORKER_MODULE]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            # A venv python.exe is a launcher; the interpreter doing the work is its child.
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def _read_exact(stream: Any, length: int) -> bytes:
    chunks: List[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise HistoryWorkerError("History worker closed its output mid-reply.", "crash")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class WorkerProcess:
    """One below-normal-priority worker subprocess, sent one job at a time by its slot thread."""

    def __init__(
        self,
        command: Sequence[str],
        cwd: Path = PROJECT_ROOT,
        env: Optional[Dict[str, str]] = None,
    ):
        self.command = list(command)
        self.cwd = cwd
        self.env = env
        self.proc: Optional[subprocess.Popen] = None
        self.last_used = time_module.monotonic()
        self.starts = 0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _spawn(self) -> None:
        kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
            **kwargs,
        )
        self.starts += 1
        logger.info("V3 history worker started (pid %s)", self.proc.pid)

    def request(self, job: Dict[str, Any], timeout: float) -> Tuple[Dict[str, Any], bytes]:
        if not self.alive():
            self._spawn()
        proc = self.proc
        self.last_used = time_module.monotonic()
        result: Dict[str, Any] = {}

        def exchange() -> None:
            try:
                proc.stdin.write(json.dumps(job).encode("utf-8") + b"\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    raise HistoryWorkerError("History worker exited.", "crash")
                header = json.loads(line)
                result["reply"] = (header, _read_exact(proc.stdout, int(header.get("length") or 0)))
            except HistoryWorkerError as exc:
                result["error"] = exc
            except (OSError, ValueError) as exc:
                result["error"] = HistoryWorkerError(f"History worker pipe failed: {exc}", "crash")

        thread = threading.Thread(target=exchange, name="v3-history-io", daemon=True)
        thread.start()
        thread.join(timeout)
        self.last_used = time_module.monotonic()
        if thread.is_alive():
            self.kill()
            raise HistoryWorkerError(f"History job timed out after {timeout:.0f} s.", "timeout")
        if "error" in result:
            self.kill()
            raise result["error"]
        return result["reply"]

    def kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        _kill_tree(proc)
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def stop(self) -> None:
        """Close stdin so the worker exits on its own; kill it if it does not."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            _kill_tree(proc)
        try:
            proc.stdout.close()
        except OSError:
            pass
        logger.info("V3 history worker stopped (pid %s)", proc.pid)


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    job: Dict[str, Any] = field(compare=False)
    timeout: float = field(compare=False)
    future: Future = field(compare=False)


class HistoryPool:
    """Worker slots sharing one priority queue, so serve jobs run ahead of queued builds."""

    def __init__(
        self,
        workers: int = HISTORY_SERVE_WORKERS,
        *,
        command: Optional[Sequence[str]] = None,
        cwd: Path = PROJECT_ROOT,
        env: Optional[Dict[str, str]] = None,
        idle_stop_sec: float = IDLE_STOP_SEC,
    ):
        self.idle_stop_sec = idle_stop_sec
        self.slots = [WorkerProcess(command or worker_command(), cwd, env) for _ in range(max(1, int(workers)))]
        self._queue: List[_Job] = []
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._ids = itertools.count(1)
        self._threads: List[threading.Thread] = []
        self._running = 0
        self._closed = False

    def start(self) -> None:
        with self._cv:
            if self._threads:
                return
            for index, slot in enumerate(self.slots):
                thread = threading.Thread(target=self._run, args=(slot,), name=f"v3-history-slot-{index}", daemon=True)
                self._threads.append(thread)
                thread.start()

    def submit(self, job: Dict[str, Any], *, priority: int, timeout: float) -> Future:
        self.start()
        future: Future = Future()
        with self._cv:
            if self._closed:
                raise HistoryWorkerError("History service is shutting down.", "busy")
            heapq.heappush(self._queue, _Job(priority, next(self._seq), {**job, "id": next(self._ids)}, timeout, future))
            self._cv.notify()
        return future

    def call(self, job: Dict[str, Any], *, priority: int, timeout: float, wait: Optional[float] = None) -> Tuple[Dict[str, Any], bytes]:
        future = self.submit(job, priority=priority, timeout=timeout)
        try:
            return future.result(timeout=wait)
        except FutureTimeout as exc:
            raise HistoryWorkerError("History is busy; try again shortly.", "busy") from exc

    def _run(self, slot: WorkerProcess) -> None:
        while True:
            with self._cv:
                if not self._queue and not self._closed:
                    self._cv.wait(timeout=30.0)
                if self._closed:
                    break
                item = heapq.heappop(self._queue) if self._queue else None
                if item is not None:
                    self._running += 1
            if item is None:
                if slot.alive() and time_module.monotonic() - slot.last_used >= self.idle_stop_sec:
                    logger.info("V3 history worker idle for %.0f minutes; stopping it", self.idle_stop_sec / 60)
                    slot.stop()
                continue
            try:
                if item.future.set_running_or_notify_cancel():
                    try:
                        item.future.set_result(self._execute(slot, item))
                    except BaseException as exc:
                        item.future.set_exception(exc)
            finally:
                with self._cv:
                    self._running -= 1
        slot.stop()

    def _execute(self, slot: WorkerProcess, item: _Job) -> Tuple[Dict[str, Any], bytes]:
        attempts = 2 if item.priority == PRIORITY_SERVE else 1
        for attempt in range(attempts):
            try:
                header, body = slot.request(item.job, item.timeout)
            except HistoryWorkerError as exc:
                if exc.code == "crash" and attempt + 1 < attempts:
                    logger.warning("V3 history worker crashed; restarting it for the serve job")
                    continue
                raise
            if not header.get("ok"):
                raise HistoryWorkerError(header.get("error") or "History job failed.", header.get("code") or "error")
            return header, body
        raise HistoryWorkerError("History job failed.")

    def queue_depth(self) -> int:
        with self._cv:
            return len(self._queue)

    def running(self) -> int:
        with self._cv:
            return self._running

    def workers_alive(self) -> int:
        return sum(1 for slot in self.slots if slot.alive())

    def close(self) -> None:
        with self._cv:
            self._closed = True
            pending, self._queue = self._queue, []
            self._cv.notify_all()
        for item in pending:
            item.future.cancel()
        for thread in self._threads:
            thread.join(timeout=15)


class ResponseCache:
    """Recent gzip history responses shared by every viewer."""

    def __init__(self, max_entries: int = RESPONSE_CACHE_ENTRIES):
        self.max_entries = max_entries
        self._entries: "OrderedDict[tuple, Tuple[str, bytes]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> Optional[Tuple[str, bytes]]:
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return hit

    def put(self, key: tuple, etag: str, body: bytes) -> None:
        with self._lock:
            self._entries[key] = (etag, body)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": sum(len(body) for _etag, body in self._entries.values()),
                "hits": self.hits,
                "misses": self.misses,
            }


class HistoryService:
    """History requests from 5053: shared cache first, then one worker job per unique range."""

    def __init__(
        self,
        pool: HistoryPool,
        cache: Optional[ResponseCache] = None,
        parse: Callable[[str], datetime] = parse_dashboard_datetime,
    ):
        self.pool = pool
        self.cache = cache or ResponseCache()
        self._parse = parse
        self._lock = threading.Lock()
        self._inflight: Dict[tuple, Future] = {}
        self._sessions: Dict[str, Tuple[float, bytes]] = {}
        self.building: Optional[str] = None
        self.last_build: Optional[Dict[str, Any]] = None

    @staticmethod
    def cache_key(view: str, start: datetime, end: datetime) -> tuple:
        return (
            view,
            start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end.astimezone(timezone.utc).isoformat(timespec="seconds"),
        )

    def _settle(self, key: tuple, future: Future) -> None:
        with self._lock:
            self._inflight.pop(key, None)
        if future.cancelled() or future.exception() is not None:
            return
        header, body = future.result()
        self.cache.put(key, header["etag"], body)

    def serve(self, view: str, start: datetime, end: datetime) -> Tuple[str, bytes, bool]:
        """(etag, gzip JSON body, served from the shared cache)."""
        key = self.cache_key(view, start, end)
        hit = self.cache.get(key)
        if hit is not None:
            return hit[0], hit[1], True
        created = False
        with self._lock:
            future = self._inflight.get(key)
            if future is None:
                future = self.pool.submit(
                    {"job": "serve", "view": view, "start": key[1], "end": key[2]},
                    priority=PRIORITY_SERVE,
                    timeout=SERVE_TIMEOUT_SEC,
                )
                self._inflight[key] = future
                created = True
        if created:
            future.add_done_callback(lambda done, key=key: self._settle(key, done))
        try:
            header, body = future.result(timeout=SERVE_WAIT_SEC)
        except FutureTimeout as exc:
            raise HistoryWorkerError("History is busy building a session; try again shortly.", "busy") from exc
        return header["etag"], body, False

    def sessions(self, view: str) -> bytes:
        now = time_module.monotonic()
        hit = self._sessions.get(view)
        if hit is not None and now - hit[0] < SESSIONS_TTL_SEC:
            return hit[1]
        _header, body = self.pool.call(
            {"job": "sessions", "view": view},
            priority=PRIORITY_SERVE,
            timeout=SERVE_TIMEOUT_SEC,
            wait=SERVE_WAIT_SEC,
        )
        self._sessions[view] = (now, body)
        return body

    def warm(self) -> None:
        """Start a worker in the background (a cold start takes ~15 s) so the next unique range is quick."""
        if not self.pool.workers_alive() and not self.pool.queue_depth() and not self.pool.running():
            self.pool.submit({"job": "ping"}, priority=PRIORITY_SERVE, timeout=SERVE_TIMEOUT_SEC)

    def invalidate(self) -> None:
        self._sessions.clear()
        self.cache.clear()

    def missing(self) -> Dict[str, List[str]]:
        header, _body = self.pool.call({"job": "missing"}, priority=PRIORITY_BUILD, timeout=SERVE_TIMEOUT_SEC)
        return header.get("missing") or {}

    def build(self, view: str, session: str) -> Dict[str, Any]:
        self.building = f"{view} {session}"
        try:
            header, _body = self.pool.call(
                {"job": "build", "view": view, "session": session},
                priority=PRIORITY_BUILD,
                timeout=BUILD_TIMEOUT_SEC,
            )
        finally:
            self.building = None
        self.last_build = {**header, "view": view, "session": session, "at": datetime.now(timezone.utc).isoformat()}
        return header

    def prewarm(self) -> int:
        """Render the last-session and last-5-session ranges of both views into the shared cache."""
        warmed = 0
        for view in HISTORY_VIEWS:
            try:
                ranges = json.loads(self.sessions(view)).get("quick_ranges") or {}
                for choice in ranges.values():
                    if choice:
                        self.serve(view, self._parse(choice["start"]), self._parse(choice["end"]))
                        warmed += 1
            except Exception:
                logger.exception("V3 history pre-warm failed for %s", view)
        return warmed

    def status(self) -> Dict[str, Any]:
        return {
            "queue": self.pool.queue_depth(),
            "running": self.pool.running(),
            "workers": len(self.pool.slots),
            "workers_alive": self.pool.workers_alive(),
            "worker_starts": sum(slot.starts for slot in self.pool.slots),
            "cache": self.cache.stats(),
            "building": self.building,
            "last_build": self.last_build,
        }


class HistoryScheduler:
    """Every few minutes: while the market is closed, build missing sessions one at a time, then pre-warm."""

    def __init__(
        self,
        service: HistoryService,
        market_open: Callable[[datetime], bool],
        *,
        interval: float = SCHEDULER_INTERVAL_SEC,
        startup_delay: float = SCHEDULER_STARTUP_DELAY_SEC,
        clock: Optional[Callable[[], datetime]] = None,
        prepare: Optional[Callable[[], None]] = None,
    ):
        self.service = service
        self.market_open = market_open
        self.interval = interval
        self.startup_delay = startup_delay
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.prepare = prepare
        self.last_run: Optional[Dict[str, Any]] = None
        self._prewarmed = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="v3-history-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(self.startup_delay):
            return
        while not self._stop.is_set():
            try:
                self.last_run = self.run_once()
            except Exception:
                logger.exception("V3 history scheduler run failed")
            self._stop.wait(self.interval)

    def run_once(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"at": self.clock().isoformat(), "built": 0, "failed": 0, "skipped": None}
        if self.market_open(self.clock()):
            summary["skipped"] = "market open"
        else:
            if self.prepare is not None:
                try:
                    self.prepare()
                except Exception:
                    logger.exception("V3 history close snapshot failed")
            for view, sessions in self.service.missing().items():
                for session in sessions:
                    if self._stop.is_set() or self.market_open(self.clock()):
                        summary["skipped"] = "market opened"
                        break
                    try:
                        result = self.service.build(view, session)
                    except HistoryWorkerError as exc:
                        logger.warning("V3 history build %s %s failed: %s", view, session, exc)
                        summary["failed"] += 1
                        continue
                    summary["built" if result.get("built") else "failed"] += 1
        changed = summary["built"] + summary["failed"] > 0
        if changed:
            self.service.invalidate()
        if changed or not self._prewarmed:
            summary["prewarmed"] = self.service.prewarm()
            self._prewarmed = True
        if changed:
            logger.info("V3 history scheduler: %s built, %s failed", summary["built"], summary["failed"])
        return summary


def build_history_service(
    market_open: Callable[[datetime], bool],
    workers: int = HISTORY_SERVE_WORKERS,
    prepare: Optional[Callable[[], None]] = None,
) -> Tuple[HistoryService, HistoryScheduler]:
    service = HistoryService(HistoryPool(workers))
    return service, HistoryScheduler(service, market_open, prepare=prepare)
