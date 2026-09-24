# C007 — Nested 3-class B−A                          tier: EXPLORATORY

## Problem statement

Does `AF_depth` improve **multiclass** prediction of {REVERSAL, CONTINUATION, OSCILLATORY_UNRESOLVED} beyond `mfe_ticks`? Nested **B−A** only. This is the one allowed iterate after C006's 2-class null.

## Motivation & provenance

- Parent **C006**: binary reversal-vs-rest ΔBrier ≈ 0. X7 / PQ3 is 3-class. C001 cache already has the three scored classes (UNRESOLVED_AMBIGUOUS and CENSORED already dropped).
- Same 75-date prefix. No store re-read unless the join fails.
- After this cycle, PARK BL-6 if still null (R001 one-iterate cap).

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.

- `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/episodes_c001.csv`
- Join `mfe_ticks` from `labeled_B8_x2.parquet` on `episode_id`

## Suggested approach

Target: `outcome` ∈ {REVERSAL, CONTINUATION, OSCILLATORY_UNRESOLVED}. Drop any other leftover labels and report the count.

- **A:** multinomial logistic `outcome ~ mfe_ticks`
- **B:** multinomial logistic `outcome ~ mfe_ticks + AF_depth`
- Walk-forward by `trading_date` (expanding; first 20 dates train; predict each later date). sklearn if available.
- Headline: OOS **multiclass ΔBrier = Brier(A) − Brier(B)** (positive = B better). Also one-vs-rest ΔAUC per class (diagnostic table, not three headlines).
- Date-clustered bootstrap 400 reps on OOS rows, seed 42.
- **n-floor:** ≥ 30 OOS episodes **per class**. If any class fails, do not headline ΔBrier; report underpowered.

Do not add other features, model C/D, a second horizon, validation, holdout, or C008.

## Success criteria

- **Advance:** OOS multiclass ΔBrier ≥ 0.005, CI not entirely ≤0, n-floor met, and at least one class has ΔAUC ≥ 2pp.
- **PARK BL-6:** ΔBrier ≤ 0 or CI-only noise or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR. Not X7 one-shot validation.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C007_nested_ba_3class/` and this cycle folder. No live dashboards, D:, C008.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `delta_brier_multiclass`, per-class ΔAUC, `n_oos`, holdout_opened false.
- `metrics.json` + OOS prediction CSV in the analysis loop folder.
