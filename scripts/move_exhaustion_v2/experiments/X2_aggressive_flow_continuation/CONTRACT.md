# Experiment Contract — X2: Aggressive flow → continuation

| Field | Value |
|-------|-------|
| ID | X2 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-1**. No run may claim evidentiary status under this contract; the idea re-enters via the autoresearch loop and, if promoted, a new frozen contract |
| Source of scientific content | `research/PROGRAM.md` §5, §6 block "X2" (verbatim below) |
| Depends on | X1 complete (episode universe + baseline tables); Phase-2 aggressive-flow features with passing leakage tests |
| Blocking open questions | OQ-7 (stability-gate unit for effect experiments), OQ-13 (which AF normalization carries the headline H1) |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); `config/frozen/gates.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X2_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json` |
| FDR family | **6 preregistered headline cells** (2 AF normalizations × 3 lookbacks N ∈ {50, 100, 250}); BH q=0.10 within this family. Other N values are descriptive only |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum; added cells form a new FDR family |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X2 — Aggressive flow → continuation (spec Experiment 2)

- **Goal:** conditional on comparable preceding price moves, does stronger confirming `AF^D` raise continuation probability?
- **Method:** within matched buckets (move-magnitude tercile × velocity tercile × vol regime), quintiles of `AF^D_dir` at `t*`; compare continuation rate Q5 vs Q1.
- **Cells:** 2 AF normalizations × 3 lookback N ∈ {50, 100, 250} = 6 headline cells (buckets pooled with date clustering); robustness across other N reported descriptively.
- **H0:** no monotone relation between AF^D quintile and continuation rate within matched buckets.
- **Gate:** SESOI ≥ 8pp Q5−Q1 lift, BH across 6 cells, stability gates.
- **Assumptions:** matching on 3 price variables adequately controls the path (checked via within-bucket balance table).

## Run log references

*(none — not started)*
