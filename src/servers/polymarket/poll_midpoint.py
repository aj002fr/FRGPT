"""
Polling utilities for Polymarket CLOB midpoint prices.

Primary use: near-live probability tracking via REST polling.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schema import POLYMARKET_API_BASE_URL

logger = logging.getLogger(__name__)


def _build_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://polymarket.com/",
        "Origin": "https://polymarket.com",
    }


def _request_json(url: str, timeout: int = 10) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    req = urllib.request.Request(url, method="GET")
    for key, value in _build_headers().items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return json.loads(data), response.status
    except urllib.error.HTTPError as exc:
        logger.warning("CLOB HTTP error %s for %s", exc.code, url)
        return None, exc.code
    except urllib.error.URLError as exc:
        logger.warning("CLOB URL error for %s: %s", url, exc.reason)
        return None, None


def fetch_midpoint(token_id: str, timeout: int = 10) -> Tuple[Optional[float], Optional[int]]:
    params = urllib.parse.urlencode({"token_id": token_id})
    url = f"{POLYMARKET_API_BASE_URL}/midpoint?{params}"
    payload, status = _request_json(url, timeout=timeout)
    if not payload or "mid" not in payload:
        return None, status
    try:
        return float(payload["mid"]), status
    except (TypeError, ValueError):
        return None, status


def fetch_price(token_id: str, side: str, timeout: int = 10) -> Tuple[Optional[float], Optional[int]]:
    params = urllib.parse.urlencode({"token_id": token_id, "side": side})
    url = f"{POLYMARKET_API_BASE_URL}/price?{params}"
    payload, status = _request_json(url, timeout=timeout)
    if not payload or "price" not in payload:
        return None, status
    try:
        return float(payload["price"]), status
    except (TypeError, ValueError):
        return None, status


def ensure_polling_tables(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id TEXT NOT NULL,
            market_slug TEXT,
            title TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_prob_timeseries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            condition_id TEXT,
            market_slug TEXT,
            observed_ts TEXT NOT NULL,
            mid REAL,
            best_bid REAL,
            best_ask REAL,
            source TEXT NOT NULL,
            raw_payload TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tracked_markets_condition_id
        ON tracked_markets (condition_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tracked_markets_active
        ON tracked_markets (active)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_prob_ts_token
        ON market_prob_timeseries (token_id, observed_ts)
        """
    )
    conn.commit()


def upsert_tracked_market(
    conn: sqlite3.Connection,
    condition_id: str,
    market_slug: Optional[str],
    title: Optional[str],
    yes_token_id: Optional[str],
    no_token_id: Optional[str],
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id FROM tracked_markets WHERE condition_id = ?
        """,
        (condition_id,),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            """
            UPDATE tracked_markets
            SET market_slug = ?, title = ?, yes_token_id = ?, no_token_id = ?, active = 1
            WHERE condition_id = ?
            """,
            (market_slug, title, yes_token_id, no_token_id, condition_id),
        )
    else:
        cursor.execute(
            """
            INSERT INTO tracked_markets
            (condition_id, market_slug, title, yes_token_id, no_token_id, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (condition_id, market_slug, title, yes_token_id, no_token_id),
        )
    conn.commit()


def mark_market_inactive(conn: sqlite3.Connection, condition_id: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE tracked_markets SET active = 0 WHERE condition_id = ?
        """,
        (condition_id,),
    )
    conn.commit()


def insert_timeseries_row(
    conn: sqlite3.Connection,
    token_id: str,
    condition_id: Optional[str],
    market_slug: Optional[str],
    observed_ts: str,
    mid: Optional[float],
    best_bid: Optional[float],
    best_ask: Optional[float],
    source: str,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO market_prob_timeseries
        (token_id, condition_id, market_slug, observed_ts, mid, best_bid, best_ask, source, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token_id,
            condition_id,
            market_slug,
            observed_ts,
            mid,
            best_bid,
            best_ask,
            source,
            json.dumps(raw_payload) if raw_payload is not None else None,
        ),
    )
    conn.commit()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_default_db_path() -> Path:
    return Path(
        os.getenv(
            "POLYMARKET_MARKETS_DB_PATH",
            str(Path.home() / ".market_data" / "polymarket_markets.db"),
        )
    )


def query_recent_timeseries(
    conn: sqlite3.Connection,
    token_id: str,
    *,
    hours: int = 24,
    max_points: int = 240,
) -> List[Dict[str, Any]]:
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))).isoformat()
    rows = conn.execute(
        """
        SELECT observed_ts, mid
        FROM market_prob_timeseries
        WHERE token_id = ? AND mid IS NOT NULL AND observed_ts >= ?
        ORDER BY observed_ts ASC
        """,
        (token_id, cutoff_ts),
    ).fetchall()
    series = []
    for row in rows:
        observed_ts = row["observed_ts"] if isinstance(row, sqlite3.Row) else row[0]
        mid = row["mid"] if isinstance(row, sqlite3.Row) else row[1]
        series.append({"observed_ts": str(observed_ts), "mid": float(mid)})
    if len(series) <= max_points or max_points <= 0:
        return series

    step = max(1, len(series) // max_points)
    sampled = series[::step]
    if sampled[-1]["observed_ts"] != series[-1]["observed_ts"]:
        sampled.append(series[-1])
    return sampled
