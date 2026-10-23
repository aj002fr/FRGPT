#!/usr/bin/env python3
"""Standalone live-feed and failover smoke checks (no dashboard restart)."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard.live_buffer import LiveTradeBuffer  # noqa: E402
from scripts.aggression_dashboard_v22.live_buffer import LiveTbboBuffer  # noqa: E402
from scripts.databento_live_backup import DatabentoLiveBackup  # noqa: E402

REQUIRED_FIELDS = {
    "ts_event", "ts_recv", "symbol", "continuous_symbol",
    "bloomberg_symbol", "action", "side", "price", "size",
    "bid_price", "ask_price", "bid_size", "ask_size", "instrument_id",
}


def _record(ts: datetime | None = None) -> Dict[str, Any]:
    event_ts = ts or datetime.now(timezone.utc)
    return {
        "ts_event": event_ts.isoformat(),
        "ts_recv": event_ts.isoformat(),
        "symbol": "ZNU6",
        "continuous_symbol": "ZN.v.0",
        "bloomberg_symbol": "TY",
        "action": "T",
        "side": "A",
        "price": 111.0,
        "size": 5,
        "bid_price": 110.984375,
        "ask_price": 111.0,
        "bid_size": 10,
        "ask_size": 12,
        "instrument_id": 123,
    }


class _FakeBackup:
    def __init__(self, on_record: Callable[[Dict[str, Any]], None]) -> None:
        self.on_record = on_record
        self.started = False
        self.stopped = False
        self.replay_from: datetime | None = None

    def start(self, replay_from: datetime | None = None) -> bool:
        self.started = True
        self.stopped = False
        self.replay_from = replay_from
        self.on_record(_record())
        return True

    def stop(self, join_timeout_sec: float = 3.0) -> None:
        del join_timeout_sec
        self.stopped = True

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.started and not self.stopped,
            "connected": self.started and not self.stopped,
            "error": None,
            "last_record_ts": (
                datetime.now(timezone.utc).isoformat() if self.started else None
            ),
            "replay_from": self.replay_from.isoformat() if self.replay_from else None,
            "dataset": "FAKE",
            "symbols": ["ZN.v.0"],
        }


def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for failover state")


def _exercise_failover(buffer: Any, name: str) -> None:
    buffer.start()
    try:
        _wait_for(lambda: buffer.status()["source"] == "databento_backup")
        assert buffer._backup.started  # noqa: SLF001 - deliberate smoke probe
        assert buffer.status()["connected"]
        if hasattr(buffer, "snapshot"):
            assert not buffer.snapshot().empty
        else:
            now = datetime.now(timezone.utc)
            trades = buffer.get_trades(["TY"], now - timedelta(minutes=1), now)
            assert not trades["TY"].empty

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            buffer._handle_record(_record(), "accessapi")  # noqa: SLF001
            if buffer.status()["source"] == "accessapi":
                break
            time.sleep(0.02)
        assert buffer.status()["source"] == "accessapi"
        assert buffer._backup.stopped  # noqa: SLF001
        print(f"[PASS] {name}: backup activation, data ingest, and recovery")
    finally:
        buffer.stop()
        for thread_name in ("_thread", "_monitor_thread"):
            thread = getattr(buffer, thread_name, None)
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2.0)


def run_failover_simulation() -> None:
    logging.disable(logging.CRITICAL)
    try:
        _exercise_failover(
            LiveTradeBuffer(
                ws_url="ws://127.0.0.1:1",
                reconnect_delay_sec=0.20,
                backup_stale_sec=0.10,
                backup_recover_sec=0.10,
                backup_factory=_FakeBackup,
            ),
            "V1",
        )
        _exercise_failover(
            LiveTbboBuffer(
                ws_url="ws://127.0.0.1:1",
                recent_url="http://127.0.0.1:1",
                reconnect_delay_sec=0.20,
                backup_stale_sec=0.10,
                backup_recover_sec=0.10,
                backup_factory=_FakeBackup,
            ),
            "V2.2",
        )
    finally:
        logging.disable(logging.NOTSET)


def run_live_smoke(seconds: float) -> None:
    lock = threading.Lock()
    count = 0
    samples: list[Dict[str, Any]] = []
    errors: list[str] = []
    symbols: set[str] = set()

    def on_record(data: Dict[str, Any]) -> None:
        nonlocal count
        missing = REQUIRED_FIELDS.difference(data)
        with lock:
            count += 1
            symbols.add(str(data.get("bloomberg_symbol") or ""))
            if missing:
                errors.append(f"record {count} missing {sorted(missing)}")
            if len(samples) < 5:
                samples.append(data)

    backup = DatabentoLiveBackup(on_record)
    backup.start(replay_from=datetime.now(timezone.utc) - timedelta(seconds=10))
    try:
        time.sleep(seconds)
    finally:
        backup.stop()
    status = backup.status()
    with lock:
        final_count = count
        final_samples = list(samples)
        final_errors = list(errors)
        final_symbols = sorted(symbols)
    if final_errors:
        raise AssertionError("; ".join(final_errors[:5]))
    if final_count == 0:
        raise AssertionError(f"No normalized TBBO records received; status={status}")
    for sample in final_samples:
        print(
            "[sample]",
            {key: sample.get(key) for key in sorted(REQUIRED_FIELDS)},
        )
    print(
        f"[PASS] live Databento TBBO: {final_count} records, "
        f"symbols={final_symbols}, AccessAPI field contract matched"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-seconds",
        type=float,
        default=30.0,
        help="Direct Databento smoke duration; set 0 to skip.",
    )
    args = parser.parse_args()
    run_failover_simulation()
    if args.live_seconds > 0:
        run_live_smoke(args.live_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
