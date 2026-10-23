"""
SQLite store for X macro stream subscriptions.

Stores handles (tier + mode) and canonical keywords.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(
    os.getenv(
        "X_MACRO_DB_PATH",
        str(Path.home() / ".market_data" / "x_macro_stream.db"),
    )
)

MODE_ALL = "all"
MODE_GATED = "gated"


@dataclass(frozen=True)
class HandleEntry:
    handle: str
    tier: int
    mode: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_handle(handle: str) -> str:
    handle = handle.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    return handle.lower()


def normalize_keyword(keyword: str) -> str:
    return " ".join(keyword.strip().split()).lower()


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS handles (
                handle TEXT PRIMARY KEY,
                tier INTEGER NOT NULL,
                mode TEXT NOT NULL,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS keywords (
                keyword TEXT PRIMARY KEY,
                added_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tweet_labels (
                tweet_id TEXT PRIMARY KEY,
                username TEXT,
                text TEXT,
                tweet_url TEXT,
                telegram_message_id INTEGER,
                label TEXT,
                score INTEGER,
                labeled_by INTEGER,
                labeled_at TEXT,
                forwarded_message_id INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(tweet_labels)").fetchall()
        }
        if "score" not in columns:
            cursor.execute("ALTER TABLE tweet_labels ADD COLUMN score INTEGER")
        conn.commit()
    finally:
        conn.close()


def seed_defaults(db_path: Optional[Path] = None) -> None:
    """Seed foundation handles + keywords on first run."""
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM handles")
        handle_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM keywords")
        keyword_count = int(cursor.fetchone()["cnt"])

        if handle_count == 0:
            now = _utc_now_iso()
            for entry in _default_handles():
                handle = normalize_handle(entry.handle)
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO handles (handle, tier, mode, added_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (handle, entry.tier, entry.mode, now, now),
                )
            logger.info("Seeded %s default handles", len(_default_handles()))

        if keyword_count == 0:
            now = _utc_now_iso()
            for keyword in _default_keywords():
                cursor.execute(
                    "INSERT OR IGNORE INTO keywords (keyword, added_at) VALUES (?, ?)",
                    (keyword, now),
                )
            logger.info("Seeded %s default keywords", len(_default_keywords()))

        conn.commit()
    finally:
        conn.close()


def list_handles(db_path: Optional[Path] = None) -> List[HandleEntry]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute("SELECT handle, tier, mode FROM handles ORDER BY tier, handle").fetchall()
        return [HandleEntry(row["handle"], int(row["tier"]), row["mode"]) for row in rows]
    finally:
        conn.close()


def list_handles_by_mode(
    mode: str,
    db_path: Optional[Path] = None,
) -> List[HandleEntry]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT handle, tier, mode FROM handles WHERE mode = ? ORDER BY tier, handle",
            (mode,),
        ).fetchall()
        return [HandleEntry(row["handle"], int(row["tier"]), row["mode"]) for row in rows]
    finally:
        conn.close()


def add_handle(
    handle: str,
    tier: int,
    mode: str,
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    handle = normalize_handle(handle)
    now = _utc_now_iso()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO handles (handle, tier, mode, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                tier=excluded.tier,
                mode=excluded.mode,
                updated_at=excluded.updated_at
            """,
            (handle, int(tier), mode, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def remove_handle(handle: str, db_path: Optional[Path] = None) -> bool:
    init_db(db_path)
    handle = normalize_handle(handle)
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute("DELETE FROM handles WHERE handle = ?", (handle,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_keywords(db_path: Optional[Path] = None) -> List[str]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute("SELECT keyword FROM keywords ORDER BY keyword").fetchall()
        return [row["keyword"] for row in rows]
    finally:
        conn.close()


def add_keyword(keyword: str, db_path: Optional[Path] = None) -> None:
    init_db(db_path)
    keyword = normalize_keyword(keyword)
    now = _utc_now_iso()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO keywords (keyword, added_at) VALUES (?, ?)",
            (keyword, now),
        )
        conn.commit()
    finally:
        conn.close()


def remove_keyword(keyword: str, db_path: Optional[Path] = None) -> bool:
    init_db(db_path)
    keyword = normalize_keyword(keyword)
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def insert_tweet_label(
    tweet_id: str,
    username: Optional[str],
    text: Optional[str],
    tweet_url: Optional[str],
    telegram_message_id: Optional[int],
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO tweet_labels (
                tweet_id, username, text, tweet_url,
                telegram_message_id, label, labeled_by, labeled_at,
                forwarded_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (
                tweet_id,
                username,
                text,
                tweet_url,
                telegram_message_id,
                _utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_tweet_label(tweet_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM tweet_labels WHERE tweet_id = ?",
            (tweet_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_approved_but_unforwarded(
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> List[dict]:
    """Return approved tweets that never got a public-group forward id."""
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM tweet_labels
            WHERE label = 'approved'
              AND forwarded_message_id IS NULL
            ORDER BY labeled_at DESC, created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_tweet_label(
    tweet_id: str,
    label: str,
    labeled_by: Optional[int],
    score: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE tweet_labels
            SET label = ?, score = ?, labeled_by = ?, labeled_at = ?
            WHERE tweet_id = ? AND label IS NULL
            """,
            (label, score, labeled_by, _utc_now_iso(), tweet_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_forwarded(
    tweet_id: str,
    forwarded_message_id: Optional[int],
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            UPDATE tweet_labels
            SET forwarded_message_id = ?
            WHERE tweet_id = ?
            """,
            (forwarded_message_id, tweet_id),
        )
        conn.commit()
    finally:
        conn.close()


def _default_handles() -> List[HandleEntry]:
    return [
        # Tier 0 - always send
        HandleEntry("federalreserve", 0, MODE_ALL),
        HandleEntry("sffed", 0, MODE_ALL),
        HandleEntry("ChicagoFed", 0, MODE_ALL),
        HandleEntry("DallasFed", 0, MODE_ALL),
        HandleEntry("philadelphiafed", 0, MODE_ALL),
        HandleEntry("PhilFedResearch", 0, MODE_ALL),
        HandleEntry("MaryDalyEcon", 0, MODE_ALL),
        HandleEntry("Austan_Goolsbee", 0, MODE_ALL),
        HandleEntry("annap_2013", 0, MODE_ALL),
        HandleEntry("RaphaelBostic", 0, MODE_ALL),
        HandleEntry("neelkashkari", 0, MODE_ALL),
        HandleEntry("WhiteHouse", 0, MODE_ALL),
        HandleEntry("POTUS", 0, MODE_ALL),
        HandleEntry("StateDept", 0, MODE_ALL),
        # Tier 1 - global central banks + institutions
        HandleEntry("ecb", 1, MODE_ALL),
        HandleEntry("ECB_Research", 1, MODE_ALL),
        HandleEntry("Lagarde", 1, MODE_ALL),
        HandleEntry("bankofengland", 1, MODE_ALL),
        HandleEntry("BoE_PressOffice", 1, MODE_ALL),
        HandleEntry("Bank_of_Japan_e", 1, MODE_ALL),
        HandleEntry("IMFNews", 1, MODE_ALL),
        HandleEntry("BIS_org", 1, MODE_ALL),
        HandleEntry("OECD", 1, MODE_ALL),
        HandleEntry("OECDeconomy", 1, MODE_ALL),
        HandleEntry("worldbankgroup", 1, MODE_ALL),
        HandleEntry("EU_Eurostat", 1, MODE_ALL),
        HandleEntry("IEA", 1, MODE_ALL),
        HandleEntry("OPECSecretariat", 1, MODE_ALL),
        # Tier 2 - fast headlines (keyword gated)
        HandleEntry("DeItaone", 2, MODE_GATED),
        HandleEntry("LiveSquawk", 2, MODE_GATED),
        HandleEntry("FirstSquawk", 2, MODE_GATED),
        HandleEntry("financialjuice", 2, MODE_GATED),
        # Tier 3 - journalists
        HandleEntry("NickTimiraos", 3, MODE_GATED),
        HandleEntry("JChengWSJ", 3, MODE_GATED),
        HandleEntry("fcastofthemonth", 3, MODE_GATED),
        # Tier 4 - prediction markets
        HandleEntry("Polymarket", 4, MODE_GATED),
        HandleEntry("Kalshi", 4, MODE_GATED),
        HandleEntry("KalshiSports", 4, MODE_GATED),
        HandleEntry("PredictIt", 4, MODE_GATED),
        HandleEntry("ManifoldMarkets", 4, MODE_GATED),
        # Tier 5 - market plumbing
        HandleEntry("CMEGroup", 5, MODE_GATED),
        HandleEntry("ICE_Markets", 5, MODE_GATED),
        HandleEntry("SIFMA", 5, MODE_GATED),
    ]


def _default_keywords() -> List[str]:
    keywords = [
        # Rates/curve
        "10y",
        "10-year",
        "2y",
        "30y",
        "yield",
        "yields",
        "curve",
        "term premium",
        "real yields",
        "breakeven",
        "TIPS",
        "auction",
        "refunding",
        "TBAC",
        "QRA",
        "buyback",
        # Fed
        "FOMC",
        "Powell",
        "dot plot",
        "minutes",
        "rate cut",
        "rate hike",
        "hold",
        "QT",
        "QE",
        "balance sheet",
        # Inflation/growth data
        "CPI",
        "PCE",
        "NFP",
        "payrolls",
        "unemployment",
        "jobless claims",
        "GDP",
        "ISM",
        "PMI",
        "retail sales",
        # Fiscal/politics
        "tariff",
        "sanctions",
        "shutdown",
        "debt ceiling",
        "deficit",
        "Treasury issuance",
        # Risk/geopolitics/energy
        "OPEC",
        "oil",
        "Brent",
        "WTI",
        "strait",
        "attack",
        "ceasefire",
        "invasion",
    ]
    return [normalize_keyword(k) for k in keywords]
