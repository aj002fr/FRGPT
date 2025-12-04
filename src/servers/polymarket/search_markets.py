"""Polymarket market search tool - simple query-based search."""

import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import gzip
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.mcp.discovery import register_tool
from .schema import (
    POLYMARKET_GAMMA_BASE_URL,
    MAX_POLYMARKET_RESULTS,
    DEFAULT_POLYMARKET_RESULTS,
    format_market_result,
)

logger = logging.getLogger(__name__)


def call_polymarket_search_api(query: str, limit_per_type: int = DEFAULT_POLYMARKET_RESULTS) -> Dict[str, Any]:
    """
    Call Polymarket Gamma /public-search API to search markets by text query.

    This endpoint lets the API pre-filter markets by the user's query string.
    We still apply strong local validation afterwards.

    Docs: https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles

    Args:
        query: Natural language search query
        limit_per_type: Maximum results per type (events/markets/profiles)

    Returns:
        Raw API response dictionary.

    Raises:
        urllib.error.URLError: If API call fails.
    """
    base_url = POLYMARKET_GAMMA_BASE_URL
    endpoint = "/public-search"

    params = {
        "q": query,
        "events_status": "active",  # Only active events/markets
        "keep_closed_markets": 0,
        "limit_per_type": min(limit_per_type, MAX_POLYMARKET_RESULTS),
        "search_profiles": False,
        "search_tags": False
    }

    query_string = urllib.parse.urlencode(params)
    url = f"{base_url}{endpoint}?{query_string}"

    logger.info(f"Calling Polymarket public-search API: GET {url}")

    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    req.add_header("Accept-Language", "en-US,en;q=0.9")
    req.add_header("Accept-Encoding", "gzip, deflate, br")
    req.add_header("Connection", "keep-alive")
    req.add_header("Referer", "https://polymarket.com/")
    req.add_header("Origin", "https://polymarket.com")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw_data = response.read()

            if raw_data[:2] == b"\x1f\x8b":  # Gzip magic number
                raw_data = gzip.decompress(raw_data)

            result = json.loads(raw_data.decode("utf-8"))

            # Expected structure: {"events": [...], "tags": [...], "profiles": [...], "pagination": {...}}
            logger.info(
                "Polymarket public-search returned %d events",
                len(result.get("events", [])),
            )
            return result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "No error details"
        logger.error(f"Polymarket public-search HTTP error: {e.code} - {error_body}")
        raise
    except urllib.error.URLError as e:
        logger.error(f"Polymarket public-search connection error: {e.reason}")
        raise


def validate_market_data(market: Dict[str, Any]) -> bool:
    """
    Validate that market data is complete and correct.
    
    Args:
        market: Formatted market dictionary
        
    Returns:
        True if valid, False otherwise
    """
    # Check required fields (minimal for robustness)
    required_fields = ["title", "prices"]
    for field in required_fields:
        if field not in market or not market[field]:
            logger.debug(f"Market missing required field: {field}")
            return False
    
    # Validate prices
    prices = market.get("prices", {})
    if not prices or (not isinstance(prices, dict)):
        logger.debug(f"Invalid prices format: {prices}")
        return False

    return True


def parse_public_search_response(response: Dict[str, Any], original_query: str) -> List[Dict[str, Any]]:
    """
    Parse Polymarket /public-search API response to extract market data.

    The response groups markets under events. We extract all markets from
    active events, format them, and apply the same validation as the generic
    /markets parser.

    Args:
        response: Raw /public-search API response.
        original_query: Original user query (for logging/debug).

    Returns:
        List of formatted market dictionaries.
    """
    results: List[Dict[str, Any]] = []

    events = response.get("events") or []

    for event in events:
        markets = event.get("markets") or []
        for market in markets:
            try:
                formatted = format_market_result(market)

                status = formatted.get("status", "active").lower()
                if status in ["closed", "resolved"]:
                    logger.debug(
                        "Skipping closed/resolved search market: %s",
                        formatted.get("title", "N/A")[:50],
                    )
                    continue

                if not validate_market_data(formatted):
                    logger.debug(
                        "Skipping invalid search market: %s",
                        formatted.get("title", "N/A")[:50],
                    )
                    continue

                results.append(formatted)
            except Exception as exc:  # pragma: no cover - defensive
                market_title = market.get("question", market.get("title", "Unknown"))[:50]
                logger.warning(
                    "Failed to parse search market '%s': %s", market_title, exc
                )
                continue

    logger.info(
        "Parsed %d active markets from Polymarket public-search response", len(results)
    )

    return results


@register_tool(
    name="search_polymarket_markets",
    description="Search Polymarket prediction markets using query-based search API"
)
def search_polymarket_markets(
    query: str,
    session_id: str,
    limit: int = DEFAULT_POLYMARKET_RESULTS
) -> Dict[str, Any]:
    """
    Search Polymarket prediction markets using simple API call.
    
    Strategy:
    1. Call /public-search API with user's query (server-side filtering)
    2. Parse and validate markets from API response
    3. Return formatted market data
    
    Args:
        query: Natural language search query (e.g., "bitcoin price prediction")
        session_id: Unique session identifier for tracking queries (unused but kept for compatibility)
        limit: Maximum results to return (default: 5, max: 50)
        
    Returns:
        {
            "markets": [
                {
                    "title": "...",
                    "market_id": "...",
                    "prices": {"yes": 0.65, "no": 0.35},
                    "volume": 1234567,
                    "liquidity": 50000,
                    "status": "active",
                    "url": "..."
                },
                ...
            ],
            "metadata": {
                "query": "...",
                "session_id": "...",
                "result_count": 5,
                "platform": "polymarket",
                "timestamp": "..."
            }
        }
        
    Raises:
        ValueError: If invalid parameters
        Exception: If API call fails
    """
    logger.info(f"search_polymarket_markets called: query={query}, session_id={session_id}, limit={limit}")
    
    # Validate parameters
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")
    
    # Generate session_id if not provided (for direct tool calls)
    if not session_id or not session_id.strip():
        import hashlib
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        hash_input = f"{run_id}_{query}"
        hash_obj = hashlib.sha256(hash_input.encode())
        session_id = f"{run_id}_{hash_obj.hexdigest()[:6]}"
        logger.info(f"Generated session_id: {session_id}")
    
    # Use default limit if None provided
    if limit is None:
        limit = DEFAULT_POLYMARKET_RESULTS
    
    if limit <= 0 or limit > MAX_POLYMARKET_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_POLYMARKET_RESULTS}")
    
    try:
        # Step 1: Call Polymarket public-search API
        logger.info(f"Calling Polymarket /public-search API with query: '{query}'")
        api_response = call_polymarket_search_api(query, limit_per_type=limit)
        
        # Step 2: Parse and validate markets
        all_markets = parse_public_search_response(api_response, query)
        logger.info(f"API returned {len(all_markets)} valid markets")
        
        # Step 3: Limit results
        markets = all_markets[:limit]
        
        # Step 4: Build response
        timestamp = datetime.now(timezone.utc).isoformat()
        
        if not markets:
            logger.warning(
                f"No markets found for query: '{query}'. "
                "No prediction markets may exist for this topic yet."
            )
        
        return {
            "markets": markets,
            "metadata": {
                "query": query,
                "session_id": session_id,
                "result_count": len(markets),
                "platform": "polymarket",
                "timestamp": timestamp
            }
        }
        
    except Exception as e:
        logger.error(f"search_polymarket_markets failed: {e}")
        raise
