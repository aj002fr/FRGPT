# C008 — Oscillatory bucket composition               tier: EXPLORATORY

## Problem statement

On the X1 prefix, what is inside `OSCILLATORY_UNRESOLVED` — near-miss continuation, near-miss reversal, or tight range?

## Motivation & provenance

- Backlog **BL-15**. PQ1–PQ3 nested-B−A are PARKED on this prefix (C001–C007). Do not re-test AF.
- X1 scored P(unresolved)=0.3135. Labels already have `mae_ticks`, `mfe_ticks`, `B_ticks`, barrier clocks.
- Labels-only. No store re-read. No flow features.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`. File: `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON` only.

## Suggested approach

Among `outcome == OSCILLATORY_UNRESOLVED`, assign mutually exclusive buckets using `B_ticks`:

- **near_cont:** `mfe_ticks / B_ticks >= 0.8`
- **near_rev:** `mae_ticks / B_ticks >= 0.8` and not near_cont (if both, count **both_near** separately)
- **tight_range:** `mfe_ticks / B_ticks < 0.5` and `mae_ticks / B_ticks < 0.5`
- **other:** remainder

Headline: share of oscillatory in each bucket, with date-clustered bootstrap 400 reps (seed 42) on the largest bucket's share. n-floor: ≥ 30 oscillatory episodes total; do not headline a bucket with n<30.

Diagnostic table (not a second headline): median `time_to_barrier_lob_events` is undefined for unresolved — use median `horizon` completeness already on the row if present, else skip clocks. Compare median mae/mfe (in ticks) vs REVERSAL and CONTINUATION.

Do not fit a model, do not use AF/NR/β, do not open C009. No store.

## Success criteria

- **Advance (diagnostic, not a signal):** one interpretable bucket has share ≥ 50% with n≥30 and CI excluding 50% on the wrong side (i.e. clearly majority), or tight_range ≥ 50%.
- **PARK:** mixed (no bucket ≥ 50%) or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C008_oscillatory_structure/` and this cycle folder. No live dashboards, D:, C009.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with bucket shares, `n_osc`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
