"""
Example Analytics Plugin

This is a reference implementation showing how to create a plugin for the analytics server.
Copy this directory and modify it to create your own plugin.

Provides:
- example_summary_stats: Compute basic summary statistics for a dataset
- example_normalize_data: Normalize data to a 0-1 range

Usage:
    from src.servers.analytics.plugins._example import example_summary_stats
    
    result = example_summary_stats(values=[1, 2, 3, 4, 5])
    print(result)
    # {"success": True, "statistics": {"mean": 3.0, "min": 1, "max": 5, ...}}
"""

from src.mcp.discovery import register_tool
from typing import Dict, Any, List, Optional
import logging
import math

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions (internal, not exposed as tools)
# =============================================================================

def _calculate_mean(values: List[float]) -> float:
    """Calculate arithmetic mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _calculate_std(values: List[float], mean: float) -> float:
    """Calculate standard deviation."""
    if len(values) < 2:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


# =============================================================================
# Registered Tools
# =============================================================================

@register_tool(
    name="example_summary_stats",
    description=(
        "Compute basic summary statistics for a numeric dataset. "
        "Returns mean, min, max, range, count, and standard deviation. "
        "This is an example plugin tool demonstrating the plugin pattern."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Array of numeric values to analyze"
            },
            "include_std": {
                "type": "boolean",
                "description": "Whether to include standard deviation (default: True)",
                "default": True
            }
        },
        "required": ["values"]
    },
    agent="analytics_agent"  # Explicit agent assignment
)
def example_summary_stats(
    values: List[float],
    include_std: bool = True
) -> Dict[str, Any]:
    """
    Compute basic summary statistics for a numeric dataset.
    
    Args:
        values: Array of numeric values to analyze
        include_std: Whether to include standard deviation
        
    Returns:
        {
            "success": True,
            "statistics": {
                "count": 5,
                "mean": 3.0,
                "min": 1.0,
                "max": 5.0,
                "range": 4.0,
                "std": 1.58  # if include_std=True
            }
        }
    """
    # Input validation
    if not values:
        return {
            "success": False,
            "error": "values array is empty",
            "statistics": {}
        }
    
    # Ensure all values are numeric
    try:
        numeric_values = [float(v) for v in values]
    except (TypeError, ValueError) as e:
        return {
            "success": False,
            "error": f"Invalid numeric value: {e}",
            "statistics": {}
        }
    
    # Calculate statistics
    try:
        mean = _calculate_mean(numeric_values)
        min_val = min(numeric_values)
        max_val = max(numeric_values)
        
        stats = {
            "count": len(numeric_values),
            "mean": round(mean, 4),
            "min": min_val,
            "max": max_val,
            "range": round(max_val - min_val, 4),
        }
        
        if include_std:
            stats["std"] = round(_calculate_std(numeric_values, mean), 4)
        
        logger.info(f"Computed summary stats for {len(numeric_values)} values")
        
        return {
            "success": True,
            "statistics": stats,
            "metadata": {
                "tool": "example_summary_stats",
                "input_count": len(values)
            }
        }
        
    except Exception as e:
        logger.exception(f"Error computing summary stats: {e}")
        return {
            "success": False,
            "error": str(e),
            "statistics": {}
        }


@register_tool(
    name="example_normalize_data",
    description=(
        "Normalize numeric data to a 0-1 range using min-max scaling. "
        "Useful for comparing datasets with different scales. "
        "This is an example plugin tool demonstrating the plugin pattern."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Array of numeric values to normalize"
            },
            "clip": {
                "type": "boolean",
                "description": "Clip values to [0, 1] range (default: True)",
                "default": True
            }
        },
        "required": ["values"]
    },
    agent="analytics_agent"
)
def example_normalize_data(
    values: List[float],
    clip: bool = True
) -> Dict[str, Any]:
    """
    Normalize data to 0-1 range using min-max scaling.
    
    Args:
        values: Array of numeric values to normalize
        clip: Whether to clip values to [0, 1] range
        
    Returns:
        {
            "success": True,
            "normalized": [0.0, 0.25, 0.5, 0.75, 1.0],
            "original_range": {"min": 1.0, "max": 5.0}
        }
    """
    # Input validation
    if not values:
        return {
            "success": False,
            "error": "values array is empty",
            "normalized": []
        }
    
    if len(values) < 2:
        return {
            "success": False,
            "error": "Need at least 2 values for normalization",
            "normalized": []
        }
    
    try:
        numeric_values = [float(v) for v in values]
    except (TypeError, ValueError) as e:
        return {
            "success": False,
            "error": f"Invalid numeric value: {e}",
            "normalized": []
        }
    
    try:
        min_val = min(numeric_values)
        max_val = max(numeric_values)
        range_val = max_val - min_val
        
        if range_val == 0:
            # All values are the same
            normalized = [0.5] * len(numeric_values)
        else:
            normalized = [(v - min_val) / range_val for v in numeric_values]
        
        if clip:
            normalized = [max(0.0, min(1.0, v)) for v in normalized]
        
        # Round for cleaner output
        normalized = [round(v, 6) for v in normalized]
        
        logger.info(f"Normalized {len(numeric_values)} values")
        
        return {
            "success": True,
            "normalized": normalized,
            "original_range": {
                "min": min_val,
                "max": max_val
            },
            "metadata": {
                "tool": "example_normalize_data",
                "input_count": len(values),
                "clipped": clip
            }
        }
        
    except Exception as e:
        logger.exception(f"Error normalizing data: {e}")
        return {
            "success": False,
            "error": str(e),
            "normalized": []
        }

