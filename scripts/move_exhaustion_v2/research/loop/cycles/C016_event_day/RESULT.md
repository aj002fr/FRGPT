# C016 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

X1 event/non-event day baseline on the 75-date prefix. Labels + read-only econ DB.

- Event day = `trading_date` matches ET date of US high-importance release or coupon/TIPS/FRN auction (E16 name list).
- Headline: P(REVERSAL|event) − P(REVERSAL|non-event); bootstrap 400, seed 42.
- Session cross-check inside RTH and non-RTH (C015 buckets).
- No store, no AF, no minute-level event windows.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1244 |
| Event-day episodes / dates | 835 / 51 |
| Non-event episodes / dates | 409 / 24 |
| Macro-only / auction-only / both episodes | 401 / 209 / 225 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** event − non-event reversal = **+1.94 pp**.

| Group | n | P(rev) |
|-------|---|--------|
| Event day | 835 | 35.9% |
| Non-event day | 409 | 34.0% |

**Date-clustered bootstrap:** +2.11 pp [-3.6, +9.0]

**Session cross-check:**

| Session | n | event n / non n | event−non rev (pp) |
|---------|---|-----------------|--------------------|
| RTH | 531 | 365 / 166 | +3.0 |
| non-RTH | 713 | 470 / 243 | +0.6 |

TOD restatement: **NO**.

| Gate | Result |
|------|--------|
| n-floor | **PASS** |
| \|lift\| ≥ 5 pp | **FAIL** |
| CI excludes 0 | **FAIL** |
| Not a TOD restatement | **PASS** |

**Recommend PARK.**

## Things tried that failed

1. Absolute event-day lift ≥ 5 pp: +1.94 pp.
2. CI excludes 0: no or blocked.
3. Session-independent sign: tod_restatement=False.

Did **not** try: minute-level windows, AF×event, surprise sign, C017.

## Leakage / confound check

- **Verified:** schedule-known day flag; DB opened query_only; dates match `dates_scored`; holdout closed.
- **Unchecked:** early-close auction days; whether trading_date vs ET calendar mismatches overnight releases.

## Limitations & surprises

- Day flag, not a ±minutes event window.
- High-importance + auction union only.

## Suggested next steps

- **PARK**. No event×AF iterate on this prefix.
