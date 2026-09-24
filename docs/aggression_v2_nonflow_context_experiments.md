# Non-Flow Alert-Quality Context Study

## Executive Read

This was Phase 1 only: a read-only value check. V1/V2/V3, live stores, ports,
SOD, Tailscale, Telegram, and dashboards were not modified.

## Clear Value Verdict

**YES—but only for magnitude/resolution context, not directional PnL.**

High pre-alert realized volatility or range adds statistically controlled
holdout information about whether an alert is a real move and whether the
residual resolves. It does not pass the after-cost PnL improvement gate.

No dashboard is authorized by this study. Phase 2 requires separate user
review and approval on a fresh isolated copy.

## Data and Split

- 517 V2 alert rows.
- Options/skew context: 182 rows.
- Magnitude vol/range context: 452 rows.
- Magnitude holdout probabilities/outcomes: 152 rows.
- Depth overlap: 0 rows.
- Alerts within +/-30m of scheduled releases: 0 rows.
- Selection dates: 2026-06-08 through 2026-06-30.
- Holdout dates: 2026-07-01 through 2026-07-10 (9 dates).

Thresholds were fixed from the selection sample:

- Prior realized-vol 70th percentile: `0.01295`.
- Prior 10m range 70th percentile: `3 ticks`.

`MAGNITUDE_STATE` is active when either threshold is met.

## Validated Value

### Magnitude state: resolution quality

| Metric | All holdout alerts | Magnitude state | Improvement |
| --- | ---: | ---: | ---: |
| Resolution rate | 39.9% | 52.5% | +12.6pp |

- Conditioned n: 61.
- 95% date-clustered improvement CI: [+1.0pp, +24.8pp].
- BH-adjusted p: 0.061.
- Verdict: **PASS**.

### Magnitude state: real-move probability

| Metric | All scored alerts | Magnitude state | Improvement |
| --- | ---: | ---: | ---: |
| >=5-tick move rate | 13.8% | 26.2% | +12.4pp |

- Conditioned n: 61.
- 95% improvement CI: [+5.2pp, +19.5pp].
- BH-adjusted p: 0.004.
- Verdict: **PASS**.

### What magnitude state does not prove

30m after-cost fade PnL improves from `-0.27` ticks to `+0.10` ticks
(+0.37-tick lift), but the improvement CI is `[-0.07, +1.05]`.

Therefore magnitude state is a **move-size/resolution annotation**, not a
validated take/skip or direction signal.

## Quality Stack

The top quality-stack tier raises big-move rate from 13.8% to 23.0% (+9.2pp)
and passes FDR. That is weaker than magnitude state alone (26.2%).

Because the stack includes magnitude state, this does not demonstrate
incremental value from gamma/skew/event components. The stack should not be
preferred over the simpler magnitude state.

## Provisional Skew Context

`SKEW_AGREES_FADE`:

- 15m after-cost PnL: baseline `-0.40` ticks, conditioned `+0.72`.
- Improvement: +1.11 ticks.
- Improvement CI: [+0.08, +7.85].
- BH-adjusted p: 0.013.
- Conditioned n: 23.

It fails the pre-specified n>=30 requirement, so it remains **PROVISIONAL**.

At 30m the point improvement is similar (+1.12 ticks), but its CI crosses zero
and it does not survive FDR.

Skew agreement does not improve residual resolution.

## Not Evaluable

- **Gamma pin:** zero active holdout rows.
- **Event proximity:** zero alerts within +/-30m of a scheduled US release.
- **Depth stress:** no timestamp overlap with current V2 alert episodes.

These are coverage failures, not evidence of no value.

## Statistical Method

- Chronological 70/30 date split.
- Modifier thresholds fixed on selection dates.
- Date-clustered 10,000-draw bootstrap improvement CIs.
- Shared all-alert holdout baseline.
- BH-FDR q=0.10 pooled across 23 modifier/outcome tests.
- PASS requires conditioned n>=30, improvement CI lower bound >0, and FDR.

## Recommendation

If a new isolated dashboard copy is later approved, the only validated
non-flow annotation worth displaying is:

> **Magnitude state: elevated probability of a real move and residual
> resolution. Not directional.**

`SKEW_AGREES_FADE` may be shown only as provisional after more episode history.
Do not add gamma/event/depth badges until overlap exists and they are re-tested.

Phase 2 is not part of this execution.

## Artifacts

- `workspace/analysis/aggression_v2_nonflow_context/nonflow_episode_table.csv`
- `nonflow_modifier_stats.csv`
- `nonflow_context_summary.json`
- `nonflow_modifier_ranking.png`
