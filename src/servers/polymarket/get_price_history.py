"""
Tool for fetching historical price data from Polymarket's CLOB API.

Uses the prices-history endpoint to retrieve time-series price data for markets.
"""

import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from src.mcp.discovery import register_tool
from .schema import POLYMARKET_API_BASE_URL

logger = logging.getLogger(__name__)


def fetch_price_history_from_polymarket(
    market_id: str,
    start_ts: int,
    end_ts: int,
    interval: str = "1d",
    fidelity: int = 1440
) -> List[Dict[str, Any]]:
    """
    Fetch historical price data from Polymarket CLOB API.
    
    Calls the prices-history endpoint which returns time-series price data.
    
    Args:
        market_id: Market/token ID (CLOB token ID)
        start_ts: Start time (Unix timestamp in seconds)
        end_ts: End time (Unix timestamp in seconds)
        interval: Time interval ('1m', '1h', '6h', '1d', '1w', 'max')
        fidelity: Resolution in minutes (default: 1440 = 1 day)
        
    Returns:
        List of price points: [{"t": timestamp, "p": price}, ...]
        
    Raises:
        Exception: If API call fails
    """
    endpoint = f"{POLYMARKET_API_BASE_URL}/prices-history"
    
    # Build query parameters
    params = {
        'market': market_id,
        'startTs': str(start_ts),
        'endTs': str(end_ts),
        'interval': interval,
        'fidelity': str(fidelity)
    }
    
    query_string = urllib.parse.urlencode(params)
    url = f"{endpoint}?{query_string}"
    
    logger.info(f"Fetching price history from Polymarket API: {url}")
    
    # Make request with browser-like headers
    req = urllib.request.Request(url, method='GET')
    req.add_header('Accept', 'application/json')
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    req.add_header('Accept-Language', 'en-US,en;q=0.9')
    req.add_header('Connection', 'keep-alive')
    req.add_header('Referer', 'https://polymarket.com/')
    req.add_header('Origin', 'https://polymarket.com')
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            # API returns: {"history": [{"t": timestamp, "p": price}, ...]}
            history = result.get('history', [])
            
            logger.info(f"Received {len(history)} price points from API")
            return history
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else "No error details"
        logger.error(f"Polymarket API HTTP error: {e.code} - {error_body}")
        
        # Common error codes:
        # 404: Market not found or no price history available
        # 429: Rate limit exceeded
        # 500: Server error
        if e.code == 404:
            logger.warning(f"No price history found for market {market_id}")
            return []
        raise
        
    except urllib.error.URLError as e:
        logger.error(f"Polymarket API connection error: {e.reason}")
        raise


def find_price_at_target_time(
    history: List[Dict[str, Any]],
    target_timestamp: int
) -> Optional[float]:
    """
    Find the price closest to a target timestamp.
    
    Uses weighted average of nearby points for better accuracy.
    
    Args:
        history: List of price points from API
        target_timestamp: Target time (Unix timestamp)
        
    Returns:
        Price at target time, or None if no data available
    """
    if not history:
        return None
    
    # Convert to list of tuples for easier sorting
    points = [(int(p['t']), float(p['p'])) for p in history if 't' in p and 'p' in p]
    
    if not points:
        return None
    
    # Sort by time
    points.sort(key=lambda x: x[0])
    
    # Find closest points
    # Take up to 5 points before and after target for weighted average
    before = [p for p in points if p[0] <= target_timestamp][-5:]
    after = [p for p in points if p[0] > target_timestamp][:5]
    
    # Combine and weight by inverse time distance
    relevant = before + after
    
    if not relevant:
        # No points near target, use closest available
        closest = min(points, key=lambda p: abs(p[0] - target_timestamp))
        return closest[1]
    
    # Weighted average with exponential decay
    # Half-life of 1 hour (3600 seconds)
    weighted_sum = 0.0
    total_weight = 0.0
    
    for timestamp, price in relevant:
        time_diff = abs(timestamp - target_timestamp)
        weight = 2 ** (-time_diff / 3600.0)
        weighted_sum += price * weight
        total_weight += weight
    
    if total_weight == 0:
        return None
    
    return weighted_sum / total_weight


@register_tool(
    name="get_market_price_history",
    description="Get historical price data for a Polymarket market on a specific date"
)
def get_market_price_history(
    market_id: str,
    date: str,  # ISO format: "2024-11-01"
    date_range_hours: int = 12
) -> Dict[str, Any]:
    """
    Fetch historical price data for a market at a specific date.
    
    Uses Polymarket's prices-history API endpoint to get time-series price data,
    then finds the price closest to the target date.
    
    Args:
        market_id: Polymarket market/token ID (CLOB token ID)
        date: Target date in ISO format (YYYY-MM-DD)
        date_range_hours: Number of hours to search around the target date (default: 12)
        
    Returns:
        {
            "market_id": "...",
            "date": "2024-11-01",
            "price": {
                "yes": 0.65,
                "no": 0.35
            },
            "data_points": 24,
            "data_source": "polymarket_clob_api",
            "note": "..."
        }
        
    Raises:
        ValueError: If invalid parameters
        Exception: If API call fails
    """
    logger.info(f"get_market_price_history called: market_id={market_id}, date={date}")
    
    # Validate parameters
    if not market_id:
        raise ValueError("market_id cannot be empty")
    
    if not date:
        raise ValueError("date cannot be empty")
    
    try:
        # Parse target date
        target_date = datetime.fromisoformat(date.replace('Z', '+00:00'))
        target_timestamp = int(target_date.timestamp())
        
        # Calculate time range
        start_timestamp = target_timestamp - (date_range_hours * 3600)
        end_timestamp = target_timestamp + (date_range_hours * 3600)
        
        logger.info(f"Querying price history from {start_timestamp} to {end_timestamp}")
        
        # Fetch price history from Polymarket API
        try:
            # Use 1-hour fidelity for better granularity around target time
            history = fetch_price_history_from_polymarket(
                market_id=market_id,
                start_ts=start_timestamp,
                end_ts=end_timestamp,
                interval="1h",
                fidelity=60,  # 60 minutes = 1 hour resolution
            )

            if history:
                # Find price at target time
                yes_price = find_price_at_target_time(history, target_timestamp)

                # Fallback: if we have history but couldn't compute a weighted price,
                # use the last available price point from the API response.
                if yes_price is None:
                    try:
                        last_point = history[-1]
                        # Polymarket history uses "p" for price; keep flexible just in case.
                        raw_price = (
                            last_point.get("p")
                            if isinstance(last_point, dict)
                            else None
                        )
                        if raw_price is not None:
                            yes_price = float(raw_price)
                            logger.info(
                                "Fallback to last available price for %s on %s: %s",
                                market_id,
                                date,
                                yes_price,
                            )
                    except Exception as fallback_exc:  # pragma: no cover - defensive
                        logger.warning(
                            "Failed fallback price extraction for %s on %s: %s",
                            market_id,
                            date,
                            fallback_exc,
                        )

                if yes_price is not None:
                    # For binary markets, no_price = 1 - yes_price
                    no_price = 1.0 - yes_price

                    return {
                        "market_id": market_id,
                        "date": date,
                        "price": {
                            "yes": round(yes_price, 4),
                            "no": round(no_price, 4),
                        },
                        "data_points": len(history),
                        "data_source": "polymarket_clob_api",
                        "note": f"Historical price from {len(history)} data points (Polymarket CLOB API)",
                    }

        except Exception as e:
            logger.warning(f"Failed to fetch price history from Polymarket API: {e}")

        # No data available (either API returned no history or we couldn't derive a price)
        logger.warning(f"No historical data found for {market_id} on {date}")
        return {
            "market_id": market_id,
            "date": date,
            "price": {
                "yes": None,
                "no": None,
            },
            "data_points": len(history) if "history" in locals() else 0,
            "data_source": "polymarket_clob_api" if "history" in locals() and history else "unavailable",
            "note": "Historical price data not available or could not be derived from API response.",
        }
        
    except Exception as e:
        logger.error(f"get_market_price_history failed: {e}")
        raise


@register_tool(
    name="get_market_price_range",
    description="Get price data over a date range for trend analysis"
)
def get_market_price_range(
    market_id: str,
    start_date: str,
    end_date: str,
    interval_days: int = 1
) -> Dict[str, Any]:
    """
    Fetch historical prices over a date range.
    
    Args:
        market_id: Market/token ID
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        interval_days: Days between data points (default: 1)
        
    Returns:
        {
            "market_id": "...",
            "start_date": "...",
            "end_date": "...",
            "prices": [
                {"date": "2024-10-01", "yes": 0.60, "no": 0.40},
                {"date": "2024-10-02", "yes": 0.62, "no": 0.38},
                ...
            ],
            "price_change": {
                "yes": +0.10,
                "no": -0.10
            }
        }
    """
    logger.info(f"get_market_price_range called: {market_id} from {start_date} to {end_date}")
    
    # Parse dates
    start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
    end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
    
    # Validate date range
    if start > end:
        raise ValueError("start_date must be before end_date")
    
    # Calculate total range and fetch all data at once for efficiency
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    
    try:
        # Fetch entire range from API
        # Use daily fidelity for efficiency
        history = fetch_price_history_from_polymarket(
            market_id=market_id,
            start_ts=start_ts,
            end_ts=end_ts,
            interval="max",
            fidelity=1440  # 1 day resolution
        )
        
        if not history:
            logger.warning(f"No price history available for {market_id}")
            return {
                "market_id": market_id,
                "start_date": start_date,
                "end_date": end_date,
                "prices": [],
                "data_points": 0,
                "price_change": {"yes": 0, "no": 0},
                "note": "No historical price data available for this date range"
            }
        
        # Sample at requested interval
        prices = []
        current = start
        
        while current <= end:
            current_ts = int(current.timestamp())
            price_value = find_price_at_target_time(history, current_ts)
            
            if price_value is not None:
                prices.append({
                    "date": current.strftime('%Y-%m-%d'),
                    "yes": round(price_value, 4),
                    "no": round(1.0 - price_value, 4)
                })
            
            # Move to next interval
            current = current + timedelta(days=interval_days)
        
        # Calculate price change
        price_change = {"yes": 0, "no": 0}
        if len(prices) >= 2:
            price_change = {
                "yes": round(prices[-1]['yes'] - prices[0]['yes'], 4),
                "no": round(prices[-1]['no'] - prices[0]['no'], 4)
            }
        
        return {
            "market_id": market_id,
            "start_date": start_date,
            "end_date": end_date,
            "prices": prices,
            "data_points": len(prices),
            "price_change": price_change,
            "note": f"Historical price trend from Polymarket CLOB API ({len(history)} raw data points)"
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch price range: {e}")
        raise
