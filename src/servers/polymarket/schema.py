"""
Polymarket tool schema and constants.

Defines API endpoints, result limits, market status constants, and helper functions
for formatting and validating Polymarket API responses.
"""

from typing import Dict, Any, List, Optional

# API Endpoints
POLYMARKET_API_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# Result limits
MAX_POLYMARKET_RESULTS = 50
DEFAULT_POLYMARKET_RESULTS = 10

# Database
HISTORY_TABLE = "prediction_queries"

# Market status constants
MARKET_STATUS_ACTIVE = "active"
MARKET_STATUS_CLOSED = "closed"
MARKET_STATUS_RESOLVED = "resolved"


def format_market_result(market: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a Polymarket API market response into standardized structure.
    
    Args:
        market: Raw market data from Polymarket API
        
    Returns:
        Formatted market dictionary with standardized fields
    """
    import json
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Extract basic info
    market_id = market.get('conditionId', market.get('id', ''))
    
    # For price history, we need the clobTokenIds (these are the actual token IDs)
    clob_token_ids = market.get('clobTokenIds', [])
    
    # Handle JSON string format
    if isinstance(clob_token_ids, str):
        try:
            clob_token_ids = json.loads(clob_token_ids)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse clobTokenIds string: {clob_token_ids[:100]}")
            clob_token_ids = []
    
    # Ensure it's a list
    if not isinstance(clob_token_ids, list):
        clob_token_ids = []
    
    title = market.get('question', market.get('title', ''))
    description = market.get('description', '')
    slug = market.get('slug', '')
    
    # Extract outcomes - handle both array and JSON string formats
    outcomes = market.get('outcomes', ['Yes', 'No'])
    if isinstance(outcomes, str):
        # Parse JSON string like "[\"Yes\", \"No\"]"
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse outcomes string: {outcomes}")
            outcomes = ['Yes', 'No']
    
    if not outcomes or not isinstance(outcomes, list):
        outcomes = ['Yes', 'No']
    
    # Extract prices - handle multiple formats
    prices = {}
    outcome_prices = market.get('outcomePrices', [])
    
    if isinstance(outcome_prices, str):
        # Parse JSON string
        try:
            outcome_prices = json.loads(outcome_prices)
        except json.JSONDecodeError:
            outcome_prices = []
    
    if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
        # outcomePrices is a list of price strings
        try:
            prices = {}
            for i, outcome in enumerate(outcomes[:len(outcome_prices)]):
                price_val = outcome_prices[i]
                if isinstance(price_val, str):
                    price_val = float(price_val) if price_val else 0.5
                prices[outcome] = float(price_val) if price_val is not None else 0.5
        except (ValueError, IndexError, TypeError) as e:
            logger.warning(f"Failed to parse prices: {e}")
            prices = {outcome: 0.5 for outcome in outcomes}
    else:
        # Fallback to default 50/50
        prices = {outcome: 0.5 for outcome in outcomes}
    
    # Extract volume and liquidity
    volume = market.get('volume', 0)
    liquidity = market.get('liquidity', 0)
    
    # Convert to numbers if they're strings
    try:
        volume = float(volume) if volume else 0
    except (ValueError, TypeError):
        volume = 0
        
    try:
        liquidity = float(liquidity) if liquidity else 0
    except (ValueError, TypeError):
        liquidity = 0
    
    # Extract status
    active = market.get('active', True)
    closed = market.get('closed', False)
    if closed:
        status = MARKET_STATUS_CLOSED
    elif active:
        status = MARKET_STATUS_ACTIVE
    else:
        status = MARKET_STATUS_RESOLVED
    
    # Build URL:
    # Prefer full URL from API payload if present; otherwise fall back to slug-based path.
    raw_url = market.get('url', '')
    if isinstance(raw_url, str) and raw_url.startswith('http'):
        url = raw_url
    else:
        # Current Polymarket front-end uses /market/{slug} for individual markets/events.
        url = f"https://polymarket.com/market/{slug}" if slug else ""
    
    # Extract creation and close times
    created_at = market.get('createdAt', market.get('created_at', ''))
    close_time = market.get('endDate', market.get('end_date', ''))
    
    return {
        'market_id': market_id,
        'clob_token_ids': clob_token_ids,  # For price history queries
        'title': title,
        'description': description,
        'outcomes': outcomes,
        'prices': prices,
        'volume': volume,
        'liquidity': liquidity,
        'status': status,
        'url': url,
        'slug': slug,
        'created_at': created_at,
        'close_time': close_time
    }


def parse_probability_from_price(price: float) -> float:
    """
    Parse probability from Polymarket price.
    
    Polymarket prices are already probabilities (0-1 range).
    
    Args:
        price: Price value
        
    Returns:
        Probability as float (0-1)
    """
    try:
        prob = float(price)
        # Clamp to valid range
        return max(0.0, min(1.0, prob))
    except (ValueError, TypeError):
        return 0.5  # Default to 50/50


def calculate_avg_probability(results: List[Dict[str, Any]]) -> Optional[float]:
    """
    Calculate average probability across markets.
    
    Args:
        results: List of market dictionaries
        
    Returns:
        Average probability or None if no valid data
    """
    if not results:
        return None
    
    total = 0.0
    count = 0
    
    for result in results:
        prices = result.get('prices', {})
        if prices:
            # Take first outcome (usually "Yes")
            first_outcome = next(iter(prices.keys()), None)
            if first_outcome:
                prob = parse_probability_from_price(prices[first_outcome])
                total += prob
                count += 1
    
    return round(total / count, 4) if count > 0 else None


def calculate_total_volume(results: List[Dict[str, Any]]) -> int:
    """
    Calculate total volume across markets.
    
    Args:
        results: List of market dictionaries
        
    Returns:
        Total volume as integer
    """
    if not results:
        return 0
    
    total = 0
    for result in results:
        volume = result.get('volume', 0)
        try:
            total += int(float(volume))
        except (ValueError, TypeError):
            continue
    
    return total


def validate_market_url(url: str, timeout: int = 5) -> bool:
    """
    Validate that a market URL actually exists and returns 200.
    
    Args:
        url: Market URL to validate
        timeout: Request timeout in seconds
        
    Returns:
        True if URL is valid and accessible, False otherwise
    """
    import urllib.request
    import urllib.error
    import logging
    
    logger = logging.getLogger(__name__)
    
    if not url or not url.startswith('http'):
        return False
    
    try:
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
            
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        logger.debug(f"URL validation failed for {url}: {e}")
        return False


def market_exists_on_date(created_at: str, target_date: str) -> bool:
    """
    Check if a market existed on a specific date.
    
    Args:
        created_at: Market creation date (ISO format or timestamp)
        target_date: Target date to check (ISO format YYYY-MM-DD)
        
    Returns:
        True if market was created before or on target_date, False otherwise
    """
    from datetime import datetime, timezone
    import logging
    
    logger = logging.getLogger(__name__)
    
    if not created_at:
        # If no creation date, assume it might not have existed
        logger.warning("No creation date provided for market")
        return False
    
    try:
        # Parse creation date
        if isinstance(created_at, (int, float)):
            # Unix timestamp
            created_dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
        else:
            # ISO string
            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        
        # Parse target date
        target_dt = datetime.fromisoformat(target_date.replace('Z', '+00:00'))
        
        # Market must have been created before or on the target date
        return created_dt.date() <= target_dt.date()
        
    except (ValueError, TypeError, AttributeError) as e:
        logger.warning(f"Failed to parse dates: created_at={created_at}, target_date={target_date}, error={e}")
        return False


def get_token_id_for_price_history(market: Dict[str, Any], outcome_index: int = 0) -> Optional[str]:
    """
    Get the correct token ID for querying price history.
    
    Polymarket's prices-history endpoint uses CLOB token IDs, not condition IDs.
    
    Args:
        market: Market dictionary with clob_token_ids
        outcome_index: Outcome index (0 for first outcome, usually "Yes")
        
    Returns:
        Token ID string or None if not available
    """
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Try to get from clobTokenIds array
    clob_token_ids = market.get('clob_token_ids', [])
    
    if isinstance(clob_token_ids, list) and len(clob_token_ids) > outcome_index:
        token_id = clob_token_ids[outcome_index]
        if token_id:
            return str(token_id)
    
    # Fallback to trying the market_id (condition_id)
    # Note: This might not work for price history, but we try it anyway
    market_id = market.get('market_id')
    if market_id:
        logger.warning(f"Using condition ID as fallback for price history: {market_id}")
        return str(market_id)
    
    logger.error(f"Could not find token ID for market: {market.get('title', 'Unknown')}")
    return None
