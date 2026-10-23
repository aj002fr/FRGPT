# C009 — High AF vs oscillatory (pressure-building)  tier: EXPLORATORY

## Problem statement

Does high depth-normalized aggressive flow at `t*` raise P(OSCILLATORY_UNRESOLVED) vs resolved outcomes — i.e. strong flow then chop rather than a further directional barrier?

## Motivation & provenance

- Backlog **BL-12**. C008: oscillatory on this cell is 100% tight-range chop. C001 found no continuation lift from AF. This cycle asks whether AF instead tags the *unresolved* class.
- Same 75-date prefix. Reuse C001 cache. No store.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
File: `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/episodes_c001.csv` (`episode_id`, `trading_date`, `AF_depth`, `outcome`).

## Suggested approach

Keep all three scored classes. Target: `y = 1{outcome == OSCILLATORY_UNRESOLVED}`.

- Rank-first terciles of `AF_depth` (T3 = highest).
- Headline: P(oscillatory | T3) − P(oscillatory | T1), 400 date-clustered bootstrap, seed 42.
- Diagnostic table: P(REVERSAL) and P(CONTINUATION) by AF tercile (not second headlines).
- **n-floor:** ≥ 30 episodes per AF tercile. If failed, do not headline a pp lift.

Do not match on mfe (C008 showed oscillatory *is* low mfe). Do not add NR/β. Do not open C010. No store.

## Success criteria

- **Advance:** T3−T1 oscillatory lift ≥ 5pp, CI not entirely ≤0, n-floor met.
- **PARK:** lift ≤ 0 or CI-only noise or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C009_pressure_af_osc/` and this cycle folder. No live dashboards, D:, C010.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `t3_minus_t1_osc_pp`, `n_scored`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
