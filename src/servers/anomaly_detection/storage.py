"""PostgreSQL storage for market anomaly notes."""

import logging
from datetime import date, datetime
from typing import Dict, Any, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from src.mcp.discovery import register_tool

logger = logging.getLogger(__name__)


def _get_pg_connection():
    """Get PostgreSQL connection using config settings."""
    import sys
    from pathlib import Path
    
    # Add project root to path
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))
    
    from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
    
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )


@register_tool(
    name="store_anomaly_note",
    description="Store a market anomaly note in the database.",
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Date of the anomaly (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Market symbol"},
            "anomaly_type": {"type": "string", "description": "Type: large_range, large_move, intraday_spike"},
            "z_score": {"type": "number", "description": "Z-score of the move"},
            "value_bps": {"type": "number", "description": "Actual value in basis points"},
            "threshold_bps": {"type": "number", "description": "Threshold that was exceeded"},
            "event_name": {"type": "string", "description": "Matched economic event if any"},
            "news_summary": {"type": "string", "description": "News summary from web search"},
            "note": {"type": "string", "description": "Human-readable note"},
            "source": {"type": "string", "description": "Source: economic_event, perplexity, unattributed"},
        },
        "required": ["date", "anomaly_type", "z_score", "value_bps", "note", "source"],
    },
    agent="anomaly_notes_agent",
)
def store_anomaly_note(
    date: str,
    anomaly_type: str,
    z_score: float,
    value_bps: float,
    note: str,
    source: str,
    symbol: str = "TY",
    threshold_bps: Optional[float] = None,
    event_name: Optional[str] = None,
    news_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Store a market anomaly note in the database.
    
    Uses UPSERT to update existing notes for the same date/symbol/type.
    
    Returns:
        Dictionary with success status and note ID
    """
    conn = _get_pg_connection()
    
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO market_anomaly_notes 
                    (date, symbol, anomaly_type, z_score, value_bps, threshold_bps, 
                     event_name, news_summary, note, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (date, symbol, anomaly_type) 
                DO UPDATE SET
                    z_score = EXCLUDED.z_score,
                    value_bps = EXCLUDED.value_bps,
                    threshold_bps = EXCLUDED.threshold_bps,
                    event_name = EXCLUDED.event_name,
                    news_summary = EXCLUDED.news_summary,
                    note = EXCLUDED.note,
                    source = EXCLUDED.source,
                    created_at = NOW()
                RETURNING id
                """,
                (date, symbol, anomaly_type, z_score, value_bps, threshold_bps,
                 event_name, news_summary, note, source),
            )
            note_id = cur.fetchone()[0]
            conn.commit()
            
            logger.info(f"Stored anomaly note {note_id} for {date} ({anomaly_type})")
            
            return {
                "success": True,
                "id": note_id,
                "date": date,
                "anomaly_type": anomaly_type,
            }
            
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to store anomaly note: {e}")
        return {
            "success": False,
            "error": str(e),
        }
    finally:
        conn.close()


@register_tool(
    name="query_anomaly_notes",
    description="Query stored market anomaly notes.",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Filter by symbol"},
            "anomaly_type": {"type": "string", "description": "Filter by type"},
            "source": {"type": "string", "description": "Filter by source"},
            "min_z_score": {"type": "number", "description": "Minimum z-score"},
            "limit": {"type": "integer", "description": "Maximum results"},
        },
    },
    agent="anomaly_notes_agent",
)
def query_anomaly_notes(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbol: Optional[str] = None,
    anomaly_type: Optional[str] = None,
    source: Optional[str] = None,
    min_z_score: Optional[float] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    Query stored market anomaly notes.
    
    Returns:
        Dictionary with notes array and count
    """
    conn = _get_pg_connection()
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Build query dynamically
            conditions = []
            params = []
            
            if start_date:
                conditions.append("date >= %s")
                params.append(start_date)
            if end_date:
                conditions.append("date <= %s")
                params.append(end_date)
            if symbol:
                conditions.append("symbol = %s")
                params.append(symbol)
            if anomaly_type:
                conditions.append("anomaly_type = %s")
                params.append(anomaly_type)
            if source:
                conditions.append("source = %s")
                params.append(source)
            if min_z_score is not None:
                conditions.append("z_score >= %s")
                params.append(min_z_score)
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)
            
            query = f"""
                SELECT id, date, symbol, anomaly_type, z_score, value_bps, 
                       threshold_bps, event_name, news_summary, note, source, created_at
                FROM market_anomaly_notes
                WHERE {where_clause}
                ORDER BY date DESC, z_score DESC
                LIMIT %s
            """
            
            cur.execute(query, params)
            rows = cur.fetchall()
            
            # Convert to serializable format
            notes = []
            for row in rows:
                note = dict(row)
                note["date"] = note["date"].isoformat() if isinstance(note["date"], date) else note["date"]
                note["created_at"] = note["created_at"].isoformat() if note["created_at"] else None
                notes.append(note)
            
            logger.info(f"Retrieved {len(notes)} anomaly notes")
            
            return {
                "success": True,
                "notes": notes,
                "count": len(notes),
            }
            
    except Exception as e:
        logger.error(f"Failed to query anomaly notes: {e}")
        return {
            "success": False,
            "notes": [],
            "count": 0,
            "error": str(e),
        }
    finally:
        conn.close()


@register_tool(
    name="delete_anomaly_notes",
    description="Delete market anomaly notes for a date range.",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Filter by symbol"},
        },
        "required": ["start_date", "end_date"],
    },
    agent="anomaly_notes_agent",
)
def delete_anomaly_notes(
    start_date: str,
    end_date: str,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Delete market anomaly notes for a date range.
    
    Returns:
        Dictionary with deleted count
    """
    conn = _get_pg_connection()
    
    try:
        with conn.cursor() as cur:
            if symbol:
                cur.execute(
                    """
                    DELETE FROM market_anomaly_notes
                    WHERE date >= %s AND date <= %s AND symbol = %s
                    """,
                    (start_date, end_date, symbol),
                )
            else:
                cur.execute(
                    """
                    DELETE FROM market_anomaly_notes
                    WHERE date >= %s AND date <= %s
                    """,
                    (start_date, end_date),
                )
            
            deleted_count = cur.rowcount
            conn.commit()
            
            logger.info(f"Deleted {deleted_count} anomaly notes")
            
            return {
                "success": True,
                "deleted_count": deleted_count,
            }
            
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to delete anomaly notes: {e}")
        return {
            "success": False,
            "deleted_count": 0,
            "error": str(e),
        }
    finally:
        conn.close()






