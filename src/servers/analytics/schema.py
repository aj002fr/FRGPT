"""
Analytics tool schema and constants.

Defines input/output schemas for statistical analysis and plotting tools.
Pure Python implementation - no external dependencies.
"""

from typing import Dict, Any, List, Optional
from pathlib import Path

# =============================================================================
# Database Configuration
# =============================================================================

def get_workspace_path() -> Path:
    """Get path to workspace directory."""
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "workspace"


def get_plots_dir() -> Path:
    """Get path to plots output directory."""
    return get_workspace_path() / "plots"


# =============================================================================
# Statistical Constants
# =============================================================================

# Default percentiles to compute
DEFAULT_PERCENTILES = [5, 10, 25, 50, 75, 90, 95]

# Histogram defaults
DEFAULT_HISTOGRAM_BINS = 20
MAX_HISTOGRAM_BINS = 100
MIN_HISTOGRAM_BINS = 5

# =============================================================================
# Plot Configuration
# =============================================================================

# SVG dimensions
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 400
DEFAULT_MARGIN = {"top": 40, "right": 40, "bottom": 60, "left": 80}

# Color schemes
COLOR_SCHEMES = {
    "default": {
        "primary": "#4A90D9",
        "secondary": "#7CB342",
        "accent": "#FF7043",
        "background": "#FFFFFF",
        "grid": "#E0E0E0",
        "text": "#333333",
        "axis": "#666666",
    },
    "dark": {
        "primary": "#64B5F6",
        "secondary": "#81C784",
        "accent": "#FF8A65",
        "background": "#1E1E1E",
        "grid": "#404040",
        "text": "#E0E0E0",
        "axis": "#AAAAAA",
    },
    "warm": {
        "primary": "#FF7043",
        "secondary": "#FFB74D",
        "accent": "#E57373",
        "background": "#FFF8E1",
        "grid": "#FFE0B2",
        "text": "#5D4037",
        "axis": "#795548",
    },
}

# Plot types
PLOT_TYPES = ["histogram", "line", "scatter", "bar"]

# =============================================================================
# Output Schema
# =============================================================================

STATISTICS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "statistics": {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "mean": {"type": "number"},
                "median": {"type": "number"},
                "std_dev": {"type": "number"},
                "variance": {"type": "number"},
                "min": {"type": "number"},
                "max": {"type": "number"},
                "range": {"type": "number"},
                "percentiles": {"type": "object"},
                "skewness": {"type": "number"},
                "kurtosis": {"type": "number"},
            }
        },
        "error": {"type": "string"},
    },
    "required": ["success"]
}

PLOT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "svg_path": {"type": "string"},
        "svg_content": {"type": "string"},
        "metadata": {
            "type": "object",
            "properties": {
                "plot_type": {"type": "string"},
                "title": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "data_points": {"type": "integer"},
            }
        },
        "error": {"type": "string"},
    },
    "required": ["success"]
}

# =============================================================================
# Helper Functions
# =============================================================================


def validate_numeric_data(data: List[Any]) -> tuple[bool, str, List[float]]:
    """
    Validate and convert data to numeric values.
    
    Args:
        data: List of values to validate
        
    Returns:
        (is_valid, error_message, converted_data)
    """
    if not data:
        return False, "Data list is empty", []
    
    converted = []
    for i, val in enumerate(data):
        if val is None:
            continue  # Skip None values
        try:
            converted.append(float(val))
        except (ValueError, TypeError):
            return False, f"Non-numeric value at index {i}: {val}", []
    
    if not converted:
        return False, "No valid numeric values in data", []
    
    return True, "", converted


def validate_plot_params(
    plot_type: str,
    width: int,
    height: int,
    bins: Optional[int] = None
) -> tuple[bool, str]:
    """
    Validate plot parameters.
    
    Args:
        plot_type: Type of plot
        width: Plot width
        height: Plot height
        bins: Number of histogram bins (optional)
        
    Returns:
        (is_valid, error_message)
    """
    if plot_type not in PLOT_TYPES:
        return False, f"Invalid plot type: {plot_type}. Allowed: {', '.join(PLOT_TYPES)}"
    
    if width < 200 or width > 2000:
        return False, f"Width must be between 200 and 2000, got {width}"
    
    if height < 150 or height > 1500:
        return False, f"Height must be between 150 and 1500, got {height}"
    
    if bins is not None:
        if bins < MIN_HISTOGRAM_BINS or bins > MAX_HISTOGRAM_BINS:
            return False, f"Bins must be between {MIN_HISTOGRAM_BINS} and {MAX_HISTOGRAM_BINS}, got {bins}"
    
    return True, ""


def format_number(value: float, precision: int = 4) -> str:
    """
    Format a number for display.
    
    Args:
        value: Numeric value
        precision: Decimal places
        
    Returns:
        Formatted string
    """
    if abs(value) >= 1e6:
        return f"{value:.2e}"
    elif abs(value) >= 1000:
        return f"{value:,.{precision}f}"
    elif abs(value) < 0.0001 and value != 0:
        return f"{value:.2e}"
    else:
        return f"{value:.{precision}f}"

