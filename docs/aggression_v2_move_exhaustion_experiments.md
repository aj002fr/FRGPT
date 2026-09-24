# Move Exhaustion Research (offline)

Isolated research package for predicting Treasury futures move exhaustion.
Does not replace Aggression V2; treats V2 residual / fair value as features.

**Observations log (all findings):** `docs/aggression_v2_move_exhaustion_observations.md`  
**Final report:** `docs/aggression_v2_move_exhaustion_final_report.md`

## Isolation

- Code: `scripts/move_exhaustion/`
- Configs: `configs/move_exhaustion/`
- Outputs: `workspace/analysis/aggression_v2_move_exhaustion/` only
- **Never** edit or restart live V1/V1.2/V2/V2.1/V2.2/V3 dashboards or streams
- No SOD / Tailscale / Telegram / execution wiring

## Phase 1 freeze

- 1-minute V2 TBBO panel (5s grid deferred)
- Legacy session `day_open` at 01:00 ET
- Ridge walk-forward join + causal `residual_z`
- Final holdout locked

## Commands

```text
py -3 -m scripts.move_exhaustion.cli e00
py -3 -m pytest tests/test_move_exhaustion_e00.py -q
```

## Experiment status

| ID | Status |
| --- | --- |
| E00 | **passed** |
| E01 | **completed** — primary candidate `B_price_plus_flow`; all A–D audit-ok on dev dates |
| E02 | **completed** — best price-only: `B1_fade_immediate` (~+0.37 mean net ticks pre-cost) |
| E03 | **completed** — best V2: `V4_extreme_aligns_event__fixed_30m` (~+0.84 ticks, 22% entry); beats E02 pre-cost |
| E04 | **completed** — flow categories **weak** on eval (&lt;8pp exhaustion spread); not yet useful alone |
| E05 | **completed** — DV01/tick hypothesis **not supported** at causal event-start (AUC~0.51 after leak fix) |
| E06 | **completed** — efficiency **decay** hypothesis **not supported** on still-extending t+5m landmark; level 5m efficiency remains an in-sample lead |
| E07 | **completed** — impact shortfall **provisional only** (collinear with −actual progress; boosted model best OOS RMSE) |
| E08 | **completed** — persistence/termination **not supported** (soft leads only; Hawkes unavailable on 1m) |
| E09 | **completed** — large-vs-small composition **not supported** (eval contrast underpowered) |
| E10 | **completed** — absorption→exhaustion **not supported**; stable **opposite** lead (at-extreme flow → continuation) |
| E11 | **completed** — liquidity interactions **supported** (eval ΔAUC +5.3pp; caveats: n=64, decay subset) |
| E12 | **completed** — microprice/imbalance confirmation **supported** (+10.4pp vs price-only; collinear signals) |
| E13 | **completed** — residual **dynamics supported** (+9.5pp vs static \|z\|); attractive fade category **not** confirmed |
| E14 | **completed** — price confirmation **unavailable**; flow-proxy **not supported** (last-mover descriptive lead) |
| E15 | **completed** — duration-norm **curve flow factor supported** (continuation); residual→exhaustion **not** |
| E16 | **completed** — **stand aside** opening + roll; interactions help but AUC still weak |
| E17 | **completed** — interaction trees **rejected** (top-leaf n&lt;15) |
| E18 | **completed** — multivariate F7 **research-promising** (Brier 0.13 vs F0 0.25) |
| E19 | **completed** — discrete hazard **rejected** (rare interval positives) |
| E20 | **completed** — competing risks **rejected** (~68% unresolved) |
| E21 | **completed** — extension quantiles **research-promising** (q80 cov ~86%) |
| E22 | **completed** — reversal magnitude **research-promising** (5m MAE ~1.1t) |
| E23 | **unavailable** — MBP-1 folder empty |
| E24 | **completed** — MBP-10 **no incremental lift** vs F7 on overlap |
| E25 | **unavailable** — no MBO |
| E26 | **completed** — trading **fails after costs** (−0.54 ticks) |
| E27 | **completed** — selective top-decile **research-promising** (n=6) |
| E28 | **completed** — ablation: cross-tenor / F7 matter |
| E29 | **completed** — placebos **5/6 pass** |
| E30 | **completed** — stability **unstable** (PSI/drift/concentration) |
| Holdout | **locked** — not opened |

### E01 snapshot (dev only; holdout locked)

| Detector | Events | Resolved | Exhaustion | Unresolved | Audit |
| --- | ---: | ---: | ---: | ---: | --- |
| A price shock | 488 | 185 | 59.5% | 62.1% | ok |
| B price+flow | 811 | 257 | 56.8% | 68.3% | ok |
| C V2 residual | 603 | 171 | 51.5% | 71.6% | ok |
| D breakout+vel | 797 | 244 | 55.3% | 69.4% | ok |

1m grid note: primary move_z uses 5m (spec 300s); 30s unavailable.

### E02 snapshot (B events, R=C=4, H=15m, pre-cost)

| Policy | Entered | Hit (resolved) | Mean net ticks | Median MAE |
| --- | ---: | ---: | ---: | ---: |
| B1 fade immediate | 811 | 56.8% | +0.37 | 1.0 |
| B2 no extreme ~30–60s | 809 | 50.2% | +0.05 | 1.0 |
| B3 no extreme ~60–120s | 809 | 48.3% | −0.01 | 1.0 |
| B4 first 1-tick retrace | 806 | 45.4% | −0.17 | 2.0 |
| B5 nonpositive velocity | 809 | 50.2% | +0.05 | 1.0 |

### E03 snapshot (same B events; V2 residual entry × exits; pre-cost)

| Policy | Entered | Entry rate | Mean net ticks |
| --- | ---: | ---: | ---: |
| **V4 align + fixed 30m** | **182** | **22.4%** | **+0.84** |
| V4 align + abs(z)&lt;1 exit | 182 | 22.4% | +0.76 |
| V1 \|z\|≥2 + abs(z)&lt;1 | 261 | 32.2% | +0.56 |
| V1 \|z\|≥2 + fixed 30m | 261 | 32.2% | +0.54 |
| E02 B1 (reference) | 811 | 100% | +0.37 |

V4 = \|residual_z\|≥2 and residual sign aligns with the event move (fade with V2). Selective but beats always-fade price-only on this sample. Holdout locked; no costs.

### E04 snapshot (B events; train-quantile categories; eval dates)

| Category | N | Resolved | Exhaustion | Med extension |
| --- | ---: | ---: | ---: | ---: |
| price_without_flow | 45 | 11 | 54.5% | 1.0 |
| apparent_absorption | 73 | 25 | 52.0% | 1.0 |
| mixed | 109 | 28 | 50.0% | 1.0 |
| aggression_dominant | ~0 on eval | — | — | — |

**Verdict:** categories do **not** clearly separate exhaustion on eval (&lt;8pp). Treat as descriptive only until E07 state-dependent impact.

### E05 snapshot (causal start-time features; resolved barrier labels)

| Feature | Spearman (deciles) | AUC | Sign-stable folds |
| --- | ---: | ---: | --- |
| dv01_per_tick | −0.23 | 0.51 | yes |
| contracts_per_tick | −0.23 | 0.51 | yes |
| trades_per_tick | −0.04 | 0.51 | yes |
| event_dv01_per_tick (pre-event 10m) | −0.26 | 0.47 | no |
| flow_since_last_new_extreme | −0.50 | 0.49 | no |

**Verdict:** After removing a forward-path leak, rising flow-per-tick does **not** clearly raise exhaustion probability at event start. Within-event panel features remain for E06+.

### E06 snapshot (t+5m landmark; primary = still-extending paths)

| Sample | N | Decay hyp | Best level lead | Ablation ΔAUC vs price+flow |
| --- | ---: | --- | --- | ---: |
| Landmark (incl. retracing) | 733 | diagnostic only | impact_efficiency_5m (tautology risk) | negative |
| **Still-extending (primary)** | **404** | **not supported** | impact_efficiency_5m (in-sample) | **−0.06** |

Eval regimes on extending sample are underpowered (most buckets &lt;10 resolved). Soft mono on decay runs **opposite** the hypothesized declining→exhaustion direction. Holdout locked.

### E07 snapshot (impact model OOS RMSE → shortfall → exhaustion)

| Impact model | OOS RMSE | Eval shortfall AUC |
| --- | ---: | ---: |
| fixed aggression | 2.07 | 0.63 |
| rolling linear | 0.688 | 0.73 |
| ridge (liq) | 0.689 | 0.72 |
| huber (liq) | 0.688 | 0.73 |
| **boosted (selected)** | **0.672** | **0.74** |

| Feature (selected model) | In-sample AUC | Eval AUC | Note |
| --- | ---: | ---: | --- |
| impact_shortfall | 0.55 | 0.74 | r(shortfall, actual)≈−0.92 |
| actual_directional_progress | 0.54 | 0.62 | move-size baseline |
| residual_z | 0.50 | 0.53 | weak |
| cum_impact_shortfall | 0.81 | 0.80 | **path-contaminated; diagnostic only** |

**Verdict:** **PROVISIONAL** — not cleanly supported. Shortfall mostly mirrors −move size; thin in-sample lift vs actual; eval n_resolved=64. Carry boosted shortfall as a candidate feature into later multivariate work, not a standalone ship.

### E08 snapshot (pre-event 15×1m bars; Hawkes unavailable)

| Feature / flag | Spearman / spread | AUC | Note |
| --- | ---: | ---: | --- |
| sign_entropy | −0.80 | 0.53 | soft lead; **lower** entropy → more exhaustion (≠ mixing story) |
| max_same_side_run | −0.50 | 0.52 | soft; unstable folds |
| intensity_acceleration | +0.43 | 0.45 | soft mono, weak AUC |
| intensity_peak_passed | +8.5pp | — | only flag ≥8pp with n≥20/side |
| persistence bundle | — | 0.55 | vs intensity-only 0.51 |

**Verdict:** Hypothesis **not supported** on 1m approximations. Optional Hawkes deferred until trade-print tier.

### E09 snapshot (large_trade_share × directional flow proxies)

| Category (eval) | N | Resolved | Exhaustion | Med extension |
| --- | ---: | ---: | ---: | ---: |
| large_flow_disappearance | 41 | 13 | 69.2% | 1.0 |
| mixed | 64 | 18 | 61.1% | 1.0 |
| opposite_large_trade_emergence | 106 | 30 | 36.7% | 1.0 |
| large_led_continuation | 8 | 3 | 66.7% | 0.0 |
| small_led_continuation | 8 | 0 | — | 0.5 |

**Verdict:** Hypothesis **not supported** — continuous features weak; large-led vs disappearance contrast underpowered on eval. Descriptive only: opposite-large shows lower exhaustion than disappearance.

### E10 snapshot (pre-event absorption at extreme; controls)

| Feature | Spearman | AUC | Eval AUC |
| --- | ---: | ---: | ---: |
| apparent_absorption_score | −0.38 | 0.55 | 0.59 |
| directional_dv01_at_extreme | −0.35 | 0.54 | 0.52 |
| controls only | — | 0.54 | — |
| score + controls | — | 0.56 (Δ+1.8pp) | — |

**Verdict:** Hypothesized absorption→exhaustion **not supported**. Stable **opposite**: more same-direction DV01 without a new extreme associates with **continuation** (negative coef survives controls). Keep as falsified-direction candidate for multivariate work.

### E11 snapshot (liquidity strata + interactions)

| Regime | Exhaustion (resolved) |
| --- | ---: |
| tight spread | 54.8% (n=210) |
| wide spread | 66.0% (n=47) |
| low / normal / high depth | 58.2% / 55.1% / 57.1% |

| Interaction | In-sample ΔAUC | Depth AUC spread |
| --- | ---: | ---: |
| shortfall × depth | +0.4pp | 11.8pp |
| decay × depth (landmark subset) | +8.1pp | 24.2pp |
| flow_per_tick × spread | +2.5pp | — |

Bundle signals+liquidity → +interactions: **+1.3pp** in-sample, **+5.3pp** on eval (n_resolved=64).

**Verdict:** Hypothesis **supported** with caveats (small eval n; decay join is landmark-only; boosted models overfit — ignore). Carry depth/spread interactions into later multivariate models.

### E12 snapshot (microprice / book imbalance)

| Feature | Spearman | AUC | Eval AUC |
| --- | ---: | ---: | ---: |
| imbalance_persistence | −0.71 | 0.57 | 0.62 |
| microprice_signal_change | −0.53 | 0.57 | 0.56 |
| directional_microprice_signal | −0.49 | 0.57 | 0.63 |

| Pattern | Exhaustion True / False | Spread | Med time-to-outcome |
| --- | ---: | ---: | --- |
| price↑ but microprice turns | 62.2% / 54.6% | +7.5pp | 6m / 9m |
| imbalance reverses before price | 61.6% / 54.9% | +6.8pp | 6m / 9m |

ΔAUC vs price-only: **+10.4pp**. Note: `microprice_dev` and `size_imbalance` are collinear (r=1) on this TBBO panel — count as one family.

**Verdict:** Hypothesis **supported**. Book support fading confirms exhaustion better than price path alone; binary pattern flags near but below 8pp.

### E13 snapshot (fair-value / residual dynamics)

| Feature | Spearman | AUC | Eval AUC |
| --- | ---: | ---: | ---: |
| price_velocity | −0.90 | 0.57 | 0.63 |
| fair_value_velocity | −0.51 | 0.56 | 0.64 |
| abs_residual_z (static) | +0.27 | 0.51 | 0.53 |

Dynamics bundle vs static \|z\|: **+9.5pp AUC**.

| Category (all) | N | Exhaustion | Mean fade 30m |
| --- | ---: | ---: | ---: |
| price_reverting (stable FV) | 426 | 57.4% | +0.24 |
| fair_value_chasing_price | 188 | 55.3% | +0.45 |
| gap_continuing_to_widen | 192 | 57.6% | +0.67 |
| both closing | 5 | 33.3% | −1.80 |

**Verdict:** Primary hypothesis **supported** (path beats static residual). Secondary claim that extreme+price-revert+stable-FV is the best fade pocket is **not** confirmed (strict attractive flag n=0; category fade PnL ranks gap-widening / FV-chase ahead of price-revert).

### E14 snapshot (cross-tenor — flow proxy only)

Neighbour mid prices are **not** on the frozen TY TBBO panel → `curve_price_breadth` / return PCA **unavailable**.

| Category (flow) | N | Resolved | Exhaustion |
| --- | ---: | ---: | ---: |
| all_tenors_confirm_flow | 496 | 155 | 51.6% |
| partial_flow_confirmation | 199 | 60 | 65.0% |
| target_is_last_tenor_to_move | 71 | 23 | **82.6%** |
| target_is_first_tenor_to_move | 23 | 10 | 40.0% |
| target_moves_alone | 8 | 2 | 50.0% |

ΔAUC adding curve breadth to TY flow: +8.5pp, but confirmation→continuation direction not stably supported.

**Verdict:** Spec price-confirmation hypothesis **not testable** here. Flow-proxy hypothesis **not supported**. Carry `target_is_last_tenor_to_move` as a descriptive lead into E15.

### E15 snapshot (cross-tenor flow confirmation)

Duration-normalised signed contracts (`agg_5m / unit_DV01`) at event start.

| Feature | AUC | Coef (exh) | Sign-stable | Note |
| --- | ---: | ---: | --- | --- |
| curve_directional_flow_factor | 0.541 | −0.072 | yes | **success** (curve → continuation) |
| curve_flow_breadth_norm | 0.572 | −0.273 | yes | correct dir; discrete mono undefined |
| n_confirming_tenors_norm | 0.572 | −0.273 | yes | same as breadth |
| target_residual_flow | 0.467 | −0.011 | yes | residual→exhaustion **not** supported |
| lagged_neighbouring_flow | 0.517 | −0.042 | yes | weak |
| target_directional_flow | 0.471 | −0.040 | yes | alone weak |

| Regime | N | Resolved | Exhaustion |
| --- | ---: | ---: | ---: |
| curve_wide | 717 | 217 | 55.3% |
| target_against_curve | 46 | 16 | 68.8% |
| partial_curve | 28 | 16 | 75.0% |
| target_only | 6 | 1 | n/a (underpowered) |

Bundle ΔAUC vs target flow alone: **+6.2pp**.

**Verdict:** Hypothesis **supported** for curve-wide DV01 → continuation via `curve_directional_flow_factor`. Target-only residual absorption claim **not** supported; regime contrast underpowered. Holdout untouched.

### E16 snapshot (time-of-day / scheduled regimes)

| Model | Eval AUC | Eval Brier |
| --- | ---: | ---: |
| pooled (base residual/move/flow) | 0.417 | — |
| + regime indicators | 0.487 | — |
| + residual×regime interactions | **0.490** | — |
| separate-by-regime (overall) | 0.444 | — |

Exclusion ΔAUC vs pooled-all: roll **+5.5pp**, opening **+3.6pp**, ordinary-only +2.7pp. Scheduled-macro overlap **n=0**; auction n=14.

**Recommendation:** `do not signal in selected regimes` — **opening_period**, **roll_period** (roll = calendar proxy days 1–12 of Mar/Jun/Sep/Dec). Remaining structure: prefer pooled+interactions over separate models. Absolute discrimination remains weak on eval.
