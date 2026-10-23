# C003 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Loaded X1 `labeled_B8_x2.parquet` (primary B=8/x=2), dropped `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`, restricted to the 75 `dates_scored` in `X1_20260813T143427Z/manifest.json` (2022-01-05 through 2022-04-25). Streamed each day's store parquet from `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` (one day in RAM at a time).

Feature construction (causal, per brief; parent C002 half-split reused):

- Events with `source_order <= t_star_source_order` and `event_class` in {AGG_BUY, AGG_SELL}.
- Split up-to-t* AGG trades into **early/late halves** by trade count (`split = n // 2`); require ≥8 trades per half (≥16 total).
- Per half: `β = signed_mid_change_ticks / signed_aggressive_size` where signed mid change = `direction × (mid_end − mid_start) / 0.015625` and signed flow = `direction × sum(+size AGG_BUY, −size AGG_SELL)`; skip half if |flow| == 0.
- **Headline feature:** `ImpactDecay_diff = β_late − β_early` (not the C002 ratio); winsorize at sample p1/p99.
- Terciles on winsorized diff: **T1 = most decay** (most negative diff), T3 = least decay (most positive diff).
- Headline: P(REVERSAL) in T1 minus T3; **date-clustered bootstrap** 400 reps (seed 42).
- **Placebo:** shuffle AGG trade order within the up-to-t* window before half split (seed 99); same tercile/bootstrap pipeline.

No clock-based splits, alternate AF normalization, vol/TOD controls, ratio feature, or holdout data. Vectorized per-day store reads with episode-level half logic.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored (post-exclusion) | 1,244 |
| Episodes with valid ImpactDecay_diff | 1,244 (0 skipped) |
| Episodes with valid placebo diff | 1,244 |
| T1 / T3 tail n | 415 / 415 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** T1−T3 reversal lift = **+2.65 pp** (T1 36.14% vs T3 33.49%). Date-clustered 95% CI: **[−3.48, +8.74] pp** (bootstrap mean +2.74 pp). Interval spans zero.

**Placebo:** shuffled-half T1−T3 reversal lift = **+2.87 pp** (95% CI **[−2.53, +8.78] pp**). Placebo is **not** near zero (|lift| = 2.87 pp > 2 pp threshold) — tercile contrast on shuffled halves again shows systematic positive bias comparable to the real lift, undermining interpretability.

Mean winsorized ImpactDecay_diff by outcome: REVERSAL **−0.0018** vs CONTINUATION **−0.0011** (slightly more negative among reversals — directionally consistent with decay predicting reversal, but effect is tiny in mean level).

Pooled tercile reversal rates (T1 > T2 > T3, monotonic):

| Tercile | n | P(REVERSAL) | mean winsorized diff |
|---------|---|-------------|----------------------|
| T1 (most decay) | 415 | 36.14% | −0.0107 |
| T2 | 414 | 36.23% | −0.0006 |
| T3 (least decay) | 415 | 33.49% | +0.0078 |

Against brief success criteria (≥5 pp lift, CI not entirely ≤0, placebo |lift| < 2pp, tail n≥30): **does not advance** — lift is positive but below 5 pp, CI crosses zero, placebo fails the near-zero check again. Tail n is adequate (415 each). **Recommend PARK** on BL-2 thread per brief (placebo fails again).

Compared to parent C002 (ratio): diff feature flips sign to +2.7 pp (vs −2.0 pp ratio) and retains all 1,244 episodes (no β_early==0 skips), but placebo calibration remains broken.

## Things tried that failed

1. **Impact-decay-diff tercile hypothesis (this cycle):** T1−T3 reversal lift +2.7 pp with CI including zero — below the 5 pp advance bar.
2. **Shuffled-half placebo calibration (again):** Placebo lift +2.9 pp — not ~0; real and placebo lifts are nearly identical, so the tercile contrast cannot be attributed to causal decay structure.
3. **BL-2 thread iteration from ratio to diff:** Direction improved vs C002 but placebo failure persists; no confirmatory signal.

Did **not** try (deferred per brief): clock-based halves, alternate AF normalization, same-sign-β filtering, holdout, C004.

## Leakage / confound check

- **Verified:** Features use only `source_order <= t_star_source_order`; no post-t* events; store reads limited to manifest `dates_scored`; holdout not opened.
- **Unchecked:** Why shuffled-half placebo tracks real lift (~+2.7–2.9 pp) for both ratio and diff; whether tercile pooling on finite halves induces spurious monotonicity; direction/session conditioning.

## Limitations & surprises

- Zero episodes skipped for diff (vs 23 in C002 for β_early==0) — difference is more defined but placebo problem remains.
- Surprise: placebo lift is **positive** (+2.9 pp) for diff vs **negative** (−10.1 pp) for ratio — sign flips but magnitude still fails calibration; real and placebo are statistically indistinguishable.
- T2 reversal rate (36.23%) nearly equals T1 (36.14%) — monotonicity is weak at the top.

## Suggested next steps

- Advisor: **PARK** BL-2 — second placebo failure on the same half-split design; real lift < 5 pp and CI spans zero.
- If revisiting decay: redesign placebo (e.g. random half assignment without order-dependent terciles) or abandon tercile framing; do not iterate to C004 without advisor brief.
- Do not promote without preregistered confirmatory contract and validation cohort.

**Artifacts:** `workspace/analysis/move_exhaustion_v2/loop/C003_impact_decay_diff/metrics.json`, `episodes_c003.csv`
