"""
Analytics Server - Statistical analysis and visualization tools.

Provides MCP tools for:
- Descriptive statistics (mean, median, std dev, percentiles)
- Distribution analysis (skewness, kurtosis, outliers)
- Correlation and comparison
- SVG plot generation (histograms, line charts, scatter plots, bar charts)

All implementations use pure Python (stdlib only).
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
)

__all__ = [
    # Statistics tools
    "compute_statistics",
    "compute_percentile_rank",
    "compare_distributions",
    "compute_correlation",
    # Plotting tools
    "generate_histogram",
    "generate_line_chart",
    "generate_scatter_plot",
    "generate_bar_chart",
]

