# C005 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Loaded X1 `labeled_B8_x2.parquet`, dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`, restricted to the 75 `dates_scored` in `X1_20260813T143427Z/manifest.json` (2022-01-05 through 2022-04-25). Streamed each day's store parquet from `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` (one day in RAM at a time; all 75 store files present).

Feature construction (causal, per brief):

- Lookback: last **N=100** store rows with `source_order < t_star_source_order` (`ends = searchsorted(so, t_orders, side="left") - 1`, same indexing as C001/C004).
- Split lookback into **early/late halves by store-row count** (`n//2`).
- Per half: **AF** = `direction × sum(+size AGG_BUY, −size AGG_SELL)`.
- States (mutually exclusive, late-half decides flip):
  - **reversed:** `AF_late < 0`
  - **decelerating:** `AF_late ≥ 0` and `AF_late < AF_early`
  - **accelerating:** `AF_late ≥ 0` and `AF_late ≥ AF_early`
- Dropped episodes with **< 8 AGG trades** in the lookback window; reported skip count.
- Vectorized per day via cumsum/searchsorted; progress logged every ~10 days.

Analysis:

1. Headline: P(REVERSAL | reversed) − P(REVERSAL | accelerating); **date-clustered bootstrap** 400 reps (seed 42).
2. Secondary: n and P(REVERSAL) per state; checked whether decelerating sits between reversed and accelerating.
3. Optional: reversed−accelerating lift inside `mfe_ticks` terciles.
4. No earliness field on labeled parquet (no `progress` / bars-from-anchor column) — earliness skipped.

Did **not** fit a model, run alternate N, use β/impact, open validation/holdout, or start C006 (per brief).

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored (post-exclusion) | 1,244 |
| Episodes with valid state (≥8 AGG in lookback) | 842 |
| Skipped (<8 AGG trades or no lookback) | 402 |
| Missing store days | 0 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** reversed−accelerating reversal lift = **+64.39 pp** (reversed 100.0% vs accelerating 35.61%). Date-clustered 95% CI: **[+61.22, +67.87] pp** (bootstrap mean +64.40 pp). CI does not span zero — but **reversed state n = 3** (all three reversed episodes are REVERSAL outcomes). The headline contrast is **not interpretable** at this sample size.

**State table:**

| State | n | P(REVERSAL) |
|-------|---|-------------|
| accelerating | 688 | 35.61% |
| decelerating | 151 | 30.46% |
| reversed | 3 | 100.00% |

**Decelerating between reversed and accelerating?** **No.** Decelerating (30.5%) is **below** accelerating (35.6%); reversed (100%, n=3) is above both. The expected ordering (reversed > decelerating > accelerating) does **not** hold.

**mfe_ticks tercile control (reversed−accelerating lift):**

| Tercile | n | reversed n | accelerating n | lift (pp) |
|---------|---|------------|----------------|-----------|
| T1 (low mfe) | 281 | 2 | 225 | +32.4 |
| T2 | 280 | 1 | 229 | +69.4 |
| T3 (high mfe) | 281 | 0 | 234 | — (no reversed episodes) |

Sign flip episodes are **vanishingly rare** (0–2 per tercile); contrast is driven entirely by microscopic reversed counts, not a stable stratum-specific signal.

Against brief success bar (≥5 pp lift, CI not entirely ≤0, **n per state ≥ 30**, decelerating between states): **does not advance** — reversed n=3 fails the minimum-count bar; decelerating does not sit between the other two; the large headline lift is an artifact of 3/3 reversed outcomes. **Recommend PARK.**

## Things tried that failed

1. **Late-window flow sign flip as reversal marker (this cycle):** reversed state occurs in only **3 / 842** valid episodes (0.36%); headline +64 pp is 100% reversal on n=3 — not actionable.
2. **Deceleration as intermediate state:** P(REVERSAL | decelerating) = 30.5% is **lower** than accelerating (35.6%), not between reversed and accelerating — contradicts the hypothesized ordering.
3. **mfe_ticks tercile stability:** 0 reversed episodes in high-mfe tercile; 1–2 in low/mid — no interpretable stratum-specific contrast.
4. **Sparse lookback filter:** 402 / 1,244 scored episodes (32%) dropped for <8 AGG trades in the 100-row window, further thinning the reversed tail.

Did **not** try (deferred per brief): alternate lookback N, logistic/model, earliness (no column), validation/holdout.

## Leakage / confound check

- **Verified:** Features use only store rows with `source_order < t_star_source_order`; no post-t* events; store reads limited to manifest `dates_scored`; holdout not opened.
- **Unchecked:** Whether the 8-AGG minimum and 100-row window jointly suppress almost all sign-flip episodes; session/volatility conditioning; whether a longer lookback or lower AGG threshold would change state prevalence.

## Limitations & surprises

- **Surprise:** Late-window flow sign reversal (`AF_late < 0`) is **extremely rare** (n=3) when requiring ≥8 AGG trades in a 100-row lookback — the primary contrast is effectively untestable on this prefix.
- Decelerating flow (same sign, weaker) shows **lower** reversal rate than accelerating — opposite of a "weakening confirms exhaustion" story.
- 32% episode loss from AGG-count filter may bias the remaining sample toward high-activity windows.

## Suggested next steps

- Advisor: **PARK** BL-5 — reversed state underpowered (n=3), decelerating not intermediate, headline lift is a small-n artifact.
- If revisiting: consider whether the 8-AGG / N=100 gate is too strict for sign-flip detection, or whether continuous AF_late (not trichotomy) is more informative; any follow-up is a new cycle, not C006 per brief.
- Do not promote; holdout remains closed.

**Artifacts:** `workspace/analysis/move_exhaustion_v2/loop/C005_decel_vs_reversal/metrics.json`, `episodes_c005.csv`, `run_c005.py`
