"""
SQLite store for Polymarket Telegram tracking state.

Stores:
- Chat subscriptions to markets
- Pending market selections for inline multi-select flow
- Alert labels (approve/reject + optional forward metadata)
- Alert evaluation state used by background polling
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex
from typing import Any, Dict, List, Optional, Set


DEFAULT_DB_PATH = Path(
    os.getenv(
        "POLYMARKET_STREAM_DB_PATH",
        str(Path.home() / ".market_data" / "polymarket_stream.db"),
    )
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db = db_path or DEFAULT_DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                query_group_id TEXT,
                condition_id TEXT NOT NULL,
                market_slug TEXT,
                title TEXT,
                yes_token_id TEXT NOT NULL,
                no_token_id TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_pending_selections (
                selection_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_query TEXT NOT NULL,
                markets_json TEXT NOT NULL,
                selected_indices_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_alert_labels (
                alert_id TEXT PRIMARY KEY,
                subscription_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                telegram_message_id INTEGER,
                label TEXT,
                labeled_by INTEGER,
                labeled_at TEXT,
                forwarded_message_id INTEGER,
                payload_text TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_alert_state (
                subscription_id INTEGER PRIMARY KEY,
                last_mid REAL,
                last_alert_mid REAL,
                last_alert_ts TEXT,
                streak_dir INTEGER,
                streak_len INTEGER NOT NULL DEFAULT 0,
                streak_start_mid REAL,
                last_heartbeat_ts TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_query_messages (
                query_group_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                query_text TEXT,
                telegram_message_id INTEGER,
                last_reason TEXT,
                last_notified_ts TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # Lightweight migration for older DBs created before query_group_id.
        sub_cols = [r["name"] for r in cur.execute("PRAGMA table_info(pm_subscriptions)").fetchall()]
        if "query_group_id" not in sub_cols:
            cur.execute("ALTER TABLE pm_subscriptions ADD COLUMN query_group_id TEXT")
        alert_cols = [r["name"] for r in cur.execute("PRAGMA table_info(pm_alert_state)").fetchall()]
        if "last_alert_ts" not in alert_cols:
            cur.execute("ALTER TABLE pm_alert_state ADD COLUMN last_alert_ts TEXT")

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pm_subs_chat_active ON pm_subscriptions (chat_id, active)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pm_subs_token_active ON pm_subscriptions (yes_token_id, active)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pm_pending_chat_user ON pm_pending_selections (chat_id, user_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pm_alert_labels_sub ON pm_alert_labels (subscription_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pm_subs_group_active ON pm_subscriptions (query_group_id, active)"
        )
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class Subscription:
    id: int
    chat_id: int
    query_group_id: Optional[str]
    condition_id: str
    market_slug: Optional[str]
    title: Optional[str]
    yes_token_id: str
    no_token_id: Optional[str]
    active: bool


def upsert_subscription(
    *,
    chat_id: int,
    query_group_id: Optional[str],
    condition_id: str,
    market_slug: Optional[str],
    title: Optional[str],
    yes_token_id: str,
    no_token_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    init_db(db_path)
    conn = _get_conn(db_path)
    now = _utc_now_iso()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id
            FROM pm_subscriptions
            WHERE chat_id = ? AND condition_id = ?
            """,
            (int(chat_id), condition_id),
        )
        row = cur.fetchone()
        if row:
            sub_id = int(row["id"])
            cur.execute(
                """
                UPDATE pm_subscriptions
                SET query_group_id = ?, market_slug = ?, title = ?, yes_token_id = ?, no_token_id = ?, active = 1, updated_at = ?
                WHERE id = ?
                """,
                (query_group_id, market_slug, title, yes_token_id, no_token_id, now, sub_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO pm_subscriptions
                (chat_id, query_group_id, condition_id, market_slug, title, yes_token_id, no_token_id, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (int(chat_id), query_group_id, condition_id, market_slug, title, yes_token_id, no_token_id, now, now),
            )
            sub_id = int(cur.lastrowid)
        conn.commit()
        return sub_id
    finally:
        conn.close()


def list_subscriptions(chat_id: int, *, active_only: bool = True, db_path: Optional[Path] = None) -> List[Subscription]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        if active_only:
            rows = cur.execute(
                """
                SELECT *
                FROM pm_subscriptions
                WHERE chat_id = ? AND active = 1
                ORDER BY created_at ASC
                """,
                (int(chat_id),),
            ).fetchall()
        else:
            rows = cur.execute(
                """
                SELECT *
                FROM pm_subscriptions
                WHERE chat_id = ?
                ORDER BY created_at ASC
                """,
                (int(chat_id),),
            ).fetchall()
        return [
            Subscription(
                id=int(r["id"]),
                chat_id=int(r["chat_id"]),
                query_group_id=r["query_group_id"],
                condition_id=str(r["condition_id"]),
                market_slug=r["market_slug"],
                title=r["title"],
                yes_token_id=str(r["yes_token_id"]),
                no_token_id=r["no_token_id"],
                active=bool(int(r["active"])),
            )
            for r in rows
        ]
    finally:
        conn.close()


def list_all_active_subscriptions(db_path: Optional[Path] = None) -> List[Subscription]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM pm_subscriptions
            WHERE active = 1
            ORDER BY id ASC
            """
        ).fetchall()
        return [
            Subscription(
                id=int(r["id"]),
                chat_id=int(r["chat_id"]),
                query_group_id=r["query_group_id"],
                condition_id=str(r["condition_id"]),
                market_slug=r["market_slug"],
                title=r["title"],
                yes_token_id=str(r["yes_token_id"]),
                no_token_id=r["no_token_id"],
                active=True,
            )
            for r in rows
        ]
    finally:
        conn.close()


def deactivate_subscription(chat_id: int, subscription_id: int, db_path: Optional[Path] = None) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pm_subscriptions
            SET active = 0, updated_at = ?
            WHERE id = ? AND chat_id = ?
            """,
            (_utc_now_iso(), int(subscription_id), int(chat_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count_active_subscriptions_for_condition(
    condition_id: str,
    *,
    exclude_subscription_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> int:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        if exclude_subscription_id is None:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM pm_subscriptions
                WHERE condition_id = ? AND active = 1
                """,
                (condition_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM pm_subscriptions
                WHERE condition_id = ? AND active = 1 AND id != ?
                """,
                (condition_id, int(exclude_subscription_id)),
            ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def create_pending_selection(
    *,
    chat_id: int,
    user_id: int,
    user_query: str,
    markets: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> str:
    init_db(db_path)
    selection_id = token_hex(8)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO pm_pending_selections
            (selection_id, chat_id, user_id, user_query, markets_json, selected_indices_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                selection_id,
                int(chat_id),
                int(user_id),
                user_query,
                json.dumps(markets),
                json.dumps([]),
                _utc_now_iso(),
            ),
        )
        conn.commit()
        return selection_id
    finally:
        conn.close()


def get_pending_selection(selection_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT *
            FROM pm_pending_selections
            WHERE selection_id = ?
            """,
            (selection_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "selection_id": str(row["selection_id"]),
            "chat_id": int(row["chat_id"]),
            "user_id": int(row["user_id"]),
            "user_query": str(row["user_query"]),
            "markets": json.loads(row["markets_json"] or "[]"),
            "selected_indices": sorted(set(json.loads(row["selected_indices_json"] or "[]"))),
            "created_at": str(row["created_at"]),
        }
    finally:
        conn.close()


def set_pending_selected_indices(
    selection_id: str,
    indices: Set[int],
    db_path: Optional[Path] = None,
) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pm_pending_selections
            SET selected_indices_json = ?
            WHERE selection_id = ?
            """,
            (json.dumps(sorted(indices)), selection_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def toggle_pending_selected_index(
    selection_id: str,
    index: int,
    db_path: Optional[Path] = None,
) -> Optional[Set[int]]:
    selection = get_pending_selection(selection_id, db_path=db_path)
    if not selection:
        return None
    selected = {int(i) for i in selection.get("selected_indices", [])}
    if index in selected:
        selected.remove(index)
    else:
        selected.add(index)
    ok = set_pending_selected_indices(selection_id, selected, db_path=db_path)
    return selected if ok else None


def delete_pending_selection(selection_id: str, db_path: Optional[Path] = None) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM pm_pending_selections WHERE selection_id = ?", (selection_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def create_alert_label(
    *,
    subscription_id: int,
    chat_id: int,
    telegram_message_id: Optional[int],
    payload_text: Optional[str],
    db_path: Optional[Path] = None,
) -> str:
    init_db(db_path)
    alert_id = token_hex(8)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO pm_alert_labels
            (alert_id, subscription_id, chat_id, telegram_message_id, label, labeled_by, labeled_at, forwarded_message_id, payload_text, created_at)
            VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (alert_id, int(subscription_id), int(chat_id), telegram_message_id, payload_text, _utc_now_iso()),
        )
        conn.commit()
        return alert_id
    finally:
        conn.close()


def set_alert_message_id(alert_id: str, telegram_message_id: int, db_path: Optional[Path] = None) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pm_alert_labels
            SET telegram_message_id = ?
            WHERE alert_id = ?
            """,
            (int(telegram_message_id), alert_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_alert_label(alert_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM pm_alert_labels WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "alert_id": str(row["alert_id"]),
            "subscription_id": int(row["subscription_id"]),
            "chat_id": int(row["chat_id"]),
            "telegram_message_id": row["telegram_message_id"],
            "label": row["label"],
            "labeled_by": row["labeled_by"],
            "labeled_at": row["labeled_at"],
            "forwarded_message_id": row["forwarded_message_id"],
            "payload_text": row["payload_text"],
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def set_alert_label(
    *,
    alert_id: str,
    label: str,
    labeled_by: Optional[int],
    forwarded_message_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pm_alert_labels
            SET label = ?, labeled_by = ?, labeled_at = ?, forwarded_message_id = COALESCE(?, forwarded_message_id)
            WHERE alert_id = ? AND (label IS NULL OR label = '')
            """,
            (label, labeled_by, _utc_now_iso(), forwarded_message_id, alert_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_alert_forwarded(
    *,
    alert_id: str,
    forwarded_message_id: int,
    db_path: Optional[Path] = None,
) -> bool:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pm_alert_labels
            SET forwarded_message_id = ?
            WHERE alert_id = ?
            """,
            (int(forwarded_message_id), alert_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_alert_state(subscription_id: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM pm_alert_state WHERE subscription_id = ?",
            (int(subscription_id),),
        ).fetchone()
        if not row:
            now = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO pm_alert_state
                (subscription_id, last_mid, last_alert_mid, last_alert_ts, streak_dir, streak_len, streak_start_mid, last_heartbeat_ts, updated_at)
                VALUES (?, NULL, NULL, NULL, NULL, 0, NULL, NULL, ?)
                """,
                (int(subscription_id), now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM pm_alert_state WHERE subscription_id = ?",
                (int(subscription_id),),
            ).fetchone()
        return {
            "subscription_id": int(row["subscription_id"]),
            "last_mid": row["last_mid"],
            "last_alert_mid": row["last_alert_mid"],
            "last_alert_ts": row["last_alert_ts"],
            "streak_dir": row["streak_dir"],
            "streak_len": int(row["streak_len"] or 0),
            "streak_start_mid": row["streak_start_mid"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def update_alert_state(
    *,
    subscription_id: int,
    last_mid: Optional[float],
    last_alert_mid: Optional[float],
    last_alert_ts: Optional[str],
    streak_dir: Optional[int],
    streak_len: int,
    streak_start_mid: Optional[float],
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO pm_alert_state
            (subscription_id, last_mid, last_alert_mid, last_alert_ts, streak_dir, streak_len, streak_start_mid, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subscription_id) DO UPDATE SET
                last_mid = excluded.last_mid,
                last_alert_mid = excluded.last_alert_mid,
                last_alert_ts = excluded.last_alert_ts,
                streak_dir = excluded.streak_dir,
                streak_len = excluded.streak_len,
                streak_start_mid = excluded.streak_start_mid,
                updated_at = excluded.updated_at
            """,
            (
                int(subscription_id),
                last_mid,
                last_alert_mid,
                last_alert_ts,
                streak_dir,
                int(streak_len),
                streak_start_mid,
                _utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_query_message_state(
    *,
    query_group_id: str,
    chat_id: int,
    query_text: Optional[str],
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        now = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO pm_query_messages
            (query_group_id, chat_id, query_text, telegram_message_id, last_reason, last_notified_ts, created_at, updated_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?)
            ON CONFLICT(query_group_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                query_text = COALESCE(excluded.query_text, pm_query_messages.query_text),
                updated_at = excluded.updated_at
            """,
            (query_group_id, int(chat_id), query_text, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_query_message_state(query_group_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM pm_query_messages WHERE query_group_id = ?",
            (query_group_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "query_group_id": str(row["query_group_id"]),
            "chat_id": int(row["chat_id"]),
            "query_text": row["query_text"],
            "telegram_message_id": row["telegram_message_id"],
            "last_reason": row["last_reason"],
            "last_notified_ts": row["last_notified_ts"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def set_query_message_delivery(
    *,
    query_group_id: str,
    telegram_message_id: int,
    reason: str,
    notified_ts: str,
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            UPDATE pm_query_messages
            SET telegram_message_id = ?, last_reason = ?, last_notified_ts = ?, updated_at = ?
            WHERE query_group_id = ?
            """,
            (int(telegram_message_id), reason, notified_ts, _utc_now_iso(), query_group_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_query_message_meta(
    *,
    query_group_id: str,
    reason: str,
    notified_ts: str,
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            UPDATE pm_query_messages
            SET last_reason = ?, last_notified_ts = ?, updated_at = ?
            WHERE query_group_id = ?
            """,
            (reason, notified_ts, _utc_now_iso(), query_group_id),
        )
        conn.commit()
    finally:
        conn.close()
