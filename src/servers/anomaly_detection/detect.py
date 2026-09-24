"""Anomaly detection algorithms for market data."""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from src.mcp.discovery import register_tool

logger = logging.getLogger(__name__)

# Detection thresholds
DEFAULT_Z_THRESHOLD = 1.5
ROLLING_WINDOW_DAYS = 20
MIN_DATA_POINTS = 10


def _get_pg_connection():
    """Get PostgreSQL connection using config settings."""
    import sys
    from pathlib import Path
    
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))
    
    from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
    
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def _price_to_bps(price_diff: float) -> float:
    """Convert price difference to basis points (TY: 1 point = 100 bps)."""
    return price_diff * 100


@register_tool(
    name="calculate_daily_metrics",
    description="Calculate daily metrics (range, move, max spike) for a date range.",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Symbol to analyze (default: TY)"},
        },
        "required": ["start_date", "end_date"],
    },
    agent="anomaly_notes_agent",
)
def calculate_daily_metrics(
    start_date: str,
    end_date: str,
    symbol: str = "TY",
) -> Dict[str, Any]:
    """
    Calculate daily metrics for anomaly detection.
    
    Metrics calculated:
    - daily_range_bps: (High - Low) in basis points
    - daily_move_bps: abs(Close - PrevClose) in basis points
    - max_5min_spike_bps: Maximum 5-minute move within the day
    
    Returns:
        Dictionary with daily metrics array
    """
    conn = _get_pg_connection()
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get daily OHLC summary using proper aggregation
            cur.execute(
                """
                WITH daily_agg AS (
                    SELECT 
                        DATE(ts_event) as trade_date,
                        (array_agg(open ORDER BY ts_event ASC))[1] as day_open,
                        MAX(high) as day_high,
                        MIN(low) as day_low,
                        (array_agg(close ORDER BY ts_event DESC))[1] as day_close
                    FROM ohlcv_1m
                    WHERE symbol LIKE %s
                      AND DATE(ts_event) >= %s
                      AND DATE(ts_event) <= %s
                    GROUP BY DATE(ts_event)
                )
                SELECT 
                    trade_date,
                    day_open,
                    day_high,
                    day_low,
                    day_close,
                    LAG(day_close) OVER (ORDER BY trade_date) as prev_close
                FROM daily_agg
                ORDER BY trade_date
                """,
                (f"{symbol}%", start_date, end_date),
            )
            
            rows = cur.fetchall()
            
            if not rows:
                logger.warning(f"No data found for {symbol} from {start_date} to {end_date}")
                return {
                    "success": False,
                    "metrics": [],
                    "count": 0,
                    "error": "No data found",
                }
            
            # Calculate metrics
            metrics = []
            for row in rows:
                if row["day_high"] is None or row["day_low"] is None:
                    continue
                    
                daily_range_bps = _price_to_bps(row["day_high"] - row["day_low"])
                
                # Calculate daily move (close to close)
                if row["prev_close"] is not None:
                    daily_move_bps = abs(_price_to_bps(row["day_close"] - row["prev_close"]))
                else:
                    daily_move_bps = None
                
                metrics.append({
                    "date": row["trade_date"].isoformat(),
                    "open": float(row["day_open"]) if row["day_open"] else None,
                    "high": float(row["day_high"]),
                    "low": float(row["day_low"]),
                    "close": float(row["day_close"]) if row["day_close"] else None,
                    "daily_range_bps": round(daily_range_bps, 2),
                    "daily_move_bps": round(daily_move_bps, 2) if daily_move_bps is not None else None,
                })
            
            logger.info(f"Calculated metrics for {len(metrics)} days")
            
            return {
                "success": True,
                "metrics": metrics,
                "count": len(metrics),
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
            }
            
    except Exception as e:
        logger.error(f"Failed to calculate daily metrics: {e}")
        return {
            "success": False,
            "metrics": [],
            "count": 0,
            "error": str(e),
        }
    finally:
        conn.close()


def _calculate_rolling_stats(
    values: List[float],
    window: int = ROLLING_WINDOW_DAYS,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Calculate rolling mean and std for a list of values."""
    means = []
    stds = []
    
    for i in range(len(values)):
        if i < window - 1:
            # Not enough data yet
            means.append(None)
            stds.append(None)
        else:
            window_values = values[i - window + 1:i + 1]
            mean = sum(window_values) / len(window_values)
            variance = sum((v - mean) ** 2 for v in window_values) / len(window_values)
            std = variance ** 0.5
            means.append(mean)
            stds.append(std)
    
    return means, stds


@register_tool(
    name="detect_anomalies",
    description="Detect anomalous market days based on z-score threshold.",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Symbol to analyze (default: TY)"},
            "z_threshold": {"type": "number", "description": "Z-score threshold (default: 1.5)"},
        },
        "required": ["start_date", "end_date"],
    },
    agent="anomaly_notes_agent",
)
def detect_anomalies(
    start_date: str,
    end_date: str,
    symbol: str = "TY",
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> Dict[str, Any]:
    """
    Detect anomalous market days.
    
    An anomaly is flagged when:
    - Daily range > z_threshold standard deviations from 20-day mean
    - Daily move (close-to-close) > z_threshold standard deviations from 20-day mean
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        symbol: Symbol to analyze
        z_threshold: Z-score threshold for flagging anomalies
        
    Returns:
        Dictionary with anomaly list
    """
    # Get extra lookback data for rolling window calculation
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    lookback_start = (start_dt - timedelta(days=ROLLING_WINDOW_DAYS + 10)).strftime("%Y-%m-%d")
    
    # Get daily metrics
    metrics_result = calculate_daily_metrics(lookback_start, end_date, symbol)
    
    if not metrics_result["success"]:
        return {
            "success": False,
            "anomalies": [],
            "count": 0,
            "error": metrics_result.get("error", "Failed to calculate metrics"),
        }
    
    metrics = metrics_result["metrics"]
    
    if len(metrics) < ROLLING_WINDOW_DAYS:
        return {
            "success": False,
            "anomalies": [],
            "count": 0,
            "error": f"Insufficient data: need at least {ROLLING_WINDOW_DAYS} days",
        }
    
    # Extract value arrays
    ranges = [m["daily_range_bps"] for m in metrics]
    moves = [m["daily_move_bps"] if m["daily_move_bps"] is not None else 0 for m in metrics]
    
    # Calculate rolling statistics
    range_means, range_stds = _calculate_rolling_stats(ranges)
    move_means, move_stds = _calculate_rolling_stats(moves)
    
    # Detect anomalies
    anomalies = []
    
    for i, m in enumerate(metrics):
        # Skip if before our target date range
        if m["date"] < start_date:
            continue
        
        # Skip if not enough rolling data
        if range_means[i] is None or range_stds[i] is None:
            continue
        if range_stds[i] == 0:
            continue
        
        date = m["date"]
        detected_types = []
        
        # Check daily range
        range_z = (m["daily_range_bps"] - range_means[i]) / range_stds[i] if range_stds[i] > 0 else 0
        if range_z > z_threshold:
            detected_types.append({
                "type": "large_range",
                "z_score": round(range_z, 2),
                "value_bps": m["daily_range_bps"],
                "threshold_bps": round(range_means[i] + z_threshold * range_stds[i], 2),
                "mean_bps": round(range_means[i], 2),
                "std_bps": round(range_stds[i], 2),
            })
        
        # Check daily move
        if m["daily_move_bps"] is not None and move_stds[i] is not None and move_stds[i] > 0:
            move_z = (m["daily_move_bps"] - move_means[i]) / move_stds[i]
            if move_z > z_threshold:
                detected_types.append({
                    "type": "large_move",
                    "z_score": round(move_z, 2),
                    "value_bps": m["daily_move_bps"],
                    "threshold_bps": round(move_means[i] + z_threshold * move_stds[i], 2),
                    "mean_bps": round(move_means[i], 2),
                    "std_bps": round(move_stds[i], 2),
                })
        
        if detected_types:
            anomalies.append({
                "date": date,
                "open": m["open"],
                "high": m["high"],
                "low": m["low"],
                "close": m["close"],
                "anomaly_types": detected_types,
            })
    
    logger.info(f"Detected {len(anomalies)} anomaly days out of {len([m for m in metrics if m['date'] >= start_date])} days")
    
    return {
        "success": True,
        "anomalies": anomalies,
        "count": len(anomalies),
        "total_days": len([m for m in metrics if m["date"] >= start_date]),
        "z_threshold": z_threshold,
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
    }

