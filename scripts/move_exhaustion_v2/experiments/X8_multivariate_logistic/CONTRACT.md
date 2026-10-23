# Experiment Contract — X8: Multivariate multinomial logistic

| Field | Value |
|-------|-------|
| ID | X8 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-7**. No run may claim evidentiary status under this contract; the idea re-enters via the autoresearch loop and, if promoted, a new frozen contract |
| Source of scientific content | `research/PROGRAM.md` §5, §6 block "X8" (verbatim below) |
| Depends on | X7 complete with written conclusion |
| Blocking open questions | OQ-7 (stability-gate unit), OQ-11 (era-consistency evaluation, inherited from X7's gate) |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); `config/frozen/gates.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X8_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json`; coefficients report; drop-one ablation table; per-class calibration |
| FDR family | Model-vs-Model-A comparison per class under the X7 material-improvement bar |
| Scope guard | Nonlinear ML (XGBoost etc.) is out of scope until X8 is understood, and only if X7 passed — this is a frozen constraint, not a preference |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X8 — Multivariate multinomial logistic (spec Experiment 8, §20, §27–28)

- **Goal:** one interpretable 3-class model on the full V1 feature set; primary target `P(Y ∈ {reversal, continuation, oscillatory} | large move, X)`; secondary path targets E[MAE], E[MFE], P(MAE>a), quantile regressions for Q90/Q95 MAE (all in ticks, frictionless — these describe path character, not economics).
- **Method:** L2 multinomial logistic + coefficients report; feature-family ablations (drop-one, like prior E28); per-class calibration.
- **Gate:** vs Model A of X7 — same material-improvement bar per class. Nonlinear ML (XGBoost etc.) is explicitly out of scope until X8 is understood and only if X7 passed.

## Run log references

*(none — not started)*
