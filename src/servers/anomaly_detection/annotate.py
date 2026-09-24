"""High-level annotation tools combining detection, event matching, and search."""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.mcp.discovery import register_tool
from .detect import detect_anomalies, DEFAULT_Z_THRESHOLD
from .storage import store_anomaly_note
from .perplexity_search import search_market_news

logger = logging.getLogger(__name__)


def _get_events_db_path() -> Path:
    """Get path to economic events database."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace" / "economic_events.db"


def _find_events_on_date(date: str) -> List[Dict[str, Any]]:
    """
    Find economic events on a specific date.
    
    Returns list of events with importance >= medium.
    """
    db_path = _get_events_db_path()
    if not db_path.exists():
        logger.warning(f"Economic events database not found: {db_path}")
        return []
    
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Query events on this date (using date part of event_date)
        cursor.execute(
            """
            SELECT event_name, event_date, importance, actual, forecast, previous, country
            FROM economic_events
            WHERE DATE(event_date) = DATE(?)
              AND importance IN ('medium', 'high')
              AND country = 'US'
            ORDER BY 
                CASE importance WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                event_date
            """,
            (date,),
        )
        
        events = []
        for row in cursor.fetchall():
            events.append({
                "event_name": row["event_name"],
                "event_date": row["event_date"],
                "importance": row["importance"],
                "actual": row["actual"],
                "forecast": row["forecast"],
                "previous": row["previous"],
                "country": row["country"],
            })
        
        conn.close()
        return events
        
    except Exception as e:
        logger.error(f"Failed to query economic events: {e}")
        return []


def _generate_note_text(
    anomaly_type: str,
    value_bps: float,
    z_score: float,
    event: Optional[Dict[str, Any]] = None,
    news_summary: Optional[str] = None,
) -> str:
    """Generate human-readable note text."""
    type_descriptions = {
        "large_range": "Large daily range",
        "large_move": "Large close-to-close move",
        "intraday_spike": "Sharp intraday spike",
    }
    
    type_desc = type_descriptions.get(anomaly_type, anomaly_type)
    base_note = f"{type_desc}: {value_bps:.1f} bps (z={z_score:.1f})"
    
    if event:
        event_name = event["event_name"]
        if event.get("actual") is not None and event.get("forecast") is not None:
            try:
                actual = float(event["actual"])
                forecast = float(event["forecast"])
                surprise = actual - forecast
                surprise_str = f"+{surprise:.2f}" if surprise > 0 else f"{surprise:.2f}"
                return f"{base_note}. {event_name} released (surprise: {surprise_str})."
            except (ValueError, TypeError):
                pass
        return f"{base_note}. {event_name} day."
    
    if news_summary:
        # Truncate news summary if too long
        summary = news_summary[:200] + "..." if len(news_summary) > 200 else news_summary
        return f"{base_note}. {summary}"
    
    return f"{base_note}. No scheduled event found, cause unattributed."


@register_tool(
    name="annotate_date_range",
    description="Detect anomalies and generate notes for a date range. Uses internal events first, Perplexity fallback.",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Symbol to analyze (default: TY)"},
            "z_threshold": {"type": "number", "description": "Z-score threshold (default: 1.5)"},
            "use_perplexity": {"type": "boolean", "description": "Use Perplexity for unexplained moves (default: True)"},
        },
        "required": ["start_date", "end_date"],
    },
    agent="anomaly_notes_agent",
)
def annotate_date_range(
    start_date: str,
    end_date: str,
    symbol: str = "TY",
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    use_perplexity: bool = True,
) -> Dict[str, Any]:
    """
    Full annotation pipeline: detect anomalies -> match events -> search news -> store notes.
    
    Process:
    1. Detect anomalous days (z-score > threshold)
    2. For each anomaly, check economic_events.db for scheduled events
    3. If no event found and use_perplexity=True, search Perplexity
    4. Store note with source attribution
    
    Returns:
        Summary of annotation run
    """
    logger.info(f"Starting annotation for {start_date} to {end_date}")
    
    # Step 1: Detect anomalies
    detection_result = detect_anomalies(start_date, end_date, symbol, z_threshold)
    
    if not detection_result["success"]:
        return {
            "success": False,
            "error": detection_result.get("error", "Detection failed"),
            "notes_created": 0,
        }
    
    anomalies = detection_result["anomalies"]
    logger.info(f"Found {len(anomalies)} anomaly days")
    
    if not anomalies:
        return {
            "success": True,
            "notes_created": 0,
            "total_days": detection_result["total_days"],
            "message": "No anomalies detected",
        }
    
    # Step 2-4: Process each anomaly
    notes_created = 0
    notes_by_source = {"economic_event": 0, "perplexity": 0, "unattributed": 0}
    perplexity_calls = 0
    
    for anomaly in anomalies:
        date = anomaly["date"]
        
        # Find events on this date
        events = _find_events_on_date(date)
        high_importance_event = next((e for e in events if e["importance"] == "high"), None)
        any_event = events[0] if events else None
        
        for atype in anomaly["anomaly_types"]:
            # Determine source and context
            if high_importance_event:
                source = "economic_event"
                event = high_importance_event
                news_summary = None
            elif any_event:
                source = "economic_event"
                event = any_event
                news_summary = None
            elif use_perplexity:
                # No event found - try Perplexity
                source = "perplexity"
                event = None
                perplexity_calls += 1
                
                context = f"{atype['type']}: {atype['value_bps']:.1f} bps move"
                search_result = search_market_news(date, symbol, context)
                
                if search_result["success"] and search_result.get("summary"):
                    news_summary = search_result["summary"]
                else:
                    source = "unattributed"
                    news_summary = None
            else:
                source = "unattributed"
                event = None
                news_summary = None
            
            # Generate note text
            note_text = _generate_note_text(
                atype["type"],
                atype["value_bps"],
                atype["z_score"],
                event,
                news_summary,
            )
            
            # Store note
            store_result = store_anomaly_note(
                date=date,
                anomaly_type=atype["type"],
                z_score=atype["z_score"],
                value_bps=atype["value_bps"],
                note=note_text,
                source=source,
                symbol=symbol,
                threshold_bps=atype.get("threshold_bps"),
                event_name=event["event_name"] if event else None,
                news_summary=news_summary,
            )
            
            if store_result["success"]:
                notes_created += 1
                notes_by_source[source] += 1
    
    logger.info(f"Created {notes_created} notes (by source: {notes_by_source})")
    
    return {
        "success": True,
        "notes_created": notes_created,
        "notes_by_source": notes_by_source,
        "anomaly_days": len(anomalies),
        "total_days": detection_result["total_days"],
        "perplexity_calls": perplexity_calls,
        "z_threshold": z_threshold,
        "start_date": start_date,
        "end_date": end_date,
    }


@register_tool(
    name="explain_move",
    description="Get context for a specific day's market move.",
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Date to explain (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Symbol (default: TY)"},
            "force_search": {"type": "boolean", "description": "Force Perplexity search even if event exists"},
        },
        "required": ["date"],
    },
    agent="anomaly_notes_agent",
)
def explain_move(
    date: str,
    symbol: str = "TY",
    force_search: bool = False,
) -> Dict[str, Any]:
    """
    Get context for a specific day's market move.
    
    Checks:
    1. Existing notes in database
    2. Economic events on that date
    3. Perplexity search (if no explanation found or force_search=True)
    
    Returns:
        Explanation context
    """
    from .storage import query_anomaly_notes
    
    # Check existing notes
    existing = query_anomaly_notes(start_date=date, end_date=date, symbol=symbol)
    if existing["success"] and existing["notes"]:
        logger.info(f"Found existing notes for {date}")
        return {
            "success": True,
            "date": date,
            "source": "database",
            "notes": existing["notes"],
            "events": _find_events_on_date(date),
        }
    
    # Check economic events
    events = _find_events_on_date(date)
    
    if events and not force_search:
        logger.info(f"Found {len(events)} events for {date}")
        return {
            "success": True,
            "date": date,
            "source": "economic_event",
            "notes": [],
            "events": events,
        }
    
    # Search Perplexity
    logger.info(f"Searching Perplexity for {date}")
    search_result = search_market_news(date, symbol)
    
    return {
        "success": search_result["success"],
        "date": date,
        "source": "perplexity",
        "notes": [],
        "events": events,
        "news_summary": search_result.get("summary"),
        "search_error": search_result.get("error"),
    }






