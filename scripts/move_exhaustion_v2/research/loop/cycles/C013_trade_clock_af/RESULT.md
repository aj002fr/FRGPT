# C013 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Trade-clock signed aggressive flow vs reversal on the X1 75-date discovery prefix.

- **Data:** `labeled_B8_x2.parquet` filtered to `dates_scored` in X1 manifest (2022-01-05 → 2022-04-25). Dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
- **Feature:** Last **100** `AGG_BUY`/`AGG_SELL` rows with `source_order < t_star_source_order`; `AF_trade = direction × sum(+size buy, −size sell)`; require ≥ **8** AGG trades before `t_star` (skipped: 0).
- **Binning:** Rank-first pooled terciles of `AF_trade` (T3 = highest trade-clock AF).
- **Headline:** P(REVERSAL|T3) − P(REVERSAL|T1); date-clustered bootstrap 400 reps, seed 42; n-floor ≥ 30 per tercile.
- No depth normalization, no LOB/price/wall clocks, no validation/holdout, no C014.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1244 |
| Episodes with valid AF_trade | 1244 |
| Skipped (< 8 AGG trades) | 0 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** T3−T1 reversal lift = **-2.17 pp** (point estimate).

| AF tercile | n | P(rev) | mean AF_trade |
|------------|---|--------|---------------|
| T1 | 415 | 34.5% | 193.5 |
| T2 | 414 | 39.1% | 800.7 |
| T3 | 415 | 32.3% | 2061.2 |

**Date-clustered bootstrap:** -2.04 pp [-8.5, +4.7]

| Gate | Result |
|------|--------|
| n-floor (≥ 30 per tercile) | **PASS** (T1=415, T2=414, T3=415) |
| T3−T1 ≥ 5 pp | **FAIL** (-2.17 pp) |
| CI not entirely ≤ 0 | **PASS** |

**Recommend PARK.**

## Things tried that failed

1. **Headline T3−T1 reversal lift ≥ 5 pp:** Point -2.17 pp — below advance threshold.
2. **Headline CI excluding zero from below:** CI upper > 0.
3. **n-floor per tercile:** PASS.

Did **not** try (deferred per brief): depth-normalized LOB-clock AF (C001), price-change or wall clocks, MFE matching, validation/holdout, C014.

## Leakage / confound check

- **Verified:** Store features use only `source_order < t_star_source_order`; dates match manifest `dates_scored`; holdout not opened; no validation/holdout dates.
- **Unchecked:** MFE/extension confound on AF_trade terciles; session-time-of-day; trade-count vs LOB-clock differences vs C001.

## Limitations & surprises

- Trade-clock AF uses AGG trade count, not LOB event count — not directly comparable to C001 LOB-clock AF.
- Skipped episodes (0) lack sufficient pre-`t_star` aggressive prints.
- Terciles are pooled across all MFE levels (no MFE control in this brief).

## Suggested next steps

- **PARK** per success bar (lift/CI/n-floor did not clear advance bar).
- If iterating: consider MFE-stratified terciles or compare trade-clock vs LOB-clock AF on the same episodes.
