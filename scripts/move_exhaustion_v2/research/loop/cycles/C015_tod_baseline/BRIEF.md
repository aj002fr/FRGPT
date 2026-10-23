# C015 — X1 TOD / session baseline                    tier: EXPLORATORY

## Problem statement

On the X1 prefix, does P(REVERSAL) differ in RTH vs non-RTH? This is the TOD conditioning X1 listed and never ran.

## Motivation & provenance

- New backlog **BL-16**. Human: go ahead after C014. Same 75-date prefix. Do not expand discovery.
- Flow threads on this prefix are parked (PQ1–PQ3, BL-3, BL-8, BL-10). BL-7 is another AF model after B−A failed — skip. BL-11/14 need MBP-10; BL-13 needs more dates.
- Labels-only. No store. No AF/IS/NR.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
Episodes: `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
Clock: `t_star_ts_event` converted to `America/New_York`.

## Suggested approach

Pin buckets **before** looking at rates (ET, left-inclusive / right-exclusive):

- **ASIA:** 18:00–03:00
- **LONDON:** 03:00–08:20
- **RTH:** 08:20–15:00
- **POST:** 15:00–17:00
- **OTHER:** 17:00–18:00 (maintenance; drop from headline; report n)

**Headline (one cell):** P(REVERSAL | RTH) − P(REVERSAL | non-RTH), where non-RTH = ASIA ∪ LONDON ∪ POST.
Date-clustered bootstrap 400 reps, seed 42.
**n-floor:** ≥ 30 RTH and ≥ 30 non-RTH. If failed, do not headline.

Diagnostic table (not a second headline): n and P(rev) per named bucket. Do not re-cut hours. Do not test event-days. Do not open C016.

## Success criteria

- **Advance (descriptive baseline, not a signal):** |RTH − non-RTH| ≥ 5 pp, bootstrap CI excludes 0, n-floor met.
- **PARK:** |lift| < 5 pp or CI includes 0 or n-floor fail.
- If it advances: record as an X1 caveat on the scoreboard. Do **not** iterate into AF×TOD on this prefix (R002 leftover rule).
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C015_tod_baseline/` and this cycle folder. No live dashboards, D:, C016. No dates outside the 75-date prefix.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `rth_minus_nonrth_reversal_pp`, `n_rth`, `n_nonrth`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
