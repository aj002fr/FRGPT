"""
SVG Plotting tools - Pure Python implementation.

Generates SVG visualizations using only the standard library:
- Histograms (frequency distributions)
- Line charts (time series)
- Scatter plots
- Bar charts

No matplotlib, plotly, or other external dependencies.
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
)

logger = logging.getLogger(__name__)


# =============================================================================
# SVG Building Utilities
# =============================================================================


def _escape_xml(text: str) -> str:
    """Escape text for XML/SVG."""
    return html.escape(str(text))


def _svg_header(width: int, height: int, background: str) -> str:
    """Generate SVG header."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect width="100%" height="100%" fill="{background}"/>
  <style>
    .title {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 16px; font-weight: bold; }}
    .axis-label {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; }}
    .tick-label {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 10px; }}
    .legend {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; }}
  </style>
'''


def _svg_footer() -> str:
    """Generate SVG footer."""
    return '</svg>'


def _svg_line(x1: float, y1: float, x2: float, y2: float, color: str, width: float = 1) -> str:
    """Generate SVG line element."""
    return f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"/>\n'


def _svg_rect(x: float, y: float, w: float, h: float, fill: str, stroke: str = "none", rx: float = 0) -> str:
    """Generate SVG rectangle element."""
    return f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" stroke="{stroke}" rx="{rx}"/>\n'


def _svg_circle(cx: float, cy: float, r: float, fill: str, stroke: str = "none") -> str:
    """Generate SVG circle element."""
    return f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}" stroke="{stroke}"/>\n'


def _svg_text(x: float, y: float, text: str, css_class: str, fill: str, anchor: str = "middle", rotate: float = 0) -> str:
    """Generate SVG text element."""
    escaped = _escape_xml(text)
    transform = f' transform="rotate({rotate} {x} {y})"' if rotate != 0 else ''
    return f'  <text x="{x:.1f}" y="{y:.1f}" class="{css_class}" fill="{fill}" text-anchor="{anchor}"{transform}>{escaped}</text>\n'


def _svg_path(d: str, fill: str = "none", stroke: str = "black", stroke_width: float = 1) -> str:
    """Generate SVG path element."""
    return f'  <path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>\n'


def _svg_polyline(points: List[Tuple[float, float]], stroke: str, stroke_width: float = 2, fill: str = "none") -> str:
    """Generate SVG polyline element."""
    points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'  <polyline points="{points_str}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>\n'


# =============================================================================
# Axis and Grid Utilities
# =============================================================================


def _nice_number(x: float, round_up: bool = False) -> float:
    """Find a 'nice' number for axis ticks."""
    if x == 0:
        return 0
    
    exp = math.floor(math.log10(abs(x)))
    f = x / (10 ** exp)
    
    if round_up:
        if f <= 1:
            nf = 1
        elif f <= 2:
            nf = 2
        elif f <= 5:
            nf = 5
        else:
            nf = 10
    else:
        if f < 1.5:
            nf = 1
        elif f < 3:
            nf = 2
        elif f < 7:
            nf = 5
        else:
            nf = 10
    
    return nf * (10 ** exp)


def _nice_axis_range(min_val: float, max_val: float, num_ticks: int = 5) -> Tuple[float, float, float]:
    """Calculate nice axis range and tick interval."""
    range_val = max_val - min_val
    
    if range_val == 0:
        return min_val - 1, max_val + 1, 1
    
    tick_spacing = _nice_number(range_val / (num_ticks - 1))
    nice_min = math.floor(min_val / tick_spacing) * tick_spacing
    nice_max = math.ceil(max_val / tick_spacing) * tick_spacing
    
    return nice_min, nice_max, tick_spacing


def _generate_axis_ticks(min_val: float, max_val: float, num_ticks: int = 5) -> List[float]:
    """Generate nice tick values for an axis."""
    nice_min, nice_max, tick_spacing = _nice_axis_range(min_val, max_val, num_ticks)
    
    ticks = []
    current = nice_min
    while current <= nice_max + tick_spacing * 0.01:  # Small epsilon for float comparison
        ticks.append(current)
        current += tick_spacing
    
    return ticks


def _draw_grid_and_axes(
    plot_x: float, plot_y: float, plot_width: float, plot_height: float,
    x_ticks: List[float], y_ticks: List[float],
    x_min: float, x_max: float, y_min: float, y_max: float,
    colors: Dict[str, str],
    x_label: str = "", y_label: str = ""
) -> str:
    """Draw grid lines and axes with labels."""
    svg = ""
    
    # Helper to scale values to plot coordinates
    def scale_x(val: float) -> float:
        if x_max == x_min:
            return plot_x + plot_width / 2
        return plot_x + (val - x_min) / (x_max - x_min) * plot_width
    
    def scale_y(val: float) -> float:
        if y_max == y_min:
            return plot_y + plot_height / 2
        return plot_y + plot_height - (val - y_min) / (y_max - y_min) * plot_height
    
    # Draw horizontal grid lines and Y-axis ticks
    for y_val in y_ticks:
        y_pos = scale_y(y_val)
        # Grid line
        svg += _svg_line(plot_x, y_pos, plot_x + plot_width, y_pos, colors["grid"], 0.5)
        # Tick label
        svg += _svg_text(plot_x - 10, y_pos + 4, format_number(y_val, 2), "tick-label", colors["text"], "end")
    
    # Draw vertical grid lines and X-axis ticks
    for x_val in x_ticks:
        x_pos = scale_x(x_val)
        # Grid line
        svg += _svg_line(x_pos, plot_y, x_pos, plot_y + plot_height, colors["grid"], 0.5)
        # Tick label
        svg += _svg_text(x_pos, plot_y + plot_height + 15, format_number(x_val, 2), "tick-label", colors["text"])
    
    # Draw axes
    svg += _svg_line(plot_x, plot_y + plot_height, plot_x + plot_width, plot_y + plot_height, colors["axis"], 1.5)  # X-axis
    svg += _svg_line(plot_x, plot_y, plot_x, plot_y + plot_height, colors["axis"], 1.5)  # Y-axis
    
    # Axis labels
    if x_label:
        svg += _svg_text(plot_x + plot_width / 2, plot_y + plot_height + 40, x_label, "axis-label", colors["text"])
    
    if y_label:
        svg += _svg_text(plot_x - 50, plot_y + plot_height / 2, y_label, "axis-label", colors["text"], "middle", -90)
    
    return svg


# =============================================================================
# Histogram Generation
# =============================================================================


def _create_histogram_bins(data: List[float], num_bins: int) -> Tuple[List[float], List[int]]:
    """Create histogram bins and counts."""
    min_val = min(data)
    max_val = max(data)
    
    if min_val == max_val:
        return [min_val], [len(data)]
    
    bin_width = (max_val - min_val) / num_bins
    bin_edges = [min_val + i * bin_width for i in range(num_bins + 1)]
    counts = [0] * num_bins
    
    for val in data:
        # Find bin index
        bin_idx = int((val - min_val) / bin_width)
        if bin_idx >= num_bins:
            bin_idx = num_bins - 1
        counts[bin_idx] += 1
    
    return bin_edges, counts


@register_tool(
    name="generate_histogram",
    description="Generate a histogram (frequency distribution) as an SVG image. "
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
                "description": "SVG width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "SVG height in pixels. Default: 400"
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
    Generate a histogram SVG.
    
    Args:
        data: List of numeric values
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        bins: Number of bins
        color_scheme: Color scheme name
        width: SVG width
        height: SVG height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with SVG content and metadata
    """
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
        # Get colors
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["default"])
        margin = DEFAULT_MARGIN
        
        # Calculate plot area
        plot_x = margin["left"]
        plot_y = margin["top"]
        plot_width = width - margin["left"] - margin["right"]
        plot_height = height - margin["top"] - margin["bottom"]
        
        # Create histogram bins
        bin_edges, counts = _create_histogram_bins(numeric_data, bins)
        
        # Calculate axis ranges
        x_min, x_max = bin_edges[0], bin_edges[-1]
        y_min, y_max = 0, max(counts) * 1.1  # Add 10% headroom
        
        # Generate ticks
        x_ticks = _generate_axis_ticks(x_min, x_max, 6)
        y_ticks = _generate_axis_ticks(y_min, y_max, 5)
        
        # Start SVG
        svg = _svg_header(width, height, colors["background"])
        
        # Title
        svg += _svg_text(width / 2, 25, title, "title", colors["text"])
        
        # Draw grid and axes
        svg += _draw_grid_and_axes(
            plot_x, plot_y, plot_width, plot_height,
            x_ticks, y_ticks, x_min, x_max, y_min, y_max,
            colors, x_label, y_label
        )
        
        # Draw bars
        bar_width = plot_width / len(counts) - 2  # 2px gap
        for i, count in enumerate(counts):
            if count > 0:
                bar_x = plot_x + i * (plot_width / len(counts)) + 1
                bar_height = (count / y_max) * plot_height if y_max > 0 else 0
                bar_y = plot_y + plot_height - bar_height
                
                svg += _svg_rect(bar_x, bar_y, bar_width, bar_height, colors["primary"], colors["axis"], 2)
        
        # Close SVG
        svg += _svg_footer()
        
        # Save to file if requested
        svg_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            svg_path = plots_dir / f"histogram_{timestamp}.svg"
            
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svg)
            
            logger.info(f"Saved histogram to {svg_path}")
        
        return {
            "success": True,
            "svg_content": svg,
            "svg_path": str(svg_path) if svg_path else None,
            "metadata": {
                "plot_type": "histogram",
                "title": title,
                "width": width,
                "height": height,
                "data_points": len(numeric_data),
                "bins": len(counts),
                "bin_edges": [round(e, 4) for e in bin_edges],
                "counts": counts,
            }
        }
        
    except Exception as e:
        logger.error(f"Error generating histogram: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Line Chart Generation
# =============================================================================


@register_tool(
    name="generate_line_chart",
    description="Generate a line chart as an SVG image. "
                "Useful for time series or sequential data visualization.",
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
                "description": "SVG width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "SVG height in pixels. Default: 400"
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
    Generate a line chart SVG.
    
    Args:
        y_data: Y-axis values
        x_data: X-axis values (optional)
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        show_points: Whether to show data points
        color_scheme: Color scheme name
        width: SVG width
        height: SVG height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with SVG content and metadata
    """
    logger.info(f"Generating line chart for {len(y_data)} values")
    
    # Validate Y data
    is_valid, error, numeric_y = validate_numeric_data(y_data)
    if not is_valid:
        return {"success": False, "error": f"Y data: {error}"}
    
    # Validate or generate X data
    if x_data is not None:
        is_valid, error, numeric_x = validate_numeric_data(x_data)
        if not is_valid:
            return {"success": False, "error": f"X data: {error}"}
        if len(numeric_x) != len(numeric_y):
            return {"success": False, "error": "X and Y data must have same length"}
    else:
        numeric_x = list(range(len(numeric_y)))
    
    # Validate plot params
    is_valid, error = validate_plot_params("line", width, height)
    if not is_valid:
        return {"success": False, "error": error}
    
    try:
        # Get colors
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["default"])
        margin = DEFAULT_MARGIN
        
        # Calculate plot area
        plot_x = margin["left"]
        plot_y = margin["top"]
        plot_width = width - margin["left"] - margin["right"]
        plot_height = height - margin["top"] - margin["bottom"]
        
        # Calculate axis ranges
        x_min, x_max = min(numeric_x), max(numeric_x)
        y_min, y_max = min(numeric_y), max(numeric_y)
        
        # Add padding to Y range
        y_range = y_max - y_min
        if y_range == 0:
            y_min -= 1
            y_max += 1
        else:
            y_min -= y_range * 0.05
            y_max += y_range * 0.05
        
        # Generate ticks
        x_ticks = _generate_axis_ticks(x_min, x_max, 6)
        y_ticks = _generate_axis_ticks(y_min, y_max, 5)
        
        # Helper to scale values
        def scale_x(val: float) -> float:
            if x_max == x_min:
                return plot_x + plot_width / 2
            return plot_x + (val - x_min) / (x_max - x_min) * plot_width
        
        def scale_y(val: float) -> float:
            if y_max == y_min:
                return plot_y + plot_height / 2
            return plot_y + plot_height - (val - y_min) / (y_max - y_min) * plot_height
        
        # Start SVG
        svg = _svg_header(width, height, colors["background"])
        
        # Title
        svg += _svg_text(width / 2, 25, title, "title", colors["text"])
        
        # Draw grid and axes
        svg += _draw_grid_and_axes(
            plot_x, plot_y, plot_width, plot_height,
            x_ticks, y_ticks, x_min, x_max, y_min, y_max,
            colors, x_label, y_label
        )
        
        # Generate line points
        points = [(scale_x(numeric_x[i]), scale_y(numeric_y[i])) for i in range(len(numeric_x))]
        
        # Draw line
        svg += _svg_polyline(points, colors["primary"], 2)
        
        # Draw points
        if show_points:
            for px, py in points:
                svg += _svg_circle(px, py, 4, colors["primary"], colors["background"])
        
        # Close SVG
        svg += _svg_footer()
        
        # Save to file if requested
        svg_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            svg_path = plots_dir / f"line_chart_{timestamp}.svg"
            
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svg)
            
            logger.info(f"Saved line chart to {svg_path}")
        
        return {
            "success": True,
            "svg_content": svg,
            "svg_path": str(svg_path) if svg_path else None,
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
# Scatter Plot Generation
# =============================================================================


@register_tool(
    name="generate_scatter_plot",
    description="Generate a scatter plot as an SVG image. "
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
                "description": "Point radius in pixels. Default: 5"
            },
            "color_scheme": {
                "type": "string",
                "enum": ["default", "dark", "warm"],
                "description": "Color scheme. Default: 'default'"
            },
            "width": {
                "type": "integer",
                "description": "SVG width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "SVG height in pixels. Default: 400"
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
    point_size: int = 5,
    color_scheme: str = "default",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    save_to_file: bool = True
) -> Dict[str, Any]:
    """
    Generate a scatter plot SVG.
    
    Args:
        x_data: X-axis values
        y_data: Y-axis values
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        point_size: Point radius
        color_scheme: Color scheme name
        width: SVG width
        height: SVG height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with SVG content and metadata
    """
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
        # Get colors
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["default"])
        margin = DEFAULT_MARGIN
        
        # Calculate plot area
        plot_x = margin["left"]
        plot_y = margin["top"]
        plot_width = width - margin["left"] - margin["right"]
        plot_height = height - margin["top"] - margin["bottom"]
        
        # Calculate axis ranges with padding
        x_min, x_max = min(numeric_x), max(numeric_x)
        y_min, y_max = min(numeric_y), max(numeric_y)
        
        x_range = x_max - x_min
        y_range = y_max - y_min
        
        if x_range == 0:
            x_min -= 1
            x_max += 1
        else:
            x_min -= x_range * 0.05
            x_max += x_range * 0.05
        
        if y_range == 0:
            y_min -= 1
            y_max += 1
        else:
            y_min -= y_range * 0.05
            y_max += y_range * 0.05
        
        # Generate ticks
        x_ticks = _generate_axis_ticks(x_min, x_max, 6)
        y_ticks = _generate_axis_ticks(y_min, y_max, 5)
        
        # Helper to scale values
        def scale_x(val: float) -> float:
            return plot_x + (val - x_min) / (x_max - x_min) * plot_width
        
        def scale_y(val: float) -> float:
            return plot_y + plot_height - (val - y_min) / (y_max - y_min) * plot_height
        
        # Start SVG
        svg = _svg_header(width, height, colors["background"])
        
        # Title
        svg += _svg_text(width / 2, 25, title, "title", colors["text"])
        
        # Draw grid and axes
        svg += _draw_grid_and_axes(
            plot_x, plot_y, plot_width, plot_height,
            x_ticks, y_ticks, x_min, x_max, y_min, y_max,
            colors, x_label, y_label
        )
        
        # Draw points
        for i in range(len(numeric_x)):
            px = scale_x(numeric_x[i])
            py = scale_y(numeric_y[i])
            svg += _svg_circle(px, py, point_size, colors["primary"], colors["axis"])
        
        # Close SVG
        svg += _svg_footer()
        
        # Save to file if requested
        svg_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            svg_path = plots_dir / f"scatter_{timestamp}.svg"
            
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svg)
            
            logger.info(f"Saved scatter plot to {svg_path}")
        
        return {
            "success": True,
            "svg_content": svg,
            "svg_path": str(svg_path) if svg_path else None,
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
# Bar Chart Generation
# =============================================================================


@register_tool(
    name="generate_bar_chart",
    description="Generate a bar chart as an SVG image. "
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
                "description": "SVG width in pixels. Default: 800"
            },
            "height": {
                "type": "integer",
                "description": "SVG height in pixels. Default: 400"
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
    Generate a bar chart SVG.
    
    Args:
        values: Bar values
        labels: Bar labels
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        color_scheme: Color scheme name
        width: SVG width
        height: SVG height
        save_to_file: Whether to save to file
        
    Returns:
        Dictionary with SVG content and metadata
    """
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
        # Get colors
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["default"])
        margin = DEFAULT_MARGIN
        
        # Calculate plot area
        plot_x = margin["left"]
        plot_y = margin["top"]
        plot_width = width - margin["left"] - margin["right"]
        plot_height = height - margin["top"] - margin["bottom"]
        
        # Calculate Y axis range
        y_min = min(0, min(numeric_values))  # Include 0
        y_max = max(numeric_values) * 1.1  # Add 10% headroom
        
        if y_max == y_min:
            y_max = y_min + 1
        
        # Generate Y ticks
        y_ticks = _generate_axis_ticks(y_min, y_max, 5)
        
        # Start SVG
        svg = _svg_header(width, height, colors["background"])
        
        # Title
        svg += _svg_text(width / 2, 25, title, "title", colors["text"])
        
        # Draw horizontal grid lines and Y-axis
        for y_val in y_ticks:
            y_pos = plot_y + plot_height - (y_val - y_min) / (y_max - y_min) * plot_height
            svg += _svg_line(plot_x, y_pos, plot_x + plot_width, y_pos, colors["grid"], 0.5)
            svg += _svg_text(plot_x - 10, y_pos + 4, format_number(y_val, 2), "tick-label", colors["text"], "end")
        
        # Draw axes
        y_zero = plot_y + plot_height - (0 - y_min) / (y_max - y_min) * plot_height
        svg += _svg_line(plot_x, y_zero, plot_x + plot_width, y_zero, colors["axis"], 1.5)  # X-axis at 0
        svg += _svg_line(plot_x, plot_y, plot_x, plot_y + plot_height, colors["axis"], 1.5)  # Y-axis
        
        # Axis labels
        if x_label:
            svg += _svg_text(plot_x + plot_width / 2, plot_y + plot_height + 45, x_label, "axis-label", colors["text"])
        if y_label:
            svg += _svg_text(plot_x - 50, plot_y + plot_height / 2, y_label, "axis-label", colors["text"], "middle", -90)
        
        # Draw bars
        n_bars = len(numeric_values)
        bar_width = (plot_width / n_bars) * 0.7
        bar_gap = (plot_width / n_bars) * 0.15
        
        for i, (val, label) in enumerate(zip(numeric_values, labels)):
            bar_x = plot_x + i * (plot_width / n_bars) + bar_gap
            
            if val >= 0:
                bar_height = (val / (y_max - y_min)) * plot_height
                bar_y = y_zero - bar_height
            else:
                bar_height = abs(val / (y_max - y_min)) * plot_height
                bar_y = y_zero
            
            svg += _svg_rect(bar_x, bar_y, bar_width, bar_height, colors["primary"], colors["axis"], 3)
            
            # Label
            label_x = bar_x + bar_width / 2
            label_y = plot_y + plot_height + 15
            # Truncate long labels
            display_label = label[:10] + "..." if len(label) > 10 else label
            svg += _svg_text(label_x, label_y, display_label, "tick-label", colors["text"])
        
        # Close SVG
        svg += _svg_footer()
        
        # Save to file if requested
        svg_path = None
        if save_to_file:
            plots_dir = get_plots_dir()
            ensure_dir(plots_dir)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            svg_path = plots_dir / f"bar_chart_{timestamp}.svg"
            
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svg)
            
            logger.info(f"Saved bar chart to {svg_path}")
        
        return {
            "success": True,
            "svg_content": svg,
            "svg_path": str(svg_path) if svg_path else None,
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

