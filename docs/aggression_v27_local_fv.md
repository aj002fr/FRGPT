# Aggression V2.7 — 10-Minute Local Fair Value

Lineage note (V1 → V2.2 / V2.7): [aggression_v22_v27_from_v1.tex](aggression_v22_v27_from_v1.tex).
Full live-page note (panels, equations, clock, provenance): [aggression_v27_monitor.tex](aggression_v27_monitor.tex).
Offline LIFO-zone follow-ups from the microstructure papers (not this page): [aggression_v2_lifo_literature_experiments.tex](aggression_v2_lifo_literature_experiments.tex). 2026-09-08 run: Track P same-bar \(R^2\) 0.25 vs lead-1 ~0; L1/L3 FAIL; L2 zone PASS is not a ship. `ship_gate` false. No live-rule change.

**Useful job:** V1’s measurement question on a short window — did the last 10 minutes of TY match the last 10 minutes of aggressive curve flow? The dashed line is a contemporaneous nowcast of that stretch, not a fade target. The paper ENTRY/EXIT clock is a diagnostic scoreboard hung on the leftover, not the reason the page exists.

Isolated V2.2 copy with the 2026-08-31 short-horizon sweet-spot construction. **V2.3 was not reused**: that name and port 5059 already belong to the move-exhaustion monitor.

## Isolation

| Item | Value |
| --- | --- |
| Package | `scripts/aggression_dashboard_v27/` |
| Port | **5063** manual-only |
| Tailscale | `https://aasthajain.tail174727.ts.net:8445/` → `127.0.0.1:5063` |
| Store | `workspace/analysis/aggression_v27_live/` |
| Label | `PROVISIONAL / RESEARCH-ONLY / LOCAL-10M-FV / NOT AFTER-COST VALIDATED` |
| Live V2 / V2.2 / V2.3 | unchanged; not restarted |

No SOD, Telegram, or executable long/short. Tailscale HTTPS is a display proxy only (`:8445` → local `5063`); V2.2 remains on `:8444`.

## What changed vs V2.2

- Fair value is **B1_W10**, not session-from-open Ridge:
  `fair_value_t = price_{t-10} + Ridge(short pack → price_t − price_{t-10})`
- Alert clock: enter `|z| ≥ 1.5` **and** session residual 2–6 ticks with the same sign as the local residual, and no re-entry while that session sign is unchanged. Session residual is a read-only join of live V2 `signal_log`. Resolve `|z| < 0.5` only after a **5-minute min-hold**, or flip at opposite `|z| ≥ 1`. **10 minutes is a score/display mark, not a flatten** (C1 persistence + same-sign lockout). This is an alert-rate gate from the 2026-09-02 correction tape / L1 autopsy, not an after-cost PASS (`ship_gate` false). A manual V2.7 restart is required for the files to appear.
- Paper scoreboard is 5m / 10m, not 15m / 30m
- LIFO FRIENDLY display is unchanged from V2.2
- Live screen names the paper **ENTRY** (SHORT/LONG at the open last print) and the **EXIT** clocks: `|z|<0.5` after 5 minutes, or flip. The +10 minute line is a mark only. Markers on the live charts. Not executable.
- Each entry→exit is one trade. Table **trade pnl** is last-print fade P&L from entry to that exit. A z-reset closes the trade and a new one may open; it no longer stays exclusive until +10m.
- Session-day banner and Today’s table for the cutover Globex session start at **2026-09-02 12:07 ET** (conviction-gate restart). After the next 18:00 ET open they use the full trading day again. Same-sign re-entry still sees the full 18:00 session.

This is a stability / display candidate from [docs/aggression_v2_short_horizon_fv_experiments.md](aggression_v2_short_horizon_fv_experiments.md). It is **not** an after-cost PASS.

## Launch

```text
py -3 -m scripts.aggression_dashboard_v27.app --host 127.0.0.1 --port 5063
```

Open `http://127.0.0.1:5063` or, on the tailnet, `https://aasthajain.tail174727.ts.net:8445/`. Existing V2.7 processes are not auto-restarted; a manual start is required for a first run.
