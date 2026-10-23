# Experiment Contract — X5: Passive absorption (Priority Question 2)

| Field | Value |
|-------|-------|
| ID | X5 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-4**. No run may claim evidentiary status under this contract; the idea re-enters via the autoresearch loop and, if promoted, a new frozen contract |
| Source of scientific content | `research/PROGRAM.md` §5, §6 block "X5" (verbatim below) |
| Depends on | X1 complete; Phase-2 passive/replenishment features with passing leakage tests; MBP-10 overlap check available (A3) |
| Blocking open questions | OQ-7 (stability-gate unit) |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); MBP-10 overlap store (A3 report); `config/frozen/gates.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X5_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json`; A3 (L1 proxy) limitation statement in the conclusion |
| FDR family | **4 preregistered headline cells** (2 ratio types NR/RR × 2 lookbacks) **+ 1 nested-model comparison**; BH q=0.10 within this family |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum; added cells form a new FDR family |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X5 — Passive absorption (spec Experiment 5 / Priority Question 2)

- **Goal:** does opposing-side replenishment add predictive power after controlling for aggressive flow?
- **Method:** match episodes on (price-move bucket × AF^D quintile × vol regime); within matched sets compare reversal rate across `NR^opp` (NR^ask for up-moves, NR^bid for down-moves) terciles. Also nested logistic: `P(rev | price, AF)` vs `P(rev | price, AF, NR^opp, PF)` — ΔAUC/ΔBrier out of sample.
- **Cells:** 2 (NR vs RR) × 2 lookbacks = 4 headline cells + 1 nested-model comparison.
- **H0:** replenishment adds nothing beyond AF.
- **Gate:** ≥ 8pp tercile lift (conditional analysis) and ≥ 0.005 Brier improvement (nested model), FDR, stability.
- **Assumptions:** L1 replenishment is the economically relevant replenishment (A3); MBP-10 overlap check reported.

## Run log references

*(none — not started)*
