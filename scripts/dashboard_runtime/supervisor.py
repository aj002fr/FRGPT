#!/usr/bin/env python3
"""Phase-aware supervisor for the canonical flow writer and the five dashboards (Milestone 6).

Every 10 s each process gets a data-based health check: the writer's lease, connection, last
sealed minute and queue depth from its status.json; the apps' HTTP endpoints, and for the flow
viewers the age of the newest bar. Halt and closed phases need no recent bar. A process is
restarted after three consecutive failed checks or when it exits, with exponential crash-loop
backoff. Alerts go to alerts.jsonl and the Windows Event Log; per-process CPU, memory, threads
and check latency go to metrics.jsonl every minute.

    python -m scripts.dashboard_runtime.supervisor --profile rehearsal
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("supervisor")

CHECK_EVERY_SEC = 10.0
METRICS_EVERY_SEC = 60.0
FAILURES_BEFORE_RESTART = 3
BACKOFF_BASE_SEC = 5.0
BACKOFF_MAX_SEC = 300.0
BACKOFF_RESET_AFTER_SEC = 600.0
WRITER_STATUS_STALE_SEC = 30.0
WRITER_SEALED_STALE_SEC = 180.0
WRITER_QUEUE_MAX = 50_000
BAR_STALE_SEC = 300.0
HTTP_TIMEOUT_SEC = 10.0
GRACEFUL_STOP_SEC = 10.0
EVENT_SOURCE = "MDP Dashboard Supervisor"


@dataclass
class AppSpec:
    name: str
    argv: list[str]
    port: Optional[int] = None
    health: str = "http"
    url: Optional[str] = None
    ok_statuses: tuple[int, ...] = (200,)
    status_file: Optional[str] = None
    fresh_bars: bool = False
    env: dict[str, str] = field(default_factory=dict)
    grace_sec: float = 120.0


@dataclass
class AppState:
    proc: Any = None
    started_at: Optional[float] = None
    failures: int = 0
    restarts: int = 0
    next_start_at: float = 0.0
    last_ok_at: Optional[float] = None
    last_detail: str = ""
    last_latency_ms: Optional[float] = None


def backoff_sec(restarts: int) -> float:
    return min(BACKOFF_MAX_SEC, BACKOFF_BASE_SEC * (2 ** max(restarts, 0)))


REOPEN_GRACE_SEC = 300.0


def market_phase(now: Optional[float] = None) -> str:
    """OPEN / HALT / CLOSED, with OPENING for the first REOPEN_GRACE_SEC of a session, when no
    bar can be fresh yet (the newest bar is from before the halt)."""
    from scripts.aggression_flow_history_dash.session import market_phase as phase
    from scripts.flow_store.sessions import session_bounds_ns, session_for_ns

    now = now if now is not None else time.time()
    value = phase(datetime.fromtimestamp(now, timezone.utc))
    if value == "OPEN":
        session = session_for_ns(int(now) * 1_000_000_000)
        if session is not None and now - session_bounds_ns(session)[0] / 1e9 < REOPEN_GRACE_SEC:
            return "OPENING"
    return value


def _pid_in_tree(pid: Optional[int], root_pid: Optional[int]) -> bool:
    if pid is None or root_pid is None:
        return False
    if pid == root_pid:
        return True
    try:
        import psutil

        return any(child.pid == pid for child in psutil.Process(root_pid).children(recursive=True))
    except Exception:
        return False


def check_writer(spec: AppSpec, root_pid: Optional[int], now: float, phase: str) -> tuple[bool, str]:
    try:
        status = json.loads(Path(spec.status_file).read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"status unreadable: {exc}"
    updated = datetime.fromisoformat(status["updated_utc"]).timestamp()
    if now - updated > WRITER_STATUS_STALE_SEC:
        return False, f"status {now - updated:.0f}s old"
    lease = status.get("lease") or {}
    if root_pid is not None and not _pid_in_tree(lease.get("pid"), root_pid):
        return False, f"lease held by pid {lease.get('pid')}, not this writer"
    if phase != "OPEN":
        return True, f"{phase}: status fresh"
    if not status.get("connected"):
        return False, "AccessAPI not connected"
    if int(status.get("queue_depth") or 0) > WRITER_QUEUE_MAX:
        return False, f"queue depth {status.get('queue_depth')}"
    sealed = [datetime.fromisoformat(v).timestamp() for v in (status.get("last_sealed_minute_utc") or {}).values() if v]
    if not sealed:
        return False, "no sealed minute yet"
    newest = max(sealed) + 60
    if now - newest > WRITER_SEALED_STALE_SEC:
        return False, f"last sealed minute ended {now - newest:.0f}s ago"
    return True, f"OPEN: sealed through {datetime.fromtimestamp(newest, timezone.utc).strftime('%H:%M')} UTC"


def check_http(spec: AppSpec, phase: str, fetch: Callable[[str], tuple[int, bytes]]) -> tuple[bool, str]:
    try:
        status, body = fetch(spec.url)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if status not in spec.ok_statuses:
        return False, f"HTTP {status}"
    if spec.fresh_bars and phase == "OPEN":
        try:
            feed = json.loads(body).get("feed") or {}
        except Exception:
            return False, "response is not JSON"
        age = feed.get("age_sec")
        if age is None or float(age) > BAR_STALE_SEC:
            return False, f"newest bar {age}s old"
    return True, f"HTTP {status}"


def http_fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SEC) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if hasattr(exc, "read") else b""


class Supervisor:
    def __init__(self, specs: list[AppSpec], *, state_dir: Path, cwd: Path = PROJECT_ROOT, base_env: Optional[dict] = None,
                 clock: Callable[[], float] = time.time, launcher: Optional[Callable] = None,
                 fetch: Callable[[str], tuple[int, bytes]] = http_fetch, phase: Callable[[float], str] = market_phase) -> None:
        self.specs = specs
        self.state = {spec.name: AppState() for spec in specs}
        self.state_dir = Path(state_dir)
        self.cwd = Path(cwd)
        self.base_env = dict(base_env or {})
        self.clock = clock
        self.launcher = launcher or self._launch
        self.fetch = fetch
        self.phase = phase
        self.last_metrics = 0.0
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ---- processes ----------------------------------------------------------------------
    def _launch(self, spec: AppSpec):
        env = {**os.environ, **self.base_env, **spec.env}
        log = open(self.state_dir / f"{spec.name}.out.log", "ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(spec.argv, cwd=str(self.cwd), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)

    def start(self, spec: AppSpec) -> None:
        state = self.state[spec.name]
        state.proc = self.launcher(spec)
        state.started_at = self.clock()
        state.failures = 0
        logger.info("started %s (pid %s)", spec.name, getattr(state.proc, "pid", None))

    def stop(self, spec: AppSpec) -> None:
        state = self.state[spec.name]
        proc, state.proc = state.proc, None
        if proc is None:
            return
        if not isinstance(proc, subprocess.Popen):
            proc.kill()
            return
        stop_file = Path(spec.status_file).with_name("stop.request") if spec.health == "writer" and spec.status_file else None
        if stop_file is not None:
            try:
                stop_file.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
                proc.wait(timeout=GRACEFUL_STOP_SEC)
            except Exception:
                pass
            finally:
                stop_file.unlink(missing_ok=True)
        try:
            import psutil

            root = psutil.Process(proc.pid)
            for child in root.children(recursive=True):
                child.kill()
            root.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _running(self, state: AppState) -> bool:
        return state.proc is not None and state.proc.poll() is None

    # ---- one supervision pass -----------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        phase = self.phase(now)
        for spec in self.specs:
            state = self.state[spec.name]
            if (self.state_dir / f"{spec.name}.disabled").exists():
                if state.proc is not None:
                    self.stop(spec)
                    self.alert("warning", spec.name, "stopped: disabled by operator")
                state.failures = 0
                state.last_detail = "disabled"
                continue
            if state.proc is None:
                if now >= state.next_start_at:
                    self.start(spec)
                continue
            if not self._running(state):
                code = state.proc.poll()
                self._schedule_restart(spec, now, f"exited with code {code}")
                continue
            if state.started_at is not None and now - state.started_at >= BACKOFF_RESET_AFTER_SEC and state.failures == 0:
                state.restarts = 0
            started = time.perf_counter()
            if spec.health == "writer":
                ok, detail = check_writer(spec, getattr(state.proc, "pid", None), now, phase)
            else:
                ok, detail = check_http(spec, phase, self.fetch)
            state.last_latency_ms = (time.perf_counter() - started) * 1000.0
            state.last_detail = detail
            if not ok and state.started_at is not None and now - state.started_at < spec.grace_sec:
                state.last_detail = f"starting ({now - state.started_at:.0f}s): {detail}"
                continue
            if ok:
                if state.failures:
                    logger.info("%s healthy again: %s", spec.name, detail)
                state.failures = 0
                state.last_ok_at = now
                continue
            state.failures += 1
            logger.warning("%s unhealthy (%s/%s): %s", spec.name, state.failures, FAILURES_BEFORE_RESTART, detail)
            if state.failures >= FAILURES_BEFORE_RESTART:
                self.stop(spec)
                self._schedule_restart(spec, now, f"{FAILURES_BEFORE_RESTART} failed checks: {detail}")
        if now - self.last_metrics >= METRICS_EVERY_SEC:
            self.last_metrics = now
            self.record_metrics(now, phase)
        self.write_status(now, phase)

    def _schedule_restart(self, spec: AppSpec, now: float, reason: str) -> None:
        state = self.state[spec.name]
        delay = backoff_sec(state.restarts)
        state.restarts += 1
        state.proc = None
        state.next_start_at = now + delay
        self.alert("error" if state.restarts > 3 else "warning", spec.name, f"restart in {delay:.0f}s: {reason}")

    # ---- outputs ------------------------------------------------------------------------
    def alert(self, level: str, app: str, message: str) -> None:
        record = {"utc": datetime.now(timezone.utc).isoformat(), "level": level, "app": app, "message": message}
        logger.warning("ALERT %s %s: %s", level, app, message)
        with open(self.state_dir / "alerts.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        try:
            import win32evtlog
            import win32evtlogutil

            kind = win32evtlog.EVENTLOG_ERROR_TYPE if level == "error" else win32evtlog.EVENTLOG_WARNING_TYPE
            win32evtlogutil.ReportEvent(EVENT_SOURCE, 1000, eventType=kind, strings=[f"{app}: {message}"])
        except Exception as exc:
            logger.debug("event log write failed: %s", exc)

    def record_metrics(self, now: float, phase: str) -> None:
        rows = []
        try:
            import psutil
        except ImportError:
            psutil = None
        for spec in self.specs:
            state = self.state[spec.name]
            row = {"utc": datetime.fromtimestamp(now, timezone.utc).isoformat(), "app": spec.name, "phase": phase,
                   "running": self._running(state), "restarts": state.restarts, "check_ms": state.last_latency_ms,
                   "detail": state.last_detail}
            if psutil is not None and self._running(state):
                try:
                    procs = [self._ps_process(psutil, state.proc.pid)]
                    procs += [self._ps_process(psutil, child.pid) for child in procs[0].children(recursive=True)]
                    row["cpu_pct"] = round(sum(p.cpu_percent(interval=None) for p in procs), 1)
                    row["rss_mb"] = round(sum(p.memory_info().rss for p in procs) / 2**20, 1)
                    row["threads"] = sum(p.num_threads() for p in procs)
                except Exception:
                    pass
            rows.append(row)
        with open(self.state_dir / "metrics.jsonl", "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def _ps_process(self, psutil, pid: int):
        """Reuse psutil.Process objects so cpu_percent measures the interval since the last snapshot."""
        cache = getattr(self, "_ps_cache", None)
        if cache is None:
            cache = self._ps_cache = {}
        proc = cache.get(pid)
        if proc is None:
            proc = cache[pid] = psutil.Process(pid)
        return proc

    def write_status(self, now: float, phase: str) -> None:
        status = {
            "updated_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "phase": phase,
            "apps": {
                spec.name: {
                    "running": self._running(self.state[spec.name]),
                    "pid": getattr(self.state[spec.name].proc, "pid", None),
                    "failures": self.state[spec.name].failures,
                    "restarts": self.state[spec.name].restarts,
                    "detail": self.state[spec.name].last_detail,
                    "last_ok_utc": (datetime.fromtimestamp(self.state[spec.name].last_ok_at, timezone.utc).isoformat()
                                    if self.state[spec.name].last_ok_at else None),
                }
                for spec in self.specs
            },
        }
        tmp = self.state_dir / "supervisor_status.tmp"
        tmp.write_text(json.dumps(status, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_dir / "supervisor_status.json")

    def run_forever(self) -> None:
        try:
            while True:
                self.tick()
                time.sleep(CHECK_EVERY_SEC)
        finally:
            for spec in self.specs:
                self.stop(spec)


def load_profile(path: Path, name: str, python: str) -> tuple[list[AppSpec], dict, Path]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))[name]
    specs = []
    for app in profile["apps"]:
        argv = [python if part == "{python}" else part for part in app["argv"]]
        specs.append(AppSpec(
            name=app["name"], argv=argv, port=app.get("port"), health=app.get("health", "http"), url=app.get("url"),
            ok_statuses=tuple(app.get("ok_statuses", [200])), status_file=app.get("status_file"),
            fresh_bars=bool(app.get("fresh_bars")), env=app.get("env", {}), grace_sec=float(app.get("grace_sec", 120.0)),
        ))
    return specs, profile.get("env", {}), Path(profile["state_dir"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profiles", type=Path, default=PROJECT_ROOT / "ops" / "supervisor" / "profiles.json")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    specs, env, state_dir = load_profile(args.profiles, args.profile, args.python)
    state_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        handlers=[logging.FileHandler(state_dir / "supervisor.log", encoding="utf-8"), logging.StreamHandler()])
    Supervisor(specs, state_dir=state_dir, base_env=env).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
