# C006 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Nested B−A contrast on the X1 discovery prefix: does depth-normalized aggressive flow (`AF_depth`) improve reversal prediction beyond `mfe_ticks`?

- **Data:** Reused C001 cache (`episodes_c001.csv`, 1,244 resolved episodes) and joined `mfe_ticks` from `labeled_B8_x2.parquet` on `episode_id` (0 missing). No store re-read.
- **Target:** `y = 1{outcome == REVERSAL}` — binary on all scored resolved episodes (REVERSAL vs CONTINUATION + OSCILLATORY_UNRESOLVED). This is **not** the X7 3-class multinomial setup.
- **Model A:** logistic `y ~ mfe_ticks`
- **Model B:** logistic `y ~ mfe_ticks + AF_depth`
- **Walk-forward:** expanding window by `trading_date`; first 20 dates (2022-01-05 → 2022-02-02) initial train; predict each of the remaining 55 dates. `sklearn.linear_model.LogisticRegression` (lbfgs, max_iter=1000).
- **Headline:** OOS ΔBrier = Brier(A) − Brier(B) (positive = B better); ΔAUC = AUC(B) − AUC(A). Date-clustered bootstrap 400 reps on pooled OOS rows, seed 42.
- **n-floor (R001):** ≥ 30 OOS positives and ≥ 30 OOS negatives.
- Did **not** add NR, β, IS, passive flow, model C/D, second horizon, validation/holdout, or C007.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates, per X1 manifest) |
| Episodes (resolved, scored) | 1,244 |
| OOS episodes (55 dates) | 1,062 |
| OOS positives (REVERSAL) | 387 |
| OOS negatives (non-REVERSAL) | 675 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** AF_depth adds **no meaningful lift** beyond MFE.

| Metric | Value |
|--------|-------|
| Brier(A) | 0.18649 |
| Brier(B) | 0.18655 |
| **ΔBrier (A−B)** | **−0.00006** (B slightly worse) |
| ΔBrier 95% CI | [−0.00190, +0.00144] |
| AUC(A) | 0.7906 |
| AUC(B) | 0.7894 |
| **ΔAUC (B−A)** | **−0.12 pp** |
| ΔAUC 95% CI | [−0.81, +0.44] pp |
| n-floor | **PASS** (387 pos, 675 neg) |

Against success bar (ΔBrier ≥ 0.005, ΔAUC ≥ 2 pp, ΔBrier CI not entirely ≤ 0, n-floor met):

- n-floor: **met**
- ΔBrier ≥ 0.005: **fail** (−0.00006)
- ΔAUC ≥ 2 pp: **fail** (−0.12 pp)
- ΔBrier CI not entirely ≤ 0: **pass** (upper bound +0.00144 > 0) — but effect is negligible and centered near zero

**Recommend PARK.** Consistent with C001 diagnostic (Q5−Q1 continuation lift −2.0 pp): depth-normalized AF does not add predictive value for reversal beyond the price-path proxy.

## Things tried that failed

1. **Nested B−A logistic (this cycle):** ΔBrier ≈ 0, ΔAUC ≈ 0 — AF_depth does not improve OOS calibration or ranking beyond `mfe_ticks`.
2. **C001 quintile continuation contrast (prior):** Q5−Q1 continuation lift −2.0 pp — univariate AF_depth signal weak/wrong-signed for continuation; B−A near-zero was expected.
3. **Implicit hope that AF_depth would rescue MFE-only ceiling:** AUC(A) already 0.79 on OOS; B does not move the needle.

Did **not** try (deferred per brief): feature interactions, standardization, alternate link/regularization, 3-class X7 multinomial, validation/holdout, C007.

## Leakage / confound check

- **Verified:** Features (`mfe_ticks`, `AF_depth`) are pre-decision; walk-forward respects date ordering; no validation/holdout dates used; holdout not opened; no store re-read.
- **Unchecked:** Whether OSCILLATORY_UNRESOLVED lumped into the negative class biases AUC/Brier vs a 3-class framing; session/volatility conditioning; whether AF_depth computed in C001 with a different lookback than MFE horizon creates a subtle mismatch.

## Limitations & surprises

- **2-class-on-resolved vs X7 3-class:** This cycle collapses CONTINUATION and OSCILLATORY_UNRESOLVED into the negative class. X7's confirmatory contract uses nested **multinomial** logistic (reversal / continuation / oscillatory). A null B−A here does not preclude incremental information in a 3-class framing, but it does not support promoting AF_depth for reversal prediction on this prefix.
- MFE alone achieves AUC ≈ 0.79 OOS — a high bar for AF_depth to beat incrementally.
- ΔBrier CI spans zero with tiny magnitude; any positive upper bound is noise, not actionable lift.

## Suggested next steps

- **PARK** this thread for promotion (per success bar).
- Advisor may deprioritize BL-6 / PQ3 depth-normalized AF for reversal modeling unless a new mechanism (e.g. interaction with deceleration state from C005, or a 3-class reframe) is hypothesized — but C005 also parked on sparse reversed counts.
- Do not open validation/holdout for this null exploratory result.
