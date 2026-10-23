# Aggression V1.2 — Provisional Caution Monitor

Isolated copy of V1 with two **display-only** annotations:

1. **RANGE caution** — large + widening residual (path-efficiency provisional).
2. **Soft B=4 breakout lean** — restrictive PASS factors from the 2026-07-16
   factor×ATR/bps sweep.

## What it means

### RANGE caution

When the causal V1 residual is large (`|z| ≥ 1`) **and still widening** over
15 minutes, V1.2 shows `CAUTIOUS_RANGE`.

### Soft breakout lean (explicit)

When persistence / speed / large residual matches the sweep’s **B=4-only**
restrictive PASS theme, V1.2 shows `SOFT_BREAKOUT_LEAN`.

**Explicit:** primary `(30m, 8bps, k=1)` had **0 PASS**. The 18 restrictive
PASSes were soft **4bps** breakouts only. This is **not** an 8bps signal and
**not** long/short.

Neither annotation changes the V1 charts.

## Status labels (fixed)

- `PROVISIONAL / RESEARCH-ONLY`
- `PROVISIONAL / RANGE-CAUTION / NON-DIRECTIONAL`
- `PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL`

## Isolation

| Item | Value |
| --- | --- |
| Port | **5056** (manual only) |
| Store | `workspace/analysis/aggression_v12_live/` |
| V1 / V2 / V3 | **not modified** |
| SOD / Tailscale / Telegram | **not wired** |

## Launch

```bash
cd Y:\Aastha\market_data_puller
py -3 -m scripts.aggression_dashboard_v12.app --host 127.0.0.1 --port 5056
```

Open: http://127.0.0.1:5056

Restart manually after code changes — existing processes are not auto-killed.
