"""Deterministic tests for aggression feed failover and handoff integrity."""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict

from scripts.aggression_dashboard.live_buffer import (
    BACKUP_SOURCE as V1_BACKUP,
    PRIMARY_SOURCE as V1_PRIMARY,
    LiveTradeBuffer,
)
from scripts.databento_live_backup import (
    DatabentoLiveBackup,
    START_WAIT_STOPPING_SEC,
    TbboEventDeduplicator,
)
from scripts.aggression_dashboard_v22.live_buffer import (
    BACKUP_SOURCE as V22_BACKUP,
    PRIMARY_SOURCE as V22_PRIMARY,
    LiveTbboBuffer,
)
from scripts.aggression_dashboard_v22 import live_buffer as v22_live


def _tbbo(sequence: int, ts: datetime | None = None) -> Dict[str, Any]:
    event_ts = ts or datetime.now(timezone.utc)
    return {
        "ts_event": event_ts.isoformat(),
        "ts_recv": event_ts.isoformat(),
        "symbol": "ZNU6",
        "continuous_symbol": "ZN.v.0",
        "bloomberg_symbol": "TY",
        "action": "T",
        "side": "B",
        "price": 109.046875,
        "size": 9,
        "bid_price": 109.03125,
        "ask_price": 109.046875,
        "bid_size": 100,
        "ask_size": 80,
        "instrument_id": 42,
        "sequence": sequence,
    }


def _recent_ts() -> datetime:
    """Keep live-buffer fixtures inside their 18-hour retention window."""
    return datetime.now(timezone.utc).replace(microsecond=0)


class _FakeBackup:
    def __init__(self, on_record: Callable[[Dict[str, Any]], None]) -> None:
        self.on_record = on_record
        self.started = False
        self.stopped = False

    def start(self, replay_from: datetime | None = None) -> bool:
        del replay_from
        self.started = True
        self.stopped = False
        return True

    def stop(self, join_timeout_sec: float = 3.0) -> None:
        del join_timeout_sec
        self.stopped = True

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.started and not self.stopped,
            "connected": self.started and not self.stopped,
            "error": None,
            "last_record_ts": None,
            "replay_from": None,
            "dataset": "FAKE",
            "symbols": ["ZN.v.0"],
        }


def test_sequence_dedupe_keeps_distinct_equal_timestamp_events() -> None:
    dedupe = TbboEventDeduplicator(capacity=10)
    ts = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    assert dedupe.accept("TY", _tbbo(100, ts), ts)
    assert not dedupe.accept("TY", _tbbo(100, ts), ts)
    assert dedupe.accept("TY", _tbbo(101, ts), ts)
    assert dedupe.accept("TY", _tbbo(99, ts + timedelta(microseconds=1)), ts + timedelta(microseconds=1))


def test_sequence_dedupe_preserves_sub_microsecond_events() -> None:
    dedupe = TbboEventDeduplicator(capacity=10)
    parsed = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    first = _tbbo(300, parsed)
    second = _tbbo(300, parsed)
    different_payload = _tbbo(300, parsed)
    first["ts_event"] = "2026-08-13T14:00:00.000000100+00:00"
    second["ts_event"] = "2026-08-13T14:00:00.000000200+00:00"
    different_payload["ts_event"] = first["ts_event"]
    different_payload["size"] = 10
    assert dedupe.accept("TY", first, parsed)
    assert dedupe.accept("TY", second, parsed)
    assert dedupe.accept("TY", different_payload, parsed)
    assert not dedupe.accept("TY", different_payload, parsed)


def test_receive_highwater_blocks_evicted_replay() -> None:
    dedupe = TbboEventDeduplicator(capacity=1)
    parsed = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    older = _tbbo(400, parsed)
    newer = _tbbo(401, parsed)
    older["ts_recv"] = "2026-08-13T14:00:00.000000100+00:00"
    newer["ts_recv"] = "2026-08-13T14:00:00.000000200+00:00"
    assert dedupe.accept("TY", older, parsed)
    assert dedupe.accept("TY", newer, parsed)
    assert not dedupe.accept("TY", older, parsed)


def test_v22_dedupes_reconnect_and_recovery_overlap() -> None:
    buffer = LiveTbboBuffer(backup_factory=_FakeBackup)
    with buffer._lock:
        buffer._active_source = V22_BACKUP
    ts = _recent_ts()
    buffer._handle_record(_tbbo(100, ts), V22_BACKUP)
    buffer._handle_record(_tbbo(100, ts), V22_BACKUP)
    buffer._handle_record(_tbbo(101, ts), V22_BACKUP)
    assert len(buffer.snapshot()) == 2

    with buffer._lock:
        buffer._active_source = V22_PRIMARY
    buffer._handle_record(_tbbo(101, ts), V22_PRIMARY)
    buffer._handle_record(_tbbo(102, ts + timedelta(microseconds=1)), V22_PRIMARY)
    assert len(buffer.snapshot()) == 3


def test_v22_recent_seed_is_sorted_before_sequence_dedupe(monkeypatch) -> None:
    ts = _recent_ts()
    newest_first = [
        _tbbo(102, ts + timedelta(microseconds=2)),
        _tbbo(101, ts + timedelta(microseconds=1)),
        _tbbo(100, ts),
    ]

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return json.dumps({"data": newest_first}).encode("utf-8")

    monkeypatch.setattr(v22_live.urllib.request, "urlopen", lambda *args, **kwargs: _Response())
    buffer = LiveTbboBuffer(backup_factory=_FakeBackup)
    buffer.seed_recent()
    assert len(buffer.snapshot()) == 3


def test_v1_dedupes_backup_replay() -> None:
    buffer = LiveTradeBuffer(backup_factory=_FakeBackup)
    with buffer._lock:
        buffer._active_source = V1_BACKUP
    ts = _recent_ts()
    buffer._handle_record(_tbbo(200, ts), V1_BACKUP)
    buffer._handle_record(_tbbo(200, ts), V1_BACKUP)
    trades = buffer.get_trades(
        ["TY"],
        ts - timedelta(seconds=1),
        ts + timedelta(seconds=1),
    )
    assert len(trades["TY"]) == 1


def test_stopped_buffers_cannot_activate_backup() -> None:
    now = datetime.now(timezone.utc)
    v1 = LiveTradeBuffer(backup_factory=_FakeBackup)
    v22 = LiveTbboBuffer(backup_factory=_FakeBackup)
    v1._activate_backup(now)
    v22._activate_backup(now)
    assert not v1._backup.started
    assert not v22._backup.started


class _FakeLive:
    def __init__(self, key: str) -> None:
        self.key = key
        self.subscribe_args: Dict[str, Any] = {}
        ts_ns = 1_786_630_400_123_456_789
        self.records = [
            SimpleNamespace(
                instrument_id=42,
                stype_in_symbol="ZN.v.0",
                stype_out_symbol="ZNU6",
            ),
            SimpleNamespace(
                instrument_id=42,
                ts_event=ts_ns,
                ts_recv=ts_ns + 100,
                price=109_046_875_000,
                size=9,
                action="T",
                side="B",
                sequence=500,
                levels=[
                    SimpleNamespace(
                        bid_px=109_031_250_000,
                        ask_px=109_046_875_000,
                        bid_sz=100,
                        ask_sz=80,
                    )
                ],
            ),
        ]

    def subscribe(self, **kwargs: Any) -> int:
        self.subscribe_args = kwargs
        return 1

    def __iter__(self):
        return iter(self.records)

    def terminate(self) -> None:
        return None


def test_databento_reconnect_cursor_advances(monkeypatch) -> None:
    fake_module = SimpleNamespace(Live=_FakeLive)
    monkeypatch.setitem(sys.modules, "databento", fake_module)
    received: list[Dict[str, Any]] = []
    start = datetime(2026, 8, 13, 13, 59, tzinfo=timezone.utc)
    backup = DatabentoLiveBackup(received.append, api_key="test")
    backup._running = True
    backup._replay_from = start

    backup._run_once()

    assert len(received) == 1
    assert received[0]["ts_event"].endswith("123456789+00:00")
    assert backup._replay_from is not None
    assert backup._replay_from > start


def test_start_waits_out_stopping_worker(monkeypatch) -> None:
    generations = {"n": 0}

    def fake_main(self: DatabentoLiveBackup) -> None:
        generations["n"] += 1
        if generations["n"] == 1:
            time.sleep(4.0)
            return
        while self._is_running():
            time.sleep(0.02)

    monkeypatch.setattr(DatabentoLiveBackup, "_thread_main", fake_main)
    backup = DatabentoLiveBackup(lambda _record: None, api_key="test")
    assert backup.start() is True
    time.sleep(0.05)
    backup.stop(join_timeout_sec=0.2)
    assert backup._thread is not None
    assert backup._thread.is_alive()
    assert backup.start() is True
    assert backup.status()["error"] is None
    assert backup.status()["running"] is True
    assert generations["n"] == 2
    assert START_WAIT_STOPPING_SEC >= 15.0
    backup.stop(join_timeout_sec=1.0)


def test_v1_does_not_advertise_backup_until_start_succeeds() -> None:
    class _SlowStartBackup(_FakeBackup):
        def __init__(self, on_record: Callable[[Dict[str, Any]], None]) -> None:
            super().__init__(on_record)
            self.gate = threading.Event()
            self.entered = threading.Event()

        def start(self, replay_from: datetime | None = None) -> bool:
            del replay_from
            self.entered.set()
            assert self.gate.wait(timeout=2.0)
            self.started = True
            self.stopped = False
            return True

    buffer = LiveTradeBuffer(backup_factory=_SlowStartBackup)
    buffer._running = True
    buffer._started_at = datetime.now(timezone.utc)
    saw_backup = {"value": False}

    def activate() -> None:
        buffer._activate_backup(datetime.now(timezone.utc))

    worker = threading.Thread(target=activate)
    worker.start()
    assert buffer._backup.entered.wait(timeout=1.0)
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        if buffer.status()["source"] == V1_BACKUP:
            saw_backup["value"] = True
            break
        time.sleep(0.02)
    assert buffer.status()["source"] == V1_PRIMARY
    buffer._backup.gate.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert saw_backup["value"] is False
    assert buffer.status()["source"] == V1_BACKUP
