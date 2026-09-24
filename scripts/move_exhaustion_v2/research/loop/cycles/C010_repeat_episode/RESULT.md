# C010 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Labels-only repeat-episode rank test on the X1 75-date discovery prefix.

- **Data:** `labeled_B8_x2.parquet` filtered to `dates_scored` in X1 manifest (2022-01-05 → 2022-04-25). Dropped `CENSORED_INCOMPLETE_HORIZON` (8 episodes); no `UNRESOLVED_AMBIGUOUS` present.
- **Ranking:** Within each `trading_date`, sort by `t_star_source_order`, tie-break `t_star_sequence`. Assign k=1 (first), k=2 (second), k≥3 (later pool).
- **Headline:** P(REVERSAL | k=2) − P(REVERSAL | k=1); date-clustered bootstrap 400 reps, seed 42; CI in percentage points.
- **Diagnostic:** P(REVERSAL | k≥3) − P(REVERSAL | k=1); P(OSCILLATORY_UNRESOLVED) by k (not a second headline).
- **n-floor:** ≥ 30 episodes in k=1 and k=2 (required to headline).
- No store, no AF/NR/β, no conditioning on prior-episode outcome, no validation/holdout, no C011.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes (after drop) | 1,244 |
| Dropped | CENSORED_INCOMPLETE_HORIZON: 8 |
| Episodes per date (median) | 16 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** Second episode of the day has a **small positive** reversal lift vs first, but below the 5 pp advance bar and with a wide CI spanning zero.

| k | n | P(rev) | P(osc) | P(cont) |
|---|-----|--------|--------|---------|
| k=1 (first) | 75 | 22.7% | 53.3% | 24.0% |
| k=2 (second) | 75 | 25.3% | 45.3% | 29.3% |
| k≥3 (later) | 1,094 | 36.8% | 28.9% | 34.3% |

Note: k=1 and k=2 each contribute exactly one episode per trading date (75 dates → n=75 for both).

**k=2 − k=1 reversal lift (point estimate):** +2.67 pp  
**Date-clustered bootstrap (400 reps, seed 42):** +2.20 pp [−10.7, +16.0]

| Gate | Result |
|------|--------|
| n-floor (≥ 30 in k=1 and k=2) | **PASS** (75 / 75) |
| k=2−k=1 ≥ 5 pp | **FAIL** (+2.67 pp) |
| CI not entirely ≤ 0 | **PASS** (CI high = +16.0 pp) |

**Recommend PARK.** Lift is positive but below 5 pp and the CI includes zero — CI-only noise on the headline contrast.

**Diagnostic (not headline):**

| Contrast | Point lift | Bootstrap 95% CI |
|----------|------------|------------------|
| k≥3 − k=1 reversal | +14.17 pp | [+2.59, +23.49] |
| P(osc) k=1 | 53.3% | — |
| P(osc) k=2 | 45.3% | — |
| P(osc) k≥3 | 28.9% | — |

Later episodes show substantially higher reversal and lower oscillatory rates than the first episode of the day. This pattern is descriptive only; k≥3 is not the preregistered headline and mixes third-through-Nth episodes.

## Things tried that failed

1. **Headline k=2 vs k=1 reversal lift ≥ 5 pp:** Point +2.67 pp; bootstrap mean +2.20 pp — below advance threshold.
2. **Headline CI excluding zero from below:** CI [−10.7, +16.0] spans zero — insufficient precision for a directional headline claim.
3. **Interpreting k≥3 − k=1 (+14 pp) as headline:** Deferred per brief (diagnostic only; pools heterogeneous later ranks).

Did **not** try (deferred per brief): conditioning on prior-episode outcome, flow/AF features, per-date episode-count weighting, validation/holdout, C011.

## Leakage / confound check

- **Verified:** Labels-only from frozen X1 parquet; dates match manifest `dates_scored`; no store read; no validation/holdout dates; holdout not opened; rank uses only episode timing columns (`t_star_source_order`, `t_star_sequence`), not outcomes.
- **Unchecked:** Whether first-episode oscillatory dominance (53%) reflects morning chop vs later resolved moves; session-time-of-day confound within date; episode-detection density driving both rank and outcome mix.

## Limitations & surprises

- **Small headline sample for k=1/k=2:** Exactly one first and one second episode per date (n=75 each) — n-floor passes but effective comparison is 75 paired contrasts, not hundreds of independent episodes.
- **Large k≥3 pool (n=1,094)** drives a strong diagnostic reversal lift vs k=1; mixing ranks 3…N may obscure whether the effect is specific to k=2.
- First episode of the day is oscillatory-heavy (53%) relative to later ranks — reversal lift for k=2 may partly reflect reduced oscillatory mass rather than a pure reversal shift.

## Suggested next steps

- **PARK** the BL-9 headline (k=2 vs k=1 reversal ≥ 5 pp not met; CI noise).
- If the advisor iterates: consider isolating k=3 only vs k=1, or time-of-day/session controls, before reparking BL-9 entirely.
- Do not promote to confirmatory; no FDR; no live/dashboard wiring.
