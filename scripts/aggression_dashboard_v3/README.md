# Aggression V3 — trade-to-trade six-state monitor

Isolated replacement of the old 5053 flow-effort / V2-copy page. This package implements `docs/aggression_v1_monitor.tex`: print clock, one 10-session change-on-change `beta_ref`, and the six-state banner.

- Port: `5053`
- Writes: `workspace/analysis/aggression_v3_live/` only (old logs are kept)
- Label: `PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE`
- Manual start only. Do not add a watchdog. Do not edit live V1 / V1.3 / V2.

```bat
Y:\Aastha\SOD\run_aggression_monitor_v3.bat
```

Or:

```powershell
cd Y:\Aastha\market_data_puller
py -3 -m scripts.aggression_dashboard_v3.app --host 127.0.0.1 --port 5053
```

File changes do not appear until you restart 5053 yourself. Existing V3 processes are not killed by this package.

The top banner is the current 15-minute window. A second hover banner (default on) shows the same six-state at the last print at or before the chart timestamp. Hover snapshots come from `flow_dominance_series` (≤4000 points). A compact color bar under Panel 1 shows READY-state runs over the same lookback; WARMING/UNAVAILABLE are gaps. Attribution only.

Live mode is a viewer. One publisher on 5053 applies each trade and pushes the same bytes on `GET /api/live/stream`. The page listens; it does not poll `/api/aggression` or `/api/live_status`. Historical mode still uses `POST /api/aggression`.

Every plot and the banner use a trade-to-trade clock. Price is the last traded price of the selected contract (default TY). Wing prints step TotalAggress and hold price. `beta_ref` is the mean of the last 10 completed Globex sessions' OLS of `ΔP` on `ΔA`. The six READY headlines are QUIET, BALANCED, AGGRESS DOMINANT, IMPLIED PASSIVE DOMINANT, JOINTLY DRIVEN, and CONTESTED. Attribution only, never long/short.

The old V3 flow-effort helpers now live at `scripts/aggression_flow_effort.py` for research imports only.
