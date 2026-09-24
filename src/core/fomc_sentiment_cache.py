"""
FOMC Sentiment Cache
====================
Persistent JSON cache for per-date FOMC sentiment labels and summaries.

Cache location: workspace/fed/fomc conference/fomc_web_labels.json

Cache entry structure:
{
    "YYYY-MM-DD": {
        "label": "hawkish|dovish|neutral|unknown",
        "confidence": 0.0-1.0,
        "summary": "Brief summary of what happened",
        "citations": [{"url": "...", "title": "..."}],
        "prompt_version": "v1",
        "cached_at": "ISO timestamp",
        "source": "perplexity"
    }
}
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cache file location
PROJECT_ROOT = Path(__file__).parent.parent.parent
CACHE_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference"
CACHE_FILE = CACHE_DIR / "fomc_web_labels.json"


def _ensure_cache_dir() -> None:
    """Ensure the cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_cache() -> Dict[str, Any]:
    """Load the cache from disk."""
    if not CACHE_FILE.exists():
        return {}
    
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load cache: {e}")
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    """Save the cache to disk."""
    _ensure_cache_dir()
    
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Failed to save cache: {e}")


def get_cached_sentiment(
    date: str,
    prompt_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get cached sentiment for a date.
    
    Args:
        date: Date string (YYYY-MM-DD)
        prompt_version: If specified, only return if cache entry matches this version
        
    Returns:
        Cached entry dict or None if not found/version mismatch
    """
    cache = _load_cache()
    entry = cache.get(date)
    
    if entry is None:
        return None
    
    # Check prompt version if specified
    if prompt_version and entry.get("prompt_version") != prompt_version:
        logger.debug(f"Cache version mismatch for {date}: {entry.get('prompt_version')} != {prompt_version}")
        return None
    
    return entry


def set_cached_sentiment(
    date: str,
    label: str,
    confidence: float,
    summary: str,
    citations: List[Dict[str, str]],
    prompt_version: str,
    source: str = "perplexity",
) -> None:
    """
    Store sentiment in cache.
    
    Args:
        date: Date string (YYYY-MM-DD)
        label: Sentiment label (hawkish/dovish/neutral/unknown)
        confidence: Confidence score 0-1
        summary: Brief summary text
        citations: List of citation dicts
        prompt_version: Version of prompt used
        source: Data source (default: perplexity)
    """
    cache = _load_cache()
    
    cache[date] = {
        "label": label,
        "confidence": confidence,
        "summary": summary,
        "citations": citations,
        "prompt_version": prompt_version,
        "source": source,
        "cached_at": datetime.utcnow().isoformat() + "Z",
    }
    
    _save_cache(cache)
    logger.info(f"Cached sentiment for {date}: {label}")


def get_all_cached_sentiments() -> Dict[str, Dict[str, Any]]:
    """Get all cached sentiments."""
    return _load_cache()


def clear_cache() -> None:
    """Clear all cached sentiments."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
        logger.info("Cache cleared")


def get_or_fetch_sentiment(
    date: str,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Get sentiment from cache or fetch from Perplexity.
    
    Args:
        date: Date string (YYYY-MM-DD)
        force_refresh: If True, skip cache and fetch fresh
        
    Returns:
        Sentiment dict with label, confidence, summary, citations, etc.
    """
    # Import here to avoid circular imports
    from src.servers.anomaly_detection.perplexity_search import (
        FOMC_SENTIMENT_PROMPT_VERSION,
        search_fomc_sentiment,
    )
    
    # Check cache first (unless force_refresh)
    if not force_refresh:
        cached = get_cached_sentiment(date, prompt_version=FOMC_SENTIMENT_PROMPT_VERSION)
        if cached:
            logger.debug(f"Using cached sentiment for {date}")
            return {
                "success": True,
                "cached": True,
                **cached,
            }
    
    # Fetch from Perplexity
    result = search_fomc_sentiment(date)
    
    if result["success"]:
        # Store in cache
        set_cached_sentiment(
            date=date,
            label=result["label"],
            confidence=result["confidence"],
            summary=result["summary"],
            citations=result.get("citations", []),
            prompt_version=result["prompt_version"],
            source=result["source"],
        )
        result["cached"] = False
    
    return result


def batch_get_or_fetch_sentiments(
    dates: List[str],
    force_refresh: bool = False,
    progress_callback: Optional[callable] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Get sentiments for multiple dates, using cache when available.
    
    Args:
        dates: List of date strings (YYYY-MM-DD)
        force_refresh: If True, skip cache for all dates
        progress_callback: Optional callback(current, total, date) for progress
        
    Returns:
        Dict mapping date -> sentiment result
    """
    results = {}
    total = len(dates)
    
    for i, date in enumerate(dates):
        if progress_callback:
            progress_callback(i + 1, total, date)
        
        results[date] = get_or_fetch_sentiment(date, force_refresh=force_refresh)
    
    return results





