"""
Event Distribution MCP Tool.

Computes event-specific return distributions.
Uses core plotting functions - no DistroDashboard dependency.
"""

import logging
import math
from pathlib import Path
from typing import Dict, Any, Optional, List

from src.mcp.discovery import register_tool
from src.servers.analytics.plot_factory import create_plot

logger = logging.getLogger(__name__)


def _check_dependencies():
    """Check if required dependencies are available."""
    try:
        import pandas
        import numpy
        return True
    except ImportError:
        return False


def _symlog(x: float, base: float = 10.0) -> float:
    """Symmetric log transform: sign(x) * log(1 + |x|)."""
    if x == 0:
        return 0.0
    return math.copysign(math.log(1 + abs(x)) / math.log(base), x)


def _compute_event_returns(
    symbol: str,
    event_name: str,
    delta_hrs: int,
    country: str,
    last_x_obs: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Compute event returns from database.
    
    Returns:
        {
            "absolute_returns": [...],
            "returns": [...],
            "volatility_returns": [...],
            "event_details": [...],
            "latest": {...},
            "statistics": {...},
        }
    """
    import pandas as pd
    import numpy as np
    
    from .db_reader import (
        get_ohlcv_from_db,
        get_events_from_db,
        get_bps_factor,
        add_timezone,
    )
    
    # Get events from database
    events_df = get_events_from_db(
        event_name_pattern=event_name,
        country=country,
    )
    
    if events_df.empty:
        return {"error": f"No events found matching '{event_name}' for {country}"}
    
    # Get price data - use Bloomberg-style symbol pattern
    symbol_pattern = f"{symbol}% Comdty"
    price_df = get_ohlcv_from_db(symbol_pattern)
    
    if price_df.empty:
        return {"error": f"No price data found for {symbol}"}
    
    # Add timezone and reset index
    price_df = add_timezone(price_df)
    price_df = price_df.reset_index()
    
    # Get BPS factor for symbol
    bps_factor = get_bps_factor(symbol)
    
    # Compute returns for each event
    absolute_returns = []
    returns = []
    volatility_returns = []
    event_details = []
    
    for _, event in events_df.iterrows():
        event_time = pd.to_datetime(event['datetime'])
        
        # Make event_time timezone-aware to match price_df['timestamp'] (US/Eastern)
        if event_time.tzinfo is None:
            event_time = event_time.tz_localize('US/Eastern')
        else:
            event_time = event_time.tz_convert('US/Eastern')
        
        # Find prices around event time
        mask = (
            (price_df['timestamp'] >= event_time - pd.Timedelta(hours=delta_hrs)) &
            (price_df['timestamp'] <= event_time + pd.Timedelta(hours=delta_hrs))
        )
        
        event_prices = price_df[mask]
        
        if len(event_prices) >= 2:
            open_price = event_prices['Open'].iloc[0]
            close_price = event_prices['Close'].iloc[-1]
            high_price = event_prices['High'].max()
            low_price = event_prices['Low'].min()
            
            ret = (close_price - open_price) * bps_factor
            abs_ret = abs(ret)
            vol_ret = (high_price - low_price) * bps_factor
            
            absolute_returns.append(abs_ret)
            returns.append(ret)
            volatility_returns.append(vol_ret)
            
            event_details.append({
                "date": str(event_time.date()),
                "event_name": event.get('event_name', event_name),
                "actual": event.get('actual'),
                "consensus": event.get('consensus'),
                "absolute_return": round(abs_ret, 2),
                "return": round(ret, 2),
                "volatility_return": round(vol_ret, 2),
            })
    
    if not absolute_returns:
        return {"error": "Could not match events with price data"}
    
    # Apply last_x_obs limit if specified
    if last_x_obs and len(absolute_returns) > last_x_obs:
        absolute_returns = absolute_returns[-last_x_obs:]
        returns = returns[-last_x_obs:]
        volatility_returns = volatility_returns[-last_x_obs:]
        event_details = event_details[-last_x_obs:]
    
    # Compute statistics
    def compute_stats(values: List[float], name: str) -> Dict[str, Any]:
        if not values:
            return {}
        arr = np.array(values)
        return {
            "count": len(arr),
            "mean": round(float(np.mean(arr)), 2),
            "std": round(float(np.std(arr)), 2),
            "min": round(float(np.min(arr)), 2),
            "25%": round(float(np.percentile(arr, 25)), 2),
            "50%": round(float(np.percentile(arr, 50)), 2),
            "75%": round(float(np.percentile(arr, 75)), 2),
            "95%": round(float(np.percentile(arr, 95)), 2),
            "99%": round(float(np.percentile(arr, 99)), 2),
            "max": round(float(np.max(arr)), 2),
        }
    
    statistics = {
        "Absolute Return": compute_stats(absolute_returns, "Absolute Return"),
        "Return": compute_stats(returns, "Return"),
        "Volatility Return": compute_stats(volatility_returns, "Volatility Return"),
    }
    
    # Latest info with percentile
    latest = event_details[-1] if event_details else {}
    if latest and absolute_returns:
        from scipy.stats import percentileofscore
        latest["percentile"] = round(
            percentileofscore(absolute_returns, latest["absolute_return"], kind="rank"), 1
        )
    
    return {
        "absolute_returns": absolute_returns,
        "returns": returns,
        "volatility_returns": volatility_returns,
        "event_details": event_details,
        "latest": latest,
        "statistics": statistics,
    }


@register_tool(
    name="compute_event_distribution",
    description=(
        "Compute event-specific return distributions. "
        "Analyzes price movements around economic events like NFP, CPI, FOMC, etc. "
        "Shows distribution of returns, volatility, and absolute returns for specified events. "
        "Automatically generates distribution plots."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol to analyze (e.g., 'ZN', 'ES', 'NQ')"
            },
            "event_name": {
                "type": "string",
                "description": "Economic event name (e.g., 'NFP', 'CPI', 'FOMC', 'Unemployment', 'JOLTS')"
            },
            "interval": {
                "type": "string",
                "description": "Data interval (default: '1h')"
            },
            "delta_hrs": {
                "type": "integer",
                "description": "Total hours to analyze around event (default: 1)"
            },
            "country": {
                "type": "string",
                "description": "Country filter for events (default: 'US')"
            },
            "last_x_obs": {
                "type": "integer",
                "description": "Limit to last X observations (optional)"
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for output plots (optional)"
            }
        },
        "required": ["symbol", "event_name"]
    }
)
def compute_event_distribution(
    symbol: str,
    event_name: str,
    interval: str = "1h",
    delta_hrs: int = 1,
    country: str = "US",
    last_x_obs: Optional[int] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute event-specific return distributions.
    
    Args:
        symbol: Symbol to analyze (e.g., "ZN")
        event_name: Economic event name (e.g., "NFP", "CPI", "FOMC")
        interval: Data interval (e.g., "1h")
        delta_hrs: Total hours to analyze around event
        country: Country filter for events (default: "US")
        last_x_obs: Limit to last X observations
        output_dir: Directory for output plots
        
    Returns:
        {
            "success": True,
            "symbol": "ZN",
            "event_name": "NFP",
            "event_count": 24,
            "statistics": {...},
            "latest": {...},
            "events_analyzed": [...],
            "plot_paths": ["...event_distro.png"]
        }
    """
    if not _check_dependencies():
        return {
            "success": False,
            "error": "Missing dependencies. Install with: pip install pandas numpy scipy"
        }
    
    logger.info(f"Computing event distribution for {symbol} - {event_name}")
    
    try:
        # 1. Generate data
        data = _compute_event_returns(
            symbol=symbol,
            event_name=event_name,
            delta_hrs=delta_hrs,
            country=country,
            last_x_obs=last_x_obs,
        )
        
        if "error" in data:
            return {"success": False, "error": data["error"]}
        
        # 2. Auto-generate plots using core plotting
        output_path = Path(output_dir) if output_dir else None
        
        plot_result = create_plot(
            data={
                "absolute_returns": data["absolute_returns"],
                "returns": data["returns"],
                "volatility_returns": data["volatility_returns"],
                "latest": data["latest"],
                "metadata": {"symbol": symbol, "event_name": event_name},
            },
            context="event_distribution",
            title=f"{symbol} - {event_name} Event Distribution",
            output_dir=output_path,
        )
        
        # 3. Return combined result
        return {
            "success": True,
            "symbol": symbol,
            "event_name": event_name,
            "interval": interval,
            "delta_hrs": delta_hrs,
            "event_count": len(data["absolute_returns"]),
            "statistics": data["statistics"],
            "latest": data["latest"],
            "events_analyzed": data["event_details"][-10:],
            "plot_paths": plot_result.get("plot_paths", []),
        }
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return {"success": False, "error": f"Import failed: {str(e)}"}
    except Exception as e:
        logger.exception(f"Error computing event distribution: {e}")
        return {"success": False, "error": str(e)}


@register_tool(
    name="plot_log_bps_distribution",
    description=(
        "Plot event distribution with log-transformed BPS on x-axis and KDE overlay. "
        "Histogram bins have fixed width in log scale. Shows distribution shape "
        "of event price moves with KDE for smoothed density visualization."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol to analyze (e.g., 'ZN', 'TY', 'ES', 'NQ')"
            },
            "event_name": {
                "type": "string",
                "description": "Economic event name (e.g., 'NFP', 'CPI', 'FOMC')"
            },
            "return_type": {
                "type": "string",
                "enum": ["absolute", "signed", "volatility"],
                "description": "Return type: 'absolute', 'signed', or 'volatility'. Default: 'absolute'"
            },
            "delta_hrs": {
                "type": "integer",
                "description": "Total hours to analyze around event (default: 1)"
            },
            "country": {
                "type": "string",
                "description": "Country filter for events (default: 'US')"
            },
            "last_x_obs": {
                "type": "integer",
                "description": "Limit to last X observations (optional)"
            },
            "log_base": {
                "type": "number",
                "description": "Base for log transform (default: 10)"
            }
        },
        "required": ["symbol", "event_name"]
    }
)
def plot_log_bps_distribution(
    symbol: str,
    event_name: str,
    return_type: str = "absolute",
    delta_hrs: int = 1,
    country: str = "US",
    last_x_obs: Optional[int] = None,
    log_base: float = 10.0,
) -> Dict[str, Any]:
    """
    Plot event distribution with log-transformed BPS returns and KDE.
    
    Args:
        symbol: Symbol to analyze (e.g., "ZN", "TY")
        event_name: Economic event name (e.g., "NFP", "CPI")
        return_type: "absolute", "signed", or "volatility"
        delta_hrs: Hours around event to analyze
        country: Country filter
        last_x_obs: Limit to last X observations
        log_base: Base for logarithm (default 10)
        
    Returns:
        {
            "success": True,
            "symbol": "ZN",
            "event_name": "NFP",
            "event_count": 48,
            "log_bps_stats": {...},
            "bps_stats": {...},
            "plot_path": "..."
        }
    """
    if not _check_dependencies():
        return {
            "success": False,
            "error": "Missing dependencies. Install with: pip install pandas numpy scipy"
        }
    
    logger.info(f"Plotting log BPS distribution for {symbol} - {event_name}")
    
    try:
        import numpy as np
        from scipy import stats
        from scipy.stats import percentileofscore
        
        from src.servers.analytics.core_plotting import plot_distribution
        from .db_reader import get_plots_output_dir
        
        # 1. Generate data
        data = _compute_event_returns(
            symbol=symbol,
            event_name=event_name,
            delta_hrs=delta_hrs,
            country=country,
            last_x_obs=last_x_obs,
        )
        
        if "error" in data:
            return {"success": False, "error": data["error"]}
        
        # Select return type
        if return_type == "signed":
            bps_returns = np.array(data["returns"])
        elif return_type == "volatility":
            bps_returns = np.array(data["volatility_returns"])
        else:
            bps_returns = np.array(data["absolute_returns"])
        
        if len(bps_returns) < 3:
            return {"success": False, "error": f"Insufficient data points: {len(bps_returns)}"}
        
        # Apply log transform
        if return_type in ["absolute", "volatility"]:
            log_returns = np.log(bps_returns + 1) / np.log(log_base)
        else:
            log_returns = np.array([_symlog(x, log_base) for x in bps_returns])
        
        # Remove inf/nan
        valid_mask = np.isfinite(log_returns)
        log_returns = log_returns[valid_mask]
        bps_returns = bps_returns[valid_mask]
        
        if len(log_returns) < 3:
            return {"success": False, "error": f"Insufficient valid data points: {len(log_returns)}"}
        
        # Compute statistics
        log_stats = {
            "count": len(log_returns),
            "mean": round(float(np.mean(log_returns)), 4),
            "std": round(float(np.std(log_returns)), 4),
            "min": round(float(np.min(log_returns)), 4),
            "max": round(float(np.max(log_returns)), 4),
            "median": round(float(np.median(log_returns)), 4),
            "skewness": round(float(stats.skew(log_returns)), 4),
            "kurtosis": round(float(stats.kurtosis(log_returns)), 4),
        }
        
        bps_stats = {
            "mean": round(float(np.mean(bps_returns)), 2),
            "std": round(float(np.std(bps_returns)), 2),
            "min": round(float(np.min(bps_returns)), 2),
            "max": round(float(np.max(bps_returns)), 2),
        }
        
        # 2. Generate plot using core plotting
        x_label = f"Log₁₀(BPS + 1)" if return_type in ["absolute", "volatility"] else f"Symlog₁₀(BPS)"
        highlight = log_returns[-1] if len(log_returns) > 0 else None
        
        plot_result = plot_distribution(
            values=log_returns.tolist(),
            title=f"{event_name} Event Distribution - {symbol}\n{return_type.title()} Returns (Log Scale)",
            x_label=x_label,
            y_label="Frequency",
            plot_type="histogram",
            highlight_value=highlight,
            output_dir=get_plots_output_dir(),
        )
        
        return {
            "success": True,
            "symbol": symbol,
            "event_name": event_name,
            "return_type": return_type,
            "log_base": log_base,
            "event_count": len(log_returns),
            "log_bps_stats": log_stats,
            "bps_stats": bps_stats,
            "plot_path": plot_result.get("png_path"),
            "plot_paths": [plot_result.get("png_path")] if plot_result.get("png_path") else [],
        }
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return {"success": False, "error": f"Import failed: {str(e)}"}
    except Exception as e:
        logger.exception(f"Error plotting log BPS distribution: {e}")
        return {"success": False, "error": str(e)}
