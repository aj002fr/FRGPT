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
    """Load event dictionary from JSON file.
    
    Returns dictionary with event names as keys and aliases (if any) as values.
    """
    global _EVENT_DICT
    
    if _EVENT_DICT is not None:
        return _EVENT_DICT
    
    if not _EVENT_DICT_PATH.exists():
        return {}
    
    with open(_EVENT_DICT_PATH, 'r', encoding='utf-8') as f:
        _EVENT_DICT = json.load(f)
    
    return _EVENT_DICT


def search_event_name(keyword: str, country: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Search for event names matching a keyword.
    
    Args:
        keyword: Search keyword (case-insensitive)
        country: Optional country filter (no longer used with simplified dictionary)
        limit: Maximum results
        
    Returns:
        List of matching events with details
    """
    event_dict = load_event_dictionary()
    keyword_lower = keyword.lower()
    
    results = []
    
    # Search across all events (country filter not applicable with simplified dict)
    for event_name, details in event_dict.items():
        if keyword_lower in event_name.lower():
            results.append({
                "event_name": event_name,
                **details
            })
            
            if len(results) >= limit:
                break
    
    return results


def get_events_for_country(country: str) -> Dict[str, Dict[str, Any]]:
    """
    Get all events for a specific country.
    
    NOTE: With simplified dictionary, country filtering is no longer available.
    Returns all events.
    
    Args:
        country: Country code or name (no longer used)
        
    Returns:
        Dictionary of event names to details
    """
    event_dict = load_event_dictionary()
    return event_dict


def get_countries_for_event(event_name: str) -> List[str]:
    """
    Get all countries that have a specific event.
    
    NOTE: With simplified dictionary, country information is no longer available.
    Returns empty list.
    
    Args:
        event_name: Exact event name
        
    Returns:
        List of country names (always empty with simplified dict)
    """
    return []


def validate_event_name(event_name: str, country: Optional[str] = None) -> bool:
    """
    Check if an event name exists in the dictionary.
    
    Args:
        event_name: Event name to validate
        country: Optional country to check (no longer used)
        
    Returns:
        True if event exists
    """
    event_dict = load_event_dictionary()
    return event_name in event_dict


def get_event_details(event_name: str, country: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get details for a specific event.
    
    Args:
        event_name: Event name
        country: Country (no longer used)
        
    Returns:
        Event details (aliases if present) or None
    """
    event_dict = load_event_dictionary()
    return event_dict.get(event_name)


def fuzzy_find_event(partial_name: str, country: Optional[str] = None) -> List[str]:
    """
    Find all matching event names for a partial/fuzzy input.
    
    Also checks aliases if present in event details.
    
    Args:
        partial_name: Partial or fuzzy event name (e.g., "nfp", "non farm", "jolts")
        country: Optional country filter (no longer used)
        
    Returns:
        List of matching event names (empty list if none found)
    """
    event_dict = load_event_dictionary()
    partial_lower = partial_name.lower()
    matches = []
    
    # Search by name AND aliases
    for event_name, details in event_dict.items():
        # Check event name
        if partial_lower in event_name.lower():
            matches.append(event_name)
        # Check aliases
        elif details and "aliases" in details:
            for alias in details["aliases"]:
                if partial_lower in alias.lower():
                    matches.append(event_name)
                    break
    
    # Return exact match first if found
    exact_match = None
    for name in matches:
        if name.lower() == partial_lower:
            exact_match = name
            break
    
    # If exact match found, put it first
    if exact_match and exact_match in matches:
        matches.remove(exact_match)
        matches.insert(0, exact_match)
    
    return matches[:10]  # Limit to 10 matches


def get_event_aliases(event_name: str, country: Optional[str] = None) -> List[str]:
    """
    Get aliases for a specific event.
    
    Args:
        event_name: Event name
        country: Country (if not provided, uses first available country)
        
    Returns:
        List of aliases (empty if none defined)
    """
    details = get_event_details(event_name, country)
    if details:
        return details.get("aliases", [])
    return []

