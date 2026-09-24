# Move Exhaustion Research — Agent Rules

## Isolation (non-negotiable)

- Do **not** edit, import runtime state from, restart, or write to:
  - `scripts/aggression_dashboard/` (V1)
  - `scripts/aggression_dashboard_v12/` (V1.2)
  - `scripts/aggression_dashboard_v2/` (V2)
  - `scripts/aggression_dashboard_v21/` (V2.1)
  - `scripts/aggression_dashboard_v22/` (V2.2)
  - `scripts/aggression_dashboard_v3/` (V3)
  - any `workspace/analysis/*_live/` store
  - SOD / Tailscale / Telegram / automated execution
- Do **not** kill or restart existing dashboard/stream processes.

## Allowed

- Read-only use of V2 research artifacts (`aggression_v2_tbbo/`, `aggression_v2_tbbo_models/`).
- Read-only reuse of pure helpers (`scripts/aggression_gate_lib.py`, DV01 config).
- Writes only under `workspace/analysis/aggression_v2_move_exhaustion/`.
- Unit tests under `tests/test_move_exhaustion_*.py`.

## Causality

At timestamp `t`, features may use only information with event time ≤ `t`.
No centred windows, no future quotes, no retrospective peaks, no random row splits.
Cross-tenor joins must be backward as-of.

## Naming

This package studies **move exhaustion** (barrier reversal vs continuation).
It is distinct from V3 live flow-effort tiers named `EXHAUSTION`.

## Experiment discipline

- Complete each experiment with a written conclusion before the next.
- Do not inspect the final untouched holdout until Phase 5 freeze.
- Prefer simple baselines before complex models.
- Never claim residual = observed passive flow.
