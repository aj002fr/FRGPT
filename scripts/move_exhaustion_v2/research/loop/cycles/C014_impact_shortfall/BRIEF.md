# C014 — Impact shortfall vs reversal                 tier: EXPLORATORY

## Problem statement

Does walk-forward impact shortfall (price moved less than OLS expected given lookback AF) raise P(REVERSAL) beyond realized lookback progress itself?

## Motivation & provenance

- Backlog **BL-3** (retired X4). Human approved `research/loop/specs/BL3_impact_shortfall_spec.md` on 2026-08-13.
- Same 75-date X1 prefix. Do not expand discovery.
- Do **not** use `B=8` or `mfe_ticks` as realized move (that restates AF). Realized move is the lookback window’s own `Δm_W`.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
Episodes: `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` — `source_order`, `size`, `event_class`, `mid`. One day in RAM.

## Suggested approach

Follow the approved spec with **no retunes**:

- Last **N=100** store rows with `source_order < t_star_source_order` (C001 indexing).
- `Δm_W = direction × (mid_last − mid_first) / 0.015625`
- `AF_W = direction × sum(+size AGG_BUY, −size AGG_SELL)` in the same window
- Expanding OLS `Δm_W = α + β AF_W`; first **20** dates train; predict later dates
- Winsorize `AF_W` and `Δm_W` at train p1/p99 before each fit
- `IS_raw = ŷ − Δm_W`; `IS = IS_raw / s` with `s` = SD of train-window `IS_raw` (prior dates only)
- Headline on OOS rows with finite IS: rank-first IS terciles; P(REVERSAL|T3) − P(REVERSAL|T1); 400 date-clustered bootstrap, seed 42
- **n-floor:** ≥ 30 OOS episodes per IS tercile
- **E07 (mandatory):** Pearson `corr(IS, Δm_W)`; flag `|r| > 0.85`. Repeat T3−T1 within each `Δm_W` tercile (C004/C012 confined-sign rule)
- Report OOS R²; do not gate on it

No second N, no 75% of B, no richer `f(·)`, no `P_t − F_t`, no validation/holdout, no C015.

## Success criteria

- **Advance:** headline ≥ 5 pp, CI not entirely ≤ 0, n-floor met, `|corr(IS, Δm_W)| ≤ 0.85`, IS contrast not confined to one Δm tercile.
- **PARK:** lift ≤ 0, CI-only noise, n-floor fail, or E07 fail.
- `EXPLORATORY / NOT EVIDENCE`. Not X4 evidence. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C014_impact_shortfall/` and this cycle folder. No live dashboards, D:, C015. No dates outside the 75-date prefix.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries; E07 block required).
- `meta.json` RESULTED with `t3_minus_t1_reversal_pp`, `corr_is_dm`, `n_oos`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
