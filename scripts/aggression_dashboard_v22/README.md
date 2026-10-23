# Aggression V2.2 — V2 Copy + LIFO FRIENDLY

Isolated copy of V2 with one added display-only alert: **LIFO FRIENDLY**.

Live V2 (`scripts/aggression_dashboard_v2/`, port 5052, `aggression_v2_live/`) is **unchanged**.

## What was added

- Banner + stats card text: `LIFO FRIENDLY` only (no factor callouts)
- Fire rule (H5 family): `|residual_z| ≥ 1` and `trade_intensity` ≤ session expanding p33
- Observational LIFO RANGE/TREND/ambiguous rates in the stats card
- Label: `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL`

Not long/short. Not executable PnL. Not SOD/Tailscale/Telegram.

## Isolation

| Item | Value |
| --- | --- |
| Port | **5058** (manual only) |
| Tailscale | `https://aasthajain.tail174727.ts.net:8444/` → `127.0.0.1:5058` |
| Store | `workspace/analysis/aggression_v22_live/` |
| V2 | **not modified** |
| Research artifacts | reads shared `aggression_v2_tbbo_models/` (read-only) |

## Launch

```text
py -3 -m scripts.aggression_dashboard_v22.app --host 127.0.0.1 --port 5058
```

Open: `http://127.0.0.1:5058`
