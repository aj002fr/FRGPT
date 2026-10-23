# Move Exhaustion V2 — Research Backlog

Advisor-owned prioritized idea queue for the autoresearch loop. Seeded
2026-08-11 from the retired X2–X10 contracts (science preserved verbatim in
`experiments/X*/CONTRACT.md`) and the original research spec. Every change to
this file gets a journal entry (`event: backlog`).

Entry states: `QUEUED` / `IN_CYCLES (C###…)` / `PARKED (reason)` / `PROMOTED (contract)` / `KILLED (reason)`.

**Window constraint (2026-08-13, human):** stay on the X1 75-date prefix. Do not expand discovery.

---

## Seeded from the retired ladder

| ID | State | Idea (one line) | Provenance |
|----|-------|-----------------|------------|
| BL-1 | PARKED (C001: Q5−Q1 continuation −2.0pp, CI crosses 0) | Does depth-normalized aggressive flow at t* raise continuation probability within matched price-move buckets? | retired X2 |
| BL-2 | PARKED (C002 ratio + C003 diff: both placebo-failed) | Does declining price impact per unit flow (β early→late, ImpactDecay) predict reversal? Includes shuffled-thirds placebo and the up-to-t*-only pitfall guard | retired X3 (Priority Q1) |
| BL-3 | PARKED (C014: T3−T1 −11.3pp; corr(IS,Δm)=−0.96 E07 fail) | Does impact shortfall (expected-minus-realized move given flow/book state) predict reversal, beyond realized progress itself? | retired X4 |
| BL-4 | PARKED (C004: +3.2pp, CI crosses 0, confined to high-AF) | Does opposing-side replenishment (NR^ask on up-moves, NR^bid on down-moves) add predictive power after controlling for aggressive flow? | retired X5 (Priority Q2) |
| BL-5 | PARKED (C005: reversed n=3; decel below accel) | Flow deceleration vs outright sign reversal: earlier/noisier vs later/stronger reversal signal? | retired X6 |
| BL-6 | PARKED (C006 2-class + C007 3-class B−A both null) | Nested incremental-information test: does flow beat price-only, per outcome class, across horizons? | retired X7 (Priority Q3) |
| BL-7 | PARKED (prefix complete: X7 B−A failed; no full feature set; AF fishing) | Interpretable multinomial model on the full feature set + drop-one family ablations | retired X8 |
| BL-8 | PARKED (C011 anti-continuation descriptor; C012 AF→LIFOScore null) | LIFOScore as a frictionless path property: can flow state predict E[LIFOScore]? | retired X9 |
| BL-9 | PARKED (C010: k2−k1 +2.7pp, CI crosses 0; k≥3 not iterated) | Repeated signals: is a 2nd exhaustion signal stronger than the 1st only when exhaustion evidence strengthened (vs mechanical averaging-down)? | retired X10 |

## Open exploration themes (advisor may expand freely)

| ID | State | Idea | Provenance |
|----|-------|------|------------|
| BL-10 | PARKED (C013: trade-clock AF T3−T1 −2.2pp, CI crosses 0) | Which clock (LOB events / trades / price changes / wall) makes flow features most informative? | spec §21 |
| BL-11 | PARKED (prefix complete: MBP-10 overlap not on this window) | Sweep-proxy validity: does the L1 burst approximation track MBP-10 beyond-best executions on the 26-day overlap? (feeds A5) | spec §5 / A5 |
| BL-12 | PARKED (C009: T3−T1 osc −0.6pp, CI crosses 0) | Pressure-building state (strong flow, little movement): absorption→reversal vs coiling→breakout — can book dynamics separate the two? | spec §1 "pressure building" |
| BL-13 | PARKED (prefix complete: needs date expansion, forbidden) | Era structure: are any flow effects stable across 2010–2026, or purely regime-local? | §3 era slices / A8 |
| BL-14 | PARKED (prefix complete: MBP-10 / infra, not this window) | Modify-decomposition quality: synthetic-sequence unit tests + MBP-10 cross-check (feeds A4) | A4 |
| BL-15 | PARKED (C008: 100% tight_range, n=390; not near-miss) | Oscillatory-class structure: what does the unresolved bucket actually contain across H — noise, range-bound, or slow trends? | labels design |

---

## Added during the loop

| ID | State | Idea | Provenance |
|----|-------|------|------------|
| BL-16 | PARKED (C015: RTH−non-RTH +10.4pp [+4.9, +16.0]; descriptive baseline) | X1 TOD/session conditioning: does P(REVERSAL) differ RTH vs non-RTH on this prefix? | X1 method (TOD bucket); unfinished baseline |
| BL-17 | PARKED (C016: +1.9pp, CI includes 0) | X1 event/non-event day: high-importance + coupon/TIPS auctions | X1 method / OQ-10 |
| BL-18 | PARKED (C017: +1.8pp, CI includes 0) | X1 up vs down direction baseline | X1 labels leftover |
| BL-19 | PARKED (C018: T3−T1 +18.1pp [+11.7, +24.5]; descriptive vol caveat) | X1 30m pre-t* range vol regime | X1 method (vol regime) |

---

*Append new entries below with the next BL-##; never renumber.*
