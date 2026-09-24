# Aggression V2 Move Exhaustion — Final Research Report

**Package**: `scripts/move_exhaustion/`  
**Artifacts**: `workspace/analysis/aggression_v2_move_exhaustion/`  
**Date**: 2026-07-28  
**Holdout**: **NOT opened**. E00 holdout dates
`2026-05-21` … `2026-06-05` were excluded from all scoring.

This report answers section 20 of the move-exhaustion research specification.

---

## 20.1 Executive conclusion

**Can exhaustion be predicted?**  
Partially, in-sample / chronological eval only. The TBBO multivariate classifier (E18 F7) is **research-promising** (eval Brier ~0.13 vs F0 ~0.25; ROC-AUC ~0.87). Selective top-decile confidence (E27) lifts precision (~0.51 → ~0.67, n=6). This is **not** a validated live signal.

**Which event definition works best?**  
Primary universe remains E01 `B_price_plus_flow` (retained A–D as event-type features). All E17–E30 results use this detector on the shared event feature matrix.

**Which features add genuinely incremental information?**  
Supported families from earlier phases and E28: liquidity interactions (E11), microprice/imbalance (E12), residual dynamics (E13), curve flow factor (E15), session stand-aside (E16), and E18 F7 all-eligible set. E28 drop-one ablation flagged **cross_tenor** as largest Brier harm when removed on this eval slice. Several earlier flow/impact hypotheses (E04–E10) were not supported.

**How many independent events support the conclusion?**  
Development (holdout excluded): **811** events across **59** dates; **257** resolved barrier labels. Eval chronological slice is small (tens of resolved events). Do not treat row counts as independent signals.

**Does the signal survive costs?**  
**No** on the primary E26 freeze. Val-selected `P4` (p>0.60 + `|residual_z|`) with 1-bar latency and fixed 15m exit: eval **mean net ≈ −0.54 ticks**, hit ≈ 30%, with **>20% of positive PnL concentrated on a single date**.

**Where does it fail?**  
- After-cost tradability (E26)  
- Stability / drift flags (E30): high feature PSI, week AUC spread, prediction mean shift, date-concentrated PnL  
- Richer book tiers: MBP-1 unavailable (E23); MBP-10 incremental ΔAUC=0 on overlap (E24); MBO unavailable (E25)  
- Hazard / competing-risks models (E19/E20) rejected  
- Holdout not opened — out-of-sample confirmation unknown  

---

## 20.2 Baseline comparison

| Baseline | Result (dev / eval, holdout locked) |
|---|---|
| Price-only fade (E02) | Best `B1_fade_immediate` ~+0.37 mean net ticks pre-cost |
| V2 residual fade (E03) | Best selective `V4_extreme_aligns_event__fixed_30m` ~+0.84 ticks pre-cost, sparse |
| Flow-per-tick / DV01 (E05) | Not supported at causal event-start |
| Impact-efficiency decay (E06) | Not supported on still-extending landmark |
| Interpretable trees (E17) | Rejected (top-leaf n&lt;15 on eval) |
| Best nonlinear / multivariate (E18 F7) | Research-promising Brier/PR-AUC; **not** after-cost tradable (E26) |

---

## 20.3 Feature conclusions

| Family | Status |
|---|---|
| Price path | Useful as baseline; alone weak vs F7 |
| Aggressive flow | Mixed / often redundant; several E04–E08 hypotheses not supported |
| Trade size / composition | Not supported (E09); ablation sometimes noisy on small eval |
| Impact efficiency | Level useful historically; **decay** claim not supported (E06) |
| Liquidity / microprice | **Useful** (E11, E12) |
| V2 residual / FV dynamics | **Useful** (E03, E13) |
| Cross-tenor | Price confirmation unavailable; **curve flow useful** (E15); E28 drop harm |
| Regime / TOD | **Useful for stand-aside** (opening/roll) more than direction (E16) |
| MBP-1 refill | **Unavailable** (E23) |
| MBP-10 book shape | Available on overlap; **no incremental lift** vs F7 (E24) |
| MBO queue | **Unavailable** (E25) |

---

## 20.4 Data-tier conclusion

| Tier | Incremental value |
|---|---|
| TBBO 1m + ridge residuals | Primary evidence base; only tier with research-promising classifiers |
| MBP-1 | Folder present, **0 parquet** → unavailable; no fabricated features |
| MBP-10 | ~26 daily parquets; 19 overlapping event dates, 78 resolved. Causal as-of depth/imbalance/slope proxies from read-only depth matrix: **ΔAUC=0 / ΔBrier=0** vs TBBO F7 on overlap |
| MBO | Not present → unavailable |

Never wrote to `D:\FRDBs`.

---

## 20.5 Model recommendation

**Continue research / collect richer data** — **do not** proceed to live shadow monitoring, SOD, Tailscale, Telegram, or controlled execution from these results alone.

Rationale:
1. Predictive lift exists on TBBO eval (E18/E27) but **fails after costs** (E26).  
2. Stability flags are red (E30).  
3. Book-depth tiers did not add value or were missing.  
4. **Final holdout remains sealed**; no confirmation claim is authorized.

If further work is approved, priorities are: expand independent dates, obtain usable MBP-1/MBO history, and preregister a costed selective policy **before** any holdout unlock.

---

## 20.6 Limitations

- Independent events: 811 / 59 dates (dev); resolved labels only 257  
- Eval slices are small (E27 top-decile n=6; E24 overlap eval n≈25)  
- Treasury tenor dependence and shared session shocks  
- Event-selection sensitivity (`B_price_plus_flow` only for late phases)  
- High unresolved barrier rate (~68% in competing-risks view)  
- Execution uncertainty: mid±half-spread proxy, 0.5-tick fee/slippage, 1m latency grid (5s/15s unavailable)  
- TBBO missing true quote updates vs full MBP  
- Regime / roll / auction effects partially proxied  
- Placebos mostly passed (5/6) but flow-shuffle incremental did not fully vanish  
- **Holdout not opened** — all metrics are discovery/validation chronology only  

---

## Experiment verdict table (E23–E30)

| ID | Verdict | Notes |
|---|---|---|
| E23 | **unavailable** | MBP-1 folder, 0 parquet |
| E24 | **not_supported** | Overlap n_resolved=78; ΔAUC=0 vs F7 |
| E25 | **unavailable** | No MBO store |
| E26 | **not_supported_after_costs** | P4@0.60 primary mean net −0.54 ticks; date concentration |
| E27 | **research-promising** | Top-decile precision lift ~+16pp (n=6) |
| E28 | **completed** | Cross-tenor largest drop harm; F7 ≫ F0–F6 |
| E29 | **pass** | 5/6 placebos |
| E30 | **unstable** | PSI, week AUC spread, pred drift, PnL concentration |

---

## Isolation checklist

- Writes only under `workspace/analysis/aggression_v2_move_exhaustion/`  
- No dashboard / `*_live` edits or restarts  
- No SOD / Tailscale / Telegram / automated execution wiring  
- Holdout dates excluded everywhere  

CLI: `py -3 -m scripts.move_exhaustion.cli eXX`
