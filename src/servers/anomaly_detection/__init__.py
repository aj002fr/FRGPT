"""
Anomaly Detection Server - Tools for detecting and annotating unusual market moves.

Provides MCP tools for:
- Detecting anomalous price moves (large range, large move, intraday spikes)
- Matching anomalies to scheduled economic events
- Searching Perplexity for news on unexplained moves
- Storing and querying market anomaly notes

Uses hybrid approach: internal data first, Perplexity fallback for unexplained moves.
"""

from .detect import (
    detect_anomalies,
    calculate_daily_metrics,
)

from .storage import (
    store_anomaly_note,
    query_anomaly_notes,
    delete_anomaly_notes,
)

from .perplexity_search import (
    search_market_news,
)

from .annotate import (
    annotate_date_range,
    explain_move,
)

__all__ = [
    # Detection tools
    "detect_anomalies",
    "calculate_daily_metrics",
    # Storage tools
    "store_anomaly_note",
    "query_anomaly_notes",
    "delete_anomaly_notes",
    # Search tools
    "search_market_news",
    # High-level tools
    "annotate_date_range",
    "explain_move",
]






