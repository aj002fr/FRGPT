"""Atomic trade / panel contracts for move-exhaustion research."""

# Phase 1 consumes the existing V2 1-minute TBBO feature matrix rather than
# rebuilding raw trade prints. Required panel fields:

REQUIRED_PANEL = [
    "bucket_ts",
    "et_date",
    "price",
    "day_open_price",
    "trade_intensity",
    "agg_1m",
    "fair_value",
    "residual",
    "residual_z",
]

OPTIONAL_PANEL = [
    "spread_mean",
    "displayed_depth_mean",
    "size_imbalance_mean",
    "buysell_ratio",
    "large_trade_share",
    "avg_trade_size",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "US_agg_5m",
    "minutes_since_open",
]
