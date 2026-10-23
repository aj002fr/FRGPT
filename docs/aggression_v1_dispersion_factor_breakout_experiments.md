# V1 Dispersion Factors → Breakout vs ATR-Range Sweep

Offline grid over horizons, breakout bps, and ATR `k`, scoring value / direction /
speed / persistence / shape residual factors. Does **not** change V1.2 or live V1/V2/V3.

Artifacts: `workspace/analysis/aggression_v1_dispersion_factor_breakout/`  
Script: `scripts/aggression_v1_dispersion_factor_breakout_sweep.py`  
Run date: 2026-07-16

## Definitions


| Label               | Rule                                                                     |
| ------------------- | ------------------------------------------------------------------------ |
| **BREAKOUT**        | |net_h| ≥ **B** yield-bps (= `B × 2` TY ticks) and not event-near (±30m) |
| **CONTINUED_RANGE** | range_h ≤ **k × ATR_unit** and not BREAKOUT                              |
| **ATR_unit**        | prior-day RTH high–low (ticks) × √(h / 420)                              |
| **OTHER**           | neither                                                                  |


Primary headline config: **h=30, B=8, k=1.0**. Full grid kept in CSVs.

Universe: non-event bars only. Episodes de-clustered at 15m. Each factor is scored
against (a) BREAKOUT∪RANGE filtered baseline (`*_extreme`) and (b) all non-event
bars (`*_all`).

## Grid & mass


| Axis    | Values             |
| ------- | ------------------ |
| h       | 15, 30, 60         |
| B (bps) | 4, 6, 8, 12, 16    |
| k       | 0.5, 1.0, 1.5, 2.0 |


39 factors × 60 label configs × 4 score families → **18,840** journal rows
(discovery + validation). Every cell is retained in `candidate_journal.csv` /
`validation_all_raw.csv`.

### Which configs have usable breakout counts (validation)


| Config                 | n_breakout  | breakout share | Notes                      |
| ---------------------- | ----------- | -------------- | -------------------------- |
| 60m, B=4               | 936         | 7.1%           | Most mass                  |
| 30m, B=4               | 441         | 3.3%           | Usable                     |
| 60m, B=6               | 341         | 2.6%           | Usable                     |
| 15m, B=4               | 153         | 1.2%           | Thin                       |
| 30m, B=6               | 125         | 0.9%           | Thin                       |
| **30m, B=8 (primary)** | **55**      | **0.42%**      | Scarce                     |
| 30m/60m, B≥12          | ≤ few dozen | ≪1%            | Lifts mostly uninformative |


With `k=1.0` on primary, ~62% of non-event bars are CONTINUED_RANGE and breakouts
remain rare. At `k=0.5`, the filtered BREAKOUT∪RANGE set is much smaller and the
extreme-set breakout baseline rises (e.g. ~13% at 30m/B=4/k=0.5).

## Gate ladder (validation, all cells)

Flexible gates are browsing-only. Only **restrictive** (n≥50, ≥8 dates, ≥8pp lift,
one-sided lower CI > 0, BH-FDR) is the shipping bar.


| Gate            | PASS   | PROVISIONAL | FAIL     |
| --------------- | ------ | ----------- | -------- |
| very_loose      | 702    | 0           | 8718     |
| loose           | 124    | 35          | 9261     |
| moderate        | 70     | 30          | 9320     |
| standard        | 70     | 30          | 9320     |
| strict          | 24     | 13          | 9383     |
| **restrictive** | **18** | **19**      | **9383** |


## Primary `(30m, 8bps, k=1.0)`

Regime mix:


| Split      | BREAKOUT | CONTINUED_RANGE | OTHER | breakout share |
| ---------- | -------- | --------------- | ----- | -------------- |
| Discovery  | 78       | 19757           | 12152 | 0.23%          |
| Validation | 55       | 8168            | 4422  | 0.42%          |


**Restrictive gate on primary: 0 PASS / 157 FAIL.**

### Top breakout lifts (extreme-filtered baseline ≈ 0.67%)


| Factor           | n   | rate | lift    | one-sided lower | BH  |
| ---------------- | --- | ---- | ------- | --------------- | --- |
| large_persist_20 | 198 | 2.0% | +1.35pp | <0              | no  |
| z_ge_2           | 205 | 2.0% | +1.28pp | <0              | no  |
| large_persist_10 | 260 | 1.9% | +1.25pp | <0              | no  |
| zq80_100         | 220 | 1.8% | +1.15pp | <0              | no  |


All <2pp; CIs cross zero. Persistence / large z lean the right way but do not
clear any serious gate.

### Top continued-range lifts (all non-event baseline ≈ 61.8%)


| Factor              | n   | rate  | lift   | one-sided lower | BH  |
| ------------------- | --- | ----- | ------ | --------------- | --- |
| large_widen_persist | 76  | 71.1% | +9.2pp | <0              | no  |
| widening            | 354 | 66.9% | +5.1pp | <0              | no  |
| large_pos           | 427 | 65.6% | +3.8pp | <0              | no  |
| large_widening      | 247 | 64.8% | +3.0pp | <0              | no  |


Widening / large+widening again look RANGE-leaning on the broad baseline, but
validation CIs cross zero and BH fails. On the extreme-filtered set, range is
already ~~99%, so “range lifts” there are mechanical (~~0.7pp).

## Restrictive PASSes (all 18)

All restrictive PASSes are **BREAKOUT** cells at **B=4 only** (soft ~8-tick moves),
almost all at **k=0.5**, horizons 30 or 60:


| Config          | Factors that PASS                                                                                                |
| --------------- | ---------------------------------------------------------------------------------------------------------------- |
| 30m, B=4, k=0.5 | z_ge_1_5, fast_15m, age_ge_30, large_persist_10/20, same_sign_streak_hi                                          |
| 60m, B=4, k=0.5 | z_ge_0_5, fast_5m/15m, age_ge_30, large_persist_10/20, narrowing, large_narrowing, same_sign_streak_hi, zq80_100 |
| 60m, B=4, k=1.0 | age_ge_30, large_persist_20                                                                                      |


Theme: **persistent / fast / large residual** → higher chance of a *soft* 4bps
move inside the tight ATR-range filter. None are at primary 8bps. Several PASSes
have two-sided `ci_low` ≤ 0 while one-sided lower > 0 (gate uses one-sided).

**FINAL was not opened.** These PASSes do not authorize V1.2 or live changes.

## Stability (cross-config)

Most stable positive patterns (validation, count of configs with lift > 0):


| Factor           | Outcome         | Family           | +lift configs / 60 | median lift |
| ---------------- | --------------- | ---------------- | ------------------ | ----------- |
| widening         | CONTINUED_RANGE | range_all        | 60                 | +5.1pp      |
| large_pos        | CONTINUED_RANGE | range_all        | 60                 | +2.5pp      |
| large_widen_fast | CONTINUED_RANGE | range_all        | 60                 | +2.4pp      |
| fast_15m         | BREAKOUT        | breakout_extreme | 48                 | +0.5pp      |
| large_persist_20 | BREAKOUT        | breakout_extreme | 44                 | +1.1pp      |


Widening → range is the most consistent *direction*, but fails primary FDR/CI.
Breakout lifts concentrate at easy `B=4` / tight `k=0.5` and shrink toward zero
as B rises to 8–16.

## Verdict

1. **Primary (30m / 8bps / k=1): nothing useful.** Breakouts too rare; best
  persistence lifts ~1.3pp with failing CIs/FDR.
2. **Soft 4bps + tight ATR filter:** persistence/speed/large residual show
  restrictive PASS breakout lifts — exploratory only; not the headline definition.
3. **Widening → continued range** is the most stable lean across the grid, still
  not restrictive-PASS on primary.
4. Do **not** retune V1.2 caution scoring from this sweep. Keep FINAL locked.
  If revisited, pre-register a single `(h,B,k)` and factor family before looking
   at FINAL.

## Artifacts


| File                                                    | Contents                                           |
| ------------------------------------------------------- | -------------------------------------------------- |
| `regime_mix_grid.csv`                                   | Label rarity by `(h,B,k,split)`                    |
| `candidate_journal.csv`                                 | Every discovery + validation cell                  |
| `validation_all_raw.csv` / `validation_leaderboard.csv` | Validation cells + BH columns                      |
| `factor_stability.csv`                                  | Cross-config positive-lift counts                  |
| `gate_threshold_sweep/`                                 | Per-gate PASS/PROV/FAIL tables                     |
| `summary.json`                                          | Machine-readable headline + restrictive PASS list  |
| `split_manifest.json`                                   | Chronological discovery / validation / FINAL dates |


