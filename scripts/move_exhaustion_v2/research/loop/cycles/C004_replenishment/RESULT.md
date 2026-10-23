# C004 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Loaded X1 `labeled_B8_x2.parquet`, dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`, restricted to the 75 `dates_scored` in `X1_20260813T143427Z/manifest.json` (2022-01-05 through 2022-04-25). Streamed each day's store parquet from `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` (one day in RAM at a time; all 75 store files present).

Feature construction (causal, per brief):

- Lookback: last **N=100** store rows with `source_order < t_star_source_order` (`ends = searchsorted(so, t_orders, side="left") - 1`, same indexing as C001).
- **AF_dir** = `direction × sum(+size AGG_BUY, −size AGG_SELL)` over lookback.
- **NR_opp** (opposing-side L1 replenishment): if `direction==+1`, `sum(size|ADD_ASK) − sum(size|CXL_ASK)`; if `direction==−1`, `sum(size|ADD_BID) − sum(size|CXL_BID)`. Other event classes ignored.
- Vectorized per day via cumsum/searchsorted; progress logged every ~10 days.

Analysis:

1. **AF_dir terciles** on the full scored sample (rank-first `qcut`, 3 bins).
2. Within each AF tercile, **NR_opp terciles** (T1 = lowest NR_opp, T3 = highest).
3. Headline: pooled P(REVERSAL|T3) − P(REVERSAL|T1) on NR_opp; **date-clustered bootstrap** 400 reps (seed 42).
4. Nested check: T3−T1 reversal lift within each AF tercile (sign only; not a model).

Did **not** run RR, alternate N, logistic, placebo, validation, or holdout (per brief).

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored (post-exclusion) | 1,244 |
| Episodes with valid AF_dir + NR_opp | 1,244 |
| Missing store days | 0 |
| NR T1 / T3 tail n (pooled) | 414 / 416 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** high−low NR_opp reversal lift = **+3.19 pp** (T3 39.18% vs T1 35.99%). Date-clustered 95% CI: **[−2.73, +9.47] pp** (bootstrap mean +3.28 pp). Interval spans zero.

Pooled NR tercile reversal rates:

| NR tercile | n | P(REVERSAL) |
|------------|---|-------------|
| T1 (lowest NR_opp) | 414 | 35.99% |
| T2 | 414 | 30.68% |
| T3 (highest NR_opp) | 416 | 39.18% |

**Nested check (within AF tercile, T3−T1 NR_opp reversal lift):**

| AF tercile | NR T1 n / T3 n | T3−T1 lift (pp) |
|------------|----------------|-----------------|
| T1 (lowest AF_dir) | 138 / 139 | **+0.42** |
| T2 | 138 / 138 | **+1.45** |
| T3 (highest AF_dir) | 138 / 139 | **+7.69** |

The pooled +3.2 pp lift is **driven almost entirely by the high-AF tercile** (+7.7 pp); low- and mid-AF strata show near-null contrasts (+0.4 pp, +1.5 pp). T2 reversal rate (30.7%) is **below** both tails — non-monotonic, suggesting noise rather than a clean dose-response.

Against brief success bar (≥5 pp lift, CI not entirely ≤0, tail n≥30, sign not confined to one AF tercile): **does not advance** — lift below 5 pp, CI crosses zero, and the economically meaningful sign is confined to AF tercile T3. Tail n is adequate (414/416). **Recommend PARK.**

## Things tried that failed

1. **Opposing-side L1 replenishment after AF matching (this cycle):** pooled T3−T1 reversal lift +3.2 pp with CI **[−2.73, +9.47]** — below the 5 pp advance bar and interval spans zero.
2. **Uniform signal across AF strata (nested check):** only high-AF episodes (T3 AF tercile) show a material +7.7 pp lift; low/mid AF contrasts are +0.4 pp and +1.5 pp — replenishment signal does not generalize after AF matching.
3. **Monotonic NR dose-response:** mid-tercile (T2) reversal rate (30.7%) is lowest, not intermediate — inconsistent with a simple replenishment→reversal gradient.

Did **not** try (deferred per brief): depth-normalized NR, alternate lookback N, logistic/RR, placebo, validation/holdout.

## Leakage / confound check

- **Verified:** Features use only store rows with `source_order < t_star_source_order`; no post-t* events; store reads limited to manifest `dates_scored`; holdout not opened.
- **Unchecked:** Whether high-AF concentration reflects a joint "exhaustion + replenishment" interaction rather than replenishment alone; session/volatility conditioning; L1 proxy vs true depth dynamics.

## Limitations & surprises

- **A3:** L1 replenishment (ADD/CXL at best bid/ask) is a **proxy** for true opposing-side depth replenishment; **MBP-10 unavailable** — cannot observe deeper book refill or queue position.
- Surprise: effect is large only when aggressive flow is already high (AF T3), suggesting replenishment may tag an interaction state rather than a standalone reversal predictor.
- Non-monotonic T2 reversal rate weakens a simple "more replenishment → more reversal" story.

## Suggested next steps

- Advisor: **PARK** BL-4 — lift < 5 pp, CI-only noise, signal confined to high-AF stratum.
- If revisiting replenishment: consider explicit AF×NR interaction framing (not nested terciles), or depth-normalized replenishment rate; requires MBP-10 or better depth data for confirmatory claims.
- Do not promote without preregistered confirmatory contract; holdout remains closed.

**Artifacts:** `workspace/analysis/move_exhaustion_v2/loop/C004_replenishment/metrics.json`, `episodes_c004.csv`, `run_c004.py`
