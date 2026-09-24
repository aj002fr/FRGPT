#!/usr/bin/env python3
"""
Scrape Iran/Israel/Lebanon conflict headlines from the Telegram distribution chat.

Uses the Telegram Client API (telethon) to pull message history from chat
-4611662021, filters to conflict-related messages, classifies the source
type (forwarded tweet, financial juice, BBG headline, discussion, etc.),
and stores them in a SQLite DB.

First run requires interactive authentication (code sent to your Telegram).
Subsequent runs reuse the saved session.

Usage:
    python scripts/scrape_iran_headlines.py
    python scripts/scrape_iran_headlines.py --limit 5000
    python scripts/scrape_iran_headlines.py --since 2026-03-01
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_API_ID = 33915885
TELEGRAM_API_HASH = "f16338a7cfb5fba6f11e3fd3211b04ce"
CHAT_ID = -4611662021
SESSION_PATH = str(Path.home() / ".market_data" / "telegram_scraper_session")
DB_PATH = PROJECT_ROOT / "workspace" / "iran_headlines.db"
HORMUZ_TRACKER_PATH = Path(r"Z:\Aastha\hormuz_tracker.xlsx")
AUTO_SINCE_OVERLAP_HOURS = 1

BROAD_KEYWORDS = [
    "iran", "iranian", "tehran", "irgc", "khamenei", "ayatollah", "hormuz",
    "israel", "israeli", "idf", "netanyahu", "jerusalem",
    "lebanon", "lebanese", "hezbollah", "beirut", "nasrallah",
    "houthi", "houthis",
    "gaza", "hamas",
]

NOISE_PATTERNS = [
    re.compile(r"^/\w+"),
    re.compile(r"^\d{1,3}\.\d+$"),
]

MIN_TEXT_LENGTH = 10

_RE_TWEET_BOT = re.compile(r"^\*\*@(\w+)\*\*\s")
_RE_BBG_HEADLINE = re.compile(r"^\*[A-Z]")
_RE_FJ_SUFFIX = re.compile(r"\|\[FJ\]\(https?://t\.me/FinancialJuice\)\s*$")
_RE_TIME_PREFIX = re.compile(r"^\d{1,2}:\d{2}\s*(AM|PM)?\s*\*?")


def classify_source(text: str, fwd_chat_title: Optional[str], sender_username: Optional[str]) -> str:
    if fwd_chat_title and "financialjuice" in fwd_chat_title.lower():
        return "forwarded_financialjuice"

    if sender_username == "FRGPboT":
        m = _RE_TWEET_BOT.match(text)
        if m:
            handle = m.group(1).lower()
            if handle in ("firstsquawk", "deltone", "deitaone"):
                return "forwarded_firstsquawk"
            if handle == "livesquawk":
                return "forwarded_livesquawk"
            if handle == "financialjuice":
                return "forwarded_financialjuice"
            return f"forwarded_tweet_{handle}"
        return "forwarded_tweet"

    if _RE_FJ_SUFFIX.search(text):
        return "pasted_financialjuice"

    stripped = text.strip()
    if _RE_TIME_PREFIX.match(stripped):
        stripped = _RE_TIME_PREFIX.sub("", stripped).strip()

    if _RE_BBG_HEADLINE.match(stripped):
        return "bbg_headline"

    if stripped.startswith("ALERT ") or stripped.startswith("BREAKING"):
        return "bbg_headline"

    lower = stripped.lower()
    conversational = [
        "i think", "i feel", "imo", "can you", "can we", "let's",
        "we should", "agree", "what do you", "pls ", "please ",
        "looking for", "checking", "any thoughts", "ref,",
        "score you think", "what proportion", "does this fade",
        "tldr:", "overnight timeline",
    ]
    if any(marker in lower for marker in conversational):
        return "discussion"

    if len(stripped) < 30 and not any(c.isupper() for c in stripped[:5]):
        return "discussion"

    upper_ratio = sum(1 for c in stripped[:60] if c.isupper()) / max(len(stripped[:60]), 1)
    if upper_ratio > 0.55:
        return "bbg_headline"

    return "other"


def _is_conflict_related(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in BROAD_KEYWORDS)


def _is_noise(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_LENGTH:
        return True
    for pattern in NOISE_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def _clean_text(text: str) -> str:
    cleaned = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return cleaned.strip()


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS headlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'other',
            telegram_message_id INTEGER,
            UNIQUE(timestamp, text)
        )
    """)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(headlines)").fetchall()}
    if "source" not in existing_cols:
        conn.execute("ALTER TABLE headlines ADD COLUMN source TEXT NOT NULL DEFAULT 'other'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_headlines_timestamp ON headlines(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_headlines_source ON headlines(source)")
    conn.commit()
    return conn


def insert_headline(
    conn: sqlite3.Connection,
    ts: str,
    text: str,
    source: str,
    msg_id: Optional[int] = None,
) -> bool:
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO headlines (timestamp, text, source, telegram_message_id) VALUES (?, ?, ?, ?)",
            (ts, text, source, msg_id),
        )
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        return False


def _message_forward_chat_title(message: Any) -> Optional[str]:
    if message.forward and message.forward.chat:
        return getattr(message.forward.chat, "title", None)
    return None


def _message_sender_username(message: Any) -> Optional[str]:
    if message.sender:
        return getattr(message.sender, "username", None)
    return None


def process_telegram_message(conn: sqlite3.Connection, message: Any) -> tuple[bool, Optional[str], str]:
    """Clean, filter, classify, and insert one Telegram message.

    Returns:
        (inserted, source, status), where status is useful for stream logging.
    """
    if not getattr(message, "text", None):
        return False, None, "no_text"

    cleaned = _clean_text(message.text)
    if _is_noise(cleaned):
        return False, None, "noise"
    if not _is_conflict_related(cleaned):
        return False, None, "not_topic"

    source = classify_source(
        cleaned,
        _message_forward_chat_title(message),
        _message_sender_username(message),
    )
    ts_utc = message.date.astimezone(timezone.utc).isoformat()
    inserted = insert_headline(conn, ts_utc, cleaned, source, message.id)
    return inserted, source, "inserted" if inserted else "duplicate"


def latest_headline_timestamp(db_path: Path) -> Optional[datetime]:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT MAX(timestamp) FROM headlines").fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(str(row[0])).astimezone(timezone.utc)
    except ValueError:
        return None


def auto_since_from_db(db_path: Path) -> Optional[datetime]:
    latest = latest_headline_timestamp(db_path)
    if latest is None:
        return None
    return latest - timedelta(hours=AUTO_SINCE_OVERLAP_HOURS)


def refresh_hormuz_flows(
    db_path: Path,
    excel_path: Path = HORMUZ_TRACKER_PATH,
) -> int:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required to refresh Hormuz flow data") from exc

    if not excel_path.exists():
        raise FileNotFoundError(f"Hormuz tracker workbook not found: {excel_path}")

    df = pd.read_excel(str(excel_path), sheet_name="Dynamic")
    date_cols = [col for col in df.columns if isinstance(col, datetime)]
    if not date_cols:
        raise ValueError(f"No date columns found in {excel_path}")

    records = []
    for _, row in df.iterrows():
        level = int(row["Level"])
        series = str(row["Series"]).strip()
        ticker = str(row["Ticker"]).strip() if pd.notna(row["Ticker"]) else None
        for dt in date_cols:
            value = row[dt]
            if pd.isna(value):
                value = 0
            records.append((dt.strftime("%Y-%m-%d"), series, level, ticker, int(value)))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hormuz_flows (
                date TEXT NOT NULL,
                series TEXT NOT NULL,
                level INTEGER NOT NULL,
                ticker TEXT,
                value INTEGER NOT NULL,
                PRIMARY KEY (date, series)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hormuz_date ON hormuz_flows(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hormuz_series ON hormuz_flows(series)")
        conn.execute("DELETE FROM hormuz_flows")
        conn.executemany(
            """
            INSERT OR REPLACE INTO hormuz_flows
                (date, series, level, ticker, value)
            VALUES (?, ?, ?, ?, ?)
            """,
            records,
        )
        conn.commit()

    return len(records)


async def scrape_chat(
    limit: Optional[int] = None,
    since: Optional[datetime] = None,
    db_path: Path = DB_PATH,
) -> int:
    from telethon import TelegramClient

    session_dir = Path(SESSION_PATH).parent
    session_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(SESSION_PATH, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    logger.info("Connected to Telegram. Fetching messages from chat %s...", CHAT_ID)

    conn = init_db(db_path)
    inserted = 0
    total_scanned = 0
    topic_matched = 0
    noise_skipped = 0
    source_counts: dict[str, int] = {}

    try:
        async for message in client.iter_messages(CHAT_ID, limit=limit):
            total_scanned += 1

            if since is not None and message.date.astimezone(timezone.utc) <= since:
                logger.info("Reached since cutoff at %s; stopping scan.", since.isoformat())
                break

            inserted_now, source, status = process_telegram_message(conn, message)
            if status == "no_text":
                continue
            if status == "noise":
                noise_skipped += 1
                continue
            if status == "not_topic":
                continue

            topic_matched += 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1
            if inserted_now:
                inserted += 1

            if total_scanned % 500 == 0:
                conn.commit()
                logger.info(
                    "Scanned %s messages, matched %s, inserted %s new...",
                    total_scanned, topic_matched, inserted,
                )

        conn.commit()
    finally:
        conn.close()
        await client.disconnect()

    logger.info(
        "Done. Scanned=%s, noise_skipped=%s, topic_matched=%s, new_inserted=%s, db=%s",
        total_scanned, noise_skipped, topic_matched, inserted, db_path,
    )
    logger.info("Source breakdown: %s", source_counts)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Iran/Israel conflict headlines from Telegram chat.")
    parser.add_argument("--limit", type=int, help="Max messages to scan (default: all)")
    parser.add_argument(
        "--since",
        help=(
            "Only fetch messages after this date/time. If omitted, uses "
            "MAX(timestamp)-1h from the target DB when available."
        ),
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"Output SQLite path (default: {DB_PATH})")
    parser.add_argument(
        "--refresh-hormuz",
        action="store_true",
        help="Refresh hormuz_flows from Z:\\Aastha\\hormuz_tracker.xlsx after scraping.",
    )
    parser.add_argument(
        "--hormuz-excel",
        type=Path,
        default=HORMUZ_TRACKER_PATH,
        help=f"Hormuz tracker workbook path (default: {HORMUZ_TRACKER_PATH})",
    )
    args = parser.parse_args()

    since_dt = None
    if args.since:
        raw_since = args.since.strip()
        if len(raw_since) <= 10:
            since_dt = datetime.strptime(raw_since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            since_dt = datetime.fromisoformat(raw_since).astimezone(timezone.utc)
    else:
        since_dt = auto_since_from_db(args.db)
        if since_dt is not None:
            logger.info("Auto-since from DB: %s", since_dt.isoformat())

    count = asyncio.run(scrape_chat(limit=args.limit, since=since_dt, db_path=args.db))
    print(f"Inserted {count} new headlines into {args.db}")

    if args.refresh_hormuz:
        flow_count = refresh_hormuz_flows(args.db, args.hormuz_excel)
        print(f"Refreshed {flow_count} Hormuz flow rows into {args.db}")


if __name__ == "__main__":
    main()
