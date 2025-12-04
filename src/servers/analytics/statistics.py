"""
Statistical analysis tools - Pure Python implementation.

Provides statistical functions using only the standard library:
- Descriptive statistics (mean, median, mode, std dev, variance)
- Percentiles and quantiles
- Z-scores and outlier detection
- Distribution analysis (skewness, kurtosis)
"""

import logging
import math
from typing import Dict, Any, List, Optional, Tuple

from src.mcp.discovery import register_tool
from .schema import (
    DEFAULT_PERCENTILES,
    validate_numeric_data,
    format_number,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Core Statistical Functions (Pure Python)
# =============================================================================


def _mean(data: List[float]) -> float:
    """Calculate arithmetic mean."""
    return sum(data) / len(data)


def _median(data: List[float]) -> float:
    """Calculate median (50th percentile)."""
    sorted_data = sorted(data)
    n = len(sorted_data)
    mid = n // 2
    
    if n % 2 == 0:
        return (sorted_data[mid - 1] + sorted_data[mid]) / 2
    return sorted_data[mid]


def _mode(data: List[float]) -> Optional[float]:
    """Calculate mode (most frequent value). Returns None if no unique mode."""
    if not data:
        return None
    
    freq = {}
    for val in data:
        freq[val] = freq.get(val, 0) + 1
    
    max_freq = max(freq.values())
    if max_freq == 1:
        return None  # No repeated values
    
    modes = [k for k, v in freq.items() if v == max_freq]
    if len(modes) > 1:
        return None  # Multiple modes
    
    return modes[0]


def _variance(data: List[float], sample: bool = True) -> float:
    """
    Calculate variance.
    
    Args:
        data: List of numeric values
        sample: If True, use sample variance (n-1), else population variance (n)
    """
    n = len(data)
    if n < 2:
        return 0.0
    
    mean = _mean(data)
    squared_diffs = [(x - mean) ** 2 for x in data]
    
    divisor = n - 1 if sample else n
    return sum(squared_diffs) / divisor


def _std_dev(data: List[float], sample: bool = True) -> float:
    """Calculate standard deviation."""
    return math.sqrt(_variance(data, sample))


def _percentile(data: List[float], p: float) -> float:
    """
    Calculate percentile using linear interpolation.
    
    Args:
        data: List of numeric values (will be sorted)
        p: Percentile (0-100)
    """
    if not 0 <= p <= 100:
        raise ValueError(f"Percentile must be 0-100, got {p}")
    
    sorted_data = sorted(data)
    n = len(sorted_data)
    
    if n == 1:
        return sorted_data[0]
    
    # Calculate index
    k = (p / 100) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    
    if f == c:
        return sorted_data[int(k)]
    
    # Linear interpolation
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def _percentiles(data: List[float], percentile_list: List[int]) -> Dict[int, float]:
    """Calculate multiple percentiles at once."""
    return {p: _percentile(data, p) for p in percentile_list}


def _z_score(value: float, mean: float, std_dev: float) -> float:
    """Calculate z-score for a single value."""
    if std_dev == 0:
        return 0.0
    return (value - mean) / std_dev


def _z_scores(data: List[float]) -> List[float]:
    """Calculate z-scores for all values in data."""
    mean = _mean(data)
    std = _std_dev(data)
    return [_z_score(x, mean, std) for x in data]


def _skewness(data: List[float]) -> float:
    """
    Calculate skewness (measure of asymmetry).
    
    Positive = right tail longer
    Negative = left tail longer
    Zero = symmetric
    """
    n = len(data)
    if n < 3:
        return 0.0
    
    mean = _mean(data)
    std = _std_dev(data, sample=False)
    
    if std == 0:
        return 0.0
    
    # Third moment
    m3 = sum((x - mean) ** 3 for x in data) / n
    return m3 / (std ** 3)


def _kurtosis(data: List[float]) -> float:
    """
    Calculate excess kurtosis (measure of tail heaviness).
    
    Positive = heavy tails (leptokurtic)
    Negative = light tails (platykurtic)
    Zero = normal distribution (mesokurtic)
    """
    n = len(data)
    if n < 4:
        return 0.0
    
    mean = _mean(data)
    std = _std_dev(data, sample=False)
    
    if std == 0:
        return 0.0
    
    # Fourth moment
    m4 = sum((x - mean) ** 4 for x in data) / n
    return (m4 / (std ** 4)) - 3  # Excess kurtosis


def _iqr(data: List[float]) -> float:
    """Calculate interquartile range (Q3 - Q1)."""
    q1 = _percentile(data, 25)
    q3 = _percentile(data, 75)
    return q3 - q1


def _detect_outliers(data: List[float], method: str = "iqr", threshold: float = 1.5) -> List[Tuple[int, float]]:
    """
    Detect outliers in data.
    
    Args:
        data: List of numeric values
        method: "iqr" (IQR method) or "zscore" (z-score method)
        threshold: For IQR: multiplier (default 1.5), for z-score: threshold (default 3)
        
    Returns:
        List of (index, value) tuples for outliers
    """
    outliers = []
    
    if method == "iqr":
        q1 = _percentile(data, 25)
        q3 = _percentile(data, 75)
        iqr = q3 - q1
        lower = q1 - threshold * iqr
        upper = q3 + threshold * iqr
        
        for i, val in enumerate(data):
            if val < lower or val > upper:
                outliers.append((i, val))
    
    elif method == "zscore":
        mean = _mean(data)
        std = _std_dev(data)
        
        for i, val in enumerate(data):
            z = abs(_z_score(val, mean, std))
            if z > threshold:
                outliers.append((i, val))
    
    return outliers


# =============================================================================
# Registered MCP Tools
# =============================================================================


@register_tool(
    name="compute_statistics",
    description="Compute comprehensive descriptive statistics for a list of numeric values. "
                "Returns mean, median, std dev, variance, percentiles, skewness, kurtosis, and more.",
    input_schema={
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of numeric values to analyze"
            },
            "percentiles": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "List of percentiles to compute (0-100). Default: [5, 10, 25, 50, 75, 90, 95]"
            },
            "include_outliers": {
                "type": "boolean",
                "description": "Whether to detect and include outliers in output. Default: false"
            },
            "outlier_method": {
                "type": "string",
                "enum": ["iqr", "zscore"],
                "description": "Method for outlier detection: 'iqr' or 'zscore'. Default: 'iqr'"
            }
        },
        "required": ["data"]
    }
)
def compute_statistics(
    data: List[Any],
    percentiles: Optional[List[int]] = None,
    include_outliers: bool = False,
    outlier_method: str = "iqr"
) -> Dict[str, Any]:
    """
    Compute comprehensive descriptive statistics.
    
    Args:
        data: List of numeric values
        percentiles: Percentiles to compute (default: [5, 10, 25, 50, 75, 90, 95])
        include_outliers: Whether to detect outliers
        outlier_method: "iqr" or "zscore"
        
    Returns:
        Dictionary with statistics and metadata
    """
    logger.info(f"Computing statistics for {len(data)} values")
    
    # Validate and convert data
    is_valid, error, numeric_data = validate_numeric_data(data)
    if not is_valid:
        return {
            "success": False,
            "error": error,
            "statistics": {}
        }
    
    # Use default percentiles if not specified
    if percentiles is None:
        percentiles = DEFAULT_PERCENTILES
    
    try:
        # Core statistics
        mean = _mean(numeric_data)
        median = _median(numeric_data)
        mode = _mode(numeric_data)
        std = _std_dev(numeric_data)
        var = _variance(numeric_data)
        
        min_val = min(numeric_data)
        max_val = max(numeric_data)
        range_val = max_val - min_val
        
        # Percentiles
        pct_values = _percentiles(numeric_data, percentiles)
        
        # Distribution shape
        skew = _skewness(numeric_data)
        kurt = _kurtosis(numeric_data)
        iqr = _iqr(numeric_data)
        
        # Build result
        stats = {
            "count": len(numeric_data),
            "mean": round(mean, 6),
            "median": round(median, 6),
            "mode": round(mode, 6) if mode is not None else None,
            "std_dev": round(std, 6),
            "variance": round(var, 6),
            "min": round(min_val, 6),
            "max": round(max_val, 6),
            "range": round(range_val, 6),
            "iqr": round(iqr, 6),
            "percentiles": {str(k): round(v, 6) for k, v in pct_values.items()},
            "skewness": round(skew, 6),
            "kurtosis": round(kurt, 6),
        }
        
        # Outlier detection
        if include_outliers:
            outliers = _detect_outliers(numeric_data, method=outlier_method)
            stats["outliers"] = {
                "method": outlier_method,
                "count": len(outliers),
                "values": [{"index": i, "value": round(v, 6)} for i, v in outliers]
            }
        
        logger.info(f"Statistics computed: mean={mean:.4f}, std={std:.4f}")
        
        return {
            "success": True,
            "statistics": stats,
            "summary": f"n={len(numeric_data)}, mean={format_number(mean)}, std={format_number(std)}, range=[{format_number(min_val)}, {format_number(max_val)}]"
        }
        
    except Exception as e:
        logger.error(f"Error computing statistics: {e}")
        return {
            "success": False,
            "error": str(e),
            "statistics": {}
        }


@register_tool(
    name="compute_percentile_rank",
    description="Compute the percentile rank of a specific value within a dataset. "
                "Useful for understanding where a value falls in a distribution.",
    input_schema={
        "type": "object",
        "properties": {
            "value": {
                "type": "number",
                "description": "The value to find the percentile rank for"
            },
            "data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "The reference dataset to compare against"
            },
            "include_equal": {
                "type": "boolean",
                "description": "If true, count values equal to target. Default: true"
            }
        },
        "required": ["value", "data"]
    }
)
def compute_percentile_rank(
    value: float,
    data: List[Any],
    include_equal: bool = True
) -> Dict[str, Any]:
    """
    Compute the percentile rank of a value within a dataset.
    
    This answers: "What percentage of values in the dataset are below this value?"
    
    Args:
        value: The value to rank
        data: Reference dataset
        include_equal: Whether to count equal values
        
    Returns:
        Dictionary with percentile rank and context
    """
    logger.info(f"Computing percentile rank for value={value}")
    
    # Validate data
    is_valid, error, numeric_data = validate_numeric_data(data)
    if not is_valid:
        return {
            "success": False,
            "error": error,
            "percentile_rank": None
        }
    
    try:
        n = len(numeric_data)
        
        if include_equal:
            count_below = sum(1 for x in numeric_data if x <= value)
        else:
            count_below = sum(1 for x in numeric_data if x < value)
        
        percentile_rank = (count_below / n) * 100
        
        # Calculate z-score for context
        mean = _mean(numeric_data)
        std = _std_dev(numeric_data)
        z = _z_score(value, mean, std)
        
        # Interpretation
        if percentile_rank >= 90:
            interpretation = "Very high (top 10%)"
        elif percentile_rank >= 75:
            interpretation = "High (top 25%)"
        elif percentile_rank >= 50:
            interpretation = "Above median"
        elif percentile_rank >= 25:
            interpretation = "Below median"
        elif percentile_rank >= 10:
            interpretation = "Low (bottom 25%)"
        else:
            interpretation = "Very low (bottom 10%)"
        
        return {
            "success": True,
            "value": value,
            "percentile_rank": round(percentile_rank, 2),
            "z_score": round(z, 4),
            "interpretation": interpretation,
            "context": {
                "dataset_size": n,
                "dataset_mean": round(mean, 6),
                "dataset_std": round(std, 6),
                "dataset_min": round(min(numeric_data), 6),
                "dataset_max": round(max(numeric_data), 6),
                "values_below": count_below,
                "values_above": n - count_below
            }
        }
        
    except Exception as e:
        logger.error(f"Error computing percentile rank: {e}")
        return {
            "success": False,
            "error": str(e),
            "percentile_rank": None
        }


@register_tool(
    name="compare_distributions",
    description="Compare two datasets statistically. Computes difference in means, "
                "effect size (Cohen's d), and distribution overlap.",
    input_schema={
        "type": "object",
        "properties": {
            "data_a": {
                "type": "array",
                "items": {"type": "number"},
                "description": "First dataset"
            },
            "data_b": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Second dataset"
            },
            "label_a": {
                "type": "string",
                "description": "Label for first dataset. Default: 'Dataset A'"
            },
            "label_b": {
                "type": "string",
                "description": "Label for second dataset. Default: 'Dataset B'"
            }
        },
        "required": ["data_a", "data_b"]
    }
)
def compare_distributions(
    data_a: List[Any],
    data_b: List[Any],
    label_a: str = "Dataset A",
    label_b: str = "Dataset B"
) -> Dict[str, Any]:
    """
    Compare two distributions statistically.
    
    Args:
        data_a: First dataset
        data_b: Second dataset
        label_a: Label for first dataset
        label_b: Label for second dataset
        
    Returns:
        Dictionary with comparison statistics
    """
    logger.info(f"Comparing distributions: {label_a} ({len(data_a)} values) vs {label_b} ({len(data_b)} values)")
    
    # Validate both datasets
    is_valid_a, error_a, numeric_a = validate_numeric_data(data_a)
    is_valid_b, error_b, numeric_b = validate_numeric_data(data_b)
    
    if not is_valid_a:
        return {"success": False, "error": f"{label_a}: {error_a}"}
    if not is_valid_b:
        return {"success": False, "error": f"{label_b}: {error_b}"}
    
    try:
        # Statistics for each
        mean_a, mean_b = _mean(numeric_a), _mean(numeric_b)
        std_a, std_b = _std_dev(numeric_a), _std_dev(numeric_b)
        median_a, median_b = _median(numeric_a), _median(numeric_b)
        
        # Difference in means
        mean_diff = mean_a - mean_b
        
        # Cohen's d (effect size)
        pooled_std = math.sqrt((std_a**2 + std_b**2) / 2)
        cohens_d = mean_diff / pooled_std if pooled_std > 0 else 0
        
        # Effect size interpretation
        abs_d = abs(cohens_d)
        if abs_d < 0.2:
            effect_interpretation = "Negligible"
        elif abs_d < 0.5:
            effect_interpretation = "Small"
        elif abs_d < 0.8:
            effect_interpretation = "Medium"
        else:
            effect_interpretation = "Large"
        
        # Overlap coefficient (simplified)
        # Using overlap of ranges
        min_a, max_a = min(numeric_a), max(numeric_a)
        min_b, max_b = min(numeric_b), max(numeric_b)
        
        overlap_start = max(min_a, min_b)
        overlap_end = min(max_a, max_b)
        
        if overlap_start < overlap_end:
            total_range = max(max_a, max_b) - min(min_a, min_b)
            overlap_range = overlap_end - overlap_start
            overlap_pct = (overlap_range / total_range) * 100 if total_range > 0 else 0
        else:
            overlap_pct = 0
        
        return {
            "success": True,
            "comparison": {
                label_a: {
                    "count": len(numeric_a),
                    "mean": round(mean_a, 6),
                    "std_dev": round(std_a, 6),
                    "median": round(median_a, 6),
                    "min": round(min_a, 6),
                    "max": round(max_a, 6),
                },
                label_b: {
                    "count": len(numeric_b),
                    "mean": round(mean_b, 6),
                    "std_dev": round(std_b, 6),
                    "median": round(median_b, 6),
                    "min": round(min_b, 6),
                    "max": round(max_b, 6),
                },
                "difference": {
                    "mean_difference": round(mean_diff, 6),
                    "cohens_d": round(cohens_d, 4),
                    "effect_size": effect_interpretation,
                    "range_overlap_pct": round(overlap_pct, 2),
                }
            },
            "summary": f"{label_a} mean={format_number(mean_a)} vs {label_b} mean={format_number(mean_b)}, "
                      f"diff={format_number(mean_diff)}, effect={effect_interpretation}"
        }
        
    except Exception as e:
        logger.error(f"Error comparing distributions: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@register_tool(
    name="compute_correlation",
    description="Compute Pearson correlation coefficient between two datasets. "
                "Values range from -1 (perfect negative) to +1 (perfect positive).",
    input_schema={
        "type": "object",
        "properties": {
            "data_x": {
                "type": "array",
                "items": {"type": "number"},
                "description": "First variable (X)"
            },
            "data_y": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Second variable (Y)"
            }
        },
        "required": ["data_x", "data_y"]
    }
)
def compute_correlation(
    data_x: List[Any],
    data_y: List[Any]
) -> Dict[str, Any]:
    """
    Compute Pearson correlation coefficient.
    
    Args:
        data_x: First variable
        data_y: Second variable (must be same length)
        
    Returns:
        Dictionary with correlation and interpretation
    """
    logger.info(f"Computing correlation between {len(data_x)} and {len(data_y)} values")
    
    # Validate
    is_valid_x, error_x, numeric_x = validate_numeric_data(data_x)
    is_valid_y, error_y, numeric_y = validate_numeric_data(data_y)
    
    if not is_valid_x:
        return {"success": False, "error": f"X: {error_x}"}
    if not is_valid_y:
        return {"success": False, "error": f"Y: {error_y}"}
    
    if len(numeric_x) != len(numeric_y):
        return {
            "success": False,
            "error": f"Datasets must have same length: X={len(numeric_x)}, Y={len(numeric_y)}"
        }
    
    try:
        n = len(numeric_x)
        if n < 3:
            return {
                "success": False,
                "error": "Need at least 3 data points for correlation"
            }
        
        mean_x = _mean(numeric_x)
        mean_y = _mean(numeric_y)
        std_x = _std_dev(numeric_x)
        std_y = _std_dev(numeric_y)
        
        if std_x == 0 or std_y == 0:
            return {
                "success": True,
                "correlation": 0.0,
                "interpretation": "Undefined (constant variable)",
                "r_squared": 0.0
            }
        
        # Pearson correlation
        covariance = sum((numeric_x[i] - mean_x) * (numeric_y[i] - mean_y) for i in range(n)) / (n - 1)
        r = covariance / (std_x * std_y)
        r_squared = r ** 2
        
        # Interpretation
        abs_r = abs(r)
        if abs_r < 0.1:
            strength = "Negligible"
        elif abs_r < 0.3:
            strength = "Weak"
        elif abs_r < 0.5:
            strength = "Moderate"
        elif abs_r < 0.7:
            strength = "Strong"
        else:
            strength = "Very strong"
        
        direction = "positive" if r > 0 else "negative" if r < 0 else "no"
        interpretation = f"{strength} {direction} correlation"
        
        return {
            "success": True,
            "correlation": round(r, 6),
            "r_squared": round(r_squared, 6),
            "interpretation": interpretation,
            "sample_size": n,
            "summary": f"r={r:.4f}, R²={r_squared:.4f} ({interpretation})"
        }
        
    except Exception as e:
        logger.error(f"Error computing correlation: {e}")
        return {
            "success": False,
            "error": str(e)
        }

