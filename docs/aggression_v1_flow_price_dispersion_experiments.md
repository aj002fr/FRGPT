# V1 Flow–Price Dispersion Regime Research

## Executive Read

Causal V1-style implied-price residual dynamics (full-curve TotalAggress → TY price) were tested as predictors of independent forward RANGE vs TREND regimes on the TBBO feature matrix.

**Stop/go verdict: STOP / PROVISIONAL — no dashboard.**

Gate counts on validation: **0 PASS / 1 PROVISIONAL / 69 FAIL**. FINAL was not inspected (no PASS cells). Dispersion features did not improve regime classification over price+flow baselines. No live V1/V2/V3 apps, ports, or `*_live/` stores were touched.

## Novelty vs Prior Failures

This study is **not** a re-run of V3 `flow_price_gap`, effort-range, or generic trend-range research. The primary object is a **causally fitted V1 residual**:

- prior-day OLS `price_delta = α + β · agg_cum_daily`
- first day uses expanding same-day prior minutes only
- residual gap/velocity/acceleration/lead-lag/effort-per-tick and large-age dynamics over 1–60m windows

Existing V3 flow-effort and structure labels appear only as benchmarks/interactions.

## Data And Splits

Source: `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv`.

| Item | Value |
| --- | ---: |
| Raw rows / dates | 57,179 / 92 |
| Clean rows / dates | 57,125 / 78 |
| Discovery dates | 43 (2026-01-27 → 2026-04-08) |
| Validation dates | 19 (2026-04-10 → 2026-05-08) |
| Locked FINAL dates | 16 (2026-05-12 → 2026-06-05) |

Short/incomplete days (&lt;400 rows) were excluded. Primary regime horizon is 30m; cutoffs for path efficiency / displacement / absolute net ticks were learned on **discovery only** and frozen.

Optional V1 cache parity smoke against `_totalaggress_cache` passed (formula check only; panel uses TBBO `agg_cum_daily`).

## Gate

Shared aggression gate (`aggression_gate_lib`): date-clustered bootstrap (2,000 draws), one-sided lower CI, BH-FDR `q=0.10`, minimum `n≥50`, `≥8` dates, `≥8pp` lift. Episodes de-clustered at 15-minute stride. Session censoring and AMBIGUOUS regimes retained as non-scored states.

PASS requires n/dates/lift/CI/FDR. PROVISIONAL never authorizes a dashboard.

## Results

### State / monotonicity / interactions

Most cells are flat-to-weak on validation (&lt;5pp). The single provisional cell:

| Cell | Split | n | Dates | RANGE rate | Baseline | Lift | BH adj. p | Gate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `state_large_widening_range` | discovery | 518 | 43 | 54.8% | 54.8% | **−0.01pp** | — | — |
| `state_large_widening_range` | validation | 200 | 19 | 62.0% | 53.9% | **+8.13pp** | 0.525 | PROVISIONAL |

Large `|z|≥1` residual **widening** looks associated with forward RANGE on validation only. Discovery lift is zero, FDR fails, and robustness slices scored as TREND lifts are all negative/FAIL (consistent with a RANGE lean, not a stable signal). No other monotonicity, state-grid, interaction, or lead/lag cell clears the gate.

### Incremental classifiers (validation)

| Model | AUC | Log loss | Brier |
| --- | ---: | ---: | ---: |
| price_only | 0.547 | 0.695 | 0.251 |
| price_flow | **0.562** | **0.694** | **0.250** |
| price_dispersion | 0.530 | 0.701 | 0.253 |
| full (price+flow+dispersion) | 0.547 | 0.700 | 0.253 |

Adding V1 residual/dispersion features **hurts** AUC vs price+flow. Conditional TREND direction hit rate after predicting TREND is ~51.5% (≈chance).

### FINAL

Not inspected (`final_inspected: false`) because no validation PASS cells existed.

## Artifacts

Under `workspace/analysis/aggression_v1_flow_price_dispersion/`:

- `dispersion_panel.csv`
- `candidate_journal.csv`
- `validation_leaderboard.csv`
- `locked_final_results.csv` (empty by design)
- `model_comparison.csv`
- `regime_cutoffs.json`, `split_manifest.json`
- `dispersion_research_summary.json`, `dispersion_gate.json`

Script: `scripts/aggression_v1_flow_price_dispersion_research.py`  
Tests: `tests/test_v1_flow_price_dispersion.py` (5 passed)

## Decision

Do **not** add V1 residual dispersion badges, RANGE/TREND predictors, or dashboard context to V1/V2/V2.1/V3 from this run. The provisional large-widening→RANGE hint is a validation-only FDR failure with null discovery lift; keep as a logged diagnostic only. Revisit only with materially more independent dates and a pre-registered gate after explicit approval.
