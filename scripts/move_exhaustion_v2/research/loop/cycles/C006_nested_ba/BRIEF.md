# C006 — Nested B−A: does AF add to the price path?  tier: EXPLORATORY

## Problem statement

On the X1 prefix, does depth-normalized aggressive flow improve reversal prediction beyond MFE (price-path proxy)? Nested contrast **B−A** only.

## Motivation & provenance

- Backlog **BL-6** / PQ3 (retired X7). R001: PQ1/PQ2 parked; skip BL-3; reuse cached features.
- C001 already computed `AF_depth` on the same 1,244 resolved episodes. Do **not** re-read the event store unless the cache is unusable.
- This is exploratory B−A, not the confirmatory 3-class walk-forward on validation.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.

Primary inputs (read-only):
- `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/episodes_c001.csv` (`episode_id`, `trading_date`, `AF_depth`, `outcome`, `mfe_tercile` if present)
- `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/labeled_B8_x2.parquet` for `mfe_ticks` (join on `episode_id`)

No validation/holdout. No store scan unless the join fails.

## Suggested approach

Resolved episodes only (already filtered in C001). Target: `y = 1{outcome == REVERSAL}`.

- **A:** logistic `y ~ mfe_ticks`
- **B:** logistic `y ~ mfe_ticks + AF_depth`
- Walk-forward by trading date (expanding window; first 20 dates as the initial train; predict each subsequent date). If sklearn is missing, use numpy least-squares probability via a clipped linear probability model and say so.
- Headline: OOS **ΔBrier = Brier(A) − Brier(B)** (positive = B better) and **ΔAUC**. Date-clustered bootstrap 400 reps on the OOS rows, seed 42.
- n-floor: require ≥ 30 OOS positives and ≥ 30 OOS negatives. If failed, do not headline Δpp-equivalent; report underpowered.
- Diagnostic only: C001 Q5−Q1 continuation was −2.0 pp — expect B−A near zero. Do not hunt extra features.

Do not add NR, β, IS, passive flow, model C/D, or a second horizon. Do not start C007.

## Success criteria

- **Advance:** OOS ΔBrier ≥ 0.005 and ΔAUC ≥ 2pp, CI for ΔBrier not entirely ≤0, n-floor met.
- **PARK:** ΔBrier ≤ 0 or CI-only noise or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR. Not the confirmatory X7 one-shot.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C006_nested_ba/` and this cycle folder. No live dashboards, D:, C007.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries; state 2-class-on-resolved limitation vs X7 3-class).
- `meta.json` RESULTED with `delta_brier_B_minus_A`, `delta_auc`, `n_oos`, holdout_opened false.
- `metrics.json` + OOS prediction CSV in the analysis loop folder.
