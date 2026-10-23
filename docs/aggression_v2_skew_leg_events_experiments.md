# TY Put/Call Skew-Leg Shock Event Study

## Executive Read

This was read-only research. The gamma tracker DB was opened with SQLite
`mode=ro`; V1/V2/V3 and all live stores were untouched.

The study directly decomposed:

`RR50 = call_iv_50 - put_iv_50`

and tested separate call/put IV shocks instead of treating every RR move as the
same mechanism.

## Bottom Line

**No arm passes the pre-registered holdout/FDR gate.**

There are two downside leads worth continuing to log:

1. **Put IV bids → TY down**, mainly over 30–60 minutes.
2. **Call IV softens → TY down**, the most consistent pattern.

Both are underpowered: the holdout contains only four skew-active days.

Call IV bidding before a rally and put IV softening before a rally are not
supported; both move in the wrong direction on average in this sample.

## Data Integrity

Matched call/put legs were required from the same snapshot, expiry, and
moneyness before front-weighting. This avoids composition changes when only one
leg is present.

RR reconstruction:

| Metric | n | Correlation with stored RR | MAE |
| --- | ---: | ---: | ---: |
| RR25 | 18,720 | 1.0000 | numerical zero |
| RR50 | 18,749 | 1.0000 | numerical zero |
| RR75 | 18,890 | 1.0000 | numerical zero |

Panel: 27,119 minutes, 2026-06-15 through 2026-07-08.

## Primary Holdout Results

Moneyness 50, causal 30-minute IV-change z-score `|z| >= 1.5`, 30-minute
same-arm cooldown.

### Put IV bids, expected TY down

| Horizon | n | Hit | Mean signed ticks | 95% clustered CI |
| --- | ---: | ---: | ---: | --- |
| 5m | 15 | 26.7% | 0.00 | [-0.61, +0.53] |
| 15m | 15 | 60.0% | +0.93 | [+0.33, +1.41] |
| 30m | 15 | 80.0% | +1.47 | [+0.11, +2.94] |
| 60m | 15 | 60.0% | +2.47 | [0.00, +4.59] |

Economically encouraging at 15–60m, but only 15 events across four days and no
cell survives BH-FDR.

### Call IV softens, expected TY down

| Horizon | n | Hit | Mean signed ticks | 95% clustered CI |
| --- | ---: | ---: | ---: | --- |
| 5m | 17 | 47.1% | +0.88 | [0.00, +1.35] |
| 15m | 17 | 64.7% | +1.24 | [+0.60, +1.83] |
| 30m | 17 | 70.6% | +1.65 | [+1.00, +2.04] |
| 60m | 17 | 70.6% | +2.94 | [+1.43, +4.25] |

This is the most consistent arm, but it also fails FDR because effective
cluster count is four days.

### Call IV bids, expected TY up

Mean signed moves are negative at every horizon:

- 5m: -0.69 ticks
- 15m: -1.50
- 30m: -1.63
- 60m: -2.00

### Put IV softens, expected TY up

Also negative at every horizon:

- 5m: -0.61 ticks
- 15m: -0.94
- 30m: -1.39
- 60m: -2.11

## Pooled Diagnostics

Across all dates:

- Put-bid/down 60m: +1.66 ticks, CI [+0.42, +2.68], n=47.
- Call-soften/down 15m: +0.84 ticks, CI [+0.31, +1.25], n=49.
- Call-soften/down 30m: +1.29 ticks, CI [+0.68, +1.67], n=49.
- Call-soften/down 60m: +1.69 ticks, CI [+0.17, +2.82], n=49.

These pooled results are diagnostic only. The chronological holdout controls
the verdict.

Moneyness 25/75 robustness generally keeps the longer-horizon put-bid/down and
call-soften/down signs, but still has only 3–4 holdout days.

## General-Vol / Overlap Cases

Similar same-direction moves in both legs were kept out of the four arms:

- Moneyness 50 general-vol up: 223 de-clustered events.
- Moneyness 50 general-vol down: 183.
- Moneyness 50 cross-arm overlap: 9.

This prevents broad volatility shocks from being mislabeled as a single-leg
put or call mechanism.

## Statistics

- Chronological 70/30 split by skew-active ET date.
- Holdout: 2026-07-02, 07-06, 07-07, 07-08.
- Date-clustered 10,000-draw bootstrap confidence intervals.
- BH-FDR q=0.10 across 16 primary arm × horizon tests.
- Research-lead requirement: n>=30, CI lower bound >0, BH significant, and
  consistent adjacent horizons.

No arm meets all requirements.

## Verdict

The user's original intuition is **partially visible only after decomposing the
legs**:

- Put IV bidding has a possible delayed downside association.
- Call IV softening has a stronger downside association.
- Call IV bidding and put IV softening do not predict rallies here.

These are logging/re-test candidates, not dashboard or trading signals. More
independent skew-active days are required before drawing a conclusion.

## Artifacts

- `workspace/analysis/aggression_v2_skew_leg_events/leg_minute_panel.csv`
- `skew_leg_events.csv`
- `skew_leg_diagnostics.csv`
- `primary_holdout_stats.csv`
- `robustness_stats.csv`
- `skew_leg_events_summary.json`
- `skew_leg_event_moves.png`
