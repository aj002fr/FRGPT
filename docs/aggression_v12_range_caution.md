# Aggression V1.2 Provisional Caution Monitor

## Purpose

Isolated V1 copy (port **5056**) with two display-only annotations:

1. **RANGE caution** — `large + widening residual` from the path-efficiency
   provisional cell (`docs/aggression_v1_flow_price_dispersion_experiments.md`).
2. **Soft B=4 breakout lean** — restrictive PASS factors from the factor×ATR/bps
   sweep (`docs/aggression_v1_dispersion_factor_breakout_experiments.md`).

## Explicit research results (do not over-claim)

| Study | Result |
| --- | --- |
| Path-efficiency RANGE cell | PROVISIONAL (+8pp validation; discovery flat; FDR fail) |
| ATR / 8bps breakout (large+widening) | No useful edge |
| Factor sweep primary `(30m, 8bps, k=1)` | **0 restrictive PASS** |
| Factor sweep soft **B=4** | **18 restrictive PASSes** — persistence / speed / large residual → mild breakout |

V1.2 therefore **explicitly labels** the second card
`PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL` and does **not**
treat it as an 8bps or directional signal.

## Non-claims

- Not a trade recommendation (no LONG/SHORT).
- Soft-breakout lean is **B=4 only** (≈8 TY ticks); primary 8bps failed.
- Live week stats are observational (RANGE-caution episodes only).
- Does not modify live V1 (5050), V2, V2.1, or V3.

## Frozen definitions

### RANGE caution

- Causal prior-day OLS residual (same-day expanding warm-start).
- `CAUTIOUS_RANGE` when `|z| ≥ 1` and abs(residual) widened over 15m.
- Episodes de-clustered at 15m; scored at +30m with frozen discovery regime cutoffs.
- Label: `PROVISIONAL / RANGE-CAUTION / NON-DIRECTIONAL`.

### Soft breakout lean (B4-only)

Fires `SOFT_BREAKOUT_LEAN` when any of:

- `large_persist_10` / `large_persist_20` (`|z|≥1` and large-residual age ≥10/20m)
- `age_ge_30` (large-residual age ≥30m)
- `z_ge_1_5` **and** `fast_15m` (|Δresidual| 15m ≥ session expanding p70)

Display-only — no episode outcome scoring for soft-breakout in this version.

Label: `PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL`.

### LIFO friendly alert (foundations H5)

UI shows only **LIFO FRIENDLY** (banner + card badge). No factor callouts on the
dashboard. Live-week stats box tracks LIFO finalized / RANGE / TREND / ambiguous /
active separately from range-caution.

Fires when `|v1_residual_z| ≥ 1` and `trade_intensity` ≤ session expanding p33
(causal). Display/environment only — not long/short.

Research: validation restrictive PASS (+10.6pp); FINAL +8.15pp but CI failed.

Label: `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL`.

## Artifacts

- Package: `scripts/aggression_dashboard_v12/`
- Store: `workspace/analysis/aggression_v12_live/`
- Tests: `tests/test_v12_range_caution.py`

## Launch

```text
py -3 -m scripts.aggression_dashboard_v12.app --host 127.0.0.1 --port 5056
```

Manual only — not SOD-wired. Restart the process to pick up these UI/API changes.
