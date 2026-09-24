# Aggression V2 Model Explainer

## Purpose

Aggression V2 is a research-only extension of the curve aggression monitor. Its purpose is to test whether order-flow and quote-at-trade features can define a more useful fair value and identify sparse rich/cheap dislocations.

The current result is promising only for **extreme residual mean reversion**, not for continuous prediction.

## Target

The model does not predict raw price level. Raw price is non-stationary and can create misleading regressions.

Instead, the target is within-day price deviation:

```text
target_price_delta = price_t - day_open_price
fair_value_t = day_open_price + model(X_t)
residual_t = price_t - fair_value_t
```

Interpretation:

- positive residual: price is rich vs fair value
- negative residual: price is cheap vs fair value
- signal direction: fade the residual, `-sign(residual)`

## Data

The V2 research uses historical TBBO from AccessAPI:

```text
source=database
schema=tbbo
format=arrow
symbols=TY,FV,TU,US
```

TBBO gives the trade and best bid/offer at trade time:

```text
price, size, side, bid_price, ask_price, bid_size, ask_size
```

This is quote-at-trade data, not full quote-update data.

## Feature Families

### Aggression / Flow

- `agg_cum_daily`: cumulative signed DV01 flow reset each day.
- `agg_1m`, `agg_5m`, `agg_30m`: signed DV01 flow over different horizons.
- Cross-tenor `FV_agg_5m`, `TU_agg_5m`, `US_agg_5m`: whether the rest of the curve agrees with the target.

### Buy/Sell Pressure

- `buysell_ratio`: smoothed percentage imbalance from buy vs sell aggression.

### Activity / Size

- `trade_intensity`: trades per minute.
- `avg_trade_size`: average trade size.
- `large_trade_share`: share of volume from large prints.

### Quote-At-Trade Liquidity

- `spread_mean`: average bid/ask spread.
- `size_imbalance_mean`: `(bid_size - ask_size) / (bid_size + ask_size)`.
- `displayed_depth_mean`: `bid_size + ask_size`.
- `trade_size_vs_depth`: trade size divided by displayed top-of-book depth.
- `depth_adjusted_signed_flow`: signed flow divided by displayed top-of-book depth.

### Price Regime

- `realized_vol`: rolling volatility of 1-minute price changes.
- `mom_5m`: trailing 5-minute price momentum.
- `minutes_since_open`: time of day.

## Models

### OLS Baseline

Uses the closest analogue to the current fair-value model, primarily cumulative aggression.

### Ridge

Regularized linear model. It is useful when features are correlated. Current sparse residual signal research uses Ridge residuals.

### Lasso

Regularized linear model that can zero out features. Useful for feature selection, but in the expanded sample it did not outperform OLS/Ridge.

### Random Forest

Nonlinear benchmark only. It underperformed on the current research sample and should not be used as the primary signal.

## Current Research Findings

Expanded sample:

- 54,835 clean rows
- 74 clean dates
- chronological walk-forward validation

Descriptive fair-value fit:

- OLS baseline had best OOS fit.
- Ridge was close but not superior on fit.
- Random Forest underperformed.

Signal validation:

- continuous residuals are not enough.
- sparse extreme residuals are more interesting.

Best chronological holdout rule:

```text
abs(residual_z) >= 2.0
horizon = 30 minutes
direction = -sign(residual)
```

Holdout results:

- `n = 492`
- hit rate about `81.1%`
- average directional move about `0.0647` price points
- median directional move `0.046875` price points

## How To Read The V2 Dashboard

### Fair Value Chart

Shows actual price vs selected model fair value. Wide gaps indicate rich/cheap conditions.

### Residual Z-Score Chart

Shows standardized residual by day. The key research threshold is `abs(z) >= 2`.

Live V2 now computes z with a historical prior plus today's causal data:

```text
z = (residual - blended_mean) / blended_std
```

At the daily model fit, V2 estimates the historical residual mean and residual volatility from the clean training days. During the live session, it blends that historical context with today's expanding residual distribution. Early in the day, the historical prior dominates; as more 1-minute rows arrive, today's own distribution takes over.

This avoids a z-score that depends only on when the dashboard was launched and removes the old early-session blind spot. New alert rows are tagged with `z_method = prior_blend_v1` so live results before and after this change can be separated.

### Live Alert Outcomes

There are two scoreboards:

- **Resolved / expired** grades the signal. Resolved means `abs(z)` dropped below `1.0` before the 30-minute deadline. Expired means the dislocation persisted.
- **30m hit / 30m pnl / 30m adverse** grades the trade. It assumes the alert was faded at the open price and held for exactly 30 minutes. `30m adverse` is the worst price move against that fade during the window.

These can disagree. A signal can resolve because fair value drifts to price, even if price does not move enough to pay the trader. A trade can also make money even if z remains elevated.

### Hold Expectations

The Hold Expectations card uses stored live alert episodes to answer three practical questions:

- **Median resolve**: the typical time, in minutes, for resolved alerts to get back below `abs(z) < 1.0`. This is the expected holding-time guide.
- **Median adverse ticks**: the typical worst price move against the fade before the alert resolves. This is the drawdown budget. One tick is `1/64 = 0.015625` price points.
- **Price share**: how much of the residual gap closed because price moved, rather than fair value drifting. A value near `100%` means the reversion mostly paid through price. A value near `0%` means the model gap closed mostly because fair value moved toward price, which may not produce much PnL.

The primary exit rule is still z-based: the signal is done when `abs(z) < 1.0`. The displayed price/fair-value gap is informative, but it is not a guaranteed resting target because fair value updates every minute as flow and quote features change.

### Model Comparison

Compares OOS R² and RMSE. This tells which model explains fair value best, not necessarily which signal trades best.

### Holdout Signal Rules

Shows rules tested only on later unseen dates. This is more important than pooled in-sample-looking results.

### Feature Drivers

For linear models, standardized coefficients show which features push fair value up/down. For Random Forest, importances are benchmark-only.

## Current Recommendation

Use V2 as a research/explainability dashboard first.

Do not automate live trading from this yet. The signal is promising as a sparse flag, but still needs event filters, transaction-cost checks, and more independent validation.
