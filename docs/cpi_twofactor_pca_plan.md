# CPI Two-Factor Dashboard Plan and Status

## Document Status
- Last updated: 2026-02-13
- Owner: Dashboard research workflow
- Current phase: Prototype complete, model hardening and regime-calibration planning
- Main artifact: `scripts/cpi_twofactor_pca_dashboard.py`

## 1) Original Plan Steps (What We Set Out To Build)
1. Build a standalone CPI dashboard script that outputs a single self-contained HTML file.
2. Join CPI components (MoM/Core MoM + YoY) to event timestamps and TY 1m market data.
3. Compute pre-event and post-event move targets in bps.
4. Build two-factor representations (orthogonal factors) and map factor state to expected move buckets.
5. Add user-facing drilldowns so outputs are auditable by historical analogs.
6. Validate via CLI generation and saved scenario files.

## 2) What We Have Right Now (Implemented)

### Data and event hygiene
- CPI components loaded from Bloomberg workbook (`bbg_event_fields.xlsx`) with flexible block parsing.
- CPI timestamps loaded from `workspace/economic_events.db` (`bbg_data`).
- TY OHLCV loaded from PostgreSQL and converted to US/Eastern.
- Strict timestamp windows implemented:
  - Pre: `3:00pm T-1 -> 8:29am T`
  - Post: `8:30am T -> 5:00pm T`

### Core calculations and targets
- Main risk target is one-sided max extent:
  - `extent_points = max(high - open, open - low)`
  - `extent_bps = points_to_bps(extent_points)`
- Signed close-to-close bps also computed for diagnostics/drilldown context.

### Factor construction
- Pre (macro) factors:
  - Primary inputs: `[mom_cons_std, core_mom_cons_std]`
  - Fallback when flat: `[mom_consensus, core_mom_consensus]`
- Pre (market) factors:
  - Inputs: `[trail_range_bps, trail_vol_bps]`
  - Trailing features computed strictly before pre-window start (`et_ts < pre_start`) to avoid forward bias.
- Post factors:
  - Trend-adjusted surprises with rolling expected MoM from YoY:
    - `mom_expected = a + b * mom_yoy_level`
    - `core_expected = a_c + b_c * core_yoy_level`
  - Adjusted z-surprises:
    - `mom_z_adj = (mom_actual - mom_expected) / mom_cons_std`
    - `core_mom_z_adj = (core_mom_actual - core_expected) / core_mom_cons_std`

### Modeling and bucket mapping
- PCA-based two-factor orthogonalization per mode (pre, pre-market, post).
- Regression scoring from factors to bps using:
  - Linear: `y = b1*F1 + b2*F2 + c`
  - Extent-friendly: `y = b1*|F1| + b2*|F2| + c`
- Buckets are quantile-based and assigned by regression score.
- UI shows `R2`, `RMSE`, equations, and matched bucket.

### UI and drilldowns
- Bucket tables + boxplots for pre, pre-market, post.
- Clickable bucket drilldown with:
  - normalized aligned paths
  - distro-style histogram + KDE
  - conditional stats (`N`, mean, median, std, percentiles)
  - bootstrap 95% CI around mean
  - small-sample caution labels
- Post bucket drilldown includes hourly mean B/E table (clock-time snapshots).

## 3) What Works Well
- Extent-first framing matches real event-risk usage (stop/risk/breakeven thinking).
- Time-window hygiene is strict and tradable (no day-level slop).
- Circularity guards are in place for trailing market features.
- Pre-event market-regime features are materially more informative than consensus-level fallback in small-N runs.
- Drilldown UX supports PM-style trust through historical analogs, not only single-line regression output.

## 4) What Does Not Work (Yet) / Known Limits
- Small sample problem remains (post-2020 CPI/NFP event counts are limited).
- PCA and bucket geometry are currently treated as time-stationary; regime drift is not explicitly modeled.
- Quantile buckets do not isolate extreme stress tails sufficiently.
- Linear/abs mapping can understate nonlinear tail behavior.
- Pre fallback to consensus level preserves continuity but is not a true uncertainty measure.
- Positioning/state variables (CTA/dealer gamma/spec positioning proxies) are not yet included.

## 5) Current Technical Boundaries
- Default start date: `2022-01-01`
- Default trend window: `36`
- Default bucket count: `5`
- Primary output path: `workspace/scenarios/cpi_twofactor_pca_dashboard_*.html`

## 6) Future Implementation Plan (Phased)

### Phase A: Regime-aware hardening (highest ROI)
1. Add explicit regime tagging for each event row (manual policy first, data-driven checks later).
2. Run regime-conditional PCA and/or rolling PCA.
3. Bucket within active regime, not only pooled sample.
4. Add UI comparison: all-history vs current-regime bucket outcomes.

### Phase B: Tail-risk calibration
1. Replace equal quantiles with tail-aware bucket bands (example: `0-60`, `60-85`, `85-95`, `95-99`, `99+`).
2. Promote tail metrics in UI:
   - `P90/P95/P99`
   - `Pr(extent > X bps)`
   - worst-case and expected shortfall summaries.
3. Keep mean/CI as secondary diagnostics.

### Phase C: Nonlinear response without model bloat
1. Add one nonlinear term set in regression:
   - candidate: `|F1|^2` or `|F1|*|F2|`
2. Compare against linear/abs using rolling out-of-sample scoring.
3. Keep simplest term that improves tail calibration materially.

### Phase D: Positioning/state enrichment
1. Add at least one positioning proxy:
   - CTA trend proxy from TY history (low-friction first step)
   - then optional CFTC/dealer gamma proxies if data access is available.
2. Validate whether residual risk drops meaningfully in tail buckets.

### Phase E: Expand shock-library training set (optional but valuable)
1. Add CPI-like macro releases (Core PCE, PPI, ISM Prices Paid, ECI) with strict event filters.
2. Train shared structure on broader event family.
3. Keep CPI-specific calibration layer for final mapping.

## 7) Future Analysis and Validation Plan

### Validation protocol
- Use walk-forward / rolling OOS only (no random split).
- Keep separate reporting for:
  - pooled sample
  - current regime
  - stress regime.

### Metrics to track
- Fit: `R2`, `RMSE`
- Tail quality:
  - P95/P99 absolute error
  - calibration of `Pr(extent > X)`
  - bucket stability under leave-one-out and bootstrap perturbations.

### Decision gates
- Do not promote a model variant unless:
  - tail metrics improve vs current baseline,
  - bucket composition is stable under small perturbations,
  - economic interpretation remains coherent (not only statistical uplift).

## 8) Immediate Next Sprint (Concrete Tasks)
1. Add regime tags to training rows and payload.
2. Implement regime-conditioned bucketing in backend + UI toggle.
3. Add tail metrics panel and `Pr(extent > X)` controls.
4. Add one nonlinear term option and OOS comparison table.
5. Add basic CTA proxy feature from existing TY data and test incremental lift.

## 9) References and Related Files
- Script: `scripts/cpi_twofactor_pca_dashboard.py`
- Related planning context: `memory-bank/activeContext.md`
- Change log: `memory-bank/progress.md`

