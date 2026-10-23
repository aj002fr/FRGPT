# C011 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Computed frictionless **LIFOScore** per episode on the X1 75-date discovery prefix using pinned parameters from the brief (no deviations):

- Tick = 0.015625; **g = 2 ticks**, **a = 2 ticks**.
- Three fade entries (opposite episode `direction`), triggered when mid first reaches `t_star_mid + direction × k × tick` for k ∈ {1,2,3} after t* (`source_order > t_star_source_order`).
- Entry **wins** if mid moves `−direction × g` ticks from entry mid before `+direction × a` ticks.
- Horizon: 15 minutes wall-clock from `t_star_ts_event` (X1 primary H).
- `LIFOScore = n_win / n_triggered` (NaN if n_triggered = 0).
- Store: one day in RAM from `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`, filtered to `mid_change != 0` rows.
- Headline: mean LIFOScore(OSCILLATORY_UNRESOLVED) − mean LIFOScore(REVERSAL ∪ CONTINUATION), 400 date-clustered bootstrap reps, seed 42.
- n-floor: ≥30 finite-LIFOScore episodes per group. No AF features, no g/a retuning.

Implementation: `workspace/analysis/move_exhaustion_v2/loop/C011_lifoscore/run_c011.py`.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 through 2022-04-25 (75 dates, manifest `X1_20260813T143427Z`) |
| Episodes (raw) | 1,252 |
| After drop (UNRESOLVED_AMBIGUOUS + CENSORED_INCOMPLETE_HORIZON) | 1,244 (8 censored dropped; 0 ambiguous) |
| Finite LIFOScore — oscillatory | 323 / 390 |
| Finite LIFOScore — resolved | 685 / 854 (439 REVERSAL + 415 CONTINUATION) |
| holdout_opened | false |

## Findings

**Headline (n-floor met):** oscillatory mean LIFOScore exceeds resolved by **+0.169** (point estimate); bootstrap mean **+0.167**, 95% CI **[+0.123, +0.212]** — CI not entirely ≤ 0. Meets descriptive advance bar (≥ +0.10, CI upper > 0).

| Class | n (total) | n finite | share n_triggered≥1 | mean LIFOScore |
|-------|-----------|----------|---------------------|----------------|
| OSCILLATORY_UNRESOLVED | 390 | 323 | 82.8% | 0.704 |
| REVERSAL | 439 | 270 | 61.5% | 0.851 |
| CONTINUATION | 415 | 415 | 100.0% | 0.330 |
| Resolved (REV ∪ CONT) | 854 | 685 | 79.8% | 0.535 |

**Interpretation (descriptive only):** LIFOScore ranks oscillatory above the resolved **union**, driven mainly by very low continuation fade-win rates. Oscillatory is **below** reversal on mean LIFOScore (0.70 vs 0.85). Phase-4 scope: descriptor separates oscillatory from continuation-like paths; it does not cleanly separate oscillatory from reversal.

Worker recommend: **ADVANCE** (descriptive success bar met). Not evidentiary.

## Things tried that failed

- No alternative g/a grids or entry counts — brief forbids retuning.
- No AF / flow-state prediction of E[LIFOScore] — deferred to a later cycle if descriptor advances.
- No headline emitted under n-floor failure scenario (not applicable; floor passed).

## Leakage / confound check

- LIFOScore uses only mid path **after** t* within the preregistered 15-minute horizon; episode labels and t* fields come from frozen X1 labeled parquet (read-only).
- Store rows filtered post-t* by `source_order` and `ts_event` — no future data relative to each fade trigger within an entry's sub-path.
- Discovery dates only; holdout unopened.
- **Unchecked:** whether oscillatory-vs-resolved separation is robust to g/a choice (pinned exploratory values only); whether REVERSAL's higher trigger miss rate (38.5% zero-trigger) biases class means.

## Limitations & surprises

- **Surprise:** REVERSAL mean LIFOScore (0.85) exceeds oscillatory (0.70); only continuation (0.33) pulls resolved union below oscillatory.
- 67 oscillatory episodes (17%) never triggered any fade entry within horizon.
- 169 reversal episodes (38%) had zero triggers vs 0% for continuation.
- Single prefix, single (g,a) pin; exploratory multiplicity not controlled.
- `mid_change != 0` filter reduces store to price-change rows only (brief-allowed).

## Suggested next steps

- Advisor: decide whether ADVANCE means a follow-on cycle for AF → E[LIFOScore] or a decomposition cycle (osc vs REVERSAL vs CONTINUATION separately).
- Consider whether LIFOScore at pinned g/a is a useful third-class proxy or mainly a continuation discriminator.
- If iterating, preregister whether the target contrast is osc−resolved union vs osc−continuation only.
