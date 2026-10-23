# Aggression V2.2 — LIFO FRIENDLY on isolated V2 copy

Lineage note (V1 → V2.2 / V2.7): [aggression_v22_v27_from_v1.tex](aggression_v22_v27_from_v1.tex).

## Status

User-approved isolated V2 copy with display-only **LIFO FRIENDLY** alert.
Live V2 is untouched.

## Isolation

| Item | Value |
| --- | --- |
| Package | `scripts/aggression_dashboard_v22/` |
| Port | **5058** manual-only |
| Tailscale | `https://aasthajain.tail174727.ts.net:8444/` (replaces V2 on `:8444`) |
| Store | `workspace/analysis/aggression_v22_live/` |
| Live V2 | unchanged (5052 / `aggression_v2_live/`) |
| SOD / Tailscale / Telegram | not wired |

## Alert

UI shows only **LIFO FRIENDLY**. Stats card tracks finalized / RANGE / TREND / ambiguous / active.

Fire: `|V2 residual_z| ≥ 1` and `trade_intensity` ≤ session expanding p33 (causal).

Label: `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL`.

Research: foundations H5 validation restrictive PASS; FINAL CI failed — display/environment only.

## Launch

```text
py -3 -m scripts.aggression_dashboard_v22.app --host 127.0.0.1 --port 5058
```

## Tests

```text
py -3 -m pytest tests/test_v22_lifo.py -q
```
