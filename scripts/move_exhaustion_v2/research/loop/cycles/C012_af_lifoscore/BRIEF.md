# C012 — Does AF_depth predict E[LIFOScore]?         tier: EXPLORATORY

## Problem statement

After controlling for MFE, does depth-normalized aggressive flow at `t*` predict LIFOScore?

## Motivation & provenance

- Parent **C011**: LIFOScore is an anti-continuation fade-success score (reversal 0.85 > osc 0.70 > cont 0.33). Union osc−resolved passed; it is **not** a unique third-class label.
- X9 target (descriptive Phase 4): can flow state predict `E[LIFOScore | X]`?
- Same 75-date prefix. Cache join only. No store. No g/a retune. R001: this is the one BL-8 iterate; PARK the thread if null.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.

- `workspace/analysis/move_exhaustion_v2/loop/C011_lifoscore/episodes_c011.csv` (`episode_id`, `LIFOScore`, `outcome`, …)
- `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/episodes_c001.csv` (`episode_id`, `AF_depth`, `trading_date`)
- `mfe_ticks` from `labeled_B8_x2.parquet` on `episode_id`

Keep rows with finite LIFOScore and finite AF_depth.

## Suggested approach

- Rank-first **AF_depth terciles** (T3 = highest).
- Headline: mean LIFOScore(T3) − mean LIFOScore(T1), 400 date-clustered bootstrap, seed 42.
- Control: repeat the T3−T1 contrast **within each mfe tercile**; report whether the sign is confined to one mfe bucket (same rule as C004).
- **n-floor:** ≥ 30 finite-LIFOScore episodes per AF tercile. If failed, do not headline.

Do not retune LIFOScore. Do not add NR/β. Do not open C013. No store.

## Success criteria

- **Advance:** T3−T1 ≥ 0.10, CI not entirely ≤0, n-floor met, and the sign is not confined to a single mfe tercile.
- **PARK BL-8:** lift ≤ 0 or CI-only noise or n-floor fail or mfe-confined.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C012_af_lifoscore/` and this cycle folder. No live dashboards, D:, C013.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `t3_minus_t1_lifoscore`, `n_valid`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
