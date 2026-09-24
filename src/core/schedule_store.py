"""
Schedule store for upcoming economic events.

Provides SQLite-based storage for scheduled event runs with status tracking.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "workspace" / "scheduled_events.db"

# Status constants
STATUS_SCHEDULED = "scheduled"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get database connection, creating tables if needed."""
    db_path = db_path or DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    # Create tables if they don't exist
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            event_name TEXT NOT NULL,
            country TEXT,
            importance TEXT,
            event_timestamp TEXT NOT NULL,
            symbols TEXT,
            telegram_chat_id TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            csv_source_row INTEGER,
            created_at TEXT NOT NULL,
            last_run_at TEXT,
            result_summary TEXT,
            error_message TEXT,
            UNIQUE(event_id, event_timestamp)
        )
    """)
    
    # Create index for efficient due-event queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_scheduled_events_status_timestamp 
        ON scheduled_events(status, event_timestamp)
    """)
    
    # Create run history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS event_run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_event_id INTEGER NOT NULL,
            run_timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            percentile_surprise REAL,
            z_score REAL,
            market_return_bps REAL,
            telegram_sent INTEGER DEFAULT 0,
            telegram_message_id TEXT,
            plot_paths TEXT,
            full_result TEXT,
            error_message TEXT,
            duration_ms REAL,
            FOREIGN KEY (scheduled_event_id) REFERENCES scheduled_events(id)
        )
    """)
    
    conn.commit()
    return conn


def add_scheduled_events(
    events: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Add events to the schedule store.
    
    Args:
        events: List of event dicts with keys:
            - event_id (optional): Event ID/ticker
            - event_name (required): Event name
            - country (optional): Country code
            - importance (optional): low/medium/high
            - event_timestamp (required): ISO format datetime
            - symbols (optional): Comma-separated symbols to analyze
            - telegram_chat_id (optional): Override chat ID
            - csv_source_row (optional): Row number from source CSV
            
    Returns:
        Dict with success status and counts
    """
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    inserted = 0
    updated = 0
    skipped = 0
    errors = []
    
    now = datetime.now(timezone.utc).isoformat()
    
    for event in events:
        try:
            event_name = event.get("event_name")
            event_timestamp = event.get("event_timestamp")
            
            if not event_name or not event_timestamp:
                skipped += 1
                errors.append(f"Missing required fields: {event}")
                continue
            
            # Check if already exists
            cursor.execute("""
                SELECT id, status FROM scheduled_events 
                WHERE event_id = ? AND event_timestamp = ?
            """, (event.get("event_id"), event_timestamp))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update if still scheduled
                if existing["status"] == STATUS_SCHEDULED:
                    cursor.execute("""
                        UPDATE scheduled_events SET
                            event_name = ?,
                            country = ?,
                            importance = ?,
                            symbols = ?,
                            telegram_chat_id = ?,
                            csv_source_row = ?
                        WHERE id = ?
                    """, (
                        event_name,
                        event.get("country"),
                        event.get("importance"),
                        event.get("symbols"),
                        event.get("telegram_chat_id"),
                        event.get("csv_source_row"),
                        existing["id"],
                    ))
                    updated += 1
                else:
                    skipped += 1
            else:
                # Insert new
                cursor.execute("""
                    INSERT INTO scheduled_events (
                        event_id, event_name, country, importance,
                        event_timestamp, symbols, telegram_chat_id,
                        status, csv_source_row, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.get("event_id"),
                    event_name,
                    event.get("country"),
                    event.get("importance"),
                    event_timestamp,
                    event.get("symbols"),
                    event.get("telegram_chat_id"),
                    STATUS_SCHEDULED,
                    event.get("csv_source_row"),
                    now,
                ))
                inserted += 1
                
        except sqlite3.IntegrityError as e:
            skipped += 1
            errors.append(f"Duplicate event: {event.get('event_name')} at {event.get('event_timestamp')}")
        except Exception as e:
            skipped += 1
            errors.append(f"Error adding event: {e}")
    
    conn.commit()
    conn.close()
    
    logger.info(f"Added {inserted} events, updated {updated}, skipped {skipped}")
    
    return {
        "success": True,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors if errors else None,
    }


def get_due_events(
    minutes_ahead: int = 2,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Get events that are due to run (within minutes_ahead of now).
    
    Args:
        minutes_ahead: Look for events up to this many minutes in the future
        db_path: Database path
        
    Returns:
        List of event dicts
    """
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=minutes_ahead)
    
    cursor.execute("""
        SELECT * FROM scheduled_events
        WHERE status = ?
        AND event_timestamp <= ?
        AND event_timestamp >= ?
        ORDER BY event_timestamp ASC
    """, (STATUS_SCHEDULED, cutoff.isoformat(), now.isoformat()))
    
    rows = cursor.fetchall()
    events = [dict(row) for row in rows]
    
    conn.close()
    return events


def get_upcoming_events(
    hours_ahead: int = 24,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Get upcoming scheduled events.
    
    Args:
        hours_ahead: Look this many hours ahead
        status: Filter by status (default: all)
        db_path: Database path
        
    Returns:
        List of event dicts
    """
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)
    
    if status:
        cursor.execute("""
            SELECT * FROM scheduled_events
            WHERE status = ?
            AND event_timestamp BETWEEN ? AND ?
            ORDER BY event_timestamp ASC
        """, (status, now.isoformat(), cutoff.isoformat()))
    else:
        cursor.execute("""
            SELECT * FROM scheduled_events
            WHERE event_timestamp BETWEEN ? AND ?
            ORDER BY event_timestamp ASC
        """, (now.isoformat(), cutoff.isoformat()))
    
    rows = cursor.fetchall()
    events = [dict(row) for row in rows]
    
    conn.close()
    return events


def update_event_status(
    event_id: int,
    status: str,
    result_summary: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """
    Update the status of a scheduled event.
    
    Args:
        event_id: Database ID of the event
        status: New status
        result_summary: Summary of results (for completed)
        error_message: Error message (for failed)
        db_path: Database path
        
    Returns:
        True if updated successfully
    """
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    now = datetime.now(timezone.utc).isoformat()
    
    cursor.execute("""
        UPDATE scheduled_events SET
            status = ?,
            last_run_at = ?,
            result_summary = ?,
            error_message = ?
        WHERE id = ?
    """, (status, now, result_summary, error_message, event_id))
    
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    return updated


def record_run_history(
    scheduled_event_id: int,
    status: str,
    percentile_surprise: Optional[float] = None,
    z_score: Optional[float] = None,
    market_return_bps: Optional[float] = None,
    telegram_sent: bool = False,
    telegram_message_id: Optional[str] = None,
    plot_paths: Optional[List[str]] = None,
    full_result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    duration_ms: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> int:
    """
    Record a run in the history table.
    
    Returns:
        The run history ID
    """
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    now = datetime.now(timezone.utc).isoformat()
    
    cursor.execute("""
        INSERT INTO event_run_history (
            scheduled_event_id, run_timestamp, status,
            percentile_surprise, z_score, market_return_bps,
            telegram_sent, telegram_message_id, plot_paths,
            full_result, error_message, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scheduled_event_id,
        now,
        status,
        percentile_surprise,
        z_score,
        market_return_bps,
        1 if telegram_sent else 0,
        telegram_message_id,
        json.dumps(plot_paths) if plot_paths else None,
        json.dumps(full_result) if full_result else None,
        error_message,
        duration_ms,
    ))
    
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return run_id


def get_schedule_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get statistics about the schedule store."""
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Count by status
    cursor.execute("""
        SELECT status, COUNT(*) as count
        FROM scheduled_events
        GROUP BY status
    """)
    status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}
    
    # Upcoming events count
    now = datetime.now(timezone.utc)
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM scheduled_events
        WHERE status = ? AND event_timestamp > ?
    """, (STATUS_SCHEDULED, now.isoformat()))
    upcoming = cursor.fetchone()["count"]
    
    # Recent runs
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM event_run_history
        WHERE run_timestamp > ?
    """, ((now - timedelta(hours=24)).isoformat(),))
    recent_runs = cursor.fetchone()["count"]
    
    conn.close()
    
    return {
        "status_counts": status_counts,
        "upcoming_scheduled": upcoming,
        "runs_last_24h": recent_runs,
        "total_events": sum(status_counts.values()),
    }


def clear_old_events(
    days_old: int = 30,
    db_path: Optional[Path] = None,
) -> int:
    """
    Clear old completed/failed events from the database.
    
    Args:
        days_old: Remove events older than this many days
        db_path: Database path
        
    Returns:
        Number of events deleted
    """
    conn = _get_db_connection(db_path)
    cursor = conn.cursor()
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    
    cursor.execute("""
        DELETE FROM scheduled_events
        WHERE status IN (?, ?, ?)
        AND event_timestamp < ?
    """, (STATUS_COMPLETED, STATUS_FAILED, STATUS_SKIPPED, cutoff))
    
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    logger.info(f"Cleared {deleted} old events")
    return deleted


def parse_csv_events(
    csv_path: str,
    default_symbols: str = "TY",
) -> List[Dict[str, Any]]:
    """
    Parse events from a CSV file.
    
    Expected CSV columns:
        - event_id (optional): Event ID/ticker
        - event_name (required): Event name
        - country (optional): Country code (default: US)
        - importance (optional): low/medium/high
        - symbols (optional): Comma-separated symbols
        - telegram_chat_id (optional): Override chat ID
        
    Returns:
        List of event dicts ready for add_scheduled_events
    """
    import csv
    
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    events = []
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 to account for header
            event = {
                "event_id": row.get("event_id", "").strip() or None,
                "event_name": row.get("event_name", "").strip(),
                "country": row.get("country", "US").strip() or "US",
                "importance": row.get("importance", "").strip() or None,
                "symbols": row.get("symbols", "").strip() or default_symbols,
                "telegram_chat_id": row.get("telegram_chat_id", "").strip() or None,
                "csv_source_row": row_num,
            }
            
            if event["event_name"]:
                events.append(event)
    
    logger.info(f"Parsed {len(events)} events from {csv_path}")
    return events

