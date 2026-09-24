# Aggression V2 Statistical Gate Re-evaluation

## Executive Read

This was **research-only** and additive. Existing experiment scripts and their recorded verdicts were not modified. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were not changed.

New files:

- `scripts/aggression_gate_lib.py`
- `scripts/aggression_v2_gate_reeval_research.py`

Outputs:

- `workspace/analysis/aggression_v2_gate_reeval/reeval_cells.csv`
- `workspace/analysis/aggression_v2_gate_reeval/reeval_summary.json`

## Bottom Line

The old gate was too crude, but the statistical rework does **not** authorize a dashboard.

Across 47 headline cells:

| Tier | Count |
| --- | ---: |
| PASS | 0 |
| PROVISIONAL | 11 |
| FAIL | 36 |

The new gate correctly separates positive but incomplete leads from negative/no-signal results. It does not rescue the broad all-minutes strategies.

## Statistical Gate

Each cell now reports:

- Date-clustered 10,000-draw bootstrap confidence interval.
- One-sided test of positive after-cost expectancy using date-cluster means.
- Benjamini-Hochberg FDR adjustment across all 47 cells (`q=0.10`).
- Cost sensitivity at `0`, `0.25`, `0.5`, and `1.0` ticks.
- Adverse excursion.
- Context-stability status.

Tier definitions:

- **PASS**: `n>=30`, BH-FDR significant, one-sided lower confidence bound `>=0`, and context stability passes.
- **PROVISIONAL**: positive after-cost point estimate but incomplete significance/sample/stability evidence.
- **FAIL**: non-positive after-cost estimate or no signal.

## Cells Surviving Multiple-Testing Correction

Six cells remain significant after BH-FDR:

| Experiment | Cell | n | After Cost | 95% CI | BH adjusted p | Tier |
| --- | --- | ---: | ---: | --- | ---: | --- |
| Event window | first-5m flow follow, 5m outcome | 49 | +1.30 | [+0.72, +2.08] | 0.006 | PROVISIONAL |
| Event window | first-5m flow follow, 15m outcome | 49 | +2.15 | [+1.01, +3.73] | 0.042 | PROVISIONAL |
| Event window | EV top-10%, 5m | 9 | +3.72 | [+1.75, +5.14] | 0.049 | PROVISIONAL |
| Event window | EV top-10%, 15m | 8 | +6.63 | [+3.00, +10.00] | 0.049 | PROVISIONAL |
| Event window | EV top-10%, 30m | 6 | +8.00 | [+5.33, +10.50] | 0.032 | PROVISIONAL |
| Magnitude | top-decile 30m opportunity | 16 | +4.13 opportunity ticks | [+2.88, +5.81] | 0.058 | PROVISIONAL |

Important:

- The magnitude cell is non-directional opportunity magnitude, not trade PnL. It is intentionally not PASS-eligible.
- Event-window cells are event-only strategies. They cannot satisfy an `RTH × no-event` stability requirement because “no event” removes the strategy definition itself. They remain PROVISIONAL until a pre-specified event-context stability test is approved.
- The EV-gated event cells are statistically interesting but have only 6-9 observations.

## Main Event-Window Readout

The de-duplicated event-window lead is now stronger than a simple `n=49 vs 50` near-miss:

- 5m flow-follow: BH significant, CI entirely above zero.
- 15m flow-follow: BH significant, CI entirely above zero.
- 30m flow-follow: positive CI, but does not survive BH (`adjusted p=0.132`).

This is evidence that the hard `n>=50` cliff was too crude. It is still not enough for a dashboard because the stability requirement is not defined appropriately for an event-only strategy.

## What Stayed FAIL

The following broad ideas remain negative or non-significant:

- All-minutes forward-return model direction.
- EV-selective all-minutes trading.
- Meta-label take/skip subsets.
- Stand-aside keep pool.
- Most resolution top-decile cells after multiple-testing correction.
- V2 × V3 formation/confluence cells.

The statistical gate does not change the economic conclusion for these experiments.

## Cost Sensitivity

`reeval_cells.csv` includes mean expectancy at 0, 0.25, 0.5, and 1.0 tick costs. Event-window 5m/15m remains positive at 1.0 tick cost. Most all-minutes signals remain negative even under the favorable 0.5-tick assumption.

## Verdict

No PASS. No dashboard. No live change.

The new gate is better calibrated:

- Event-window and magnitude are formally PROVISIONAL.
- Negative/no-signal experiments remain FAIL.
- Multiple-testing correction prevents threshold loosening from becoming post-hoc p-hacking.

Next action, if separately approved, should be to define a pre-registered event-context stability test (for example, stability across release families and calendar months) and re-run event-window only. That is a new research decision, not part of this re-evaluation.
