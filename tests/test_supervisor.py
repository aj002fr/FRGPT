"""Supervisor policy: three failed checks restart, exits back off exponentially, phase-aware checks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.dashboard_runtime import supervisor as sv


class FakeProc:
    _next = 2_000_000_001  # odd and huge: never a Windows PID

    def __init__(self) -> None:
        FakeProc._next += 2
        self.pid = FakeProc._next
        self.code = None

    def poll(self):
        return self.code

    def kill(self):
        self.code = -9


class Clock:
    def __init__(self) -> None:
        self.t = 1_790_000_000.0

    def __call__(self) -> float:
        return self.t


def make(tmp_path: Path, health_results: list, *, phase: str = "OPEN", grace: float = 0.0):
    clock = Clock()
    started = []
    results = iter(health_results)

    def launcher(spec):
        proc = FakeProc()
        started.append(proc)
        return proc

    def fetch(url):
        ok = next(results)
        return (200, b'{"feed": {"age_sec": 5}}') if ok else (500, b"")

    spec = sv.AppSpec(name="flow", argv=["x"], url="http://x/api/status", fresh_bars=True, grace_sec=grace)
    sup = sv.Supervisor([spec], state_dir=tmp_path, clock=clock, launcher=launcher, fetch=fetch, phase=lambda now: phase)
    return sup, clock, started


def test_backoff_doubles_to_a_cap() -> None:
    assert [sv.backoff_sec(k) for k in range(8)] == [5, 10, 20, 40, 80, 160, 300, 300]


def test_three_failed_checks_restart_and_success_resets(tmp_path) -> None:
    sup, clock, started = make(tmp_path, [False, False, True, False, False, False, True])
    sup.tick()
    assert len(started) == 1
    for _ in range(3):
        clock.t += 10
        sup.tick()
    assert sup.state["flow"].failures == 0 and len(started) == 1
    for _ in range(3):
        clock.t += 10
        sup.tick()
    assert sup.state["flow"].proc is None and started[0].code == -9
    clock.t += sv.backoff_sec(0) + 1
    sup.tick()
    assert len(started) == 2
    alerts = (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    assert "3 failed checks" in json.loads(alerts[-1])["message"]


def test_crash_loop_backs_off(tmp_path) -> None:
    sup, clock, started = make(tmp_path, [True] * 50)
    sup.tick()
    for expected_delay in (5, 10, 20):
        started[-1].code = 1
        clock.t += 1
        sup.tick()
        assert sup.state["flow"].proc is None
        clock.t += expected_delay - 1
        sup.tick()
        assert sup.state["flow"].proc is None
        clock.t += 1.5
        sup.tick()
        assert sup.state["flow"].proc is not None
    assert sup.state["flow"].restarts == 3
    clock.t += sv.BACKOFF_RESET_AFTER_SEC + 1
    sup.tick()
    assert sup.state["flow"].restarts == 0


def test_failures_during_startup_grace_do_not_count(tmp_path) -> None:
    sup, clock, started = make(tmp_path, [False] * 10, grace=60)
    sup.tick()
    for _ in range(5):
        clock.t += 10
        sup.tick()
    assert sup.state["flow"].failures == 0 and "starting" in sup.state["flow"].last_detail
    for _ in range(3):
        clock.t += 10
        sup.tick()
    assert sup.state["flow"].proc is None and len(started) == 1


def test_first_minutes_after_reopen_are_opening() -> None:
    from datetime import datetime

    from scripts.flow_store.sessions import EASTERN

    at = lambda text: datetime.fromisoformat(text).replace(tzinfo=EASTERN).timestamp()
    assert sv.market_phase(at("2026-09-24 18:02")) == "OPENING"
    assert sv.market_phase(at("2026-09-24 18:06")) == "OPEN"
    assert sv.market_phase(at("2026-09-24 17:30")) == "HALT"
    assert sv.market_phase(at("2026-09-27 18:01")) == "OPENING"


def test_stale_bars_fail_only_while_open(tmp_path) -> None:
    spec = sv.AppSpec(name="flow", argv=[], url="u", fresh_bars=True)
    stale = lambda url: (200, b'{"feed": {"age_sec": 900}}')
    assert sv.check_http(spec, "OPEN", stale)[0] is False
    assert sv.check_http(spec, "HALT", stale)[0] is True
    assert sv.check_http(spec, "CLOSED", stale)[0] is True


def _writer_status(tmp_path: Path, *, age: float, sealed_ago: float | None, connected=True, pid=4242) -> tuple[sv.AppSpec, float]:
    now = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    sealed = {"TY": (now - timedelta(seconds=sealed_ago + 60)).isoformat()} if sealed_ago is not None else {}
    status = {"updated_utc": (now - timedelta(seconds=age)).isoformat(), "connected": connected, "queue_depth": 3,
              "lease": {"pid": pid}, "last_sealed_minute_utc": sealed}
    path = tmp_path / "status.json"
    path.write_text(json.dumps(status), encoding="utf-8")
    return sv.AppSpec(name="writer", argv=[], health="writer", status_file=str(path)), now.timestamp()


def test_writer_checks_are_phase_aware(tmp_path) -> None:
    spec, now = _writer_status(tmp_path, age=5, sealed_ago=20)
    assert sv.check_writer(spec, 4242, now, "OPEN")[0] is True
    assert sv.check_writer(spec, 2_000_000_003, now, "OPEN")[0] is False
    spec, now = _writer_status(tmp_path, age=5, sealed_ago=400)
    assert sv.check_writer(spec, 4242, now, "OPEN")[0] is False
    assert sv.check_writer(spec, 4242, now, "HALT")[0] is True
    spec, now = _writer_status(tmp_path, age=90, sealed_ago=20)
    assert sv.check_writer(spec, 4242, now, "HALT")[0] is False
    spec, now = _writer_status(tmp_path, age=5, sealed_ago=20, connected=False)
    assert sv.check_writer(spec, 4242, now, "OPEN")[0] is False


def test_profiles_parse(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("rehearsal", "production"):
        specs, env, state_dir = sv.load_profile(root / "ops" / "supervisor" / "profiles.json", name, "python")
        assert [s.name for s in specs] == ["writer", "v3", "v22", "v27", "flow", "supply"]
        assert specs[0].health == "writer" and env["FLOW_TAPE_DB_PATH"]


def test_disabled_marker_stops_the_app_and_keeps_it_down(tmp_path) -> None:
    sup, clock, started = make(tmp_path, [True] * 10)
    sup.tick()
    assert len(started) == 1
    (tmp_path / "flow.disabled").write_text("", encoding="utf-8")
    clock.t += 5
    sup.tick()
    assert started[0].code == -9 and sup.state["flow"].proc is None
    for _ in range(3):
        clock.t += 400
        sup.tick()
    assert len(started) == 1
    assert json.loads((tmp_path / "supervisor_status.json").read_text())["apps"]["flow"]["detail"] == "disabled"
    (tmp_path / "flow.disabled").unlink()
    sup.tick()
    assert len(started) == 2


def test_stream_lag_over_threshold_alerts_without_restarting(tmp_path) -> None:
    spec = sv.AppSpec(name="v3", argv=["x"], url="http://x/api/live_status", lag_alert_sec=10.0)
    bodies = iter([b'{"stream_lag_sec": 25.0}'] * 3)
    started = []

    def launcher(_spec):
        proc = FakeProc()
        started.append(proc)
        return proc

    sv_clock = Clock()
    sup = sv.Supervisor([spec], state_dir=tmp_path, clock=sv_clock, launcher=launcher,
                        fetch=lambda url: (200, next(bodies)), phase=lambda now: "OPEN")
    for _ in range(3):
        sup.tick()
        sv_clock.t += 10
    alerts = (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(started) == 1 and len(alerts) == 1 and "stream lag 25.0s" in alerts[0]
    assert sv.check_http(spec, "HALT", lambda url: (200, b'{"stream_lag_sec": 25.0}')) == (True, "HTTP 200")
