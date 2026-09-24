# C016 — X1 event / non-event day                     tier: EXPLORATORY

## Problem statement

On the X1 prefix, does P(REVERSAL) differ on scheduled event days vs other days?

## Motivation & provenance

- New backlog **BL-17**. Human: complete the 75-date loop; do not expand discovery.
- Last skipped X1 day-level conditioner (OQ-10: `workspace/economic_events.db`, US high-importance + coupon/TIPS auctions).
- Labels + read-only econ DB. No store. No AF.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
Episodes: `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
DB: `workspace/economic_events.db` with `PRAGMA query_only=ON`.

## Suggested approach

Pin **before** looking at rates:

- **Event day:** episode `trading_date` equals the America/New_York calendar date of a US `importance='high'` release **or** a coupon/TIPS auction from the frozen E16 name list (`2/3/5/7/10/20/30-Year Note/Bond Auction`, `5/10/30-Year TIPS Auction`, `2-Year FRN Auction`).
- Schedule is known before the session — whole-day flag is causal.
- **Headline:** P(REVERSAL | event_day) − P(REVERSAL | non_event_day). Date-clustered bootstrap 400, seed 42.
- **n-floor:** ≥ 30 episodes each side.
- **Session cross-check (C015 / R003):** repeat the contrast within RTH and within non-RTH (same C015 ET buckets). If the pooled sign is confined to one session group or disappears inside both, treat as a TOD restatement (E07-style fail).
- Diagnostic only: macro-only vs auction-only vs both day counts. Do not open a second headline.

No AF, no event-window minutes, no C017 in this worker.

## Success criteria

- **Advance (descriptive):** |event − non-event| ≥ 5 pp, CI excludes 0, n-floor met, and the sign is not a TOD restatement.
- **PARK:** |lift| < 5 pp or CI includes 0 or n-floor fail or session-confined.
- Scoreboard caveat only if it advances. No event×AF iterate.
- `EXPLORATORY / NOT EVIDENCE`.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C016_event_day/` and this cycle folder. No live dashboards, D:, discovery expansion.

## Deliverables

- `RESULT.md` with E07-style session table.
- `meta.json` RESULTED with `event_minus_nonevent_reversal_pp`, `n_event`, `n_nonevent`.
- `metrics.json` + episode CSV.
