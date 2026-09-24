"""Durable 1-minute V1 aggress buy/sell volume history.

Writes only under workspace/analysis/aggression_v1_live/, unless FLOW_HISTORY_DB_PATH names
the database explicitly (read at import; the local runtime and shadow runs set it).
Fail-closed: callers must treat every public function as best-effort.

buy_contracts / buy_flow = aggress buys (took the ask, Databento side B)
sell_contracts / sell_flow = aggress sells (hit the bid, Databento side A)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from scripts.aggression_dashboard.core import (
    CACHE_DIR,
    EASTERN,
    SYMBOL_BAR_COLUMNS,
    SYMBOL_PARENT_MAP,
    load_dv01_table,
    trades_to_symbol_minute_bars,
)
from scripts.aggression_dashboard.core import _select_front_month_per_day

logger = logging.getLogger(__name__)

FLOW_HISTORY_DB_PATH_ENV = "FLOW_HISTORY_DB_PATH"
DB_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_live"
DB_PATH = Path(os.environ.get(FLOW_HISTORY_DB_PATH_ENV) or DB_DIR / "flow_history.sqlite")
CACHE_NAME_RE = re.compile(r"^trades_v2_(ZF_FUT|ZT_FUT|ZN_FUT|ZB_FUT)_(\d{8})\.parquet$")
PARENT_TO_SYMBOL = {
    "ZF_FUT": "FV",
    "ZT_FUT": "TU",
    "ZN_FUT": "TY",
    "ZB_FUT": "US",
}

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS v1_aggress_flow_1m (
    ts_utc TEXT NOT NULL,
    ts_et TEXT NOT NULL,
    symbol TEXT NOT NULL,
    buy_contracts REAL NOT NULL,
    sell_contracts REAL NOT NULL,
    buy_flow REAL NOT NULL,
    sell_flow REAL NOT NULL,
    signed_contracts REAL NOT NULL,
    signed_flow REAL NOT NULL,
    trade_count INTEGER NOT NULL,
    trade_price REAL,
    source TEXT,
    PRIMARY KEY (ts_utc, symbol)
);
CREATE INDEX IF NOT EXISTS idx_v1_flow_symbol_ts ON v1_aggress_flow_1m(symbol, ts_utc);

CREATE TABLE IF NOT EXISTS v1_flow_history_backfill (
    symbol TEXT NOT NULL,
    utc_date TEXT NOT NULL,
    rows INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (symbol, utc_date)
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


_READERS = threading.local()
STATUS_TTL_SEC = 60.0
_STATUS_MEMO: dict[str, tuple[float, dict[str, Any]]] = {}


def read_connection(db_path: Path = DB_PATH) -> sqlite3.Connection | None:
    """Thread-local read-only connection: no DDL, no journal-mode change, no process-wide lock.

    Plain path open (a `file:` URI breaks on UNC hosts); `query_only` refuses writes. None if
    the database does not exist (it is never created here).
    """
    key = str(db_path)
    conns = getattr(_READERS, "conns", None)
    if conns is None:
        conns = _READERS.conns = {}
    conn = conns.get(key)
    if conn is not None:
        return conn
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(key, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conns[key] = conn
    return conn


def latest_bar(symbol: str = "TY", *, db_path: Path = DB_PATH) -> tuple[str | None, str | None]:
    """(ts_utc, source) of the newest 1-minute bar; CURVE means any symbol. Index seek, no scan."""
    conn = read_connection(db_path)
    if conn is None:
        return None, None
    wanted = str(symbol or "TY").strip().upper()
    try:
        if wanted == "CURVE":
            row = conn.execute("SELECT ts_utc, source FROM v1_aggress_flow_1m ORDER BY ts_utc DESC LIMIT 1").fetchone()
        else:
            row = conn.execute(
                "SELECT ts_utc, source FROM v1_aggress_flow_1m WHERE symbol = ? ORDER BY ts_utc DESC LIMIT 1", (wanted,)
            ).fetchone()
    except sqlite3.Error:
        return None, None
    return (None, None) if row is None else (str(row[0]), row[1])


def _as_utc_ts(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _clocks(value: Any) -> tuple[str, str]:
    utc = _as_utc_ts(value)
    return utc.isoformat(), utc.tz_convert(EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def persist_completed_bars(
    bars_by_symbol: dict[str, pd.DataFrame] | None,
    *,
    before: Any = None,
    after: Any = None,
    source: str = "live",
    db_path: Path = DB_PATH,
) -> int:
    """Upsert completed 1-minute buy/sell bars. Returns rows written, or 0 on failure."""
    if not bars_by_symbol:
        return 0
    cutoff = None if before is None else _as_utc_ts(before)
    floor = None if after is None else _as_utc_ts(after)
    rows: list[tuple[Any, ...]] = []
    for symbol, frame in bars_by_symbol.items():
        if symbol not in SYMBOL_PARENT_MAP or frame is None or frame.empty:
            continue
        work = frame.copy()
        work.index = pd.to_datetime(work.index, utc=True)
        if floor is not None:
            work = work.loc[work.index >= floor]
        if cutoff is not None:
            work = work.loc[work.index < cutoff]
        if work.empty:
            continue
        for ts, rec in work.iterrows():
            ts_utc, ts_et = _clocks(ts)
            rows.append((
                ts_utc,
                ts_et,
                symbol,
                _finite(rec.get("buy_contracts")),
                _finite(rec.get("sell_contracts")),
                _finite(rec.get("buy_flow")),
                _finite(rec.get("sell_flow")),
                _finite(rec.get("signed_contracts")),
                _finite(rec.get("signed_flow")),
                int(_finite(rec.get("trade_count"))),
                None if pd.isna(rec.get("trade_price")) else _finite(rec.get("trade_price")),
                source,
            ))
    if not rows:
        return 0
    try:
        with _LOCK:
            conn = _connect(db_path)
            try:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO v1_aggress_flow_1m (
                        ts_utc, ts_et, symbol, buy_contracts, sell_contracts,
                        buy_flow, sell_flow, signed_contracts, signed_flow,
                        trade_count, trade_price, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.commit()
            finally:
                conn.close()
        return len(rows)
    except Exception:
        logger.exception("V1 flow history persist failed")
        return 0


def load_completed_symbol_bars(
    symbols: Any,
    *,
    start: Any = None,
    end: Any = None,
    db_path: Path = DB_PATH,
) -> dict[str, pd.DataFrame]:
    """Read completed 1-minute symbol bars for live hole-heal. Read-only."""
    wanted = [str(symbol).strip().upper() for symbol in (symbols or [])]
    out: dict[str, pd.DataFrame] = {}
    for symbol in wanted:
        if symbol not in SYMBOL_PARENT_MAP:
            continue
        rows = query_flow_history(symbol, start=start, end=end, db_path=db_path)
        if not rows:
            out[symbol] = pd.DataFrame(columns=list(SYMBOL_BAR_COLUMNS))
            continue
        frame = pd.DataFrame(rows)
        frame.index = pd.to_datetime(frame["ts_utc"], utc=True)
        frame.sort_index(inplace=True)
        out[symbol] = pd.DataFrame({
            "signed_flow": frame["signed_flow"].astype(float),
            "signed_contracts": frame["signed_contracts"].astype(float),
            "buy_flow": frame["buy_flow"].astype(float),
            "sell_flow": frame["sell_flow"].astype(float),
            "buy_contracts": frame["buy_contracts"].astype(float),
            "sell_contracts": frame["sell_contracts"].astype(float),
            "trade_price": frame["trade_price"].astype(float) if "trade_price" in frame else float("nan"),
            "trade_count": frame["trade_count"].fillna(0).astype(int),
        })
    return out


def query_flow_history(
    symbol: str = "TY",
    *,
    start: Any = None,
    end: Any = None,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return per-minute aggress buy/sell volume. symbol=CURVE sums FV/TU/TY/US."""
    wanted = str(symbol or "TY").strip().upper()
    filters: list[str] = []
    params: list[Any] = []
    if wanted != "CURVE":
        filters.append("symbol = ?")
        params.append(wanted)
    if start is not None:
        filters.append("ts_utc >= ?")
        params.append(_as_utc_ts(start).isoformat())
    if end is not None:
        filters.append("ts_utc < ?")
        params.append(_as_utc_ts(end).isoformat())
    clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    try:
        conn = read_connection(db_path)
        if conn is None:
            return []
        if wanted == "CURVE":
            sql = f"""
            SELECT ts_utc, ts_et,
                   SUM(buy_contracts) AS buy_contracts,
                   SUM(sell_contracts) AS sell_contracts,
                   SUM(buy_flow) AS buy_flow,
                   SUM(sell_flow) AS sell_flow,
                   SUM(signed_contracts) AS signed_contracts,
                   SUM(signed_flow) AS signed_flow,
                   SUM(trade_count) AS trade_count
            FROM v1_aggress_flow_1m
            {clause}
            GROUP BY ts_utc, ts_et
            ORDER BY ts_utc
            """
        else:
            sql = f"""
            SELECT ts_utc, ts_et, symbol, buy_contracts, sell_contracts,
                   buy_flow, sell_flow, signed_contracts, signed_flow,
                   trade_count, trade_price, source
            FROM v1_aggress_flow_1m
            {clause}
            ORDER BY ts_utc
            """
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("V1 flow history query failed")
        return []


def history_status(*, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Coverage summary for the durable store (full scans; memoized for STATUS_TTL_SEC)."""
    empty = {
        "path": str(db_path),
        "row_count": 0,
        "symbols": {},
        "min_ts_utc": None,
        "max_ts_utc": None,
    }
    memo = _STATUS_MEMO.get(str(db_path))
    if memo is not None and memo[0] > time.monotonic():
        return memo[1]
    try:
        conn = read_connection(db_path)
        if conn is None:
            return empty
        total = conn.execute("SELECT COUNT(*) AS n FROM v1_aggress_flow_1m").fetchone()
        bounds = conn.execute(
            "SELECT MIN(ts_utc) AS min_ts, MAX(ts_utc) AS max_ts FROM v1_aggress_flow_1m"
        ).fetchone()
        per = conn.execute(
            """
            SELECT symbol, COUNT(*) AS n, MIN(ts_utc) AS min_ts, MAX(ts_utc) AS max_ts
            FROM v1_aggress_flow_1m
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
        result = {
            "path": str(db_path),
            "row_count": int(total["n"] if total else 0),
            "min_ts_utc": None if bounds is None else bounds["min_ts"],
            "max_ts_utc": None if bounds is None else bounds["max_ts"],
            "symbols": {
                str(row["symbol"]): {
                    "row_count": int(row["n"]),
                    "min_ts_utc": row["min_ts"],
                    "max_ts_utc": row["max_ts"],
                }
                for row in per
            },
            "fields": {
                "buy_contracts": "aggress buy volume in contracts (took the ask)",
                "sell_contracts": "aggress sell volume in contracts (hit the bid)",
                "buy_flow": "aggress buy $DV01",
                "sell_flow": "aggress sell $DV01",
            },
        }
        _STATUS_MEMO[str(db_path)] = (time.monotonic() + STATUS_TTL_SEC, result)
        return result
    except Exception:
        logger.exception("V1 flow history status failed")
        return empty


def list_cached_utc_days(cache_dir: Path = CACHE_DIR) -> list[tuple[str, datetime, Path]]:
    """Return (symbol, utc_midnight, path) for existing V1 trade-cache files."""
    if not cache_dir.exists():
        return []
    found: list[tuple[str, datetime, Path]] = []
    for path in sorted(cache_dir.glob("trades_v2_*.parquet")):
        match = CACHE_NAME_RE.match(path.name)
        if match is None:
            continue
        symbol = PARENT_TO_SYMBOL[match.group(1)]
        day = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=timezone.utc)
        found.append((symbol, day, path))
    return found


def _backfill_done(conn: sqlite3.Connection, symbol: str, utc_date: str) -> bool:
    row = conn.execute(
        "SELECT rows FROM v1_flow_history_backfill WHERE symbol = ? AND utc_date = ?",
        (symbol, utc_date),
    ).fetchone()
    return row is not None and int(row["rows"]) > 0


def _mark_backfill(conn: sqlite3.Connection, symbol: str, utc_date: str, rows: int) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO v1_flow_history_backfill (symbol, utc_date, rows, completed_at)
        VALUES (?, ?, ?, ?)
        """,
        (symbol, utc_date, int(rows), datetime.now(timezone.utc).isoformat()),
    )


def backfill_from_trade_cache(
    *,
    cache_dir: Path = CACHE_DIR,
    db_path: Path = DB_PATH,
    dv01_table: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build history from existing trades_v2 parquet only. Does not call Databento."""
    units = dv01_table or load_dv01_table()
    cached = list_cached_utc_days(cache_dir)
    stats = {"files": len(cached), "written": 0, "skipped": 0, "errors": 0, "dates": []}
    for symbol, day, path in cached:
        utc_date = day.strftime("%Y-%m-%d")
        try:
            with _LOCK:
                conn = _connect(db_path)
                try:
                    already = _backfill_done(conn, symbol, utc_date)
                finally:
                    conn.close()
            if already:
                stats["skipped"] += 1
                continue
            raw = pd.read_parquet(path)
            raw["ts"] = pd.to_datetime(raw["ts"], utc=True)
            filtered, _front = _select_front_month_per_day(raw)
            filtered["dashboard_symbol"] = symbol
            bars, _ = trades_to_symbol_minute_bars(
                filtered,
                symbol,
                dv01_unit=float(units.get(symbol, 1.0)),
            )
            written = persist_completed_bars(
                {symbol: bars},
                source="cache",
                db_path=db_path,
            )
            with _LOCK:
                conn = _connect(db_path)
                try:
                    _mark_backfill(conn, symbol, utc_date, written)
                    conn.commit()
                finally:
                    conn.close()
            stats["written"] += written
            stats["dates"].append(f"{symbol}:{utc_date}:{written}")
        except Exception:
            stats["errors"] += 1
            logger.exception("V1 flow history cache backfill failed for %s %s", symbol, utc_date)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
    result = backfill_from_trade_cache()
    print(result)
    print(history_status())
