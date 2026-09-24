# C002 — Impact decay vs reversal (up to t* only)    tier: EXPLORATORY

## Problem statement

Does declining price impact per unit aggressive flow through the move, measured only up to `t*`, predict reversal?

## Motivation & provenance

- Backlog **BL-2** (retired X3 / Priority Q1). C001 parked BL-1 (depth-norm AF → continuation: −2.0 pp, CI crosses 0).
- Same X1 prefix baseline: 75 dates 2022-01-05 through 2022-04-25; P(REVERSAL)=0.3529 among scored.
- Known pitfall: decay measured while the move is still extending after t* leaks the label. **β must use only events with source_order ≤ t_star_source_order.** Mandatory shuffled-thirds placebo must be near zero.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`. Reuse `labeled_B8_x2.parquet`. Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`. No validation/holdout dates.

## Suggested approach

Lean one-cell version (exploratory; not the retired 4-cell FDR family):

1. Scored X1 episodes only (drop UNRESOLVED_AMBIGUOUS and CENSORED_INCOMPLETE_HORIZON). Reversal = outcome `REVERSAL`.
2. On each day, events with `source_order <= t_star_source_order` (up to and including t*). Split that window into **early/late halves** by trade-event count (`event_class` in {AGG_BUY, AGG_SELL}), not wall clock. Require ≥ 8 trades per half (relaxed from the retired ≥15/third so this prefix can run; report how many episodes fail the floor).
3. Per half: `β = signed_mid_change_ticks / signed_aggressive_size` where signed mid change is `direction * (mid_end − mid_start)` in ticks (tick=0.015625) and signed aggressive size is `direction * sum(+size AGG_BUY, −size AGG_SELL)` in that half. Skip half if |flow| == 0.
4. `ImpactDecay = β_late / β_early` if |β_early| > 0; else skip. Winsorize ImpactDecay at sample p1/p99.
5. Headline: mean ImpactDecay | REVERSAL vs | CONTINUATION, and P(REVERSAL) in ImpactDecay tercile T1 (most decay) minus T3 (least decay), date-clustered bootstrap **400** reps.
6. **Placebo:** shuffle the early/late half labels within the up-to-t* window (permute trade order thirds/halves without using post-t* data) and repeat the T1−T3 reversal lift. Placebo should be ~0. If placebo is as large as the real lift, kill the claim.

Do not run a second clock or second AF normalization in this cycle. Vectorize; print progress; do not hold all store days in RAM.

## Success criteria

- **Advance (ITERATE):** real T1−T3 reversal lift ≥ 5pp, CI not entirely ≤ 0, placebo |lift| < 2pp, n per tercile ≥ 30.
- **PARK:** real lift ≤ 0, or placebo as large as real, or too few episodes pass the trade-floor.
- Label `EXPLORATORY / NOT EVIDENCE`. No FDR verdicts.

## Constraints

Frozen configs read-only. Causal: no events after t*. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C002_impact_decay/` and this cycle folder. No D:, live dashboards, C003.

## Deliverables

- `RESULT.md` in this cycle folder (template LOOP_PROTOCOL §5; non-empty failed-tries).
- Update `meta.json`: status RESULTED, worker_model, headline_metrics (`t1_minus_t3_reversal_pp`, `placebo_t1_minus_t3_pp`, `n_scored`), holdout_opened false.
- Artifacts: `metrics.json` + episode CSV under the analysis loop folder.
