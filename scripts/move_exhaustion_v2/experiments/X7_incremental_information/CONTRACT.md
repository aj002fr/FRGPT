# Experiment Contract — X7: Incremental information (Priority Question 3 — THE decision experiment)

| Field | Value |
|-------|-------|
| ID | X7 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-6**. Its nested-model design remains the natural template for a future CONFIRMATORY promotion. No run may claim evidentiary status under this contract |
| Source of scientific content | `research/PROGRAM.md` §3, §5, §6 block "X7" (verbatim below) |
| Depends on | X1–X6 complete with written conclusions; full Phase-2 feature library with passing leakage tests |
| Blocking open questions | OQ-11 (where era-consistency is evaluated given a 2025-only validation window) |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); `config/frozen/gates.yaml`, `config/frozen/splits.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X7_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json`; per-class calibration reports; per-H readouts |
| FDR family | **3 preregistered nested contrasts** (B−A, C−B, D−C); BH q=0.10 within this family |
| One-shot rule | Validation is scored **once** with discovery-frozen models. No refitting, no second look, no model selection on validation |
| Preregistered interpretation | If B−A fails: headline conclusion is "flow does not add to price mean-reversion at this resolution"; Phases 4+ shrink to descriptive scope. Acceptable outcome, stated in advance |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum; added contrasts form a new FDR family |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X7 — Incremental information (spec Experiment 7 / Priority Question 3) — THE decision experiment

- **Goal:** does flow materially improve out-of-sample prediction beyond the price path — for each of the three outcome categories?
- **Method:** nested **multinomial** logistic models (3-class: reversal / continuation / oscillatory), walk-forward within discovery, one-shot on validation:
  - A: price path only. B: A + aggressive flow. C: B + passive flow. D: C + IE/β/IS response features.
  - Metrics: multiclass Brier and log loss (headline), plus one-vs-rest ROC-AUC/PR-AUC and calibration per class so predictive power is attributed to each category separately; all date-clustered. Reported across the full H grid.
- **Cells:** 3 nested contrasts (B−A, C−B, D−C) — FDR across 3.
- **H0:** ΔBrier ≤ 0 for each nesting step (multiclass and per-class one-vs-rest).
- **Gate:** multiclass ΔBrier ≥ 0.005 and per-class ΔAUC ≥ 2pp for the step to count as material for that class; stability gates; effect must survive era slicing (sign-consistent in ≥ 3 of 5 eras).
- **Preregistered interpretation:** if B−A fails, the program's headline conclusion is "flow does not add to price mean-reversion at this resolution" and Phases 4–6 shrink to descriptive scope. This is an acceptable outcome, stated in advance.

## Run log references

*(none — not started)*
