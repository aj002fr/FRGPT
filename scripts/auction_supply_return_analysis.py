#!/usr/bin/env python
"""
20Y Treasury Supply Return Analysis

Analyzes the relationship between 20Y auction fields and subsequent TY price returns
at 2h, 4h, 6h, 8h, 12h, and 24h horizons after the auction.

Features:
- Join auction data with TY market returns
- OLS regression of auction fields vs returns
- Bucketed rally/selloff analysis
- Distribution histograms

Usage:
    python scripts/auction_supply_return_analysis.py
    python scripts/auction_supply_return_analysis.py --buckets 4
    python scripts/auction_supply_return_analysis.py --no-plots
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from itertools import combinations

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.servers.marketdata.run_query import run_multi_date_query
from src.servers.analytics.schema import decimal_to_bps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

HORIZONS_MINUTES = [120, 240, 360, 480, 720, 1440]  # 2h, 4h, 6h, 8h, 12h, 24h
HORIZON_LABELS = ["ret_2h", "ret_4h", "ret_6h", "ret_8h", "ret_12h", "ret_24h"]

DEFAULT_INPUT_CSV = PROJECT_ROOT / "workspace" / "results" / "Securities_2_nomura_only.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "workspace" / "results" / "auction_supply_return_analysis"

# Numeric columns for regression (after parsing)
NUMERIC_COLUMNS = [
    "Size",
    "Coupon",
    "Stop",
    "Bid/Cover",
    "Yld Gap",
    "Dealer%",
    "Direct%",
    "Indirect%",
    "Dlr Hit",
    "Dir Hit",
    "Indir Hit",
]

# ============================================================================
# Parsing helpers
# ============================================================================


def parse_percent(val: str) -> Optional[float]:
    """Parse '4.625%' -> 4.625 or None."""
    if not val or val.strip() == "":
        return None
    val = val.strip().replace("%", "")
    try:
        return float(val)
    except ValueError:
        return None


def parse_money(val: str) -> Optional[float]:
    """Parse '$16,000,000,000' -> 16000000000.0 or None."""
    if not val or val.strip() == "":
        return None
    val = val.strip().replace("$", "").replace(",", "")
    try:
        return float(val)
    except ValueError:
        return None


def parse_float(val: str) -> Optional[float]:
    """Parse generic float or None."""
    if not val or val.strip() == "":
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def parse_auction_date(val: str) -> Optional[datetime]:
    """Parse MM/DD/YYYY date."""
    if not val or val.strip() == "":
        return None
    try:
        return datetime.strptime(val.strip(), "%m/%d/%Y")
    except ValueError:
        return None


def parse_row(row: Dict[str, str]) -> Dict[str, Any]:
    """Parse a CSV row into typed values."""
    parsed = {}
    
    # Auction date
    parsed["Auction"] = parse_auction_date(row.get("Auction", ""))
    
    # Size (money)
    parsed["Size"] = parse_money(row.get("Size", ""))
    
    # Percentages
    parsed["Coupon"] = parse_percent(row.get("Coupon", ""))
    parsed["Stop"] = parse_percent(row.get("Stop", ""))
    parsed["Dealer%"] = parse_percent(row.get("Dealer%", ""))
    parsed["Direct%"] = parse_percent(row.get("Direct%", ""))
    parsed["Indirect%"] = parse_percent(row.get("Indirect%", ""))
    parsed["Dlr Hit"] = parse_percent(row.get("Dlr Hit", ""))
    parsed["Dir Hit"] = parse_percent(row.get("Dir Hit", ""))
    parsed["Indir Hit"] = parse_percent(row.get("Indir Hit", ""))
    
    # Plain floats
    parsed["Bid/Cover"] = parse_float(row.get("Bid/Cover", ""))
    parsed["Tail"] = parse_float(row.get("Tail", ""))
    parsed["Yld Gap"] = parse_float(row.get("Yld Gap", ""))
    
    return parsed


def load_auction_data(csv_path: Path) -> List[Dict[str, Any]]:
    """Load and parse auction CSV file."""
    auctions = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = parse_row(row)
            if parsed["Auction"] is not None:
                auctions.append(parsed)
    logger.info(f"Loaded {len(auctions)} auctions from {csv_path}")
    return auctions


# ============================================================================
# Market data fetching
# ============================================================================


def fetch_returns_for_auction(
    auction_dt: datetime,
    horizons_minutes: List[int] = HORIZONS_MINUTES,
) -> Dict[str, Optional[float]]:
    """
    Fetch TY returns at various horizons after an auction.
    
    Args:
        auction_dt: Auction timestamp (1pm ET on auction day)
        horizons_minutes: List of minute offsets to compute returns
        
    Returns:
        Dict with keys like 'ret_2h' -> return in bps, or None if data missing
    """
    # Build timestamp string
    auction_ts = auction_dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    # Fetch 24 hours of data starting from auction time
    max_horizon = max(horizons_minutes)
    result = run_multi_date_query(
        symbol="TY",
        dates=[auction_ts],
        columns=["timestamp", "open", "high", "low", "close"],
        limit_per_date=2000,
        offset_minutes=0,
        window_minutes=max_horizon + 5,  # Add buffer
    )
    
    returns = {}
    
    if not result.get("success") or not result.get("results"):
        for label in HORIZON_LABELS:
            returns[label] = None
        return returns
    
    data = result["results"][0].get("data", [])
    if not data:
        for label in HORIZON_LABELS:
            returns[label] = None
        return returns
    
    # Reference price (first bar = auction time)
    ref_price = data[0].get("close")
    if ref_price is None:
        for label in HORIZON_LABELS:
            returns[label] = None
        return returns
    
    # Build timestamp -> close mapping
    # Filter out invalid prices (treasury futures should be between 80 and 150)
    MIN_VALID_PRICE = 80.0
    MAX_VALID_PRICE = 150.0
    
    price_by_offset = {}
    for bar in data:
        bar_ts = bar.get("timestamp", "")
        bar_close = bar.get("close")
        if bar_ts and bar_close is not None:
            # Skip invalid prices (negative, too low, or too high)
            if bar_close < MIN_VALID_PRICE or bar_close > MAX_VALID_PRICE:
                continue
            try:
                # Parse bar timestamp (strip timezone info for naive comparison)
                bar_ts_clean = bar_ts.replace("Z", "").replace("+00:00", "")
                if "T" in bar_ts_clean:
                    bar_dt = datetime.strptime(bar_ts_clean[:19], "%Y-%m-%dT%H:%M:%S")
                else:
                    bar_dt = datetime.strptime(bar_ts_clean[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            offset = int((bar_dt - auction_dt).total_seconds() / 60)
            price_by_offset[offset] = bar_close
    
    # Compute returns at each horizon
    for horizon, label in zip(horizons_minutes, HORIZON_LABELS):
        # Find closest bar to target offset
        target_offset = horizon
        closest_offset = None
        min_diff = float("inf")
        
        for offset in price_by_offset.keys():
            diff = abs(offset - target_offset)
            if diff < min_diff:
                min_diff = diff
                closest_offset = offset
        
        if closest_offset is not None and min_diff <= 5:  # Within 5 minutes
            end_price = price_by_offset[closest_offset]
            price_move = end_price - ref_price
            ret_bps = decimal_to_bps(price_move)
            returns[label] = ret_bps
        else:
            returns[label] = None
    
    return returns


def build_joined_dataset(
    auctions: List[Dict[str, Any]],
    start_year: int = 2020,
) -> List[Dict[str, Any]]:
    """
    Build joined dataset of auction fields + returns.
    
    Args:
        auctions: Parsed auction data
        start_year: Only include auctions from this year onwards (for data availability)
        
    Returns:
        List of dicts with auction fields and return columns
    """
    joined = []
    
    for auction in auctions:
        auction_dt = auction["Auction"]
        if auction_dt is None:
            continue
        
        # Skip auctions before start_year
        if auction_dt.year < start_year:
            logger.debug(f"Skipping auction {auction_dt.date()} (before {start_year})")
            continue
        
        # Auction time is 1pm ET
        auction_1pm = auction_dt.replace(hour=13, minute=0, second=0, microsecond=0)
        
        logger.info(f"Fetching returns for auction {auction_dt.date()}")
        returns = fetch_returns_for_auction(auction_1pm)
        
        # Combine
        record = {**auction, **returns}
        record["Auction_str"] = auction_dt.strftime("%Y-%m-%d")
        joined.append(record)
    
    logger.info(f"Built joined dataset with {len(joined)} records")
    return joined


# ============================================================================
# Regression analysis
# ============================================================================


def run_ols_regression(
    data: List[Dict[str, Any]],
    y_col: str,
    x_cols: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Run simple OLS regression (uses numpy/scipy if available, else skip).
    
    Returns dict with coefficients, t-stats, R², or None if not possible.
    """
    try:
        import numpy as np
        from scipy import stats
    except ImportError:
        logger.warning("numpy/scipy not available, skipping regression")
        return None
    
    # Filter rows with complete data
    valid_rows = []
    for row in data:
        y_val = row.get(y_col)
        if y_val is None:
            continue
        x_vals = [row.get(c) for c in x_cols]
        if None in x_vals:
            continue
        valid_rows.append((y_val, x_vals))
    
    if len(valid_rows) < 5:
        return None
    
    y = np.array([r[0] for r in valid_rows])
    X = np.array([r[1] for r in valid_rows])
    
    # Add constant term
    X = np.column_stack([np.ones(len(X)), X])
    
    try:
        # OLS: beta = (X'X)^-1 X'y
        XtX_inv = np.linalg.inv(X.T @ X)
        beta = XtX_inv @ X.T @ y
        
        # Residuals and R²
        y_pred = X @ beta
        residuals = y - y_pred
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        
        # Standard errors and t-stats
        n, k = X.shape
        mse = ss_res / (n - k) if n > k else 1
        var_beta = mse * np.diag(XtX_inv)
        se_beta = np.sqrt(np.maximum(var_beta, 0))
        t_stats = beta / np.where(se_beta > 0, se_beta, 1)
        
        return {
            "y_col": y_col,
            "x_cols": ["const"] + x_cols,
            "coefficients": beta.tolist(),
            "std_errors": se_beta.tolist(),
            "t_stats": t_stats.tolist(),
            "r_squared": r_squared,
            "n_obs": len(valid_rows),
        }
    except Exception as e:
        logger.warning(f"Regression failed for {y_col}: {e}")
        return None


def run_all_regressions(
    data: List[Dict[str, Any]],
    x_cols: List[str],
) -> List[Dict[str, Any]]:
    """Run regressions for all horizon returns."""
    results = []
    for y_col in HORIZON_LABELS:
        reg = run_ols_regression(data, y_col, x_cols)
        if reg:
            results.append(reg)
    return results


# ============================================================================
# Bucketing analysis
# ============================================================================


def compute_bucket_stats(
    data: List[Dict[str, Any]],
    x_col: str,
    y_col: str,
    n_buckets: int = 4,
) -> Optional[Dict[str, Any]]:
    """
    Compute bucketed statistics for an X column vs Y return.
    
    Returns:
        Dict with bucket boundaries, mean returns, rally rates, etc.
    """
    # Filter valid rows
    valid = [(row.get(x_col), row.get(y_col)) for row in data]
    valid = [(x, y) for x, y in valid if x is not None and y is not None]
    
    if len(valid) < n_buckets * 2:
        return None
    
    # Sort by x value
    valid.sort(key=lambda t: t[0])
    
    # Create buckets
    bucket_size = len(valid) // n_buckets
    buckets = []
    
    for i in range(n_buckets):
        start_idx = i * bucket_size
        end_idx = len(valid) if i == n_buckets - 1 else (i + 1) * bucket_size
        bucket_data = valid[start_idx:end_idx]
        
        x_vals = [t[0] for t in bucket_data]
        y_vals = [t[1] for t in bucket_data]
        
        bucket = {
            "bucket": i + 1,
            "x_min": min(x_vals),
            "x_max": max(x_vals),
            "x_mean": sum(x_vals) / len(x_vals),
            "y_mean": sum(y_vals) / len(y_vals),
            "y_std": (sum((y - sum(y_vals)/len(y_vals))**2 for y in y_vals) / len(y_vals)) ** 0.5 if len(y_vals) > 1 else 0,
            "rally_rate": sum(1 for y in y_vals if y > 0) / len(y_vals),
            "selloff_rate": sum(1 for y in y_vals if y < 0) / len(y_vals),
            "count": len(y_vals),
        }
        buckets.append(bucket)
    
    # Check monotonicity
    means = [b["y_mean"] for b in buckets]
    is_increasing = all(means[i] <= means[i+1] for i in range(len(means)-1))
    is_decreasing = all(means[i] >= means[i+1] for i in range(len(means)-1))
    
    return {
        "x_col": x_col,
        "y_col": y_col,
        "n_buckets": n_buckets,
        "buckets": buckets,
        "is_monotonic": is_increasing or is_decreasing,
        "monotonic_direction": "increasing" if is_increasing else ("decreasing" if is_decreasing else "none"),
        "range_mean_diff": means[-1] - means[0],
        "range_rally_diff": buckets[-1]["rally_rate"] - buckets[0]["rally_rate"],
    }


def run_all_bucket_analysis(
    data: List[Dict[str, Any]],
    x_cols: List[str],
    n_buckets: int = 4,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run bucket analysis for all X columns and all horizons."""
    results = {}
    
    for y_col in HORIZON_LABELS:
        horizon_results = []
        for x_col in x_cols:
            stats = compute_bucket_stats(data, x_col, y_col, n_buckets)
            if stats:
                horizon_results.append(stats)
        results[y_col] = horizon_results
    
    return results


# ============================================================================
# Plotting
# ============================================================================


def plot_return_distributions_by_bucket(
    data: List[Dict[str, Any]],
    output_dir: Path,
    predictor_cols: List[str],
) -> List[Path]:
    """
    Plot return histograms with bars colored by predictor bucket.
    
    For each horizon and each significant predictor, creates a histogram
    where each bar is colored based on whether the auction had a high or low
    value for that predictor.
    """
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available, skipping plots")
        return []
    
    plots = []
    
    # For each predictor, determine median split
    predictor_medians = {}
    for col in predictor_cols:
        values = [row.get(col) for row in data if row.get(col) is not None]
        if len(values) >= 4:
            predictor_medians[col] = sorted(values)[len(values) // 2]
    
    for y_col in HORIZON_LABELS:
        horizon_label = y_col.replace("ret_", "").upper()
        
        # Get valid data for this horizon
        valid_data = [row for row in data if row.get(y_col) is not None]
        if len(valid_data) < 5:
            continue
        
        returns = [row[y_col] for row in valid_data]
        
        # Create plot for each significant predictor
        for x_col in predictor_cols:
            if x_col not in predictor_medians:
                continue
            
            median_val = predictor_medians[x_col]
            
            # Split returns by high/low predictor value
            high_returns = [row[y_col] for row in valid_data 
                           if row.get(x_col) is not None and row[x_col] >= median_val]
            low_returns = [row[y_col] for row in valid_data 
                          if row.get(x_col) is not None and row[x_col] < median_val]
            
            if len(high_returns) < 2 or len(low_returns) < 2:
                continue
            
            # Create figure
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Determine bin edges for both groups
            all_rets = high_returns + low_returns
            bin_min = min(all_rets) - 1
            bin_max = max(all_rets) + 1
            bins = np.linspace(bin_min, bin_max, 12)
            
            # Plot histograms with transparency
            x_label_clean = x_col.replace("%", "")
            ax.hist(low_returns, bins=bins, alpha=0.6, label=f'Low {x_label_clean} (n={len(low_returns)})', 
                   color='#E94F37', edgecolor='white')
            ax.hist(high_returns, bins=bins, alpha=0.6, label=f'High {x_label_clean} (n={len(high_returns)})', 
                   color='#2E86AB', edgecolor='white')
            
            # Add vertical line at zero
            ax.axvline(x=0, color='gray', linestyle='--', linewidth=1, alpha=0.7)
            
            # Calculate stats for annotation
            high_mean = sum(high_returns) / len(high_returns)
            low_mean = sum(low_returns) / len(low_returns)
            high_rally_rate = sum(1 for r in high_returns if r > 0) / len(high_returns)
            low_rally_rate = sum(1 for r in low_returns if r > 0) / len(low_returns)
            
            # Add stats box
            stats_text = (
                f"High {x_label_clean}: mean={high_mean:.1f}bps, rally={high_rally_rate:.0%}\n"
                f"Low {x_label_clean}: mean={low_mean:.1f}bps, rally={low_rally_rate:.0%}\n"
                f"Median split: {median_val:.1f}"
            )
            ax.text(0.98, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
                   verticalalignment='top', horizontalalignment='right',
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.9))
            
            ax.set_xlabel(f'Return (bps) at {horizon_label}', fontsize=11)
            ax.set_ylabel('Count', fontsize=11)
            ax.set_title(f'TY Returns {horizon_label} After 20Y Supply\nColored by {x_col}', fontsize=12)
            ax.legend(loc='upper left')
            ax.grid(axis='y', alpha=0.3)
            
            # Save
            filename = f"ret_{y_col}_{x_col.replace('/', '_').replace('%', 'pct')}.png"
            filepath = output_dir / filename
            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close(fig)
            
            plots.append(filepath)
            logger.info(f"Created plot: {filepath}")
    
    return plots


def compute_interaction_stats(
    data: List[Dict[str, Any]],
    x1_col: str,
    x2_col: str,
    y_col: str,
) -> Optional[Dict[str, Any]]:
    """
    Compute interaction statistics for two predictors.
    
    Creates a 2x2 grid: (High/Low x1) × (High/Low x2) and computes
    mean return and rally rate for each quadrant.
    """
    # Get valid data
    valid = [row for row in data 
             if row.get(x1_col) is not None 
             and row.get(x2_col) is not None 
             and row.get(y_col) is not None]
    
    if len(valid) < 8:
        return None
    
    # Compute medians
    x1_vals = sorted([row[x1_col] for row in valid])
    x2_vals = sorted([row[x2_col] for row in valid])
    x1_median = x1_vals[len(x1_vals) // 2]
    x2_median = x2_vals[len(x2_vals) // 2]
    
    # Create quadrants
    quadrants = {
        "High-High": [],  # High x1, High x2
        "High-Low": [],   # High x1, Low x2
        "Low-High": [],   # Low x1, High x2
        "Low-Low": [],    # Low x1, Low x2
    }
    
    for row in valid:
        x1_high = row[x1_col] >= x1_median
        x2_high = row[x2_col] >= x2_median
        y_val = row[y_col]
        
        if x1_high and x2_high:
            quadrants["High-High"].append(y_val)
        elif x1_high and not x2_high:
            quadrants["High-Low"].append(y_val)
        elif not x1_high and x2_high:
            quadrants["Low-High"].append(y_val)
        else:
            quadrants["Low-Low"].append(y_val)
    
    # Compute stats for each quadrant
    stats = {}
    for quad_name, values in quadrants.items():
        if len(values) >= 1:
            stats[quad_name] = {
                "count": len(values),
                "mean": sum(values) / len(values),
                "rally_rate": sum(1 for v in values if v > 0) / len(values) if values else 0,
            }
        else:
            stats[quad_name] = {"count": 0, "mean": None, "rally_rate": None}
    
    return {
        "x1_col": x1_col,
        "x2_col": x2_col,
        "y_col": y_col,
        "x1_median": x1_median,
        "x2_median": x2_median,
        "quadrants": stats,
        "n_valid": len(valid),
    }


def run_interaction_analysis(
    data: List[Dict[str, Any]],
    predictor_cols: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Run interaction analysis for all pairs of predictors."""
    from itertools import combinations
    
    results = {}
    
    for y_col in HORIZON_LABELS:
        horizon_results = []
        
        # Test all pairs of predictors
        for x1_col, x2_col in combinations(predictor_cols, 2):
            stats = compute_interaction_stats(data, x1_col, x2_col, y_col)
            if stats:
                # Check if interaction is meaningful
                quads = stats["quadrants"]
                if all(q["count"] >= 1 for q in quads.values()):
                    # Compute interaction strength
                    hh = quads["High-High"]["mean"] or 0
                    hl = quads["High-Low"]["mean"] or 0
                    lh = quads["Low-High"]["mean"] or 0
                    ll = quads["Low-Low"]["mean"] or 0
                    
                    # Interaction = deviation from additive model
                    # If factors are additive: HH - HL ≈ LH - LL
                    # Interaction = (HH - HL) - (LH - LL)
                    interaction = (hh - hl) - (lh - ll)
                    stats["interaction_strength"] = interaction
                    
                    # Range of means across quadrants
                    means = [hh, hl, lh, ll]
                    stats["mean_range"] = max(means) - min(means)
                    
                    horizon_results.append(stats)
        
        # Sort by interaction strength
        horizon_results.sort(key=lambda x: abs(x.get("interaction_strength", 0)), reverse=True)
        results[y_col] = horizon_results
    
    return results


def plot_interaction_heatmaps(
    data: List[Dict[str, Any]],
    output_dir: Path,
    predictor_pairs: List[Tuple[str, str]],
) -> List[Path]:
    """
    Plot interaction heatmaps for predictor pairs.
    
    Creates a 2x2 heatmap for each pair showing mean return in each quadrant.
    """
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available, skipping interaction plots")
        return []
    
    plots = []
    
    for y_col in HORIZON_LABELS:
        horizon_label = y_col.replace("ret_", "").upper()
        
        for x1_col, x2_col in predictor_pairs:
            stats = compute_interaction_stats(data, x1_col, x2_col, y_col)
            if not stats:
                continue
            
            quads = stats["quadrants"]
            
            # Skip if any quadrant is empty
            if any(q["count"] == 0 for q in quads.values()):
                continue
            
            # Create 2x2 matrix
            matrix = np.array([
                [quads["Low-Low"]["mean"], quads["Low-High"]["mean"]],
                [quads["High-Low"]["mean"], quads["High-High"]["mean"]],
            ])
            
            counts = np.array([
                [quads["Low-Low"]["count"], quads["Low-High"]["count"]],
                [quads["High-Low"]["count"], quads["High-High"]["count"]],
            ])
            
            rally_rates = np.array([
                [quads["Low-Low"]["rally_rate"], quads["Low-High"]["rally_rate"]],
                [quads["High-Low"]["rally_rate"], quads["High-High"]["rally_rate"]],
            ])
            
            # Create figure
            fig, ax = plt.subplots(figsize=(8, 6))
            
            # Heatmap
            vmax = max(abs(matrix.min()), abs(matrix.max()), 3)
            im = ax.imshow(matrix, cmap='RdYlGn', vmin=-vmax, vmax=vmax, aspect='auto')
            
            # Add colorbar
            cbar = plt.colorbar(im, ax=ax)
            cbar.set_label('Mean Return (bps)', fontsize=10)
            
            # Labels
            x1_label = x1_col.replace("%", "")
            x2_label = x2_col.replace("%", "")
            
            ax.set_xticks([0, 1])
            ax.set_xticklabels([f'Low {x2_label}', f'High {x2_label}'], fontsize=10)
            ax.set_yticks([0, 1])
            ax.set_yticklabels([f'Low {x1_label}', f'High {x1_label}'], fontsize=10)
            
            # Add text annotations
            for i in range(2):
                for j in range(2):
                    mean_val = matrix[i, j]
                    count = int(counts[i, j])
                    rally = rally_rates[i, j]
                    
                    text_color = 'white' if abs(mean_val) > vmax * 0.5 else 'black'
                    ax.text(j, i, f'{mean_val:.1f} bps\nn={count}\n{rally:.0%} rally',
                           ha='center', va='center', fontsize=10, color=text_color,
                           fontweight='bold')
            
            ax.set_title(f'TY Returns {horizon_label}: {x1_col} × {x2_col} Interaction', fontsize=12)
            ax.set_xlabel(x2_col, fontsize=11)
            ax.set_ylabel(x1_col, fontsize=11)
            
            # Save
            x1_clean = x1_col.replace("/", "_").replace("%", "pct")
            x2_clean = x2_col.replace("/", "_").replace("%", "pct")
            filename = f"interaction_{y_col}_{x1_clean}_x_{x2_clean}.png"
            filepath = output_dir / filename
            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close(fig)
            
            plots.append(filepath)
            logger.info(f"Created interaction plot: {filepath}")
    
    return plots


def save_interaction_results(
    results: Dict[str, List[Dict[str, Any]]],
    output_path: Path,
) -> None:
    """Save interaction analysis results to CSV."""
    rows = []
    
    for y_col, horizon_results in results.items():
        for stats in horizon_results[:10]:  # Top 10 interactions per horizon
            quads = stats["quadrants"]
            rows.append({
                "horizon": y_col,
                "x1_col": stats["x1_col"],
                "x2_col": stats["x2_col"],
                "x1_median": stats["x1_median"],
                "x2_median": stats["x2_median"],
                "HH_mean": quads["High-High"]["mean"],
                "HH_rally": quads["High-High"]["rally_rate"],
                "HH_n": quads["High-High"]["count"],
                "HL_mean": quads["High-Low"]["mean"],
                "HL_rally": quads["High-Low"]["rally_rate"],
                "HL_n": quads["High-Low"]["count"],
                "LH_mean": quads["Low-High"]["mean"],
                "LH_rally": quads["Low-High"]["rally_rate"],
                "LH_n": quads["Low-High"]["count"],
                "LL_mean": quads["Low-Low"]["mean"],
                "LL_rally": quads["Low-Low"]["rally_rate"],
                "LL_n": quads["Low-Low"]["count"],
                "interaction_strength": stats.get("interaction_strength", 0),
                "mean_range": stats.get("mean_range", 0),
            })
    
    if rows:
        cols = ["horizon", "x1_col", "x2_col", "x1_median", "x2_median",
                "HH_mean", "HH_rally", "HH_n", "HL_mean", "HL_rally", "HL_n",
                "LH_mean", "LH_rally", "LH_n", "LL_mean", "LL_rally", "LL_n",
                "interaction_strength", "mean_range"]
        
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)
        
        logger.info(f"Saved interaction results to {output_path}")


def plot_overall_return_distributions(
    data: List[Dict[str, Any]],
    output_dir: Path,
) -> List[Path]:
    """Plot simple return distributions for each horizon."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available, skipping plots")
        return []
    
    plots = []
    
    for y_col in HORIZON_LABELS:
        values = [row.get(y_col) for row in data if row.get(y_col) is not None]
        if len(values) < 5:
            continue
        
        horizon_label = y_col.replace("ret_", "").upper()
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # Color bars by rally/selloff
        rally_vals = [v for v in values if v > 0]
        selloff_vals = [v for v in values if v <= 0]
        
        bin_min = min(values) - 1
        bin_max = max(values) + 1
        bins = np.linspace(bin_min, bin_max, 15)
        
        ax.hist(selloff_vals, bins=bins, alpha=0.7, label=f'Selloff (n={len(selloff_vals)})', 
               color='#E94F37', edgecolor='white')
        ax.hist(rally_vals, bins=bins, alpha=0.7, label=f'Rally (n={len(rally_vals)})', 
               color='#26A69A', edgecolor='white')
        
        ax.axvline(x=0, color='gray', linestyle='--', linewidth=1.5)
        
        # Stats
        mean_ret = sum(values) / len(values)
        rally_rate = len(rally_vals) / len(values)
        
        stats_text = f"Mean: {mean_ret:.1f} bps\nRally rate: {rally_rate:.0%}\nN: {len(values)}"
        ax.text(0.98, 0.98, stats_text, transform=ax.transAxes, fontsize=10,
               verticalalignment='top', horizontalalignment='right',
               bbox=dict(boxstyle='round', facecolor='white', alpha=0.9))
        
        ax.set_xlabel('Return (bps)', fontsize=11)
        ax.set_ylabel('Count', fontsize=11)
        ax.set_title(f'TY Returns {horizon_label} After 20Y Supply', fontsize=12)
        ax.legend(loc='upper left')
        ax.grid(axis='y', alpha=0.3)
        
        filepath = output_dir / f"ret_distro_{y_col}.png"
        plt.tight_layout()
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close(fig)
        
        plots.append(filepath)
        logger.info(f"Created plot: {filepath}")
    
    return plots


# ============================================================================
# Output helpers
# ============================================================================


def save_joined_data(data: List[Dict[str, Any]], output_path: Path) -> None:
    """Save joined dataset to CSV."""
    if not data:
        return
    
    # Get all columns
    cols = ["Auction_str"] + NUMERIC_COLUMNS + HORIZON_LABELS
    
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in data:
            # Convert datetime to string if needed
            out_row = {k: v for k, v in row.items()}
            writer.writerow(out_row)
    
    logger.info(f"Saved joined data to {output_path}")


def save_regression_results(results: List[Dict[str, Any]], output_path: Path) -> None:
    """Save regression results to CSV."""
    if not results:
        return
    
    rows = []
    for reg in results:
        y_col = reg["y_col"]
        for i, x_col in enumerate(reg["x_cols"]):
            rows.append({
                "y_col": y_col,
                "x_col": x_col,
                "coefficient": reg["coefficients"][i],
                "std_error": reg["std_errors"][i],
                "t_stat": reg["t_stats"][i],
                "r_squared": reg["r_squared"],
                "n_obs": reg["n_obs"],
            })
    
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["y_col", "x_col", "coefficient", "std_error", "t_stat", "r_squared", "n_obs"])
        writer.writeheader()
        writer.writerows(rows)
    
    logger.info(f"Saved regression results to {output_path}")


def save_bucket_results(results: Dict[str, List[Dict[str, Any]]], output_path: Path) -> None:
    """Save bucket analysis results to CSV."""
    rows = []
    
    for y_col, horizon_results in results.items():
        for stats in horizon_results:
            for bucket in stats["buckets"]:
                rows.append({
                    "y_col": y_col,
                    "x_col": stats["x_col"],
                    "bucket": bucket["bucket"],
                    "x_min": bucket["x_min"],
                    "x_max": bucket["x_max"],
                    "x_mean": bucket["x_mean"],
                    "y_mean": bucket["y_mean"],
                    "y_std": bucket["y_std"],
                    "rally_rate": bucket["rally_rate"],
                    "selloff_rate": bucket["selloff_rate"],
                    "count": bucket["count"],
                    "is_monotonic": stats["is_monotonic"],
                    "monotonic_direction": stats["monotonic_direction"],
                })
    
    if rows:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            cols = ["y_col", "x_col", "bucket", "x_min", "x_max", "x_mean", "y_mean", "y_std", 
                    "rally_rate", "selloff_rate", "count", "is_monotonic", "monotonic_direction"]
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)
        
        logger.info(f"Saved bucket results to {output_path}")


def save_summary_report(
    data: List[Dict[str, Any]],
    reg_results: List[Dict[str, Any]],
    bucket_results: Dict[str, List[Dict[str, Any]]],
    output_path: Path,
) -> None:
    """Save a summary JSON report."""
    # Find significant predictors
    significant_regs = []
    for reg in reg_results:
        for i, t_stat in enumerate(reg["t_stats"]):
            if abs(t_stat) > 2.0 and reg["x_cols"][i] != "const":
                significant_regs.append({
                    "horizon": reg["y_col"],
                    "predictor": reg["x_cols"][i],
                    "coefficient": reg["coefficients"][i],
                    "t_stat": t_stat,
                })
    
    # Find monotonic relationships
    monotonic_rels = []
    for y_col, horizon_results in bucket_results.items():
        for stats in horizon_results:
            if stats["is_monotonic"]:
                monotonic_rels.append({
                    "horizon": y_col,
                    "predictor": stats["x_col"],
                    "direction": stats["monotonic_direction"],
                    "mean_diff": stats["range_mean_diff"],
                    "rally_diff": stats["range_rally_diff"],
                })
    
    report = {
        "summary": {
            "n_auctions": len(data),
            "horizons": HORIZON_LABELS,
            "predictors": NUMERIC_COLUMNS,
        },
        "significant_regressors": significant_regs,
        "monotonic_relationships": monotonic_rels,
        "regression_r_squared": {reg["y_col"]: reg["r_squared"] for reg in reg_results},
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    
    logger.info(f"Saved summary report to {output_path}")


# ============================================================================
# Main
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="20Y Supply Return Analysis")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT_CSV),
                        help="Path to auction CSV file")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR),
                        help="Output directory for results")
    parser.add_argument("--buckets", type=int, default=4,
                        help="Number of buckets for quantile analysis")
    parser.add_argument("--start-year", type=int, default=2020,
                        help="Only include auctions from this year onwards")
    parser.add_argument("--no-plots", action="store_true",
                        help="Skip generating plots")
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Starting 20Y Supply Return Analysis")
    logger.info(f"Input: {input_path}")
    logger.info(f"Output: {output_dir}")
    
    # Load auction data
    auctions = load_auction_data(input_path)
    
    # Build joined dataset with returns
    joined_data = build_joined_dataset(auctions, start_year=args.start_year)
    
    if not joined_data:
        logger.error("No data to analyze (check date range or market data availability)")
        return
    
    # Save joined dataset
    save_joined_data(joined_data, output_dir / "auction_returns_joined.csv")
    
    # Determine valid X columns (those with enough data)
    valid_x_cols = []
    for col in NUMERIC_COLUMNS:
        count = sum(1 for row in joined_data if row.get(col) is not None)
        if count >= 5:
            valid_x_cols.append(col)
    logger.info(f"Valid predictor columns: {valid_x_cols}")
    
    # Run regressions
    reg_results = run_all_regressions(joined_data, valid_x_cols)
    save_regression_results(reg_results, output_dir / "regression_results.csv")
    
    # Run bucket analysis
    bucket_results = run_all_bucket_analysis(joined_data, valid_x_cols, n_buckets=args.buckets)
    save_bucket_results(bucket_results, output_dir / "bucket_analysis.csv")
    
    # Save summary report
    save_summary_report(joined_data, reg_results, bucket_results, output_dir / "summary_report.json")
    
    # Run interaction analysis
    logger.info("Running interaction analysis...")
    interaction_predictors = ["Bid/Cover", "Direct%", "Indirect%", "Dealer%", "Stop", "Yld Gap"]
    interaction_results = run_interaction_analysis(joined_data, interaction_predictors)
    save_interaction_results(interaction_results, output_dir / "interaction_analysis.csv")
    
    # Generate plots
    if not args.no_plots:
        plots_dir = output_dir / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)
        
        # Overall return distribution plots (rally vs selloff colored)
        plot_overall_return_distributions(joined_data, plots_dir)
        
        # Return distributions colored by predictor bucket
        # Focus on predictors with monotonic relationships or significance
        key_predictors = ["Bid/Cover", "Direct%", "Indirect%", "Dealer%", "Stop", "Yld Gap"]
        plot_return_distributions_by_bucket(joined_data, plots_dir, key_predictors)
        
        # Interaction heatmaps for key pairs
        key_pairs = [
            ("Stop", "Indirect%"),
            ("Stop", "Dealer%"),
            ("Stop", "Bid/Cover"),
            ("Indirect%", "Dealer%"),
            ("Indirect%", "Direct%"),
            ("Dealer%", "Yld Gap"),
        ]
        plot_interaction_heatmaps(joined_data, plots_dir, key_pairs)
    
    logger.info("Analysis complete!")
    logger.info(f"Results saved to: {output_dir}")


if __name__ == "__main__":
    main()

