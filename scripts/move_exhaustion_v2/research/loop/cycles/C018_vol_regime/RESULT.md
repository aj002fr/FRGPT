# C018 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

X1 30-minute pre-t* mid range vs reversal on the 75-date prefix.

- Lookback: `source_order < t_star` and `ts_event >= t_star − 30m`.
- `range_ticks = (mid_max − mid_min) / 0.015625`. Rank-first terciles.
- Headline: P(REVERSAL|T3) − P(REVERSAL|T1); bootstrap 400, seed 42.
- Session cross-check inside RTH and non-RTH. No AF.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1244 |
| Finite range | 1244 |
| Skipped | 0 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** T3−T1 reversal = **+18.07 pp**.

| Vol tercile | n | P(rev) | mean range (ticks) |
|-------------|---|--------|--------------------|
| T1 | 415 | 28.0% | 6.92 |
| T2 | 414 | 31.9% | 8.96 |
| T3 | 415 | 46.0% | 17.20 |

**Date-clustered bootstrap:** +17.90 pp [+11.7, +24.5]

**Session cross-check:**

| Session | n | T1 n / T3 n | T3−T1 rev (pp) |
|---------|---|-------------|----------------|
| RTH | 531 | 136 / 232 | +7.5 |
| non-RTH | 713 | 279 / 183 | +23.5 |

TOD restatement: **NO**.

| Gate | Result |
|------|--------|
| n-floor | **PASS** |
| \|lift\| ≥ 5 pp | **PASS** |
| CI excludes 0 | **PASS** |
| Not a TOD restatement | **PASS** |

**Recommend ADVANCE.**

## Things tried that failed

1. Absolute vol tercile lift ≥ 5 pp: +18.07 pp.
2. CI excludes 0: yes.
3. Session-independent sign: tod_restatement=False.

Did **not** try: second window, AF×vol, RV-of-returns instead of range.

## Leakage / confound check

- **Verified:** only pre-t* store rows; dates match `dates_scored`; holdout closed.
- **Unchecked:** 30m range vs session occupancy (RTH may have wider ranges).

## Limitations & surprises

- Range, not sigma. B=8 is fixed so this is background vol, not move size.

## Suggested next steps

- **ADVANCE**. No vol×AF iterate on this prefix.
