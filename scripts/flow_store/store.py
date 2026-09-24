"""SQLite storage for the canonical writer: tape, provisional tape, coverage, front contract,
single-writer lease, revisions, and the canonical 1-minute bars in flow_history.sqlite.

Only the lease holder writes. Bars keep V1's `v1_aggress_flow_1m` schema; minutes before
`canonical_start_us` are never written, and every change to an existing bar is logged.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from scripts.flow_store.aggregate import Bar
from scripts.flow_store.normalize import Trade, trade_id
from scripts.flow_store.settings import (
    BAR_SOURCE_CANONICAL,
    BAR_SOURCE_PROVISIONAL,
    LEASE_STALE_SEC,
    MINUTE_US,
)

SCHEMA_VERSION = 1
_TRADE_COLUMNS = """
    trade_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    contract TEXT NOT NULL,
    ts_us INTEGER NOT NULL,
    ts_event_ns INTEGER NOT NULL,
    price_nanos INTEGER NOT NULL,
    size INTEGER NOT NULL,
    side TEXT NOT NULL,
    occurrence INTEGER NOT NULL,
    source TEXT NOT NULL,
    sequence INTEGER,
    ingested_utc TEXT NOT NULL,
    raw TEXT
"""
TAPE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_tape ({_TRADE_COLUMNS});
CREATE INDEX IF NOT EXISTS ix_tape_symbol_ts ON trade_tape(symbol, ts_us);
CREATE TABLE IF NOT EXISTS provisional_tape ({_TRADE_COLUMNS});
CREATE INDEX IF NOT EXISTS ix_prov_symbol_ts ON provisional_tape(symbol, ts_us);
CREATE TABLE IF NOT EXISTS coverage (
    symbol TEXT NOT NULL, start_us INTEGER NOT NULL, end_us INTEGER NOT NULL,
    source TEXT NOT NULL, recorded_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_coverage_symbol ON coverage(symbol, start_us);
CREATE TABLE IF NOT EXISTS front_contract (
    session TEXT NOT NULL, symbol TEXT NOT NULL, contract TEXT NOT NULL, method TEXT NOT NULL,
    volume INTEGER, computed_utc TEXT NOT NULL, PRIMARY KEY (session, symbol)
);
CREATE TABLE IF NOT EXISTS front_contract_override (
    session TEXT NOT NULL, symbol TEXT NOT NULL, contract TEXT NOT NULL, reason TEXT,
    PRIMARY KEY (session, symbol)
);
CREATE TABLE IF NOT EXISTS writer_lease (
    id INTEGER PRIMARY KEY CHECK (id = 1), owner TEXT NOT NULL, pid INTEGER NOT NULL, host TEXT NOT NULL,
    acquired_utc TEXT NOT NULL, heartbeat_utc TEXT NOT NULL, heartbeat_epoch REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
    before_json TEXT, after_json TEXT, reason TEXT NOT NULL, revised_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sealed_minutes (
    symbol TEXT NOT NULL, minute_us INTEGER NOT NULL, source TEXT NOT NULL, sealed_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, minute_us)
);
"""
BAR_FIELDS = (
    "buy_contracts", "sell_contracts", "buy_flow", "sell_flow",
    "signed_contracts", "signed_flow", "trade_count", "trade_price",
)


class LeaseLost(RuntimeError):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def minute_clocks(minute_us: int) -> tuple[str, str]:
    """Same text keys V1 writes (`ts_utc` ISO, `ts_et` with zone abbreviation)."""
    from scripts.aggression_dashboard.flow_history import _clocks

    return _clocks(pd.Timestamp(int(minute_us), unit="us", tz="UTC"))


def _connect(path: Path, schema: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(schema)
    return conn


class FlowStore:
    """Single-thread handle on the tape DB and the flow-history DB."""

    def __init__(self, tape_path: Path, history_path: Path, *, owner: str = "flow_writer") -> None:
        from scripts.aggression_dashboard.flow_history import _SCHEMA as HISTORY_SCHEMA

        self.owner = owner
        self.pid = os.getpid()
        self.host = socket.gethostname()
        self.tape = _connect(Path(tape_path), TAPE_SCHEMA)
        self.hist = _connect(Path(history_path), HISTORY_SCHEMA)
        if self.tape.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self.tape.execute("INSERT INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION, _now_utc()))

    def close(self) -> None:
        self.tape.close()
        self.hist.close()

    # ---- meta -------------------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        row = self.tape.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.tape.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def canonical_start_us(self) -> Optional[int]:
        value = self.get_meta("canonical_start_us")
        return int(value) if value else None

    def ensure_canonical_start(self, start_us: int) -> int:
        existing = self.canonical_start_us()
        if existing is not None:
            return existing
        self.set_meta("canonical_start_us", str(int(start_us)))
        return int(start_us)

    # ---- lease ------------------------------------------------------------------------
    def acquire_lease(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        self.tape.execute("BEGIN IMMEDIATE")
        try:
            row = self.tape.execute("SELECT owner, pid, host, heartbeat_epoch FROM writer_lease WHERE id = 1").fetchone()
            mine = row is not None and (row[0], row[1], row[2]) == (self.owner, self.pid, self.host)
            if row is not None and not mine and now - float(row[3]) <= LEASE_STALE_SEC:
                self.tape.execute("ROLLBACK")
                return False
            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
            self.tape.execute(
                "INSERT OR REPLACE INTO writer_lease VALUES (1, ?, ?, ?, ?, ?, ?)",
                (self.owner, self.pid, self.host, stamp, stamp, now),
            )
            self.tape.execute("COMMIT")
            return True
        except Exception:
            self.tape.execute("ROLLBACK")
            raise

    def heartbeat(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        cur = self.tape.execute(
            "UPDATE writer_lease SET heartbeat_utc = ?, heartbeat_epoch = ? WHERE id = 1 AND owner = ? AND pid = ? AND host = ?",
            (datetime.fromtimestamp(now, timezone.utc).isoformat(), now, self.owner, self.pid, self.host),
        )
        if cur.rowcount != 1:
            raise LeaseLost("writer lease is held by another process")

    def release_lease(self) -> None:
        self.tape.execute(
            "DELETE FROM writer_lease WHERE id = 1 AND owner = ? AND pid = ? AND host = ?",
            (self.owner, self.pid, self.host),
        )

    def lease_row(self) -> Optional[dict]:
        row = self.tape.execute("SELECT owner, pid, host, acquired_utc, heartbeat_utc, heartbeat_epoch FROM writer_lease WHERE id = 1").fetchone()
        if row is None:
            return None
        return dict(zip(("owner", "pid", "host", "acquired_utc", "heartbeat_utc", "heartbeat_epoch"), row))

    def assert_lease(self) -> None:
        row = self.lease_row()
        if row is None or (row["owner"], row["pid"], row["host"]) != (self.owner, self.pid, self.host):
            raise LeaseLost("writer lease is not held by this process")

    # ---- trades -----------------------------------------------------------------------
    def key_counts(self, symbol: str, start_us: int, end_us: int, *, table: str = "trade_tape") -> Counter:
        rows = self.tape.execute(
            f"SELECT contract, ts_us, price_nanos, size, side, COUNT(*) FROM {table} "
            "WHERE symbol = ? AND ts_us >= ? AND ts_us < ? GROUP BY contract, ts_us, price_nanos, size, side",
            (symbol, int(start_us), int(end_us)),
        ).fetchall()
        return Counter({tuple(r[:5]): int(r[5]) for r in rows})

    def insert_trades(self, trades: Iterable[Trade], *, mode: str, table: str = "trade_tape",
                      window: Optional[tuple[int, int]] = None) -> list[Trade]:
        """Insert trades and return the ones added.

        mode="append": each trade is a new print (live stream); it takes the next occurrence
        number for its key. mode="set": the trades are the complete set for `window`
        [start_us, end_us); only the per-key shortfall is inserted, so replays are no-ops.
        """
        self.assert_lease()
        batch = list(trades)
        if window is not None:
            batch = [t for t in batch if window[0] <= t.ts_us < window[1]]
        if not batch:
            return []
        by_symbol: dict[str, list[Trade]] = {}
        for trade in batch:
            by_symbol.setdefault(trade.symbol, []).append(trade)
        added: list[Trade] = []
        rows = []
        stamp = _now_utc()
        for symbol, items in by_symbol.items():
            lo = window[0] if window else min(t.ts_us for t in items)
            hi = window[1] if window else max(t.ts_us for t in items) + 1
            existing = self.key_counts(symbol, lo, hi, table=table)
            if mode == "set":
                wanted = Counter(t.key for t in items)
                seen: Counter = Counter()
                for trade in items:
                    seen[trade.key] += 1
                    occurrence = seen[trade.key] - 1
                    if occurrence < existing[trade.key] or occurrence >= wanted[trade.key]:
                        continue
                    rows.append(self._row(trade, occurrence, stamp))
                    added.append(trade)
            elif mode == "append":
                for trade in items:
                    occurrence = existing[trade.key]
                    existing[trade.key] += 1
                    rows.append(self._row(trade, occurrence, stamp))
                    added.append(trade)
            else:
                raise ValueError(f"unknown insert mode {mode!r}")
        self.tape.execute("BEGIN")
        try:
            self.tape.executemany(f"INSERT OR IGNORE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.tape.execute("COMMIT")
        except Exception:
            self.tape.execute("ROLLBACK")
            raise
        return added

    @staticmethod
    def _row(trade: Trade, occurrence: int, stamp: str) -> tuple:
        return (
            trade_id(trade.key, occurrence), trade.symbol, trade.contract, trade.ts_us, trade.ts_event_ns,
            trade.price_nanos, trade.size, trade.side, occurrence, trade.source, trade.sequence, stamp, trade.raw,
        )

    def trades_in_range(self, symbol: str, start_us: int, end_us: int, *, contract: Optional[str] = None,
                        table: str = "trade_tape") -> list[Trade]:
        sql = (f"SELECT symbol, contract, ts_event_ns, ts_us, price_nanos, size, side, source, sequence FROM {table} "
               "WHERE symbol = ? AND ts_us >= ? AND ts_us < ?")
        args: list = [symbol, int(start_us), int(end_us)]
        if contract is not None:
            sql += " AND contract = ?"
            args.append(contract)
        sql += " ORDER BY ts_us, rowid"
        return [Trade(*row) for row in self.tape.execute(sql, args).fetchall()]

    def contract_volumes(self, symbol: str, start_us: int, end_us: int) -> dict[str, int]:
        rows = self.tape.execute(
            "SELECT contract, SUM(size) FROM trade_tape WHERE symbol = ? AND ts_us >= ? AND ts_us < ? GROUP BY contract",
            (symbol, int(start_us), int(end_us)),
        ).fetchall()
        return {contract: int(volume) for contract, volume in rows}

    # ---- coverage ---------------------------------------------------------------------
    def add_coverage(self, symbol: str, start_us: int, end_us: int, source: str) -> None:
        if end_us <= start_us:
            return
        self.tape.execute(
            "INSERT INTO coverage VALUES (?, ?, ?, ?, ?)", (symbol, int(start_us), int(end_us), source, _now_utc())
        )

    def open_coverage(self, symbol: str, start_us: int, source: str) -> int:
        cur = self.tape.execute(
            "INSERT INTO coverage VALUES (?, ?, ?, ?, ?)", (symbol, int(start_us), int(start_us), source, _now_utc())
        )
        return int(cur.lastrowid)

    def extend_coverage(self, rowid: int, end_us: int) -> None:
        self.tape.execute("UPDATE coverage SET end_us = MAX(end_us, ?) WHERE rowid = ?", (int(end_us), int(rowid)))

    def coverage_intervals(self, symbol: str, start_us: int, end_us: int) -> list[tuple[int, int]]:
        rows = self.tape.execute(
            "SELECT start_us, end_us FROM coverage WHERE symbol = ? AND end_us > ? AND start_us < ? ORDER BY start_us",
            (symbol, int(start_us), int(end_us)),
        ).fetchall()
        merged: list[list[int]] = []
        for lo, hi in rows:
            lo, hi = max(lo, start_us), min(hi, end_us)
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        return [(lo, hi) for lo, hi in merged]

    def gaps(self, symbol: str, start_us: int, end_us: int) -> list[tuple[int, int]]:
        out = []
        cursor = int(start_us)
        for lo, hi in self.coverage_intervals(symbol, start_us, end_us):
            if lo > cursor:
                out.append((cursor, lo))
            cursor = max(cursor, hi)
        if cursor < end_us:
            out.append((cursor, int(end_us)))
        return out

    def minute_covered(self, symbol: str, minute_us: int) -> bool:
        return not self.gaps(symbol, minute_us, minute_us + MINUTE_US)

    # ---- front contract ---------------------------------------------------------------
    def front_contract(self, session: str, symbol: str) -> Optional[str]:
        row = self.tape.execute(
            "SELECT contract FROM front_contract_override WHERE session = ? AND symbol = ?", (session, symbol)
        ).fetchone()
        if row:
            return row[0]
        row = self.tape.execute(
            "SELECT contract FROM front_contract WHERE session = ? AND symbol = ?", (session, symbol)
        ).fetchone()
        return row[0] if row else None

    def freeze_front_contract(self, session: str, symbol: str, contract: str, method: str, volume: Optional[int]) -> str:
        """First value wins: a session's front contract never changes once frozen."""
        self.tape.execute(
            "INSERT OR IGNORE INTO front_contract VALUES (?, ?, ?, ?, ?, ?)",
            (session, symbol, contract, method, volume, _now_utc()),
        )
        return self.front_contract(session, symbol)

    def set_override(self, session: str, symbol: str, contract: str, reason: str) -> None:
        self.tape.execute("INSERT OR REPLACE INTO front_contract_override VALUES (?, ?, ?, ?)", (session, symbol, contract, reason))

    # ---- bars -------------------------------------------------------------------------
    def read_bar(self, symbol: str, minute_us: int) -> Optional[dict]:
        ts_utc, _ = minute_clocks(minute_us)
        row = self.hist.execute(
            f"SELECT {', '.join(BAR_FIELDS)}, source FROM v1_aggress_flow_1m WHERE ts_utc = ? AND symbol = ?",
            (ts_utc, symbol),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(BAR_FIELDS + ("source",), row))

    def write_bar(self, symbol: str, minute_us: int, bar: Optional[Bar], *, source: str, reason: str) -> str:
        """Upsert (or, with bar=None, remove) one minute. Logs every change to an existing row."""
        self.assert_lease()
        start = self.canonical_start_us()
        if start is None or minute_us < start:
            return "before_canonical_start"
        ts_utc, ts_et = minute_clocks(minute_us)
        before = self.read_bar(symbol, minute_us)
        after = None if bar is None else {**bar.as_dict(), "source": source}
        if before == after or (before is None and after is None):
            return "unchanged"
        if after is None:
            self.hist.execute("DELETE FROM v1_aggress_flow_1m WHERE ts_utc = ? AND symbol = ?", (ts_utc, symbol))
        else:
            self.hist.execute(
                "INSERT OR REPLACE INTO v1_aggress_flow_1m (ts_utc, ts_et, symbol, buy_contracts, sell_contracts, "
                "buy_flow, sell_flow, signed_contracts, signed_flow, trade_count, trade_price, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts_utc, ts_et, symbol, bar.buy_contracts, bar.sell_contracts, bar.buy_flow, bar.sell_flow,
                 bar.signed_contracts, bar.signed_flow, bar.trade_count, bar.trade_price, source),
            )
        if before is not None:
            self.tape.execute(
                "INSERT INTO flow_revisions (ts_utc, symbol, before_json, after_json, reason, revised_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (ts_utc, symbol, json.dumps(before), json.dumps(after) if after else None, reason, _now_utc()),
            )
            return "deleted" if after is None else "revised"
        return "inserted"

    def mark_sealed(self, symbol: str, minute_us: int, source: str) -> None:
        self.tape.execute("INSERT OR REPLACE INTO sealed_minutes VALUES (?, ?, ?, ?)", (symbol, int(minute_us), source, _now_utc()))

    def sealed_source(self, symbol: str, minute_us: int) -> Optional[str]:
        row = self.tape.execute("SELECT source FROM sealed_minutes WHERE symbol = ? AND minute_us = ?", (symbol, int(minute_us))).fetchone()
        return row[0] if row else None

    def last_sealed_minute(self, symbol: str) -> Optional[int]:
        row = self.tape.execute("SELECT MAX(minute_us) FROM sealed_minutes WHERE symbol = ?", (symbol,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def provisional_minutes(self, symbol: str, start_us: int, end_us: int) -> list[int]:
        rows = self.tape.execute(
            "SELECT minute_us FROM sealed_minutes WHERE symbol = ? AND source = ? AND minute_us >= ? AND minute_us < ? ORDER BY minute_us",
            (symbol, BAR_SOURCE_PROVISIONAL, int(start_us), int(end_us)),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def revisions(self) -> list[dict]:
        rows = self.tape.execute("SELECT ts_utc, symbol, before_json, after_json, reason FROM flow_revisions ORDER BY id").fetchall()
        return [dict(zip(("ts_utc", "symbol", "before", "after", "reason"), r)) for r in rows]


__all__ = ["BAR_FIELDS", "BAR_SOURCE_CANONICAL", "FlowStore", "LeaseLost", "minute_clocks"]
