# Experiment Contract — X1: Price-only baseline

| Field | Value |
|-------|-------|
| ID | X1 |
| Status | **FROZEN** (2026-08-10) |
| Source of scientific content | `research/PROGRAM.md` §4, §6 block "X1" (verbatim below) |
| Depends on | X0 PASS (eligible dates + frozen splits) |
| Blocking open questions | None for X1 (OQ-1/OQ-2/OQ-10 answered; OQ-3/OQ-4 deferred; OQ-16/OQ-17/OQ-18 answered 2026-08-12; see PROGRAM §10) |
| Inputs (read-only) | Event store (discovery + validation dates only per `split_manifest.json`); `config/frozen/episodes.yaml`, `config/frozen/labels.yaml` |
| Outputs | Episode tables + baseline tables under `workspace/analysis/move_exhaustion_v2/experiments/X1_<run_id>/`; `manifest.json`/`metrics.json`/`conclusion.json` |
| Evidentiary rule | Descriptive — **no FDR verdict** (preregistered as the reference distribution). Key number recorded: discovery-period base reversal rate for the primary cell |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum required for any change |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X1 — Price-only baseline (spec Experiment 1)

- **Goal:** establish what the price path alone already predicts, the benchmark every flow claim must beat.
- **Method:** for the primary (B,x,R,C,H) cell and robustness grid, compute base rates P(reversal), P(continuation), P(unresolved); distributions of forward return, MAE, MFE (Q50/Q90/Q95), time-to-barrier in each clock. Condition on: move magnitude (terciles), velocity (terciles), max pullback, vol regime, TOD bucket, event/non-event day, era.
- **Cells:** 4 B × 4 x primary label + conditioning tables ≈ preregistered as descriptive (no FDR verdict; this is the reference distribution).
- **Assumptions:** episode definition is flow-free (verified: builder consumes only mid path).
- **Output verdict:** none (baseline). Key number recorded: discovery-period base reversal rate for the primary cell.

Episode and label definitions are PROGRAM.md §4 (machine-readable:
`config/frozen/episodes.yaml`, `config/frozen/labels.yaml`), including cell
guardrails (reject cells with < 100 resolved discovery episodes, > 80%
unresolved, or > 90% one class).

## Run log references

*(none — not started)*
ces

*(none — not started)*
*
ces

*(none — not started)*
)*
*
ces

*(none — not started)*
d)*
)*
*
ces

*(none — not started)*
ed)*
d)*
)*
*
ces

*(none — not started)*
ted)*
ed)*
d)*
)*
*
ces

*(none — not started)*
rted)*
ted)*
ed)*
d)*
)*
*
ces

*(none — not started)*
