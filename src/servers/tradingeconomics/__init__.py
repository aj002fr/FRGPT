"""
Trading Economics MCP Server.

Provides tools for fetching and querying economic calendar data.
"""

from src.servers.tradingeconomics.fetch_calendar import fetch_economic_calendar
from src.servers.tradingeconomics.query_events import (
    query_event_history,
    search_events,
    find_correlated_events,
)
from src.servers.tradingeconomics.search_event_names import search_event_names
from src.servers.tradingeconomics.websocket_client import (
    start_event_stream,
    stop_event_stream,
    get_stream_status,
    get_live_events,
)

__all__ = [
    "fetch_economic_calendar",
    "query_event_history",
    "search_events",
    "find_correlated_events",
    "search_event_names",
    "start_event_stream",
    "stop_event_stream",
    "get_stream_status",
    "get_live_events",
]
