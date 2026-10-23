# V1 Flow–Dispersion Foundations Study

Offline hypothesis catalog (H1–H10) testing whether TotalAggress–fair-value
dispersion (width, duration, shape, speed), flow intensity, and
depth/passive proxies predict **CONTINUED_RANGE vs BREAKOUT**.

Does **not** change live V1 or V1.2 scoring.

Artifacts: `workspace/analysis/aggression_v1_dispersion_foundations/`  
Script: `scripts/aggression_v1_dispersion_foundations_research.py`  
Run date: 2026-07-17

## Definitions

| Label | Rule |
| --- | --- |
| **BREAKOUT** | \|net_h\| ≥ **B** yield-bps (= `B × 2` TY ticks) and not event-near (±30m) |
| **CONTINUED_RANGE** | range_h ≤ **k × ATR_unit** and not BREAKOUT |
| **ATR_unit** | prior-day RTH high–low (ticks) × √(h / 420) |

Primary headline: **h=30, B=8, k=1.0**. Grid: `h∈{15,30,60}`, `B∈{4,6,8,12}`, `k∈{0.5,1.0,1.5}`.

Passive is a **proxy** only (`displayed_depth`, `size_imbalance`, `trade_size_vs_depth`) — not resting-order identity.

## Scale

| Item | Value |
| --- | ---: |
| Factors (H1–H10) | 73 |
| Label configs | 36 |
| Journal rows | 21,024 |
| Validation cells | 10,512 |

## Gate ladder (validation)

| Gate | PASS | PROVISIONAL | FAIL |
| --- | ---: | ---: | ---: |
| very_loose | 1097 | 0 | 9415 |
| loose | 187 | 280 | 10045 |
| moderate | 111 | 196 | 10205 |
| standard | 107 | 193 | 10212 |
| strict | 53 | 63 | 10396 |
| **restrictive** | **53** | **63** | **10396** |

Most restrictive PASSes are soft **B=4** (or B=6 @ k=0.5) breakout cells — same theme as the prior factor sweep (persist / speed / large).

## Primary `(30m, 8bps, k=1.0)`

Regime mix:

| Split | BREAKOUT | CONTINUED_RANGE | breakout share | range share |
| --- | ---: | ---: | ---: | ---: |
| Discovery | 78 | 19757 | 0.23% | 59.4% |
| Validation | 55 | 8168 | 0.42% | 61.8% |
| FINAL | 95 | 5662 | 0.89% | 53.2% |

### Hypothesis readout on primary (validation max lift)

| Family | Best factor | Best outcome | Max lift | BH sig cells |
| --- | --- | --- | ---: | ---: |
| **H5 flow-in-episode** | `H5_trade_int_lo` | CONTINUED_RANGE | **+10.6pp** | 3 |
| H7 passive/depth | `H7_depth_lo` | CONTINUED_RANGE | +7.7pp | 0 |
| H3 shape | `widening_5m` | CONTINUED_RANGE | +5.6pp | 5 |
| H9 combos | `combo_widen_thick_depth` | CONTINUED_RANGE | +5.4pp | 1 |
| H2 duration | `age_15_30` | CONTINUED_RANGE | +2.8pp | 0 |
| H4 speed | `slow_5m` | CONTINUED_RANGE | +1.6pp | 0 |
| H1 width | `z_ge_2` | CONTINUED_RANGE | +1.6pp | 0 |
| H8 microstructure | `H8_micro_dev_hi` | CONTINUED_RANGE | +1.3pp | 0 |
| H10 FV parity | `fv_z_gap_hi` | BREAKOUT | +1.1pp | 0 |
| H6 flow×sign | `disagree` | CONTINUED_RANGE | +0.4pp | 0 |

### Primary restrictive PASS (validation)

**One cell:** `H5_trade_int_lo` → **CONTINUED_RANGE** (all non-event baseline)

| Split | n | rate | baseline | lift | one-sided lower |
| --- | ---: | ---: | ---: | ---: | ---: |
| Discovery | 847 | 67.2% | 59.4% | **+7.8pp** | (not bootstrapped) |
| Validation | 323 | 72.4% | 61.8% | **+10.6pp** | >0, BH pass |
| **FINAL** | 272 | 61.4% | 53.2% | **+8.15pp** | **<0 (fails)** |

Plain English: when the residual is large **and** trade intensity is in the **low** tercile, the next 30m is more often ATR-range than the non-event baseline. The point estimate is **stable across discovery / validation / FINAL (~8–11pp)**, but the **FINAL CI does not clear zero**, so this is **not shippable**.

### What this means for each hypothesis

1. **Width (H1):** Large \|z\| alone does not clear primary for 8bps breakout; soft B=4 only.
2. **Duration (H2):** Persist ≥20 / age ≥30 remain the strongest **soft-breakout** theme (B=4), not primary 8bps.
3. **Shape (H3):** Widening → continued-range lean is stable (all configs positive on `range_all`) but only provisional at primary; FINAL CI fails.
4. **Speed (H4):** Fast → soft B=4 breakout; slow → mild range lean; neither ships on primary 8bps.
5. **Flow-in-episode (H5):** **Best primary lead** — low trade intensity during large residual → range. Needs a cleaner CI/re-gate before V1.2.
6. **Flow×sign (H6):** Weak on primary.
7. **Passive/depth (H7):** Thin displayed depth (`H7_depth_lo`) → range lean across configs (+7.7pp primary, no BH); FINAL only +2.4pp / CI fail. Useful as context, not a signal.
8. **Microstructure (H8):** No primary edge.
9. **Combos (H9):** Persist+fast (+thin depth) amplify soft B=4 breakouts; widen+thick depth leans range. Not primary-shipping.
10. **FV parity (H10):** Live vs causal disagreement is diagnostic; soft B=4 breakout lift only.

## Soft B=4 (exploratory)

Restrictive PASSes concentrate at **B=4, k=0.5** for persistence / speed / large / persist+fast combos → BREAKOUT. Same story as the prior factor sweep. **Not** an 8bps edge.

## Verdict

| Question | Answer |
| --- | --- |
| Does dispersion predict primary 8bps breakout? | **No** useful primary breakout PASS |
| Best range lean? | **Low trade intensity while \|z\|≥1** (`H5_trade_int_lo`) — validation restrictive PASS; FINAL lift holds but CI fails |
| Does depth/passive help? | Thin depth leans range; **not** restrictive on primary; FINAL weak |
| Ship to V1.2? | **No** — keep current RANGE caution + soft B4 lean; do not add `trade_int_lo` until a re-gate with clearer FINAL CI |
| FINAL | Opened because validation primary PASS existed; **did not confirm** shipping |

## Artifacts

| File | Contents |
| --- | --- |
| `hypothesis_catalog.json` | Locked H1–H10 definitions |
| `candidate_journal.csv` | Every discovery + validation cell |
| `validation_leaderboard.csv` / `validation_by_family/` | Validation + BH |
| `factor_stability.csv` | Cross-config positive-lift counts |
| `gate_threshold_sweep/` | Gate ladder tables |
| `final_primary_eval.json` | FINAL scores for primary leaders |
| `summary.json` | Machine-readable headline |
