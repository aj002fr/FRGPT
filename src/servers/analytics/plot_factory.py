"""
Unified Plotting Interface - Auto-detects plot type from data.

Provides a single entry point for all plotting:
- create_plot(data, context, title, output_dir)

Context handlers map query contexts to appropriate core plotting functions.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from .core_plotting import (
    plot_distribution,
    plot_timeseries,
    plot_categorical,
    plot_correlation,
    plot_multi_distribution,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Context Handlers
# =============================================================================

def _handle_event_distribution(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle event distribution context.
    
    Expected data format:
    {
        "absolute_returns": [1.2, 3.4, ...],
        "returns": [0.5, -0.2, ...],          # Optional
        "volatility_returns": [2.1, 1.8, ...], # Optional
        "latest": {"absolute_return": 5.0, ...},  # Optional
        "metadata": {"symbol": "ZN", "event_name": "NFP", ...}
    }
    """
    plot_paths = []
    
    # Extract data
    abs_returns = data.get("absolute_returns", data.get("values", []))
    returns = data.get("returns", [])
    vol_returns = data.get("volatility_returns", [])
    latest = data.get("latest", {})
    metadata = data.get("metadata", {})
    
    symbol = metadata.get("symbol", "")
    event_name = metadata.get("event_name", "")
    
    # Determine which distributions to plot
    distributions = {}
    highlight_values = {}
    
    if abs_returns:
        distributions["Absolute Return"] = abs_returns
        if "absolute_return" in latest:
            highlight_values["Absolute Return"] = latest["absolute_return"]
    
    if returns:
        distributions["Return"] = returns
        if "return" in latest:
            highlight_values["Return"] = latest["return"]
    
    if vol_returns:
        distributions["Volatility Return"] = vol_returns
        if "volatility_return" in latest:
            highlight_values["Volatility Return"] = latest["volatility_return"]
    
    if not distributions:
        return {"success": False, "error": "No distribution data provided", "plot_paths": []}
    
    # Generate multi-distribution plot if multiple distributions
    if len(distributions) > 1:
        result = plot_multi_distribution(
            data_dict=distributions,
            title=title or f"{symbol} - {event_name} Distribution",
            x_label="BPS",
            highlight_values=highlight_values,
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    else:
        # Single distribution
        name, values = list(distributions.items())[0]
        highlight = highlight_values.get(name)
        
        result = plot_distribution(
            values=values,
            title=title or f"{symbol} - {event_name} {name}",
            x_label="BPS",
            y_label="Frequency",
            highlight_value=highlight,
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    return {
        "success": len(plot_paths) > 0,
        "plot_paths": plot_paths,
        "metadata": {
            "context": "event_distribution",
            "distributions_plotted": list(distributions.keys()),
        }
    }


def _handle_returns_distribution(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle returns distribution context (session-based).
    
    Expected data format:
    {
        "sessions": {
            "London 0-7 ET": [1.2, 3.4, ...],
            "US Open 7-10 ET": [2.1, 1.8, ...],
            ...
        },
        "all_day": [0.5, -0.2, ...],
        "latest": {"return": 3.2, "session": "US Open", ...},
        "metadata": {"symbol": "ZN", ...}
    }
    """
    plot_paths = []
    
    sessions = data.get("sessions", {})
    all_day = data.get("all_day", data.get("values", []))
    latest = data.get("latest", {})
    metadata = data.get("metadata", {})
    
    symbol = metadata.get("symbol", "")
    
    # If we have sessions, use multi-distribution
    if sessions:
        result = plot_multi_distribution(
            data_dict=sessions,
            title=title or f"{symbol} Session Returns Distribution",
            x_label="BPS",
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    # Also plot all-day if available
    if all_day:
        highlight = latest.get("return")
        result = plot_distribution(
            values=all_day,
            title=title or f"{symbol} All-Day Returns Distribution",
            x_label="BPS",
            y_label="Frequency",
            highlight_value=highlight,
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    return {
        "success": len(plot_paths) > 0,
        "plot_paths": plot_paths,
        "metadata": {"context": "returns_distribution"}
    }


def _handle_volatility_distribution(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle volatility distribution context.
    
    Expected data format:
    {
        "sessions": {...},
        "all_day": [...],
        "latest": {"volatility": 5.2, ...},
        "metadata": {"symbol": "ZN", ...}
    }
    """
    # Similar to returns, but for volatility
    plot_paths = []
    
    sessions = data.get("sessions", {})
    all_day = data.get("all_day", data.get("values", []))
    latest = data.get("latest", {})
    metadata = data.get("metadata", {})
    
    symbol = metadata.get("symbol", "")
    
    if sessions:
        result = plot_multi_distribution(
            data_dict=sessions,
            title=title or f"{symbol} Session Volatility Distribution",
            x_label="BPS (High-Low)",
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    if all_day:
        highlight = latest.get("volatility")
        result = plot_distribution(
            values=all_day,
            title=title or f"{symbol} All-Day Volatility Distribution",
            x_label="BPS (High-Low)",
            y_label="Frequency",
            highlight_value=highlight,
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    return {
        "success": len(plot_paths) > 0,
        "plot_paths": plot_paths,
        "metadata": {"context": "volatility_distribution"}
    }


def _handle_candlestick(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle candlestick chart context.
    
    Expected data format:
    {
        "open": [...],
        "high": [...],
        "low": [...],
        "close": [...],
        "timestamps": [...],
        "events": [...],  # Optional
        "metadata": {"symbol": "TY", ...}
    }
    """
    events = data.get("events", [])
    metadata = data.get("metadata", {})
    symbol = metadata.get("symbol", "")
    
    result = plot_timeseries(
        data=data,
        title=title or f"{symbol} Price Chart",
        plot_type="candlestick",
        events=events,
        output_dir=output_dir,
    )
    
    return {
        "success": result.get("success", False),
        "plot_paths": [result["png_path"]] if result.get("success") else [],
        "metadata": result.get("metadata", {})
    }


def _handle_correlation(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle correlation/scatter plot context.
    
    Expected data format:
    {
        "x_values": [0.1, 0.2, ...],
        "y_values": [5.2, 3.1, ...],
        "x_label": "Surprise Percentile",
        "y_label": "Price Return (BPS)",
        "metadata": {"event_name": "NFP", ...}
    }
    """
    x_values = data.get("x_values", [])
    y_values = data.get("y_values", [])
    x_label = data.get("x_label", "X")
    y_label = data.get("y_label", "Y")
    metadata = data.get("metadata", {})
    
    result = plot_correlation(
        x_values=x_values,
        y_values=y_values,
        title=title or f"{metadata.get('event_name', '')} Correlation",
        x_label=x_label,
        y_label=y_label,
        show_regression=True,
        output_dir=output_dir,
    )
    
    return {
        "success": result.get("success", False),
        "plot_paths": [result["png_path"]] if result.get("success") else [],
        "metadata": result.get("metadata", {})
    }


def _handle_surprise_analysis(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle surprise analysis context.
    
    Expected data format:
    {
        "surprise_percentiles": [...],
        "price_returns": [...],
        "price_paths": {...},  # Optional: for path plots
        "metadata": {"event_name": "NFP", ...}
    }
    """
    plot_paths = []
    metadata = data.get("metadata", {})
    event_name = metadata.get("event_name", "Event")
    
    # Plot correlation if we have surprise and returns
    surprise = data.get("surprise_percentiles", [])
    returns = data.get("price_returns", [])
    
    if surprise and returns:
        result = plot_correlation(
            x_values=surprise,
            y_values=returns,
            title=title or f"{event_name} Surprise vs Price Return",
            x_label="Surprise Percentile",
            y_label="Price Return (BPS)",
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    # Also plot return distribution if available
    if returns:
        result = plot_distribution(
            values=[abs(r) for r in returns],
            title=f"{event_name} Absolute Price Return Distribution",
            x_label="Absolute Return (BPS)",
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
    
    return {
        "success": len(plot_paths) > 0,
        "plot_paths": plot_paths,
        "metadata": {"context": "surprise_analysis"}
    }


def _handle_probability_matrix(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle probability matrix context.
    
    Expected data format:
    {
        "buckets": ["<-10", "-10 to 0", "0 to 10", ">10"],
        "probabilities": [0.15, 0.25, 0.35, 0.25],
        "metadata": {...}
    }
    """
    buckets = data.get("buckets", data.get("labels", []))
    probs = data.get("probabilities", data.get("values", []))
    metadata = data.get("metadata", {})
    
    if not buckets or not probs:
        return {"success": False, "error": "No bucket/probability data", "plot_paths": []}
    
    result = plot_categorical(
        values=[p * 100 for p in probs],  # Convert to percentage
        labels=buckets,
        title=title or "Probability Distribution",
        y_label="Probability (%)",
        output_dir=output_dir,
    )
    
    return {
        "success": result.get("success", False),
        "plot_paths": [result["png_path"]] if result.get("success") else [],
        "metadata": result.get("metadata", {})
    }


def _handle_generic(
    data: Dict[str, Any],
    title: str,
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """
    Handle generic/unknown context - auto-detect from data structure.
    """
    plot_paths = []
    
    # Check for OHLC data
    if all(k in data for k in ["open", "high", "low", "close"]):
        result = _handle_candlestick(data, title, output_dir)
        return result
    
    # Check for x/y paired data
    if "x_values" in data and "y_values" in data:
        result = _handle_correlation(data, title, output_dir)
        return result
    
    # Check for labeled values (bar chart)
    if "labels" in data and "values" in data:
        result = plot_categorical(
            values=data["values"],
            labels=data["labels"],
            title=title or "Chart",
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
        return {
            "success": len(plot_paths) > 0,
            "plot_paths": plot_paths,
            "metadata": {"context": "generic_categorical"}
        }
    
    # Check for simple values array (histogram)
    if "values" in data:
        highlight = None
        if "latest" in data and isinstance(data["latest"], dict):
            highlight = data["latest"].get("value", data["latest"].get("return"))
        elif "highlight_value" in data:
            highlight = data["highlight_value"]
        
        result = plot_distribution(
            values=data["values"],
            title=title or "Distribution",
            highlight_value=highlight,
            output_dir=output_dir,
        )
        if result.get("success"):
            plot_paths.append(result["png_path"])
        return {
            "success": len(plot_paths) > 0,
            "plot_paths": plot_paths,
            "metadata": {"context": "generic_distribution"}
        }
    
    return {"success": False, "error": "Could not auto-detect plot type", "plot_paths": []}


# =============================================================================
# Context Handler Registry
# =============================================================================

_CONTEXT_HANDLERS: Dict[str, Callable] = {
    "event_distribution": _handle_event_distribution,
    "returns_distribution": _handle_returns_distribution,
    "volatility_distribution": _handle_volatility_distribution,
    "candlestick": _handle_candlestick,
    "correlation": _handle_correlation,
    "surprise_analysis": _handle_surprise_analysis,
    "probability_matrix": _handle_probability_matrix,
    "generic": _handle_generic,
}


# =============================================================================
# Main Entry Point
# =============================================================================

def create_plot(
    data: Dict[str, Any],
    context: str = "generic",
    title: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Main entry point for all plotting.
    
    Auto-detects appropriate plot type based on context and data structure.
    
    Args:
        data: Data dictionary (format depends on context)
        context: Query context, one of:
            - "event_distribution": histogram for event returns
            - "returns_distribution": session-based returns
            - "volatility_distribution": session-based volatility
            - "candlestick": OHLC candlestick chart
            - "correlation": scatter plot with regression
            - "surprise_analysis": surprise vs price analysis
            - "probability_matrix": probability bar chart
            - "generic": auto-detect from data structure
        title: Plot title (optional, auto-generated if not provided)
        output_dir: Output directory (default: workspace/plots)
        
    Returns:
        {
            "success": True/False,
            "plot_paths": ["path/to/plot1.png", ...],
            "metadata": {...},
            "error": "..." (if failed)
        }
        
    Example:
        # Event distribution
        result = create_plot(
            data={
                "absolute_returns": [1.2, 3.4, 5.6],
                "latest": {"absolute_return": 4.5},
                "metadata": {"symbol": "ZN", "event_name": "NFP"}
            },
            context="event_distribution",
            title="NFP Event Distribution"
        )
        
        # Candlestick
        result = create_plot(
            data={
                "open": [...], "high": [...], "low": [...], "close": [...],
                "timestamps": [...],
                "metadata": {"symbol": "TY"}
            },
            context="candlestick"
        )
    """
    logger.info(f"create_plot called with context='{context}'")
    
    if not data:
        return {"success": False, "error": "No data provided", "plot_paths": []}
    
    # Convert output_dir to Path if string
    if output_dir and isinstance(output_dir, str):
        output_dir = Path(output_dir)
    
    # Get handler for context
    handler = _CONTEXT_HANDLERS.get(context, _handle_generic)
    
    try:
        result = handler(data, title, output_dir)
        
        if result.get("success"):
            logger.info(f"Successfully created {len(result.get('plot_paths', []))} plot(s) for context '{context}'")
        else:
            logger.warning(f"Plot creation failed for context '{context}': {result.get('error', 'Unknown error')}")
        
        return result
        
    except Exception as e:
        logger.exception(f"Error in create_plot for context '{context}': {e}")
        return {"success": False, "error": str(e), "plot_paths": []}


def get_available_contexts() -> List[str]:
    """Return list of available plot contexts."""
    return list(_CONTEXT_HANDLERS.keys())





