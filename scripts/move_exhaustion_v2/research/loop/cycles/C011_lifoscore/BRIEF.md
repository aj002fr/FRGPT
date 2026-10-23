# C011 — LIFOScore as a path descriptor                tier: EXPLORATORY

## Problem statement

On this prefix, does a frictionless **LIFOScore** rank `OSCILLATORY_UNRESOLVED` above resolved classes? Descriptive only (X7 B−A failed → Phase 4 shrinks to descriptive scope). Not “can AF predict E[LIFOScore]” — that is a later cycle if this descriptor works.

## Motivation & provenance

- Human approved BL-8 at the 10-cycle checkpoint (2026-08-13). Retired X9 / spec §22.
- C008: oscillatory is 100% tight-range chop. If LIFOScore is the finer third-class definition, it should be **higher** on oscillatory than on REVERSAL/CONTINUATION.
- Same 75-date prefix. `g`/`a` were never frozen on X9 — pin exploratory values below. Do not edit frozen YAML.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
Episodes: `labeled_B8_x2.parquet` (need `episode_id`, `trading_date`, `direction`, `t_star_source_order`, `t_star_ts_event`, `t_star_mid`, `outcome`). Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` — columns `ts_event`, `source_order`, `mid` (price-change rows only is fine). One day in RAM.

## Suggested approach (pinned)

- Tick = 0.015625. **g = 2 ticks**, **a = 2 ticks**. Three fade entries, opposite `direction`, triggered when mid first reaches `t_star_mid + direction × k × tick` for k=1,2,3 after `t*` (`source_order > t_star_source_order`).
- After trigger, that entry **wins** if mid then moves `−direction × g` ticks from the entry mid before `+direction × a` ticks. Horizon: 15 minutes of wall clock from `t_star_ts_event` (X1 primary H).
- `LIFOScore = n_win / n_triggered` (NaN if n_triggered=0).
- Headline: mean LIFOScore(OSCILLATORY) − mean LIFOScore(resolved = REVERSAL ∪ CONTINUATION), 400 date-clustered bootstrap, seed 42.
- Diagnostic table: mean LIFOScore by the three classes; share with n_triggered≥1.
- **n-floor:** ≥ 30 episodes with finite LIFOScore in oscillatory **and** in resolved. If failed, do not headline.

Vectorize per day; print progress. Do not predict from AF. Do not retune g/a. Do not open C012. No live dashboards, D:, holdout.

## Success criteria

- **Advance (descriptive):** osc − resolved mean LIFOScore ≥ 0.10, CI not entirely ≤0, n-floor met.
- **PARK:** difference ≤ 0 or CI-only noise or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR. Not a frozen X9 run.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C011_lifoscore/` and this cycle folder.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries; state Phase-4 descriptive scope).
- `meta.json` RESULTED with `osc_minus_resolved_lifoscore`, `n_osc`, `n_resolved`, holdout_opened false.
- `metrics.json` + episode CSV (episode_id, LIFOScore, n_triggered, n_win, outcome).
