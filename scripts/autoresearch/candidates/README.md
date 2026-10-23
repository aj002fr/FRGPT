# Candidates

Phase 3 contains execution-bound, target-free candidates for E001, E002, and
D001. Their source hashes are frozen in current `execution_manifest_v2.json`.

A future, separately approved candidate must expose:

- `fit(rows, config) -> JSON-serializable frozen_state`, called on discovery only;
- `transform(rows, frozen_state) -> rows`.

The runner binds contract hash, declared config, and candidate state in one
immutable envelope. Each output row is limited to UTC `formation_ts` plus the
exact registered finite feature map. Targets, future values, cells, direction,
PnL, costs, censoring, sampling, and baselines belong to the fixed evaluator.

Real discovery scoring remains unauthorized. The only completed real-data run
was the formation-only deterministic preflight, with all forbidden-call
counters equal to zero.

