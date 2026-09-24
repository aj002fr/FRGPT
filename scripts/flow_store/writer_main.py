#!/usr/bin/env python3
"""Standalone canonical flow writer: AccessAPI live trades -> tape -> sparse sealed 1-minute bars.

One process holds the writer lease (heartbeat 10 s, stale after 60 s). Records go through a
bounded queue and are written in batches about once a second. A minute is sealed at its end plus
10 s if AccessAPI covered it; otherwise it waits for gap backfill (every 60 s). With
--provisional, Databento backup trades seal uncovered minutes as PROVISIONAL bars during an
AccessAPI outage; backfill replaces them later. status.json is rewritten every 10 s.

    python -m scripts.flow_store.writer_main --tape C:\\...\\flow_tape.sqlite --history C:\\...\\flow_history.sqlite
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard.core import load_dv01_table  # noqa: E402
from scripts.flow_store import backfill as bf  # noqa: E402
from scripts.flow_store.aggregate import minute_floor_us  # noqa: E402
from scripts.flow_store.normalize import normalize_record, parse_ts_ns, round_us  # noqa: E402
from scripts.flow_store.sessions import session_for_ns  # noqa: E402
from scripts.flow_store.settings import (  # noqa: E402
    ACCESS_API_BASE,
    ACCESS_API_WS,
    BACKFILL_EVERY_SEC,
    COVERAGE_HEAD_US,
    COVERAGE_STALE_SEC,
    COVERAGE_TAIL_US,
    FLUSH_EVERY_SEC,
    LEASE_HEARTBEAT_SEC,
    MINUTE_US,
    QUEUE_MAX,
    ROOT_TO_SYMBOL,
    SEAL_DELAY_SEC,
    SOURCE_BACKUP,
    SOURCE_LIVE,
    STATUS_EVERY_SEC,
    SYMBOL_ROOTS,
    history_db_path,
    tape_db_path,
)
from scripts.flow_store.store import FlowStore, LeaseLost  # noqa: E402

logger = logging.getLogger("flow_writer")
SYMBOLS = tuple(SYMBOL_ROOTS)
BACKUP_AFTER_SEC = 30.0
BACKUP_RECOVER_SEC = 60.0


def now_us() -> int:
    return time.time_ns() // 1000




class FlowWriter:
    def __init__(self, *, tape_path: Path, history_path: Path, status_path: Path, ws_url: str = ACCESS_API_WS,
                 api_base: str = ACCESS_API_BASE, provisional: bool = False, owner: str = "flow_writer") -> None:
        self.tape_path = tape_path
        self.history_path = history_path
        self.status_path = status_path
        self.ws_url = ws_url
        self.api_base = api_base
        self.provisional = provisional
        self.owner = owner
        self.db_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flow-db")
        self.store: Optional[FlowStore] = None
        self.dv01 = load_dv01_table()
        self.queue: Optional[asyncio.Queue] = None
        self.stopping: Optional[asyncio.Event] = None
        self.connected = False
        self.connected_since: Optional[float] = None
        self.last_record_wall: dict[str, Optional[float]] = {s: None for s in SYMBOLS}
        self.last_event_us: dict[str, Optional[int]] = {s: None for s in SYMBOLS}
        self.cov_open: dict[str, Optional[int]] = {s: None for s in SYMBOLS}
        self.seal_cursor: dict[str, Optional[int]] = {s: None for s in SYMBOLS}
        self.pending: dict[str, set[int]] = {s: set() for s in SYMBOLS}
        self.counters = {"records": 0, "trades_inserted": 0, "backup_trades": 0, "dropped": 0,
                         "minutes_sealed": 0, "minutes_provisional": 0, "backfill_trades": 0, "revisions": 0}
        self.errors: deque = deque(maxlen=20)
        self.epoch = 0
        self.cov_epoch = 0
        self.backup = None
        self.backup_active = False
        self.last_backfill: Optional[dict] = None
        self.started_utc = datetime.now(timezone.utc).isoformat()

    # ---- helpers ----------------------------------------------------------------------
    async def db(self, fn, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(self.db_pool, lambda: fn(*args, **kwargs))

    def fetch(self, symbol: str, start_us: int, end_us: int):
        return bf.fetch_accessapi_trades(symbol, start_us, end_us, api_base=self.api_base)

    def note_error(self, where: str, exc: BaseException) -> None:
        logger.warning("%s: %s", where, exc)
        self.errors.append({"utc": datetime.now(timezone.utc).isoformat(), "where": where, "error": f"{type(exc).__name__}: {exc}"})

    def enqueue(self, item: tuple) -> None:
        """Queue (kind, data, wall). A drop starts a new epoch: coverage closes at the drop and
        reopens only at the first record queued after it, so dropped prints fall in a gap."""
        try:
            self.queue.put_nowait((*item, self.epoch))
        except asyncio.QueueFull:
            self.counters["dropped"] += 1
            self.epoch += 1

    # ---- DB-thread work ---------------------------------------------------------------
    def _open(self) -> None:
        self.store = FlowStore(self.tape_path, self.history_path, owner=self.owner)
        if not self.store.acquire_lease():
            row = self.store.lease_row()
            raise SystemExit(f"writer lease held by {row}; refusing to start")
        start = self.store.ensure_canonical_start(minute_floor_us(now_us()) + MINUTE_US)
        logger.info("lease acquired (pid %s); canonical_start_us=%s", os.getpid(), start)

    def _write_batch(self, live: list, backup: list, coverage: dict[str, tuple[str, int, int]]) -> None:
        store = self.store
        if live:
            self.counters["trades_inserted"] += len(store.insert_trades(live, mode="append"))
        if backup:
            self.counters["backup_trades"] += len(store.insert_trades(backup, mode="append", table="provisional_tape"))
        for symbol, (action, start_us, end_us) in coverage.items():
            if action == "open":
                self.cov_open[symbol] = store.open_coverage(symbol, start_us, SOURCE_LIVE)
            if self.cov_open[symbol] is not None and end_us > start_us:
                store.extend_coverage(self.cov_open[symbol], end_us)

    def _seal_due(self, now: int) -> None:
        store = self.store
        start = store.canonical_start_us()
        for symbol in SYMBOLS:
            for minute in sorted(self.pending[symbol]):
                if store.minute_covered(symbol, minute):
                    self._seal(symbol, minute, "late_seal")
                    self.pending[symbol].discard(minute)
            cursor = self.seal_cursor[symbol]
            if cursor is None:
                last = store.last_sealed_minute(symbol)
                cursor = max(start, last + MINUTE_US) if last is not None else start
            while cursor + MINUTE_US + SEAL_DELAY_SEC * 1_000_000 <= now:
                session = session_for_ns(cursor * 1000)
                if session is not None:
                    if store.minute_covered(symbol, cursor):
                        self._seal(symbol, cursor, "live_seal")
                    else:
                        if self.provisional:
                            front = bf.ensure_front(store, session.isoformat(), symbol, self.fetch)
                            if front and bf.seal_provisional(store, symbol, cursor, front=front,
                                                             dv01_unit=self.dv01.get(symbol, 1.0)) not in ("covered", "no_trades"):
                                self.counters["minutes_provisional"] += 1
                        self.pending[symbol].add(cursor)
                cursor += MINUTE_US
            self.seal_cursor[symbol] = cursor

    def _seal(self, symbol: str, minute: int, reason: str) -> None:
        session = session_for_ns(minute * 1000)
        front = bf.ensure_front(self.store, session.isoformat(), symbol, self.fetch)
        if front is None:
            return
        result = bf.seal_minute(self.store, symbol, minute, front=front, dv01_unit=self.dv01.get(symbol, 1.0), reason=reason)
        self.counters["minutes_sealed"] += 1
        self.counters["revisions"] += int(result in ("revised", "deleted"))

    def _backfill(self, now: int) -> dict:
        start = self.store.canonical_start_us()
        out = {}
        for symbol in SYMBOLS:
            try:
                stats = bf.fill_gaps(self.store, symbol, start, now, fetch=self.fetch, dv01_unit=self.dv01.get(symbol, 1.0))
            except LeaseLost:
                raise
            except Exception as exc:
                self.note_error(f"backfill {symbol}", exc)
                out[symbol] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            self.counters["backfill_trades"] += stats["trades_added"]
            self.counters["revisions"] += stats["revised"]
            out[symbol] = stats
            for minute in list(self.pending[symbol]):
                if self.store.minute_covered(symbol, minute) and self.store.sealed_source(symbol, minute) == "accessapi":
                    self.pending[symbol].discard(minute)
        return out

    def _status_facts(self) -> dict:
        store = self.store
        return {
            "lease": store.lease_row(),
            "canonical_start_us": store.canonical_start_us(),
            "last_sealed_minute_utc": {
                s: (datetime.fromtimestamp(m / 1e6, timezone.utc).isoformat() if (m := store.last_sealed_minute(s)) else None)
                for s in SYMBOLS
            },
        }

    # ---- async loops ------------------------------------------------------------------
    async def consume(self) -> None:
        import websockets

        backoff = 1.0
        while not self.stopping.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                    greeting = await ws.recv()
                    logger.info("AccessAPI connected: %s", str(greeting)[:160])
                    self.connected, self.connected_since, backoff = True, time.time(), 1.0
                    async for raw in ws:
                        if self.stopping.is_set():
                            break
                        msg = json.loads(raw)
                        if msg.get("type") == "record":
                            self.enqueue(("live", msg.get("data") or {}, time.time()))
            except Exception as exc:  # reconnect with backoff
                self.note_error("websocket", exc)
            finally:
                self.connected = False
                for symbol in SYMBOLS:
                    self.cov_open[symbol] = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _coverage_updates(self, batch_max_us: Optional[int]) -> dict[str, tuple[str, int, int]]:
        """One connection carries all four roots, and TBBO has only trades, so a quiet root is not
        a gap: every root is covered while the connection is up and any record arrived within
        COVERAGE_STALE_SEC. A new interval starts COVERAGE_HEAD_US after the newest event of its
        first batch and ends COVERAGE_TAIL_US behind the newest event, so records still in flight
        at either edge fall into a gap that backfill fills."""
        updates: dict[str, tuple[str, int, int]] = {}
        wall = time.time()
        last_wall = max((v for v in self.last_record_wall.values() if v is not None), default=None)
        fresh = self.connected and last_wall is not None and wall - last_wall <= COVERAGE_STALE_SEC
        watermark = max((v for v in self.last_event_us.values() if v is not None), default=None)
        for symbol in SYMBOLS:
            if not fresh or watermark is None:
                self.cov_open[symbol] = None
                continue
            end = watermark - COVERAGE_TAIL_US
            if self.cov_open[symbol] is None:
                if batch_max_us is None:
                    continue
                start = batch_max_us + COVERAGE_HEAD_US
                updates[symbol] = ("open", start, max(start, end))
            else:
                updates[symbol] = ("extend", 0, end)
        return updates

    async def flush_loop(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(FLUSH_EVERY_SEC)
            live, backup, batch_max = [], [], None
            while True:
                try:
                    kind, data, wall, epoch = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self.counters["records"] += 1
                if epoch != self.cov_epoch:
                    self.cov_epoch = epoch
                    batch_max = None
                    for symbol in SYMBOLS:
                        self.cov_open[symbol] = None
                if kind == "live":
                    symbol = ROOT_TO_SYMBOL.get(str(data.get("symbol") or "")[:2].upper())
                    ts_ns = parse_ts_ns(data.get("ts_event"))
                    if symbol is None or ts_ns is None:
                        continue
                    ts_us = round_us(ts_ns)
                    self.last_record_wall[symbol] = wall
                    self.last_event_us[symbol] = max(self.last_event_us[symbol] or 0, ts_us)
                    batch_max = ts_us if batch_max is None else max(batch_max, ts_us)
                    trade = normalize_record(data, source=SOURCE_LIVE, keep_raw=True)
                    if trade is not None:
                        live.append(trade)
                else:
                    trade = normalize_record(data, source=SOURCE_BACKUP)
                    if trade is not None:
                        backup.append(trade)
            coverage = self._coverage_updates(batch_max)
            try:
                await self.db(self._write_batch, live, backup, coverage)
            except LeaseLost as exc:
                self.note_error("lease", exc)
                self.stopping.set()
            except Exception as exc:
                self.note_error("flush", exc)

    async def seal_loop(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(1.0)
            try:
                await self.db(self._seal_due, now_us())
            except LeaseLost as exc:
                self.note_error("lease", exc)
                self.stopping.set()
            except Exception as exc:
                self.note_error("seal", exc)

    async def backfill_loop(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(BACKFILL_EVERY_SEC)
            try:
                stats = await self.db(self._backfill, now_us())
                self.last_backfill = {"utc": datetime.now(timezone.utc).isoformat(), "stats": stats}
            except LeaseLost as exc:
                self.note_error("lease", exc)
                self.stopping.set()
            except Exception as exc:
                self.note_error("backfill", exc)

    async def heartbeat_loop(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(LEASE_HEARTBEAT_SEC)
            try:
                await self.db(self.store.heartbeat)
            except LeaseLost as exc:
                self.note_error("lease", exc)
                self.stopping.set()

    async def backup_loop(self) -> None:
        """Databento backup while AccessAPI is silent; provisional only, never canonical."""
        from scripts.databento_live_backup import DatabentoLiveBackup

        loop = asyncio.get_running_loop()
        self.backup = DatabentoLiveBackup(on_record=lambda d: loop.call_soon_threadsafe(self.enqueue, ("backup", d, time.time())))
        healthy_since: Optional[float] = None
        while not self.stopping.is_set():
            await asyncio.sleep(1.0)
            last = max((v for v in self.last_record_wall.values() if v is not None), default=None)
            silent = last is None or time.time() - last > BACKUP_AFTER_SEC
            market_open = session_for_ns(time.time_ns()) is not None
            if not self.backup_active and silent and market_open:
                replay_us = max((v for v in self.last_event_us.values() if v is not None), default=None)
                replay = datetime.fromtimestamp(replay_us / 1e6, timezone.utc) if replay_us else None
                self.backup_active = bool(await loop.run_in_executor(None, self.backup.start, replay))
                logger.warning("AccessAPI silent; Databento backup %s", "started" if self.backup_active else "failed to start")
                healthy_since = None
            elif self.backup_active and not silent:
                healthy_since = healthy_since or time.time()
                if time.time() - healthy_since >= BACKUP_RECOVER_SEC:
                    await loop.run_in_executor(None, self.backup.stop)
                    self.backup_active = False
                    logger.warning("AccessAPI healthy for %ss; Databento backup stopped", BACKUP_RECOVER_SEC)

    async def status_loop(self) -> None:
        while not self.stopping.is_set():
            await self.write_status()
            await asyncio.sleep(STATUS_EVERY_SEC)

    async def write_status(self) -> None:
        try:
            facts = await self.db(self._status_facts)
        except Exception as exc:
            facts = {"error": str(exc)}
        wall = time.time()
        status = {
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "started_utc": self.started_utc,
            "tape": str(self.tape_path),
            "history": str(self.history_path),
            "connected": self.connected,
            "source": "databento_provisional" if self.backup_active else "accessapi",
            "record_age_sec": {s: (round(wall - v, 1) if v else None) for s, v in self.last_record_wall.items()},
            "queue_depth": self.queue.qsize() if self.queue else 0,
            "pending_minutes": {s: len(v) for s, v in self.pending.items()},
            "counters": dict(self.counters),
            "last_backfill": self.last_backfill,
            "errors": list(self.errors)[-5:],
            **facts,
        }
        tmp = self.status_path.with_suffix(".tmp")
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(status, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.status_path)
        except Exception as exc:
            self.note_error("status", exc)

    async def run(self) -> int:
        self.queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.stopping = asyncio.Event()
        await self.db(self._open)
        tasks = [asyncio.create_task(coro) for coro in (
            self.consume(), self.flush_loop(), self.seal_loop(), self.backfill_loop(), self.heartbeat_loop(), self.status_loop(),
        )]
        if self.provisional:
            tasks.append(asyncio.create_task(self.backup_loop()))
        try:
            await self.stopping.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.backup_active and self.backup is not None:
                self.backup.stop()
            await self.write_status()
            try:
                await self.db(self.store.release_lease)
            except Exception:
                pass
        return 0 if not any(e["where"] == "lease" for e in self.errors) else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical AccessAPI flow writer")
    parser.add_argument("--tape", type=Path, default=None, help="flow_tape.sqlite (default FLOW_TAPE_DB_PATH or repo workspace)")
    parser.add_argument("--history", type=Path, default=None, help="flow_history.sqlite (default FLOW_HISTORY_DB_PATH or repo workspace)")
    parser.add_argument("--status", type=Path, default=None, help="status.json (default next to the tape)")
    parser.add_argument("--provisional", action="store_true", help="Databento backup bars for uncovered minutes during AccessAPI outages")
    parser.add_argument("--owner", default="flow_writer")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    tape = args.tape or tape_db_path()
    writer = FlowWriter(
        tape_path=tape,
        history_path=args.history or history_db_path(),
        status_path=args.status or tape.with_name("status.json"),
        provisional=args.provisional,
        owner=args.owner,
    )
    try:
        return asyncio.run(writer.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
