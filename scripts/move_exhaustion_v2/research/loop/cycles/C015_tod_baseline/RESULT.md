# C015 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

X1 TOD/session baseline on the 75-date discovery prefix. Labels-only.

- **Data:** `labeled_B8_x2.parquet` filtered to `dates_scored` (2022-01-05 → 2022-04-25). Dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
- **Clock:** `t_star_ts_event` → America/New_York. Buckets pinned in the brief (ASIA / LONDON / RTH / POST; OTHER dropped).
- **Headline:** P(REVERSAL|RTH) − P(REVERSAL|non-RTH); date-clustered bootstrap 400 reps, seed 42; n-floor ≥ 30 per side.
- No store, no AF/IS/NR, no event-day split, no C016.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1244 |
| RTH | 531 |
| non-RTH | 713 |
| OTHER dropped | 0 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** RTH − non-RTH reversal = **+10.39 pp**.

| Group | n | P(rev) |
|-------|---|--------|
| RTH | 531 | 41.2% |
| non-RTH | 713 | 30.9% |

**Date-clustered bootstrap:** +10.43 pp [+4.9, +16.0]

**Diagnostic buckets (not a second headline):**

| Bucket | n | P(rev) |
|--------|---|--------|
| ASIA | 334 | 27.8% |
| LONDON | 281 | 33.8% |
| RTH | 531 | 41.2% |
| POST | 98 | 32.7% |
| OTHER | 0 | n/a |

| Gate | Result |
|------|--------|
| n-floor (≥ 30 RTH and non-RTH) | **PASS** |
| \|RTH − non-RTH\| ≥ 5 pp | **PASS** (+10.39 pp) |
| CI excludes 0 | **PASS** |

**Recommend ADVANCE.**

## Things tried that failed

1. **Absolute session lift ≥ 5 pp:** Point +10.39 pp.
2. **Bootstrap CI excludes 0:** yes.
3. **n-floor:** PASS.

Did **not** try: hour terciles, event-days, AF×TOD, store features, validation/holdout, C016.

## Leakage / confound check

- **Verified:** buckets use only `t_star_ts_event` (known at decision time); dates match `dates_scored`; holdout not opened; no flow features.
- **Unchecked:** early-close sessions; whether POST is economically closer to RTH than to overnight.

## Limitations & surprises

- Four buckets are diagnostic; only RTH vs non-RTH is the headline.
- This is an X1 baseline caveat, not a tradable session signal.

## Suggested next steps

- **ADVANCE** per the brief. If ADVANCE: scoreboard caveat only — do not open AF×TOD on this prefix.
