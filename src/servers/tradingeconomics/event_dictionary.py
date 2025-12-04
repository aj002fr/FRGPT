"""
Event dictionary utilities for Trading Economics.

Provides fast lookups for event names without API calls.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any

# Path to the event dictionary
_EVENT_DICT_PATH = Path(__file__).parent.parent.parent.parent / "config" / "event_dictionary.json"

# Cached dictionary
_EVENT_DICT: Optional[Dict[str, Any]] = None


def load_event_dictionary() -> Dict[str, Any]:
    """Load event dictionary from JSON file."""
    global _EVENT_DICT
    
    if _EVENT_DICT is not None:
        return _EVENT_DICT
    
    if not _EVENT_DICT_PATH.exists():
        return {
            "by_country": {},
            "by_event": {},
            "metadata": {"error": "Dictionary not built yet. Run scripts/build_event_dictionary.py"}
        }
    
    with open(_EVENT_DICT_PATH, 'r', encoding='utf-8') as f:
        _EVENT_DICT = json.load(f)
    
    return _EVENT_DICT


def search_event_name(keyword: str, country: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Search for event names matching a keyword.
    
    Args:
        keyword: Search keyword (case-insensitive)
        country: Optional country filter
        limit: Maximum results
        
    Returns:
        List of matching events with details
    """
    event_dict = load_event_dictionary()
    keyword_lower = keyword.lower()
    
    results = []
    
    if country:
        # Search within specific country
        country_lower = country.lower()
        country_events = event_dict["by_country"].get(country_lower, {})
        
        for event_name, details in country_events.items():
            if keyword_lower in event_name.lower():
                results.append({
                    "event_name": event_name,
                    "country": country_lower,
                    **details
                })
                
                if len(results) >= limit:
                    break
    else:
        # Search across all events
        for event_name, countries in event_dict["by_event"].items():
            if keyword_lower in event_name.lower():
                # Get details from first country
                first_country = countries[0] if countries else ""
                details = event_dict["by_country"].get(first_country, {}).get(event_name, {})
                
                results.append({
                    "event_name": event_name,
                    "countries": countries,
                    "country_count": len(countries),
                    **details
                })
                
                if len(results) >= limit:
                    break
    
    return results


def get_events_for_country(country: str) -> Dict[str, Dict[str, Any]]:
    """
    Get all events for a specific country.
    
    Args:
        country: Country code or name
        
    Returns:
        Dictionary of event names to details
    """
    event_dict = load_event_dictionary()
    country_lower = country.lower()
    return event_dict["by_country"].get(country_lower, {})


def get_countries_for_event(event_name: str) -> List[str]:
    """
    Get all countries that have a specific event.
    
    Args:
        event_name: Exact event name
        
    Returns:
        List of country names
    """
    event_dict = load_event_dictionary()
    return event_dict["by_event"].get(event_name, [])


def validate_event_name(event_name: str, country: Optional[str] = None) -> bool:
    """
    Check if an event name exists in the dictionary.
    
    Args:
        event_name: Event name to validate
        country: Optional country to check
        
    Returns:
        True if event exists
    """
    event_dict = load_event_dictionary()
    
    if country:
        country_lower = country.lower()
        country_events = event_dict["by_country"].get(country_lower, {})
        return event_name in country_events
    else:
        return event_name in event_dict["by_event"]


def get_event_details(event_name: str, country: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get details for a specific event.
    
    Args:
        event_name: Event name
        country: Country (if not provided, returns from first available country)
        
    Returns:
        Event details or None
    """
    event_dict = load_event_dictionary()
    
    if country:
        country_lower = country.lower()
        country_events = event_dict["by_country"].get(country_lower, {})
        return country_events.get(event_name)
    else:
        # Return from first country that has it
        countries = event_dict["by_event"].get(event_name, [])
        if countries:
            first_country = countries[0]
            return event_dict["by_country"].get(first_country, {}).get(event_name)
        return None


def fuzzy_find_event(partial_name: str, country: Optional[str] = None) -> List[str]:
    """
    Find all matching event names for a partial/fuzzy input.
    
    Args:
        partial_name: Partial or fuzzy event name (e.g., "nfp", "non farm", "jolts")
        country: Optional country filter
        
    Returns:
        List of matching event names (empty list if none found)
    """
    matches = search_event_name(partial_name, country=country, limit=10)
    
    if not matches:
        return []
    
    # Return exact match first if found
    partial_lower = partial_name.lower()
    exact_match = None
    for match in matches:
        if match["event_name"].lower() == partial_lower:
            exact_match = match["event_name"]
            break
    
    # Extract all event names
    event_names = [m["event_name"] for m in matches]
    
    # If exact match found, put it first
    if exact_match and exact_match in event_names:
        event_names.remove(exact_match)
        event_names.insert(0, exact_match)
    
    return event_names

