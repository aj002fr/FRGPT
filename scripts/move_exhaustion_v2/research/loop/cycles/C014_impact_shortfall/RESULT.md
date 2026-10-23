# C014 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Walk-forward impact shortfall vs reversal on the X1 75-date discovery prefix, per approved BL-3 spec.

- **Data:** `labeled_B8_x2.parquet` filtered to `dates_scored` in X1 manifest (2022-01-05 → 2022-04-25). Dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
- **Window:** last **100** store rows with `source_order < t_star_source_order` (C001 sort/index).
- **Features:** `Δm_W = direction × (mid_last − mid_first) / 0.015625`; `AF_W = direction × signed AGG size` in the same window.
- **Model:** expanding OLS `Δm_W ~ AF_W`; first **20** dates train; train p1/p99 winsorize; `IS_raw = ŷ − Δm_W`; `IS = IS_raw / s` with `s` = train residual SD.
- **Headline:** rank-first IS terciles on OOS rows; P(REVERSAL|T3) − P(REVERSAL|T1); date-clustered bootstrap 400 reps, seed 42; n-floor ≥ 30 per IS tercile.
- **E07:** Pearson corr(IS, Δm_W); T3−T1 within each Δm_W tercile (pooled IS terciles).
- No second N, no % of B, no richer f(·), no validation/holdout, no C015.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1244 |
| Finite AF_W and Δm_W | 1244 |
| OOS episodes (finite IS) | 1062 |
| OOS dates | 55 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** T3−T1 reversal lift = **-11.30 pp** (point estimate).

| IS tercile | n | P(rev) | mean IS | mean Δm_W |
|------------|---|--------|---------|-----------|
| T1 | 354 | 42.4% | -0.829 | 0.65 |
| T2 | 354 | 35.9% | 0.279 | 0.00 |
| T3 | 354 | 31.1% | 0.474 | -0.03 |

**Date-clustered bootstrap:** -11.21 pp [-18.5, -4.0]

**OOS R² (Δm_W ~ ŷ):** +0.0075 (reported; not gated).

### E07 cross-check

- Pearson `corr(IS, Δm_W)` = **-0.957** — FLAG |r|>0.85.
- T3−T1 within Δm_W tercile (same pooled IS terciles):

| Δm tercile | n | IS T1 n / T3 n | T3−T1 rev (pp) |
|------------|---|----------------|----------------|
| T1 | 354 | 2 / 63 | -81.0 |
| T2 | 354 | 0 / 289 | +nan |
| T3 | 354 | 352 / 2 | +8.0 |

- Confined-sign: **NO — not confined to one Δm tercile**.

| Gate | Result |
|------|--------|
| n-floor (≥ 30 per IS tercile) | **PASS** |
| T3−T1 ≥ 5 pp | **FAIL** (-11.30 pp) |
| CI not entirely ≤ 0 | **FAIL** |
| E07 corr + not confined | **FAIL** |

**Recommend PARK.**

## Things tried that failed

1. **Headline T3−T1 reversal lift ≥ 5 pp:** Point -11.30 pp.
2. **Headline CI excluding zero from below:** CI-only noise or n-floor blocked bootstrap.
3. **E07 (corr and Δm control):** corr=-0.957; confined=False.
4. **n-floor per IS tercile:** PASS.

Did **not** try (deferred per spec): second N, 75% of B, PF/depth/spread/σ/TOD, raw P−F, validation/holdout, C015.

## Leakage / confound check

- **Verified:** features use only `source_order < t_star_source_order`; expanding walk-forward never fits on the test date; dates match manifest `dates_scored`; holdout not opened.
- **Unchecked:** whether residual SD from in-sample train IS_raw is the only reasonable s; session TOD; whether AF-only linear impact is misspecified enough to make IS noise.

## Limitations & surprises

- First 20 dates are train-only and do not enter the headline (OOS n < scored n).
- Low OOS R² is acceptable per X4/BL-3; IS only needs rank information.
- This cell is not confirmatory X4 evidence.

## Suggested next steps

- **PARK** per the approved spec success bar.
- Do not retune N, add covariates, or open a second checkpoint in this worker.
