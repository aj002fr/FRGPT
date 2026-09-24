# Aggression V2.7 — V2.2 Copy + 10-Minute Local Fair Value

Full live-page note: [`docs/aggression_v27_monitor.tex`](../../docs/aggression_v27_monitor.tex).

Isolated copy of V2.2 with the short-horizon study's sweet-spot construction:

- Fair value is **B1_W10**: `price_{t-10} + Ridge(short flow pack)`
- Lean clock: enter `|z| ≥ 1.5`, exit `|z| < 0.5` after a **5-minute min-hold**, or flip; **10-minute** score mark (not a flatten)
- LIFO FRIENDLY display from V2.2 is kept
- Label: `PROVISIONAL / RESEARCH-ONLY / LOCAL-10M-FV / NOT AFTER-COST VALIDATED`

Named **V2.7** because **V2.3 already exists** (move-exhaustion badges on port 5059). Live V2 (5052), V2.2 (5058), and V2.3 (5059) are **unchanged**.

Not long/short. Not an after-cost PASS. Not SOD/Telegram. Tailscale HTTPS is a display proxy only.

## Isolation

| Item | Value |
| --- | --- |
| Port | **5063** (manual only) |
| Tailscale | `https://aasthajain.tail174727.ts.net:8445/` → `127.0.0.1:5063` |
| Store | `workspace/analysis/aggression_v27_live/` |
| V2 / V2.2 / V2.3 | **not modified** |
| Research artifacts | reads `aggression_v2_short_horizon_fv/` (read-only) |

## Launch

```text
py -3 -m scripts.aggression_dashboard_v27.app --host 127.0.0.1 --port 5063
```

Open: `http://127.0.0.1:5063`

Tailscale (tailnet only, added 2026-08-31; does not replace V2.2 `:8444`):

```text
https://aasthajain.tail174727.ts.net:8445/  →  127.0.0.1:5063
```

Command used: `tailscale serve --bg --https 8445 http://127.0.0.1:5063`
