"""
Core Plotting Functions - PNG only, config-driven.

All functions:
1. Accept standardized data structures
2. Read styling from config/plot_config.yaml
3. Return PNG path + metadata
4. Are data-agnostic (work with any numeric data)

Four core functions:
- plot_distribution: histograms, KDE, log-scale distributions
- plot_timeseries: candlesticks, line charts
- plot_categorical: bar charts
- plot_correlation: scatter plots
"""

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .schema import (
    get_plots_dir,
    validate_numeric_data,
    is_bond_price,
    decimal_to_32nds,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Loading
# =============================================================================

_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """Load plot configuration from YAML file."""
    global _CONFIG_CACHE
    
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    
    try:
        import yaml
        config_path = Path(__file__).parent.parent.parent.parent / "config" / "plot_config.yaml"
        
        if config_path.exists():
            with open(config_path, 'r') as f:
                _CONFIG_CACHE = yaml.safe_load(f)
                logger.debug(f"Loaded plot config from {config_path}")
                return _CONFIG_CACHE
    except ImportError:
        logger.warning("PyYAML not installed, using default config")
    except Exception as e:
        logger.warning(f"Failed to load plot config: {e}, using defaults")
    
    # Default config if YAML loading fails
    _CONFIG_CACHE = _get_default_config()
    return _CONFIG_CACHE


def _get_default_config() -> Dict[str, Any]:
    """Return default configuration."""
    return {
        "output": {"format": "png", "dpi": 150, "scale": 2},
        "colors": {
            "default": {
                "background": "#FFFFFF",
                "primary": "#2E86AB",
                "secondary": "#E94F37",
                "accent": "#F6AE2D",
                "text": "#333333",
                "grid": "#E0E0E0",
                "axis": "#666666",
                "bullish": "#26A69A",
                "bearish": "#EF5350",
            }
        },
        "fonts": {
            "family": "Segoe UI, Arial, sans-serif",
            "title_size": 14,
            "label_size": 12,
            "tick_size": 10,
            "legend_size": 11,
        },
        "dimensions": {
            "histogram": {"width": 800, "height": 500, "bins": 20},
            "candlestick": {"width": 1600, "height": 600},
            "line_chart": {"width": 800, "height": 400},
            "scatter": {"width": 800, "height": 600, "point_size": 5},
            "bar_chart": {"width": 800, "height": 400},
        },
        "histogram": {"show_kde": True, "kde_color": "secondary", "edge_color": "white", "alpha": 0.7},
        "candlestick": {"auto_fetch_events": True, "show_event_labels": True, "event_line_style": "dash"},
        "timeseries": {"show_points": False, "line_width": 2},
        "stats_box": {"show": True, "position": "top_left", "alpha": 0.9},
    }


def _get_colors(scheme: str = "default") -> Dict[str, str]:
    """Get color scheme from config."""
    config = _load_config()
    schemes = config.get("colors", {})
    return schemes.get(scheme, schemes.get("default", {}))


def _get_dimensions(plot_type: str) -> Dict[str, Any]:
    """Get dimensions for a plot type."""
    config = _load_config()
    dims = config.get("dimensions", {})
    return dims.get(plot_type, {"width": 800, "height": 500})


def _get_output_config() -> Dict[str, Any]:
    """Get output configuration."""
    config = _load_config()
    return config.get("output", {"format": "png", "dpi": 150, "scale": 2})


def _ensure_output_dir(output_dir: Optional[Path] = None) -> Path:
    """Ensure output directory exists and return it."""
    if output_dir is None:
        output_dir = get_plots_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _generate_filename(prefix: str, output_dir: Path) -> Path:
    """Generate unique filename with timestamp."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return output_dir / f"{prefix}_{timestamp}.png"


# =============================================================================
# Event Fetching Helper
# =============================================================================

def _fetch_events_for_timerange(
    timestamps: List[str],
    country: str = "US"
) -> List[Dict[str, Any]]:
    """
    Fetch economic events that occurred within the given timestamp range.
    
    Args:
        timestamps: List of ISO timestamp strings from the chart data
        country: Country code to filter events (default "US")
        
    Returns:
        List of events with timestamp, name, and importance
    """
    try:
        import sqlite3
        
        # Map country codes to full names used in DB
        country_map = {
            "US": "United States",
            "GB": "United Kingdom", 
            "UK": "United Kingdom",
            "DE": "Germany",
            "JP": "Japan",
            "CN": "China",
            "EU": "Euro Area",
        }
        country_name = country_map.get(country.upper(), country)
        
        # Get events database path (check multiple locations)
        project_root = Path(__file__).parent.parent.parent.parent
        db_path = project_root / "workspace" / "economic_events.db"
        
        # Fallback to data/ directory
        if not db_path.exists():
            db_path = project_root / "data" / "economic_events.db"
        
        if not db_path.exists():
            logger.debug(f"Events database not found at {db_path}")
            return []
        
        # Parse time range from timestamps
        if not timestamps:
            return []
        
        # Get min/max timestamps
        start_ts = min(timestamps)
        end_ts = max(timestamps)
        
        # Extract date portion for query
        start_date = start_ts[:10] if len(start_ts) >= 10 else start_ts
        end_date = end_ts[:10] if len(end_ts) >= 10 else end_ts
        
        logger.debug(f"Fetching events for {country_name} between {start_date} and {end_date}")
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Query events in the time range.
        #
        # NOTE: We intentionally do NOT filter to only "high" importance here.
        # The orchestrator's `generate_candlestick_chart` soft dependency fetches
        # *all* events (importance=None), and users expect those markers to show
        # up even when `plot_candlestick` is relying on auto-fetch.
        query = """
            SELECT event_name, event_date, importance
            FROM economic_events
            WHERE country = ?
              AND event_date >= ?
              AND event_date <= ?
            ORDER BY event_date
        """
        cursor.execute(query, (country_name, start_date, end_date + "T23:59:59"))
        rows = cursor.fetchall()
        conn.close()
        
        events = []
        for row in rows:
            events.append({
                "timestamp": row["event_date"],
                "name": row["event_name"],
                "importance": (row["importance"] or "medium"),
            })
        
        logger.debug(f"Found {len(events)} events in time range")
        return events
        
    except Exception as e:
        logger.debug(f"Failed to fetch events: {e}")
        return []


# =============================================================================
# Statistics Helpers
# =============================================================================

def _compute_statistics(values: List[float]) -> Dict[str, float]:
    """Compute basic statistics for values."""
    if not values:
        return {}
    
    n = len(values)
    sorted_vals = sorted(values)
    mean = sum(values) / n
    
    # Variance and std dev
    variance = sum((x - mean) ** 2 for x in values) / n if n > 1 else 0
    std = math.sqrt(variance)
    
    # Median
    if n % 2 == 0:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    else:
        median = sorted_vals[n // 2]
    
    # Skewness and kurtosis (simplified)
    if std > 0:
        skewness = sum(((x - mean) / std) ** 3 for x in values) / n
        kurtosis = sum(((x - mean) / std) ** 4 for x in values) / n - 3
    else:
        skewness = 0
        kurtosis = 0
    
    return {
        "count": n,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "median": round(median, 4),
        "skewness": round(skewness, 4),
        "kurtosis": round(kurtosis, 4),
    }


def _percentile_of_value(values: List[float], value: float) -> float:
    """Calculate percentile rank of a value in a list."""
    if not values:
        return 0
    count_below = sum(1 for v in values if v < value)
    count_equal = sum(1 for v in values if v == value)
    return (count_below + 0.5 * count_equal) / len(values) * 100




def plot_distribution(
    values: List[float],
    title: str,
    x_label: str = "Value",
    y_label: str = "Frequency",
    plot_type: str = "histogram",
    highlight_value: Optional[float] = None,
    stats_box: bool = True,
    stats_lines: Optional[List[str]] = None,
    vertical_lines: Optional[List[Dict[str, Any]]] = None,
    overlay_values: Optional[List[float]] = None,
    overlay_label: str = "Overlay",
    color_scheme: str = "default",
    output_dir: Optional[Path] = None,
    bins: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Generate distribution plot (histogram + optional KDE).
    
    Args:
        values: List of numeric values to plot
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        plot_type: "histogram", "kde", or "log_histogram"
        highlight_value: Value to highlight (e.g., latest observation)
        stats_box: Whether to show statistics box
        color_scheme: Color scheme name
        output_dir: Output directory (default: workspace/plots)
        bins: Number of histogram bins (default from config)
        
    Returns:
        {
            "success": True,
            "png_path": "...",
            "metadata": {"plot_type": "histogram", "data_points": N, ...}
        }
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"success": False, "error": "matplotlib not installed"}
    
    if not values or len(values) < 2:
        return {"success": False, "error": f"Insufficient data points: {len(values) if values else 0}"}
    
    logger.info(f"Generating {plot_type} distribution plot for {len(values)} values")
    
    try:
        # Get configuration
        config = _load_config()
        colors = _get_colors(color_scheme)
        dims = _get_dimensions("histogram")
        hist_config = config.get("histogram", {})
        stats_config = config.get("stats_box", {})
        output_config = _get_output_config()
        
        if bins is None:
            bins = dims.get("bins", 20)
        
        # Convert to numpy array
        data = np.array(values)
        
        # Apply log transform if requested
        if plot_type == "log_histogram":
            data = np.log10(data + 1)
            x_label = f"Log₁₀({x_label} + 1)"
        
        # Create figure
        fig, ax = plt.subplots(figsize=(dims["width"] / 100, dims["height"] / 100))
        fig.patch.set_facecolor(colors.get("background", "#FFFFFF"))
        ax.set_facecolor(colors.get("background", "#FFFFFF"))
        
        # Plot histogram
        n, bin_edges, patches = ax.hist(
            data,
            bins=bins,
            color=colors.get("primary", "#2E86AB"),
            alpha=hist_config.get("alpha", 0.7),
            edgecolor=hist_config.get("edge_color", "white"),
            linewidth=0.5,
        )
        
        # Add KDE if configured
        if hist_config.get("show_kde", True) and plot_type != "kde":
            try:
                from scipy.stats import gaussian_kde
                kde = gaussian_kde(data)
                x_kde = np.linspace(data.min(), data.max(), 200)
                y_kde = kde(x_kde)
                
                # Scale KDE to match histogram
                y_kde_scaled = y_kde * len(data) * (bin_edges[1] - bin_edges[0])
                
                ax2 = ax.twinx()
                kde_color_key = hist_config.get("kde_color", "secondary")
                kde_color = colors.get(kde_color_key, colors.get("secondary", "#E94F37"))
                ax2.plot(x_kde, y_kde, color=kde_color, linewidth=2, label="KDE")
                ax2.set_ylabel("Density", fontsize=10, color=kde_color)
                ax2.tick_params(axis='y', labelcolor=kde_color)
            except ImportError:
                logger.debug("scipy not available for KDE")

        legend_needed = False

        # Overlay markers (e.g., Friday subset) as a rug plot at the baseline
        if overlay_values:
            overlay_color = colors.get("accent", "#F6AE2D")
            ax.scatter(
                overlay_values,
                [0] * len(overlay_values),
                color=overlay_color,
                marker="|",
                s=120,
                linewidths=2,
                label=overlay_label,
                alpha=0.9,
            )
            legend_needed = True
        
        # Highlight value if provided (marks the most recent/latest event)
        if highlight_value is not None:
            highlight_x = highlight_value
            if plot_type == "log_histogram":
                highlight_x = np.log10(highlight_value + 1)
            
            ax.axvline(x=highlight_x, color=colors.get("accent", "#F6AE2D"), 
                      linestyle=':', linewidth=2, alpha=0.8, label='Latest Event')
            
            pct = _percentile_of_value(data.tolist(), highlight_x)
            y_max = ax.get_ylim()[1]
            ax.annotate(
                f"Latest Event: {highlight_value:.2f}\n%ile: {pct:.0f}%",
                xy=(highlight_x, y_max * 0.85),
                fontsize=9,
                fontweight='bold',
                color=colors.get("accent", "#F6AE2D"),
                ha='center',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', 
                         edgecolor=colors.get("accent", "#F6AE2D"), alpha=0.9)
            )
            legend_needed = True

        if vertical_lines:
            y_max = ax.get_ylim()[1]
            for idx, line in enumerate(vertical_lines):
                raw_x = float(line.get("value", 0.0))
                line_x = raw_x
                if plot_type == "log_histogram":
                    line_x = np.log10(raw_x + 1)
                line_color = line.get("color", colors.get("accent", "#F6AE2D"))
                ax.axvline(x=line_x, color=line_color, linestyle='--', linewidth=1.5, alpha=0.9)
                label = line.get("label")
                if label:
                    # Place labels near the baseline to avoid stats box overlap.
                    y_pos = y_max * (0.04 + idx * 0.06)
                    ax.annotate(
                        label,
                        xy=(line_x, y_pos),
                        fontsize=9,
                        color=line_color,
                        ha='center',
                        va='bottom',
                        bbox=dict(
                            boxstyle='round,pad=0.25',
                            facecolor='white',
                            edgecolor=line_color,
                            alpha=0.9,
                        ),
                    )
        
        if legend_needed:
            ax.legend(loc='upper right', fontsize=8)

        # Add stats box if configured
        if stats_box and stats_config.get("show", True):
            if stats_lines:
                stats_text = "\n".join(stats_lines)
            else:
                stats = _compute_statistics(values)
                stats_text = (
                    f"N = {stats['count']}\n"
                    f"Mean = {stats['mean']:.2f}\n"
                    f"Std = {stats['std']:.2f}\n"
                    f"Median = {stats['median']:.2f}\n"
                    f"Skew = {stats['skewness']:.2f}\n"
                    f"Kurt = {stats['kurtosis']:.2f}"
                )
            
            pos = stats_config.get("position", "top_left")
            if pos == "top_left":
                x_pos, y_pos, ha, va = 0.02, 0.98, "left", "top"
            elif pos == "top_right":
                x_pos, y_pos, ha, va = 0.98, 0.98, "right", "top"
            else:
                x_pos, y_pos, ha, va = 0.02, 0.98, "left", "top"
            
            ax.text(x_pos, y_pos, stats_text, transform=ax.transAxes, fontsize=9,
                   verticalalignment=va, horizontalalignment=ha,
                   bbox=dict(boxstyle='round,pad=0.4', facecolor='white', 
                            edgecolor=colors.get("grid", "#E0E0E0"), 
                            alpha=stats_config.get("alpha", 0.9)))
        
        # Labels and title
        ax.set_xlabel(x_label, fontsize=11, color=colors.get("text", "#333333"))
        ax.set_ylabel(y_label, fontsize=11, color=colors.get("text", "#333333"))
        ax.set_title(title, fontsize=13, fontweight='bold', color=colors.get("text", "#333333"))
        ax.grid(True, alpha=0.3, color=colors.get("grid", "#E0E0E0"))
        ax.tick_params(colors=colors.get("axis", "#666666"))
        
        plt.tight_layout()
        
        # Save to file
        output_dir = _ensure_output_dir(output_dir)
        png_path = _generate_filename("distribution", output_dir)
        fig.savefig(str(png_path), dpi=output_config.get("dpi", 150), 
                   bbox_inches='tight', facecolor=colors.get("background", "#FFFFFF"))
        plt.close(fig)
        
        logger.info(f"Saved distribution plot to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path),
            "metadata": {
                "plot_type": plot_type,
                "title": title,
                "data_points": len(values),
                "bins": bins,
                "statistics": _compute_statistics(values),
            }
        }
        
    except Exception as e:
        logger.exception(f"Error generating distribution plot: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Core Function 2: Timeseries Plots
# =============================================================================

def plot_timeseries(
    data: Dict[str, Any],
    title: str,
    plot_type: str = "auto",
    x_label: str = "Time",
    y_label: str = "Price",
    events: Optional[List[Dict[str, Any]]] = None,
    color_scheme: str = "default",
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generate timeseries plot (candlestick or line).
    
    Auto-detects:
    - If data has open/high/low/close → candlestick
    - If data has y_values only → line chart
    
    Args:
        data: Dict with OHLC arrays or y_values
            OHLC format: {"open": [], "high": [], "low": [], "close": [], "timestamps": []}
            Line format: {"y_values": [], "timestamps": []} or {"y_values": [], "x_values": []}
        title: Chart title
        plot_type: "auto", "candlestick", or "line"
        x_label: X-axis label
        y_label: Y-axis label
        events: Optional list of events to mark
        color_scheme: Color scheme name
        output_dir: Output directory
        
    Returns:
        {
            "success": True,
            "png_path": "...",
            "metadata": {"plot_type": "candlestick", "num_points": N, ...}
        }
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {"success": False, "error": "plotly not installed. Run: pip install plotly kaleido"}
    
    logger.info(f"Generating timeseries plot: {title}")
    
    # Auto-detect plot type
    has_ohlc = all(k in data for k in ["open", "high", "low", "close"])
    
    if plot_type == "auto":
        plot_type = "candlestick" if has_ohlc else "line"
    
    try:
        if plot_type == "candlestick" and has_ohlc:
            return plot_candlestick(
                open_data=data["open"],
                high_data=data["high"],
                low_data=data["low"],
                close_data=data["close"],
                timestamps=data.get("timestamps"),
                events=events,
                title=title,
                x_label=x_label,
                y_label=y_label,
                output_dir=output_dir,
            )
        else:
            colors = _get_colors(color_scheme)
            output_config = _get_output_config()
            return _plot_line(data, title, x_label, y_label, colors, output_config, output_dir)
        
    except Exception as e:
        logger.exception(f"Error generating timeseries plot: {e}")
        return {"success": False, "error": str(e)}


def plot_candlestick(
    open_data: List[Any],
    high_data: List[Any],
    low_data: List[Any],
    close_data: List[Any],
    timestamps: Optional[List[Any]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    show_event_labels: bool = True,
    auto_fetch_events: bool = False,
    event_country: str = "US",
    title: str = "Candlestick Chart",
    x_label: str = "Time",
    y_label: str = "Price",
    theme: str = "dark",
    width: int = 1600,
    height: int = 600,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generate a rich candlestick chart with auto-resampling and bond price formatting.
    
    Features:
    - Auto-resample 1m→5m candles for full-day views
    - Auto-detect Treasury futures and show bond prices in 32nds format
    - Dark/light theme support
    - Event clustering with staggered labels
    - Optional auto-fetch of economic events
    
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
        width: Image width in pixels
        height: Image height in pixels
        output_dir: Output directory (default: workspace/plots)
        
    Returns:
        {
            "success": True,
            "png_path": "...",
            "metadata": {"plot_type": "candlestick", "num_candles": N, ...}
        }
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {"success": False, "error": "plotly not installed. Run: pip install plotly kaleido"}
    
    logger.info(f"Generating candlestick chart: {title} with {len(close_data)} candles")
    
    # Validate all OHLC data
    for name, data_arr in [("open", open_data), ("high", high_data), ("low", low_data), ("close", close_data)]:
        is_valid, error, _ = validate_numeric_data(data_arr)
        if not is_valid:
            return {"success": False, "error": f"{name} data: {error}"}
    
    # Convert to numeric
    _, _, numeric_open = validate_numeric_data(open_data)
    _, _, numeric_high = validate_numeric_data(high_data)
    _, _, numeric_low = validate_numeric_data(low_data)
    _, _, numeric_close = validate_numeric_data(close_data)
    
    n = len(numeric_close)
    if not all(len(d) == n for d in [numeric_open, numeric_high, numeric_low]):
        return {"success": False, "error": "All OHLC arrays must have the same length"}
    
    if n == 0:
        return {"success": False, "error": "No data points"}
    
    try:
        # Prepare x-axis data
        if timestamps and len(timestamps) > 0:
            x_data = list(timestamps)
            logger.debug(f"Using {len(timestamps)} timestamps for x-axis")
        else:
            x_data = list(range(n))
            logger.debug(f"No timestamps provided, using indices 0-{n} for x-axis")
        
        # Auto-resample for better candle density
        if timestamps and len(timestamps) > 10:
            def _try_parse_dt(v: Any):
                try:
                    if isinstance(v, str):
                        return datetime.fromisoformat(v)
                except Exception:
                    return None
                return None
            
            dt0 = _try_parse_dt(timestamps[0])
            dtN = _try_parse_dt(timestamps[-1])
            if dt0 and dtN:
                span_seconds = (dtN - dt0).total_seconds()
                
                # Detect typical bar size
                from collections import Counter
                sample_pairs = min(200, len(timestamps) - 1)
                gaps = []
                prev = _try_parse_dt(timestamps[0])
                for i in range(1, sample_pairs + 1):
                    cur = _try_parse_dt(timestamps[i])
                    if not prev or not cur:
                        prev = cur
                        continue
                    g = (cur - prev).total_seconds()
                    if 0 < g <= 3600:
                        gaps.append(g)
                    prev = cur
                
                mode_gap = None
                mode_share = 0.0
                if gaps:
                    c = Counter(int(round(g)) for g in gaps)
                    mode_gap, mode_count = c.most_common(1)[0]
                    mode_share = mode_count / max(1, len(gaps))
                
                is_1mish = (mode_gap is not None and 50 <= mode_gap <= 70 and mode_share >= 0.5)
                should_resample = (span_seconds >= 8 * 3600 and is_1mish)
                
                if should_resample:
                    logger.info("Auto-resampling 1m candles to 5m for full-day readability")
                    
                    def _floor_to_5m(dt: datetime) -> datetime:
                        m = (dt.minute // 5) * 5
                        return dt.replace(minute=m, second=0, microsecond=0)
                    
                    bins: Dict[datetime, Dict[str, Any]] = {}
                    bin_order: List[datetime] = []
                    for i in range(n):
                        dti = _try_parse_dt(timestamps[i])
                        if not dti:
                            continue
                        b = _floor_to_5m(dti)
                        if b not in bins:
                            bins[b] = {
                                "open": numeric_open[i],
                                "high": numeric_high[i],
                                "low": numeric_low[i],
                                "close": numeric_close[i],
                                "ts": b,
                            }
                            bin_order.append(b)
                        else:
                            agg = bins[b]
                            if numeric_high[i] > agg["high"]:
                                agg["high"] = numeric_high[i]
                            if numeric_low[i] < agg["low"]:
                                agg["low"] = numeric_low[i]
                            agg["close"] = numeric_close[i]
                    
                    bin_order.sort()
                    x_data = [bins[b]["ts"].isoformat() for b in bin_order]
                    numeric_open = [bins[b]["open"] for b in bin_order]
                    numeric_high = [bins[b]["high"] for b in bin_order]
                    numeric_low = [bins[b]["low"] for b in bin_order]
                    numeric_close = [bins[b]["close"] for b in bin_order]
                    n = len(numeric_close)
        
        # Calculate price range
        y_min = min(numeric_low)
        y_max = max(numeric_high)
        
        # Detect if this is bond data (Treasury futures)
        use_bond_format = is_bond_price(y_min) and is_bond_price(y_max)
        
        theme_norm = (theme or "dark").strip().lower()
        use_dark = theme_norm in ("dark", "plotly_dark", "night")
        
        # Theme colors
        grid_major_color = "rgba(255,255,255,0.10)" if use_dark else "rgba(0,0,0,0.10)"
        grid_minor_color = "rgba(255,255,255,0.06)" if use_dark else "rgba(0,0,0,0.06)"
        event_line_color = "rgba(255,255,255,0.75)" if use_dark else "black"
        event_text_color = "rgba(255,255,255,0.90)" if use_dark else "black"
        legend_bg = "rgba(0,0,0,0.65)" if use_dark else "rgba(255,255,255,0.8)"
        legend_border = "rgba(255,255,255,0.55)" if use_dark else "black"
        legend_font_color = "rgba(255,255,255,0.92)" if use_dark else "black"
        template_name = "plotly_dark" if use_dark else "plotly_white"
        
        # Auto-fetch events if requested
        if auto_fetch_events and timestamps and len(timestamps) > 0 and not events:
            events = _fetch_events_for_timerange(timestamps, event_country)
            if events:
                logger.info(f"Auto-fetched {len(events)} events for chart time range")
        
        # Create TradingView-style candlestick chart
        fig = go.Figure(data=[go.Candlestick(
            x=x_data,
            open=numeric_open,
            high=numeric_high,
            low=numeric_low,
            close=numeric_close,
            increasing_line_color='#26A69A',
            increasing_fillcolor='#26A69A',
            decreasing_line_color='#EF5350',
            decreasing_fillcolor='#EF5350',
            line=dict(width=0.6),
        )])
        
        # Generate Y-axis tick values for bond format
        y_range = y_max - y_min
        y_padding = y_range * 0.05
        tick_min = y_min - y_padding
        tick_max = y_max + y_padding
        
        if use_bond_format:
            major_step = 1.0 / 16.0
            minor_step = 1.0 / 32.0
            
            major_start = math.floor(tick_min / major_step) * major_step
            major_end = math.ceil(tick_max / major_step) * major_step
            major_count = int(round((major_end - major_start) / major_step)) + 1
            
            if major_count > 400:
                major_count = 400
                major_end = major_start + (major_count - 1) * major_step
            
            tick_vals = [major_start + i * major_step for i in range(major_count)]
            target_labels = 12
            stride = max(1, int(math.ceil(major_count / target_labels)))
            tick_text = []
            for i, v in enumerate(tick_vals):
                if i == 0 or i == (major_count - 1) or (i % stride == 0):
                    tick_text.append(decimal_to_32nds(v))
                else:
                    tick_text.append("")
            
            y_axis_config = dict(
                title=f"{y_label} ",
                tickmode="array",
                tickvals=tick_vals,
                ticktext=tick_text,
                showgrid=True,
                minor=dict(
                    dtick=minor_step,
                    showgrid=True,
                    gridcolor=grid_minor_color,
                    gridwidth=0.5,
                ),
                gridcolor=grid_major_color,
            )
        else:
            y_axis_config = dict(title=y_label, showgrid=True, gridcolor=grid_major_color)
        
        # Update layout
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor='center'),
            xaxis=dict(
                title=x_label,
                rangeslider=dict(visible=False),
                showgrid=True,
                gridcolor=grid_major_color,
            ),
            yaxis=y_axis_config,
            template=template_name,
            width=width,
            height=height,
            margin=dict(l=80, r=40, t=70, b=60),
        )
        
        # Add event markers with clustering
        events_shown = []
        event_legend_items = []
        if events and show_event_labels:
            clustered: Dict[str, List[Dict[str, Any]]] = {}
            for event in events:
                ts = event.get("timestamp")
                if not ts:
                    continue
                clustered.setdefault(ts, []).append(event)
            
            clustered_items = sorted(clustered.items(), key=lambda kv: kv[0])
            
            def _format_event_time(ts: str) -> str:
                try:
                    dt = datetime.fromisoformat(ts)
                    return dt.strftime("%H:%M")
                except Exception:
                    if "T" in ts and len(ts) >= 16:
                        return ts.split("T", 1)[1][:5]
                    return ts[:5]
            
            last_dt = None
            close_stack = 0
            
            for ts, group in clustered_items:
                fig.add_vline(
                    x=ts,
                    line_dash="dash",
                    line_color=event_line_color,
                    line_width=1,
                    opacity=0.6,
                )
                
                t_str = _format_event_time(ts)
                n_group = len(group)
                label = f"{t_str} ({n_group})" if n_group > 1 else t_str
                
                try:
                    dt_ts = datetime.fromisoformat(ts)
                except Exception:
                    dt_ts = None
                
                if dt_ts and last_dt:
                    if abs((dt_ts - last_dt).total_seconds()) <= 20 * 60:
                        close_stack += 1
                    else:
                        close_stack = 0
                else:
                    close_stack = 0
                last_dt = dt_ts or last_dt
                
                fig.add_annotation(
                    x=ts,
                    y=1.01,
                    yref="paper",
                    text=label,
                    showarrow=False,
                    font=dict(size=9, color=event_text_color, family="Arial Black"),
                    xanchor="left",
                    xshift=8,
                    yshift=int(close_stack * 12),
                )
                
                event_legend_items.append(f"{label}:")
                for ev in group:
                    nm = (ev.get("name") or "Event").strip()
                    event_legend_items.append(f"&nbsp;&nbsp;• {nm[:40]}")
                
                for ev in group:
                    events_shown.append({
                        "timestamp": ts,
                        "name": ev.get("name", "Event"),
                        "importance": (ev.get("importance", "medium") or "medium").lower(),
                    })
            
            logger.info(f"Added {len(events_shown)} event markers to chart")
            
            if event_legend_items:
                legend_text = "<br>".join(event_legend_items)
                fig.add_annotation(
                    x=0.99,
                    y=0.99,
                    xref="paper",
                    yref="paper",
                    text=legend_text,
                    showarrow=False,
                    font=dict(size=8, color=legend_font_color, family="Arial"),
                    align="left",
                    xanchor="right",
                    yanchor="top",
                    bgcolor=legend_bg,
                    bordercolor=legend_border,
                    borderwidth=1,
                    borderpad=4,
                )
        
        # Save to file
        output_dir = _ensure_output_dir(output_dir)
        png_path = _generate_filename("candlestick", output_dir)
        fig.write_image(str(png_path), format='png', scale=3)
        
        logger.info(f"Saved candlestick chart to {png_path}")
        
        # Format price range for metadata
        if use_bond_format:
            price_range_formatted = {
                "min": decimal_to_32nds(y_min),
                "max": decimal_to_32nds(y_max),
                "min_decimal": y_min,
                "max_decimal": y_max,
            }
        else:
            price_range_formatted = {"min": y_min, "max": y_max}
        
        return {
            "success": True,
            "png_path": str(png_path),
            "metadata": {
                "plot_type": "candlestick",
                "title": title,
                "width": width,
                "height": height,
                "num_candles": n,
                "price_range": price_range_formatted,
                "bond_format": use_bond_format,
                "events_shown": events_shown if events_shown else None,
                "num_events": len(events_shown) if events_shown else 0,
            }
        }
        
    except Exception as e:
        logger.exception(f"Error generating candlestick chart: {e}")
        return {"success": False, "error": str(e)}


def _plot_line(
    data: Dict[str, Any],
    title: str,
    x_label: str,
    y_label: str,
    colors: Dict[str, str],
    output_config: Dict[str, Any],
    output_dir: Optional[Path],
) -> Dict[str, Any]:
    """Generate line chart using matplotlib."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    
    config = _load_config()
    dims = _get_dimensions("line_chart")
    ts_config = config.get("timeseries", {})
    
    y_values = data.get("y_values", data.get("close", []))
    x_values = data.get("x_values", data.get("timestamps", list(range(len(y_values)))))
    
    if not y_values:
        return {"success": False, "error": "No y_values in data"}
    
    fig, ax = plt.subplots(figsize=(dims["width"] / 100, dims["height"] / 100))
    fig.patch.set_facecolor(colors.get("background", "#FFFFFF"))
    ax.set_facecolor(colors.get("background", "#FFFFFF"))
    
    ax.plot(x_values, y_values, 
           color=colors.get("primary", "#2E86AB"),
           linewidth=ts_config.get("line_width", 2))
    
    if ts_config.get("show_points", False):
        ax.scatter(x_values, y_values, color=colors.get("primary", "#2E86AB"), s=20)
    
    ax.set_xlabel(x_label, fontsize=11, color=colors.get("text", "#333333"))
    ax.set_ylabel(y_label, fontsize=11, color=colors.get("text", "#333333"))
    ax.set_title(title, fontsize=13, fontweight='bold', color=colors.get("text", "#333333"))
    ax.grid(True, alpha=0.3, color=colors.get("grid", "#E0E0E0"))
    
    plt.tight_layout()
    
    output_dir = _ensure_output_dir(output_dir)
    png_path = _generate_filename("line_chart", output_dir)
    fig.savefig(str(png_path), dpi=output_config.get("dpi", 150), 
               bbox_inches='tight', facecolor=colors.get("background", "#FFFFFF"))
    plt.close(fig)
    
    logger.info(f"Saved line chart to {png_path}")
    
    return {
        "success": True,
        "png_path": str(png_path),
        "metadata": {
            "plot_type": "line",
            "title": title,
            "num_points": len(y_values),
        }
    }


# =============================================================================
# Core Function 3: Categorical Plots
# =============================================================================

def plot_categorical(
    values: List[float],
    labels: List[str],
    title: str,
    plot_type: str = "bar",
    x_label: str = "",
    y_label: str = "Value",
    color_scheme: str = "default",
    output_dir: Optional[Path] = None,
    horizontal: bool = False,
) -> Dict[str, Any]:
    """
    Generate categorical/bar chart.
    
    Args:
        values: Bar heights/values
        labels: Category labels
        title: Chart title
        plot_type: "bar", "horizontal_bar"
        x_label: X-axis label
        y_label: Y-axis label
        color_scheme: Color scheme name
        output_dir: Output directory
        horizontal: If True, create horizontal bar chart
        
    Returns:
        {
            "success": True,
            "png_path": "...",
            "metadata": {"plot_type": "bar", "categories": N, ...}
        }
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"success": False, "error": "matplotlib not installed"}
    
    if not values or not labels:
        return {"success": False, "error": "Empty values or labels"}
    
    if len(values) != len(labels):
        return {"success": False, "error": "values and labels must have same length"}
    
    logger.info(f"Generating bar chart: {title} with {len(values)} categories")
    
    try:
        colors = _get_colors(color_scheme)
        dims = _get_dimensions("bar_chart")
        output_config = _get_output_config()
        
        fig, ax = plt.subplots(figsize=(dims["width"] / 100, dims["height"] / 100))
        fig.patch.set_facecolor(colors.get("background", "#FFFFFF"))
        ax.set_facecolor(colors.get("background", "#FFFFFF"))
        
        x = np.arange(len(labels))
        
        if horizontal or plot_type == "horizontal_bar":
            ax.barh(x, values, color=colors.get("primary", "#2E86AB"), 
                   edgecolor=colors.get("axis", "#666666"), linewidth=0.5)
            ax.set_yticks(x)
            ax.set_yticklabels(labels)
            ax.set_xlabel(y_label, fontsize=11, color=colors.get("text", "#333333"))
            ax.set_ylabel(x_label, fontsize=11, color=colors.get("text", "#333333"))
        else:
            ax.bar(x, values, color=colors.get("primary", "#2E86AB"),
                  edgecolor=colors.get("axis", "#666666"), linewidth=0.5)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=45, ha='right')
            ax.set_xlabel(x_label, fontsize=11, color=colors.get("text", "#333333"))
            ax.set_ylabel(y_label, fontsize=11, color=colors.get("text", "#333333"))
        
        ax.set_title(title, fontsize=13, fontweight='bold', color=colors.get("text", "#333333"))
        ax.grid(True, alpha=0.3, axis='y' if not horizontal else 'x', 
               color=colors.get("grid", "#E0E0E0"))
        ax.tick_params(colors=colors.get("axis", "#666666"))
        
        plt.tight_layout()
        
        output_dir = _ensure_output_dir(output_dir)
        png_path = _generate_filename("bar_chart", output_dir)
        fig.savefig(str(png_path), dpi=output_config.get("dpi", 150),
                   bbox_inches='tight', facecolor=colors.get("background", "#FFFFFF"))
        plt.close(fig)
        
        logger.info(f"Saved bar chart to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path),
            "metadata": {
                "plot_type": "horizontal_bar" if horizontal else "bar",
                "title": title,
                "categories": len(labels),
                "labels": labels,
            }
        }
        
    except Exception as e:
        logger.exception(f"Error generating bar chart: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Core Function 4: Correlation Plots
# =============================================================================

def plot_correlation(
    x_values: List[float],
    y_values: List[float],
    title: str,
    x_label: str = "X",
    y_label: str = "Y",
    plot_type: str = "scatter",
    show_regression: bool = True,
    color_scheme: str = "default",
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generate correlation/scatter plot.
    
    Args:
        x_values: X-axis values
        y_values: Y-axis values
        title: Chart title
        x_label: X-axis label
        y_label: Y-axis label
        plot_type: "scatter" or "regression"
        show_regression: Whether to show regression line
        color_scheme: Color scheme name
        output_dir: Output directory
        
    Returns:
        {
            "success": True,
            "png_path": "...",
            "metadata": {"plot_type": "scatter", "correlation": r, ...}
        }
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"success": False, "error": "matplotlib not installed"}
    
    if not x_values or not y_values:
        return {"success": False, "error": "Empty x_values or y_values"}
    
    if len(x_values) != len(y_values):
        return {"success": False, "error": "x_values and y_values must have same length"}
    
    logger.info(f"Generating scatter plot: {title} with {len(x_values)} points")
    
    try:
        colors = _get_colors(color_scheme)
        dims = _get_dimensions("scatter")
        output_config = _get_output_config()
        
        x = np.array(x_values)
        y = np.array(y_values)
        
        fig, ax = plt.subplots(figsize=(dims["width"] / 100, dims["height"] / 100))
        fig.patch.set_facecolor(colors.get("background", "#FFFFFF"))
        ax.set_facecolor(colors.get("background", "#FFFFFF"))
        
        # Scatter plot
        ax.scatter(x, y, 
                  c=colors.get("primary", "#2E86AB"),
                  s=dims.get("point_size", 5) * 10,
                  alpha=0.6,
                  edgecolors=colors.get("axis", "#666666"),
                  linewidths=0.5)
        
        # Calculate correlation
        correlation = 0
        if len(x) > 1:
            x_mean, y_mean = x.mean(), y.mean()
            x_std, y_std = x.std(), y.std()
            if x_std > 0 and y_std > 0:
                correlation = np.corrcoef(x, y)[0, 1]
        
        # Add regression line
        if show_regression and len(x) > 1:
            try:
                z = np.polyfit(x, y, 1)
                p = np.poly1d(z)
                x_line = np.linspace(x.min(), x.max(), 100)
                ax.plot(x_line, p(x_line), 
                       color=colors.get("secondary", "#E94F37"),
                       linewidth=2, linestyle='--', alpha=0.8,
                       label=f'r = {correlation:.3f}')
                ax.legend(loc='upper right', fontsize=10)
            except Exception:
                pass
        
        # Add correlation annotation
        ax.text(0.02, 0.98, f"r = {correlation:.3f}\nN = {len(x)}",
               transform=ax.transAxes, fontsize=10,
               verticalalignment='top',
               bbox=dict(boxstyle='round,pad=0.3', facecolor='white', 
                        edgecolor=colors.get("grid", "#E0E0E0"), alpha=0.9))
        
        ax.set_xlabel(x_label, fontsize=11, color=colors.get("text", "#333333"))
        ax.set_ylabel(y_label, fontsize=11, color=colors.get("text", "#333333"))
        ax.set_title(title, fontsize=13, fontweight='bold', color=colors.get("text", "#333333"))
        ax.grid(True, alpha=0.3, color=colors.get("grid", "#E0E0E0"))
        ax.tick_params(colors=colors.get("axis", "#666666"))
        
        plt.tight_layout()
        
        output_dir = _ensure_output_dir(output_dir)
        png_path = _generate_filename("scatter", output_dir)
        fig.savefig(str(png_path), dpi=output_config.get("dpi", 150),
                   bbox_inches='tight', facecolor=colors.get("background", "#FFFFFF"))
        plt.close(fig)
        
        logger.info(f"Saved scatter plot to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path),
            "metadata": {
                "plot_type": plot_type,
                "title": title,
                "data_points": len(x_values),
                "correlation": round(correlation, 4),
            }
        }
        
    except Exception as e:
        logger.exception(f"Error generating scatter plot: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Multi-Plot Helpers
# =============================================================================

def plot_multi_distribution(
    data_dict: Dict[str, List[float]],
    title: str,
    x_label: str = "Value",
    highlight_values: Optional[Dict[str, float]] = None,
    color_scheme: str = "default",
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generate multiple distribution plots in a grid.
    
    Args:
        data_dict: Dict mapping names to value lists
            e.g., {"Absolute Return": [...], "Return": [...], "Volatility": [...]}
        title: Overall title
        x_label: X-axis label
        highlight_values: Dict mapping names to highlight values
        color_scheme: Color scheme name
        output_dir: Output directory
        
    Returns:
        {
            "success": True,
            "png_path": "...",
            "metadata": {"plots": 3, ...}
        }
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"success": False, "error": "matplotlib not installed"}
    
    if not data_dict:
        return {"success": False, "error": "Empty data_dict"}
    
    logger.info(f"Generating multi-distribution plot: {title} with {len(data_dict)} distributions")
    
    try:
        colors = _get_colors(color_scheme)
        output_config = _get_output_config()
        config = _load_config()
        hist_config = config.get("histogram", {})
        
        n_plots = len(data_dict)
        n_cols = min(3, n_plots)
        n_rows = math.ceil(n_plots / n_cols)
        
        fig, axes = plt.subplots(n_rows, n_cols, 
                                figsize=(5 * n_cols, 4 * n_rows))
        fig.suptitle(title, fontsize=14, fontweight='bold', color=colors.get("text", "#333333"))
        fig.patch.set_facecolor(colors.get("background", "#FFFFFF"))
        
        # Flatten axes for iteration
        if n_plots == 1:
            axes = [axes]
        else:
            axes = axes.flatten() if hasattr(axes, 'flatten') else [axes]
        
        highlight_values = highlight_values or {}
        
        for idx, (name, values) in enumerate(data_dict.items()):
            ax = axes[idx]
            ax.set_facecolor(colors.get("background", "#FFFFFF"))
            
            if not values:
                ax.text(0.5, 0.5, "No data", ha='center', va='center', transform=ax.transAxes)
                ax.set_title(name, fontsize=11)
                continue
            
            data = np.array(values)
            
            # Plot histogram
            ax.hist(data, bins=20,
                   color=colors.get("primary", "#2E86AB"),
                   alpha=hist_config.get("alpha", 0.7),
                   edgecolor=hist_config.get("edge_color", "white"),
                   linewidth=0.5)
            
            # Add KDE
            if hist_config.get("show_kde", True) and len(data) > 2:
                try:
                    from scipy.stats import gaussian_kde
                    kde = gaussian_kde(data)
                    x_kde = np.linspace(data.min(), data.max(), 100)
                    
                    ax2 = ax.twinx()
                    kde_color = colors.get("secondary", "#E94F37")
                    ax2.plot(x_kde, kde(x_kde), color=kde_color, linewidth=1.5)
                    ax2.set_ylabel("")
                    ax2.tick_params(axis='y', labelsize=0)
                except ImportError:
                    pass
            
            # Add stats box (compact version for multi-plot with percentiles)
            stats = _compute_statistics(values)
            sorted_data = sorted(data)
            n = len(sorted_data)
            p25 = sorted_data[int(n * 0.25)] if n > 0 else 0
            p75 = sorted_data[int(n * 0.75)] if n > 0 else 0
            p95 = sorted_data[int(n * 0.95)] if n > 0 else 0
            
            stats_text = (
                f"N={stats['count']} μ={stats['mean']:.1f} σ={stats['std']:.1f}\n"
                f"P25={p25:.1f} P75={p75:.1f} P95={p95:.1f}"
            )
            ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=7,
                   verticalalignment='top', horizontalalignment='left',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white', 
                            edgecolor=colors.get("grid", "#E0E0E0"), alpha=0.85))
            
            # Highlight value (marks the most recent/latest event)
            if name in highlight_values:
                highlight_val = highlight_values[name]
                ax.axvline(x=highlight_val, color=colors.get("accent", "#F6AE2D"),
                          linestyle=':', linewidth=2, label='Latest Event')
                
                # Add annotation with value and percentile
                pct = _percentile_of_value(values, highlight_val)
                y_max = ax.get_ylim()[1]
                ax.annotate(
                    f"Latest: {highlight_val:.1f}\n%ile: {pct:.0f}%",
                    xy=(highlight_val, y_max * 0.7),
                    fontsize=7,
                    fontweight='bold',
                    color=colors.get("accent", "#F6AE2D"),
                    ha='center',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', 
                             edgecolor=colors.get("accent", "#F6AE2D"), alpha=0.9)
                )
            
            ax.set_title(name, fontsize=11, color=colors.get("text", "#333333"))
            ax.set_xlabel(x_label, fontsize=9, color=colors.get("text", "#333333"))
            ax.set_ylabel("Frequency", fontsize=9, color=colors.get("text", "#333333"))
            ax.grid(True, alpha=0.3, color=colors.get("grid", "#E0E0E0"))
        
        # Hide unused subplots
        for idx in range(len(data_dict), len(axes)):
            axes[idx].set_visible(False)
        
        plt.tight_layout()
        
        output_dir = _ensure_output_dir(output_dir)
        png_path = _generate_filename("multi_distribution", output_dir)
        fig.savefig(str(png_path), dpi=output_config.get("dpi", 150),
                   bbox_inches='tight', facecolor=colors.get("background", "#FFFFFF"))
        plt.close(fig)
        
        logger.info(f"Saved multi-distribution plot to {png_path}")
        
        return {
            "success": True,
            "png_path": str(png_path),
            "metadata": {
                "plot_type": "multi_distribution",
                "title": title,
                "num_distributions": len(data_dict),
                "distribution_names": list(data_dict.keys()),
            }
        }
        
    except Exception as e:
        logger.exception(f"Error generating multi-distribution plot: {e}")
        return {"success": False, "error": str(e)}

