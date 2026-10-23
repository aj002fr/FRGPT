# C017 — X1 direction baseline                        tier: EXPLORATORY

## Problem statement

On the X1 prefix, does P(REVERSAL) differ for up-moves vs down-moves?

## Motivation & provenance

- New backlog **BL-18**. Completing leftover X1 labels-only conditioners on the locked 75-date prefix. No discovery expansion.
- `direction` is already on the labeled parquet. No store. No AF.

## Data window

Exactly `dates_scored` in `X1_20260813T143427Z/manifest.json`. `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.

## Suggested approach

- Up = `direction == +1`, down = `direction == -1`.
- **Headline:** P(REVERSAL | up) − P(REVERSAL | down). Bootstrap 400, seed 42.
- **n-floor:** ≥ 30 each side.
- **Session cross-check:** repeat inside RTH and non-RTH (C015 buckets). TOD-restatement rule as C016.

No AF, no event-day re-cut, no C018 in this worker.

## Success criteria

- **Advance (descriptive):** |up − down| ≥ 5 pp, CI excludes 0, n-floor met, not a TOD restatement.
- **PARK:** otherwise. No direction×AF iterate.
- `EXPLORATORY / NOT EVIDENCE`.

## Constraints

`holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C017_direction/` and this cycle folder.

## Deliverables

`RESULT.md`, RESULTED `meta.json` with `up_minus_down_reversal_pp`, `n_up`, `n_down`, metrics + CSV.
