# C001 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Loaded X1 `labeled_B8_x2.parquet` (primary B=8/x=2), dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`, restricted to the 75 `dates_scored` in `X1_20260813T143427Z/manifest.json` (2022-01-05 through 2022-04-25). For each episode, streamed that day's store parquet from `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`.

Feature construction (causal, per brief):

- Last **N=100** store rows with `source_order < t_star_source_order`, ordered by `(ts_event, sequence, source_order)`.
- Signed aggressive size: `AGG_BUY` → +size, `AGG_SELL` → −size; direction-aligned `AF_dir = direction × sum(signed)`.
- Depth at t*: `pre_bid_sz + pre_ask_sz` on the `t_star_source_order` row; `AF_depth = AF_dir / depth` (skip if depth ≤ 0).
- Matched on **mfe_ticks tercile** (within-sample); **AF_depth quintiles** computed within each tercile, pooled for headline.
- Headline: pooled continuation rate Q5 minus Q1; **date-clustered bootstrap** (400 resamples, seed 42).

No other N, normalizations, vol/TOD controls, or holdout data. Vectorized per-day store reads; one store day in memory at a time.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored (post-exclusion) | 1,244 |
| Episodes with valid AF_depth | 1,244 (0 skipped) |
| Episodes in quintile analysis | 1,244 |
| Q1 / Q5 tail n | 249 / 249 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** Q5−Q1 continuation lift = **−2.01 pp** (Q5 31.33% vs Q1 33.33%). Date-clustered 95% CI: **[−9.73, +6.78] pp** (bootstrap mean −1.66 pp). Interval spans zero and is not entirely above zero.

Compared to X1 prefix P(continuation) = **33.36%**, neither tail deviates materially from baseline.

Pooled quintile continuation rates:

| Quintile | n | P(CONTINUATION) |
|----------|---|-----------------|
| Q1 (low AF_depth) | 249 | 33.33% |
| Q2 | 249 | 32.13% |
| Q3 | 248 | 33.47% |
| Q4 | 249 | 36.55% |
| Q5 (high AF_depth) | 249 | 31.33% |

Pattern is **non-monotonic** (Q4 highest, Q5 lowest). Stronger depth-normalized confirming flow at t* does **not** raise continuation probability on this sample.

Against brief success criteria (≥5 pp lift, CI not entirely ≤0, tail n≥30): **does not advance** — lift is negative, CI crosses zero. Tail n is adequate (249 each).

## Things tried that failed

1. **Row-wise episode loop (first attempt):** `iterrows()` over ~1,244 episodes × 75 store loads; ran >7 min with no stdout, process killed. Too slow for the loop SLA.
2. **Bootstrap via `pd.concat` (5000 reps):** Concatenating full episode frames per bootstrap draw was memory/CPU-heavy; job interrupted before artifacts landed.
3. **Depth-normalized AF at N=100 (this cycle's hypothesis):** Negative Q5−Q1 (−2.0 pp) with wide CI including zero — no directional signal on the X1 prefix.

Did **not** try (deferred per brief): N∈{50,250}, alternate normalizations, vol/TOD/velocity controls.

## Leakage / confound check

- **Verified:** Features use only `source_order < t_star_source_order`; depth from t* row only for normalization denominator; store reads limited to manifest `dates_scored`; holdout not opened.
- **Unchecked:** Calendar-month concentration of the (weak) Q4 bump; interaction with direction or session segment; whether mfe_ticks tercile matching fully controls move-stretch confounding; OSCILLATORY_UNRESOLVED treated as non-continuation in rate denominators (same as X1 scored denominator).

## Limitations & surprises

- First runnable version required two infrastructure fixes (vectorization + count-based bootstrap); wall clock ~3–4 min for 75 store days.
- Surprise: Q4 (not Q5) shows the highest continuation rate (~36.5%), suggesting any weak pattern is not a clean high-AF_depth monotonic gradient.
- Single normalization and N; exploratory multiplicity not controlled.

## Suggested next steps

- Advisor: likely **PARK** or low-priority **ITERATE** — no ≥5 pp lift, negative point estimate.
- If iterating: test whether signal is direction-conditional or session-conditional before another normalization sweep (OQ-13).
- Do not promote without a fresh preregistered confirmatory contract and validation cohort.

**Artifacts:** `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/metrics.json`, `episodes_c001.csv`
