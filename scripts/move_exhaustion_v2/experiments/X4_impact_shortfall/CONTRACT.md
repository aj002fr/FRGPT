# Experiment Contract — X4: Price/flow divergence via impact shortfall

| Field | Value |
|-------|-------|
| ID | X4 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-3**. No run may claim evidentiary status under this contract; the idea re-enters via the autoresearch loop and, if promoted, a new frozen contract |
| Source of scientific content | `research/PROGRAM.md` §5, §6 block "X4" (verbatim below) |
| Depends on | X1 complete; Phase-2 features incl. walk-forward expected-impact model (model spec must be frozen before evaluation, per Method) |
| Blocking open questions | OQ-7 (stability-gate unit), **OQ-8 (checkpoint-set cells overlap — must be answered before the FDR family is final)** |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); `config/frozen/gates.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X4_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json`; mandatory E07-style cross-check report |
| FDR family | **2 preregistered headline cells** (2 checkpoint sets × 1 model) — subject to OQ-8 resolution; BH q=0.10 within this family |
| Mandatory cross-check | corr(IS, realized move) reported; conditional effect must survive controlling for realized move (prior E07 lesson). A run without this report has no evidentiary status |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum; added cells form a new FDR family |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X4 — Price/flow divergence via impact shortfall (spec Experiment 4)

- **Goal:** does "price should have moved farther given this flow, but didn't" predict reversal?
- **Method:** expected-impact model `f(AF^D, PF, D, spread, σ, TOD)` fit walk-forward on discovery (linear first; model spec frozen before evaluation); `IS_t` standardized per era; `P(reversal | IS decile)` at `t*` and at within-move checkpoints. Never raw `P_t − F_t` (unit mismatch, per spec §16).
- **Cells:** 2 checkpoint sets ({t*}, {75%, 100% of B}) × 1 model = 2 headline cells.
- **H0:** IS decile uninformative for reversal.
- **Gate:** top-decile vs bottom-decile lift ≥ 8pp, FDR, stability.
- **Assumptions:** expected-impact model R² will be low; that is acceptable — IS only needs rank information. Model fit quality reported but not gated.
- **Cross-check (from prior E07):** verify IS is not just −(realized progress); report corr(IS, realized move) and require the conditional effect to survive controlling for realized move.

## Run log references

*(none — not started)*
