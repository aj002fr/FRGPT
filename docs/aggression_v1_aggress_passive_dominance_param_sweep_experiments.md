# V1 Aggress/Passive Dominance Parameter Sweep

Offline staged expansion of lookbacks, dominance ratios, QUIET floors, forward
horizons, and RANGE/TREND cutoff packs under the locked **role/absorption**
definition (QUIET remains a separate 4th bucket).

Does **not** change live V1 or V1.2. Does **not** overwrite the baseline
dominance artifact dir.

Artifacts: `workspace/analysis/aggression_v1_aggress_passive_dominance_param_sweep/`  
Script: `scripts/aggression_v1_aggress_passive_dominance_param_sweep.py`  
Run date: 2026-07-17

## Axes (pre-registered)

| Axis | Grid |
| --- | --- |
| Lookback `w` | `{1, 3, 5, 10, 15, 30}` |
| Dominance ratio `d` | `{1.1, 1.25, 1.5, 2.0, 2.5, 3.0}` |
| QUIET floor `q` | `{0.25, 0.5, 0.75}` |
| Forward horizon `h` | `{5, 10, 15, 30, 60, 90}` |
| Cutoff pack | `frozen` / `strict` / `loose` |

Cutoff packs (discovery-only quantiles on path_eff / disp / abs_net):

| Pack | TREND qs | RANGE qs |
| --- | --- | --- |
| frozen | 0.70 / 0.70 / 0.60 (h=30 uses locked dispersion JSON) | 0.30 / 0.30 / 0.40 |
| strict | 0.80 / 0.80 / 0.70 | 0.20 / 0.20 / 0.30 |
| loose | 0.60 / 0.60 / 0.50 | 0.40 / 0.40 / 0.50 |

## Staging

1. **Stage 1** — geometry `w×d×q` at `h=30`, pack=`frozen`
2. **Stage 2** — full `h × pack` expansion (shipping board)
3. **Stage 3** — ATR CONTINUED_RANGE vs BREAKOUT diagnostic on top Stage-1 geometries (+ plan defaults)

Shipping gate: **restrictive** (`n≥50`, `≥8` dates, `≥8pp`, CI>0, BH). FINAL only if Stage-2 restrictive PASS.

## Scale

| Item | Value |
| --- | ---: |
| Clean dates / rows | 78 / 57,125 |
| Stage-1 journal rows | 1,728 |
| Stage-2 journal rows | 31,104 |
| Stage-3 ATR rows | 5,184 |
| Stage-2 validation primary cells | 11,664 |

## Gate ladder (validation)

| Board | Gate | PASS | PROV | FAIL |
| --- | --- | ---: | ---: | ---: |
| Stage 1 geometry | restrictive | **0** | 0 | 648 |
| Stage 1 geometry | moderate | 0 | 0 | 648 |
| Stage 1 geometry | loose | 12 | 0 | 636 |
| Stage 2 path | **restrictive** | **0** | 0 | 11,664 |
| Stage 2 path | strict | 0 | 0 | 11,664 |
| Stage 2 path | standard / moderate | 30 | 0 | 11,634 |
| Stage 2 path | loose | 268 | 0 | 11,396 |
| Stage 3 ATR | restrictive | **0** | 0 | 2,592 |

Standard/moderate Stage-2 PASSes are **browsing-only** (lift ~5–6.7pp with CI>0 but **no BH**, and none reach the 8pp strict/restrictive bar).

## Headline replication `(w=15, d=1.5, q=0.5, h=30, frozen)`

Matches the baseline dominance study (within noise):

| State | Outcome | n | lift | BH |
| --- | --- | ---: | ---: | --- |
| NEITHER | TREND | 635 | +2.35pp | fail |
| PASSIVE_DOM | TREND | 589 | +0.21pp | fail |
| others | — | — | ≤0 | fail |

## Best Stage-2 lifts (not shippable)

Top validation primary cells (~6.1–6.7pp, CI>0, **BH fail**, below 8pp):

- `w15_d3_q0.75_h10_loose` AGGRESS_DOM → RANGE (+6.65pp)
- `w30_d2_q0.75_h60_loose` PASSIVE_DOM → TREND (+6.11pp)
- `w1_d3_q*_h60_frozen` AGGRESS_DOM → TREND (+6.10pp)

Stage-1 geometry browsing best (moderate gate all FAIL): NEITHER→TREND at `w=1,d=2,q=0.5` (+4.47pp).

## Verdict

**FAIL / STOP for shipping.**

- Stage-2 **restrictive PASS = 0**; FINAL locked.
- Expanding bucket widths, dominance hardness, QUIET floors, horizons, and RANGE/TREND levels does **not** produce a BH-surviving ≥8pp RANGE/TREND edge.
- Standard/moderate PASSes are exploratory only — do **not** retune V1.2 from them.
- No V1.2 aggress/passive-dom badge without a future clean restrictive PASS and explicit approval.

## Guardrails

- Offline only; baseline dir `aggression_v1_aggress_passive_dominance/` untouched
- Role/absorption primary; magnitude-only not promoted
- Implied passive ≠ resting-order identity
