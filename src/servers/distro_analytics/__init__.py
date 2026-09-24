"""
Distribution Analytics Server - Distribution analysis and visualization tools.

Provides MCP tools for:
- Returns distribution analysis (session-based)
- Volatility distribution analysis (High-Low)
- Probability matrices for price movements
- Event-specific distribution analysis
- Surprise analysis (correlation and price distribution by surprise bucket)

All tools use core_plotting for standardized PNG output.

NOTE: This server requires optional dependencies (pandas, matplotlib, scipy).
Install with: pip install pandas numpy scipy matplotlib plotly kaleido
"""

# Import tools to register them with the MCP discovery system
from .returns_distribution import compute_returns_distribution
from .volatility_distribution import compute_volatility_distribution
from .probability_matrix import compute_probability_matrix
from .event_distribution import compute_event_distribution, plot_log_bps_distribution
from .surprise_analysis import compute_surprise_correlation, compute_surprise_price_distribution

__all__ = [
    "compute_returns_distribution",
    "compute_volatility_distribution", 
    "compute_probability_matrix",
    "compute_event_distribution",
    "plot_log_bps_distribution",
    "compute_surprise_correlation",
    "compute_surprise_price_distribution",
]
