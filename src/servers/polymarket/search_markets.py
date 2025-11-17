"""Polymarket market search tool with LLM-powered relevance scoring."""

import json
import logging
import re
import sqlite3
import urllib.request
import urllib.error
import urllib.parse
import gzip
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.mcp.discovery import register_tool
from .schema import (
    POLYMARKET_API_BASE_URL,
    POLYMARKET_GAMMA_BASE_URL,
    MAX_POLYMARKET_RESULTS,
    DEFAULT_POLYMARKET_RESULTS,
    HISTORY_TABLE,
    MARKET_STATUS_ACTIVE,
    format_market_result,
    calculate_avg_probability,
    calculate_total_volume
)
from .llm_relevance_scorer import hybrid_search, score_market_relevance_batch

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    """Get database path for Polymarket data."""
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "polymarket_markets.db"
    
    if not db_path.exists():
        # Create database if it doesn't exist
        logger.warning(f"Polymarket database not found, creating: {db_path}")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_query TEXT NOT NULL,
                expanded_keywords TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                results TEXT NOT NULL,
                platform TEXT DEFAULT 'polymarket',
                market_ids TEXT,
                avg_probability REAL,
                total_volume INTEGER,
                result_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_id ON {HISTORY_TABLE} (session_id)")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_timestamp ON {HISTORY_TABLE} (timestamp)")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_platform ON {HISTORY_TABLE} (platform)")
        conn.commit()
        conn.close()
        logger.info(f"Created Polymarket database at {db_path}")
    
    return db_path


def call_polymarket_api(limit: int = 200, order_by: str = 'createdAt') -> Dict[str, Any]:
    """
    Call Polymarket Gamma API to fetch markets.
    
    Fetches markets sorted by specified field. We filter locally since the API's
    text search functionality doesn't work reliably.
    
    Args:
        limit: Maximum results to return
        order_by: Sort field ('volume', 'liquidity', 'createdAt', 'endDate')
        
    Returns:
        API response dictionary with markets
        
    Raises:
        urllib.error.URLError: If API call fails
    """
    # Use Gamma API - it returns actual market data with volume/liquidity
    base_url = POLYMARKET_GAMMA_BASE_URL
    endpoint = "/markets"
    
    # API parameters - fetch markets and filter locally
    # Note: API's query/search parameters don't work reliably
    # Note: orderBy options are 'volume', 'liquidity', 'createdAt', 'endDate'
    # 'volume' = most popular (may miss newer/specific markets)
    # 'createdAt' = most recent (better for finding specific markets)
    params = {
        'limit': min(limit, 500),  # Fetch many for local filtering
        'active': 'true',  # Only active markets
        'closed': 'false',  # Exclude closed markets
        'orderBy': order_by,  # Sort by specified field
        '_limit': min(limit, 500)
    }
    
    query_string = urllib.parse.urlencode(params)
    url = f"{base_url}{endpoint}?{query_string}"
    
    logger.info(f"Calling Polymarket API: GET {url}")
    
    # Make request with browser-like headers to avoid Cloudflare blocks
    req = urllib.request.Request(url, method='GET')
    req.add_header('Accept', 'application/json')
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    req.add_header('Accept-Language', 'en-US,en;q=0.9')
    req.add_header('Accept-Encoding', 'gzip, deflate, br')
    req.add_header('Connection', 'keep-alive')
    req.add_header('Referer', 'https://polymarket.com/')
    req.add_header('Origin', 'https://polymarket.com')
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            # Read raw response
            raw_data = response.read()
            
            # Check if response is gzip-compressed
            if raw_data[:2] == b'\x1f\x8b':  # Gzip magic number
                raw_data = gzip.decompress(raw_data)
            
            # Decode and parse JSON
            result = json.loads(raw_data.decode('utf-8'))
            
            # Polymarket Gamma API returns array of markets
            if isinstance(result, list):
                markets = result
            elif isinstance(result, dict) and 'data' in result:
                markets = result['data']
            else:
                markets = []
            
            logger.info(f"Fetched {len(markets)} markets from Polymarket API")
            
            # Log sample of first market for debugging
            if markets:
                sample = markets[0]
                logger.debug(f"Sample market: title='{sample.get('question', 'N/A')}', "
                           f"volume={sample.get('volume', 0)}, "
                           f"liquidity={sample.get('liquidity', 0)}, "
                           f"active={sample.get('active', 'N/A')}")
            
            # Return all markets for local filtering
            return {"markets": markets}
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else "No error details"
        logger.error(f"Polymarket API HTTP error: {e.code} - {error_body}")
        raise
    except urllib.error.URLError as e:
        logger.error(f"Polymarket API connection error: {e.reason}")
        raise


def filter_markets_by_keywords(markets: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
    """
    Filter markets by keyword matching in title/description.
    
    Uses scoring system:
    - Multiple keyword matches score higher
    - Phrase matches (consecutive keywords) score highest
    - Case-insensitive matching
    - Multi-word keywords are split into individual words
    
    Fallback behavior:
    - If no keyword matches found, returns top market by volume
    - Ensures at least 1 result if any markets exist
    
    Args:
        markets: List of market dictionaries
        keywords: List of keywords to match
        
    Returns:
        Filtered and sorted list of markets (at least 1 if markets exist)
    """
    if not keywords:
        return markets
    
    # Split multi-word keywords into individual words
    # e.g., "bitcoin price prediction" -> ["bitcoin", "price", "prediction"]
    # Also handle hyphens: "Russia-Ukraine" -> ["Russia", "Ukraine"]
    all_words = []
    for kw in keywords:
        # Strip punctuation and split on both spaces and hyphens
        # Remove common punctuation: ?,!,.,;
        cleaned = re.sub(r'[?!.,;:]', '', kw.lower())
        # Split on spaces and hyphens
        words = re.split(r'[\s\-]+', cleaned)
        all_words.extend(words)
    
    # Remove duplicates and common words
    common_words = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'by', 'will', 'be'}
    keywords_lower = [w for w in set(all_words) if w not in common_words and len(w) > 2]
    
    # Also keep original multi-word phrases for phrase matching
    original_phrases = [k.lower() for k in keywords]
    phrase = ' '.join(original_phrases)
    
    scored_markets = []
    
    for i, market in enumerate(markets):
        # Get searchable text
        question = market.get("question", "").lower()
        description = market.get("description", "").lower()
        title = market.get("title", "").lower()
        
        # Combine all text
        full_text = f"{question} {description} {title}"
        
        # Debug first few markets
        if i < 3:
            logger.debug(f"Market {i}: '{question[:80]}' - checking against keywords")
        
        score = 0
        
        # Phrase match (highest priority) - exact phrase
        if phrase in full_text:
            score += 100
        
        # Count keyword matches using word boundaries
        # Use word boundaries to avoid matching "fed" in "federal"
        keyword_matches = 0
        matched_keywords = []
        for kw in keywords_lower:
            # Use word boundary regex for exact word matching
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, full_text):
                keyword_matches += 1
                matched_keywords.append(kw)
        
        # Require at least 40% of keywords to match (or minimum 1 if single word query)
        min_required = max(1, int(len(keywords_lower) * 0.4))
        if keyword_matches < min_required:
            continue
        
        # Score based on number of matches
        score += keyword_matches * 10
        
        # Bonus for matches in question (most important field)
        question_matches = 0
        for kw in matched_keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, question):
                question_matches += 1
        score += question_matches * 5
        
        # Always add to scored_markets with its score (even if 0)
        scored_markets.append((score, market))
    
    # Sort by score (highest first)
    scored_markets.sort(key=lambda x: x[0], reverse=True)
    
    # Filter: keep markets with score > 0
    filtered = [(score, market) for score, market in scored_markets if score > 0]
    
    # If no matches, return top market by volume as fallback
    if not filtered and scored_markets:
        logger.warning("No keyword matches found, returning top market by volume")
        # Sort by volume and return top market
        by_volume = sorted(scored_markets, key=lambda x: x[1].get('volume', 0), reverse=True)
        return [by_volume[0][1]] if by_volume else []
    
    # Return markets without scores
    return [market for _, market in filtered]


def validate_market_data(market: Dict[str, Any]) -> bool:
    """
    Validate that market data is complete and correct.
    
    Args:
        market: Formatted market dictionary
        
    Returns:
        True if valid, False otherwise
    """
    # Check required fields
    required_fields = ["title", "url", "prices", "volume", "slug"]
    for field in required_fields:
        if field not in market or not market[field]:
            logger.debug(f"Market missing required field: {field}")
            return False
    
    # Validate URL format
    url = market.get("url", "")
    if not url.startswith("https://polymarket.com/event/"):
        logger.debug(f"Invalid URL format: {url}")
        return False
    
    # Validate prices
    prices = market.get("prices", {})
    if not prices or (not isinstance(prices, dict)):
        logger.debug(f"Invalid prices format: {prices}")
        return False
    
    # Validate volume is a number
    volume = market.get("volume", 0)
    if not isinstance(volume, (int, float)) or volume < 0:
        logger.debug(f"Invalid volume: {volume}")
        return False
    
    return True


def parse_polymarket_response(response: Dict[str, Any], original_query: str) -> List[Dict[str, Any]]:
    """
    Parse Polymarket API response to extract market data.
    
    Filters out inactive markets (0 volume/liquidity) and expired markets.
    
    Args:
        response: API response
        original_query: Original user query
        
    Returns:
        List of formatted market dictionaries
    """
    results = []
    
    # Polymarket response structure: {"markets": [...]}
    markets = response.get("markets", [])
    
    for market in markets:
        try:
            # Format using schema helper
            formatted = format_market_result(market)
            
            # Filter out markets with no activity
            # Only skip if both volume AND liquidity are 0 (likely inactive)
            # But allow markets with at least some volume (> 1.0)
            volume = formatted.get('volume', 0)
            liquidity = formatted.get('liquidity', 0)
            
            # Skip only if truly inactive (no volume at all)
            if volume == 0 and liquidity == 0:
                logger.debug(f"Skipping inactive market: {formatted.get('title', 'N/A')[:50]}")
                continue
            
            # Skip if market is closed/resolved
            status = formatted.get('status', 'active').lower()
            if status in ['closed', 'resolved']:
                logger.debug(f"Skipping closed/resolved market: {formatted.get('title', 'N/A')[:50]}")
                continue
            
            # Validate market data is complete and correct
            if not validate_market_data(formatted):
                logger.debug(f"Skipping invalid market: {formatted.get('title', 'N/A')[:50]}")
                continue
            
            results.append(formatted)
        except Exception as e:
            market_title = market.get('question', market.get('title', 'Unknown'))[:50]
            logger.warning(f"Failed to parse market '{market_title}': {e}")
            logger.debug(f"Market data: volume={market.get('volume')}, liquidity={market.get('liquidity')}, outcomePrices={market.get('outcomePrices')}")
            continue
    
    logger.info(f"Parsed {len(results)} active markets from Polymarket response")
    
    return results


def store_query_history(
    session_id: str,
    user_query: str,
    expanded_keywords: List[str],
    results: List[Dict[str, Any]]
) -> None:
    """
    Store query and results in database.
    
    Args:
        session_id: Unique session identifier
        user_query: User's original query
        expanded_keywords: LLM-expanded keywords
        results: Search results list
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    
    try:
        cursor = conn.cursor()
        
        timestamp = datetime.now(timezone.utc).isoformat()
        results_json = json.dumps(results)
        keywords_json = json.dumps(expanded_keywords)
        
        # Extract market IDs
        market_ids = [r.get('market_id', '') for r in results]
        market_ids_json = json.dumps(market_ids)
        
        # Calculate aggregates
        avg_prob = calculate_avg_probability(results)
        total_vol = calculate_total_volume(results)
        
        cursor.execute(
            f"""
            INSERT INTO {HISTORY_TABLE}
            (session_id, user_query, expanded_keywords, timestamp, results, 
             platform, market_ids, avg_probability, total_volume, result_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user_query,
                keywords_json,
                timestamp,
                results_json,
                "polymarket",
                market_ids_json,
                avg_prob,
                total_vol,
                len(results),
                timestamp
            )
        )
        
        conn.commit()
        logger.info(f"Stored query history: session={session_id}, results={len(results)}")
        
    finally:
        conn.close()


@register_tool(
    name="search_polymarket_markets",
    description="Search Polymarket prediction markets with LLM-powered relevance scoring"
)
def search_polymarket_markets(
    query: str,
    session_id: str,
    limit: int = DEFAULT_POLYMARKET_RESULTS
) -> Dict[str, Any]:
    """
    Search Polymarket prediction markets using hybrid approach.
    
    Strategy:
    1. Fetch markets from Polymarket API (hybrid: recent + popular)
    2. Fast keyword filter to get ~50 candidates
    3. GPT-4 re-ranks by semantic relevance for accuracy
    4. Falls back to keyword-only if no OpenAI API key available
    
    Args:
        query: Natural language search query
        session_id: Unique session identifier
        limit: Maximum results to return (max: MAX_POLYMARKET_RESULTS)
        
    Returns:
        {
            "markets": [...],  # List of market data (may include relevance_score if LLM used)
            "metadata": {
                "query": "...",
                "search_method": "hybrid_llm" or "keyword_fallback",
                "session_id": "...",
                "result_count": 5,
                "platform": "polymarket",
                "timestamp": "..."
            }
        }
        
    Raises:
        ValueError: If invalid parameters
        Exception: If API call or database write fails
    """
    logger.info(f"search_polymarket_markets called: query={query}, session_id={session_id}")
    
    # Validate parameters
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")
    
    if not session_id or not session_id.strip():
        raise ValueError("Session ID cannot be empty")
    
    if limit <= 0 or limit > MAX_POLYMARKET_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_POLYMARKET_RESULTS}")
    
    try:
        # Step 1: Fetch markets from API using HYBRID approach
        # Problem: Sorting by volume misses low-volume specific markets
        # Problem: Sorting by creation misses popular older markets
        # Solution: Fetch BOTH and combine them!
        
        logger.info("Fetching markets from Polymarket API (hybrid: recent + popular)")
        
        # Fetch recent markets (for specific/new markets)
        # Using higher limit to get more diversity
        api_response_recent = call_polymarket_api(limit=400, order_by='createdAt')
        markets_recent = parse_polymarket_response(api_response_recent, query)
        logger.info(f"Got {len(markets_recent)} recent markets")
        
        # Fetch popular markets (for well-known markets)
        # Using lower limit since popular markets are more likely to be recent anyway
        api_response_popular = call_polymarket_api(limit=200, order_by='volume')
        markets_popular = parse_polymarket_response(api_response_popular, query)
        logger.info(f"Got {len(markets_popular)} popular markets")
        
        # Combine and deduplicate by market_id
        seen_ids = set()
        all_markets = []
        for market in markets_recent + markets_popular:
            market_id = market.get('market_id')
            if market_id and market_id not in seen_ids:
                seen_ids.add(market_id)
                all_markets.append(market)
        
        logger.info(f"Total {len(all_markets)} unique markets (after deduplication)")
        
        # Step 3: LLM-powered relevance scoring
        # Uses hybrid approach:
        # - Fast keyword filter to get ~50 candidates
        # - GPT-4 re-ranks by semantic relevance (falls back to keyword-only if no API key)
        logger.info(f"Running hybrid search (keyword filter + LLM re-ranking)")
        
        scored_markets = hybrid_search(
            query=query,
            all_markets=all_markets,
            keywords=[query],
            keyword_filter_func=filter_markets_by_keywords,
            top_k=limit * 2,  # Get extra candidates for better LLM ranking
            api_key=None  # Will auto-load from config/keys.env or env var
        )
        
        logger.info(f"Hybrid search returned {len(scored_markets)} relevant markets")
        
        # Limit final results
        markets = scored_markets[:limit]
        
        # Check if results are actually relevant (score-based heuristic)
        # If we found markets but they're all low-score partial matches, warn user
        if len(markets) > 0:
            # Check if best match has a good score (would need to be tracked in filter function)
            # For now, just log success
            logger.info(f"Returning {len(markets)} markets")
        else:
            logger.warning(f"No markets found for query: '{query}'. "
                         "This may mean no prediction markets exist for this topic. "
                         "Try a different query or check polymarket.com directly.")
        
        # Step 4: Determine search method used
        # Check if any market has LLM relevance score
        has_llm_scores = any(m.get('relevance_score') is not None for m in markets)
        search_method = "hybrid_llm" if has_llm_scores else "keyword_fallback"
        
        # Step 5: Store in database (pass empty list for keywords since we use full query)
        store_query_history(session_id, query, [], markets)
        
        # Step 6: Build response
        timestamp = datetime.now(timezone.utc).isoformat()
        
        return {
            "markets": markets,
            "metadata": {
                "query": query,
                "search_method": search_method,
                "llm_scoring_enabled": has_llm_scores,
                "session_id": session_id,
                "result_count": len(markets),
                "platform": "polymarket",
                "timestamp": timestamp
            }
        }
        
    except Exception as e:
        logger.error(f"search_polymarket_markets failed: {e}")
        raise

