"""
Analytics Server - Statistical analysis and visualization tools.

Provides MCP tools for:
- Descriptive statistics (mean, median, std dev, percentiles)
- Distribution analysis (skewness, kurtosis, outliers)
- Correlation and comparison
- SVG plot generation (histograms, line charts, scatter plots, bar charts)
- PNG plot generation (candlestick charts for OHLC data)

SVG tools use pure Python (stdlib only).
PNG tools require plotly and kaleido.

Plugins:
- Additional tools can be added via the plugins/ directory
- See plugins/README.md for documentation
"""

# Import tools to register them with the MCP discovery system
from .statistics import (
    compute_statistics,
    compute_percentile_rank,
    compare_distributions,
    compute_correlation,
)

from .plotting import (
    generate_histogram,
    generate_line_chart,
    generate_scatter_plot,
    generate_bar_chart,
    generate_candlestick_chart,
    generate_multi_candlestick_charts,
)

from .nam_nbm_analytics import (
    find_price_stretches,
    find_primary_secondary_stretches,
)

# Import plugins to trigger auto-discovery
from . import plugins

__all__ = [
    # Statistics tools
    "compute_statistics",
    "compute_percentile_rank",
    "compare_distributions",
    "compute_correlation",
    # SVG Plotting tools (no dependencies)
    "generate_histogram",
    "generate_line_chart",
    "generate_scatter_plot",
    "generate_bar_chart",
    # PNG Plotting tools (requires plotly, kaleido)
    "generate_candlestick_chart",
    "generate_multi_candlestick_charts",
    # NAM/NBM Analytics (stretch detection)
    "find_price_stretches",
    "find_primary_secondary_stretches",
    # Plugins module
    "plugins",
]


