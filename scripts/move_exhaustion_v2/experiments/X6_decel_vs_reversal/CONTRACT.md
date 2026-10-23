# Experiment Contract — X6: Flow deceleration vs flow reversal

| Field | Value |
|-------|-------|
| ID | X6 |
| Status | **RETIRED_TO_BACKLOG** (2026-08-11; PROGRAM.md §10 addendum — advisor/worker loop restructure). Science preserved verbatim below (hash-pinned) as audit and backlog seed **BL-5**. No run may claim evidentiary status under this contract; the idea re-enters via the autoresearch loop and, if promoted, a new frozen contract |
| Source of scientific content | `research/PROGRAM.md` §5, §6 block "X6" (verbatim below) |
| Depends on | X1 complete; Phase-2 aggressive-flow features (AF, ΔAF at within-move checkpoints) with passing leakage tests |
| Blocking open questions | OQ-7 (stability-gate unit) |
| Inputs (read-only) | Event store + cached feature matrix (discovery/validation only); `config/frozen/gates.yaml` |
| Outputs | `workspace/analysis/move_exhaustion_v2/experiments/X6_<run_id>/` with `manifest.json`/`metrics.json`/`conclusion.json` |
| FDR family | **3 preregistered headline cells** (3 pairwise state contrasts × 1 primary lookback); BH q=0.10 within this family |
| Holdout rule | Never touches holdout dates. `holdout_opened: false` |
| Amendment policy | User approval + dated PROGRAM.md §10 addendum; added cells form a new FDR family |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X6 — Flow deceleration vs flow reversal (spec Experiment 6)

- **Goal:** separate the earlier/noisier signal (AF decelerating but same-signed) from the later/stronger one (AF sign flip).
- **Method:** at within-move checkpoints, classify state ∈ {accelerating, decelerating (AF·s>0, ΔAF·s<0), reversed (AF·s<0)}; compare reversal rates, time-to-reversal, MAE/MFE across states within matched price buckets.
- **Cells:** 3 pairwise state contrasts × 1 primary lookback = 3 headline cells.
- **H0:** state uninformative.
- **Preregistered expectation:** reversed > decelerating > accelerating in reversal rate; decelerating fires earlier (shorter time-from-anchor) with wider MAE dispersion.
- **Gate:** ≥ 8pp between reversed and accelerating; deceleration evaluated on both lift and earliness.

## Run log references

*(none — not started)*
