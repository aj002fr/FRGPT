"""SQLite persistence for Telegram message↔run mapping and user feedback.

This module stores:
- A mapping from bot-sent Telegram messages to `run_id` (reply-to linking)
- Self-contained feedback snapshots linked to the mapped `run_id`

All storage is in the central metadata DB (`data/metadata.db`) via
`src.core.run_store.init_db()`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.run_store import init_db, get_run_dir, PROJECT_ROOT

logger = logging.getLogger(__name__)


ALLOWED_FEEDBACK_LABELS: tuple[str, ...] = (
    "correct",
    "incorrect",
    "incomplete",
    "unclear",
    "other",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_telegram_run_message(
    *,
    run_id: str,
    chat_id: int,
    message_id: int,
    message_kind: str,
    db_path: Optional[Path] = None,
) -> None:
    """Persist a mapping from a Telegram bot message to `run_id`.

    This enables users to reply to any bot answer chunk with `/feedback ...`
    and have it linked deterministically to the correct orchestration run.
    """
    db_file = init_db(db_path=db_path)
    created_at = _utc_now_iso()

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO telegram_run_messages (
                run_id,
                chat_id,
                message_id,
                message_kind,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, int(chat_id), int(message_id), str(message_kind), created_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_run_id_for_telegram_message(
    *,
    chat_id: int,
    message_id: int,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Lookup the `run_id` for a replied-to Telegram message."""
    db_file = init_db(db_path=db_path)

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT run_id
            FROM telegram_run_messages
            WHERE chat_id = ? AND message_id = ?
            """,
            (int(chat_id), int(message_id)),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return str(row["run_id"])
    finally:
        conn.close()


def read_run_query_text(run_id: str) -> Optional[str]:
    """Read the original user query text for a given run_id (best-effort)."""
    try:
        query_path = get_run_dir(run_id) / "query.json"
        if not query_path.exists():
            return None
        with query_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        user_query = payload.get("user_query")
        return str(user_query) if user_query is not None else None
    except Exception:
        logger.exception("Failed to read query.json for run_id=%s", run_id)
        return None


def read_orchestrator_output_path(run_id: str) -> Optional[str]:
    """Read OrchestratorAgent's output_path from its run log (best-effort)."""
    try:
        log_path = (
            PROJECT_ROOT
            / "workspace"
            / "agents"
            / "orchestrator-agent"
            / "logs"
            / f"{run_id}.json"
        )
        if not log_path.exists():
            return None
        with log_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        output_path = payload.get("output_path")
        return str(output_path) if output_path else None
    except Exception:
        logger.exception("Failed to read orchestrator log for run_id=%s", run_id)
        return None


def read_run_final_answer(run_id: str, db_path: Optional[Path] = None) -> Optional[str]:
    """Read the final_answer from the runner output for a given run_id.
    
    Uses the agent_outputs table to locate the runner output file,
    then extracts the final_answer field from the JSON.
    
    Args:
        run_id: The orchestration run identifier.
        db_path: Optional override for database path.
        
    Returns:
        The final_answer string if found, None otherwise.
    """
    db_file = init_db(db_path=db_path)
    
    # Query agent_outputs for the runner output path
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT data_pointer
            FROM agent_outputs
            WHERE run_id = ? AND agent = 'runner'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            logger.debug("No runner output found in agent_outputs for run_id=%s", run_id)
            return None
        data_pointer = row["data_pointer"]
    finally:
        conn.close()
    
    # Resolve the path (may be relative to project root)
    output_path = Path(data_pointer)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    
    if not output_path.exists():
        logger.warning("Runner output file not found: %s", output_path)
        return None
    
    try:
        with output_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        
        # Handle both wrapped and unwrapped formats
        # Format 1: {"data": [{"final_answer": "..."}]}
        # Format 2: {"final_answer": "..."}
        if "data" in payload and isinstance(payload["data"], list) and payload["data"]:
            final_answer = payload["data"][0].get("final_answer")
        else:
            final_answer = payload.get("final_answer")
        
        return str(final_answer) if final_answer else None
    except Exception:
        logger.exception("Failed to read runner output for run_id=%s", run_id)
        return None


def insert_telegram_feedback(
    *,
    run_id: str,
    chat_id: int,
    from_user_id: int,
    from_username: Optional[str],
    reply_to_message_id: int,
    label: str,
    comment: Optional[str],
    query_text: str,
    answer_text: str,
    output_path: Optional[str],
    db_path: Optional[Path] = None,
) -> int:
    """Insert a feedback record and return its row id."""
    db_file = init_db(db_path=db_path)
    created_at = _utc_now_iso()

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO telegram_feedback (
                run_id,
                chat_id,
                from_user_id,
                from_username,
                reply_to_message_id,
                label,
                comment,
                query_text,
                answer_text,
                output_path,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(chat_id),
                int(from_user_id),
                from_username,
                int(reply_to_message_id),
                str(label),
                comment,
                query_text,
                answer_text,
                output_path,
                created_at,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()



