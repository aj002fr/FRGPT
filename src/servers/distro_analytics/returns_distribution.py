"""
Returns Distribution MCP Tool.

Computes session-based returns distribution.
Uses core plotting functions - no DistroDashboard dependency.
"""

import logging
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


# Session definitions (US/Eastern time)
SESSIONS = {
    "London 0-7 ET": (0, 7),
    "US Open 7-10 ET": (7, 10),
    "US Mid 10-15 ET": (10, 15),
    "US Close 15-17 ET": (15, 17),
    "Asia 18-24 ET": (18, 24),
}


def _compute_session_returns(
    symbol: str,
    start_date: Optional[str],
    end_date: Optional[str],
    bps_factor: Optional[int],
) -> Dict[str, Any]:
    """
    Compute session-based returns from database.
    
    Returns:
        {
            "sessions": {
                "London 0-7 ET": [...],
                "US Open 7-10 ET": [...],
                ...
            },
            "all_day": [...],
            "latest": {...},
            "statistics": {...},
            "date_range": {...},
        }
    """
    import pandas as pd
    import numpy as np
    
    from .db_reader import (
        get_ohlcv_from_db,
        get_bps_factor,
        add_timezone,
    )
    
    # Get price data
    symbol_pattern = f"{symbol}%"
    price_df = get_ohlcv_from_db(symbol_pattern, start_date, end_date)
    
    if price_df.empty:
        return {"error": f"No price data found for {symbol}"}
    
    # Add timezone and reset index
    price_df = add_timezone(price_df, source_tz='UTC', target_tz='US/Eastern')
    price_df = price_df.reset_index()
    
    if 'index' in price_df.columns:
        price_df = price_df.rename(columns={'index': 'timestamp'})
    
    if 'timestamp' not in price_df.columns and 'US/Eastern Timezone' in price_df.columns:
        price_df['timestamp'] = price_df['US/Eastern Timezone']
    
    # Determine BPS factor
    if bps_factor is None:
        bps_factor = get_bps_factor(symbol)
    
    # Ensure timestamp is datetime
    price_df['timestamp'] = pd.to_datetime(price_df['timestamp'])
    price_df['date'] = price_df['timestamp'].dt.date
    price_df['hour'] = price_df['timestamp'].dt.hour
    
    # Compute daily returns by session
    session_returns = {session: [] for session in SESSIONS.keys()}
    all_day_returns = []
    daily_data = []
    
    for date, day_df in price_df.groupby('date'):
        if len(day_df) < 2:
            continue
        
        # All-day return (open to close)
        day_open = day_df['Open'].iloc[0]
        day_close = day_df['Close'].iloc[-1]
        day_return = (day_close - day_open) * bps_factor
        all_day_returns.append(day_return)
        daily_data.append({"date": str(date), "return": round(day_return, 2)})
        
        # Session returns
        for session, (start_hr, end_hr) in SESSIONS.items():
            session_mask = (day_df['hour'] >= start_hr) & (day_df['hour'] < end_hr)
            session_df = day_df[session_mask]
            
            if len(session_df) >= 2:
                session_open = session_df['Open'].iloc[0]
                session_close = session_df['Close'].iloc[-1]
                session_return = (session_close - session_open) * bps_factor
                session_returns[session].append(session_return)
    
    if not all_day_returns:
        return {"error": "No returns could be computed"}
    
    # Compute statistics
    def compute_stats(values: List[float]) -> Dict[str, Any]:
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
    
    statistics = {"All day": compute_stats(all_day_returns)}
    for session, returns in session_returns.items():
        if returns:
            statistics[session] = compute_stats(returns)
    
    # Latest info with percentile
    latest = daily_data[-1] if daily_data else {}
    if latest and all_day_returns:
        from scipy.stats import percentileofscore
        latest["percentile"] = round(
            percentileofscore(all_day_returns, latest["return"], kind="rank"), 1
        )
        mean = np.mean(all_day_returns)
        std = np.std(all_day_returns)
        latest["zscore"] = round((latest["return"] - mean) / std, 2) if std > 0 else 0
    
    # Date range
    date_range = {}
    if 'timestamp' in price_df.columns:
        date_range = {
            "start": str(price_df['timestamp'].min().date()),
            "end": str(price_df['timestamp'].max().date()),
        }
    
    return {
        "sessions": session_returns,
        "all_day": all_day_returns,
        "daily_data": daily_data,
        "latest": latest,
        "statistics": statistics,
        "date_range": date_range,
        "bps_factor": bps_factor,
    }


@register_tool(
    name="compute_returns_distribution",
    description=(
        "Compute session-based returns distribution for a symbol. "
        "Generates distribution plots showing returns across trading sessions "
        "(London, US Open, US Mid, US Close, Asia) with statistics and percentile rankings."
    )
)
def compute_returns_distribution(
    symbol: str,
    interval: str = "1h",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    bps_factor: Optional[int] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute session-based returns distribution.
    
    Args:
        symbol: Symbol to analyze (e.g., "ZN", "ZB", "ES")
        interval: Data interval ("1m", "15m", "1h", "1d")
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)
        bps_factor: BPS conversion factor (default: auto-detect from symbol)
        output_dir: Directory for output plots (default: workspace/plots)
        
    Returns:
        {
            "success": True,
            "symbol": "ZN",
            "interval": "1h",
            "data_points": 1250,
            "date_range": {"start": "2024-01-01", "end": "2025-12-04"},
            "statistics": {...},
            "latest": {...},
            "plot_paths": ["...Returns_Distribution.png"],
        }
    """
    if not _check_dependencies():
        return {
            "success": False,
            "error": "Missing dependencies. Install with: pip install pandas numpy scipy"
        }
    
    logger.info(f"Computing returns distribution for {symbol}")
    
    try:
        # 1. Generate data
        data = _compute_session_returns(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            bps_factor=bps_factor,
        )
        
        if "error" in data:
            return {"success": False, "error": data["error"]}
        
        # 2. Auto-generate plots using core plotting
        output_path = Path(output_dir) if output_dir else None
        
        plot_result = create_plot(
            data={
                "sessions": data["sessions"],
                "all_day": data["all_day"],
                "latest": data["latest"],
                "metadata": {"symbol": symbol, "interval": interval},
            },
            context="returns_distribution",
            title=f"{symbol} Session Returns Distribution",
            output_dir=output_path,
        )
        
        # 3. Return combined result
        return {
            "success": True,
            "symbol": symbol,
            "interval": interval,
            "bps_factor": data["bps_factor"],
            "data_points": len(data["all_day"]),
            "date_range": data["date_range"],
            "statistics": data["statistics"],
            "latest": data["latest"],
            "plot_paths": plot_result.get("plot_paths", []),
        }
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return {"success": False, "error": f"Import failed: {str(e)}"}
    except Exception as e:
        logger.exception(f"Error computing returns distribution: {e}")
        return {"success": False, "error": str(e)}
