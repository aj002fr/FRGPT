# Experiment Contract — X0: Store build, QC, split freeze

| Field | Value |
|-------|-------|
| ID | X0 |
| Status | **FROZEN** (2026-08-10) |
| Source of scientific content | `research/PROGRAM.md` §1, §2, §3, §6 block "X0" (verbatim below) |
| Depends on | — (first experiment) |
| Blocking open questions | None for X0 (OQ-5/OQ-6/OQ-15 answered 2026-08-11; see PROGRAM §10) |
| Inputs (read-only) | Authorized 2022-01-01→2026-04-02 MBP-1 replacement batch under `C:\FRDBs\FutMBP\move_exhaustion_v2_source\`; `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` (parity only). MBP-10 is unavailable and disclosed as a non-blocking X0/X1 limitation |
| Outputs | `C:\FRDBs\FutMBP\move_exhaustion_v2_store\` (per-day parquet); `workspace/analysis/move_exhaustion_v2/` → `qc/daily_qc.csv`, `manifests/store_manifest.csv`, `eligible_dates.json`, `split_manifest.json`, disk-budget record, MBP-1 semantics verification note; `manifest.json`/`metrics.json`/`conclusion.json` under `experiments/X0_<run_id>/` |
| Evidentiary rule | X0 is infrastructure — no hypotheses, no FDR family. Its PASS gates data eligibility for all later experiments |
| Holdout rule | X0 may *list* holdout dates in `split_manifest.json` (that is its job) but must not compute episodes, features, or statistics on them beyond day-level QC. `holdout_opened: false` |
| Amendment policy | Any change to this contract requires user approval + dated addendum in PROGRAM.md §10 + note in STATE.md |

## Preregistered scientific content (verbatim from PROGRAM.md — do not edit)

### X0 — Store build, QC, split freeze

- **Goal:** research-eligible event store + frozen splits. No hypotheses.
- **Deliverables:** `eligible_dates.json`, `split_manifest.json` (with hashes), QC report, TBBO-parity report (Q7/Q8), disk-budget record, MBP-1 semantics verification note.
- **Pass criterion:** Q1–Q8 pass on ≥ 95% of days in 2024–2026; parity sign check exact.

QC gates Q1–Q8, builder specification, session convention, disk-budget rule, and
split design are defined in PROGRAM.md §1–§3 and extracted machine-readably in
`config/frozen/data_store.yaml` and `config/frozen/splits.yaml`.

## Run log references

*(none — not started)*
