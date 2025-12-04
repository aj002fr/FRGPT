"""
Search for event names in Trading Economics API.

Helps discover the exact event names for use in queries.
Uses cached event dictionary when available for fast lookups.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

from src.mcp.discovery import register_tool
from src.servers.tradingeconomics.schema import (
    TE_API_BASE_URL,
    TE_FORMAT_JSON,
    build_api_url,
    normalize_country,
)
from src.servers.tradingeconomics.event_dictionary import (
    search_event_name as dict_search,
    load_event_dictionary,
)

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    """Load Trading Economics API key from config."""
    from config.settings import get_api_key
    return get_api_key("TRADING_ECONOMICS_API_KEY")


@register_tool(
    name="search_event_names",
    description="Search for exact event names from Trading Economics. "
                "Uses cached dictionary for fast lookups, falls back to API if needed.",
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Keyword to search for in event names (case-insensitive partial match)."
            },
            "country": {
                "type": "string",
                "description": "Optional country filter (e.g., 'US', 'united states')."
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of unique event names to return. Default: 50."
            }
        },
        "required": ["keyword"]
    }
)
def search_event_names(
    keyword: str,
    country: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Search for event names matching a keyword.
    
    First tries cached event dictionary (fast), then falls back to API if needed.
    
    Args:
        keyword: Search keyword (e.g., 'JOLTS', 'CPI')
        country: Country filter (optional)
        limit: Maximum results
        
    Returns:
        Dictionary with matching event names and sample data
    """
    logger.info(f"Searching for events matching: {keyword}")
    
    # Try dictionary first (fast, no API call)
    try:
        dict_results = dict_search(keyword, country=country, limit=limit)
        if dict_results:
            logger.info(f"Found {len(dict_results)} matches in cached dictionary")
            return {
                "success": True,
                "keyword": keyword,
                "country_filter": country,
                "matches": dict_results,
                "count": len(dict_results),
                "source": "cached_dictionary",
            }
    except Exception as e:
        logger.warning(f"Dictionary search failed, falling back to API: {e}")
    
    # Fall back to API search
    logger.info("Searching via API (dictionary not available or empty)")
    
    try:
        api_key = _get_api_key()
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Failed to load API key: {e}")
        return {
            "success": False,
            "error": f"Failed to load API key: {e}",
            "matches": [],
            "count": 0,
        }
    
    # Normalize country if provided
    normalized_country = normalize_country(country) if country else None
    
    # Build endpoint - use country-specific or general calendar
    if normalized_country:
        endpoint = f"/calendar/country/{normalized_country}"
    else:
        endpoint = "/calendar"
    
    url = build_api_url(endpoint, api_key)
    
    logger.info(f"Fetching from: {endpoint}")
    
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "MarketDataPuller/1.0")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            if isinstance(data, list):
                # Extract unique event names matching keyword
                keyword_lower = keyword.lower()
                seen_names = set()
                matches = []
                
                for event in data:
                    event_name = event.get("Event", "")
                    
                    if not event_name or event_name in seen_names:
                        continue
                    
                    if keyword_lower in event_name.lower():
                        seen_names.add(event_name)
                        matches.append({
                            "event_name": event_name,
                            "country": event.get("Country", ""),
                            "category": event.get("Category", ""),
                            "importance": event.get("Importance", ""),
                            "ticker": event.get("Ticker", ""),
                            "sample_date": event.get("Date", "")[:10] if event.get("Date") else "",
                        })
                        
                        if len(matches) >= limit:
                            break
                
                logger.info(f"Found {len(matches)} matching events via API")
                
                return {
                    "success": True,
                    "keyword": keyword,
                    "country_filter": country,
                    "matches": matches,
                    "count": len(matches),
                    "source": "api",
                }
                    
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except:
            pass
        logger.error(f"HTTP error: {e.code} - {e.reason}. {error_body}")
        return {
            "success": False,
            "error": f"HTTP {e.code}: {e.reason}",
            "matches": [],
            "count": 0,
        }
    except Exception as e:
        logger.error(f"Error searching events: {e}")
        return {
            "success": False,
            "error": str(e),
            "matches": [],
            "count": 0,
        }

