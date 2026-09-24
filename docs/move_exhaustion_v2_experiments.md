# Move Exhaustion V2 — Program Design, Preregistration, and Experimental Log

**Status:** DESIGN / PREREGISTRATION — no experiments run yet.
**Package:** `scripts/move_exhaustion_v2/` (to be created)
**Small artifacts:** `workspace/analysis/move_exhaustion_v2/` only
**Large source archive:** `C:\FRDBs\FutMBP\move_exhaustion_v2_source\` (local disk, never D: and never the network share)
**Large derived data:** `C:\FRDBs\FutMBP\move_exhaustion_v2_store\` (local disk, never D: and never the network share)
**Label:** OFFLINE / RESEARCH-ONLY. No output of this program authorizes live signals, dashboards, SOD, Tailscale, Telegram, or execution.

This document is the single running experimental log. Every experiment gets a preregistration block (frozen **before** the run) and a run-log block (appended **after** the run, never edited retroactively). Corrections are appended as dated addenda, not rewrites.

---

## 0. Research question and program goal

Conditional on a substantial directional price move having already occurred, can aggressive and passive order flow predict whether the next meaningful move is a **reversal**, a **continuation**, or an **oscillatory / LIFO-friendly** path?

Formally we estimate `P(Y_t | X_t, P_t)` where `Y_t` is the future barrier outcome, `X_t` is the flow/book state at decision time, and `P_t` is the price path into the observation. The program's central discipline: **market state (features) is measured strictly before and independently of the future outcome (labels)** — exhaustion is never defined using the same flow features later used to predict it.

**Scope decision (2026-08-10, user-directed):** this program is a **pure information-content study**. Execution costs, fill simulation, queue modeling, after-cost PnL, and tradability are explicitly OUT OF SCOPE at this stage. The only question is whether flow has out-of-sample predictive power for the three outcome categories — **reversal, continuation, oscillatory/LIFO-friendly** — over whatever horizons prove relevant (horizon relevance is measured across the H grid, not assumed). All outcomes are frictionless mid-price barrier outcomes. Execution realism would be a separately approved follow-on only if predictive power is established.

### Relationship to the prior program (binding context)

- `scripts/move_exhaustion/` E00–E30 ran on the **1-minute TBBO panel**. E18 (F7 classifier) was research-promising (ROC-AUC ~0.87 chronological eval); **E30 flagged instability** (week AUC spread ~0.32, heavy date concentration). E30's statistical-stability lessons are adopted as preregistered gates here. E26's after-cost verdict is noted for the record but **not relevant to this program's scope** — we make no tradability claims and apply no cost gates.
- The prior flow hypotheses (E04–E10) failed **at 1m granularity**. This program re-tests them at **event granularity** with proper passive decomposition; prior failures are not treated as evidence either way at this resolution.
- The old program, its registry, and its locked 10-date holdout (2026-05-21 … 2026-06-05) stay frozen. Those dates are outside this program's data coverage (archive ends 2026-04) and are flagged excluded regardless.

### Three deliverable answers (the program succeeds even if all are "no")

1. Does declining price impact per unit of aggressive flow predict reversal? (X3)
2. Does opposing-side passive replenishment add information beyond aggressive flow? (X5)
3. Does flow genuinely add out-of-sample alpha beyond the price path, or are we rediscovering mean reversion? (X7)

---

## 1. Data acquisition

### 1.1 Source

The original 2010–2026 batch expired before its packs were retained. The
user-authorized replacement is exactly one Databento batch for `ZN.FUT`
MBP-1, parent symbology, `2022-01-01` inclusive → `2026-04-02` exclusive,
monthly CSV+Zstd, downloaded under
`C:\FRDBs\FutMBP\move_exhaustion_v2_source\<job_id>\`.

MBP-1 record semantics (to be verified against the first decompressed pack and recorded in the build manifest):

- `action ∈ {A(dd), C(ancel), M(odify), R(eset/clear), T(rade)}`; `side ∈ {B(id), A(sk), N(one)}`.
- For `T` records, `side` is the **aggressor** side: `B` = aggressive buy (lifted ask), `A` = aggressive sell (hit bid). Verified in Phase 0 QC against the TBBO matrix sign convention on overlap dates.
- Each row carries the post-event L1 state (`bid_px_00/ask_px_00/bid_sz_00/ask_sz_00`), `sequence`, `ts_event`, `ts_recv`.
- **Known scope limitation:** MBP-1 only publishes events that update the best bid/offer. Adds/cancels resting deeper than L1 are invisible. The configured MBP-10 overlap is currently absent, so A3/A4/A5 remain non-promotable limitations after X0/X1.

### 1.2 Builder (`scripts/move_exhaustion_v2/data/mbp1_store_builder.py`)

- **Order:** newest-first (2026-04 backwards to 2022-01) after a chronological prior-session volume audit.
- **Checkpointed and resumable:** per-month → per-day units; a `build_state.json` checkpoint records completed days; interrupted builds resume without recomputation.
- **Per pack:** stream-decompress zst → filter to ZN **outrights** (exclude calendar spreads by symbol pattern) → causal front-contract selection using **prior-session volume** (same convention as `aggression_autoresearch_data_build.py`; roll decisions never use same-day information) → write one parquet per trading day.
- **Sessions:** trading date = prior day 18:00 ET → trading date 17:00 ET (matches the dense-publication convention, not the legacy 01:00 ET window).
- **Derived at build time** (so research code never re-derives basics):
  - `event_class ∈ {AGG_BUY, AGG_SELL, ADD_BID, ADD_ASK, CXL_BID, CXL_ASK, MOD_*, RESET}` — modifies decomposed via consecutive L1 state diff into add-like (size up) / cancel-like (size down) / cancel+add (price change) components.
  - `pre_bid_sz`, `pre_ask_sz`, `pre_bid_px`, `pre_ask_px` = previous row's post-event book (pre-event depth per spec §4).
  - `mid`, `mid_change` flag, `spread_ticks`.
  - Four clocks as cumulative counters per session: `n_lob_events`, `n_trades`, `n_price_changes`, wall clock (spec §21).
- **Manifest:** per-day row counts, min/max timestamps, front contract used, SHA-256 of the parquet, builder version hash. Append-only `store_manifest.csv` on the workspace share; parquet stays on D:.
- **Disk budget:** before the full run, build one recent month, measure bytes/day, extrapolate to full history, and record the projection + D: free space in the Phase-0 manifest. If projected footprint exceeds free space minus 20% headroom, stop and escalate (options: column pruning, coarser float types, or trimming pre-2014 history) rather than filling the disk.

### 1.3 What we are NOT fetching

- No MBO (true queue position / order IDs); execution realism is outside scope.
- No external pull except the exact one-shot `$0` replacement batch approved on 2026-08-11.

---

## 2. Cleaning and QC (Phase-0 gates; all must pass per day before a day is research-eligible)

| # | Check | Rule | On failure |
|---|-------|------|-----------|
| Q1 | Timestamp monotonicity | `ts_event` non-decreasing within day (ties broken by `sequence`) | Day flagged `qc_fail`, excluded |
| Q2 | Crossed book | `bid_px_00 < ask_px_00` for >99.99% of rows; crossed rows logged and dropped | >0.01% → day excluded |
| Q3 | Session coverage | first/last event within 30 min of session bounds; no intra-session gap > 30 min outside known halts | Gap days flagged `partial`, excluded from episode construction |
| Q4 | Event-count sanity | daily row count within [0.1×, 10×] of trailing-20-day median | Flagged for manual review |
| Q5 | Roll audit | front contract changes only at documented roll windows; volume share of chosen contract >50% of prior session | Mismatch → manual review |
| Q6 | Book resets | `R` actions handled (state re-seeded); days with >5 resets flagged | Review |
| Q7 | Cross-source parity | on 2026 overlap dates: per-5m aggressive buy/sell volumes from the store vs `tbbo_feature_matrix.csv` aggression; sign convention identical; correlation of 5m signed flow > 0.95 | Any sign flip → hard stop, fix before proceeding |
| Q8 | Price parity | store mid vs TBBO matrix 1m price on overlap dates, max abs diff ≤ 1 tick at bucket close | Investigate before proceeding |

QC results per day go in `workspace/analysis/move_exhaustion_v2/qc/daily_qc.csv` (append-only). A frozen `eligible_dates.json` is produced at the end of Phase 0 and referenced by every later run manifest.

---

## 3. Splits and validation design (frozen at end of Phase 0, before any episode is inspected)

| Split | Window (proposed) | Role |
|-------|-------------------|------|
| Discovery | 2022-01 → 2024-12 | All exploration, parameter selection, model fitting |
| Validation | 2025-01 → 2025-12 | One-shot evaluation of discovery-frozen choices |
| Final holdout | 2026-01 → last full Q3-pass session ≤ 2026-04-01 | **LOCKED.** Explicit approval remains required to open |

- **Embargo:** 5 trading days purged at each boundary.
- **Grouping:** all signals/checkpoints belonging to the same episode stay in one fold; episodes on the same date stay in one fold. All inference is date-clustered.
- **Walk-forward:** every fitted object (expected-impact model for IS, rolling β calibration, classifiers) is fit strictly walk-forward inside discovery; nothing refits on validation.
- **Era slices:** 2022–2023 (hiking) and 2024–2026 (current). A result that holds only in one available era is regime-dependent, not general.

### 3.1 Statistical gate (applied to every experiment)

Reuses `scripts/aggression_gate_lib.py` conventions:

- Date-clustered bootstrap (10,000 draws) for effect CIs; one-sided lower bound must clear 0 for PASS.
- BH-FDR q = 0.10 across all cells **within an experiment** (cell count preregistered in each block below).
- Sample floors: n ≥ 50 episodes and ≥ 8 distinct dates per cell; cells below floor are `blocked_underpowered`, never evidence.
- Effect floor (SESOI): **≥ 8pp** absolute lift in outcome-category rate for conditional-probability experiments; **≥ 0.005 Brier** improvement for model-comparison experiments (X7/X8). No cost gates — all outcomes are frictionless mid-price barrier outcomes.
- **Stability gates (lesson of E30), required for PASS:** week-level AUC/effect spread ≤ 0.15; feature PSI train→eval ≤ 0.25 on headline features; no single date contributes > 40% of the aggregate effect.
- Verdict vocabulary: `PASS` / `PROVISIONAL` / `FAIL` / `blocked_underpowered` / `not_supported`. PROVISIONAL never authorizes anything downstream.

---

## 4. Episode and label definitions (Phase 1)

### 4.1 Directional episodes (price-only, flow-free — spec §2)

- Mid-price path `m_t` in event time. Direction `s ∈ {−1,+1}`.
- Episode qualifies when `s·(m_t − m_{t0}) ≥ B` before any counter-directional pullback `> x` (pullback measured from the running extreme).
- **Primary parameters (tick-denominated; ZN tick = 1/64 ≈ 1.4 bps at price ~112 — bps equivalents documented per era since price ranged ~105–135 over the sample):**
  - `B = 8 ticks` (~11 bps), robustness grid `B ∈ {4, 6, 8, 12}` ticks.
  - `x = 2 ticks` (~2.8 bps), robustness grid `x ∈ {1, 2, 3, 4}` ticks, plus a vol-normalized variant `x = 0.5·σ_5m`.
- **De-dupe/overlap:** one active episode per direction at a time; a new episode cannot anchor inside an unresolved prior episode's horizon; minimum 5-minute wall-clock separation between anchors.
- **Episode variables recorded:** total move (ticks + bps), time-to-B in all four clocks, trade count, LOB-event count, max pullback, realized vol during the move.

### 4.2 Outcome labels (spec §3)

From `t*` (first touch of B), competing barriers on the mid:

- Reversal barrier: `−R` from `m_{t*}` (against move direction).
- Continuation barrier: `+C` beyond `m_{t*}`.
- `Y = REVERSAL` if reversal barrier first, `CONTINUATION` if continuation first, `OSCILLATORY/UNRESOLVED` if neither within horizon `H`. **All three classes are first-class prediction targets** — the oscillatory class is the barrier-level counterpart of "LIFO-friendly" (Phase 4 gives it a finer path-based definition via LIFOScore) and is not treated as a leftover bucket.
- **Primary:** `R = C = 4 ticks`, `H = 15 min` wall clock (comparable to prior program). Robustness grid: `(R,C) ∈ {(2,2),(4,4),(8,8),(3,5),(5,3)}` × `H ∈ {5, 15, 30}` min, plus price-change-time horizon variants. **Horizon relevance is an empirical output:** every headline result is reported across the full H grid so we learn at which horizons (if any) flow is informative, rather than fixing one horizon by fiat.
- **Cell guardrails (carried from prior program):** reject any (B,x,R,C,H) cell with < 100 resolved episodes in discovery, > 80% unresolved, or > 90% one class.
- **Same-bar/same-event ambiguity:** if both barriers are touched inside one event burst (same `ts_event`), label `UNRESOLVED_AMBIGUOUS` and exclude.

---

## 5. Feature dictionary (Phase 2 — all causal at decision time, spec §4–12, §27)

Decision times: `t*` (primary) plus within-move checkpoints at 25/50/75/100% of B for the divergence and decay experiments. Every feature carries a unit test and an automated leakage assertion (max input timestamp ≤ decision timestamp).

Event-time lookbacks: `N ∈ {10, 25, 50, 100, 250, 500}` LOB events (flow/passive families), trade-event windows for aggressive-flow families, wall-clock context windows for vol/TOD.

| Family | Features | Definition notes |
|--------|----------|------------------|
| Price path | move ticks/bps, velocity (ticks per unit of each clock), acceleration, max pullback | from episode builder |
| Aggressive | `AF = M^b − M^s`; `AI = AF/(M^b+M^s+ε)`; `AF^D_sym = AF / ((Q^a+Q^b)/2)`; `AF^D_dir = M^b/(Q^a_pre+ε) − M^s/(Q^b_pre+ε)`; cumulative AF; ΔAF (acceleration); sign persistence (share of same-sign windows); arrival intensities `λ^b, λ^s` and `Δλ` | pre-event depth used in `AF^D_dir` (primary normalization) |
| Sweep proxy | volume in trade bursts that displace price ≥ 1 tick within one `sequence` burst / total aggressive volume | **L1 approximation** — documented; validated vs MBP-10 overlap |
| Passive raw | `L^bid, L^ask, C^bid, C^ask` (adds/cancels at touch, modify-decomposed) | kept as 4 raw components per spec §6 |
| Passive directional | `PF^up = L^bid + C^ask − L^ask − C^bid` (sign-flipped for down moves) | |
| Replenishment | `RR^ask = L^ask/(M^b+ε)`; `NR^ask = (L^ask−C^ask)/(M^b+ε)`; `NR^bid = (L^bid−C^bid)/(M^s+ε)` | opposing-side NR is the absorption measure |
| Price response | `IE = s·Δm_bps/(|AF^D|+ε)` (winsorized at p1/p99); rolling `β_t` from windowed OLS `Δm_j = α + β·AF^D_j`; `Δβ`; `ImpactDecay = β_late/(β_early+ε)`; impact shortfall `IS = s·(E[Δm|state] − Δm)` from a walk-forward expected-impact model `f(AF^D, PF, D, spread, σ, TOD)` | β windows: episode split into early/middle/late thirds by trade-event count |
| Book | spread, directional L1 depth, queue imbalance `(Q^b−Q^a)/(Q^b+Q^a)`, resilience (events to refill after touch depletion) | L1 only (Assumption A3) |
| Context | realized vol (5m/30m), TOD bucket, event/non-event day (existing econ/auction DB read-only), event intensity (LOB events per second vs trailing distribution) | |

Feature matrix cached per (B,x) episode universe as parquet on D: with meta JSON (feature list, window params, builder hash).

---

## 6. Experiment register (preregistration blocks)

Execution order is fixed. Each experiment must produce `manifest.json` (inputs, hashes, params), `metrics.json`, `conclusion.json`, and a run-log entry in §7 before the next begins. Cells per experiment are enumerated here; any cell added later is a dated addendum and counts against FDR from its own family.

---

### X0 — Store build, QC, split freeze

- **Goal:** research-eligible event store + frozen splits. No hypotheses.
- **Deliverables:** `eligible_dates.json`, `split_manifest.json` (with hashes), QC report, TBBO-parity report (Q7/Q8), disk-budget record, MBP-1 semantics verification note.
- **Pass criterion:** Q1–Q8 pass on ≥ 95% of days in 2024–2026; parity sign check exact.

### X1 — Price-only baseline (spec Experiment 1)

- **Goal:** establish what the price path alone already predicts, the benchmark every flow claim must beat.
- **Method:** for the primary (B,x,R,C,H) cell and robustness grid, compute base rates P(reversal), P(continuation), P(unresolved); distributions of forward return, MAE, MFE (Q50/Q90/Q95), time-to-barrier in each clock. Condition on: move magnitude (terciles), velocity (terciles), max pullback, vol regime, TOD bucket, event/non-event day, era.
- **Cells:** 4 B × 4 x primary label + conditioning tables ≈ preregistered as descriptive (no FDR verdict; this is the reference distribution).
- **Assumptions:** episode definition is flow-free (verified: builder consumes only mid path).
- **Output verdict:** none (baseline). Key number recorded: discovery-period base reversal rate for the primary cell.

### X2 — Aggressive flow → continuation (spec Experiment 2)

- **Goal:** conditional on comparable preceding price moves, does stronger confirming `AF^D` raise continuation probability?
- **Method:** within matched buckets (move-magnitude tercile × velocity tercile × vol regime), quintiles of `AF^D_dir` at `t*`; compare continuation rate Q5 vs Q1.
- **Cells:** 2 AF normalizations × 3 lookback N ∈ {50, 100, 250} = 6 headline cells (buckets pooled with date clustering); robustness across other N reported descriptively.
- **H0:** no monotone relation between AF^D quintile and continuation rate within matched buckets.
- **Gate:** SESOI ≥ 8pp Q5−Q1 lift, BH across 6 cells, stability gates.
- **Assumptions:** matching on 3 price variables adequately controls the path (checked via within-bucket balance table).

### X3 — Impact decay (spec Experiment 3 / Priority Question 1)

- **Goal:** the cleanest exhaustion test — does declining price impact per unit flow through the move predict reversal?
- **Method:** episode split into early/middle/late thirds by trade-event count; OLS β per third; `ImpactDecay = β_late/β_early`. Compare E[ImpactDecay | reversal] vs E[ImpactDecay | continuation]; logistic `P(reversal | ImpactDecay)` within matched price buckets; report deciles.
- **Cells:** 2 AF normalizations × {trade-event, LOB-event} β estimation clock = 4 headline cells.
- **H0:** ImpactDecay distribution identical across outcomes.
- **H1 (directional):** E[ImpactDecay | reversal] < E[ImpactDecay | continuation]; decile lift ≥ 8pp.
- **Assumptions:** ≥ 15 trade events per third required for a valid β (episodes below floor excluded, count reported); β winsorized at p1/p99.
- **Known pitfall (from prior E06):** decay measured while the move is still extending mechanically correlates with the label. Mitigation: β estimated only on data up to `t*`, never after; a placebo version with shuffled thirds must show no effect.

### X4 — Price/flow divergence via impact shortfall (spec Experiment 4)

- **Goal:** does "price should have moved farther given this flow, but didn't" predict reversal?
- **Method:** expected-impact model `f(AF^D, PF, D, spread, σ, TOD)` fit walk-forward on discovery (linear first; model spec frozen before evaluation); `IS_t` standardized per era; `P(reversal | IS decile)` at `t*` and at within-move checkpoints. Never raw `P_t − F_t` (unit mismatch, per spec §16).
- **Cells:** 2 checkpoint sets ({t*}, {75%, 100% of B}) × 1 model = 2 headline cells.
- **H0:** IS decile uninformative for reversal.
- **Gate:** top-decile vs bottom-decile lift ≥ 8pp, FDR, stability.
- **Assumptions:** expected-impact model R² will be low; that is acceptable — IS only needs rank information. Model fit quality reported but not gated.
- **Cross-check (from prior E07):** verify IS is not just −(realized progress); report corr(IS, realized move) and require the conditional effect to survive controlling for realized move.

### X5 — Passive absorption (spec Experiment 5 / Priority Question 2)

- **Goal:** does opposing-side replenishment add predictive power after controlling for aggressive flow?
- **Method:** match episodes on (price-move bucket × AF^D quintile × vol regime); within matched sets compare reversal rate across `NR^opp` (NR^ask for up-moves, NR^bid for down-moves) terciles. Also nested logistic: `P(rev | price, AF)` vs `P(rev | price, AF, NR^opp, PF)` — ΔAUC/ΔBrier out of sample.
- **Cells:** 2 (NR vs RR) × 2 lookbacks = 4 headline cells + 1 nested-model comparison.
- **H0:** replenishment adds nothing beyond AF.
- **Gate:** ≥ 8pp tercile lift (conditional analysis) and ≥ 0.005 Brier improvement (nested model), FDR, stability.
- **Assumptions:** L1 replenishment is the economically relevant replenishment (A3); MBP-10 overlap check reported.

### X6 — Flow deceleration vs flow reversal (spec Experiment 6)

- **Goal:** separate the earlier/noisier signal (AF decelerating but same-signed) from the later/stronger one (AF sign flip).
- **Method:** at within-move checkpoints, classify state ∈ {accelerating, decelerating (AF·s>0, ΔAF·s<0), reversed (AF·s<0)}; compare reversal rates, time-to-reversal, MAE/MFE across states within matched price buckets.
- **Cells:** 3 pairwise state contrasts × 1 primary lookback = 3 headline cells.
- **H0:** state uninformative.
- **Preregistered expectation:** reversed > decelerating > accelerating in reversal rate; decelerating fires earlier (shorter time-from-anchor) with wider MAE dispersion.
- **Gate:** ≥ 8pp between reversed and accelerating; deceleration evaluated on both lift and earliness.

### X7 — Incremental information (spec Experiment 7 / Priority Question 3) — THE decision experiment

- **Goal:** does flow materially improve out-of-sample prediction beyond the price path — for each of the three outcome categories?
- **Method:** nested **multinomial** logistic models (3-class: reversal / continuation / oscillatory), walk-forward within discovery, one-shot on validation:
  - A: price path only. B: A + aggressive flow. C: B + passive flow. D: C + IE/β/IS response features.
  - Metrics: multiclass Brier and log loss (headline), plus one-vs-rest ROC-AUC/PR-AUC and calibration per class so predictive power is attributed to each category separately; all date-clustered. Reported across the full H grid.
- **Cells:** 3 nested contrasts (B−A, C−B, D−C) — FDR across 3.
- **H0:** ΔBrier ≤ 0 for each nesting step (multiclass and per-class one-vs-rest).
- **Gate:** multiclass ΔBrier ≥ 0.005 and per-class ΔAUC ≥ 2pp for the step to count as material for that class; stability gates; effect must survive era slicing (sign-consistent in ≥ 3 of 5 eras).
- **Preregistered interpretation:** if B−A fails, the program's headline conclusion is "flow does not add to price mean-reversion at this resolution" and Phases 4–6 shrink to descriptive scope. This is an acceptable outcome, stated in advance.

### X8 — Multivariate multinomial logistic (spec Experiment 8, §20, §27–28)

- **Goal:** one interpretable 3-class model on the full V1 feature set; primary target `P(Y ∈ {reversal, continuation, oscillatory} | large move, X)`; secondary path targets E[MAE], E[MFE], P(MAE>a), quantile regressions for Q90/Q95 MAE (all in ticks, frictionless — these describe path character, not economics).
- **Method:** L2 multinomial logistic + coefficients report; feature-family ablations (drop-one, like prior E28); per-class calibration.
- **Gate:** vs Model A of X7 — same material-improvement bar per class. Nonlinear ML (XGBoost etc.) is explicitly out of scope until X8 is understood and only if X7 passed.

### Phase 4 — LIFO and repeated signals (X9–X10, preregistered after X7 readout; parameters frozen then)

- **X9 LIFO path property (spec §22):** the measurable definition of the third category. For episodes with staged hypothetical adverse entries `p1>p2>p3` (grid `g`, `a` in ticks preregistered at freeze), compute LIFOScore (fraction of later entries reaching `+g` before `−a` on the mid), max inventory, adverse/favorable ticks, time-to-unwind — all frictionless, tick-based path descriptors, not economics. Target: can flow state at signal time predict `E[LIFOScore | X]`?
- **X10 repeated signals (spec §23–24):** P(reversal | 2nd signal) vs P(reversal | 1st signal) with MAE/MFE (ticks); requirement that later signals show *strengthening* exhaustion evidence (IS↑, IE↓, NR^opp↑) rather than mechanical price-lower averaging-down; state machine Impulse → Exhaustion Candidate → Reversal Confirmed → Reset; persistent same-direction signals during continued movement scored as continuation evidence.

### Out of scope (descoped 2026-08-10, user-directed)

Execution realism — limit-order fill simulation, queue-position modeling, adverse selection conditional on fill, cost sweeps, after-cost PnL, and any tradability verdict — is removed from this program. The former X11 block is retired. If the program establishes predictive power, execution realism would be a separately preregistered, separately approved follow-on.

---

## 7. Run log (append-only — one block per completed run)

> Template — copy per run, never edit prior entries:
>
> ```
> ### [DATE] X# run <run_id>
> - Code version / builder hash:
> - Input hashes (store manifest rev, eligible_dates, split_manifest):
> - Parameters actually used (diff vs preregistration: NONE required for evidentiary status):
> - Headline metrics:
> - Gate outcome per cell (PASS/PROVISIONAL/FAIL/blocked_underpowered):
> - Stability gates (week spread / PSI / date concentration):
> - Verdict + one-paragraph interpretation:
> - Artifacts: workspace/analysis/move_exhaustion_v2/experiments/X#_<run_id>/
> - Surprises / anomalies / follow-ups:
> ```

*(no runs yet)*

---

## 8. Assumptions register

| ID | Assumption | Risk if wrong | Mitigation / check |
|----|-----------|---------------|--------------------|
| A1 | Databento MBP-1 `T.side` = aggressor side, `B`=buy aggressor | All flow signs flip | X0 Q7 parity vs TBBO matrix (hard stop on mismatch) |
| A2 | Prior-session-volume front-contract selection is causal and correct across 16 years of rolls | Episodes span wrong contract | X0 Q5 roll audit; roll windows excluded from episodes |
| A3 | L1 (at-touch) passive flow is an adequate proxy for the spec's passive quantities | Absorption mismeasured | MBP-10 26-day overlap correlation report; limitation stated in every conclusion |
| A4 | Modify decomposition via L1 state diff correctly splits add-like/cancel-like | Passive components biased | Unit tests on synthetic sequences; MBP-10 cross-check |
| A5 | Sweep proxy (price-displacing trade bursts) tracks true beyond-best executions | Urgency feature noisy | Validated vs MBP-10 overlap; demoted if corr < 0.7 |
| A6 | 5-day embargo suffices to purge label overlap at split boundaries | Leakage into validation | Max H = 30 min ≪ 5 days; formal check in X0 |
| A7 | Matched-bucket conditioning (X2/X5/X6) adequately controls the price path | Confounded flow effects | Balance tables per bucket; X7 nested models as the confound-robust test |
| A8 | Pre-2014 microstructure is comparable enough to pool | Diluted or spurious pooled effects | Era slicing mandatory in every gate; era-inconsistent results labeled regime-dependent |
| A9 | Mid-price barrier outcomes are the right object for an information-content study | Predictive power on mid may not translate to executable outcomes | Accepted by scope decision: this program makes predictive claims only, never tradability claims; translation to execution is a separate future program |

---

## 9. Governance checklist (every run)

- [ ] Reads only: MBP-1/MBP-10 archives, TBBO matrix (parity), econ/auction DB (read-only)
- [ ] Writes only: `C:\FRDBs\FutMBP\move_exhaustion_v2_source\`, `C:\FRDBs\FutMBP\move_exhaustion_v2_store\`, `workspace/analysis/move_exhaustion_v2/`
- [ ] No imports of live dashboard code; no `*_live/`, SOD, Tailscale, Telegram, port surfaces
- [ ] Final holdout untouched (`holdout_opened: false` in every manifest)
- [ ] Preregistration block frozen before run; run log appended after
- [ ] Memory bank updated (code-index, io-schema, progress, activeContext)

---

## 10. Change log

- **2026-08-10** — Document created: program design, data plan, QC gates, split design, preregistration blocks X0–X11, assumptions register. No experiments run.
- **2026-08-10 (addendum, user-directed scope change)** — Program rescoped to a pure information-content study: all execution-cost, fill-simulation, and tradability content removed (former Phase 5 / X11 retired; cost sweep dropped from the gate; A9 reworded). Three-class outcome (reversal / continuation / oscillatory-LIFO-friendly) made the explicit prediction target in X7/X8 (multinomial + per-class one-vs-rest); horizon relevance made an empirical output across the H grid. No preregistered experiment had run before this change, so no evidentiary status is affected.
- **2026-08-11 (autoresearch + prerequisite authorization)** — X2–X10 retired to the advisor/worker backlog; X0/X1 retained as prerequisites. User authorized the exact 2022–2026 replacement batch and execution of X0 then X1. Scientific resolutions and deferrals are recorded in `research/PROGRAM.md` §10 and `research/OPEN_QUESTIONS.md`; the final holdout remains locked.
