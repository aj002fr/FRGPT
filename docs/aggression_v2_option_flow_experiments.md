# TY Option Signed-Flow Research

## Executive Read

The staged read-only data system and 20-date pilot were completed.

The option print data is high quality and supports reliable signed-flow
construction. The pilot does **not** validate TY direction, magnitude/range,
flow/FV confluence, large/sweep effects, or flow persistence because validation
contains only three independent dates and no candidate clears the gate.

**Stop/go verdict: STOP before six-month expansion.**

No dashboard or live process was modified.

## Data System

Historical inventory:

- 1,471 dates available through AccessAPI, 2021-10-17 to 2026-07-12.
- Local read-only parquet mount:
  `D:\BigMarketDataParquet\OptionsMBP1_v2`
- 1,278 local daily files through 2026-03-01.

The API endpoint has no server-side filters and would transfer ~675 GB for 20
dates. The implemented solution uses local DuckDB predicate pushdown:

```text
action='T' AND underlying LIKE 'ZN%' AND dte<=20
```

Only compact trade prints are cached under:

`~/.market_data/ty_option_flow_cache/`

The source parquet remains read-only.

## Phase-0 and 20-Date Quality

Twenty local dates yielded:

- 34,096 retained TY option trades.
- Signed-flow usable: 99.99%.
- BBO-classified: 99.93%.
- Side-classified: 99.98%.
- BBO/side agreement: 100%.
- Instrument mapping: 100%.
- Duplicate rate before compact dedup sensitivity: 0.12%.
- Call share: 50.7%.
- Monthly OZN share: 28.5%; remaining flow is weekly options.

The current local February archive predates native Greek enrichment. Native
delta/vega/gamma coverage across the combined cache is only 0.15%, driven by
the small June/July API sample. The primary pilot therefore uses contracts,
premium, moneyness and expiry buckets—not fabricated Greeks.

## Feature Panel

- 34,152 cached trades including the initial API audit windows.
- 8,593 option-flow minutes.
- Historical TY price/FV/residual joined from TBBO/Ridge artifacts.
- Price join rate: 74% across the mixed local/API cache.
- ATM share: 51.7%.
- Large-print p90 share: 10.3%.
- One-second sweep-burst proxy: 5.3%.

Features include:

- call/put buy/sell contracts;
- underlying-direction-equivalent and premium flow;
- native delta/vega/gamma flow where available;
- ATM/near-wing/wing and front/deferred splits;
- 5/15/30m rolling flow and streaks;
- p90/p95 large prints and sweep bursts.

## Chronological Pilot Split

Dates with sufficient TY minute/flow coverage:

- Discovery: 6 dates.
- Validation: 3 dates.
- Untouched FINAL: 3 dates.

FINAL was not evaluated because no validation candidate passed.

## Experiment 1 — Direct Option Flow

Top-decile 5m net option-flow threshold: 1,452.9 directional contracts.

### Direction

Option-flow direction did not predict TY direction:

| Horizon | n | Mean signed ticks | Hit rate |
| --- | ---: | ---: | ---: |
| 5m | 206 | -0.11 | 35.4% |
| 15m | 206 | -0.33 | 33.5% |
| 30m | 206 | -0.69 | 37.9% |
| 60m | 198 | -0.38 | 39.9% |

No directional cell is positive or significant.

### Magnitude / Range

Compared with quiet-flow validation minutes:

| Horizon | Absolute-move lift | Range lift |
| --- | ---: | ---: |
| 5m | +0.55 ticks | +0.81 ticks |
| 15m | +0.56 | +1.08 |
| 30m | +0.70 | +1.25 |
| 60m | +0.30 | +1.35 |

Several raw clustered CIs are positive, but nothing survives the pilot gate:
only three validation dates and BH-adjusted p around 0.13 for the strongest
range cells.

Interpretation: option-flow extremes may indicate delivered range, not
direction, but this is provisional.

## Experiment 2 — Flow Versus Skew Repricing

Not evaluable in the February pilot:

- gamma/skew joined history begins later;
- no validation overlap with `opt_rr50_chg_30m` / skew index.

This is a coverage failure, not evidence of no relationship.

## Experiment 3 — Option Flow × Futures Flow/Fair Value

When option and futures flow agree, signed TY outcomes are negative on average.

When they disagree:

- 30m mean signed move: +0.44 ticks, n=61.
- 60m: +0.81 ticks, n=57.

CIs cross zero; only three validation dates; no FDR pass. The result resembles
earlier skew/futures disagreement hints but is not validated.

## Experiment 4 — Large Prints, Sweeps, ATM Versus Wing

Range-lift point estimates versus complementary validation baselines:

- p90 large prints: +0.73/+1.03/+1.29 ticks at 15/30/60m.
- ATM-dominant flow: +0.69/+0.94/+1.30 ticks.
- sweep bursts: +0.56/+0.31/+0.34 ticks.
- wing-dominant flow: approximately flat/slightly negative.

Several raw CIs are positive, but none survives the validation gate because
there are only three independent dates.

## Experiment 5 — Flow Persistence

Flow streaks of 3 or 5 minutes do not predict TY direction:

- signed means are near zero or negative;
- all CIs cross zero;
- no FDR pass.

Event-window flow was not separately evaluated because the February pilot lacks
the required event/modern gamma overlap.

## Gate and Final Decision

Requirements:

- n>=30;
- >=8 validation dates;
- positive clustered CI;
- BH-FDR significance.

Eligible candidates: 0.

- `candidate_leaderboard.csv` retains every pilot cell.
- `locked_final_results.csv` records no qualifying candidate.
- Six-month expansion was not executed.

## What Was Learned

1. Trade-level option flow is technically usable and classification quality is
   excellent.
2. Raw flow direction does not forecast TY direction in this pilot.
3. Extreme/large/ATM option flow may be a **range/magnitude context**, not a
   directional signal.
4. Option/futures disagreement remains a provisional hypothesis.
5. More independent validation dates are required before expansion.

## Artifacts

- `workspace/analysis/aggression_v2_option_flow/data_audit.csv/json`
- `option_flow_phase0_audit.png`
- `option_flow_1m.csv`
- `option_flow_features_summary.json`
- `option_flow_joined_panel.csv`
- `experiment_01_direct_flow.csv` through `experiment_05_event_persistence.csv`
- `candidate_leaderboard.csv`
- `locked_final_results.csv`
- `option_flow_research_summary.json`

No dashboard/live integration is authorized.
