# C018 — X1 30m vol-regime baseline                   tier: EXPLORATORY

## Problem statement

On the X1 prefix, does pre-t* realized range (30m wall clock) change P(REVERSAL)?

## Motivation & provenance

- New backlog **BL-19**. Last X1 conditioner that can be built on this prefix without expanding dates or fishing AF. Magnitude is pinned at B=8; velocity/pullback need a start-of-move that the labeled file does not carry.
- Store read of `mid` + `ts_event` only. No AF.

## Data window

Exactly `dates_scored` in `X1_20260813T143427Z/manifest.json`. Episodes from `labeled_B8_x2.parquet` after dropping ambiguous/censored. Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`.

## Suggested approach

- Lookback: store rows with `source_order < t_star_source_order` and `ts_event >= t_star_ts_event − 30 minutes`.
- `range_ticks = (mid_max − mid_min) / 0.015625`. Require ≥ 2 finite mids; report skips.
- Rank-first terciles of `range_ticks`. **Headline:** P(REVERSAL|T3) − P(REVERSAL|T1). Bootstrap 400, seed 42.
- **n-floor:** ≥ 30 per tercile.
- **Session cross-check:** T3−T1 inside RTH and non-RTH. TOD-restatement rule as C016.

No AF, no second window, no C019 in this worker.

## Success criteria

- **Advance (descriptive):** |T3−T1| ≥ 5 pp, CI excludes 0, n-floor met, not a TOD restatement.
- **PARK:** otherwise. No vol×AF iterate.
- `EXPLORATORY / NOT EVIDENCE`.

## Constraints

`holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C018_vol_regime/` and this cycle folder. No D:, no discovery expansion.

## Deliverables

`RESULT.md`, RESULTED `meta.json` with `t3_minus_t1_reversal_pp`, `n_valid`, metrics + CSV.
