"""
Date Tags Store - CRUD operations for trader notes/annotations.

Stores date-tagged notes in the economic_events.db database.
Supports flexible date/time parsing and hashtag categorization.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import dateparser
    DATEPARSER_AVAILABLE = True
except ImportError:
    DATEPARSER_AVAILABLE = False

from src.servers.tradingeconomics.schema import (
    CREATE_DATE_TAGS_TABLE,
    CREATE_DATE_TAGS_INDICES,
    get_db_path,
)

logger = logging.getLogger(__name__)

# Dateparser settings
DATEPARSER_SETTINGS = {
    'DATE_ORDER': 'DMY',
    'PREFER_DATES_FROM': 'past',
    'TIMEZONE': 'US/Eastern',
    'RETURN_AS_TIMEZONE_AWARE': False,
}

# Hashtag pattern
HASHTAG_PATTERN = re.compile(r'#(\w+)')


def _get_connection() -> sqlite3.Connection:
    """Get database connection and ensure table exists."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    # Ensure table exists
    cursor = conn.cursor()
    cursor.executescript(CREATE_DATE_TAGS_TABLE)
    cursor.executescript(CREATE_DATE_TAGS_INDICES)
    conn.commit()
    
    return conn


def _extract_hashtags(text: str) -> tuple[list[str], str]:
    """
    Extract hashtags from text and return (hashtags, cleaned_text).
    
    Example:
        "#nfp #fed NFP came in hot" -> (["nfp", "fed"], "NFP came in hot")
    """
    hashtags = HASHTAG_PATTERN.findall(text)
    # Remove hashtags from text
    cleaned = HASHTAG_PATTERN.sub('', text).strip()
    # Clean up multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return [h.lower() for h in hashtags], cleaned


def _parse_datetime_greedy(text: str) -> tuple[Optional[datetime], bool, str]:
    """
    Greedily parse datetime from the start of text.
    
    Returns:
        (parsed_datetime, has_time, remaining_text)
        
    Tries progressively longer prefixes until parsing fails.
    """
    if not DATEPARSER_AVAILABLE:
        logger.warning("dateparser not available, falling back to basic parsing")
        return None, False, text
    
    words = text.split()
    if not words:
        return None, False, text
    
    best_dt = None
    best_has_time = False
    best_idx = 0
    
    # Try progressively longer prefixes, but stop at hashtags
    for i in range(1, len(words) + 1):
        # Stop if current word is a hashtag - don't include it in datetime
        if words[i-1].startswith('#'):
            break
            
        prefix = ' '.join(words[:i])
            
        parsed = dateparser.parse(prefix, settings=DATEPARSER_SETTINGS)
        
        if parsed:
            best_dt = parsed
            best_idx = i
            # Check if time was specified (not midnight default)
            has_time_indicators = any(x in prefix.lower() for x in [
                ':', 'am', 'pm', 'noon', 'midnight', 'morning', 'afternoon', 'evening'
            ])
            best_has_time = has_time_indicators or (parsed.hour != 0 or parsed.minute != 0)
    
    if best_dt:
        remaining = ' '.join(words[best_idx:])
        return best_dt, best_has_time, remaining
    
    return None, False, text


def add_tag(
    raw_input: str,
    created_by: Optional[str] = None,
) -> dict:
    """
    Add a date tag from raw user input.
    
    Args:
        raw_input: Full input like "jan 10 8:30am #nfp NFP beat expectations"
        created_by: Telegram username (optional)
        
    Returns:
        dict with success status and tag details
    """
    if not raw_input.strip():
        return {"success": False, "error": "Empty input"}
    
    # Parse datetime from start of input
    parsed_dt, has_time, remaining = _parse_datetime_greedy(raw_input)
    
    if not parsed_dt:
        return {
            "success": False, 
            "error": "Could not parse date. Try: 'jan 10', 'yesterday', '10-01', etc."
        }
    
    if not remaining.strip():
        return {"success": False, "error": "No comment provided after date"}
    
    # Extract hashtags from remaining text
    hashtags, comment = _extract_hashtags(remaining)
    
    if not comment.strip():
        return {"success": False, "error": "No comment text after hashtags"}
    
    # Prepare values
    tag_date = parsed_dt.strftime('%Y-%m-%d')
    tag_datetime = parsed_dt.isoformat() if has_time else None
    hashtags_json = json.dumps(hashtags) if hashtags else None
    created_at = datetime.now().isoformat()
    
    # Insert
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO date_tags (tag_date, tag_datetime, has_time, comment, hashtags, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (tag_date, tag_datetime, 1 if has_time else 0, comment, hashtags_json, created_by, created_at))
    
    tag_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.info(f"Added tag {tag_id}: {tag_date} - {comment[:50]}...")
    
    return {
        "success": True,
        "id": tag_id,
        "tag_date": tag_date,
        "tag_datetime": tag_datetime,
        "has_time": has_time,
        "comment": comment,
        "hashtags": hashtags,
        "created_by": created_by,
    }


def get_tags(
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    hashtag: Optional[str] = None,
    limit: int = 10,
    created_by: Optional[str] = None,
) -> list[dict]:
    """
    Get tags with optional filters.
    
    Args:
        date: Specific date (flexible format)
        start_date: Start of date range
        end_date: End of date range
        hashtag: Filter by hashtag (without #)
        limit: Max results
        created_by: Filter by creator
        
    Returns:
        List of tag dicts
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM date_tags WHERE 1=1"
    params = []
    
    # Parse date if provided
    if date:
        if DATEPARSER_AVAILABLE:
            parsed = dateparser.parse(date, settings=DATEPARSER_SETTINGS)
            if parsed:
                date = parsed.strftime('%Y-%m-%d')
        query += " AND tag_date = ?"
        params.append(date)
    
    if start_date:
        if DATEPARSER_AVAILABLE:
            parsed = dateparser.parse(start_date, settings=DATEPARSER_SETTINGS)
            if parsed:
                start_date = parsed.strftime('%Y-%m-%d')
        query += " AND tag_date >= ?"
        params.append(start_date)
    
    if end_date:
        if DATEPARSER_AVAILABLE:
            parsed = dateparser.parse(end_date, settings=DATEPARSER_SETTINGS)
            if parsed:
                end_date = parsed.strftime('%Y-%m-%d')
        query += " AND tag_date <= ?"
        params.append(end_date)
    
    if hashtag:
        # Search in JSON array
        hashtag_clean = hashtag.lower().lstrip('#')
        query += " AND hashtags LIKE ?"
        params.append(f'%"{hashtag_clean}"%')
    
    if created_by:
        query += " AND created_by = ?"
        params.append(created_by)
    
    query += " ORDER BY tag_date DESC, tag_datetime DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        tag = dict(row)
        # Parse hashtags JSON
        if tag.get('hashtags'):
            try:
                tag['hashtags'] = json.loads(tag['hashtags'])
            except json.JSONDecodeError:
                tag['hashtags'] = []
        else:
            tag['hashtags'] = []
        results.append(tag)
    
    return results


def get_tag_stats() -> dict:
    """Get statistics on tag usage by hashtag."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    # Get all tags
    cursor.execute("SELECT hashtags FROM date_tags WHERE hashtags IS NOT NULL")
    rows = cursor.fetchall()
    
    # Count hashtags
    hashtag_counts = {}
    for row in rows:
        try:
            tags = json.loads(row['hashtags'])
            for tag in tags:
                hashtag_counts[tag] = hashtag_counts.get(tag, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Get total count
    cursor.execute("SELECT COUNT(*) as total FROM date_tags")
    total = cursor.fetchone()['total']
    
    # Get uncategorized count
    cursor.execute("SELECT COUNT(*) as count FROM date_tags WHERE hashtags IS NULL OR hashtags = '[]'")
    uncategorized = cursor.fetchone()['count']
    
    conn.close()
    
    # Sort by count descending
    sorted_hashtags = sorted(hashtag_counts.items(), key=lambda x: -x[1])
    
    return {
        "total_tags": total,
        "uncategorized": uncategorized,
        "hashtag_counts": sorted_hashtags,
    }


def delete_tag(tag_id: int, created_by: Optional[str] = None) -> dict:
    """
    Delete a tag by ID.
    
    Args:
        tag_id: Tag ID to delete
        created_by: If provided, only delete if creator matches
        
    Returns:
        dict with success status
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    # Check if tag exists
    cursor.execute("SELECT * FROM date_tags WHERE id = ?", (tag_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return {"success": False, "error": f"Tag {tag_id} not found"}
    
    # Check creator if specified
    if created_by and row['created_by'] != created_by:
        conn.close()
        return {"success": False, "error": "You can only delete your own tags"}
    
    # Delete
    cursor.execute("DELETE FROM date_tags WHERE id = ?", (tag_id,))
    conn.commit()
    conn.close()
    
    logger.info(f"Deleted tag {tag_id}")
    
    return {
        "success": True,
        "id": tag_id,
        "deleted_tag": dict(row),
    }


def format_tag_for_display(tag: dict) -> str:
    """Format a tag dict for Telegram display."""
    lines = []
    
    # Date/time
    if tag.get('has_time') and tag.get('tag_datetime'):
        dt = datetime.fromisoformat(tag['tag_datetime'])
        date_str = dt.strftime('%Y-%m-%d %H:%M EST')
    else:
        date_str = tag['tag_date']
    
    # Hashtags
    hashtags_str = ' '.join(f"#{h}" for h in tag.get('hashtags', []))
    
    lines.append(f"[{tag['id']}] {date_str}")
    lines.append(f"    {tag['comment']}")
    if hashtags_str:
        lines.append(f"    🏷️ {hashtags_str}")
    if tag.get('created_by'):
        lines.append(f"    — @{tag['created_by']}")
    
    return '\n'.join(lines)

