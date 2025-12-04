"""Unified Polymarket search tool that combines current search with historical price data."""

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, List

from src.mcp.discovery import register_tool
from .search_markets import search_polymarket_markets
from .get_price_history import fetch_price_history_from_polymarket, find_price_at_target_time

logger = logging.getLogger(__name__)


@register_tool(
    name="search_polymarket_with_history",
    description=(
        "Search Polymarket prediction markets and show price changes from a past date. "
        "Uses CLOB API to fetch historical prices for each market found."
    )
)
def search_polymarket_with_history(
    query: str,
    limit: Optional[int] = None,
    session_id: Optional[str] = None,
    historical_date: Optional[str] = None,
    days_back: int = 7
) -> Dict[str, Any]:
    """
    Search markets and fetch historical prices using market token IDs.
    
    Workflow:
    1. Search for markets matching query (get current prices + token IDs)
    2. For each market, fetch historical price from CLOB API using token ID
    3. Calculate price change (delta and percentage)
    4. Return markets with current + historical + price change
    
    Args:
        query: Natural language search query (e.g., "bitcoin predictions")
        limit: Maximum number of markets to return (default: 5, max: 50)
        session_id: Optional session ID for tracking (auto-generated if not provided)
        historical_date: Historical date in ISO format (YYYY-MM-DD). If not provided, uses days_back
        days_back: Days to look back if historical_date not provided (default: 7)
        
    Returns:
        {
            "markets": [
                {
                    "title": "...",
                    "market_id": "...",
                    "current_price": {"yes": 0.65, "no": 0.35},
                    "historical_price": {"yes": 0.55, "no": 0.45},
                    "price_change": {
                        "yes": +0.10,
                        "no": -0.10,
                        "yes_percent": +18.2,
                        "no_percent": -22.2,
                        "direction": "up"
                    },
                    "current_date": "2025-11-27",
                    "historical_date": "2025-11-20",
                    "volume": 1234567,
                    "url": "..."
                }
            ],
            "metadata": {
                "query": "...",
                "session_id": "...",
                "result_count": 5,
                "current_date": "2025-11-27",
                "historical_date": "2025-11-20",
                "days_back": 7,
                "platform": "polymarket",
                "timestamp": "..."
            }
        }
        
    Raises:
        ValueError: If query is empty or parameters are invalid
        
    Example:
        >>> # Compare with 1 week ago (default)
        >>> result = search_polymarket_with_history("bitcoin predictions", limit=5)
        >>> 
        >>> # Compare with specific date
        >>> result = search_polymarket_with_history(
        ...     "bitcoin predictions",
        ...     historical_date="2025-01-01"
        ... )
    """
    logger.info(f"search_polymarket_with_history called: query='{query}', limit={limit}, historical_date={historical_date}, days_back={days_back}")
    
    # Validate query
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")
    
    # Generate session_id if not provided
    if not session_id or not session_id.strip():
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        hash_input = f"{run_id}_{query}"
        hash_obj = hashlib.sha256(hash_input.encode())
        session_id = f"{run_id}_{hash_obj.hexdigest()[:6]}"
        logger.info(f"Generated session_id: {session_id}")
    
    # Determine historical date
    current_date = datetime.now(timezone.utc)
    if historical_date:
        try:
            hist_date = datetime.fromisoformat(historical_date.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Invalid historical_date format: {historical_date}. Use YYYY-MM-DD")
    else:
        hist_date = current_date - timedelta(days=days_back)
    
    hist_date_str = hist_date.strftime("%Y-%m-%d")
    current_date_str = current_date.strftime("%Y-%m-%d")
    hist_timestamp = int(hist_date.timestamp())
    
    logger.info(f"Comparing: current={current_date_str}, historical={hist_date_str}")
    
    try:
        # Step 1: Search current markets
        logger.info(f"Searching markets: query='{query}'")
        current_results = search_polymarket_markets(
            query=query,
            session_id=session_id,
            limit=limit
        )
        
        current_markets = current_results.get('markets', [])
        logger.info(f"Found {len(current_markets)} current markets")
        
        # Step 2: For each market, fetch historical price using token ID
        enriched_markets = []
        for market in current_markets:
            market_id = market.get('market_id')
            clob_token_ids = market.get('clob_token_ids', [])
            
            if not clob_token_ids:
                logger.warning(f"Market {market_id} has no token IDs, skipping historical price")
                enriched_market = {
                    **market,
                    "current_price": market['prices'],
                    "historical_price": None,
                    "price_change": None,
                    "current_date": current_date_str,
                    "historical_date": hist_date_str,
                    "note": "No token ID available for historical price lookup"
                }
                enriched_markets.append(enriched_market)
                continue
            
            # Use first token ID (usually the "Yes" outcome)
            token_id = clob_token_ids[0]
            
            try:
                logger.debug(f"Fetching historical price for token {token_id}")
                
                # Fetch price history around the target date
                # Use a window of +/- 24 hours
                start_ts = hist_timestamp - (24 * 3600)
                end_ts = hist_timestamp + (24 * 3600)
                
                history = fetch_price_history_from_polymarket(
                    market_id=token_id,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    interval="1h",
                    fidelity=60  # 1-hour resolution
                )
                
                if history:
                    # Find price at target time
                    hist_yes_price = find_price_at_target_time(history, hist_timestamp)
                    
                    if hist_yes_price is not None:
                        # Calculate historical "no" price (complement)
                        hist_no_price = 1.0 - hist_yes_price
                        
                        historical_price = {
                            "yes": hist_yes_price,
                            "no": hist_no_price
                        }
                        
                        # Calculate price change
                        current_yes = market['prices'].get('yes', market['prices'].get('Yes', 0.5))
                        current_no = market['prices'].get('no', market['prices'].get('No', 0.5))
                        
                        yes_change = current_yes - hist_yes_price
                        no_change = current_no - hist_no_price
                        
                        # Calculate percentage change
                        yes_pct = (yes_change / hist_yes_price * 100) if hist_yes_price > 0 else 0
                        no_pct = (no_change / hist_no_price * 100) if hist_no_price > 0 else 0
                        
                        # Determine direction based on "yes" price
                        if yes_change > 0.01:
                            direction = "up"
                        elif yes_change < -0.01:
                            direction = "down"
                        else:
                            direction = "stable"
                        
                        price_change = {
                            "yes": round(yes_change, 4),
                            "no": round(no_change, 4),
                            "yes_percent": round(yes_pct, 2),
                            "no_percent": round(no_pct, 2),
                            "direction": direction
                        }
                        
                        enriched_market = {
                            **market,
                            "current_price": market['prices'],
                            "historical_price": historical_price,
                            "price_change": price_change,
                            "current_date": current_date_str,
                            "historical_date": hist_date_str,
                            "data_points": len(history)
                        }
                        
                        enriched_markets.append(enriched_market)
                        logger.info(f"Market '{market['title'][:50]}': {direction} {yes_change:+.2%}")
                        
                    else:
                        # Could not interpolate price
                        logger.warning(f"Could not interpolate price for {token_id}")
                        enriched_market = {
                            **market,
                            "current_price": market['prices'],
                            "historical_price": None,
                            "price_change": None,
                            "current_date": current_date_str,
                            "historical_date": hist_date_str,
                            "note": "Could not interpolate historical price from data points"
                        }
                        enriched_markets.append(enriched_market)
                else:
                    # No historical data available
                    logger.warning(f"No price history found for token {token_id}")
                    enriched_market = {
                        **market,
                        "current_price": market['prices'],
                        "historical_price": None,
                        "price_change": None,
                        "current_date": current_date_str,
                        "historical_date": hist_date_str,
                        "note": "No historical price data available from API"
                    }
                    enriched_markets.append(enriched_market)
                    
            except Exception as e:
                logger.error(f"Failed to fetch historical price for {token_id}: {e}")
                enriched_market = {
                    **market,
                    "current_price": market['prices'],
                    "historical_price": None,
                    "price_change": None,
                    "current_date": current_date_str,
                    "historical_date": hist_date_str,
                    "error": str(e)
                }
                enriched_markets.append(enriched_market)
        
        # Step 3: Build response
        timestamp = datetime.now(timezone.utc).isoformat()
        
        result = {
            "markets": enriched_markets,
            "metadata": {
                "query": query,
                "session_id": session_id,
                "result_count": len(enriched_markets),
                "current_date": current_date_str,
                "historical_date": hist_date_str,
                "days_back": (current_date - hist_date).days,
                "platform": "polymarket",
                "timestamp": timestamp
            }
        }
        
        logger.info(
            f"Unified search complete: {len(enriched_markets)} markets with price comparisons"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"search_polymarket_with_history failed: {e}")
        raise

