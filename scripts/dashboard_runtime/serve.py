"""Serving, logging and response caching shared by 5058/5063/5064/5065.

`serve` runs waitress with a fixed thread pool (or Flask's dev server when asked), request logs
stay at WARNING, application logs rotate under %LOCALAPPDATA%\\market_data_puller\\logs, and slow
requests get their own log. `ResponseCache` computes one JSON body per (key, data tick) with a
single build at a time, serves the last good body while a rebuild runs or fails, and keeps it
pre-gzipped with an ETag.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Hashable, Optional

WAITRESS_THREADS = 16
WAITRESS_CONNECTION_LIMIT = 500
WAITRESS_CHANNEL_TIMEOUT = 120
SLOW_REQUEST_MS = 1000
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5


def log_dir() -> Path:
    base = os.environ.get("MDP_LOG_DIR") or str(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "market_data_puller" / "logs")
    return Path(base)


def configure_logging(app_name: str, *, level: int = logging.INFO) -> Path:
    """Rotating file log for the app; werkzeug/waitress request chatter only at WARNING."""
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{app_name}.log"
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(path) for h in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("werkzeug", "waitress.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    slow = logging.getLogger("slow_requests")
    if not slow.handlers:
        slow_handler = RotatingFileHandler(folder / f"{app_name}.slow.log", maxBytes=LOG_MAX_BYTES, backupCount=2, encoding="utf-8")
        slow_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        slow.addHandler(slow_handler)
        slow.propagate = False
    return path


def install_request_timing(app, *, slow_ms: int = SLOW_REQUEST_MS) -> None:
    """Log requests slower than slow_ms (path, status, bytes) to the slow-request log."""
    from flask import g, request

    slow = logging.getLogger("slow_requests")

    @app.before_request
    def _start_timer() -> None:
        g._mdp_started = time.perf_counter()

    @app.after_request
    def _log_slow(response):
        started = getattr(g, "_mdp_started", None)
        if started is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if elapsed_ms >= slow_ms and not response.is_streamed:
                slow.warning("%s %s -> %s in %.0f ms (%s bytes)", request.method, request.full_path, response.status_code,
                             elapsed_ms, response.calculate_content_length())
        return response


def serve(app, *, host: str, port: int, server: str = "waitress", threads: int = WAITRESS_THREADS,
          connection_limit: int = WAITRESS_CONNECTION_LIMIT, channel_timeout: int = WAITRESS_CHANNEL_TIMEOUT,
          debug: bool = False) -> None:
    if server == "waitress" and not debug:
        from waitress import serve as waitress_serve

        logging.getLogger(__name__).info("waitress on %s:%s (threads=%s, connection_limit=%s, channel_timeout=%s)",
                                         host, port, threads, connection_limit, channel_timeout)
        waitress_serve(app, host=host, port=port, threads=threads, connection_limit=connection_limit,
                       channel_timeout=channel_timeout, ident="mdp")
        return
    app.run(host=host, port=port, debug=debug, threaded=True)


def add_server_args(parser) -> None:
    parser.add_argument("--server", choices=("werkzeug", "waitress"), default=os.environ.get("MDP_SERVER", "werkzeug"),
                        help="waitress for the local runtime (fixed thread pool); werkzeug keeps the old dev server")


class CachedBody:
    __slots__ = ("body", "gzipped", "etag", "status", "built_at", "tick")

    def __init__(self, text: str, status: int, tick: Hashable) -> None:
        self.body = text.encode("utf-8")
        self.gzipped = gzip.compress(self.body, compresslevel=5)
        self.etag = '"' + hashlib.sha1(self.body).hexdigest()[:20] + '"'
        self.status = status
        self.built_at = time.time()
        self.tick = tick


class ResponseCache:
    """One build per (key, tick); concurrent callers wait (bounded) or get the last good body."""

    def __init__(self, *, wait_sec: float = 20.0, max_keys: int = 256) -> None:
        self.wait_sec = wait_sec
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._bodies: dict[Hashable, CachedBody] = {}
        self._building: dict[Hashable, threading.Event] = {}
        self.builds = 0
        self.hits = 0
        self.errors = 0

    def get(self, key: Hashable, tick: Hashable, builder: Callable[[], tuple[Any, int]],
            serializer: Callable[[Any], str] = lambda payload: json.dumps(payload, separators=(",", ":"))) -> Optional[CachedBody]:
        while True:
            with self._lock:
                cached = self._bodies.get(key)
                if cached is not None and cached.tick == tick:
                    self.hits += 1
                    return cached
                event = self._building.get(key)
                if event is None:
                    event = threading.Event()
                    self._building[key] = event
                    break
            if not event.wait(self.wait_sec):
                with self._lock:
                    return self._bodies.get(key)
            with self._lock:
                cached = self._bodies.get(key)
                if cached is not None and (cached.tick == tick or key not in self._building):
                    return cached
        try:
            payload, status = builder()
            body = CachedBody(serializer(payload), status, tick)
            with self._lock:
                self.builds += 1
                if status < 500 or key not in self._bodies:
                    self._bodies[key] = body
                if len(self._bodies) > self.max_keys:
                    oldest = min(self._bodies, key=lambda k: self._bodies[k].built_at)
                    self._bodies.pop(oldest, None)
                return self._bodies[key]
        except Exception:
            with self._lock:
                self.errors += 1
                last = self._bodies.get(key)
            logging.getLogger(__name__).exception("response build failed for %s", key)
            if last is None:
                raise
            return last
        finally:
            with self._lock:
                done = self._building.pop(key, None)
            if done is not None:
                done.set()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"keys": len(self._bodies), "builds": self.builds, "hits": self.hits, "errors": self.errors}


def cached_response(cache: ResponseCache, key: Hashable, tick: Hashable, builder: Callable[[], tuple[Any, int]]):
    """Flask response from the cache: gzip when accepted, 304 when the ETag matches."""
    from flask import Response, current_app, request

    cached = cache.get(key, tick, builder, current_app.json.dumps)
    if cached is None:
        return Response(json.dumps({"success": False, "error": "still computing"}), status=503, mimetype="application/json")
    if request.headers.get("If-None-Match") == cached.etag:
        response = Response(status=304)
    elif "gzip" in (request.headers.get("Accept-Encoding") or ""):
        response = Response(cached.gzipped, status=cached.status, mimetype="application/json")
        response.headers["Content-Encoding"] = "gzip"
    else:
        response = Response(cached.body, status=cached.status, mimetype="application/json")
    response.headers["ETag"] = cached.etag
    response.headers["Vary"] = "Accept-Encoding"
    response.headers["Cache-Control"] = "no-cache"
    return response
