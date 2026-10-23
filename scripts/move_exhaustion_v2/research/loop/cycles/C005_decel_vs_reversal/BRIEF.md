# C005 — Flow deceleration vs sign reversal         tier: EXPLORATORY

## Problem statement

At `t*`, is a late-window flow **sign flip** a stronger reversal marker than same-signed **deceleration**, and is deceleration earlier/noisier?

## Motivation & provenance

- Backlog **BL-5** (retired X6). BL-1/BL-2/BL-4 PARKED on this prefix. PQ1 and PQ2 have no lead.
- Same X1 75-date prefix. P(REVERSAL)=0.3529 among scored.
- Do not rebuild C001 (flow *level*) or C002 (price-impact β). This is a **state** contrast: accelerating vs decelerating vs reversed.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`. Reuse `labeled_B8_x2.parquet`. Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`. No validation/holdout.

## Suggested approach

Causal lookback: last **N=100** store rows with `source_order < t_star_source_order` (C001/C004 indexing). Split that lookback into **early/late halves by event count** (`n//2`).

- Per half: `AF = direction × sum(+size AGG_BUY, −size AGG_SELL)`.
- States (mutually exclusive, late-half decides the flip):
  - **reversed:** `AF_late < 0` (late flow opposes the move).
  - **decelerating:** `AF_late ≥ 0` and `AF_late < AF_early` (still confirming, weaker).
  - **accelerating:** `AF_late ≥ 0` and `AF_late ≥ AF_early`.
- Drop episodes with fewer than 8 AGG trades in the lookback; report skip count.
- Headline: P(REVERSAL | reversed) − P(REVERSAL | accelerating), 400 date-clustered bootstrap, seed 42.
- Secondary (one table, not a second headline): P(REVERSAL | decelerating) vs the other two; median bars-from-anchor (`t_star` progress / B if a progress field exists, else skip earliness) — only if the column is already on the labeled parquet. Do not join extra stores for earliness.
- Optional light control: report the reversed−accelerating contrast inside `mfe_ticks` terciles. If the sign lives in only one tercile, say so.

Do not fit a model, do not use β/impact, do not open C006. Vectorize; one store day in RAM; print progress.

## Success criteria

- **Advance:** reversed−accelerating reversal lift ≥ 5pp, CI not entirely ≤0, n per state ≥ 30, and decelerating sits between them (or is clearly earlier if an earliness field exists).
- **PARK:** lift ≤ 0 or CI-only noise, or state contrast collapses after mfe control.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. No post-t* events. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C005_decel_vs_reversal/` and this cycle folder. No live dashboards, D:, C006.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `reversed_minus_accel_reversal_pp`, `n_scored`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
