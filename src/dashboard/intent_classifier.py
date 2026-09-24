"""
Intent Classifier - Simple pattern matching for PRESET dashboard templates.

This is NOT for duplicating orchestrator logic. The orchestrator already
handles query understanding via Planner1/Planner2.

This module is only used for:
1. Quick preset dashboard selection (e.g., /pre_event command)
2. Fallback when running without orchestrator
3. Validating that a query matches expected patterns
"""

import re
from dataclasses import dataclass
from typing import Optional, List


# =============================================================================
# SIMPLE PATTERN MATCHING FOR PRESETS
# =============================================================================

@dataclass
class PresetMatch:
    """Result of matching a query to a preset dashboard."""
    preset_type: Optional[str]  # pre_event, post_event, adhoc, None
    event_type: Optional[str]   # NFP, CPI, etc.
    confidence: float


# Event patterns for quick matching
EVENT_KEYWORDS = {
    "NFP": ["nfp", "payroll", "jobs report", "employment"],
    "CPI": ["cpi", "inflation", "consumer price"],
    "PPI": ["ppi", "producer price"],
    "JOLTS": ["jolts", "job openings"],
    "ISM": ["ism", "pmi", "manufacturing"],
    "FOMC": ["fomc", "fed", "rate decision"],
}


def match_preset(query: str) -> PresetMatch:
    """
    Quick pattern match to see if query matches a preset dashboard.
    
    Used for commands like /pre_event NFP or direct dashboard requests.
    The orchestrator handles complex queries - this is just for simple presets.
    """
    query_lower = query.lower().strip()
    
    # Check for pre-event patterns
    if any(p in query_lower for p in ["pre-event", "preevent", "pre event", "before event"]):
        event_type = _extract_event_type(query_lower)
        return PresetMatch(preset_type="pre_event", event_type=event_type, confidence=0.9)
    
    # Check for post-event patterns
    if any(p in query_lower for p in ["post-event", "postevent", "post event", "after event"]):
        event_type = _extract_event_type(query_lower)
        return PresetMatch(preset_type="post_event", event_type=event_type, confidence=0.9)
    
    # Check for adhoc patterns
    if any(p in query_lower for p in ["adhoc", "ad-hoc", "ad hoc", "custom analysis"]):
        return PresetMatch(preset_type="adhoc", event_type=None, confidence=0.8)
    
    # No preset match - let orchestrator handle it
    return PresetMatch(preset_type=None, event_type=_extract_event_type(query_lower), confidence=0.0)


def _extract_event_type(query_lower: str) -> Optional[str]:
    """Extract event type from query using simple keyword matching."""
    for event_type, keywords in EVENT_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            return event_type
    return None


def get_preset_dashboard_type(query: str) -> Optional[str]:
    """
    Simple helper to get preset type from query.
    Returns None if query should go through full orchestrator.
    """
    match = match_preset(query)
    return match.preset_type if match.confidence > 0.5 else None
