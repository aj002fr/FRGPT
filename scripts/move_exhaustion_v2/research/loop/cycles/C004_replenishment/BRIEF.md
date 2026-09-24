# C004 — Opposing-side replenishment vs reversal    tier: EXPLORATORY

## Problem statement

After matching on aggressive flow, does opposing-side L1 replenishment at `t*` raise reversal probability?

## Motivation & provenance

- Backlog **BL-4** (retired X5 / PQ2). BL-1 PARKED (C001). BL-2 PARKED (C002–C003 placebo failures).
- Same X1 75-date prefix. P(REVERSAL)=0.3529 among scored.
- L1-only proxy (A3); MBP-10 unavailable — state that limitation in RESULT.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`. Reuse `labeled_B8_x2.parquet`. Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`. No validation/holdout.

## Suggested approach

Causal lookback: last **N=100** store rows with `source_order < t_star_source_order`.

- Signed AF as in C001: `AF_dir = direction × sum(+size AGG_BUY, −size AGG_SELL)`.
- Opposing replenishment: if `direction==+1` (up), `NR_opp = sum(size | ADD_ASK) − sum(size | CXL_ASK)`; if `direction==-1`, use ADD_BID − CXL_BID. Ignore other event classes.
- Match on **AF_dir tercile**. Within each tercile, `NR_opp` terciles. Headline: pooled P(REVERSAL) in high-NR_opp tercile minus low-NR_opp, 400 date-clustered bootstrap.
- One-line nested check (not a model bake-off): within each AF tercile, is the NR contrast the same sign? If it only appears in one AF tercile, say so.

Do not run RR, second N, or a logistic. Vectorize; print progress; one store day in RAM.

## Success criteria

- **Advance:** high−low NR_opp reversal lift ≥ 5pp, CI not entirely ≤0, n per tail ≥ 30, and the sign is not confined to a single AF tercile.
- **PARK:** lift ≤ 0 or CI-only noise.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. No post-t* events. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C004_replenishment/` and this cycle folder. No live dashboards, D:, C005.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries; A3 L1-proxy limitation).
- `meta.json` RESULTED with `nr_high_minus_low_reversal_pp`, `n_scored`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
