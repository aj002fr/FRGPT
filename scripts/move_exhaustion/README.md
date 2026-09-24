# Move Exhaustion Research

Offline research package for predicting Treasury futures **move exhaustion**.

Does **not** replace Aggression V2. Uses V2 fair value / residual as features only.

## Isolation

| Item | Value |
| --- | --- |
| Code | `scripts/move_exhaustion/` |
| Outputs | `workspace/analysis/aggression_v2_move_exhaustion/` |
| Live dashboards / streams | **untouched** |
| Ports / SOD / Telegram | **not wired** |

## Phase 1 freeze (current)

- Decision grid: existing V2 **1-minute** TBBO panel (5s rebuild deferred)
- Session / `day_open_price`: legacy V2 **01:00–17:00 ET**
- Primary model join: ridge walk-forward predictions + causal residual_z
- Primary symbols: TY (panel), with FV/TU/US features already in matrix
- Final holdout: locked until later phases

## Quick start

```text
py -3 -m scripts.move_exhaustion.cli e00
py -3 -m pytest tests/test_move_exhaustion_e00.py -q
```

## Spec source

Full autonomous research specification lives in the conversation / project docs.
Experiment order starts at E00 → E01 → … Final holdout last.
