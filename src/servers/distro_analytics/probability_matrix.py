"""
Probability Matrix MCP Tool.

Computes probability matrices for price movements.
Uses core plotting functions - no DistroDashboard dependency.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

import numpy as np
import pandas as pd

from src.mcp.discovery import register_tool
from src.servers.analytics.plot_factory import create_plot
from src.servers.analytics.core_plotting import plot_distribution, plot_categorical

logger = logging.getLogger(__name__)


def _check_dependencies():
    """Check if required dependencies are available."""
    try:
        import pandas
        import numpy
        return True
    except ImportError:
        return False


def _prepare_ohlcv_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare OHLCV data for probability analysis.
    
    Args:
        df: DataFrame with OHLCV columns from database
        
    Returns:
        DataFrame with US/Eastern timezone
    """
    result = df.copy()
    
    # Ensure we have a timestamp column
    if 'US/Eastern Timezone' not in result.columns:
        if 'timestamp' in result.columns:
            result['US/Eastern Timezone'] = result['timestamp']
        elif result.index.name == 'timestamp' or isinstance(result.index, pd.DatetimeIndex):
            result = result.reset_index()
            if 'index' in result.columns:
                result = result.rename(columns={'index': 'timestamp'})
            result['US/Eastern Timezone'] = result['timestamp']
    
    # Ensure datetime type
    if 'US/Eastern Timezone' in result.columns:
        result['US/Eastern Timezone'] = pd.to_datetime(result['US/Eastern Timezone'])
    
    # Sort by timestamp
    result = result.sort_values('US/Eastern Timezone').reset_index(drop=True)
    
    return result


def _resample_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample data to hourly OHLC bars.
    
    Args:
        df: DataFrame with 'US/Eastern Timezone', 'Open', 'High', 'Low', 'Close'
        
    Returns:
        Hourly OHLC DataFrame
    """
    df = df.set_index('US/Eastern Timezone')
    
    hourly = df.resample('1h').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last'
    }).dropna()
    
    hourly = hourly.reset_index()
    return hourly


def _compute_probability_matrix(
    df: pd.DataFrame,
    bps_factor: int,
    target_bps: float,
    target_hrs: int,
    version: str = "Absolute",
) -> Dict[str, Any]:
    """
    Compute probability matrices for price movements.
    
    Args:
        df: DataFrame with OHLC data
        bps_factor: BPS conversion factor
        target_bps: Target basis points threshold
        target_hrs: Target time horizon in hours
        version: "Absolute", "Up", or "Down"
        
    Returns:
        {
            "modes": {
                "open-close": {...},
                "open-high/low": {...},
                "high-low": {...},
            },
            "all_movements": {...},
        }
    """
    # Compute movements in BPS
    df = df.copy()
    df['open_close'] = (df['Close'] - df['Open']) * bps_factor
    
    # For high/low from open, we need to determine direction
    if version == "Up":
        df['open_extreme'] = (df['High'] - df['Open']) * bps_factor
    elif version == "Down":
        df['open_extreme'] = (df['Low'] - df['Open']) * bps_factor
    else:  # Absolute
        df['open_extreme'] = df.apply(
            lambda row: max(
                abs(row['High'] - row['Open']),
                abs(row['Low'] - row['Open'])
            ) * bps_factor,
            axis=1
        )
    
    df['high_low'] = (df['High'] - df['Low']) * bps_factor
    
    # Apply version transform
    if version == "Absolute":
        df['open_close'] = df['open_close'].abs()
    elif version == "Up":
        df['open_close'] = df['open_close'].clip(lower=0)
    elif version == "Down":
        df['open_close'] = df['open_close'].clip(upper=0).abs()
    
    # Compute rolling movements over target_hrs window
    movements_oc = []
    movements_oe = []
    movements_hl = []
    
    for i in range(len(df) - target_hrs + 1):
        window = df.iloc[i:i + target_hrs]
        
        # Open-close: from first bar's open to last bar's close
        oc_move = abs(window['Close'].iloc[-1] - window['Open'].iloc[0]) * bps_factor
        movements_oc.append(oc_move)
        
        # Open-extreme: max distance from first open
        first_open = window['Open'].iloc[0]
        if version == "Up":
            oe_move = (window['High'].max() - first_open) * bps_factor
        elif version == "Down":
            oe_move = (first_open - window['Low'].min()) * bps_factor
        else:
            high_dist = abs(window['High'].max() - first_open) * bps_factor
            low_dist = abs(first_open - window['Low'].min()) * bps_factor
            oe_move = max(high_dist, low_dist)
        movements_oe.append(oe_move)
        
        # High-low: total range
        hl_move = (window['High'].max() - window['Low'].min()) * bps_factor
        movements_hl.append(hl_move)
    
    # Calculate probabilities
    def calc_probs(movements: List[float], target: float) -> Dict[str, Any]:
        if not movements:
            return {"prob_greater": 0, "prob_less_equal": 100, "count": 0}
        
        arr = np.array(movements)
        count_greater = np.sum(arr > target)
        total = len(arr)
        
        return {
            "prob_greater": round(100 * count_greater / total, 2),
            "prob_less_equal": round(100 * (total - count_greater) / total, 2),
            "count": total,
            "mean": round(float(np.mean(arr)), 2),
            "std": round(float(np.std(arr)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
            "median": round(float(np.median(arr)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
            "p99": round(float(np.percentile(arr, 99)), 2),
        }
    
    modes = {
        "open-close": calc_probs(movements_oc, target_bps),
        "open-high/low": calc_probs(movements_oe, target_bps),
        "high-low": calc_probs(movements_hl, target_bps),
    }
    
    all_movements = {
        "open-close": movements_oc,
        "open-high/low": movements_oe,
        "high-low": movements_hl,
    }
    
    # Create buckets for probability matrix
    buckets = [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20]
    matrices = {}
    
    for mode, movements in all_movements.items():
        if not movements:
            continue
        
        arr = np.array(movements)
        bucket_probs = []
        
        for bucket in buckets:
            prob = round(100 * np.sum(arr <= bucket) / len(arr), 2)
            bucket_probs.append({"threshold_bps": bucket, "cumulative_prob": prob})
        
        matrices[mode] = bucket_probs
    
    return {
        "modes": modes,
        "all_movements": all_movements,
        "matrices": matrices,
    }


@register_tool(
    name="compute_probability_matrix",
    description=(
        "Compute probability matrices for price movements. "
        "Shows probability of price moving X bps within Y hours, "
        "with distribution plots for open-close, open-high/low, and high-low modes."
    )
)
def compute_probability_matrix(
    symbol: str,
    interval: str = "1h",
    target_bps: float = 2.0,
    target_hrs: int = 6,
    version: str = "Absolute",
    output_dir: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute probability matrices for price movements.
    
    Args:
        symbol: Symbol to analyze (e.g., "ZN", "TY")
        interval: Data interval (e.g., "1h")
        target_bps: Target basis points threshold (e.g., 2.0 = 2 bps)
        target_hrs: Target time horizon in hours
        version: Analysis version:
            - "Absolute": Absolute value of moves
            - "Up": Only upward moves
            - "Down": Only downward moves
        output_dir: Directory for output plots
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)
        
    Returns:
        {
            "success": True,
            "symbol": "ZN",
            "target_bps": 2.0,
            "target_hrs": 6,
            "version": "Absolute",
            "data_points": 12500,
            "probabilities": {...},
            "matrices": {...},
            "plot_paths": [...]
        }
    """
    # Input validation
    if not symbol or not isinstance(symbol, str):
        return {"success": False, "error": "symbol must be a non-empty string"}
    
    if target_bps <= 0:
        return {"success": False, "error": "target_bps must be positive"}
    
    if target_hrs <= 0:
        return {"success": False, "error": "target_hrs must be positive"}
    
    if not _check_dependencies():
        return {
            "success": False,
            "error": "Missing dependencies. Install with: pip install pandas numpy"
        }
    
    try:
        from .db_reader import (
            get_plots_output_dir,
            get_ohlcv_from_db,
            get_bps_factor,
            add_timezone,
        )
        
        # Determine output directory
        if output_dir:
            plots_dir = Path(output_dir)
            plots_dir.mkdir(parents=True, exist_ok=True)
        else:
            plots_dir = get_plots_output_dir()
        
        # Validate version
        valid_versions = ["Absolute", "Up", "Down"]
        if version not in valid_versions:
            return {
                "success": False,
                "error": f"Invalid version: {version}. Must be one of {valid_versions}"
            }
        
        # Get BPS factor
        bps_factor = get_bps_factor(symbol)
        
        # Load from database
        symbol_pattern = f"{symbol}%"
        df = get_ohlcv_from_db(symbol_pattern, start_date, end_date)
        
        if df.empty:
            return {
                "success": False,
                "error": f"No data found for {symbol} in database"
            }
        
        # Add timezone
        df = add_timezone(df, source_tz='UTC', target_tz='US/Eastern')
        
        # Prepare data
        df = _prepare_ohlcv_data(df)
        
        # Resample to hourly if needed
        if interval == "1h":
            df = _resample_to_hourly(df)
        
        logger.info(f"Loaded {len(df)} rows for {symbol}")
        
        # Compute probability matrices
        result = _compute_probability_matrix(
            df=df,
            bps_factor=bps_factor,
            target_bps=target_bps,
            target_hrs=target_hrs,
            version=version,
        )
        
        # Generate plots using core plotting
        plot_paths = []
        modes = ["open-close", "open-high/low", "high-low"]
        
        for mode in modes:
            movements = result["all_movements"].get(mode, [])
            if not movements:
                continue
            
            # Distribution plot
            dist_result = plot_distribution(
                values=movements,
                title=f"{symbol} {mode.title()} Movement Distribution\n{version} over {target_hrs}h",
                x_label=f"Movement (BPS)",
                y_label="Frequency",
                highlight_value=target_bps,
                output_dir=plots_dir,
            )
            if dist_result.get("success"):
                plot_paths.append(dist_result["png_path"])
        
        # Probability bar chart for the target
        probs = result["modes"]
        prob_values = [probs[m]["prob_greater"] for m in modes]
        
        bar_result = plot_categorical(
            values=prob_values,
            labels=[f"P(>{target_bps} bps)\n{m}" for m in modes],
            title=f"{symbol} - Probability of Moving >{target_bps} BPS in {target_hrs}h\n({version})",
            y_label="Probability (%)",
            output_dir=plots_dir,
        )
        if bar_result.get("success"):
            plot_paths.append(bar_result["png_path"])
        
        return {
            "success": True,
            "symbol": symbol,
            "interval": interval,
            "target_bps": target_bps,
            "target_hrs": target_hrs,
            "version": version,
            "data_points": len(df),
            "bps_factor": bps_factor,
            "probabilities": result["modes"],
            "matrices": result["matrices"],
            "plot_paths": plot_paths,
            "description": f"Probability of {symbol} moving {target_bps} bps within {target_hrs} hours"
        }
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return {"success": False, "error": f"Import failed: {str(e)}"}
    except Exception as e:
        logger.exception(f"Error computing probability matrix: {e}")
        return {"success": False, "error": str(e)}
