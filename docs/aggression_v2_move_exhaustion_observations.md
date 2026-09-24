# Move Exhaustion Research — All Observations

**Package**: `scripts/move_exhaustion/`  
**Artifacts**: `workspace/analysis/aggression_v2_move_exhaustion/`  
**Compiled**: 2026-07-28  
**Scope**: E00–E30 on development dates only  

**Holdout status**: Locked / not opened. Dates `2026-05-21` … `2026-06-05` were excluded from all scoring.

**Isolation**: Offline only. No live V1/V1.2/V2/V2.1/V2.2/V3 edits, restarts, SOD, Tailscale, Telegram, or automated execution.

Related docs:

- Experiment log: `docs/aggression_v2_move_exhaustion_experiments.md`
- Final report (promotion framing): `docs/aggression_v2_move_exhaustion_final_report.md`

---

## 1. Sample and setup observations

1. Decision grid is the frozen **1-minute** V2 TBBO panel (5s / 15s / 30s bins unavailable; several specs approximated to 1m).
2. Session convention matches legacy V2: **day_open at 01:00 ET**.
3. Ridge walk-forward residuals join cleanly; causal `residual_z` uses prior-blend within day.
4. Primary event universe (frozen after E01): `**B_price_plus_flow`**.
5. Primary barrier label: first-touch **R = 4 ticks**, **C = 4 ticks**, horizon **H = 15 minutes**.
6. Development (holdout excluded): **811 events**, **59 dates**, **257 resolved** barrier outcomes (~**56.8%** exhaustion among resolved).
7. Unresolved rate is high (~**68%**): many events neither reverse nor continue by the barrier before H.
8. Independent-signal count is dates/events, not minute-rows. Eval chronological slices are often thin (tens of resolved events).

### Detector census (dev only)


| Detector         | Events  | Resolved | Exhaustion (resolved) | Unresolved share |
| ---------------- | ------- | -------- | --------------------- | ---------------- |
| A price shock    | 488     | 185      | 59.5%                 | 62.1%            |
| **B price+flow** | **811** | **257**  | **56.8%**             | **68.3%**        |
| C V2 residual    | 603     | 171      | 51.5%                 | 71.6%            |
| D breakout+vel   | 797     | 244      | 55.3%                 | 69.4%            |


---

## 2. Top-line observations (what actually held)

### Supported / research-promising


| Observation                                                                       | Evidence                                                                                    |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Selective V2 residual fade beats always-fade price-only **pre-cost**              | E03 V4 ≈ **+0.84** mean net ticks vs E02 B1 ≈ **+0.37**                                     |
| Liquidity interactions add exhaustion discrimination                              | E11 eval ΔAUC **+5.3pp**                                                                    |
| Microprice / imbalance confirmation helps vs price-only                           | E12 ΔAUC **+10.4pp**                                                                        |
| Residual **dynamics** beat static                                                 | z                                                                                           |
| Curve-wide aggressive flow associates with **continuation**                       | E15 `curve_directional_flow_factor` AUC 0.54, coef < 0; bundle **+6.2pp**                   |
| Opening + roll are better as **stand-aside** filters than as directional features | E16 exclusion ΔAUC +3.6pp / +5.5pp                                                          |
| Multivariate TBBO classifier has real eval lift                                   | E18 F7 Brier **0.134** vs F0 **0.252** (~47% relative); ROC-AUC ~**0.87**; PR-AUC ~**0.78** |
| Additional-extension quantiles calibrate roughly                                  | E21 linear q=0.8 coverage **~86%**                                                          |
| Conditional reversal size is somewhat modellable                                  | E22 5m Ridge MAE **~1.10** ticks (exhaustion-only, n_eval=31)                               |
| Abstention helps precision (thin)                                                 | E27 top-decile precision **0.67** vs all **0.51** (n=6)                                     |
| Cross-tenor family matters in ablation                                            | E28 largest Brier harm when dropped: **cross_tenor**                                        |
| Most placebos behave as expected                                                  | E29 **5/6** pass                                                                            |


### Not supported / rejected / failed after costs


| Observation                                                       | Evidence                                                            |
| ----------------------------------------------------------------- | ------------------------------------------------------------------- |
| Simple flow-dominance categories do not separate exhaustion       | E04 eval spread < 8pp                                               |
| Rising DV01/contracts-per-tick at event start ≠ exhaustion        | E05 AUC ~0.51 after leak fix                                        |
| Impact-efficiency **decay** on still-extending paths ≠ exhaustion | E06 direction wrong / underpowered                                  |
| Impact shortfall is mostly −actual progress                       | E07 r ≈ **−0.92**; provisional only                                 |
| Flow persistence / Hawkes termination not usable on 1m            | E08; Hawkes skipped                                                 |
| Large-vs-small trade composition underpowered                     | E09                                                                 |
| Apparent absorption → exhaustion is **false** on this sample      | E10; stable **opposite** (more DV01 at extreme → continuation)      |
| Cross-tenor **price** confirmation untestable                     | E14: no neighbour mids on panel                                     |
| Target-only residual flow → exhaustion not supported              | E15 residual coef ≈ 0 / wrong sign                                  |
| Shallow interaction trees overfit tiny leaves                     | E17 top-leaf lift high but **n=3**                                  |
| Discrete-time hazard / competing risks not useful                 | E19 PR-AUC ~0.08; E20 unresolved 68%                                |
| MBP-10 book shape adds **no** incremental lift vs F7              | E24 ΔAUC = 0, ΔBrier = 0                                            |
| Costed trading fails                                              | E26 [P4@0.60](mailto:P4@0.60) mean net **≈ −0.54** ticks; hit ≈ 30% |
| Stability is poor                                                 | E30 high PSI, week AUC spread, pred drift, date-concentrated PnL    |


### Unavailable data tiers


| Tier   | Observation                                                                                |
| ------ | ------------------------------------------------------------------------------------------ |
| MBP-1  | Folder exists; **0 parquet** → E23 unavailable (no fabricated refill features)             |
| MBO    | No store → E25 unavailable                                                                 |
| MBP-10 | Available on ~26 days; only **19** overlap event dates (**78** resolved) — tested, no lift |


---

## 3. Observations by experiment

### E00 — Data / causality audit

- V2 ridge residual panel joins and is usable for Phase 1.
- Sign checks and day_open audit recorded.
- Holdout dates listed; never used for modelling metrics.

### E01 — Event detectors

- All A–D audit-ok on development dates.
- **Primary candidate**: `B_price_plus_flow` (count / stability / audit), not chosen for eventual hit rate alone.
- Detectors retained later as event-type context features where helpful.

### E02 — Price-only fade baselines (pre-cost)

- Best always-enter policy: **B1 fade immediate** ≈ **+0.37** mean net ticks.
- Waiting for “no new extreme” or first 1-tick retrace **hurts** vs immediate fade on this sample.
- Establishes how much edge exists without flow/residual features.

### E03 — V2 residual baselines (pre-cost)

- Best: **V4** (`|residual_z|≥2` and residual aligns with event) × **fixed 30m** ≈ **+0.84** ticks at **22%** entry rate.
- Beats E02 B1 on pre-cost mean net ticks.
- Still the strongest **simple tradable-style** lead before costs.

### E04 — Flow-dominance categories

- Train-quantile buckets (price-without-flow, absorption, mixed, aggression-dominant) do **not** cleanly separate exhaustion on eval.
- Eval exhaustion-rate spread ≈ **4.5pp** (< 8pp gate).
- Descriptive only.

### E05 — DV01 / contracts per tick

- Forward-path features at event start leaked; after fix, start-time DV01/tick AUC ≈ **0.51**.
- Hypothesis “more flow per tick → exhaustion” **not supported** at causal event start.
- Soft lead `flow_since_last_extreme` unstable across folds.

### E06 — Impact-efficiency decay

- On **still-extending** t+5m landmarks, decay→exhaustion **not supported** (direction wrong / thin regimes).
- Level `impact_efficiency_5m` can look informative in-sample but is not the decay claim; ablation ΔAUC vs price+flow **negative**.
- Landmark / path features risk tautology with labels.

### E07 — State-dependent impact shortfall

- Boosted expected-impact best by OOS RMSE.
- Start-time shortfall ≈ **−actual progress** (r ~ −0.92); only ~+1.8pp AUC vs actual.
- **Provisional** feature candidate, not a standalone ship.
- Cumulative path shortfall excluded as contaminated.

### E08 — Flow persistence / termination

- No strict successes on 1m pre-event windows.
- Soft leads: `max_same_side_run`, intensity acceleration, sign entropy (entropy direction opposite the “mixing→exhaustion” story).
- Only `intensity_peak_passed` clears ~8pp flag spread.
- Hawkes unavailable without trade prints.

### E09 — Large vs small trade composition

- Continuous features weak; large-led continuation contrast underpowered on eval (`n_resolved` tiny).
- Descriptive: large-flow disappearance exhaustion ~69% vs opposite-large ~37% on eval — not gated as supported.

### E10 — TBBO apparent absorption at extreme

- Hypothesized absorption→exhaustion **falsified**.
- Stable opposite: more directional DV01 at extreme without advance associates with **continuation** (spearman ~−0.38; survives controls ΔAUC ~+1.8pp).
- Important negative: “stuck flow” is not a fade signal here.

### E11 — Liquidity-conditioned exhaustion

- Wide-spread exhaustion ~**66%** vs tight ~**55%**.
- Useful interactions: shortfall×depth, decay×depth (landmark subset), flowtick×spread.
- Bundle +interactions: eval ΔAUC **+5.3pp** (n_resolved_eval=64).
- **Supported** with small-n caveat; boosted AUCs diagnostic-only (overfit).

### E12 — Microprice / book imbalance

- Supportive book → less exhaustion (hypothesized direction holds).
- ΔAUC vs price-only **+10.4pp**.
- Pattern flags ~+7pp (just under 8pp cut).
- `microprice_dev` and `size_imbalance` are **collinear (r=1)** on this panel — one family, not two.

### E13 — Fair-value / residual dynamics

- Price / FV velocity dynamics beat static `|residual_z|` by **+9.5pp** AUC.
- Attractive “extreme + price-revert + stable FV” pocket **not** confirmed (strict flag n=0).
- Category fade PnL ranking: gap-widening > FV-chasing > price-reverting — secondary fade-pocket claim fails.
- Multi-model FV disagreement unavailable (single ridge).

### E14 — Cross-tenor price confirmation

- Neighbour mid prices **absent** on frozen TY panel → price breadth / return PCA **unavailable**.
- Flow-proxy alone: alone-vs-all contrast underpowered/wrong sign.
- Descriptive lead: `**target_is_last_tenor_to_move` exhaustion 82.6%** (n=23).

### E15 — Cross-tenor flow confirmation

- Duration-normalised neighbour flow (`agg / unit_DV01`):
  - `**curve_directional_flow_factor` supported** (more curve DV01 → less exhaustion).
  - Bundle ΔAUC vs target flow alone **+6.2pp**.
  - Breadth / n_confirming AUC ~0.57 with correct direction but discrete mono undefined.
- Target residual → exhaustion **not** supported.
- Target-only vs curve-wide regime contrast underpowered (`n_resolved=1` for target_only).

### E16 — Time-of-day / scheduled regimes

- Plain pooled residual/move/flow eval AUC poor (~0.42).
- Interactions lift to ~0.49 — still weak absolutely.
- Separate regime models do **not** beat pooled+interactions.
- **Recommendation**: do not signal in **opening_period** and **roll_period**.
- Roll flag is a **calendar proxy** (days 1–12 of Mar/Jun/Sep/Dec); no front-month symbol on panel.
- Scheduled-macro overlap **n=0** on this event sample; auction overlap n=14.

### E17 — Interaction-only shallow trees

- Best depth=3; eval top-leaf lift ~+49pp but **n=3**.
- **Rejected** (need n≥15 and ≥5pp).

### E18 — Multivariate classification

- Best binary set: **F7 (all eligible)** logistic / HGB family.
- Eval: Brier **0.134**, PR-AUC **0.78**, ROC-AUC **0.87**, top-10% precision **0.50**.
- Relative Brier improvement vs price-only F0 ≈ **47%**.
- GAM / LightGBM skipped (not installed).
- **Research-promising** classifier — not a costed trade.

### E19 — Discrete-time exhaustion hazard

- Primary bin forced to **1m** (sub-minute unavailable).
- Interval positives rare (base rate ~0.9%); PR-AUC ~**0.08**.
- Horizon Briers look numerically small because events are rare — not useful timing.
- **Rejected**.

### E20 — Competing risks

- Unresolved **68.3%**.
- Cause-specific PR-AUCs ~0.05 / ~0.01 — weak.
- Cumulative incidence at 15m: exhaustion ~14.5%, continuation ~13.7% on eval expansion.
- Does not beat the resolved-only classifier framing.
- **Rejected**.

### E21 — Additional-extension quantiles

- Target: further extension ticks before reverse/horizon.
- Linear q=0.8 coverage **~85.8%** (passes ~80% gate); mean actual extension ~1.7 ticks.
- **Research-promising** for risk bounds, not direction alone.

### E22 — Reversal magnitude (exhaustion-only)

- Conditional on exhaustion, 5m max reversal: Ridge MAE **~1.10** ticks (mean actual ~2.3).
- Longer horizons harder (15m MAE ~1.8; 30m ~2.4).
- Distinguishes pause vs larger revangelaerse only modestly; **research-promising** with thin eval n=31.

### E23 — MBP-1 refill

- **Unavailable** (0 parquet). No fabricated refill features.

### E24 — MBP-10 book shape

- Overlap: 19 dates, 78 resolved.
- Causal as-of depth / imbalance / slope proxies: **ΔAUC = 0**, **ΔBrier = 0** vs TBBO F7.
- **Not supported** as incremental tier on available overlap.

### E25 — MBO queue / hidden liquidity

- **Unavailable**. Cannot compare TBBO absorption vs MBP-1 vs MBO.

### E26 — Trading-policy simulation (after costs)

- Val-selected primary: **P4** (p > **0.60** and `|residual_z|` gate), fixed **15m** exit, **1-bar** latency.
- Eval mean net ≈ **−0.54 ticks**, hit ≈ **30%**, n_entered=56.
- **Date concentration flag**: one date can dominate positive PnL.
- Costs / execution: half-spread + fee/slippage proxy; entry not at mid.
- **Not supported after costs.**

### E27 — Abstention / selective prediction

- E18 F7 + Platt: all precision ~0.51; top-decile **0.67** (n=**6**); lift ~+16pp.
- Top-decile net-ticks proxy positive in-sample but **underpowered**.
- **Research-promising** only as a sparse-selectivity lead.

### E28 — Feature-family ablation

- Full F7 strong vs F0–F6 ladder.
- Largest harm when dropped on this eval slice: **cross_tenor**.
- Reinforces E15 curve-flow usefulness inside the multivariate stack.

### E29 — Placebos / falsification

- Suite: direction flip, flow shuffle, future-shift, random timestamps, wrong-tenor, threshold ±10/20%.
- **5/6 pass** overall (flow-shuffle incremental did not fully vanish — noted limitation).
- No catastrophic placebo equality with the real model.

### E30 — Stability and drift

- Flags all red:
  - high feature PSI (`abs_flow_z_5m`, `abs_residual_z`, `abs_target_flow`, `anchor_price`, opposing depth, …)
  - large week AUC spread (~0.32)
  - prediction mean shift > 0.1
  - E26 date concentration (top positive-PnL date share ~**81%** on `2026-05-01` in that report)
- Verdict: **unstable**.

---

## 4. Feature-family status (synthesis)


| Family                         | Status                      | Notes                          |
| ------------------------------ | --------------------------- | ------------------------------ |
| Price path                     | Baseline useful             | Alone weak vs F7               |
| Aggressive flow (raw)          | Mixed / often redundant     | E04–E08 mostly fail as claimed |
| Flow per tick / DV01 intensity | Not supported at start      | E05                            |
| Impact efficiency level        | Soft / provisional          | Decay claim fails (E06)        |
| Impact shortfall               | Provisional only            | Collinear with −progress (E07) |
| Trade size composition         | Not supported               | E09                            |
| Apparent absorption            | Opposite of hypothesis      | E10                            |
| Liquidity / spread / depth     | **Useful**                  | E11                            |
| Microprice / imbalance         | **Useful** (collinear pair) | E12                            |
| V2 residual / FV dynamics      | **Useful**                  | E03, E13                       |
| Cross-tenor price              | Unavailable                 | E14                            |
| Cross-tenor flow               | **Useful**                  | E15, E28                       |
| TOD / roll / macro regimes     | Stand-aside useful          | Opening + roll (E16)           |
| MBP-1 / MBO                    | Unavailable                 | E23, E25                       |
| MBP-10                         | No incremental lift         | E24                            |


---

## 5. Modelling / systems observations

1. Prefer **resolved-barrier classification** (E18) over hazard / competing-risks (E19/E20) on this label design.
2. Prefer **pooled + interactions + stand-aside filters** over separate regime models (E16).
3. Prefer **selective / abstaining** policies conceptually (E27) — but sample for top-decile is tiny.
4. Pre-cost residual fade (E03) still looks better as a simple benchmark than costed ML policies (E26).
5. Causality lessons learned the hard way:
  - no forward-path features at event start (E05)
  - landmark/path features can tautologically encode labels (E06/E07)
  - require hypothesized **direction**, not just AUC (E10)
  - gate interactions on eval when n is small (E11)
  - document data gaps instead of inventing prices/depth (E14/E23/E25)
6. Promotion label for the package today: **continue research / collect richer data** — not live shadow, not execution.
7. **Independently validated** is not claimable: holdout sealed; after-cost negative; stability red.

---

## 6. What we still do **not** know

1. Whether any frozen policy survives the **untouched holdout**.
2. Whether usable **MBP-1 refill** or **MBO queue** features would add incremental value.
3. Whether a larger independent date cohort would stabilize E18/E27 or fix E26/E30.
4. Sub-minute timing of exhaustion (grid limitation).
5. True executable PnL under live queue position / partial fills (proxied only).

---

## 7. Artifact index


| Item                        | Path                                                                                                |
| --------------------------- | --------------------------------------------------------------------------------------------------- |
| Experiment artifacts        | `workspace/analysis/aggression_v2_move_exhaustion/experiments/`                                     |
| Shared event feature matrix | `workspace/analysis/aggression_v2_move_exhaustion/experiments/_shared/event_feature_matrix.parquet` |
| Registry                    | `workspace/analysis/aggression_v2_move_exhaustion/manifests/experiment_registry.csv`                |
| Config / notes              | `configs/move_exhaustion/experiments.yaml`                                                          |
| CLI                         | `py -3 -m scripts.move_exhaustion.cli eXX`                                                          |


---

*End of observations log. All metrics above are discovery/validation chronology only; final holdout remains locked.*