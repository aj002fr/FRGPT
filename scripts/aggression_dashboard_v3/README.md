# Aggression V3 — trade-to-trade six-state monitor

Isolated replacement of the old 5053 flow-effort / V2-copy page. This package implements `docs/aggression_v1_monitor.tex`: print clock, tagged day-OLS `beta_ref`, and the six-state banner.

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

The top banner is the current 15-minute window. A second hover banner (default on) shows the same state at the last print at or before the chart timestamp. Hover snapshots come from `flow_dominance_series` (≤4000 points). A compact color bar under Panel 1 shows READY-state runs over the same lookback; WARMING, UNAVAILABLE and UNCLASSIFIED are gaps. Attribution only.

Live mode is a viewer. One publisher on 5053 applies each trade and pushes the same bytes on `GET /api/live/stream`. The page listens; it does not poll `/api/aggression` or `/api/live_status`. Historical mode still uses `POST /api/aggression`.

Every plot and the banner use a trade-to-trade clock. Price is the last traded price of the selected contract (default TY). Wing prints step TotalAggress and hold price. Live `beta_ref` is today's day-tag beta from `tag_beta.json` (flow-history day OLS of day bps on net DV01 for auction tenor / other auction / month-end / tier-1 / regular). The print path reads that file; a background thread updates it after each 17:00 ET close. The superseded 10-session whole-session mean is `ensure_session_mean_beta_ref`. Attribution only, never long/short. File changes do not appear until you restart 5053 yourself.

## Banner states

Each 15-minute window has actual move `d`, aggression-implied move `a = beta_ref * ΔA / tau` and residual `r = d - a` (implied passive), all in ticks. Each becomes `+` above `q = 1` tick, `-` below `-q`, or small (`|x| <= q`). When `a` and `r` are both beyond `q` with the same sign, the aggression share `p_A = |a| / (|a| + |r|)` splits them: `p_A >= 0.60` is AGGRESS DOMINANT, THIN LIQUIDITY, `p_A <= 0.40` is PASSIVE DOMINANT, THIN LIQUIDITY, and anything between is UNCLASSIFIED. Every other state is a lookup on the `(a, d, r)` signs:

| a | d | r | State |
|---|---|---|---|
| small | + | + | SELLER STRIKE |
| small | - | - | BUYER STRIKE |
| small | small | small | QUIET |
| + | + | small | AGGRESS DOMINANT · BUYERS |
| + | + | - | PASSIVE SELLERS ABSORBING BUYERS |
| + | - | - | PASSIVE SELLERS DOMINATING BUYERS |
| + | small | - | PASSIVE SELLERS OFFSETTING BUYERS |
| - | + | + | PASSIVE BUYERS DOMINATING SELLERS |
| - | - | + | PASSIVE BUYERS ABSORBING SELLERS |
| - | - | small | AGGRESS DOMINANT · SELLERS |
| - | small | + | PASSIVE BUYERS OFFSETTING SELLERS |

Combinations the table leaves out fold into the nearest listed state: `a` beyond `q` with `d` and `r` small becomes the matching OFFSETTING state; `a` and `d` small becomes QUIET; `a` and `r` small with `d` beyond `q` (only on exact boundaries) becomes SELLER or BUYER STRIKE. Combinations that `d = a + r` rules out are UNCLASSIFIED. Every READY state has its own colour from `banner.STATE_STYLE`: blues for buying pressure, reds for selling pressure, light grey for QUIET. UNCLASSIFIED has a grey banner and no strip colour.

The old V3 flow-effort helpers now live at `scripts/aggression_flow_effort.py` for research imports only.
