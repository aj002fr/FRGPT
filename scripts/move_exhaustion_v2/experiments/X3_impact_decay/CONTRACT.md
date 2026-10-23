# Experiment Contract — X3: Impact decay (Priority Question 1)

| Field | Value |
|-------|-------|
| ID | X3 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-2**. No run may claim evidentiary status under this contract; the idea re-enters via the autoresearch loop and, if promoted, a new frozen contract |
| Source of scientific content | `research/PROGRAM.md` §5, §6 block "X3" (verbatim below) |
| Depends on | X1 complete; Phase-2 price-response features (rolling β) with passing leakage tests |
| Blocking open questions | OQ-7 (stability-gate unit) |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); `config/frozen/gates.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X3_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json`; mandatory placebo report |
| FDR family | **4 preregistered headline cells** (2 AF normalizations × {trade-event, LOB-event} β clock); BH q=0.10 within this family |
| Mandatory placebo | Shuffled-thirds placebo must show no effect; a run without the placebo report has no evidentiary status |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum; added cells form a new FDR family |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X3 — Impact decay (spec Experiment 3 / Priority Question 1)

- **Goal:** the cleanest exhaustion test — does declining price impact per unit flow through the move predict reversal?
- **Method:** episode split into early/middle/late thirds by trade-event count; OLS β per third; `ImpactDecay = β_late/β_early`. Compare E[ImpactDecay | reversal] vs E[ImpactDecay | continuation]; logistic `P(reversal | ImpactDecay)` within matched price buckets; report deciles.
- **Cells:** 2 AF normalizations × {trade-event, LOB-event} β estimation clock = 4 headline cells.
- **H0:** ImpactDecay distribution identical across outcomes.
- **H1 (directional):** E[ImpactDecay | reversal] < E[ImpactDecay | continuation]; decile lift ≥ 8pp.
- **Assumptions:** ≥ 15 trade events per third required for a valid β (episodes below floor excluded, count reported); β winsorized at p1/p99.
- **Known pitfall (from prior E06):** decay measured while the move is still extending mechanically correlates with the label. Mitigation: β estimated only on data up to `t*`, never after; a placebo version with shuffled thirds must show no effect.

## Run log references

*(none — not started)*
