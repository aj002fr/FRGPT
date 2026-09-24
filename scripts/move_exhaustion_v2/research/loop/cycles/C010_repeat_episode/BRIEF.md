# C010 — First vs later episode of the day            tier: EXPLORATORY

## Problem statement

Is the **second** (or later) scored move-exhaustion episode on a date more likely to reverse than the **first** — a repeated-signal check, not a flow feature.

## Motivation & provenance

- Backlog **BL-9** (retired X10), lean slice: repeat count only. Do not require “exhaustion evidence strengthened” (needs flow state; C001–C009 already parked those).
- Same 75-date prefix. Labels-only. No store. No AF.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
File: `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.

## Suggested approach

Within each `trading_date`, rank remaining episodes by `t_star_source_order` (stable tie-break: `t_star_sequence`).

- `k=1`: first episode of the date
- `k=2`: second
- `k>=3`: later (pool)

Headline: P(REVERSAL | k=2) − P(REVERSAL | k=1), 400 date-clustered bootstrap, seed 42.
Diagnostic: k≥3 vs k=1; P(OSCILLATORY) by k (not a second headline).
**n-floor:** ≥ 30 episodes in k=1 and in k=2. If failed, do not headline.

Do not use AF/NR/β. Do not condition on prior-episode outcome (that’s a different brief). Do not open C011. No store.

## Success criteria

- **Advance:** k=2 minus k=1 reversal lift ≥ 5pp, CI not entirely ≤0, n-floor met.
- **PARK:** lift ≤ 0 or CI-only noise or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C010_repeat_episode/` and this cycle folder. No live dashboards, D:, C011.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `k2_minus_k1_reversal_pp`, `n_k1`, `n_k2`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
