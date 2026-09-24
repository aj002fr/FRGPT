# C002 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Loaded X1 `labeled_B8_x2.parquet` (primary B=8/x=2), dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`, restricted to the 75 `dates_scored` in `X1_20260813T143427Z/manifest.json` (2022-01-05 through 2022-04-25). Streamed each day's store parquet from `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` (one day in RAM at a time).

Feature construction (causal, per brief):

- Events with `source_order <= t_star_source_order` and `event_class` in {AGG_BUY, AGG_SELL}.
- Split up-to-t* AGG trades into **early/late halves** by trade count (`split = n // 2`); require ≥8 trades per half (≥16 total).
- Per half: `β = signed_mid_change_ticks / signed_aggressive_size` where signed mid change = `direction × (mid_end − mid_start) / 0.015625` and signed flow = `direction × sum(+size AGG_BUY, −size AGG_SELL)`; skip half if |flow| == 0.
- `ImpactDecay = β_late / β_early` if |β_early| > 0; winsorize at sample p1/p99.
- Terciles on winsorized ImpactDecay: **T1 = most decay** (lowest ratio), T3 = least decay (highest ratio).
- Headline: P(REVERSAL) in T1 minus T3; **date-clustered bootstrap** 400 reps (seed 42).
- **Placebo:** shuffle AGG trade order within the up-to-t* window before half split (seed 99); same tercile/bootstrap pipeline.

No clock-based splits, alternate AF normalization, vol/TOD controls, or holdout data. Vectorized per-day store reads with episode-level half logic.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored (post-exclusion) | 1,244 |
| Episodes with valid ImpactDecay | 1,221 (23 skipped: |β_early| == 0) |
| Episodes with valid placebo decay | 1,181 |
| T1 / T3 tail n | 407 / 407 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** T1−T3 reversal lift = **−1.97 pp** (T1 32.92% vs T3 34.89%). Date-clustered 95% CI: **[−8.78, +5.05] pp** (bootstrap mean −1.86 pp). Interval spans zero.

**Placebo:** shuffled-half T1−T3 reversal lift = **−10.12 pp** (bootstrap mean; 95% CI **[−15.93, −4.57] pp**). Placebo is **not** near zero and is larger in magnitude than the real lift — the tercile contrast on shuffled halves shows a systematic negative bias, undermining interpretability of the real contrast.

Mean winsorized ImpactDecay by outcome: REVERSAL **0.17** vs CONTINUATION **0.34** (lower ratio among reversals — more “decay” in mean level, but tercile contrast does not translate cleanly).

Pooled tercile reversal rates (non-monotonic):

| Tercile | n | P(REVERSAL) | mean winsorized decay |
|---------|---|-------------|------------------------|
| T1 (most decay) | 407 | 32.92% | −2.59 |
| T2 | 407 | 37.35% | 0.38 |
| T3 (least decay) | 407 | 34.89% | 2.94 |

Against brief success criteria (≥5 pp lift, CI not entirely ≤0, placebo |lift| < 2pp, tail n≥30): **does not advance** — lift is negative and small, CI crosses zero, placebo fails the near-zero check. Tail n is adequate (407 each).

## Things tried that failed

1. **Impact-decay tercile hypothesis (this cycle):** T1−T3 reversal lift −2.0 pp with CI including zero; direction opposite to “more decay → more reversal.”
2. **Shuffled-half placebo calibration:** Placebo lift −10.1 pp (CI entirely below zero) — not ~0; kills clean interpretation of the tercile contrast even if a positive real lift had appeared.
3. **Monotonic tercile gradient:** T2 highest reversal rate (37.3%), not T1 — pattern is non-monotonic.

Did **not** try (deferred per brief): clock-based halves, alternate AF normalization, retired 4-cell FDR family.

## Leakage / confound check

- **Verified:** Features use only `source_order <= t_star_source_order`; no post-t* events; store reads limited to manifest `dates_scored`; holdout not opened.
- **Unchecked:** Whether ratio-of-ratios (β_late/β_early) instability drives placebo bias; date concentration of T2 bump; direction/session conditioning; OSCILLATORY_UNRESOLVED in denominators (same as X1 scored set).

## Limitations & surprises

- 23 episodes skipped because β_early == 0 (near-zero early-half flow with non-zero mid move).
- Surprise: placebo substantially negative rather than ~0 — likely finite-sample ratio noise and tercile pooling artifact on shuffled halves.
- Ratio feature is heavy-tailed before winsorization; winsorized T1 mean is negative (−2.59) indicating many episodes with sign flip in β ratio.

## Suggested next steps

- Advisor: likely **PARK** — no ≥5 pp lift, placebo calibration failed, non-monotonic terciles.
- If iterating: consider signed difference (β_late − β_early) instead of ratio; or test decay only on episodes with same-sign β halves; fix placebo design (e.g. random half assignment without ratio).
- Do not promote without preregistered confirmatory contract and validation cohort.

**Artifacts:** `workspace/analysis/move_exhaustion_v2/loop/C002_impact_decay/metrics.json`, `episodes_c002.csv`
