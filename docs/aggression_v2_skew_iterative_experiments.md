# Iterative TY Skew/Vol Hypothesis Research Cycle

## Executive Read

Five read-only hypothesis/test/learn iterations were run. Thresholds were
adapted only on research dates. The latest four shock-active dates were held
untouched as FINAL.

**No candidate cleared validation coverage, date stability and FDR. FINAL
outcomes were not inspected.**

This is the central result: changing thresholds can produce attractive point
estimates, but none is supported by enough independent validation dates to be
meaningful.

## Data Split

- 18 option-active dates.
- Discovery: 9 dates.
- Validation: 5 dates.
- Untouched FINAL: 4 dates (2026-07-07, 07-08, 07-09, 07-13).

No post-final tuning occurred.

## Iteration 1 — Shock Taxonomy / Causality

Grid: 81 combinations of:

- formation 5/15/30m;
- causal z 1.0/1.5/2.0;
- cooldown 15/30/60m;
- price-quiet p40/p50/p60.

Selected taxonomy (coverage only; no outcome used):

- formation: 5m;
- z: 2.0;
- cooldown: 15m;
- price-quiet threshold: p40.

Research coverage:

- 138 shocks across 10 dates.
- 111 price-quiet shocks across 10 dates.
- 27 price-led shocks.

Learning: a shorter formation/high-z definition materially increases
price-quiet event counts, but event count alone says nothing about explanatory
or trading value.

## Iteration 2 — Magnitude / Range Delivery

864 validation tests across arms, horizons, magnitude/range/RV/4-tick
outcomes and all/top20/top10 shock sizes.

No candidate satisfies coverage/FDR:

- The only BH-significant row was CALL_IV_UP 60m absolute-move lift (+0.8
  ticks), but n=5 across 2 dates.
- Large 8h/24h effects came from single observations.
- Better-covered rows had wide CIs and BH-adjusted p near 1.

Learning: stronger thresholds/top quantiles amplify single-event results rather
than create date-stable magnitude/range effects.

## Iteration 3 — Trend Versus Range / Persistence

360 tests using 15/30/60m option persistence states (persistent, partial,
reverted) against path efficiency, trend/chop, stay-in-range and delivered
range.

Best validation diagnostic:

- 30m partial persistence -> +2.41 ticks 120m range lift;
- n=13 across 5 dates;
- CI [-0.02, +4.43], BH-adjusted p=1.

Other persistent/reverted range hints also crossed zero and failed FDR.

Learning: persistence does not reliably separate unidirectional TY delivery
from wide chop in current data.

## Iteration 4 — Directional Asymmetry

88 expected/contrarian direction tests at 5/15/30/60m.

Raw hints:

- RR50_UP contrarian at 30m: +2.17 ticks versus controls, n=9/4 dates, CI
  [+0.21,+5.0], BH-adjusted p=0.70.
- CALL_IV_UP contrarian at 60m: +3.28 ticks, n=5/2 dates, CI crosses zero.
- SKEW_INDEX_DOWN expected at 60m: +1.27 ticks, n=9/4 dates, CI crosses zero.

No direction cell survives FDR or minimum coverage.

Learning: there are weak contrarian-looking RR/call-skew patterns, but
threshold iteration does not make them reliable.

## Iteration 5 — Parsimonious Context

48 one-condition tests: RTH, residual stretch, magnitude state and option
persistence.

Raw hints:

- Residual-stretched context, 120m signed lift: +2.16 ticks, n=13/4 dates,
  CI crosses zero.
- Persistent context, 120m signed lift: +1.33 ticks, n=15/5 dates, CI nearly
  touches zero.
- Residual context, 60m signed lift: +0.8 ticks with positive raw CI, but
  BH-adjusted p approximately 1 and n=13/4 dates.

No simple context passes FDR or n/date guards.

Learning: adding one context filter reduces sample faster than it stabilizes
the effect.

## Candidate Leaderboard / FINAL

- 1,360 validation candidate rows retained in `candidate_leaderboard.csv`.
- Eligible candidates: 0.
- `locked_final_results.csv` is intentionally empty.
- FINAL outcomes were not evaluated because no candidate met:
  - n>=30;
  - >=8 validation dates;
  - positive CI;
  - BH-FDR significance.

This avoids selecting a threshold simply because it looked best on a tiny
sample.

## Overall Interpretation

What the iterative cycle supports:

1. Most apparent skew/vol effects are highly threshold-sensitive.
2. Stronger/top-quantile filters create eye-catching but single-event results.
3. No robust magnitude/range, trend/range or direction explanation emerges.
4. Price-quiet filtering improves causal interpretation but leaves too few
   independent dates.
5. Option shocks often mean-revert, but persistence does not yet explain TY
   path shape reliably.

Meaningful conclusion:

> Current skew/vol history is insufficient to identify a stable TY futures
> signal or regime explanation. The correct action is to keep logging, not
> continue threshold tuning on the same dates.

## Artifacts

- `workspace/analysis/aggression_v2_skew_iterative/research_panel.csv`
- `split_manifest.json`
- `iteration_01_taxonomy.csv/json`
- `iteration_02_magnitude.csv/json`
- `iteration_03_regime.csv/json`
- `iteration_04_direction.csv/json`
- `iteration_05_context.csv/json`
- `iteration_journal.jsonl`
- `candidate_leaderboard.csv`
- `locked_final_results.csv`
- `iterative_skew_research_summary.json`
- `iteration_effects.png`
