# C003 — Impact decay as β difference, not ratio    tier: EXPLORATORY

## Problem statement

Does a decline in price impact per unit aggressive flow, measured as `β_late − β_early` up to `t*` only, predict reversal — after a shuffled-half placebo that must stay near zero?

## Motivation & provenance

- Backlog **BL-2** (PQ1). Parent **C002**: ratio `β_late/β_early` failed (T1−T3 reversal −2.0 pp; placebo −10.1 pp, not ~0). Feedback: iterate with the signed difference.
- Same X1 prefix (75 dates). P(REVERSAL)=0.3529 among scored.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`. Reuse `labeled_B8_x2.parquet`. Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`. No validation/holdout.

## Suggested approach

Reuse C002's up-to-t* half split (≥8 AGG trades per half, tick=0.015625, causal `source_order <= t_star_source_order`). Replace the headline feature:

- `β` per half as in C002.
- `ImpactDecay_diff = β_late − β_early` (skip if either half |flow|==0). Winsorize p1/p99.
- T1 = most negative (most decay), T3 = most positive. Headline: P(REVERSAL) T1 minus T3, 400 date-clustered bootstrap.
- **Placebo:** shuffle AGG trade order in the up-to-t* window, rebuild halves, recompute diff. Must be |lift| < 2pp.

Do not revive the ratio. Vectorize; print progress; one store day in RAM. BOOTSTRAP_N=400.

## Success criteria

- **Advance:** real T1−T3 ≥ 5pp, CI not entirely ≤0, placebo |lift| < 2pp, n/tercile ≥ 30.
- **PARK the BL-2 thread:** real lift ≤ 0, or placebo fails again.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. No post-t* events. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C003_impact_decay_diff/` and this cycle folder. No live dashboards, D:, C004.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `t1_minus_t3_reversal_pp`, `placebo_t1_minus_t3_pp`, `n_scored`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
