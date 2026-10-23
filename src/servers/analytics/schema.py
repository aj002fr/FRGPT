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
PLOT_TYPES = ["histogram", "line", "scatter", "bar", "candlestick"]

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
        "png_path": {"type": "string"},
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


# =============================================================================
# Bond Price Formatting (32nds Convention)
# =============================================================================

# Treasury futures symbols that use 32nds pricing
BOND_SYMBOLS = {"TY", "ZN", "ZF", "ZT", "ZB", "UB", "TN", "TWE", "US"}


def decimal_to_32nds(price: float) -> str:
    """
    Convert decimal bond price to 32nds format.
    
    Treasury futures are quoted in 32nds and half-32nds (1/64 increments).
    Valid formats: XX-00 through XX-31 (exact) and XX-00+ through XX-31+ (half)
    
    Examples:
        108.703125 -> "108-22+" (108 and 22.5/32)
        108.6875   -> "108-22"  (108 and 22/32)
        108.71875  -> "108-23"  (108 and 23/32)
    
    Args:
        price: Decimal price (e.g., 108.703125)
        
    Returns:
        Bond format string (e.g., "108-22+")
    """
    whole = int(price)
    fraction = price - whole
    
    # Convert to 32nds
    thirty_seconds = fraction * 32
    
    # Check for half-32nds (remainder ~0.5 means + suffix)
    remainder = thirty_seconds - int(thirty_seconds)
    
    if abs(remainder - 0.5) < 0.001:
        # Half 32nd (e.g., 22.5 -> "22+")
        return f"{whole}-{int(thirty_seconds):02d}+"
    else:
        # Exact 32nd (round to nearest)
        return f"{whole}-{int(round(thirty_seconds)):02d}"


def thirtytwonds_to_decimal(bond_price: str) -> float:
    """
    Convert 32nds format to decimal.
    
    Treasury futures use 32nds and half-32nds (1/64 increments).
    Valid formats: XX-00 through XX-31 (exact) and XX-00+ through XX-31+ (half)
    
    Examples:
        "108-22+" -> 108.703125
        "108-22"  -> 108.6875
        "108-23"  -> 108.71875
    
    Args:
        bond_price: Bond format string (e.g., "108-22+", "108-22")
        
    Returns:
        Decimal price
    """
    # Handle different separators
    if '-' in bond_price:
        parts = bond_price.split('-')
    elif "'" in bond_price and bond_price.count("'") == 1:
        # Alternative format like 108'22
        parts = bond_price.split("'")
    else:
        return float(bond_price)  # Already decimal
    
    whole = int(parts[0])
    fraction_str = parts[1]
    
    # Check for half-32nds (+ suffix)
    if fraction_str.endswith('+'):
        # Half 32nd (e.g., "22+" = 22.5/32)
        thirty_seconds = int(fraction_str[:-1]) + 0.5
    else:
        # Exact 32nd
        thirty_seconds = int(fraction_str)
    
    return whole + thirty_seconds / 32


def is_bond_price(value: float) -> bool:
    """
    Heuristic to detect if a price looks like a Treasury future.
    
    Treasury futures typically trade between 90-140.
    """
    return 85 <= value <= 145


def format_bond_price(value: float, force_bond: bool = False) -> str:
    """
    Format price as bond 32nds if it looks like a Treasury price.
    
    Args:
        value: Price value
        force_bond: Force bond formatting regardless of value range
        
    Returns:
        Formatted string (32nds if bond, decimal otherwise)
    """
    if force_bond or is_bond_price(value):
        return decimal_to_32nds(value)
    return format_number(value, 4)


# =============================================================================
# BPS Conversion Utilities for Treasury Futures
# =============================================================================

# BPS factors: how many bps per 1 point of price movement
# Based on approximate duration sensitivity
BPS_FACTORS = {
    "ZN": 16,   # 10-year note
    "TY": 16,   # 10-year note (alternate symbol)
    "ZB": 16,   # 30-year bond
    "ZT": 16,   # 2-year note
    "ZF": 16,   # 5-year note
    "UB": 16,   # Ultra bond
    "TN": 16,   # Ultra 10-year
    "FGBL": 100,  # German Bund
    "ES": 1,    # S&P 500 futures
    "NQ": 1,    # Nasdaq futures
}

# Default BPS factor for treasury futures
# 1 point of price movement ≈ 16 bps (duration-based approximation)
DEFAULT_BPS_FACTOR = 16

# ZN Outright Tick Specification (CME):
# Minimum tick = 1/2 of 1/32 = 1/64 of a point = 0.015625 = $15.625
# 1 point = 64 ticks
TICKS_PER_POINT = 64

# For display purposes, prices are quoted in 32nds (with "+" for halves)
DISPLAY_32NDS_PER_POINT = 32


def get_bps_factor(symbol: str) -> int:
    """
    Get BPS factor for a symbol.
    
    Args:
        symbol: Futures symbol (e.g., "ZN", "TY", "ZN.C.0")
        
    Returns:
        BPS factor (bps per 1 point of price movement)
    """
    # Try exact match first
    if symbol in BPS_FACTORS:
        return BPS_FACTORS[symbol]
    
    # Try prefix match (e.g., "ZN.C" matches "ZN")
    for key, value in BPS_FACTORS.items():
        if symbol.startswith(key):
            return value
    
    # Default
    return DEFAULT_BPS_FACTOR


# =============================================================================
# Core Tick Conversions (CME Spec: 1 tick = 1/64 point = 0.015625)
# =============================================================================

def decimal_to_ticks(price_move: float) -> float:
    """
    Convert decimal price movement to ticks.
    
    For ZN Outright: 1 tick = 1/64 point = 0.015625 = $15.625
    
    This is the fundamental conversion based on CME tick spec.
    
    Args:
        price_move: Price movement in decimal
        
    Returns:
        Number of ticks (1/64 of a point for ZN)
        
    Examples:
        >>> decimal_to_ticks(0.015625)  # 1/64 point
        1.0
        >>> decimal_to_ticks(0.0625)    # 1/16 point = 4 ticks = 1 bps
        4.0
        >>> decimal_to_ticks(1.0)       # 1 point
        64.0
    """
    return price_move * TICKS_PER_POINT


def ticks_to_decimal(ticks: float) -> float:
    """
    Convert ticks to decimal price movement.
    
    For ZN Outright: 1 tick = 1/64 point = 0.015625 = $15.625
    
    This is the fundamental conversion based on CME tick spec.
    
    Args:
        ticks: Number of ticks
        
    Returns:
        Price movement in decimal
        
    Examples:
        >>> ticks_to_decimal(1)   # 1 tick
        0.015625
        >>> ticks_to_decimal(4)   # 4 ticks = 1 bps
        0.0625
        >>> ticks_to_decimal(64)  # 64 ticks = 1 point
        1.0
    """
    return ticks / TICKS_PER_POINT


# =============================================================================
# Tick <-> BPS Conversions (4 ticks = 1 bps for ZN)
# =============================================================================

# Ticks per basis point (derived: TICKS_PER_POINT / BPS_FACTOR = 64 / 16 = 4)
TICKS_PER_BPS = TICKS_PER_POINT // DEFAULT_BPS_FACTOR  # = 4


def ticks_to_bps(ticks: float) -> float:
    """
    Convert ticks to basis points.
    
    For ZN Outright: 4 ticks = 1 bps
    
    Args:
        ticks: Number of ticks (1/64 of a point for ZN)
        
    Returns:
        Movement in basis points
        
    Examples:
        >>> ticks_to_bps(1)   # 1 tick
        0.25
        >>> ticks_to_bps(4)   # 4 ticks = 1 bps
        1.0
        >>> ticks_to_bps(64)  # 64 ticks = 1 point = 16 bps
        16.0
    """
    return ticks / TICKS_PER_BPS


def bps_to_ticks(bps_move: float) -> float:
    """
    Convert basis points to ticks.
    
    For ZN Outright: 1 bps = 4 ticks
    
    Args:
        bps_move: Movement in basis points
        
    Returns:
        Number of ticks (1/64 of a point for ZN)
        
    Examples:
        >>> bps_to_ticks(0.25)  # 0.25 bps
        1.0  # 1 tick
        >>> bps_to_ticks(1.0)   # 1 bps
        4.0  # 4 ticks
        >>> bps_to_ticks(16.0)  # 16 bps = 1 point
        64.0  # 64 ticks
    """
    return bps_move * TICKS_PER_BPS


# =============================================================================
# Decimal <-> BPS Conversions (via ticks for clarity)
# =============================================================================

def decimal_to_bps(price_move: float) -> float:
    """
    Convert decimal price movement to basis points.
    
    Conversion path: decimal → ticks → bps
    
    For ZN (10-Year T-Note):
    - decimal → ticks: multiply by 64 (TICKS_PER_POINT)
    - ticks → bps: divide by 4 (TICKS_PER_BPS)
    
    Args:
        price_move: Price movement in decimal (e.g., 0.5 = half a point)
        
    Returns:
        Movement in basis points
        
    Examples:
        >>> decimal_to_bps(1.0)       # 1 point → 64 ticks → 16 bps
        16.0
        >>> decimal_to_bps(0.0625)    # 1/16 point → 4 ticks → 1 bps
        1.0
        >>> decimal_to_bps(0.015625)  # 1 tick → 0.25 bps
        0.25
    """
    ticks = decimal_to_ticks(price_move)
    return ticks_to_bps(ticks)


def bps_to_decimal(bps_move: float) -> float:
    """
    Convert basis points to decimal price movement.
    
    Conversion path: bps → ticks → decimal
    
    For ZN (10-Year T-Note):
    - bps → ticks: multiply by 4 (TICKS_PER_BPS)
    - ticks → decimal: divide by 64 (TICKS_PER_POINT)
    
    Args:
        bps_move: Movement in basis points
        
    Returns:
        Price movement in decimal
        
    Examples:
        >>> bps_to_decimal(16.0)  # 16 bps → 64 ticks → 1 point
        1.0
        >>> bps_to_decimal(1.0)   # 1 bps → 4 ticks → 0.0625
        0.0625
        >>> bps_to_decimal(0.25)  # 0.25 bps → 1 tick → 0.015625
        0.015625
    """
    ticks = bps_to_ticks(bps_move)
    return ticks_to_decimal(ticks)




