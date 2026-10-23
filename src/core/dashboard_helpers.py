"""
Shared helper functions for the unified dashboard.

These helpers centralize ordering checks, normalization, bucketization,
outlier trimming, and histogram bin edge construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from rules.treasury_price_conversion import points_to_bps


def is_strictly_increasing(values: Sequence[int]) -> bool:
    """Return True if values are strictly increasing."""
    if not values:
        return True
    prev = values[0]
    for v in values[1:]:
        if v <= prev:
            return False
        prev = v
    return True


def sort_by_ts(ts: Sequence[int], *arrays: Sequence) -> Tuple[List[int], ...]:
    """
    Sort timestamp vector and any aligned arrays by timestamp ascending.

    Returns a tuple of lists: (sorted_ts, sorted_arr1, sorted_arr2, ...)
    """
    if not arrays:
        return (sorted(ts),)
    zipped = list(zip(ts, *arrays))
    zipped.sort(key=lambda x: x[0])
    sorted_cols = list(zip(*zipped))
    return tuple([list(col) for col in sorted_cols])  # type: ignore[return-value]


def normalize_price_to_bps(prices_dec: Sequence[float], ref_price_dec: float) -> List[float]:
    """Normalize decimal prices to bps from a reference price."""
    if ref_price_dec == 0:
        return [0.0 for _ in prices_dec]
    return [float(points_to_bps(px - ref_price_dec)) for px in prices_dec]


def window_move_bps(price_start_dec: float, price_end_dec: float) -> float:
    """Compute window move in bps using points_to_bps."""
    return float(points_to_bps(price_end_dec - price_start_dec))


def be_multiplier(window_move_bps_value: float, fwd_bps: float) -> Optional[float]:
    """Compute B/E multiplier (window move / fwd), returning None if invalid."""
    if fwd_bps <= 0:
        return None
    return float(window_move_bps_value / fwd_bps)


def surprise_z(actual: Optional[float], consensus: Optional[float], stddev: Optional[float]) -> Optional[float]:
    """Compute surprise z-score given actual, consensus, and stddev."""
    if actual is None or consensus is None or stddev is None or stddev == 0:
        return None
    return float((actual - consensus) / stddev)


def headline_bucket_index(z: float, width_sigma: float) -> int:
    """Bucket index for headline z-score with fixed width."""
    if width_sigma <= 0:
        raise ValueError("width_sigma must be > 0")
    return int(z // width_sigma)


def lmh_bucket(z: float, threshold_sigma: float) -> str:
    """Map z-score to Low/Medium/High using a symmetric threshold."""
    if z <= -threshold_sigma:
        return "Low"
    if z >= threshold_sigma:
        return "High"
    return "Medium"


def percentile(values: Sequence[float], p: float) -> float:
    """
    Compute percentile with linear interpolation (p in [0,100]).
    Returns NaN for empty input.
    """
    if not values:
        return float("nan")
    if p <= 0:
        return float(min(values))
    if p >= 100:
        return float(max(values))
    vals = sorted(values)
    k = (len(vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return float(vals[f])
    d0 = vals[f] * (c - k)
    d1 = vals[c] * (k - f)
    return float(d0 + d1)


def trim_outliers_p5_p95(values: Sequence[float]) -> List[float]:
    """Trim values outside [P5, P95]."""
    if not values:
        return []
    p5 = percentile(values, 5)
    p95 = percentile(values, 95)
    return [v for v in values if p5 <= v <= p95]


def histogram_edges_zero_aligned(min_bps: float, max_bps: float, bin_width: float) -> List[float]:
    """Compute histogram bin edges so that 0 is always a boundary."""
    if bin_width <= 0:
        raise ValueError("bin_width must be > 0")
    if min_bps == max_bps:
        return [min_bps, min_bps + bin_width]
    import math

    k_min = math.floor(min_bps / bin_width)
    k_max = math.ceil(max_bps / bin_width)
    return [i * bin_width for i in range(k_min, k_max + 1)]


@dataclass(frozen=True)
class BucketScheme:
    headline_width_sigma: float = 0.5
    lmh_threshold_sigma: float = 0.5
    max_components_in_combo: int = 2




