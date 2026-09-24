# BL-3 — Impact-shortfall spec (exploratory, prefix-only)

**Status:** APPROVED and RUN 2026-08-13 — C014 PARKED (E07: IS ≈ −Δm). Spec pins stay frozen; do not retune from the readout.  
**Tier:** EXPLORATORY / NOT EVIDENCE  
**Backlog:** BL-3 (retired X4). Not a frozen `experiments/` contract.  
**Window:** X1 `dates_scored` only (2022-01-05 → 2022-04-25, 75 dates). Do not expand discovery.  
**Holdout:** `holdout_opened: false`

This pins the expected-impact model **before** any evaluation, as required by retired X4 / PROGRAM.md §6.

---

## 1. Question

Does **impact shortfall** — price moved less than a walk-forward model expected given contemporaneous aggressive flow — raise P(REVERSAL) **beyond realized lookback progress itself**?

H0: IS terciles are uninformative for reversal after controlling for realized Δm.

---

## 2. Why not IS at the B-barrier

At `t*` the episode has already moved `B=8` ticks by construction. Then `IS = E[Δm|AF] − 8` is a monotone function of AF. C001/C013 already showed AF terciles do not lift reversal. So the **lookback window’s own mid change** (which varies) is the realized move, not the barrier `B`.

This also answers **OQ-8** for this exploratory cell: do not run a second “75% / 100% of B” checkpoint. Those two X4 cells collapse at `t*` (100% of B **is** `t*`). Confirmatory FDR-family resolution stays open if X4 is ever re-frozen.

---

## 3. Frozen model (do not retune in the run)

| Pin | Value |
|-----|--------|
| Clock / window | Last **N=100** store rows with `source_order < t_star_source_order` (same indexing as C001) |
| Realized move | `Δm_W = direction × (mid_last − mid_first) / 0.015625` over that window |
| Flow | `AF_W = direction × sum(+size AGG_BUY, −size AGG_SELL)` in the same window |
| Model | OLS `Δm_W = α + β · AF_W` only. No PF, depth, spread, σ, TOD in this first spec |
| Fit | Expanding walk-forward by `trading_date`: first **20** dates train; predict each later date (same split as C006) |
| Shortfall | `IS_raw = (α̂ + β̂ AF_W) − Δm_W` (positive = less movement than expected) |
| Standardization | `IS = IS_raw / s` where `s` is the expanding-window SD of `IS_raw` on **prior** dates only (min 20 dates). No era split on a 75-date 2022 prefix |
| Winsorize | `AF_W` and `Δm_W` at train-window p1/p99 before each fit |
| Forbidden | Raw `P_t − F_t`; using `B_ticks` or `mfe_ticks` as Δm; refitting on the test date; validation/holdout dates |

Low OOS R² is acceptable (X4 assumption). Report it; do not gate on it.

---

## 4. Headline and E07 cross-check

**Universe:** scored X1 episodes (drop `UNRESOLVED_AMBIGUOUS`, `CENSORED_INCOMPLETE_HORIZON`) with finite IS on OOS dates.

**Headline (one cell):** rank-first terciles of `IS`.  
`P(REVERSAL | T3) − P(REVERSAL | T1)`, 400 date-clustered bootstrap, seed 42.  
T3 = largest shortfall (price lagged flow).

**n-floor:** ≥ 30 OOS episodes per IS tercile. If failed, do not headline.

**Mandatory E07 report (no evidentiary status without it):**

1. Pearson `corr(IS, Δm_W)` on OOS rows. Flag if `|r| > 0.85` (IS is mostly −realized progress).
2. Repeat T3−T1 **within each Δm_W tercile**. The headline sign must not live in a single Δm tercile (same rule as C004/C012).

Do not run a second checkpoint (75% of B), a second N, or a richer `f(·)`.

---

## 5. Success bar (exploratory)

- **Advance:** headline ≥ **5 pp**, CI not entirely ≤ 0, n-floor met, `|corr(IS, Δm_W)| ≤ 0.85`, and IS contrast not confined to one Δm tercile.
- **PARK:** lift ≤ 0, CI-only noise, n-floor fail, or E07 fail.
- Confirmatory X4 gate (8 pp, FDR, two checkpoint cells) is **out of scope**. This spec cannot be cited as X4 evidence.

---

## 6. I/O

| Direction | Path |
|-----------|------|
| Read | `X1_20260813T143427Z/labeled_B8_x2.parquet`, `manifest.json` `dates_scored` |
| Read | `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` (`source_order`, `size`, `event_class`, `mid`) |
| Write | `workspace/analysis/move_exhaustion_v2/loop/C014_impact_shortfall/` only, **after** run approval |
| Cycle folder | `research/loop/cycles/C014_impact_shortfall/` created only when the run is approved |

No frozen YAML edits. No D:. No live dashboards. No C015 in the same worker.

---

## 7. Approval

Reply **approve BL-3 spec** (or request edits). Until then the loop stays paused; C014 is not opened.
