# C017 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

X1 up vs down direction baseline on the 75-date prefix. Labels-only.

- Up = `direction == +1`, down = `direction == -1`.
- Headline: P(REVERSAL|up) − P(REVERSAL|down); bootstrap 400, seed 42.
- Session cross-check inside RTH and non-RTH.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1244 |
| Up / down | 599 / 645 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** up − down reversal = **+1.81 pp**.

| Group | n | P(rev) |
|-------|---|--------|
| Up | 599 | 36.2% |
| Down | 645 | 34.4% |

**Date-clustered bootstrap:** +1.82 pp [-4.0, +8.2]

**Session cross-check:**

| Session | n | up n / down n | up−down rev (pp) |
|---------|---|---------------|------------------|
| RTH | 531 | 252 / 279 | +3.1 |
| non-RTH | 713 | 347 / 366 | +1.1 |

TOD restatement: **NO**.

| Gate | Result |
|------|--------|
| n-floor | **PASS** |
| \|lift\| ≥ 5 pp | **FAIL** |
| CI excludes 0 | **FAIL** |
| Not a TOD restatement | **PASS** |

**Recommend PARK.**

## Things tried that failed

1. Absolute direction lift ≥ 5 pp: +1.81 pp.
2. CI excludes 0: no or blocked.
3. Session-independent sign: tod_restatement=False.

Did **not** try: AF×direction, event-day re-cut, C018.

## Leakage / confound check

- **Verified:** direction is on the frozen labeled parquet; dates match `dates_scored`; holdout closed.
- **Unchecked:** whether up/down occupancy differs by session.

## Limitations & surprises

- Direction is the barrier side, not a flow signal.

## Suggested next steps

- **PARK**. No direction×AF iterate on this prefix.
