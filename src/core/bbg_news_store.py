"""
SQLite store for Bloomberg email news moderation state.

The schema mirrors the X macro `tweet_labels` flow while using a short integer
primary key for Telegram callback data and the Graph message id for dedupe.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = Path(
    os.getenv(
        "BBG_NEWS_DB_PATH",
        str(Path.home() / ".market_data" / "bbg_news.db"),
    )
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Create the Bloomberg news moderation table if needed."""

    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                graph_id TEXT NOT NULL UNIQUE,
                sender TEXT,
                subject TEXT,
                body TEXT,
                web_link TEXT,
                received_at TEXT,
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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_labels_label_forwarded
            ON news_labels(label, forwarded_message_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_labels_received_at
            ON news_labels(received_at)
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_news_label(
    graph_id: str,
    sender: Optional[str],
    subject: Optional[str],
    body: Optional[str],
    web_link: Optional[str],
    received_at: Optional[str],
    telegram_message_id: Optional[int],
    db_path: Optional[Path] = None,
) -> int:
    """
    Insert a pending Bloomberg news row if new, returning its integer id.

    Existing rows are preserved so duplicate Graph polls do not create duplicate
    review-chat messages or reset moderation state.
    """

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO news_labels (
                graph_id, sender, subject, body, web_link, received_at,
                telegram_message_id, label, score, labeled_by, labeled_at,
                forwarded_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?)
            """,
            (
                graph_id,
                sender,
                subject,
                body,
                web_link,
                received_at,
                telegram_message_id,
                _utc_now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT id FROM news_labels WHERE graph_id = ?",
            (graph_id,),
        ).fetchone()
        conn.commit()
        if not row:
            raise RuntimeError(f"Failed to insert or load news label for graph_id={graph_id}")
        return int(row["id"])
    finally:
        conn.close()


def set_review_message_id(
    news_id: int,
    telegram_message_id: Optional[int],
    db_path: Optional[Path] = None,
) -> None:
    """Persist the review-chat message id after sending."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE news_labels SET telegram_message_id = ? WHERE id = ?",
            (telegram_message_id, int(news_id)),
        )
        conn.commit()
    finally:
        conn.close()


def get_news_label(news_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """Return one Bloomberg news moderation row by id."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM news_labels WHERE id = ?",
            (int(news_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_news_label_by_graph_id(
    graph_id: str,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """Return one Bloomberg news moderation row by Graph message id."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM news_labels WHERE graph_id = ?",
            (graph_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_approved_but_unforwarded(
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> List[dict]:
    """Return approved Bloomberg news rows missing a public forward id."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM news_labels
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


def update_news_label(
    news_id: int,
    label: str,
    labeled_by: Optional[int],
    score: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Atomically label a pending Bloomberg news row."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE news_labels
            SET label = ?, score = ?, labeled_by = ?, labeled_at = ?
            WHERE id = ? AND label IS NULL
            """,
            (label, score, labeled_by, _utc_now_iso(), int(news_id)),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_forwarded(
    news_id: int,
    forwarded_message_id: Optional[int],
    db_path: Optional[Path] = None,
) -> None:
    """Store the public-chat forward message id."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            UPDATE news_labels
            SET forwarded_message_id = ?
            WHERE id = ?
            """,
            (forwarded_message_id, int(news_id)),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_received_at(db_path: Optional[Path] = None) -> Optional[str]:
    """Return the latest received timestamp seen in the store."""

    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(received_at) AS max_received FROM news_labels"
        ).fetchone()
        value = row["max_received"] if row else None
        return str(value) if value else None
    finally:
        conn.close()
