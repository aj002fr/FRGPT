# C007 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Nested B−A contrast on the X1 discovery prefix: does `AF_depth` improve **3-class** outcome prediction beyond `mfe_ticks`?

- **Data:** Reused C001 cache (`episodes_c001.csv`, 1,244 episodes) and joined `mfe_ticks` from `labeled_B8_x2.parquet` on `episode_id` (0 missing). No store re-read.
- **Target:** `outcome` ∈ {REVERSAL, CONTINUATION, OSCILLATORY_UNRESOLVED}. No leftover labels dropped (0 episodes outside the three classes).
- **Model A:** multinomial logistic `outcome ~ mfe_ticks`
- **Model B:** multinomial logistic `outcome ~ mfe_ticks + AF_depth`
- **Walk-forward:** expanding window by `trading_date`; first 20 dates (2022-01-05 → 2022-02-02) initial train; predict each of the remaining 55 dates. `sklearn.linear_model.LogisticRegression` (lbfgs, max_iter=1000; sklearn 1.8 default multinomial — no `multi_class` parameter).
- **Headline:** OOS multiclass ΔBrier = Brier(A) − Brier(B) (positive = B better). One-vs-rest ΔAUC per class (diagnostic). Date-clustered bootstrap 400 reps on pooled OOS rows, seed 42.
- **n-floor:** ≥ 30 OOS episodes per class.
- Did **not** add other features, model C/D, second horizon, validation/holdout, or C008.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates, per X1 manifest) |
| Episodes (3-class) | 1,244 |
| Dropped leftover labels | 0 |
| OOS episodes (55 dates) | 1,062 |
| OOS REVERSAL | 387 |
| OOS CONTINUATION | 367 |
| OOS OSCILLATORY_UNRESOLVED | 308 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** AF_depth adds **no lift** beyond MFE in the 3-class framing — consistent with C006's binary null.

| Metric | Value |
|--------|-------|
| Brier(A) multiclass | 0.4567 |
| Brier(B) multiclass | 0.4577 |
| **ΔBrier (A−B)** | **−0.00102** (B slightly worse) |
| ΔBrier 95% CI | [−0.00559, +0.00247] |
| n-floor | **PASS** (387 / 367 / 308 per class) |

**Diagnostic one-vs-rest ΔAUC (B−A, pp):**

| Class | ΔAUC (pp) | Bootstrap 95% CI (pp) |
|-------|-----------|------------------------|
| REVERSAL | −0.12 | [−0.28, +0.04] |
| CONTINUATION | +0.002 | [−0.04, +0.06] |
| OSCILLATORY_UNRESOLVED | −0.82 | [−1.46, −0.23] |

Against success bar (ΔBrier ≥ 0.005, CI not entirely ≤ 0, n-floor met, ≥1 class ΔAUC ≥ 2 pp):

- n-floor: **met**
- ΔBrier ≥ 0.005: **fail** (−0.00102)
- Any class ΔAUC ≥ 2 pp: **fail** (max +0.002 pp CONTINUATION)
- ΔBrier CI not entirely ≤ 0: **pass** (upper bound +0.00247 > 0) — negligible magnitude, B-centered near zero or worse

**Recommend PARK BL-6.** The one allowed 3-class iterate after C006's binary null does not rescue AF_depth.

## Things tried that failed

1. **3-class nested B−A multinomial (this cycle):** ΔBrier ≈ −0.001, all per-class ΔAUC < 2 pp — AF_depth does not improve multiclass calibration or ranking beyond `mfe_ticks`.
2. **C006 binary nested B−A (parent):** ΔBrier ≈ 0, ΔAUC ≈ 0 on reversal-vs-rest — same null in the collapsed framing.
3. **C001 quintile continuation contrast (prior):** Q5−Q1 continuation lift −2.0 pp — univariate AF_depth signal weak; nested B−A null was expected.

Did **not** try (deferred per brief): feature interactions, standardization, alternate link/regularization, validation/holdout, C008.

## Leakage / confound check

- **Verified:** Features (`mfe_ticks`, `AF_depth`) are pre-decision; walk-forward respects date ordering; no validation/holdout dates used; holdout not opened; no store re-read; dates match X1 `dates_scored` window.
- **Unchecked:** Session/volatility conditioning; whether AF_depth lookback in C001 differs subtly from MFE horizon; class imbalance effects on multiclass Brier vs per-class AUC.

## Limitations & surprises

- 3-class framing was the hypothesized rescue after C006's binary null — it did not change the conclusion.
- OSCILLATORY_UNRESOLVED shows the largest (negative) ΔAUC (−0.82 pp), suggesting AF_depth may slightly harm that class's ranking — but effect is small and not actionable.
- Multiclass Brier (~0.46) is on a different scale than C006's binary Brier (~0.19); comparisons across framings are not direct.

## Suggested next steps

- **PARK BL-6** (per success bar and R001 one-iterate cap).
- Do not open validation/holdout for this null exploratory result.
- Advisor may deprioritize depth-normalized AF for move-exhaustion outcome modeling unless a new mechanism is hypothesized outside this nested-B−A thread.
