# C012 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Cache-only join on the X1 75-date discovery prefix (no store, no LIFOScore retune, no NR/β):

1. **LIFOScore** from `loop/C011_lifoscore/episodes_c011.csv`.
2. **AF_depth** + `trading_date` from `loop/C001_depth_norm_flow_continuation/episodes_c001.csv`.
3. **mfe_ticks** from `experiments/X1_20260813T143427Z/labeled_B8_x2.parquet` on `episode_id`.
4. Kept rows with finite LIFOScore and finite AF_depth; restricted to manifest `dates_scored` (2022-01-05 through 2022-04-25).

Analysis (per brief, no deviations):

- **AF_depth terciles:** rank-first `qcut` on pooled sample (T3 = highest AF_depth).
- **MFE control:** rank-first `qcut` on pooled `mfe_ticks` (separate assignment).
- **Headline:** mean LIFOScore(T3 AF) − mean LIFOScore(T1 AF); **date-clustered bootstrap** 400 reps, seed 42.
- **MFE nested check:** repeat T3−T1 within each mfe tercile; assess whether sign is confined (C004 rule: material lift ≥ 0.05 in one bucket only).
- **n-floor:** ≥ 30 finite-LIFOScore episodes per AF tercile; if failed, no headline (floor met here).

Implementation: `workspace/analysis/move_exhaustion_v2/loop/C012_af_lifoscore/run_c012.py`.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes with finite LIFOScore + finite AF_depth | 1,008 |
| AF tercile n (finite LIFOScore each) | T1 336 / T2 336 / T3 336 |
| holdout_opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** high−low AF_depth LIFOScore lift = **−0.054** (T3 mean 0.571 vs T1 mean 0.625). Date-clustered 95% CI: **[−0.114, +0.011]** (bootstrap mean −0.052). CI upper marginally > 0 but point lift is **negative** — direction opposite the hypothesized “deeper flow → higher fade success” story.

| AF tercile | n (finite LIFOScore) | mean LIFOScore |
|------------|------------------------|----------------|
| T1 (lowest AF_depth) | 336 | 0.625 |
| T2 | 336 | 0.571 |
| T3 (highest AF_depth) | 336 | 0.571 |

Non-monotonic: T2 and T3 are nearly identical; the contrast is T1 vs the rest.

**MFE control (T3−T1 LIFOScore within mfe tercile):**

| mfe tercile | n | AF T1 n / T3 n | T3−T1 LIFOScore |
|-------------|---|----------------|-----------------|
| T1 (lowest mfe) | 336 | 134 / 101 | **−0.063** |
| T2 | 336 | 92 / 119 | **−0.043** |
| T3 (highest mfe) | 336 | 110 / 116 | **+0.037** |

Sign is **not confined** to one mfe bucket (two low/mid-mfe strata negative, high-mfe stratum weakly positive). However, the pooled headline is negative and far below the +0.10 advance bar.

Against brief success bar (T3−T1 ≥ +0.10, CI not entirely ≤ 0, n-floor met, sign not mfe-confined): **does not advance** — lift ≤ 0 (−0.054), well below +0.10. n-floor met (336 per AF tercile). **Recommend PARK BL-8.**

## Things tried that failed

1. **Pooled AF_depth → LIFOScore (this cycle):** T3−T1 = **−0.054**, CI **[−0.114, +0.011]** — negative point estimate; fails advance bar (need ≥ +0.10).
2. **Positive dose-response:** T3 AF_depth does not exceed T2; both sit ~0.571 vs T1 at 0.625 — no monotonic “deeper flow → higher LIFOScore” gradient.
3. **MFE-adjusted positive signal:** within high-mfe stratum only a weak +0.037 (below +0.10); low/mid mfe show negative contrasts — no robust positive signal after MFE control.

Did **not** try (deferred per brief): LIFOScore retune, NR/β features, validation/holdout, C013.

## Leakage / confound check

- **Verified:** Cache-only join; no event store; features (AF_depth, mfe_ticks) are pre-t* scalars from prior frozen runs; holdout not opened; discovery dates only.
- **Unchecked:** Joint AF×MFE interaction structure (sign flip across mfe strata); whether finite-LIFOScore filter (236 episodes dropped vs C011 scored set) biases AF tertiles; outcome-class mix shifts across AF bins.

## Limitations & surprises

- 236 episodes from C011 lack finite LIFOScore (n_triggered = 0); excluded before tertiling — may skew AF bins if missingness correlates with AF_depth.
- Surprise: **highest AF_depth episodes have lower LIFOScore** than low-AF episodes (opposite of a fade-success-at-exhaustion story); mid and high AF are flat.
- MFE stratum flip (negative in low/mid mfe, weak positive in high mfe) suggests interaction rather than a clean main effect.

## Suggested next steps

- Advisor: **PARK BL-8** — lift ≤ 0, below +0.10 bar; CI spans zero; no actionable positive AF→LIFOScore gradient after MFE control.
- If revisiting flow→fade: consider explicit AF×MFE interaction or outcome-conditional LIFOScore (C011 showed class-specific means); do not iterate without new hypothesis.
