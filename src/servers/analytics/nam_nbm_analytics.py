"""
NAM/NBM Analytics - Price stretch detection tools.

Provides MCP tools for finding price stretches (sustained moves before pullback):
- find_price_stretches: Find all upward/downward stretches
- find_primary_secondary_stretches: Find primary stretches with corresponding secondary moves

This module is self-contained with no external dependencies beyond pandas/numpy.
"""

import logging
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd

from src.mcp.discovery import register_tool

logger = logging.getLogger(__name__)


# =============================================================================
# Core Stretch Algorithms
# =============================================================================


def _compute_down_stretches_hilo(
    high: pd.Series,
    low: pd.Series,
    threshold_points: float,
    finalize: bool = False
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Compute downward stretches using high/low with pullback-first logic.
    
    Args:
        high: Series of high prices
        low: Series of low prices
        threshold_points: Pullback threshold in basis points (bps)
        finalize: Whether to include the last incomplete stretch
    
    Returns:
        Tuple of (stretches_array, metadata_dict):
        - stretches_array: np.ndarray of COMPLETED stretches in bps
        - metadata_dict: dict with 'max_stretch' and 'stretch_info'
    """
    high_arr = high.to_numpy(dtype=float, copy=False)
    low_arr = low.to_numpy(dtype=float, copy=False)
    
    if len(high_arr) != len(low_arr):
        raise ValueError("high and low series must have the same length")
    
    valid_mask = np.isfinite(high_arr) & np.isfinite(low_arr)
    high_arr = high_arr[valid_mask]
    low_arr = low_arr[valid_mask]
    
    if len(high_arr) == 0:
        return np.array([]), {'max_stretch': 0.0, 'stretch_info': []}
    
    stretches = []
    stretch_info = []  # List of (H_idx, L_idx, pullback_idx, drop_value, pullback_value)
    H = high_arr[0]
    L = low_arr[0]
    H_idx = 0
    L_idx = 0
    max_stretch = 0.0
    
    for i in range(1, len(high_arr)):
        pullback = (high_arr[i] - L) * 16
        
        if pullback >= threshold_points:
            if H_idx != L_idx:
                drop = (H - L) * 16
                if drop > 0:
                    stretches.append(drop)
                    stretch_info.append((H_idx, L_idx, i, drop, pullback))
            H = high_arr[i]
            L = low_arr[i]
            H_idx = i
            L_idx = i
        else:
            if low_arr[i] < L:
                L = low_arr[i]
                L_idx = i
            if high_arr[i] > H:
                H = high_arr[i]
                L = low_arr[i]
                H_idx = i
                L_idx = i
        
        max_stretch = max(max_stretch, (H - L) * 16)
    
    if finalize and H_idx != L_idx:
        drop = (H - L) * 16
        if drop > 0:
            stretches.append(drop)
            final_pullback = (high_arr[-1] - L) * 16
            stretch_info.append((H_idx, L_idx, len(high_arr)-1, drop, final_pullback))
    
    return np.array(stretches), {'max_stretch': max_stretch, 'stretch_info': stretch_info}


def _compute_up_stretches_hilo(
    high: pd.Series,
    low: pd.Series,
    threshold_points: float,
    finalize: bool = False
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Compute upward stretches using high/low with pullback-first logic.
    
    Args:
        high: Series of high prices
        low: Series of low prices
        threshold_points: Pullback threshold in basis points (bps)
        finalize: Whether to include the last incomplete stretch
    
    Returns:
        Tuple of (stretches_array, metadata_dict):
        - stretches_array: np.ndarray of COMPLETED stretches in bps
        - metadata_dict: dict with 'max_stretch' and 'stretch_info'
    """
    high_arr = high.to_numpy(dtype=float, copy=False)
    low_arr = low.to_numpy(dtype=float, copy=False)
    
    if len(high_arr) != len(low_arr):
        raise ValueError("high and low series must have the same length")
    
    valid_mask = np.isfinite(high_arr) & np.isfinite(low_arr)
    high_arr = high_arr[valid_mask]
    low_arr = low_arr[valid_mask]
    
    if len(high_arr) == 0:
        return np.array([]), {'max_stretch': 0.0, 'stretch_info': []}
    
    stretches = []
    stretch_info = []  # List of (L_idx, H_idx, pullback_idx, rise_value, pullback_value)
    H = high_arr[0]
    L = low_arr[0]
    H_idx = 0
    L_idx = 0
    max_stretch = 0.0
    
    for i in range(1, len(high_arr)):
        pullback = (H - low_arr[i]) * 16
        
        if pullback >= threshold_points:
            if H_idx != L_idx:
                rise = (H - L) * 16
                if rise > 0:
                    stretches.append(rise)
                    stretch_info.append((L_idx, H_idx, i, rise, pullback))
            H = high_arr[i]
            L = low_arr[i]
            H_idx = i
            L_idx = i
        else:
            if high_arr[i] > H:
                H = high_arr[i]
                H_idx = i
            if low_arr[i] < L:
                L = low_arr[i]
                H = high_arr[i]
                H_idx = i
                L_idx = i
        
        max_stretch = max(max_stretch, (H - L) * 16)
    
    if finalize and H_idx != L_idx:
        rise = (H - L) * 16
        if rise > 0:
            stretches.append(rise)
            final_pullback = (H - low_arr[-1]) * 16
            stretch_info.append((L_idx, H_idx, len(high_arr)-1, rise, final_pullback))
    
    return np.array(stretches), {'max_stretch': max_stretch, 'stretch_info': stretch_info}


# =============================================================================
# Stretch Detection Functions
# =============================================================================


def _find_stretches(
    df: pd.DataFrame,
    threshold_bps: float,
    include_final: bool = False
) -> List[Dict]:
    """
    Find all upward and downward stretches in the price data.
    
    Args:
        df: DataFrame with 'high', 'low', 'timestamp' columns
        threshold_bps: Pullback threshold in basis points
        include_final: Whether to include the last incomplete stretch
    
    Returns:
        List of dictionaries with stretch details, sorted by start timestamp.
    """
    if 'high' not in df.columns or 'low' not in df.columns or 'timestamp' not in df.columns:
        raise ValueError("DataFrame must have 'high', 'low', and 'timestamp' columns")
    
    high_series = df['high']
    low_series = df['low']
    
    stretches = []
    
    # Find downward stretches
    down_values, down_meta = _compute_down_stretches_hilo(
        high_series, low_series, threshold_bps, finalize=include_final
    )
    
    for info in down_meta.get('stretch_info', []):
        high_idx, low_idx, pullback_idx, stretch_value, pullback_value = info
        high_idx = int(high_idx)
        low_idx = int(low_idx)
        pullback_idx = int(pullback_idx)
        
        stretches.append({
            'direction': 'down',
            'start_idx': high_idx,
            'start_timestamp': df.iloc[high_idx]['timestamp'],
            'start_price': df.iloc[high_idx]['high'],
            'end_idx': low_idx,
            'end_timestamp': df.iloc[low_idx]['timestamp'],
            'end_price': df.iloc[low_idx]['low'],
            'pullback_idx': pullback_idx,
            'pullback_timestamp': df.iloc[pullback_idx]['timestamp'],
            'pullback_price': df.iloc[pullback_idx]['high'],
            'stretch_bps': stretch_value
        })
    
    # Find upward stretches
    up_values, up_meta = _compute_up_stretches_hilo(
        high_series, low_series, threshold_bps, finalize=include_final
    )
    
    for info in up_meta.get('stretch_info', []):
        low_idx, high_idx, pullback_idx, stretch_value, pullback_value = info
        low_idx = int(low_idx)
        high_idx = int(high_idx)
        pullback_idx = int(pullback_idx)
        
        stretches.append({
            'direction': 'up',
            'start_idx': low_idx,
            'start_timestamp': df.iloc[low_idx]['timestamp'],
            'start_price': df.iloc[low_idx]['low'],
            'end_idx': high_idx,
            'end_timestamp': df.iloc[high_idx]['timestamp'],
            'end_price': df.iloc[high_idx]['high'],
            'pullback_idx': pullback_idx,
            'pullback_timestamp': df.iloc[pullback_idx]['timestamp'],
            'pullback_price': df.iloc[pullback_idx]['low'],
            'stretch_bps': stretch_value
        })
    
    stretches.sort(key=lambda x: x['start_timestamp'])
    return stretches


def _find_first_stretch_in_direction(
    df: pd.DataFrame,
    direction: str,
    threshold_bps: float,
    start_offset: int = 0,
    highs: Optional[np.ndarray] = None,
    lows: Optional[np.ndarray] = None
) -> Optional[Dict]:
    """
    Find the FIRST stretch in the given direction, stopping immediately when found.
    
    OPTIMIZED: Does not copy the DataFrame - uses an offset into the original arrays.
    Returns as soon as the first stretch is detected.
    
    Args:
        df: DataFrame with 'high', 'low', 'timestamp' columns
        direction: 'up' or 'down'
        threshold_bps: Pullback threshold in basis points
        start_offset: Index to start searching from (avoids DataFrame slicing)
        highs: Pre-extracted numpy array of highs (optional, for performance)
        lows: Pre-extracted numpy array of lows (optional, for performance)
    
    Returns:
        Dictionary with stretch info (indices are absolute), or None if no stretch found.
    """
    n = len(df)
    if n - start_offset < 2:
        return None
    
    threshold_pts = threshold_bps / 16  # Convert bps to price points
    
    if highs is None:
        highs = df['high'].values
    if lows is None:
        lows = df['low'].values
    
    if direction == 'down':
        high_val = highs[start_offset]
        high_idx = start_offset
        low_val = lows[start_offset]
        low_idx = start_offset
        
        for i in range(start_offset + 1, n):
            h = highs[i]
            l = lows[i]
            
            if h - low_val >= threshold_pts:
                stretch_bps = (high_val - low_val) * 16
                return {
                    'direction': 'down',
                    'start_idx': high_idx,
                    'start_timestamp': df.iloc[high_idx]['timestamp'],
                    'start_price': high_val,
                    'end_idx': low_idx,
                    'end_timestamp': df.iloc[low_idx]['timestamp'],
                    'end_price': low_val,
                    'pullback_idx': i,
                    'pullback_timestamp': df.iloc[i]['timestamp'],
                    'pullback_price': h,
                    'stretch_bps': stretch_bps
                }
            
            if l < low_val:
                low_val = l
                low_idx = i
            if h > high_val:
                high_val = h
                high_idx = i
                low_val = l
                low_idx = i
                
    else:  # direction == 'up'
        low_val = lows[start_offset]
        low_idx = start_offset
        high_val = highs[start_offset]
        high_idx = start_offset
        
        for i in range(start_offset + 1, n):
            h = highs[i]
            l = lows[i]
            
            if high_val - l >= threshold_pts:
                stretch_bps = (high_val - low_val) * 16
                return {
                    'direction': 'up',
                    'start_idx': low_idx,
                    'start_timestamp': df.iloc[low_idx]['timestamp'],
                    'start_price': low_val,
                    'end_idx': high_idx,
                    'end_timestamp': df.iloc[high_idx]['timestamp'],
                    'end_price': high_val,
                    'pullback_idx': i,
                    'pullback_timestamp': df.iloc[i]['timestamp'],
                    'pullback_price': l,
                    'stretch_bps': stretch_bps
                }
            
            if h > high_val:
                high_val = h
                high_idx = i
            if l < low_val:
                low_val = l
                low_idx = i
                high_val = h
                high_idx = i
    
    return None


def _find_stretches_with_secondary(
    df: pd.DataFrame,
    primary_threshold_bps: float,
    secondary_threshold_bps: float,
    primary_direction: str = 'down'
) -> List[Tuple[Dict, Optional[Dict]]]:
    """
    Find all primary stretches and their corresponding secondary stretches.
    
    The secondary stretch starts exactly where the primary ends (opposite direction).
    
    Args:
        df: DataFrame with 'high', 'low', 'timestamp' columns
        primary_threshold_bps: Pullback threshold for primary stretches (bps)
        secondary_threshold_bps: Pullback threshold for secondary stretches (bps)
        primary_direction: 'down' or 'up' for primary stretches
    
    Returns:
        List of tuples: [(primary_dict, secondary_dict), ...]
    """
    if 'high' not in df.columns or 'low' not in df.columns or 'timestamp' not in df.columns:
        raise ValueError("DataFrame must have 'high', 'low', and 'timestamp' columns")
    
    df = df.reset_index(drop=True)
    
    high_series = df['high']
    low_series = df['low']
    
    secondary_direction = 'up' if primary_direction == 'down' else 'down'
    
    highs_arr = df['high'].values
    lows_arr = df['low'].values
    
    if primary_direction == 'down':
        values, meta = _compute_down_stretches_hilo(high_series, low_series, primary_threshold_bps, finalize=False)
    else:
        values, meta = _compute_up_stretches_hilo(high_series, low_series, primary_threshold_bps, finalize=False)
    
    primary_infos = meta.get('stretch_info', [])
    
    results = []
    
    for info in primary_infos:
        if primary_direction == 'down':
            high_idx, low_idx, pullback_idx, stretch_value, pullback_value = info
            primary = {
                'direction': 'down',
                'start_idx': int(high_idx),
                'start_timestamp': df.iloc[int(high_idx)]['timestamp'],
                'start_price': df.iloc[int(high_idx)]['high'],
                'end_idx': int(low_idx),
                'end_timestamp': df.iloc[int(low_idx)]['timestamp'],
                'end_price': df.iloc[int(low_idx)]['low'],
                'pullback_idx': int(pullback_idx),
                'pullback_timestamp': df.iloc[int(pullback_idx)]['timestamp'],
                'pullback_price': df.iloc[int(pullback_idx)]['high'],
                'stretch_bps': stretch_value
            }
            primary_end_idx = int(low_idx)
        else:
            low_idx, high_idx, pullback_idx, stretch_value, pullback_value = info
            primary = {
                'direction': 'up',
                'start_idx': int(low_idx),
                'start_timestamp': df.iloc[int(low_idx)]['timestamp'],
                'start_price': df.iloc[int(low_idx)]['low'],
                'end_idx': int(high_idx),
                'end_timestamp': df.iloc[int(high_idx)]['timestamp'],
                'end_price': df.iloc[int(high_idx)]['high'],
                'pullback_idx': int(pullback_idx),
                'pullback_timestamp': df.iloc[int(pullback_idx)]['timestamp'],
                'pullback_price': df.iloc[int(pullback_idx)]['low'],
                'stretch_bps': stretch_value
            }
            primary_end_idx = int(high_idx)
        
        secondary = _find_first_stretch_in_direction(
            df, secondary_direction, secondary_threshold_bps,
            start_offset=primary_end_idx, highs=highs_arr, lows=lows_arr
        )
        
        results.append((primary, secondary))
    
    return results


# =============================================================================
# Helper Functions
# =============================================================================


def _ohlc_list_to_dataframe(ohlc_data: List[Dict]) -> pd.DataFrame:
    """
    Convert list of OHLC dicts to DataFrame.
    
    Args:
        ohlc_data: List of dicts with 'high', 'low', 'timestamp' keys
        
    Returns:
        DataFrame with high, low, timestamp columns
    """
    df = pd.DataFrame(ohlc_data)
    required_cols = {'high', 'low', 'timestamp'}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return df


def _serialize_stretches(stretches: List[Dict]) -> List[Dict]:
    """Convert stretch dicts to JSON-serializable format."""
    result = []
    for s in stretches:
        serialized = {}
        for k, v in s.items():
            if hasattr(v, 'isoformat'):
                serialized[k] = v.isoformat()
            else:
                serialized[k] = v
        result.append(serialized)
    return result


# =============================================================================
# MCP Tools
# =============================================================================


@register_tool(
    name="find_price_stretches",
    description="Find all upward and downward price stretches in OHLC data. "
                "A stretch is a sustained price move (up or down) before a pullback "
                "exceeding the threshold. Returns stretches sorted by start timestamp.",
    input_schema={
        "type": "object",
        "properties": {
            "ohlc_data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "high": {"type": "number"},
                        "low": {"type": "number"},
                        "timestamp": {"type": "string"}
                    },
                    "required": ["high", "low", "timestamp"]
                },
                "description": "List of OHLC bars with high, low, timestamp fields"
            },
            "threshold_bps": {
                "type": "number",
                "description": "Pullback threshold in basis points (1 bps = 1/16th of a point)"
            },
            "include_final": {
                "type": "boolean",
                "description": "Whether to include the last incomplete stretch. Default: false"
            }
        },
        "required": ["ohlc_data", "threshold_bps"]
    }
)
def find_price_stretches(
    ohlc_data: List[Dict],
    threshold_bps: float,
    include_final: bool = False
) -> Dict[str, Any]:
    """
    Find all upward and downward price stretches in OHLC data.
    
    Args:
        ohlc_data: List of dicts with 'high', 'low', 'timestamp' keys
        threshold_bps: Pullback threshold in basis points
        include_final: Whether to include the last incomplete stretch
        
    Returns:
        Dictionary with stretches and summary statistics
    """
    logger.info(f"Finding price stretches in {len(ohlc_data)} bars, threshold={threshold_bps} bps")
    
    try:
        df = _ohlc_list_to_dataframe(ohlc_data)
        stretches = _find_stretches(df, threshold_bps, include_final)
        
        serialized = _serialize_stretches(stretches)
        
        up_stretches = [s for s in stretches if s['direction'] == 'up']
        down_stretches = [s for s in stretches if s['direction'] == 'down']
        
        logger.info(f"Found {len(stretches)} stretches ({len(up_stretches)} up, {len(down_stretches)} down)")
        
        return {
            "success": True,
            "stretches": serialized,
            "summary": {
                "total_count": len(stretches),
                "up_count": len(up_stretches),
                "down_count": len(down_stretches),
                "threshold_bps": threshold_bps
            }
        }
        
    except Exception as e:
        logger.error(f"Error finding price stretches: {e}")
        return {
            "success": False,
            "error": str(e),
            "stretches": []
        }


@register_tool(
    name="find_primary_secondary_stretches",
    description="Find primary price stretches and their corresponding secondary stretches. "
                "The secondary stretch starts where the primary ends (opposite direction). "
                "Useful for analyzing retracement patterns.",
    input_schema={
        "type": "object",
        "properties": {
            "ohlc_data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "high": {"type": "number"},
                        "low": {"type": "number"},
                        "timestamp": {"type": "string"}
                    },
                    "required": ["high", "low", "timestamp"]
                },
                "description": "List of OHLC bars with high, low, timestamp fields"
            },
            "primary_threshold_bps": {
                "type": "number",
                "description": "Pullback threshold for primary stretches (basis points)"
            },
            "secondary_threshold_bps": {
                "type": "number",
                "description": "Pullback threshold for secondary stretches (basis points)"
            },
            "primary_direction": {
                "type": "string",
                "enum": ["down", "up"],
                "description": "Direction of primary stretches: 'down' or 'up'. Default: 'down'"
            }
        },
        "required": ["ohlc_data", "primary_threshold_bps", "secondary_threshold_bps"]
    }
)
def find_primary_secondary_stretches(
    ohlc_data: List[Dict],
    primary_threshold_bps: float,
    secondary_threshold_bps: float,
    primary_direction: str = "down"
) -> Dict[str, Any]:
    """
    Find primary stretches and their corresponding secondary stretches.
    
    Args:
        ohlc_data: List of dicts with 'high', 'low', 'timestamp' keys
        primary_threshold_bps: Pullback threshold for primary stretches (bps)
        secondary_threshold_bps: Pullback threshold for secondary stretches (bps)
        primary_direction: Direction of primary stretches ('down' or 'up')
        
    Returns:
        Dictionary with primary/secondary stretch pairs and summary
    """
    logger.info(
        f"Finding primary/secondary stretches in {len(ohlc_data)} bars, "
        f"primary={primary_threshold_bps} bps, secondary={secondary_threshold_bps} bps, "
        f"direction={primary_direction}"
    )
    
    try:
        df = _ohlc_list_to_dataframe(ohlc_data)
        results = _find_stretches_with_secondary(
            df, primary_threshold_bps, secondary_threshold_bps, primary_direction
        )
        
        pairs = []
        with_secondary_count = 0
        for primary, secondary in results:
            pair = {
                "primary": _serialize_stretches([primary])[0],
                "secondary": _serialize_stretches([secondary])[0] if secondary else None
            }
            pairs.append(pair)
            if secondary is not None:
                with_secondary_count += 1
        
        logger.info(
            f"Found {len(pairs)} primary stretches, "
            f"{with_secondary_count} with secondary stretches"
        )
        
        return {
            "success": True,
            "pairs": pairs,
            "summary": {
                "primary_count": len(pairs),
                "with_secondary_count": with_secondary_count,
                "without_secondary_count": len(pairs) - with_secondary_count,
                "primary_direction": primary_direction,
                "secondary_direction": "up" if primary_direction == "down" else "down",
                "primary_threshold_bps": primary_threshold_bps,
                "secondary_threshold_bps": secondary_threshold_bps
            }
        }
        
    except Exception as e:
        logger.error(f"Error finding primary/secondary stretches: {e}")
        return {
            "success": False,
            "error": str(e),
            "pairs": []
        }
