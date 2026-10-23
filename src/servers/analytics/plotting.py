"""
PNG Plotting tools - Plotly-based implementation.

Generates PNG visualizations using Plotly:
- Histograms (frequency distributions)
- Line charts (time series)
- Scatter plots
- Bar charts
- Candlestick charts (OHLC)

Requires: plotly, kaleido

Also exports core plotting functions for richer visualization.
"""

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import html

from src.mcp.discovery import register_tool
from src.bus.file_bus import ensure_dir, write_atomic
from .schema import (
    get_plots_dir,
    DEFAULT_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_MARGIN,
    DEFAULT_HISTOGRAM_BINS,
    COLOR_SCHEMES,
    validate_numeric_data,
    validate_plot_params,
    format_number,
    is_bond_price,
    decimal_to_32nds,
    format_bond_price,
)

# Re-export core plotting functions for convenience
# These generate PNG plots with matplotlib/plotly
from .core_plotting import (
    plot_distribution,
    plot_timeseries,
    plot_candlestick,
    plot_categorical,
    plot_correlation,
    plot_multi_distribution,
)
from .plot_factory import create_plot, get_available_contexts

logger = logging.getLogger(__name__)


# =============================================================================
# Public API - Core Plotting (PNG output)
# =============================================================================
# The core_plotting module provides PNG-based plotting functions using
# matplotlib/plotly. These are recommended for richer visualizations.
#
# Exported from core_plotting:
#   - plot_distribution(values, title, ...) -> PNG histogram/KDE
#   - plot_timeseries(data, title, ...) -> PNG candlestick/line
#   - plot_categorical(values, labels, ...) -> PNG bar chart  
#   - plot_correlation(x_values, y_values, ...) -> PNG scatter plot
#   - plot_multi_distribution(data_dict, ...) -> PNG grid of distributions
#
# Exported from plot_factory:
#   - create_plot(data, context, ...) -> Auto-detect and create plot
#   - get_available_contexts() -> List of supported contexts
#
# All plotting functions below output PNG via Plotly.


# =============================================================================
# Plotly Theme Helpers
# =============================================================================


def _get_plotly_theme(color_scheme: str) -> Tuple[str, Dict[str, str]]:
    """
    Get Plotly template and color mapping for a color scheme.
    
    Args:
        color_scheme: Color scheme name ("default", "dark", "warm")
        
    Returns:
        (template_name, color_dict)
    """
    colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["default"])
    
    if color_scheme == "dark":
        template = "plotly_dark"
    else:
        template = "plotly_white"
    
    return template, colors


# =============================================================================
# Timestamp Handling Utilities
# =============================================================================


def _is_timestamp_string(val: str) -> bool:
    """Check if a string looks like an ISO timestamp."""
    if not isinstance(val, str):
        return False
    # ISO format patterns: 2024-12-26T00:00:00 or 2024-12-26T00:00:00-05:00
    import re
    return bool(re.match(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', val))


def _convert_timestamps_to_indices(data: List[Any]) -> Tuple[List[float], List[str], bool]:
    """
    Convert timestamp strings to numeric indices and return original labels.
    
    Returns:
        (numeric_indices, original_labels, was_converted)
    """
    if not data or len(data) == 0:
        return [], [], False
    
    # Check if first few items look like timestamps
    sample = data[:min(3, len(data))]
    if all(_is_timestamp_string(str(v)) for v in sample):
        # Convert to sequential indices
        indices = list(range(len(data)))
        labels = [str(v) for v in data]
        return indices, labels, True
    
    return data, [], False


# =============================================================================
# Histogram Generation (PNG via Plotly)
# =============================================================================


@register_tool(
    name="generate_histogram",
    description="Generate a histogram (frequency distribution) as a PNG image. "
                "Visualizes the distribution of numeric values.",
    input_schema={
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of numeric values to plot"
            },
            "title": {
                "type": "string",
                "description": "Chart title. Default: 'Distribution'"
            },
            "x_label": {
                "type": "string",
                "description": "X-axis label. Default: 'Value'"
            },
            "y_label": {
                "type": "string",
                "description": "Y-axis label. Default: 'Frequency'"
            },
            "bins": {
                "type": "integer",
                "description": "Number of bins. Default: 20"
            },
            "color_scheme": {
                "type": "string",
                "enum": ["default", "dark", "warm"],
                "description": "Color scheme. Default: 'default'"
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels. Default: 400"
            },
            "save_to_file": {
                "type": "boolean",
                "description": "Whether to save to file. Default: true"
            }
        },
        "required": ["data"]
    }
)
def generate_histogram(
    data: List[Any],
    title: str = "Distribution",
    x_label: str = "Value",
    y_label: str = "Frequency",
    bins: int = DEFAULT_HISTOGRAM_BINS,
    color_scheme: str = "default",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    save_to_file: bool = True
) -> Dict[str, Any]:
    """
    Generate a histogram PNG using Plotly.
    
    Args:
        data: List of numeric values
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        bins: Number of bins
        color_scheme: Color scheme name
        width: Image width
        height: Image height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with PNG path and metadata
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {"success": False, "error": "Plotly not installed. Run: pip install plotly kaleido"}
    
    logger.info(f"Generating histogram for {len(data)} values, {bins} bins")
    
    # Validate data
    is_valid, error, numeric_data = validate_numeric_data(data)
    if not is_valid:
        return {"success": False, "error": error}
    
    # Validate plot params
    is_valid, error = validate_plot_params("histogram", width, height, bins)
    if not is_valid:
        return {"success": False, "error": error}
    
    try:
        # Get theme
        template, colors = _get_plotly_theme(color_scheme)
        
        # Create histogram
        fig = go.Figure(data=[go.Histogram(
            x=numeric_data,
            nbinsx=bins,
            marker=dict(
                color=colors["primary"],
                line=dict(color=colors["axis"], width=1)
            )
        )])
        
        # Update layout
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor='center'),
            xaxis_title=x_label,
            yaxis_title=y_label,
            template=template,
            width=width,
            height=height,
            margin=dict(l=80, r=40, t=70, b=60),
            bargap=0.05,
        )
        
        # Save to file if requested
        png_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            png_path = plots_dir / f"histogram_{timestamp}.png"
            
            fig.write_image(str(png_path), format='png', scale=2)
            
            logger.info(f"Saved histogram to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path) if png_path else None,
            "metadata": {
                "plot_type": "histogram",
                "title": title,
                "width": width,
                "height": height,
                "data_points": len(numeric_data),
                "bins": bins,
            }
        }
        
    except Exception as e:
        logger.error(f"Error generating histogram: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Line Chart Generation (PNG via Plotly)
# =============================================================================


@register_tool(
    name="generate_line_chart",
    description="Generate a simple line chart as a PNG image. "
                "ONLY use for single-value metrics (close-only, returns, etc.). "
                "DO NOT use for price data with OHLC - use generate_candlestick_chart instead. "
                "Only use this if user explicitly asks for 'line chart' or 'close price only'.",
    input_schema={
        "type": "object",
        "properties": {
            "y_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Y-axis values"
            },
            "x_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "X-axis values (optional, defaults to 0, 1, 2, ...)"
            },
            "title": {
                "type": "string",
                "description": "Chart title. Default: 'Line Chart'"
            },
            "x_label": {
                "type": "string",
                "description": "X-axis label. Default: 'X'"
            },
            "y_label": {
                "type": "string",
                "description": "Y-axis label. Default: 'Y'"
            },
            "show_points": {
                "type": "boolean",
                "description": "Whether to show data points. Default: true"
            },
            "color_scheme": {
                "type": "string",
                "enum": ["default", "dark", "warm"],
                "description": "Color scheme. Default: 'default'"
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels. Default: 400"
            },
            "save_to_file": {
                "type": "boolean",
                "description": "Whether to save to file. Default: true"
            }
        },
        "required": ["y_data"]
    }
)
def generate_line_chart(
    y_data: List[Any],
    x_data: Optional[List[Any]] = None,
    title: str = "Line Chart",
    x_label: str = "X",
    y_label: str = "Y",
    show_points: bool = True,
    color_scheme: str = "default",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    save_to_file: bool = True
) -> Dict[str, Any]:
    """
    Generate a line chart PNG using Plotly.
    
    Args:
        y_data: Y-axis values
        x_data: X-axis values (optional)
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        show_points: Whether to show data points
        color_scheme: Color scheme name
        width: Image width
        height: Image height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with PNG path and metadata
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {"success": False, "error": "Plotly not installed. Run: pip install plotly kaleido"}
    
    logger.info(f"Generating line chart for {len(y_data)} values")
    
    # Validate Y data
    is_valid, error, numeric_y = validate_numeric_data(y_data)
    if not is_valid:
        return {"success": False, "error": f"Y data: {error}"}
    
    # Validate or generate X data
    if x_data is not None:
        # First check if x_data contains timestamps
        converted_x, x_labels, was_converted = _convert_timestamps_to_indices(x_data)
        if was_converted:
            # Use original timestamps for Plotly (it handles them well)
            numeric_x = x_data
            logger.info(f"Using {len(x_data)} timestamps for X axis")
        else:
            is_valid, error, numeric_x = validate_numeric_data(x_data)
            if not is_valid:
                return {"success": False, "error": f"X data: {error}"}
        if len(x_data) != len(numeric_y):
            return {"success": False, "error": "X and Y data must have same length"}
    else:
        numeric_x = list(range(len(numeric_y)))
    
    # Validate plot params
    is_valid, error = validate_plot_params("line", width, height)
    if not is_valid:
        return {"success": False, "error": error}
    
    try:
        # Get theme
        template, colors = _get_plotly_theme(color_scheme)
        
        # Determine line mode
        mode = "lines+markers" if show_points else "lines"
        
        # Create line chart
        fig = go.Figure(data=[go.Scatter(
            x=numeric_x,
            y=numeric_y,
            mode=mode,
            line=dict(color=colors["primary"], width=2),
            marker=dict(color=colors["primary"], size=6) if show_points else None,
        )])
        
        # Update layout
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor='center'),
            xaxis_title=x_label,
            yaxis_title=y_label,
            template=template,
            width=width,
            height=height,
            margin=dict(l=80, r=40, t=70, b=60),
        )
        
        # Save to file if requested
        png_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            png_path = plots_dir / f"line_chart_{timestamp}.png"
            
            fig.write_image(str(png_path), format='png', scale=2)
            
            logger.info(f"Saved line chart to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path) if png_path else None,
            "metadata": {
                "plot_type": "line",
                "title": title,
                "width": width,
                "height": height,
                "data_points": len(numeric_y),
            }
        }
        
    except Exception as e:
        logger.error(f"Error generating line chart: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Scatter Plot Generation (PNG via Plotly)
# =============================================================================


@register_tool(
    name="generate_scatter_plot",
    description="Generate a scatter plot as a PNG image. "
                "Useful for visualizing relationships between two variables.",
    input_schema={
        "type": "object",
        "properties": {
            "x_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "X-axis values"
            },
            "y_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Y-axis values"
            },
            "title": {
                "type": "string",
                "description": "Chart title. Default: 'Scatter Plot'"
            },
            "x_label": {
                "type": "string",
                "description": "X-axis label. Default: 'X'"
            },
            "y_label": {
                "type": "string",
                "description": "Y-axis label. Default: 'Y'"
            },
            "point_size": {
                "type": "integer",
                "description": "Point radius in pixels. Default: 8"
            },
            "color_scheme": {
                "type": "string",
                "enum": ["default", "dark", "warm"],
                "description": "Color scheme. Default: 'default'"
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels. Default: 400"
            },
            "save_to_file": {
                "type": "boolean",
                "description": "Whether to save to file. Default: true"
            }
        },
        "required": ["x_data", "y_data"]
    }
)
def generate_scatter_plot(
    x_data: List[Any],
    y_data: List[Any],
    title: str = "Scatter Plot",
    x_label: str = "X",
    y_label: str = "Y",
    point_size: int = 8,
    color_scheme: str = "default",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    save_to_file: bool = True
) -> Dict[str, Any]:
    """
    Generate a scatter plot PNG using Plotly.
    
    Args:
        x_data: X-axis values
        y_data: Y-axis values
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        point_size: Point radius
        color_scheme: Color scheme name
        width: Image width
        height: Image height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with PNG path and metadata
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {"success": False, "error": "Plotly not installed. Run: pip install plotly kaleido"}
    
    logger.info(f"Generating scatter plot for {len(x_data)} points")
    
    # Validate data
    is_valid, error, numeric_x = validate_numeric_data(x_data)
    if not is_valid:
        return {"success": False, "error": f"X data: {error}"}
    
    is_valid, error, numeric_y = validate_numeric_data(y_data)
    if not is_valid:
        return {"success": False, "error": f"Y data: {error}"}
    
    if len(numeric_x) != len(numeric_y):
        return {"success": False, "error": "X and Y data must have same length"}
    
    # Validate plot params
    is_valid, error = validate_plot_params("scatter", width, height)
    if not is_valid:
        return {"success": False, "error": error}
    
    try:
        # Get theme
        template, colors = _get_plotly_theme(color_scheme)
        
        # Create scatter plot
        fig = go.Figure(data=[go.Scatter(
            x=numeric_x,
            y=numeric_y,
            mode='markers',
            marker=dict(
                color=colors["primary"],
                size=point_size,
                line=dict(color=colors["axis"], width=1)
            ),
        )])
        
        # Update layout
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor='center'),
            xaxis_title=x_label,
            yaxis_title=y_label,
            template=template,
            width=width,
            height=height,
            margin=dict(l=80, r=40, t=70, b=60),
        )
        
        # Save to file if requested
        png_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            png_path = plots_dir / f"scatter_{timestamp}.png"
            
            fig.write_image(str(png_path), format='png', scale=2)
            
            logger.info(f"Saved scatter plot to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path) if png_path else None,
            "metadata": {
                "plot_type": "scatter",
                "title": title,
                "width": width,
                "height": height,
                "data_points": len(numeric_x),
            }
        }
        
    except Exception as e:
        logger.error(f"Error generating scatter plot: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Bar Chart Generation (PNG via Plotly)
# =============================================================================


@register_tool(
    name="generate_bar_chart",
    description="Generate a bar chart as a PNG image. "
                "Useful for comparing categorical data.",
    input_schema={
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Bar values (heights)"
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Bar labels (optional, defaults to 1, 2, 3, ...)"
            },
            "title": {
                "type": "string",
                "description": "Chart title. Default: 'Bar Chart'"
            },
            "x_label": {
                "type": "string",
                "description": "X-axis label. Default: ''"
            },
            "y_label": {
                "type": "string",
                "description": "Y-axis label. Default: 'Value'"
            },
            "color_scheme": {
                "type": "string",
                "enum": ["default", "dark", "warm"],
                "description": "Color scheme. Default: 'default'"
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels. Default: 400"
            },
            "save_to_file": {
                "type": "boolean",
                "description": "Whether to save to file. Default: true"
            }
        },
        "required": ["values"]
    }
)
def generate_bar_chart(
    values: List[Any],
    labels: Optional[List[str]] = None,
    title: str = "Bar Chart",
    x_label: str = "",
    y_label: str = "Value",
    color_scheme: str = "default",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    save_to_file: bool = True
) -> Dict[str, Any]:
    """
    Generate a bar chart PNG using Plotly.
    
    Args:
        values: Bar values
        labels: Bar labels
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        color_scheme: Color scheme name
        width: Image width
        height: Image height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with PNG path and metadata
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {"success": False, "error": "Plotly not installed. Run: pip install plotly kaleido"}
    
    logger.info(f"Generating bar chart for {len(values)} bars")
    
    # Validate values
    is_valid, error, numeric_values = validate_numeric_data(values)
    if not is_valid:
        return {"success": False, "error": error}
    
    # Generate default labels if not provided
    if labels is None:
        labels = [str(i + 1) for i in range(len(numeric_values))]
    elif len(labels) != len(numeric_values):
        return {"success": False, "error": "Labels and values must have same length"}
    
    # Validate plot params
    is_valid, error = validate_plot_params("bar", width, height)
    if not is_valid:
        return {"success": False, "error": error}
    
    try:
        # Get theme
        template, colors = _get_plotly_theme(color_scheme)
        
        # Create bar chart
        fig = go.Figure(data=[go.Bar(
            x=labels,
            y=numeric_values,
            marker=dict(
                color=colors["primary"],
                line=dict(color=colors["axis"], width=1)
            ),
        )])
        
        # Update layout
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor='center'),
            xaxis_title=x_label if x_label else None,
            yaxis_title=y_label,
            template=template,
            width=width,
            height=height,
            margin=dict(l=80, r=40, t=70, b=60),
            bargap=0.2,
        )
        
        # Save to file if requested
        png_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            png_path = plots_dir / f"bar_chart_{timestamp}.png"
            
            fig.write_image(str(png_path), format='png', scale=2)
            
            logger.info(f"Saved bar chart to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path) if png_path else None,
            "metadata": {
                "plot_type": "bar",
                "title": title,
                "width": width,
                "height": height,
                "data_points": len(numeric_values),
                "labels": labels,
            }
        }
        
    except Exception as e:
        logger.error(f"Error generating bar chart: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Candlestick Chart (OHLC) - Plotly Implementation
# =============================================================================


@register_tool(
    name="generate_candlestick_chart",
    description="DEFAULT CHART FOR PRICE DATA. Generate a candlestick chart (OHLC) as a PNG image. "
                "ALWAYS use this instead of line_chart when OHLC data is available. "
                "Shows price movement with open, high, low, close candles. "
                "Auto-detects Treasury futures and shows bond prices in 32nds format. "
                "Auto-fetches economic events and marks them as vertical lines. "
                "Only use generate_line_chart if user explicitly asks for 'line chart' or 'close only'.",
    input_schema={
        "type": "object",
        "properties": {
            "open_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of opening prices"
            },
            "high_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of high prices"
            },
            "low_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of low prices"
            },
            "close_data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of closing prices"
            },
            "timestamps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of timestamps for x-axis labels"
            },
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "description": "Event timestamp (ISO format)"},
                        "name": {"type": "string", "description": "Event name (e.g., 'NFP', 'CPI')"},
                        "importance": {"type": "string", "description": "Event importance: low, medium, high"}
                    },
                    "required": ["timestamp", "name"]
                },
                "description": "Optional list of economic events to mark on the chart as vertical lines"
            },
            "show_event_labels": {
                "type": "boolean",
                "description": "Whether to show event names as labels on the chart",
                "default": True
            },
            "auto_fetch_events": {
                "type": "boolean",
                "description": "If True, automatically query economic events for the chart's time range. "
                              "Enabled by default. Set to False to disable event overlays.",
                "default": True
            },
            "event_country": {
                "type": "string",
                "description": "Country code for auto-fetching events (e.g., 'US', 'GB'). "
                              "Default: 'US' for Treasury futures",
                "default": "US"
            },
            "title": {
                "type": "string",
                "description": "Chart title",
                "default": "Candlestick Chart"
            },
            "x_label": {
                "type": "string",
                "description": "X-axis label",
                "default": "Time"
            },
            "y_label": {
                "type": "string",
                "description": "Y-axis label",
                "default": "Price"
            },
            "theme": {
                "type": "string",
                "description": "Chart theme. Use 'dark' for dark mode or 'light' for light mode.",
                "default": "dark"
            },
        },
        "required": ["open_data", "high_data", "low_data", "close_data"]
    },
    # Soft dependencies: orchestrator auto-injects these tasks before chart generation
    soft_dependencies=[
        {
            "param_name": "events",
            "source_agent": "eventdata_puller_agent",
            "source_tool": "get_events_by_date_range",  # New tool with date range support
            "param_mapping": {
                # Context refs: $context.path.to.value|default
                # Pass timestamp range if available (for intraday charts), else date range
                "start_date": "$context.time_range.start_timestamp|$context.time_range.start",
                "end_date": "$context.time_range.end_timestamp|$context.time_range.end",
                "country": "$context.country|US",
                # Fetch ALL events for the day (no importance filter)
                "importance": None,
                "limit": 500
            },
            "output_path": "events",  # Extract 'events' from tool result
            "required": False  # Graceful degradation: plot without events if fetch fails
        }
    ]
)
def generate_candlestick_chart(
    open_data: List[Any],
    high_data: List[Any],
    low_data: List[Any],
    close_data: List[Any],
    timestamps: Optional[List[Any]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    show_event_labels: bool = True,
    auto_fetch_events: bool = True,
    event_country: str = "US",
    title: str = "Candlestick Chart",
    x_label: str = "Time",
    y_label: str = "Price",
    theme: str = "dark",
    color_scheme: str = "default",
    width: int = 1600,
    height: int = 600,
    save_to_file: bool = True,
) -> Dict[str, Any]:
    """
    MCP tool wrapper for candlestick chart generation.
    
    Delegates to plot_candlestick() in core_plotting.py which provides:
    - Auto-resample 1m→5m candles for full-day views
    - Auto-detect Treasury futures and show bond prices in 32nds format
    - Dark/light theme support
    - Event clustering with staggered labels
    
    Args:
        open_data: List of opening prices
        high_data: List of high prices
        low_data: List of low prices
        close_data: List of closing prices
        timestamps: Optional list of timestamps (ISO format strings)
        events: Optional list of events to mark as vertical lines
        show_event_labels: Whether to show event names on chart
        auto_fetch_events: If True, auto-query events for the time range
        event_country: Country code for event lookup (default "US")
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        theme: "dark" or "light"
        color_scheme: Color scheme name (unused, kept for API compatibility)
        width: Image width in pixels
        height: Image height in pixels
        save_to_file: Whether to save to file (unused, always saves)
        
    Returns:
        Dictionary with PNG path and metadata
    """
    from pathlib import Path
    
    return plot_candlestick(
        open_data=open_data,
        high_data=high_data,
        low_data=low_data,
        close_data=close_data,
        timestamps=timestamps,
        events=events,
        show_event_labels=show_event_labels,
        auto_fetch_events=auto_fetch_events,
        event_country=event_country,
        title=title,
        x_label=x_label,
        y_label=y_label,
        theme=theme,
        width=width,
        height=height,
        output_dir=None,  # Use default plots directory
    )


@register_tool(
    name="generate_multi_candlestick_charts",
    description="Generate multiple candlestick charts from a list of timeseries data. Ideal for 'for each event date, plot a chart' queries. Each chart auto-fetches events for its specific date range.",
)
@register_tool(
    name="generate_multi_candlestick_chart",  # Alias without 's' for AI compatibility
    description="Generate multiple candlestick charts from a list of timeseries data. Ideal for 'for each event date, plot a chart' queries."
)
def generate_multi_candlestick_charts(
    timeseries_list: List[Dict[str, Any]],
    width: int = 1600,
    height: int = 600,
) -> Dict[str, Any]:
    """
    Generate multiple candlestick charts from a list of timeseries data.
    
    This is designed for queries like "for each NFP event, show a price chart".
    Instead of creating multiple tasks, pass all timeseries data to this single tool.
    
    Args:
        timeseries_list: List of timeseries, each with:
            - "title": Chart title (e.g., "NFP Dec 6, 2024")
            - "date": Date string for identification
            - "data": List of OHLC records [{timestamp, open, high, low, close}, ...]
              OR separate arrays: {"open": [...], "high": [...], "low": [...], "close": [...], "timestamps": [...]}
        width: Chart width in pixels (default: 1600)
        height: Chart height in pixels (default: 600)
        
    Returns:
        {
            "success": True,
            "charts": [
                {"date": "2024-12-06", "title": "...", "png_path": "...", "num_candles": N},
                ...
            ],
            "metadata": {
                "charts_generated": 5,
                "charts_failed": 0,
                "total_candles": 5000
            }
        }
        
    Example input:
        timeseries_list = [
            {
                "title": "TY Futures - NFP Dec 6, 2024",
                "date": "2024-12-06",
                "data": [
                    {"timestamp": "2024-12-06T08:00:00", "open": 110.5, "high": 110.7, "low": 110.4, "close": 110.6},
                    ...
                ]
            },
            {
                "title": "TY Futures - NFP Dec 8, 2023",
                "date": "2023-12-08",
                "data": [...]
            }
        ]
    """
    logger.info(f"generate_multi_candlestick_charts called with {len(timeseries_list)} timeseries")
    
    if not timeseries_list:
        return {
            "success": False,
            "error": "No timeseries data provided",
            "charts": [],
            "metadata": {}
        }
    
    charts = []
    charts_generated = 0
    charts_failed = 0
    total_candles = 0
    
    for i, ts in enumerate(timeseries_list):
        try:
            date = ts.get("date", f"unknown_{i}")
            # Use date as default title if no title provided
            title = ts.get("title", f"TY Futures - {date}")
            data = ts.get("data", [])
            
            # Handle two data formats:
            # 1. List of dicts: [{"open": ..., "high": ..., ...}, ...]
            # 2. Separate arrays: {"open": [...], "high": [...], ...}
            
            if isinstance(data, list) and len(data) > 0:
                # Format 1: List of OHLC dicts
                if isinstance(data[0], dict):
                    open_data = [d.get("open") for d in data]
                    high_data = [d.get("high") for d in data]
                    low_data = [d.get("low") for d in data]
                    close_data = [d.get("close") for d in data]
                    timestamps = [d.get("timestamp") for d in data]
                else:
                    logger.warning(f"Timeseries {i} has invalid data format")
                    charts_failed += 1
                    charts.append({
                        "date": date,
                        "title": title,
                        "success": False,
                        "error": "Invalid data format - expected list of OHLC dicts"
                    })
                    continue
            elif isinstance(data, dict):
                # Format 2: Separate arrays
                open_data = data.get("open", [])
                high_data = data.get("high", [])
                low_data = data.get("low", [])
                close_data = data.get("close", [])
                timestamps = data.get("timestamps", [])
            else:
                logger.warning(f"Timeseries {i} has no data")
                charts_failed += 1
                charts.append({
                    "date": date,
                    "title": title,
                    "success": False,
                    "error": "No data provided"
                })
                continue
            
            # Skip if no data
            if not close_data or len(close_data) == 0:
                logger.warning(f"Timeseries {i} ({date}) has empty data")
                charts_failed += 1
                charts.append({
                    "date": date,
                    "title": title,
                    "success": False,
                    "error": "Empty data array"
                })
                continue
            
            # Generate the chart - each chart auto-fetches events for its date range
            result = generate_candlestick_chart(
                open_data=open_data,
                high_data=high_data,
                low_data=low_data,
                close_data=close_data,
                timestamps=timestamps,
                title=title,
                width=width,
                height=height,
                auto_fetch_events=True,
                save_to_file=True
            )
            
            if result.get("success"):
                charts_generated += 1
                num_candles = result.get("metadata", {}).get("num_candles", len(close_data))
                total_candles += num_candles
                charts.append({
                    "date": date,
                    "title": title,
                    "success": True,
                    "png_path": result.get("png_path"),
                    "num_candles": num_candles
                })
                logger.info(f"  Generated chart for {date}: {num_candles} candles")
            else:
                charts_failed += 1
                charts.append({
                    "date": date,
                    "title": title,
                    "success": False,
                    "error": result.get("error", "Unknown error")
                })
                logger.error(f"  Failed to generate chart for {date}: {result.get('error')}")
                
        except Exception as e:
            charts_failed += 1
            charts.append({
                "date": ts.get("date", f"unknown_{i}"),
                "title": ts.get("title", f"Chart {i+1}"),
                "success": False,
                "error": str(e)
            })
            logger.error(f"  Exception generating chart {i}: {e}")
    
    return {
        "success": charts_generated > 0,
        "charts": charts,
        "metadata": {
            "charts_generated": charts_generated,
            "charts_failed": charts_failed,
            "total_candles": total_candles
        }
    }
