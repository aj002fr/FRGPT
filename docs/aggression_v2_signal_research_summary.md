# Aggression V2 Signal Research Summary

## Current Research Status

The expanded TBBO-backed research pass materially improved the evidence for a sparse residual-fade signal. The signal is still research-only, but the latest chronological holdout test supports continuing toward an explainability/research dashboard.

## Model Target

The model predicts within-day price deviation:

```text
target_price_delta = price_t - day_open_price
fair_value_t = day_open_price + model(X_t)
residual_t = price_t - fair_value_t
```

This avoids raw price-level regression and keeps the target roll/session safer.

## Candidate Signal

Direction:

```text
signal_direction = -sign(residual)
```

Meaning:

- positive residual: price rich vs fair value, fade short
- negative residual: price cheap vs fair value, fade long

Candidate thresholds tested:

- `abs(residual_z) >= 1.0`
- `abs(residual_z) >= 1.5`
- `abs(residual_z) >= 2.0`

Horizons tested:

- 15 minutes
- 30 minutes

## Research Findings

The first small-sample holdout weakened the signal, so the dataset was expanded to the maximum AccessAPI DB TBBO coverage available in the current window. The expanded run used 54,835 clean rows and 74 clean dates after quality filtering.

In the expanded chronological holdout, the strongest sparse rule was:

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
- residual/future-change correlation about `-0.52`

The 15-minute version also held up:

- `n = 525`
- hit rate about `80.7%`
- average directional move about `0.0342` price points

## Current Recommendation

Do not deploy V2 directly as a live trading signal yet, but proceed to a research dashboard and explainer for review.

The signal should be treated as a sparse, high-threshold fair-value dislocation flag, not a continuous predictor. The next step should be an explainability/research dashboard showing flags, historical outcomes, and driver contributions before any live deployment.

## Live Paper-Trading Logger

The V2 dashboard now includes a live paper-trading validator. It records every WATCH/DISLOCATION episode while the V2 process is running, even if the browser is closed.

For each alert it records:

- open price, fair value, residual, and residual z-score
- whether the residual closed back inside `abs(z)<1.0` within 30 minutes
- 15m and 30m directional move from the fade perspective
- worst adverse move over those windows

Finalized outcomes are appended to:

```text
workspace/analysis/aggression_v2_live/alert_log.csv
```

This live logger is for paper validation only; it is not an automated trading system.

## Known Limitations

- Holdout is chronological but still within one 2026 regime.
- No transaction costs, slippage, or execution delay.
- TBBO is quote-at-trade, not full quote update history.
- Signal thresholds were motivated by exploratory work and need more independent validation before automation.
