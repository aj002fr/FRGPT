# V1 Aggress vs Passive Dominance → RANGE / TREND

Offline role/absorption study: after aggress-implied vs residual/passive
dominance states, is the next 15–60m more often **RANGE** or **TREND**
(path-efficiency)?

Does **not** change live V1 or V1.2 scoring.

Artifacts: `workspace/analysis/aggression_v1_aggress_passive_dominance/`  
Script: `scripts/aggression_v1_aggress_passive_dominance_research.py`  
Run date: 2026-07-17

## Definitions

Decomposition (causal prior-day β):

\[
\Delta P \approx \underbrace{\beta\,\Delta A}_{C^A} + \underbrace{\Delta R}_{C^P}
\]

Implied passive only — not resting-order identity.

| State | Rule (role / absorption) |
| --- | --- |
| **QUIET** | \(\max(\|z^A\|,\|z^P\|) < 0.5\) — separate 4th bucket; excluded from main 3-way rates |
| **AGGRESS_DOM** | \(\mathrm{sign}(z^A)=\mathrm{sign}(\Delta P_w)\) and \(\|z^A\| \ge d\,\|z^P\|\) |
| **PASSIVE_DOM** | \(\|z^P\| \ge d\,\|z^A\|\) and (fight \(z^P\) vs \(z^A\), or residual same-sign with \(\Delta P\)) |
| **NEITHER** | Activity floor met; neither role rule fires |

Windows \(w \in \{1,5,15\}\); dominance \(d \in \{1.25,1.5,2.0\}\); headline **\(w=15\), \(d=1.5\)**.

**Primary outcomes:** path-efficiency `RANGE` / `TREND` (frozen h=30 cutoffs from
`aggression_v1_flow_price_dispersion/regime_cutoffs.json`; h=15/60 learned on discovery).

**Secondary (diagnostic only):** ATR CONTINUED_RANGE vs yield-bps BREAKOUT on headline
`(w,d)` only.

Universe: non-event (±30m); episodes de-clustered at 15m within state.

## Scale

| Item | Value |
| --- | ---: |
| Clean dates / rows | 78 / 57,125 |
| Splits (disc / val / FINAL) | 43 / 19 / 16 |
| Path journal rows | 432 |
| ATR diagnostic rows | 432 |
| Validation primary 3-way cells | 162 |

## State mix (validation, headline w=15, d=1.5)

| State | n_rows | share |
| --- | ---: | ---: |
| AGGRESS_DOM | 3,177 | 24.0% |
| PASSIVE_DOM | 3,666 | 27.7% |
| NEITHER | 3,981 | 30.1% |
| QUIET | 2,391 | 18.1% |

## Gate ladder (path primary 3-way, validation)

| Gate | PASS | PROVISIONAL | FAIL |
| --- | ---: | ---: | ---: |
| very_loose | 19 | 0 | 143 |
| loose | 6 | 0 | 156 |
| moderate | 0 | 0 | 162 |
| standard | 0 | 0 | 162 |
| strict | 0 | 0 | 162 |
| **restrictive** | **0** | **0** | **162** |

ATR diagnostic board: **0** restrictive / standard / loose PASS (12 very_loose only).

## Primary headline `(w=15, d=1.5, h=30)` validation

Non-event baselines ≈ RANGE 31.3%, TREND 26.8%.

| State | Outcome | n | lift | BH |
| --- | --- | ---: | ---: | --- |
| NEITHER | TREND | 635 | +2.35pp | fail |
| PASSIVE_DOM | TREND | 589 | +0.21pp | fail |
| NEITHER | RANGE | 635 | −0.10pp | fail |
| AGGRESS_DOM | RANGE | 502 | −0.20pp | fail |
| AGGRESS_DOM | TREND | 502 | −0.29pp | fail |
| PASSIVE_DOM | RANGE | 589 | −0.55pp | fail |

Best headline cell (NEITHER→TREND) has one-sided lower CI below zero.

QUIET (diagnostic row): RANGE +0.79pp / TREND −1.89pp (n=474) — not a shipping claim.

## Grid notes (not shipping)

Best path validation lifts are ~3–4.6pp (e.g. `w1_d2.0_h60_AGGRESS_DOM_TREND`
+4.59pp with CI>0) but **none** clear moderate+ lift (≥5pp) or BH-FDR. Loose
PASSes are browsing-only.

## Verdict

**FAIL / STOP for shipping.**

- Primary restrictive PASS count: **0**; FINAL locked.
- Aggress-dom / passive-dom / neither do **not** cleanly forecast forward RANGE vs TREND
  at the pre-registered headline.
- ATR diagnostic also fails restrictive.
- Do **not** add aggress/passive dominance badges to V1.2 or live V1 without a future
  clean restrictive PASS and explicit approval.

## Guardrails

- Offline only; write only under the artifact dir above.
- Implied-passive labeling — not true book passive.
- Magnitude-only dominance was not the primary definition (role/absorption locked).
