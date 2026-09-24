# TY Skew/Vol Shock -> Futures Path Study

## Executive Read

This was read-only research. Gamma and economic-event databases were opened
with `mode=ro`; V1/V2/V3/V2.1 and all live stores were untouched.

## Clear Verdict

**No directional, magnitude/range, realized-vol, or trend/range cell passes
the chronological holdout, clustered-CI, and FDR gate.**

The data shows a weak pooled short-horizon magnitude/range hint after
price-quiet option shocks, but the holdout is far too small:

- 54 total de-clustered shocks across 14 active dates.
- 23 total `PRICE_QUIET_SHOCK` events across 11 dates.
- Holdout: only 6 price-quiet shocks across 3 dates.
- Maximum primary per-arm holdout n: 2.

No dashboard or live signal is justified.

## Design

- Matched call/put IV legs, RR50, skew index, backbone and ATM/general vol.
- Causal trailing-15m changes and z-scores; `|z|>=1.5`.
- Selection-sample absolute-change floors.
- 30m cross-arm de-clustering.
- Signal enters at t0+1m, after the option shock is known.
- `PRICE_QUIET_SHOCK` excludes large formation-window TY moves and scheduled
  US-event windows; `PRICE_LED_VOL` is diagnostic only.
- Quiet-option controls come from other dates in the same chronological split,
  matched on time of day, RTH status, event status, residual z and pre-RV.

The original same-date/same-time control idea is infeasible because an
option-market shock applies to every row at that time; cross-date controls are
required.

## Coverage

Panel:

- 37,316 TY minute rows.
- 2026-06-10 19:48 UTC through 2026-07-13 14:41 UTC.
- Selection shock dates: 9.
- Holdout shock dates: 5.

Valid outcomes:

| Horizon | Valid shocks |
| --- | ---: |
| 1m | 54 |
| 5m | 54 |
| 15–480m | 53 |
| Calendar 24h | 51 |
| RTH close | 37 |
| Overnight | 36 |

Calendar 24h uses strict endpoint coverage and does not roll Friday events to
Monday.

## Pooled Diagnostics (not holdout-authoritative)

### Price-quiet shocks versus matched quiet-option controls

| Horizon | Absolute-move lift | Range lift | RV-ratio lift |
| --- | ---: | ---: | ---: |
| 5m | +0.47 ticks | +0.40 ticks | -0.007 |
| 15m | +0.12 | +0.21 | -0.045 |
| 30m | +0.34 | +0.57 | -0.020 |
| 60m | +0.61 | +0.68 | +0.027 |
| 120m | +0.33 | +0.23 | -0.009 |
| 240m | +0.28 | +0.43 | +0.001 |
| 480m | -1.57 | -0.05 | +0.025 |
| 24h | -1.09 | +2.46 | +0.068 |

This suggests at most a modest 5–120m range expansion. Realized-vol ratios are
approximately unchanged, and the absolute-move lift reverses by 8h/24h.

At 5m, price-quiet shocks show a pooled +10pp trend-rate and -22pp chop-rate
delta versus controls, but this is based on only 23 all-date events and is not
holdout/FDR evidence.

### Price-led vol shocks

Price-led shocks show larger 15m and 120m range/move deltas, but these are
diagnostic: TY already moved during shock formation, so they cannot establish
that options vol predicted the move.

At 8h/24h and overnight/RTH-close, price-led shocks generally deliver smaller
absolute moves/ranges than matched controls.

## Direction

No arm has enough primary holdout events for direction inference. The largest
per-arm holdout sample is 2 events across 2 dates. Raw signed deltas therefore
must not be interpreted as predictive.

The prior leg study's put-bid/down and call-soften/down patterns remain logging
candidates only; this broader study does not validate them.

## Trend Versus Range

Long horizons are classified as choppy by the fixed path-efficiency thresholds
for both shocks and controls. There is no FDR-controlled evidence that vol/skew
shocks cause either persistent trends or a higher stay-in-range rate.

## Statistical Gate

- Chronological 70/30 split by shock-active date.
- Date-clustered 10,000-draw event-minus-control CIs.
- Separate BH-FDR pools for direction, magnitude/range/RV and regime outcomes.
- PASS requires n>=30, at least 8 holdout dates, CI lower bound >0, and FDR.

No cell passes.

## Recommendation

Keep logging option shocks and rerun after substantially more independent
shock-active days. Current evidence does not support using skew/vol pops as:

- a TY direction signal;
- a reliable volatility expansion signal;
- a trend-versus-range classifier.

The only tentative observation is modest short-horizon delivered range after
price-quiet shocks, which is not validated.

## Artifacts

- `workspace/analysis/aggression_v2_vol_skew_shock_paths/shock_minute_panel.csv`
- `shock_events.csv`
- `shock_outcomes.csv`
- `matched_controls.csv`
- `primary_holdout_stats.csv`
- `price_led_diagnostics.csv`
- `robustness_stats.csv`
- `shock_paths_summary.json`
- `shock_path_heatmaps.png`
