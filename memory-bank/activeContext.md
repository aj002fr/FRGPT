# Active Context

## Recent Changes (2026-09-24)

### Dashboard reliability plan: Release 0 (R0.2–R0.4 staged for the 17:00 ET window)

- Plan: `c:\Users\aasth\.cursor\plans\dashboard_reliability_fixes_4e8509b6.plan.md`. This work lives in the C: checkout `C:\dev\market_data_puller`, branch `ops/local-runtime` (Y: history, snapshot commit `6715631` of the 11:05 Y: tree, parallel V3 fix merged in `ae0cd42`). Y: stays read-only except Release 0 deploys through `ops\release0\deploy_release0.ps1`, which refuses if a Y: file changed since the `yshare-sync` merge base.
- R0.1 done 10:58: v27 (5063) and supply weeks (5065) run headless from tasks `MDP R0 v27 5063` / `MDP R0 supply 5065` (logs `%LOCALAPPDATA%\market_data_puller\logs\`); `:8445`/`:8447` answer.
- R0.2 V3 (staged): vectorized exact matcher; the 11:37 bisect loop is now `_matched_incoming_reference` and the fallback. On the 9/23 US-open fixture (`C:\dev\mdp_golden\v3\20260923`) all three implementations agree (original loop on 2 h windows, bisect loop on 30 min windows, synthetic adversarial cases). 2 h window at the open: full overlay 20–80 ms per symbol, cached rebuild 10–28 ms per symbol (72 ms all four); the original loop took minutes. Publisher: coalesced rebuilds (60 s; 5 s warming), off-lock prepare, one JSON encode, broadcasts under the lock, 2000-print catch-up batches.
- R0.3 V1 (staged): ten-day Databento bars stay in memory only; live AccessAPI writes unchanged.
- R0.4 5064/5065 (staged): events from Postgres `economic_events` (read-only role) via `EventsCache`; output identical to the SQLite reader (2,124 events, same order); `/api/events` 17 ms vs 0.7–13 s over the share. Postgres is a one-time copy of the 9/23 12:26 SQLite build until Yaman's pipeline writes it.
- Deploy at 17:00, in order `v3`, `v1`, `events`: `powershell -File ops\release0\deploy_release0.ps1 -Item <item> -Action deploy`; rollback `-Action restore`. 5053 and 5064 move to headless tasks (`MDP R0 v3 5053`, `MDP R0 flow 5064`); V1 restarts through its launcher loop.
- Finding (not changed in R0, identical-output rule): the Databento cache `ts` that V3 stores as hist runs 0.2–14 ms (median ~1 ms) behind AccessAPI exchange timestamps, so the 1 ms window pairs only ~50–65% of the same trades; where hist and live overlap, unmatched live prints sit next to their hist twins. For Milestone 5/9.
- Deploy content is the fixed tag `release0` (`fe00867`), not the branch head (the head's 5064/5065 need Milestone 2 modules). A simulated deploy onto a copy of the Y: tree confirmed V1/V3/5064/5065 import cleanly with it.
- Coordination: a parallel session edited V3 on Y: again at 12:41–12:57 (live_publisher, core, app, dashboard.js, index.html, tests; not yet restarted or documented). The deploy guard refuses V3 until that work is re-synced and merged; V1 and events are unaffected.

### Canonical flow writer, local runtime, supervisor (Milestones 1–4, 6 on `ops/local-runtime`)

- `scripts/flow_store/` (writer) and `scripts/dashboard_runtime/` (serve/cache/supervisor) are built and tested (24 flow_store, 6 supervisor, 4 runtime tests). Shadow run 2 (`MDP shadow flow writer` task, DBs in `C:\dev\mdp_shadow`, Databento off) started 13:22 ET; the gate needs 08:20–10:30 ET tomorrow and tonight's halt/reopen, then `shadow_compare` clean over sampled intervals.
- Shadow run 1 (13:02–13:21, kept in `C:\dev\mdp_shadow_run1`) found three writer bugs, all fixed before run 2: stream de-duplication dropped real same-packet identical fills (tape never had trades historical lacked, so no de-dup at all now); per-root staleness opened false gaps because TBBO carries only trades (coverage now follows the connection); AccessAPI `/historical/trades` returns empty JSON for microsecond bounds (fetches use whole seconds and filter locally).
- Read path: `flow_history.read_connection` (thread-local, `query_only`, no DDL/lock) for V1 queries, 5064 and 5065; `history_status` memoized 60 s; PROVISIONAL feed badge; `backfill_accessapi.py` refuses the canonical-owned DB.
- Local runtime: waitress option on 5058/5063/5064/5065, rotating and slow-request logs, response cache for 5064/5065 endpoints, V2.2/V2.7 background producers (requests read the latest evaluation). `docs/local_runtime_paths.md` classifies every data path; risks: V1+V2.7 share `banner_alerts.sqlite`, V2.7 reads V2's signal log (V2 down since 9/23 19:05), Databento key on Z:, 5065 OHLCV via this PC's Tailscale IP.
- Supervisor profiles in `ops/supervisor/profiles.json` (rehearsal 6053–6065, production 5053–5065). Local venv `C:\dev\market_data_puller\.venv` from `ops/requirements-dashboards.lock.txt` (Y: freeze + waitress + psutil).

### V3 5053 CPU spin quick fix (restarted 11:47 ET)

- Symptom: 5053 was delayed and not responding. The 9/23 21:07 process (PID 34760) had used one full core for 14 hours (~52k CPU-s, ~3 GB). `py-spy dump --nonblocking` showed `v3-live-rebuild` holding the GIL in `print_reconcile._matched_incoming`, called from `apply_live` in `_live_display_trades`.
- Cause 1: `_matched_incoming` scanned every stored print with the same side/size/price for each incoming print, with no time bound. Live prints carry no `sequence`, so all ~30k live TY prints in the 2-hour lookback took that path on every rebuild (minutes each).
- Cause 2: the rebuild never stopped. The cached curve beta was from the 21:05 ET bucket, so every rebuild saw `bucket_stale` and kicked a beta thread. `ensure_beta_ref(compute=True)` returned the same cached entry without computing, and `_kick_beta_ref` still called `offer_history()`, which queued the next rebuild. `beta_ref.json` had not been written since 21:08:36.
- Fix (V3 package only): the matcher uses time-sorted buckets and a ±1 ms bisect, with the same greedy order and tie-break (tested against the old scan on 40 random seeds). `ensure_beta_ref` has a new `refresh_stale` kwarg; a failed refresh keeps last-good; each session is mask-sliced instead of full-frame copied. `_kick_beta_ref` refreshes once per chip set per process, then holds the value (the user chose a frozen beta for this session), and calls `offer_history()` only after a fresh OK beta. `_snapshot_tenday_trades` and `_snapshot_tenday_path` return shared frames instead of deep copies.
- Restart (user approved): stopped cmd 32840 and its children, relaunched `Y:\Aastha\SOD\run_aggression_monitor_v3.bat` (cmd 34424, Python PID 19800). Beta refreshed at 11:48:52 for the 11:45 ET bucket (curve 3.16e-08, 10 sessions) and stayed held through later buckets. Idle CPU ~0.6% of a core; ~17% averaged over a 4-minute backfill cycle. Live trade events are ~0.6 s behind the exchange; `/api/live_status` answers in ~0.2 s.
- One-time startup cost: once the 10-session trades load, the Panel 5 path build takes ~80 CPU-s. At 11:55 it starved the AccessAPI websocket keepalive (1011 ping timeout, reconnected within a second).
- Open for the performance chat:
  - Every rebuild still broadcasts the full picture as a `history` event (41.5 MB opening snapshot; up to 80k points on each of four charts), because `total_aggress` is rebased at the moving 2-hour start. The rebuild cost (~12–23 CPU-s) is mostly `iterrows` panel building and JSON encoding.
  - The backfill gets Databento `422 data_start_after_available_end` each cycle, but `covered_end` advances anyway, leaving historical holes. The session store has no rows for the last 2 hours, so rebuilds fall back to the backfill window.
  - SSE writer threads block in `write` on slow Tailscale or Cursor clients.
  - After the 18:00 ET session roll, the held beta locks to the first evening bucket that fits (~18:05). Restart V3 once the fuller fix lands, or before the US session.

## Recent Changes (2026-09-23)

### Gamma Tracker V6 restarted

- 8055 and the v3 writer were both down. Started `Z:\uikitxv2\OptMonitor\run_gamma_tracker_v6_stack.bat` at 21:12 ET. Dashboard PID 22440 on `127.0.0.1:8055` (homepage 200). Writer PID 36564, same nosite launcher, DB `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db`. Latest snapshot `2026-09-23T21:14:22`. Live Options API was healthy. CMED/Telegram not started.

### V3 started

- Started at 21:06 ET via `Y:\Aastha\SOD\run_aggression_monitor_v3.bat` on `127.0.0.1:5053` (listener PID 34760). `/api/live_status`: AccessAPI connected, mode `live`, market open. Label `PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE`. V1 / V2.2 / other monitors not touched.

### V1 left running; V2.2 started

- V1 was already up on `127.0.0.1:5050` (listener PID 7428, watchdog launcher, homepage 200). It was not restarted.
- V2.2 started at 21:03 ET via `Y:\Aastha\SOD\run_aggression_monitor_v22.bat` on `127.0.0.1:5058` (listener PID 16836, window cmd 21488). `/api/signal_status`: AccessAPI connected, source `accessapi`, ~20k rows. V2 / V2.7 / V3 / 5064 / 5065 not touched.

### Supply-weeks return distributions include the 5Y auction day

### Supply-weeks return distributions include the 5Y auction day

- The 5065 return-distribution section now has a 5Y auction-day row beside the 2Y row. Each row is 08:00–13:00, 10:00–13:00, and 13:00–17:00 on that tenor’s auction date. Measure, year, and month-end annotation still apply to every chart. Static HTML dumps stay unchanged. 5065 needs a manual restart for the new row to appear.

### V2.2 / V2.7 restarted for LIFO lean color

- Manual restart at 08:00 ET so the display-only LIFO FRIENDLY green/red banner is live. V2.2 5058 AccessAPI connected, asset `20260922_v22_lifo_lean_color`. V2.7 5063 B1_W10, asset `20260922_v27_lifo_lean_color`. V1 5050 / V2 5052 / V3 5053 / 5064 / 5065 left running.

## Recent Changes (2026-09-22)

### V2.2 / V2.7 LIFO FRIENDLY lean color (display-only)

- Isolated V2.2 and V2.7 LIFO FRIENDLY banner stays the same text. Color is green when `residual_z < 0` (fade long) and red when `residual_z > 0` (fade short). Asset versions `20260922_v22_lifo_lean_color` / `20260922_v27_lifo_lean_color`. No Python fire-rule change. Live dashboards not restarted. V1/V2/V2.6/V3/SOD untouched.

### V3 live stream (FRG distribution)

- Live 5053 no longer polls `POST /api/aggression` every second. One publisher applies each trade and broadcasts the same SSE bytes on `GET /api/live/stream`. The page extends traces. Historical mode still posts. Empty-tape WARMING is not published. Past strip colors stay until a hole-fill or `beta_ref` revision. Live 5050 / V2 untouched.

## Recent Changes (2026-09-21)

### V6 upcoming-events banner uses Yaman events.sqlite

- Live `gamma_tracker_dash_v6.py` banner again lists upcoming US prints. Source is `Y:\Yaman\Fundamentals\Economic Events\Data\events.sqlite` (`events` table, read-only). One row per parent/time: coupon note/bond auctions, EventScorer families, and High/Very High releases. Bills, TIPS, FRN, and Fed speeches are omitted. `economic_events.db` has no rows after 2026-08-14, which is why the strip had gone blank. Event-conditioned heatmap scores still use the old DB via `EventScorer`. Writer and other monitors untouched.

### Live 2Y/5Y/7Y supply-weeks viewer (5065)

- Isolated Flask `scripts/supply_weeks_live/` on manual-only port `5065`. Label `DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL`. The 2Y comparison clock is 13:00 ET. Rows are 08:00–13:00, 10:00–13:00, and 13:00–17:00. Each cell is p25 / median / p75. Non-supply can drop Tier-1 and last-3-session month-end. The week month-end filter applies to supply rows. 2D heatmaps, OLS cards, and the return distribution stay. The week clock is off the page. Tailscale Serve exposes it at `https://aasthajain.tail174727.ts.net:8447/` → `127.0.0.1:5065` only. 5064, live V1, SOD, and the two static HTML dumps stay unchanged. Do not move `:8443`–`:8446`.

### V3 Panel 1 state strip

- Compact color-coded READY-state bars sit above TotalAggress (280px), same lookback as P1, from `flow_dominance_series`. WARMING/UNAVAILABLE are gaps. Hover still uses the existing hover banner. Live 5050 / V2 untouched.

### V2.7 launched

- Isolated Aggression V2.7 started on `127.0.0.1:5063` at 08:42 ET (Python PID 45952, AccessAPI connected, B1_W10 fit 2026-09-21). Store `workspace/analysis/aggression_v27_live/`. Label `PROVISIONAL / RESEARCH-ONLY / LOCAL-10M-FV / NOT AFTER-COST VALIDATED`.
- V1 5050, V2 5052, V3 5053, V2.2 5058, and flow-history 5064 were left running. No SOD/Tailscale/Telegram changes.

## Recent Changes (2026-09-20)

### V3 path crashed on duplicate print times

- `apply_beta_ref_path` did `float(group.loc[ts, total_aggress])`. Same-millisecond prints return a Series and the live API 400'd (`cannot convert the series to float`). First priced row is now taken by position. Live 5050 not edited.

### Flow-history viewer uses Yaman events.sqlite

- Isolated 5064 now reads `Y:\Yaman\Fundamentals\Economic Events\Data\events.sqlite` (`events` table). Daily rows are Globex sessions: Sun 18:00–Mon 17:00 ET is Monday, closed Fri 17:00–Sun 18:00. Top feed badge is LIVE only when the session is open and the last 1-minute bar is ≤180s old. Store remains 1-minute completed bars from every trade print. Coupon supply / month-end / tier-1 / weekday tags only — no residual QUIET label. OLS, distributions, and today analog stay descriptive. 2022+ history is in `flow_history.sqlite` (source `accessapi_2022`) up to the 2026-06-12 V1 seam. Load is one `/api/bootstrap`; after that the page polls `/api/today` every 15s (current session only). Live V1 5050 untouched.

## Recent Changes (2026-09-17)

### V3 hover banner

- Second banner on 5053 shows the six-state at the hovered chart timestamp (15-minute window ending at the last print ≤ hover). Live top banner stays current. Checkbox `Banner on hover` defaults on. Series is `flow_dominance_series` (≤4000). Gap reset is as-of-aware so a later hole does not wipe earlier hover states. Live 5050 not edited.

### V3 WARMING was a false config-reset

- Live 5053 stayed on `WARMING` / “Banner window reset after a session or configuration change” while `beta_ref: unavailable`. The config-change check used a process-global prior key that included `beta_sessions`, so a TY-only cache/request and the default FV+TU+TY+US chips flipped the key every refresh and hid the real gate.
- `beta_ref.json` was a single TY-only payload (`tod_bucket` 13:40). Curve peek with `compute=False` never hit. READY still needs the curve last-3h mean.
- Banner no longer short-circuits on a foreign config key. Cache is multi-entry (TY and curve coexist) and last-good beta is reused across 5-minute TOD buckets. Status line shows the beta reason. Live 5050 not edited.
- Restarted 5053 at 14:05 ET (PID 67492 / listener 56408). AccessAPI connected. First curve `/api/aggression` is WARMING with `Reference beta is still computing.` (honest reason). Live 5050 PID 24880 left running.

### V3 live/hist merge is hole-only

- Session store no longer cuts live at `hist_max` or exact-ts concat. `print_reconcile.py` applies live first, then Databento hist: 1ms one-to-one match on size/side/tick-price; keep stored; insert hist only for left-of-store and interior holes. Interior match rate < 0.5 skips unmatched hist. Hist is not inserted after the live tail. Panel 2 resample uses `origin=epoch`. Tests in `test_v3_trade_to_trade.py`. 5053 not restarted. Live 5050 not edited.

### V1 Panel 1 is trade-to-trade last price

- Live V1 (5050) Panel 1 now uses the print clock: last traded price of the selected symbol and session `TotalAggress(print)=cumsum(sign*size*$DV01)` (first print = 0). Other-symbol prints update aggress and hold the last selected-symbol trade price.
- Panels 2–5, OLS fair value, the dominance banner, and `flow_history.sqlite` stay on completed 1-minute bars. Session `_SESSION_TICKS` is not trimmed.
- Files: `scripts/aggression_dashboard/{core,app,panels}.py`. Tests: `tests/test_v1_tick_aggress.py`, `tests/test_v1_panel1_hover_stable.py`. Restarted 5050 at 08:52 ET (PID 24880, AccessAPI connected). Live P1 title `TotalAggress vs Last Traded Price`, `bucket_interval=print`. Watchdog left running. V1.2/V1.3/V2/V2.x/V3 not restarted.

## Recent Changes (2026-09-16)

### V3 Panel 2 is chart-smoothed

- Live 5053 Panel 2 was an EMA(10) of ±100 prints drawn vs wall clock, so a 2h lookback filled the pane. The plotted green line is now a 30s time-weighted EWM of `buysell_raw`, thinned to 4000 points, y-range ±100. Spec EMA(10) is unchanged on the print frame. Banner / other panels untouched.

### V3 default is TY vs TY

- Default price TY, default aggression chip TY only. `beta_ref` is the TY-vs-TY 10-session mean of last-3-hour TOD `ΔP`-on-`ΔA` slopes. FV/TU/US chips stay optional. Live 5050 unchanged.

### V3 is now the trade-to-trade six-state monitor

- Replaced `scripts/aggression_dashboard_v3/` in place on port 5053. Live V1/V1.3/V2 were not edited. The running 5053 process was not killed; restart it yourself via `Y:\Aastha\SOD\run_aggression_monitor_v3.bat`.
- Print clock, interim 10-session `ΔP`-on-`ΔA` `beta_ref`, six-state banner. Writes stay under `workspace/analysis/aggression_v3_live/` (old logs kept). Label `PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE`.
- Flow-effort helpers moved to `scripts/aggression_flow_effort.py`. Tests: `tests/test_v3_trade_to_trade.py`, `tests/test_flow_effort.py`.

### V1 six-state banner discussion spec

- Stored the revised V1 measurement note at `docs/aggression_v1_monitor.tex` (charts as of 20 Aug 2026; banner revised 15 Sep 2026).
- This chat's reference: six READY states `QUIET / BALANCED / AGGRESS DOMINANT / IMPLIED PASSIVE DOMINANT / JOINTLY DRIVEN / CONTESTED`, plus `WARMING / UNAVAILABLE`.
- Banner uses fixed `beta_ref` = 1 yield bp per $1m signed DV01. Identity `d = a + b` in ticks. `q=1` tick, `r=1.5`. Session and 10-day OLS fits stay chart diagnostics only.
- Live 5050 is unchanged: still the four-state OLS-beta banner in `docs/aggression_v1_flow_dominance_banner.md`. The spec is not authorization to edit or restart V1.
- Cursor rule: `.cursor/rules/v1-six-state-banner-spec.mdc`.
- Panel 2 now specifies a fixed readability caption and percentage map (`0`, `±33`, `±50`, `±100`). Chart-only; not a banner sentence. Live 5050 still unchanged.
- Banner `beta_ref` (spec only): interim = rolling mean of last 10 completed sessions; intended = same mean on the last 3 hours before display time; later may be day-tagged (auction, month-end; Duffie–Krishnamurthy LSE 2024). Live 5050 not edited.
- Spec clock is trade-to-trade everywhere: last traded price (not 1-minute / not mid); aggression and price increments are print-to-print. Live 5050 still 1-minute.
- One `beta_ref` only (10-session mean of ΔP-on-ΔA slopes). Banner, Panel 4, and Panel 5 all use it; the charts do not fit a separate session or pooled beta. Live 5050 not edited.

## Recent Changes (2026-09-15)

### Isolated V1.3 tick TotalAggress

- Existing V1.2 on 5056 is the RANGE-caution monitor, so the per-print P1 copy is **V1.3** on port **5055**.
- Package `scripts/aggression_dashboard_v13/`. Panel 1: `TotalAggress(print)=cumsum(sign*size*$DV01)`, first print = 0, linear connect. Session tape `_SESSION_TICKS` is not trimmed. P2–P5 and the banner stay 1-minute.
- Writes only `workspace/analysis/aggression_v13_live/`. No V1 sqlite, no banner-alert writes. Label `PROVISIONAL / RESEARCH-ONLY / TICK-TOTALAGGRESS / NON-EXECUTABLE`.
- Manual start: `Y:\Aastha\SOD\run_aggression_monitor_v13.bat`. Do not start unless asked. Live V1 (5050) and V1.2 (5056) were not edited or restarted.
- Tests: `tests/test_v13_tick_aggress.py` 4/4.

### V1 TotalAggress flat-line / tail fix

- Panel 1 no longer forward-fills TotalAggress onto mid-only minutes (`ffill_aggress=False`, Plotly `connectgaps=false`, step `hv`).
- Session ingest rewrites the forming minute every 1s; backfill sets `refold_tail` to re-fold the last 15 minutes once. `upsert_minute_bars` merges by index; `printed_minute_bars` drops zero-print buckets so a gappy resample cannot zero closed bars.
- In-memory holes are healed from `flow_history.sqlite`. Backfill ticks are trimmed only after the store is ready.
- Live P1 y-range uses a 2% deadband. Running 5050 was not killed; a manual V1 restart is required for the files to appear.
- Confirm: at 08:43 ET live P1 03:00–08:00 ET matched sqlite (300 unique cumsums, span 757951). The earlier laser-flat screenshot was ffill-across-a-hole, not true zero flow.
- Tests: `test_v1_panel1_hover_stable.py`, `test_v1_session_minute_bars.py`, `test_v1_flow_history.py`.

## Recent Changes (2026-09-14)

### V6 implementation note

- Added `docs/gamma_tracker_v6_implementation.md`: concept, infrastructure, units, data history, live formulas, event-ownership rule, implemented vs ideated. Does not change 8055.

### Isolated V1 flow-history viewer

- Read-only Flask viewer on `127.0.0.1:5064`. Does not write `flow_history.sqlite` or touch live V1/V2.7.
- Daily bid vs ask volume, event tags (auction / month-end / NFP / CPI / FOMC), day ticks, a full-history ticks-vs-net-flow scatter, and a descriptive bid-heavy vs rally card.
- Files: `scripts/aggression_flow_history_dash/`. Tests: `tests/test_v1_flow_history_dash.py`. Optional launcher `Y:\Aastha\SOD\run_v1_flow_history_dash.bat`.

### V6 event score matches ownership rule

- Only still-ahead events that print before this expiry (12:00 CT) contribute.
- Same-day expiry uses unused `1st` weights. Printed events score 0 (no 12h afterglow).
- Dropped 0.3 floor and tier τ_before floors. Contribution table uses the same kernel.
- Files: `event_score.py` (live + standalone), `gamma_tracker_dash_v6.py`, standalone `dash.py`, scientific record. Tests: `tests/test_event_score_ownership.py` 5/5. Restarted live 8055 at 10:53 ET (same nosite launcher + v3 DB). Writer untouched.

### V1 aggress buy/sell volume history

- Durable 1-minute store at `workspace/analysis/aggression_v1_live/flow_history.sqlite`.
- Per symbol: `buy_contracts` / `sell_contracts` (took ask / hit bid) plus matching `$DV01` flow.
- Seeded from existing `trades_v2_*.parquet` cache (2026-06-12 .. 2026-09-11). Live V1 restarted 10:41 ET (PID 28816, AccessAPI connected) and is appending completed minutes. Watchdog cmd left running.
- Endpoints: `GET /api/flow_history`, `GET /api/flow_history/status`.
- Files: `scripts/aggression_dashboard/flow_history.py`, `app.py`. Tests: `tests/test_v1_flow_history.py`.

### V2.7 monitor started

- Isolated Aggression V2.7 started on `127.0.0.1:5063` at 10:01 ET (Python PID 32372, AccessAPI connected, B1_W10 fit 2026-09-14). Store `workspace/analysis/aggression_v27_live/`. Tailscale `:8445` already pointed here.
- V1 on 5050 left running. V2/V2.2/V2.6 were down and were not started.

### V6 wing-spread ATM column is n/a

- Heatmap ATM cell is blank in wing-spread mode (`iv − atm`). Bucket 0 is not backbone ATM, so that rank was a leftover mismatch.
- Same skip on the event-distribution surface boxes. Raw-IV mode still colors ATM.
- Files: `gamma_tracker_dash_v6.py`, standalone `dash.py`, `docs/gamma_tracker_v6_scientific_record.tex`. Restarted live 8055 at 09:37 ET (PID 15444, same nosite launcher + v3 DB). Writer untouched.

## Recent Changes (2026-09-11)

### V1 banner uses local 15-minute ticks

- Dropped session-expanding z for the live/hover banner. Classification is the raw 15-minute aggress vs residual sums, 1.5 ratio, Quiet at 2 ticks. Dynamics compare |15m contrib| vs 5 minutes earlier with a 1-tick deadband. Restarted 5050 at 13:49 ET (PID 39096, AccessAPI connected). Offline dominance study untouched.

### V1 banner-on-hover

- Local **Banner on hover** checkbox on V1 (5050). Hovering any of the five panels shows the completed-bar flow-dominance banner for that timestamp in a preview strip. Live top banner is unchanged. Series uses full-session z, then lookback clip. Restarted 5050 at 12:01 ET (PID 44332, AccessAPI connected). No other monitors or stores touched.

### V1 panel 1 hover is session-absolute

- `build_total_aggress_panel` calls `align_flow_and_price(..., rezero=False)`. Lookback only clips rows.
- Fair-value OLS still uses the default `rezero=True`. Live V1 on 5050 needs a manual restart for hover values to stop shifting.

## Recent Changes (2026-09-10)

### V1 session 1-minute publisher

- Live V1 no longer copies the full Globex tape on each `/api/aggression`. A background thread upserts new prints into per-symbol 1-minute bars; HTTP combines those bars, applies the frozen OLS/banner, then clips lookback for display.
- 10-day source is stored as 1-minute bars; raw tick frames are dropped. Session backfill ticks are trimmed to 20 minutes after the fold.
- Files: `scripts/aggression_dashboard/core.py`, `app.py`. Tests: `tests/test_v1_session_minute_bars.py`.

## Recent Changes (2026-09-09)

### V1 live payload cache (Tailscale tabs)

- Live `POST /api/aggression` serves the last identical payload for 1s, then falls back to the existing in-flight coalesce. Extra Tailscale viewers no longer each rebuild the full session.
- File: `scripts/aggression_dashboard/app.py`. Tests: `tests/test_v1_live_result_cache.py`. Restarted 5050 at 17:10 ET (PID 27352).

## Recent Changes (2026-09-08)

### LIFO literature L1–L4 run (offline)

- Script `scripts/aggression_v2_lifo_literature_research.py` + tests. Writes `workspace/analysis/aggression_v2_lifo_literature/`.
- Track P: same-bar R² 0.25 vs lead-1 ~0 — contemporaneous flow–price is not a 15m zone model. Track F: two-sidedness persists only +2.6pp.
- L1 `mod_twosided` −2.1pp FAIL. L3 flippy +1.2pp FAIL. L2 absorb_extreme zone +8.3pp PASS but fade hit 48% FAIL; lift is already-swinging tape. H5 still −4.3pp.
- `ship_gate` false. No live V1/V2/V2.7 edits or H5 retune.

### Flow forecast vs two-stage price map (offline)

- Past signed flow does not forecast future signed flow at any tested \((W,H)\). Sign hit is a coin flip. Stacking lookbacks does not help.
- If future flow were known, mapping it to price is easy (validation oracle \(R^2\) \(0.25/0.40/0.52\) at \(H=1/5/15\)). Two-stage \(\lambda\times\hat F\) and direct past→price are both \(\approx 0\). Bottleneck is stage 1.
- Script: `scripts/aggression_v2_lifo_flow_forecast_research.py`. `ship_gate` false.

### Path follow-up (offline)

- Trail meat → more meat, not the zone. Last-15m two-way does not hold on validation. Flow inside trail meat adds nothing. Fade of last 3-tick push is 53%. US morning 08:00–12:00 ET letters the zone on all three splits. `ship_gate` false.

## Recent Changes (2026-09-07)

### LIFO-zone study (offline)

- New label: 15m two-way box, meat ≥3 ticks, |net|<8. Zone rate 9.9%. Binding constraint is the reversal, not meat.
- Frozen H5 / live LIFO FRIENDLY: validation 4.2% vs 8.5% baseline (−4.3pp). Quiet leftover ≠ LIFO clip zone. High intensity is slightly *more* zonal. Edge clock n=3. `ship_gate` false. Live banners unchanged.

### V2.2 / V2.7 lineage note

- Added `docs/aggression_v22_v27_from_v1.tex`: companion to the V1 measurement note. Covers how V2.2 (session Ridge fade + LIFO FRIENDLY) and V2.7 (B1_W10 + conviction gate) were ideated from V1. Docs only; no live process or store changes.
- V2.7 goal rewritten as a 10-minute consistency check (V1’s question on the last burst), not a fade target. Paper clock is diagnostic only. Updated `docs/aggression_v27_monitor.tex` Goal / How to read.

### V1 restart + crash keep-alive

- Live V1 on 5050 was down since the 2026-09-05 19:08 ET network-share OpenBLAS in-page crash. Restarted on request.
- `Y:\Aastha\SOD\run_aggression_monitor.bat` now loops after a crash instead of pausing. `/watchdog` skips the browser.
- Scheduled task `AggressionMonitorV1Watchdog` runs `watch_aggression_monitor_v1.ps1` every minute and starts V1 only when 5050 is down. Does not kill or restart V2/V2.2/V2.6/V2.7/V3.

## Recent Changes (2026-09-03)

### Standalone Gamma Tracker V6 package

- Added portable package `Z:\uikitxv2\OptMonitor\gamma_screener\gamma_tracker_v6_standalone\` (writer, dash copy, slim store, seed).
- No imports of `gamma_tracker_v2` / `gamma_tracker_v3`. Seeded `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v6.db` (147 MB) read-only from the live v3 file.
- Live 8055, `gamma_tracker_dash_v6.py`, `gamma_tracker_v3.py`, and `gamma_tracker_v3.db` were not edited or restarted.

### V2.7 × V1 profitable zones (analysis only)

- Full available tape, not the 14 live-week trades alone: 185 current-rule B1_W10 OOS trades (2026-02-03..2026-06-05) plus 14 live-week = **199**. Jun 6–Aug 30 has V2 session logs but no B1_W10 nowcast.
- Frozen E / E×S. One nominee: `AGGRESS_SINGLE` (n=7, +7 ticks, 57%). Historical-only has **no** nominee (`AGGRESS` +43 at exactly 50% hit). `IMPLIED_PASSIVE` still loses (−7).
- `ship_gate` false. Not a live filter. 5063 and 5050 were not edited or restarted.

### V1 banner / V2.7 alert SQLite

- New isolated DB `workspace/analysis/aggression_v1_v27_banner_alerts/banner_alerts.sqlite`.
- Live V1 5050 appends a row only when the descriptive banner state changes. V2.7 5063 appends ENTRY/EXIT and stamps `v1_state_at_alert`.
- Does not change the banner or the V2.7 clock. Manual restart of 5050 and 5063 required for the hooks to run.

### V2.7 live-page LaTeX note

- Added `docs/aggression_v27_monitor.tex` (V1-style): isolation, B1_W10, prior-blend \(z\), conviction gate, paper clock, session banner, LIFO overlay, and 2026-08-31 / 2026-09-02 provenance. Pointers from `docs/aggression_v27_local_fv.md` and the V2.7 README. No live process or store writes.

### V1 Databento backup handoff

- Shared `scripts/databento_live_backup.py` now serializes `start()`/`stop()` and waits 15s for a stopping worker instead of failing after 3s.
- V1 `live_buffer._activate_backup` leaves `source=accessapi` until backup `start()` returns True.
- Live V1 `POST /api/aggression` coalesces identical in-flight rebuilds so stacked 1Hz tabs share one pandas pass.
- File changes only until an explicit restart. Port 5050 was restarted on request at 2026-09-03 ~09:20 ET; V2.2/V2.6/V2.7 were not restarted.

## Recent Changes (2026-09-02)

### V2.7 high-conviction entry gate

- Isolated V2.7 on port 5063 now opens only when local `|z|≥1.5` and session residual is 2–6 ticks, same sign, and not the same session sign as the last finalized episode this Globex session.
- Session residual is stamped after `predict()` from read-only `workspace/analysis/aggression_v2_live/signal_log_*.csv`. Missing session → `SESSION_UNAVAILABLE` (do not open on local z alone).
- Session-day banner / Today’s table for this Globex session start at **2026-09-02 12:07 ET**. After the next 18:00 ET open they use the full trading day. Same-sign re-entry still sees the full 18:00 session.
- Exit clock: fade through 0.5 after 5m, or flip at opposite `|z|≥1`. **10m is a score/display mark, not a flatten.** Status label unchanged. `ship_gate` false.

### Executable PnL correction cells (offline)

- `--mode corrections` scores C1–C4 / I1–I8 / combo / L1 on cached TY TBBO only. Writes `workspace/analysis/aggression_v2_executable_pnl/corrections/`.
- Binding readout: G=2 is a persistent overnight regime; bid/ask +2/−2 is a 1-mid-tick stop; mid-space (3,2) raises P(TP before SL) but does not create after-cost expectancy.
- Those cells authorized the V2.7 alert-rate gate, not an after-cost PASS.

## Recent Changes (2026-09-01)

### Session-gap executable PnL (offline)

- New script `scripts/aggression_v2_executable_pnl_research.py` treats session `|residual|/tick >= G`, same-bar B1_W10 agree, V1 aggress-with-fade veto, and price +N/−X as the actual open/close. Writes only under `workspace/analysis/aggression_v2_executable_pnl/`.
- Official today tape uses AccessAPI TY TBBO (long ask / short bid). Overnight session residual stays several ticks cheap, so full-session G=2 is almost always-in and loses after cost. Live 12:07 / 12:15 V2.7 longs are DISAGREE / BELOW_G and do not open.
- Live V2.7 on 5063 stays B1_W10. Not a ship gate.

### V2.7 session-day ticks banner

- Isolated V2.7 now shows ticks captured and trade counts since the last 18:00 ET Globex open. Today’s table uses that same session window. Research display only.

### V2.7 on-screen ENTRY / EXIT

- Isolated V2.7 UI now shows a research-only ENTRY / EXIT / NOW strip and chart marks. Same clock: enter `|z|≥1.5`, z-exit `|z|<0.5` after 5m, 10m score clock. Not executable. Live process needs a manual restart to pick up the files.

## Recent Changes (2026-08-31)

### V2.7 alert-clock grid (offline)

- New script `scripts/aggression_v2_alert_clock_research.py` replays B1_W10 alerts under 38 frozen clocks (wall / prints / volume / tick-minutes / TOD-adaptive / hybrid). Writes only under `workspace/analysis/aggression_v2_alert_clocks/`.
- Fixed print/volume counts on 1-minute bars fire in ~1 minute and are not usable. Usable 5–15m clocks still have median % via price = 0. `tickmins_3` is a research lead (holdout hit 53%, all-OOS 50%, ~0.1 tick). Keep live V2.7 on 10-minute wall time. `ship_gate=false`. No MBP-1 update clock.

### Isolated V2.7 local 10m fair value

- New manual-only copy `scripts/aggression_dashboard_v27/` on port **5063**, store `workspace/analysis/aggression_v27_live/`.
- Live FV is B1_W10 (10-minute rolling anchor + short pack). Alert clock: enter 1.5 / exit 0.5 / min-hold 5 / deadline 10m. LIFO FRIENDLY kept from V2.2.
- Named V2.7 because V2.3 already exists (move-exhaustion, 5059). Live V2/V2.2/V2.3 were not edited or restarted.

### Short-horizon V2 fair-value research (offline)

- New script `scripts/aggression_v2_short_horizon_fv_research.py` explores a forward-price Ridge (Track A) and a local rolling-anchor nowcast (Track B), plus a frozen flip-vs-sticky lean pack (Track C) for a 5–10 minute / ≤3-tick hold.
- Writes only under `workspace/analysis/aggression_v2_short_horizon_fv/`. Live V2.2 stays on session-from-open FV. Not a retune of `aggression_v2_forward_return/`.
- Holdout: Track A does not beat random walk in any useful way; `W=5` is leaked; sweet-spot display candidate is `B1_W10` with `|z|≥1.5` enter, `|z|<0.5` exit, 5-minute min-hold. No ship gate.

## Recent Changes (2026-08-24)

### Gamma Tracker V6 scientific record

- Added `docs/gamma_tracker_v6_scientific_record.tex`, a V1-style measurement note for live Gamma Tracker V6 (port 8055): stack, surface construction, RR/fly/backbone, percentile heatmap, event scores, and reading habits.
- Documentation only. No OptMonitor writer/dashboard edit, no `gamma_tracker_v3.db` write, no SOD/Tailscale/Telegram change.

## Recent Changes (2026-08-22)

### V1 start time is the trading-day open

- Live V1 `default_start_time()` / session backfill now use the current Globex open: 18:00 ET today if the clock is at or after 18:00 ET, otherwise yesterday 18:00 ET.
- Panel 5's 10-business-day source window starts at the first included date's 18:00 ET open (prior calendar evening).
- The V1-only note documents this. Port 5050 was not restarted; a manual restart is required for the live page to pick it up.

## Recent Changes (2026-08-21)

### Conceptual states are now price-path only

- In `docs/aggression_v1_v22_research_program.tex`, exhaustion / breakout / balanced / compression are defined from bp geometry plus a 100-print aftermath (30-minute incomplete censor). Old flow wording is kept as hypotheses, not as the states. The later trader-facing example was aligned. No other monograph or runtime surface changed.

### Aggression monograph rewrite follow-up

- Continued the approved V1/V2.2-only rewrite of `docs/aggression_v1_v22_research_program.tex`. The early object-registry table stays out; names are defined at first use.
- Applied remaining P1/P2 defects from the successful ontology audit: ME-II price-point vs tick qualification and running-extreme pullback; distinct 30-minute clocks; z-resolved vs barrier-resolved denominators; \(z^{\mathrm{V1,off}}\) vs \(z^{\mathrm{MEI}}\); F7 as all eligible numeric columns; \(\mathrm{RepR}\) vs option RR; E002 \(g^{\mathrm{FV}}\) vs C011 \(g\); options later cohort = last 30% of that script's dates; V2-C7 `regime_blended`; D1 15-minute declustered anchors and \(\max(\cdot,\eps)\) ratios.
- [Audit scientific claims](b3f1be21-abbd-4eaf-9700-3598a1803e69) and two earlier claim/notation audits returned no usable findings (usage-limit switch). Static LaTeX environments and braces balance; `pdflatex` is still unavailable. No dashboard, store, SOD, Tailscale, or Telegram change.

## Recent Changes (2026-08-19)

### Aggression Program LaTeX monograph

- Added `docs/aggression_v1_v22_research_program.tex`, a self-contained chronology of the Aggression research program from V1 measurement/fair value through V2/V3 and isolated monitors, all related tradability/context studies, Move Exhaustion E00–E30, Move Exhaustion V2 X0/X1/C001–C018, and autoresearch D001/E001/E002.
- The monograph uses exact V1 and V2.2 dashboard vocabulary, separates V1.2 H5/V2.2 `LIFO FRIENDLY`, C011 `LIFOScore`, and V2.4 `DISLOCATION_X_LIFO`, and preserves failed/provisional findings, locked holdouts, source conflicts, and reduced-proxy parity gaps.
- Independent scientific and structural audits found no remaining actionable defects or static LaTeX blockers. A PDF was not compiled because `latexmk` and `pdflatex` are not installed; no runtime, dashboard, store, launcher, or external service changed.

## Recent Changes (2026-08-17)

### Aggression V1 descriptive flow-dominance banner

- Added a compact top banner to `scripts/aggression_dashboard/` and a top-level `flow_dominance` block to `POST /api/aggression`.
- Completed 1m bars decompose V1's frozen intraday model into `beta * delta(TotalAggress)` and `delta(residual)`; fixed 15m/1.5/0.5 states report AGGRESS DOMINANT, IMPLIED PASSIVE DOMINANT, BALANCED, QUIET, transitions, and five-minute increasing/slowing/steady context.
- The fixed label is `DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED`. Implied passive is residual flow, not resting-order identity; there are no RANGE/TREND, long/short, execution, alert, episode, or store outputs.
- Focused calculation/API/HTML tests plus V1/V2.2 feed-failover regressions pass 14/14; Python compilation and JavaScript syntax checks pass. The existing V1 process on port 5050 was not restarted and needs a manual restart to show the banner.

### Isolated Aggression V2.6 C018 + LIFO feedback

- Added manual-only `scripts/aggression_dashboard_v26/` on browser-safe port `5062` (migrated from Chromium-blocked SIP-TLS port 5061); all runtime output is isolated to `workspace/analysis/aggression_v26_live/`.
- V2.6 keeps V2.2 LIFO FRIENDLY and adds a display-only reduced C018 heuristic: forming bar excluded, 30-bar completed window ending at each candidate, 8-tick edge trigger, frozen prefix T3 cutoff `10.5` ticks, 15-minute decluster, opposite fade lean. It is explicitly not offline C018 and remains `PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE`.
- New LIFO/C018 grade prompt posts Yes/No/Skip to `/api/feedback`. `feedback.jsonl` is append-only, episode JSON keeps the latest grade, Skip remains pending, and daily ET cards report issued/pending/graded/correct/wrong/hit rate using human grades only.
- The grade UI is a compact, fixed-corner non-modal prompt: it leaves the dashboard interactive and restores the user’s prior focus when it appears.
- Focused V2.6 tests pass 9/9 (14/14 with V2.2 regression coverage), Python compilation and JavaScript syntax validation pass, and HTTP/browser rendering checks pass. V2.6 is currently running at `http://127.0.0.1:5062/`; old port 5061 is closed. V1/V1.2/V2/V2.1/V2.2/V2.3/V2.4/V3, their stores, SOD, Tailscale, and Telegram were not changed or restarted.

## Recent Changes (2026-08-13)

### Move Exhaustion V2 75-date prefix loop COMPLETE

- Human: complete the loop without expanding discovery. C016–C018 finished leftover X1 conditioners on the locked 75-date prefix.
- C016 event-day +1.9 pp (null). C017 direction +1.8 pp (null). C018 30m range T3−T1 **+18.1 pp** [+11.7, +24.5] — descriptive vol caveat, same sign in RTH and non-RTH.
- BL-7/11/13/14 parked as blocked (AF fishing / MBP-10 / needs more dates). No open threads. Holdout locked. C001–C018 closed. Not evidence. No promotion.

### Move Exhaustion V2 C015 PARKED + R003

- Human said go ahead after C014. C015 ran X1 TOD baseline (labels-only) on the locked 75-date prefix.
- RTH reversal **41.2%** vs non-RTH **30.9%** (+10.4 pp, CI [+4.9, +16.0]). Recorded as an X1 caveat. Thread PARKED; no AF×TOD iterate.
- R003 adopted session-before-flow. Loop paused. Holdout locked. C001–C015 closed.

### Move Exhaustion V2 C014 PARKED (BL-3)

- Human approved the BL-3 spec; C014 ran on the locked 75-date X1 prefix.
- Headline IS T3−T1 reversal **−11.3 pp** [−18.5, −4.0]. OOS R² +0.0075. `corr(IS, Δm_W)=−0.957` — E07 fail (IS ≈ −realized lookback progress).
- BL-3 PARKED. No richer-model iterate. No C015 unless the human asks. Holdout locked. C001–C014 closed.

### Aggression V1/V2.2 Databento Live backup complete

- Implemented automatic direct-Databento TBBO failover for V1 and isolated V2.2.
- AccessAPI remains primary; a backup session starts only after disconnect/staleness and stops after sustained primary recovery.
- Exact instrument/nanosecond-time/sequence/payload handoff dedupe plus receive-time replay floors prevent duplicate flow rows without collapsing multi-trade CME messages; shutdown guards prevent post-stop backup activation.
- Deterministic failover/recovery simulations passed for both buffers; 30s/10s live smokes normalized 590/32 records across FV/TU/TY/US. Focused failover + existing V2.2 tests pass 13/13.
- Live V2, running dashboard processes, stores, SOD, Tailscale, and Telegram remain unchanged. Manual dashboard restarts are required.

### Move Exhaustion V2 X0 PASS; X1 sufficient-prefix baseline on record

- Full selected-row store is complete (1095 days, 88.247 GiB). QC replay: canonical Q1 + Globex Q3 last-event bound (17:00 ET or 18:00 wrap). Research-eligible 1060/1095.
- Q7/Q8 parity PASS on 36 comparable 2026 overlap dates (corr ~1.0, 0 sign flips, max abs 1 tick). 2026-02-24/25 excluded as not the same 1m panel.
- `finalize_x0` verdict **PASS**: 1042 eligible dates; discovery 731 / validation 234 / holdout 57 locked. Artifact `experiments/X0_20260813T134216Z/`.
- X1 key number recorded on a cell-guardrail prefix (`X1_20260813T143427Z`): 75 dates 2022-01-05→04-25, 854 resolved, P(REVERSAL)=0.3529. Full census stopped per user. Dual-advisor readiness review is next; do not open C001.

## Recent Changes (2026-08-12)

### Move Exhaustion V2 X0 volume + March pilot PASS; full store building

- Volume prepass is complete (`workspace/analysis/move_exhaustion_v2/x0_volume/`). Causal day plan: 1096 rows, 1095 research-eligible, sessions 2022-01-01 through 2026-04-01, `holdout_opened: false`.
- March 2026 isolated pilot passed the 20% disk-headroom gate: 22 days, 3.185 GiB actual, 158.7 GiB projected full store, ~411 GiB free. Record: `workspace/analysis/move_exhaustion_v2/pilot_202603_disk.json`.
- Selected-row scan is pandas + zstd chunked streaming (DuckDB `read_csv_auto` OOMed on a full month). Databento `F` fills count as trades; no-op signed modifies are `MOD_UNKNOWN`.
- **Current action:** full store build to `C:\FRDBs\FutMBP\move_exhaustion_v2_store` (`x0_run.py store`). After that: Q7/Q8 parity, `finalize_x0`, then X1 without holdout. Do not open C001.

## Recent Changes (2026-08-11)

### Move Exhaustion V2 Restructured to Advisor/Worker Autoresearch Loop

- The fixed X2–X10 experiment ladder is retired (contracts flipped to `RETIRED_TO_BACKLOG`; hash-pinned science preserved as audit and backlog seeds). The program now runs as a logged advisor/worker loop per `scripts/move_exhaustion_v2/research/loop/LOOP_PROTOCOL.md`: advisor maintains `BACKLOG.md` (BL-1…BL-15), writes cycle briefs, reviews worker results, iterates/parks/promotes/escalates; workers execute one brief per cycle. All events journaled append-only in `research/loop/journal/loop_journal.jsonl` via `tools/loop_journal.py`.
- Two evidence tiers: EXPLORATORY (discovery dates only, `NOT EVIDENCE`, mechanically enforced by `tests/test_loop_integrity.py`) and CONFIRMATORY (human approval + newly frozen contract + one-shot validation under `config/frozen/gates.yaml`). Holdout stays locked; X0 (data build) and X1 (price-only baseline) remain FROZEN prerequisites requiring user approval before the loop starts.
- Loop hardening (same day, after user review questions): `research/loop/AGENT_ROSTER.md` (advisor = session parent agent on the strongest model; workers = subagents, default inherit, per-brief overrides allowed; `worker_model` + `worker_agent_id` mandatory in meta.json; replicate with a fresh/different-model worker before any PROMOTE_REQUEST), `SCOREBOARD.md` (PQ1–PQ3 status, candidate ledger vs X1 baseline with ΔBrier≥0.005 / ΔAUC≥2pp bar, loop KPIs), `PLAYBOOK.md` (advisor process memory), LOOP_PROTOCOL §9 retrospectives (1 per 5 closed cycles → `retros/R###.md`, journal events `retro`/`process_change`; process-only, frozen science untouchable). meta.json completed cycles must carry `headline_metrics`. Suite: 27 pass / 3 scaffold-skips.
- Model assignment (user-directed, same day): workers run on DeepSeek `deepseek-v4-flash` via Codex CLI — launch with `npx --yes @openai/codex exec` (`codex` not on PATH; smoke-tested OK, provider `deepseek` per user's `~/.codex/config.toml`, upgrade path to `deepseek-v4-pro` noted). Advisors are a dual panel: Fable 5 + GPT Sol — session parent chairs, other reviews as subagent; independent opinions per cycle consolidated in FEEDBACK.md; disagreement → conservative decision; PROMOTE_REQUEST requires unanimity; solo reviews need `solo_review_reason` and can never promote. `advisor_models` now required in meta.json on REVIEWED cycles (test-enforced).
- X0/X1 are now explicitly authorized. The original batch is expired/missing;
  the user selected a one-shot `$0` replacement for ZN MBP-1 covering
  2022-01-01→2026-04-02 (205.7 GB billable / 2.572B records). Discovery is
  2022–2024, Validation 2025, and the 2026 final holdout remains locked.
  OQ-1/2/5/6/10/15 are answered; OQ-3/4 are deferred/inactive for X1.
- **Current action:** acquire/checksum the replacement archive, execute X0,
  then X1, and stop before C001 for dual-advisor readiness review.

## Recent Changes (2026-08-10)

### Move Exhaustion V2 Research Harness Initialized

- New long-running agentic research program rooted at `scripts/move_exhaustion_v2/` — a pure information-content study (no costs/tradability) of whether MBP-1 event-level flow predicts reversal / continuation / oscillatory outcomes after large directional ZN moves. Distinct from the frozen prior program `scripts/move_exhaustion/` (E00–E30).
- Harness: root `AGENTS.md` (repo-wide instructions + source-of-truth map), `research/PROGRAM.md` (frozen program of record, verbatim from `docs/move_exhaustion_v2_experiments.md`), `research/STATE.md` (position: X0 not started, holdout LOCKED), `research/OPEN_QUESTIONS.md` (OQ-1…OQ-15, all OPEN, none resolved by agents), `research/HYPOTHESIS_PARKING_LOT.md`, frozen contracts `experiments/X0…X8` (+ skeletal X9/X10), hash-pinned `config/frozen/` (verify/regenerate via `tools/freeze_manifest.py`), and mechanical tests under `scripts/move_exhaustion_v2/tests/` (18 pass / 3 scaffold-skips).
- **Next action requires user input:** answer X0-blocking open questions (OQ-5 split-freeze mechanism, OQ-6 pre-2024 eligibility, OQ-15 holdout end boundary) and explicitly approve starting X0. Do not begin X0 without approval.

## Recent Changes (2026-07-27)

### Supply Weeks Dashboard Refreshed

- Regenerated both 2Y/5Y/7Y supply-weeks HTML dashboards through `2026-W26` from the current auction CSVs and PostgreSQL TY data.
- Added independent, default-on `Annotate January` and `Annotate Month-End` controls to the distribution chart; disabling annotations does not change the sample, filters, or statistics.
- PostgreSQL returned no TY points for `2026-W13`, `2026-W18`, or `2026-W26`, so those cards remain present with empty chart payloads.

## Recent Changes (2026-07-24)

### Autoresearch Hypothesis Lab Complete

- Added isolated `scripts/autoresearch_lab/`, a manual-only Flask dashboard on port `5057` with three lanes: read-only campaign evidence, discovery-only exploratory compute, and preregistration authoring. It writes only under `workspace/analysis/autoresearch_lab/` and keeps every sandbox result labeled `EXPLORATORY / DISCOVERY-ONLY / NOT EVIDENCE`.
- The Evidence lane verifies the manifest chain, shows all eight predictive gates, discovery fold/power audits, E002 tradability diagnostics, the filterable D001 243-cell map, and the real 250-row exposure ledger. It reads active `campaign.sqlite` via `mode=ro` and explicitly warns that the exposure manifest names the empty `campaign_state.sqlite` freeze stub.
- `DiscoveryOnlyMatrix` rejects validation/quarantine/confirmation access. The sandbox supports frozen or isolated custom candidates, parameterized frozen-primitive outcomes, a chronological 70/30 discovery sub-split, 999-resample inference, optional 999 circular shifts, registered slices, background jobs, and an append-only multiplicity journal.
- Preregistration validates real schema-v2 specs/contracts, computes canonical/effective hashes, performs exposure prechecks, and freezes only non-authorizing scaffolds. The lab has no holdout execution endpoint and does not construct `StateStore`.
- All 11 focused lab tests pass; the complete autoresearch suite passes 110 tests. Browser smoke on `127.0.0.1:5057` loaded every API/panel with no application console errors; the temporary test server was stopped after verification. No hash-bound campaign file or V1/V1.2/V2/V2.1/V3 runtime surface changed.

### Aggression Autoresearch Validation and Branching Complete

- Completed one-shot, transform-only validation for E001 and E002 on all 101 frozen validation dates with 9,999-resample clustered inference, original Holm denominators, discovery-frozen utility thresholds, and within-date/session 30-minute circular-shift falsification.
- E001 is `not_confirmed`: ALL effect `0.00000506`, 95% CI `[-0.00008769, 0.00009930]`, adjusted p `0.9036`; the CI upper bound is far below the `0.005` SESOI. E002 is `not_confirmed`: ALL_ELIGIBLE effect `-0.00042768`, 95% CI `[-0.00080779, -0.00005070]`, adjusted p `0.9878`.
- E002's diagnostic delayed-entry strategy lost `0.4774` tick per episode after the frozen 0.5-tick round-trip cost; its 95% CI is `[-0.5875, -0.3690]` ticks. This is diagnostic context, not a separate decision.
- Completed 24-date historical quarantine as explicitly non-evidentiary. Both predictive evaluations are below the 25-date floor; E002 economics remain negative (`-0.4321` tick). Validation decisions were not overwritten.
- `research_branching_v1.json` closes E001/E002 without confirmation or retuning. D001's two descriptive nominations remain proposal-only and require new preregistered specs plus independent prospective cohorts.
- Current chains are validation `execution_manifest_v4_2.json` / `validation_authorization_v3.json` and quarantine `execution_manifest_v5.json` / `quarantine_authorization_v1.json`. Confirmation and all live use remain unauthorized. All 99 autoresearch tests pass.

## Recent Changes (2026-07-22)

### Aggression Autoresearch Phase 4 Discovery Complete

- Completed authorized discovery-only runs for D001, E001, and E002. Immutable result bundles are under `workspace/analysis/aggression_autoresearch_campaign/results/`; all report `validation_opened=false`.
- D001 completed its 243-cell descriptive map and nominated two preregistered cells. E001 selected `(window=60m, deadband=1 tick, dwell=2m)` with pooled OOF Brier improvement `0.00010274` and 3/4 positive folds. E002 selected `(lookback=30m, ridge=10, entry gap=2 ticks)` with pooled improvement `0.00000159` and 3/4 positive folds.
- Both predictive selections pass the preregistered power-availability gate (`25` required vs `101` validation dates), but this is not evidence of validation or a useful/live signal. Validation and quarantine remain locked pending separate explicit authorization.
- A fail-closed E002 result-serialization defect was fixed by converting numeric audit-map keys to canonical strings. The append-only authorization chain is now `execution_manifest_v3_7.json` + `discovery_authorization_v8.json`; all 93 autoresearch tests pass.

### Aggression Autoresearch Phase 4 Plan (plan-only)

- Wrote `docs/autoresearch_phase4_execution_plan.md`: staged roadmap to real runs — Stage 1 `run-discovery` command build (code only), Stage 2 discovery authorization via append-only `execution_manifest_v3.json`, Stage 3 D001 descriptive map, Stage 4 E001/E002 discovery selection + power gates, Stage 5 separately-authorized one-shot validation (`execution_manifest_v4.json`), Stage 6 quarantine diagnostics/reporting, Stage 7 conditional prospective confirmation. No code was run and nothing is authorized by the plan itself.

### Aggression Autoresearch Phase 3 Execution Bridge

- Added append-only execution contracts for E001/E002/D001 while preserving all Phase 2 spec/manifests byte-for-byte. Effective spec hashes bind the original methodology to exact model columns, preprocessing, nested selection, and D001's fixed ±4-tick path rule.
- Added checksummed matrix separation, runner-bound candidate configuration/state, target-free candidate modules, experiment-specific selection/evaluation machinery, detached result hashing, and synthetic end-to-end tests.
- Frozen current `execution_manifest_v2.json` with transitive code/data/spec hashes and both authorization flags false; v1 remains as the superseded preflight audit. Formation-only real-data preflight passed on all 28 candidate preprocessing configurations with zero outcomes, scoring calls, state reservations, or exposure writes.
- Real discovery selection, real outcomes, validation/quarantine, decisions, and confirmation remain locked pending separate explicit approval. No live surface changed.

### Aggression Autoresearch Phase 2 Methodology Freeze

- Published immutable dense TBBO matrix `v20260722_phase0_v3_dense_20260226`: 580,860 rows, 423 eligible dates, matrix SHA-256 `dd2cceb815a503836556ae19b555474518eb3c51a5bcee0107e85397a30d7949`. The broader audit found incomplete database sessions after 2026-02-26; the owner selected a dense cutoff instead of paid Databento backfill.
- Frozen 298 discovery dates, 101 validation dates, 24 historical-quarantine dates, a point-in-time US high-importance event manifest, an append-only exposure ledger, and prospective confirmation strictly after `2026-07-22T17:07:04.239793Z`.
- Preregistered design-only E001 residual oscillation, E002 frozen-FV convergence, and D001 dominance map under strict schema v2. `docs/autoresearch_campaign_design.md` is authoritative. No candidate, target, fit, outcome, validation, quarantine, or confirmation was run/opened.
- Execution remains locked pending a new explicit instruction. Confirmation separately requires spec-bound owner approval; all live aggression dashboards and stores remain untouched.

## Recent Changes (2026-07-21)

### Aggression Autoresearch Phase 0/1 Infrastructure

- Added an isolated TBBO research-data builder with specific-contract verification, restartable checksummed session shards, causal prior-session front selection, exact-duplicate auditing, and versioned parity-gated publication under `workspace/analysis/aggression_autoresearch_data/`. It never overwrites the V2/V3 matrix.
- Added `scripts/autoresearch/`, a cooperative/tamper-evident harness with strict H001-H004 registry schemas, target-free candidate execution, evaluator-owned outcomes/baselines, paired equal-date clustered inference, transactional one-shot state, alpha-family linkage, hash-bound approvals, and family-level holdout retirement.
- Phase boundary remains locked: no E001, real market experiment, validation/confirmation exposure, or live/dashboard change is authorized. Phase 2 requires a separate explicit user instruction.



## Current Status

✅ **Treasury Auction Data Puller - Official FiscalData API → CSV + Telegram**

✅ **Code-Mode MCP System - Complete & Optimized**

✅ **Live Surprise Tracker - Implemented & Simplified**

✅ **Distro Analytics Integration - Complete**

✅ **Scheduled Event Runner - Proactive event scheduling with Telegram alerts**

✅ **NFP Live Trigger - Automated post-event analysis with Telegram alerts**

✅ **Multi-Event Pre-Event Dashboard - NFP and CPI support**

✅ **20Y Auction Supply Return Analysis - NEW: Regression and bucketed analysis**

✅ **Unified Dashboard - Lightweight events + on-demand details**

✅ **X Macro Stream Bot - Filtered stream → Telegram with rule management**

✅ **Bloomberg Email News - Microsoft Graph mailbox → Telegram approval workflow**

✅ **Polymarket Telegram Alerts - Chart-based significant-only updates with approval workflow**



## Recent Changes (2026-07-16)

### Move Exhaustion Research (Phase 1 / E00) ✅

**What changed**:

- Added offline package `scripts/move_exhaustion/` with configs, data contracts, E00 join/audit CLI, barrier + purge helpers, and tests.
- Writes only under `workspace/analysis/aggression_v2_move_exhaustion/`. Live dashboards/streams untouched (5050/5052/5056/5058 verified still listening).
- E00 **passed**: 50,062 joined ridge rows / 69 dates; sign + day_open audits OK; holdout locked.
- E01 **completed** (dev only): primary candidate `B_price_plus_flow`; retain A–D as event-type features.
- E02 **completed**: best price-only policy `B1_fade_immediate` (~+0.37 mean net ticks, 56.8% resolved hit, pre-cost). Waiting/retrace rules worse.
- E03 **completed**: best V2 event-conditioned `V4_extreme_aligns_event__fixed_30m` (~+0.84 ticks on 182/811 entries). Beats E02 pre-cost but selective.
- E04 **completed**: flow-dominance categories weak on eval (&lt;8pp exhaustion spread).
- E05 **completed**: DV01/tick hypothesis not supported at causal event-start after leak fix.
- E06 **completed**: impact-efficiency **decay** not supported on still-extending t+5m landmark.
- E07 **completed**: state-dependent impact shortfall **provisional only** (collinear with −actual).
- E08 **completed**: flow persistence/termination **not supported** on 1m approximations.
- E09 **completed**: large-vs-small composition **not supported** (eval contrast underpowered).
- E10 **completed**: absorption→exhaustion **not supported**; stable opposite (at-extreme flow → continuation).
- E11 **completed**: liquidity interactions **supported** (eval ΔAUC +5.3pp; n=64 caveat).
- E12 **completed**: microprice/imbalance confirmation **supported** (+10.4pp vs price-only).
- E13 **completed**: residual dynamics **supported** (+9.5pp vs static \|z\|); attractive fade category not confirmed.
- E14 **completed**: cross-tenor **price** confirmation unavailable on panel; flow-proxy **not supported**.
- E15 **completed**: duration-norm **curve_directional_flow_factor supported** (curve → continuation, ΔAUC +6.2pp); residual→exhaustion not supported.
- E16 **completed**: recommend **stand aside** in opening + roll; interactions +7pp vs pooled but AUC~0.49.
- **E17–E30 complete** (2026-07-28). Final report: `docs/aggression_v2_move_exhaustion_final_report.md`.
  - Research-promising: E18 F7 classifier, E21 quantiles, E22 reversal mag, E27 top-decile (thin n).
  - Not tradable after costs (E26 −0.54 ticks). Stability unstable (E30). Holdout **locked**.
  - Recommendation: continue research / collect richer data — **no** live shadow or execution.
- **V2.3 approved** (`scripts/aggression_dashboard_v23/`, port **5059**, store `aggression_v23_live/`): isolated V2 copy + display-only exhaustion badges. Label `PROVISIONAL / RESEARCH-ONLY / NOT AFTER-COST VALIDATED`. Docs: `docs/aggression_v23_move_exhaustion.md`. Live V2 unchanged.
- Docs: `docs/aggression_v2_move_exhaustion_experiments.md`.

### Aggression V2.2 LIFO Copy ✅

**What changed**:

- Added isolated V2 copy `scripts/aggression_dashboard_v22/` on manual-only port **5058**.
- Tailscale `https://aasthajain.tail174727.ts.net:8444/` now proxies V2.2 (was V2 on 5052). SOD launcher updated; live V2 package untouched.
- Same V2 residual WATCH/DISLOCATION research surface; adds display-only **LIFO FRIENDLY** banner + stats (no factor callouts).
- Writes only under `workspace/analysis/aggression_v22_live/`. Live V2 (5052 / `aggression_v2_live/`) untouched.
- Docs: `docs/aggression_v22_lifo.md`. Tests: `tests/test_v22_lifo.py` (5 passed).

### Aggression V1.2 Provisional RANGE-Caution Monitor ✅

**What changed**:

- Added isolated V1 copy `scripts/aggression_dashboard_v12/` on manual-only port **5056**.
- Displays non-directional `CAUTIOUS_RANGE` when causal residual `|z|≥1` and widening over 15m; logs episodes + 30m RANGE/TREND outcomes under `workspace/analysis/aggression_v12_live/`.
- Also displays **soft B=4 breakout lean** (`SOFT_BREAKOUT_LEAN`) from factor-sweep restrictive PASS factors; label fixed `PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL`. Explicit: primary `(30m,8bps,k=1)=0 PASS`; 18 PASSes were soft 4bps only.
- **LIFO FRIENDLY** alert (H5 family): banner/card text only; separate LIFO rates in live-week stats; no factor explanations on UI. Label `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL`.
- Fixed labels `PROVISIONAL / RESEARCH-ONLY` and `PROVISIONAL / RANGE-CAUTION / NON-DIRECTIONAL`. Live week stats are observational only. V1/V2/V3 untouched. 12 targeted tests pass.
- Docs: `docs/aggression_v12_range_caution.md`, package README.

### V1 Flow–Price Dispersion Regime Research ✅

**What changed**:

- Added research-only `scripts/aggression_v1_flow_price_dispersion_research.py` building a causal V1-style TotalAggress→TY residual panel from the TBBO feature matrix, with independent forward RANGE/TREND outcomes and chronological discovery/validation/locked-FINAL splits.
- Validation gate: **0 PASS / 1 PROVISIONAL / 69 FAIL**. Sole provisional cell (`state_large_widening_range`, +8.13pp RANGE) had ~0 discovery lift and failed BH-FDR; classifiers show dispersion features hurt AUC vs price+flow. FINAL not inspected.
- Report: `docs/aggression_v1_flow_price_dispersion_experiments.md`. Artifacts under `workspace/analysis/aggression_v1_flow_price_dispersion/`. No live V1/V2/V3 or dashboard changes. 5 focused tests passed.

### V1 Dispersion Factor → Breakout/ATR-Range Sweep ✅

**What changed**:

- Added `scripts/aggression_v1_dispersion_factor_breakout_sweep.py` over grid `h∈{15,30,60}`, `B∈{4,6,8,12,16}` bps, `k∈{0.5,1,1.5,2}` with 39 residual factors (value/direction/speed/persistence/shape/combos).
- Primary `(30m, 8bps, k=1)`: **0 restrictive PASS**; best breakout lifts ~1.3pp with failing CIs. Restrictive PASSes (18) only at soft **B=4** (mostly k=0.5) for persistence/speed/large → BREAKOUT. Widening → RANGE is most stable but fails primary FDR.
- Report: `docs/aggression_v1_dispersion_factor_breakout_experiments.md`. Artifacts under `workspace/analysis/aggression_v1_dispersion_factor_breakout/`. FINAL not opened; V1.2/V1/V2/V3 untouched.

## Recent Changes (2026-07-17)

### V1 Aggress/Passive Dominance Parameter Sweep ✅

**What changed**:

- Added `scripts/aggression_v1_aggress_passive_dominance_param_sweep.py`: staged expansion of lookbacks/dominance ratios/QUIET floors/horizons/RANGE-TREND cutoff packs (frozen/strict/loose); ATR diagnostic Stage 3.
- Stage-2 validation: **0 restrictive PASS** across 11,664 primary cells (30 standard/moderate browsing PASSes ~5–6.7pp, BH fail). FINAL locked. Headline replication matches baseline. No V1/V1.2 retune.
- Report: `docs/aggression_v1_aggress_passive_dominance_param_sweep_experiments.md`. Artifacts under `workspace/analysis/aggression_v1_aggress_passive_dominance_param_sweep/`.

### V1 Aggress vs Passive Dominance → RANGE/TREND ✅

**What changed**:

- Added `scripts/aggression_v1_aggress_passive_dominance_research.py`: role/absorption AGGRESS_DOM / PASSIVE_DOM / NEITHER with QUIET as separate 4th bucket; primary path-efficiency RANGE vs TREND; ATR/bps diagnostic only.
- Validation restrictive PASS: **0** (162 primary cells FAIL). Headline `(w=15,d=1.5,h=30)` best lift NEITHER→TREND +2.35pp (BH fail, CI crosses 0). FINAL locked. No V1/V1.2 retune.
- Report: `docs/aggression_v1_aggress_passive_dominance_experiments.md`. Artifacts under `workspace/analysis/aggression_v1_aggress_passive_dominance/`.

### V1 Flow–Dispersion Foundations (H1–H10) ✅

**What changed**:

- Added `scripts/aggression_v1_dispersion_foundations_research.py` with locked H1–H10 (width/duration/shape/speed/flow-in-episode/flow×sign/depth/microstructure/combos/FV-parity) vs ATR/bps labels; 73 factors × 36 configs; 21,024 journal rows.
- Primary validation restrictive PASS: `H5_trade_int_lo` → CONTINUED_RANGE (+10.6pp). FINAL opened: lift +8.15pp but one-sided CI fails — **not shippable**. Soft B=4 persist/speed PASSes exploratory. Depth thin → range lean only.
- Report: `docs/aggression_v1_dispersion_foundations_experiments.md`. Artifacts under `workspace/analysis/aggression_v1_dispersion_foundations/`. No V1/V1.2/V2/V3 edits.

## Recent Changes (2026-07-13)

### TY Option Signed-Flow Pilot ✅

**What changed**:

- Added resumable API audit plus local DuckDB trade extraction, signed-flow feature construction, and staged research gates.
- Local 20-date pilot retained 34,096 trades with ~99.9% classification quality; historical local files lack native Greeks, so broad-history primary features use contracts/premium/moneyness.
- No validation candidate passed. Option-flow direction was wrong/flat; provisional extreme/large/ATM range-lift hints failed FDR/date guards. FINAL and six-month expansion remain gated. Report: `docs/aggression_v2_option_flow_experiments.md`.

### Iterative TY Skew/Vol Hypothesis Cycle ✅

**What changed**:

- Ran five read-only adaptive iterations (taxonomy, magnitude/range, persistence/regime, direction, simple context) with discovery/validation separation and four untouched FINAL dates.
- No validation candidate cleared minimum n/date/FDR guards; 1,360 candidates and every failed branch are retained in the leaderboard/journal.
- FINAL outcomes were not inspected. Report: `docs/aggression_v2_skew_iterative_experiments.md`. More independent dates are required; stop threshold tuning on current history.

### TY Option Shock Persistence / Half-Life Study ✅

**What changed**:

- Added a read-only option-metric retention study from 1m to 24h plus persistent-vs-reverted TY path comparisons.
- No holdout/FDR pass. Pooled ATM shocks typically cross 50% retention in ~17m and fully revert in ~29–36m; later re-expansion is common.
- TY range/RV differences by persistence are based on only 6 events across 3 dates and are not validated. Report: `docs/aggression_v2_option_shock_persistence_experiments.md`.

### TY Skew/Vol Shock Futures-Path Study ✅

**What changed**:

- Added a read-only event study from causal option shocks through TY path outcomes at 1m–24h, with post-shock entry, price-quiet vs price-led separation, strict endpoint censoring and matched controls.
- No direction, magnitude/range/RV or trend/range cell passes holdout/FDR; primary holdout has only 6 price-quiet shocks across 3 dates.
- Pooled diagnostics show only a weak short-horizon range lift, not a validated signal. Report: `docs/aggression_v2_vol_skew_shock_paths_experiments.md`.

## Recent Changes (2026-07-10)

### V2.1 Magnitude-Confidence Badge ✅

**What changed**:

- Added a V2.1-only display card/API block showing ELEVATED/NORMAL magnitude context from the validated pre-alert vol/range thresholds.
- The card is fixed `PROVISIONAL / NON-DIRECTIONAL`: it shows historical big-move/resolution lifts but does not alter direction, fade, or event-flow logic.
- 15 targeted tests pass. V1/V2/V3 remain untouched. The already-running external V2.1 process served the prior template and was not restarted; a manual V2.1 restart is needed to display the new card.

### Non-Flow Alert-Quality Context Study ✅

**What changed**:

- Completed Phase 1 only: a read-only common-gate comparison of magnitude/vol, gamma pin, skew agreement, event proximity, depth stress, and a quality stack on V2 alert outcomes.
- Value verdict: magnitude state adds validated big-move (+12.4pp) and resolution (+12.6pp) context, but no modifier passes after-cost directional/PnL gating. Skew-agrees-fade is provisional at 15m (n=23); gamma/event/depth lacked holdout coverage.
- Report: `docs/aggression_v2_nonflow_context_experiments.md`; artifacts under `workspace/analysis/aggression_v2_nonflow_context/`. Phase 2 dashboard was not built.

### TY Put/Call Skew-Leg Shock Research ✅

**What changed**:

- Added a read-only matched-leg event study using gamma DB `mode=ro`; RR25/50/75 reconstruct exactly from call-minus-put IV legs.
- No arm passes chronological holdout/FDR (only four holdout days). Put-IV bid and call-IV soften show provisional delayed TY-down associations; call-IV bid and put-IV soften do not predict rallies in this sample.
- Report: `docs/aggression_v2_skew_leg_events_experiments.md`; artifacts under `workspace/analysis/aggression_v2_skew_leg_events/`. Logging/re-test only; no dashboard/live signal.

### TY Skew-Price Correlation Research ✅

**What changed**:

- Added a read-only multi-metric correlation/lead-lag study over `rr25`, `rr50`, `rr75`, and `skew_index` using existing 1-minute V2/V3 joined artifacts.
- RR50 30m Pearson is `0.005` with date-clustered CI `[-0.035,+0.038]`. No contemporaneous, skew-leading, or price-leading Pearson/Spearman cell survives pooled BH-FDR.
- Report: `docs/aggression_v2_skew_price_corr_experiments.md`; artifacts under `workspace/analysis/aggression_v2_skew_price_corr/`. No dashboard/live changes and no gamma DB access.

## Recent Changes (2026-07-09)

### Aggression V2.1 Event-Flow Paper Dashboard ✅

**What changed**:

- Added `scripts/aggression_dashboard_v21/` on manual-only port `5054`, fully isolated under `workspace/analysis/aggression_v21_live/`. It preserves V2 fair-value context and adds scheduled-release flow collection, T+5 paper lean lock, post-lock outcomes, health/quality flags, and event logs.
- Corrected offline research now enters at the T+5 lock price: holdout after-cost means are -0.23/+0.19/+0.03 ticks at +5/+15/+30m and are not statistically significant. V2.1 is therefore display/paper only with a fixed `PROVISIONAL / RESEARCH-ONLY` badge.
- Added `tests/test_v21_event_flow.py` (9 passing). No V1/V2/V3, SOD, Tailscale, Telegram, or automated-execution changes.

### Aggression Statistical Gate Re-evaluation (research-only) ✅

**What changed**:

- Added `scripts/aggression_gate_lib.py` and `scripts/aggression_v2_gate_reeval_research.py` for date-clustered bootstrap CIs, one-sided tests, BH-FDR, cost sweeps, and PASS/PROVISIONAL/FAIL tiers.
- Re-evaluated 47 headline cells: **0 PASS, 11 PROVISIONAL, 36 FAIL**. Event-window 5m/15m remains the strongest lead and survives FDR, but stays PROVISIONAL because event-only context cannot satisfy RTH × no-event stability.
- Report: `docs/aggression_v2_gate_reeval_experiments.md`; artifacts under `workspace/analysis/aggression_v2_gate_reeval/`. Existing experiment verdicts and V1/V2/V3 remain unchanged.

### EV-Selective Aggression Track (research-only) ✅

**What changed**:

- Added three offline scripts: `aggression_v2_ev_selectivity_research.py`, `aggression_v2_meta_label_research.py`, and `aggression_v2_event_window_research.py`.
- EV selectivity and meta-labeling both **FAIL** after-cost gates. Event-window first-5m flow-follow is the best new lead (+1.30/+2.15/+1.89 ticks at 5/15/30m), but after de-duplicating release timestamps it still **FAILS** strict gate at `n=49 < 50`.
- Reports: `docs/aggression_v2_ev_selectivity_experiments.md`, `docs/aggression_v2_meta_label_experiments.md`, `docs/aggression_v2_event_window_experiments.md`. No dashboard; V1/V2/V3 untouched.

### Aggression V2 Forward-Return Research (research-only) ✅

**What changed**:

- Added `scripts/aggression_v2_forward_return_research.py` to test a generic, regime-conditional forward-return signal over 1/3/5/15/30m horizons. It uses existing TBBO features, explicit flow dynamics, causal residual-z, and read-only V3 flow-effort regimes.
- Report: `docs/aggression_v2_forward_return_experiments.md`; artifacts under `workspace/analysis/aggression_v2_forward_return/`.
- Gate **FAIL**: short-horizon direction AUC exists (1m 0.659, 3m 0.618, 5m 0.601), but expected moves are below 0.5-tick cost and regime-conditioned follow/fade translations are not positive after costs. No dashboard; V1/V2/V3 untouched.

### Aggression Monitor Tradability Tier 1 (research-only) ✅

**What changed**:

- Ran four offline Tier-1 experiments (resolution diagnostics, V2×V3 confluence, magnitude, stand-aside). All gates **FAIL**. No V2.1; V1/V2/V3 code/stores/ports untouched.
- Artifacts under `workspace/analysis/aggression_v2_resolution/`, `aggression_v2xv3_confluence/`, `aggression_v2_magnitude/`, `aggression_v2_standaside/`.
- Report: `docs/aggression_v2_tradability_tier1_experiments.md`. Tier 2/3 stay queued until more V3 regime history / larger holdout or a new explicit plan.

## Recent Changes (2026-07-08)

### BBG Workbook Stale Telegram Alerts Disabled ✅

**What changed**:

- `data_setup/create_bbg_data_table.py` now keeps the stale workbook check as a local warning only. It no longer sends Telegram messages or writes per-user stale-alert dedupe state.
- BBG Telegram data-health alert routing remains active for `scripts/check_economic_events_db_staleness.py`.

## Recent Changes (2026-06-15)

### Aggression V3 Flow-Effort Regime Layer ✅

**What changed**:

- V3 now remains a V2 copy plus live drivers, with an added display-only flow-effort layer for exhaustion/breakout/false-break/trend-follow context. V1 and V2 are unchanged.

- New `scripts/aggression_dashboard_v3/flow_effort.py` computes rolling effort per tick, flow-price divergence, absorption, flow deceleration, cross-tenor agreement/disagreement, frozen-range break tracking, false-break detection, event-blackout downgrades, dwell-time hysteresis, tiered labels, and conviction parts.

- `/api/live_signal` now includes `flow_effort`; the UI renders a separate regime banner, flag chips, effort/range cards, and a TotalAggress-vs-price chart with active/frozen range bands.

- New regime audit rows write to `workspace/analysis/aggression_v3_live/regime_log_YYYYMMDD.csv`. Offline validation lives in `scripts/aggression_effort_range_research.py` and writes to `workspace/analysis/aggression_effort_range/`.

- Targeted tests in `tests/test_flow_effort.py` cover exhaustion, trend-follow, breakout, false-break, warmup, and frozen-range behavior.

### Aggression Options Context Research ✅

**What changed**:

- Added read-only Phase 1 research script `scripts/aggression_v3_options_context_research.py` against the gamma tracker v3 DB (`mode=ro`). It joins front-weighted RR25/RR50/RR75, skew/backbone metrics, and near-spot `dv01_gamma` to V3 signal/regime logs and TBBO history.

- Outputs live under `workspace/analysis/aggression_v3_options_context/` (`options_context_joined.csv`, `options_context_stats.csv`, `options_context_summary.json`).

- Current decision: do **not** build Aggression V4 yet. Phase 1 did not show enough lift: RR-conditioned residual fade topped around ~44% hit, direct RR lead/lag was ~37-40%, gamma-pin tests were weak/zero on current overlap, and V3 flow-effort breakout samples are not yet available.

### Aggression V2 Options Context Research ✅

**What changed**:

- Added `scripts/aggression_v2_options_context_research.py`, a read-only experiment suite against V2 `alert_log.csv` / `signal_log_*.csv` plus gamma-tracker RR/gamma metrics. It writes to `workspace/analysis/aggression_v2_options_context/` and never modifies V2.

- Added explained-results report `docs/aggression_v2_options_experiments.md`. Key result: skew agreeing with V2 fade improved pooled 30m trade hit rate, but holdout did not pass the build gate (`n=35`, +5.3pp vs required n>=50 and +8-10pp). Track D rejected `put skew bid + fair value lower` as a continuation signal in current data.

- Current decision: do **not** build V2.5 yet. Revisit only after more V2 episode history accumulates.

- Added `scripts/aggression_v2_convergence_research.py` and `docs/aggression_v2_convergence_experiments.md` (read-only; V1/V2/V3 untouched). Continuation after costs fails. Best cell is PRICE_LED fade at `|z|>=2` 30m (+0.68 ticks after cost, n=73 holdout episodes) — gate letter-passes but RTH×no-event and live-signal confirmation fail. Decision: do **not** build V2.1 without explicit approval after reading the report.

- Added `scripts/aggression_trend_range_research.py` and `docs/aggression_v21_trend_range_experiments.md` to test trend-vs-range structure labels plus the trader claim. Clarified “black trades” as block/large prints (`large_trade_share`, `avg_trade_size`). Live V2 was not modified. Holdout gate failed (trend persistence −7.2pp; TREND had *higher* large-print intensity than RANGE). Decision: do **not** build `scripts/aggression_dashboard_v21/` on port `5054`.

### SOD Auto-Start Suite

**What changed**:

- Added `Y:\Aastha\SOD\run_all_sod.bat`, a one-click additive launcher for the full SOD suite: aggression dashboards v1/v2/v3 and the Telegram, Bloomberg news, X tweets, and Polymarket bots.

- Added `Y:\Aastha\SOD\run_aggression_monitor_v3.bat` for the V3 market-depth dashboard on port `5053`, with `msgpack` included for MBP-10 decoding.

- Hardened the standalone bot launchers with duplicate-process guards and dependency checks. The Bloomberg news launcher now starts **Outlook Classic** explicitly and sets `BBG_OUTLOOK_START_IF_CLOSED=true`.

- The master launcher never kills or restarts existing SOD services. It checks command lines and dashboard ports, skips anything already running, warns only on missing keys or unreachable externally managed AccessAPI/PostgreSQL, and opens the three dashboard browser tabs.

- Added `docs/sod_auto_start.md` with the operating contract and optional at-logon Task Scheduler command. No scheduled task was auto-registered.

## Recent Changes (2026-06-10)





### Aggression V3 Live Drivers Dashboard ?

**What changed**:

- Decommissioned the prior depth V3 entirely: stopped the 5053 MBP-10 process, deleted `scripts/aggression_dashboard_v3/`, and deleted `workspace/analysis/aggression_v3_live/` data. Separate depth research artifacts remain available.

- Recreated `scripts/aggression_dashboard_v3/` as a fresh V2 copy on port `5053`, using shared V2 research artifacts and isolated live storage under `workspace/analysis/aggression_v3_live/`.

- Added a live `drivers_now` decomposition to `/api/live_signal`: current-level contributors and one-bucket change contributors, in price points and 1/64 ticks, reconciling to `predicted_delta` and its change.

- The V3 UI now shows a `Fair-Value Drivers` panel with two signed bars: `Drivers now (level)` and `What moved fair value (last tick)`. No depth panel or MBP-10 feed remains in V3.

### Aggression V3 Market Depth Phase 1.5 Diagnostics ?

**What changed**:

- Extended `scripts/aggression_v2_depth_research.py` with composite gamma-bid risk, liquidity replenishment, imbalance persistence, sweep/absorption, OFI-vs-trade divergence, and top-vs-deep concentration diagnostics.

- New outputs live under `workspace/analysis/aggression_v2_depth/phase15/`; `docs/aggression_v2_depth_research.md` now includes Phase 1.5 tables and interpretation.

- Main result: a composite stress score is much more useful than raw depth alone. Score 0 -> 5 moved 30m realized vol from `0.0081` to `0.0159` and abs 30m move from `0.0249` to `0.0869`, though score 5 is small (`n=49`).

- Other diagnostics: refill and imbalance persistence were weak; sweeps mostly absorbed rather than continued; OFI/trade agreement was better than divergence; high top-of-book concentration lined up with larger future moves.

- Recommendation remains: proceed to V3 depth as display/risk context if approved, but keep depth out of Ridge fair value for now.

### Aggression V3 Market Depth Phase 1 Research ✅

**What changed**:

- Added `scripts/aggression_v2_depth_research.py` for the research-first V3 market-depth plan. It builds 1-minute ZN MBP-10 depth/OFI/cancel/sweep features from `D:\FRDBs\FutMBP\ZN_outrights_mbp10`, using prebuilt `D:\FRDBs\FutMBP\research_features` definitions where available.

- Wrote research artifacts to `workspace/analysis/aggression_v2_depth/` and the Gate 1 writeup to `docs/aggression_v2_depth_research.md`.

- Key Gate 1 findings: raw total depth did not support the gamma-bid thesis (`0.87x` bottom/top 30m RV), time-adjusted depth was weakly supportive (`depth_z` ratio `1.12x`), thin-depth V2 dislocations had worse directional hit/adverse stats on a tiny sample (`n=7`), and adding depth/OFI worsened common-sample Ridge OOS fit.

- Recommendation: proceed only to a V3 display/risk-context depth panel if approved; keep depth out of the fair-value model until MBP-10 history is expanded and train/live parity is better.



### Aggression V2 Hold Expectations + Prior-Blended Z ✅

**What changed**:

- Updated `scripts/aggression_dashboard_v2/model_state.py` so live residual z uses a historical residual mean/std prior from daily training data blended with today's expanding stats (`z_method=prior_blend_v1`), removing launch-time dependence and the 30-minute blind spot while staying causal.

- Extended `scripts/aggression_dashboard_v2/alerts.py` to persist hold-management outcomes: `close_price`, `close_fair_value`, `mae_to_resolve_ticks`, and `price_share_of_reversion`, plus median hold/adverse/share stats and one-time `alert_log.csv` column migration.

- Added daily minute-level signal audit logs in `workspace/analysis/aggression_v2_live/signal_log_YYYYMMDD.csv`, generated by the background evaluator from the live model rows.

- Updated the V2 UI with a Hold Expectations card, outcomes-table close/MAE/share columns, and a glossary explaining resolved/expired vs 30m hit/pnl/adverse.

- Added `Y:\Aastha\SOD\run_aggression_monitor_v2_sched.bat` and registered Windows Task Scheduler task `AggressionMonitorV2` to start/restart V2 daily at 18:00.



### Bloomberg Email News Approval Workflow ✅

**What changed**:

- Added Bloomberg news email ingestion with selectable backends: local Outlook Desktop COM (`BBG_MAIL_SOURCE=outlook_com`) and Microsoft Graph (`BBG_MAIL_SOURCE=graph`). Current deployment uses local Outlook COM because both app-only and delegated Graph paths require tenant/admin consent.

- Upgraded the Outlook backend to real-time push mode (`BBG_NEWS_MODE=event`) using Outlook `Items.ItemAdd`; polling remains available as `--mode poll`, and event mode can add an optional slow backup with `--safety-poll-seconds`.

- Added local SQLite moderation state at `~/.market_data/bbg_news.db` by default; `news_labels.id` is the short Telegram callback key and `graph_id` is the unique Microsoft Graph dedupe key.

- Added `scripts/bbg_news_stream_bot.py` as a sender-only collector with `--once`, `--dry-run`, sender filtering via `BBG_NEWS_SENDERS`, mailbox selection via `BBG_MAILBOX`, and the same X macro review/public chats.

- Wired `bbgnews_(approve|reject|score)` callbacks and `/bbg_retry_forwards` into `scripts/telegram_bot.py`, preserving forward-before-label commit semantics so failed public forwards remain retryable.



## Macro Event Analysis - State Snapshot (2026-05-19)



**Canonical doc**: `docs/macro_event_analysis_state.md`



**Scope covered**: headline macro releases (NFP/CPI/PPI), secondary macro releases (JOLTS/ISM), Fed/FOMC/Powell workflows, Treasury supply and auctions, calendar effects, policy/geopolitical dashboards, live dashboard/Telegram infrastructure, and tinkering/ad-hoc analysis scripts.



**Use this first when resuming macro dashboard work**: it maps each event family to generator scripts, HTML/CSV/PNG/JSON outputs, data sources, analysis depth, current status, and next-build candidates.



## Recent Changes (2026-05-12)



### Economic Events Secondary DB Path Move ✅

**What changed**:

- Confirmed the secondary `economic_events.db` copy was not deleted; it moved from `D:\workspace\economic_events.db` to `D:\FRDBs\workspace\economic_events.db` on the mapped `D:` share.

- Updated `scripts/run_bbg_data_daily.bat` so the 3-hour `BBGData` wrapper now refreshes the relocated secondary DB path.

- Updated `scripts/check_economic_events_db_staleness.py` so stale-DB monitoring checks the relocated secondary DB path instead of alerting on the old missing path.

- Manual wrapper verification now reaches the relocated DB path, but SQLite fails with `attempt to write a readonly database` because `D:\FRDBs\workspace` grants full control to a different SID, Administrators, and SYSTEM, not the current `aasthajain\aastha` SID.



## Recent Changes (2026-05-11)



### Telegram Approval Forward Reliability ✅

**What changed**:

- Updated `scripts/telegram_bot.py` to use longer `HTTPXRequest` timeouts for send/edit/copy operations and to register a top-level Telegram error handler with traceback logging.

- Hardened X tweet scoring in `scripts/x_macro_stream_bot.py`: approve/score callbacks now acknowledge quickly, retry Telegram `TimedOut`/`NetworkError` sends, attempt the public-group forward before committing a new label, and leave failed forwards retryable.

- Added `/x_retry_forwards [limit]` plus `list_approved_but_unforwarded()` so historical X tweets that were marked approved but never got a `forwarded_message_id` can be replayed to the public group.

- Mirrored the forward-then-label retry behavior for Polymarket alert approvals in `scripts/polymarket_stream_bot.py`, with `mark_alert_forwarded()` for retrying previously labeled-but-unforwarded alerts.

- Root cause for the recurring “button clicked but nothing forwarded” issue was Telegram public-chat send timeout after SQLite label commit, not callback registration, event-loop lag, or network-share SQLite latency.



## Recent Changes (2026-04-20)



### BBG 3-Hour Refresh/Loader Cadence ✅

**What changed**:

- Manually re-ran `data_setup/create_bbg_data_table.py` and `data_setup/ingest_excel_calendar_to_sqlite.py` against both `workspace/economic_events.db` and `D:\workspace\economic_events.db`, restoring both files after they had stalled since `2026-04-18`.

- Updated `scripts/run_bbg_data_daily.bat` so the overnight wrapper now keeps a local fallback log under `%LOCALAPPDATA%\market_data_puller\logs\`, writes status markers under `%LOCALAPPDATA%\market_data_puller\status\`, and attempts both DB targets independently instead of exiting before the secondary fan-out.

- Updated `data_setup/create_bbg_data_table.py` so stale-workbook Telegram alerts are deduped to at most one alert per `(workbook path, local date)`, with state persisted under `Path.home() / ".market_data" / "alerts" / "bbg_excel_stale_state.json"`.

- Reworked `scripts/install_bbg_data_task.ps1` to replace the old once-daily `BBGDataDaily` task with a new `BBGData` task that runs every 3 hours at `HH:15` (24x7), computes `StartBoundary` from the next valid slot at install time, and keeps `StartWhenAvailable=true`, battery restrictions disabled, and optional `-TaskPassword` support.

- Reworked `scripts/install_bbg_refresh_task.ps1` so the Bloomberg-machine `BBGRefreshExcel` task now runs every 3 hours at `HH:00` (24x7) with `/IT`, dynamic `StartBoundary`, and explicit guidance to deploy from a local folder such as `C:\bbg_refresh\`.

- Installed and verified `BBGData` on AASTHAJAIN; the old `BBGDataDaily` / `BBGDataHourly` entries are gone and the new task repeats every 3 hours starting from the next `HH:15`.

- Added `scripts/check_economic_events_db_staleness.py`, `scripts/run_economic_events_staleness_check.bat`, and `scripts/install_economic_events_staleness_task.ps1` to alert when either economic-events SQLite copy is older than 30 hours, plus duplicate-alert suppression via a small local state file.

- Added `scripts/enable_task_scheduler_operational_log.ps1` as an elevated helper because enabling `Microsoft-Windows-TaskScheduler/Operational` directly from the current shell returned `Access is denied`.



## Recent Changes (2026-04-21)



### BBGData Wrapper Execution Fix ✅

**What changed**:

- Investigated why both `economic_events.db` copies were still stuck at the 2026-04-20 manual recovery even though the new `BBGData` task was firing every 3 hours.

- Root cause was a cmd batch bug in `scripts/run_bbg_data_daily.bat`: `:run_and_capture` used `shift /1` and then executed `%*`, but cmd does not update `%*` after `shift`, so the quoted step label (`"primary create_bbg_data_table"`) was being treated as the command and every scheduled run failed with exit code `9009`.

- Updated the wrapper to pass the human-readable step label through `CURRENT_STEP` instead of a positional arg, leaving `%*` to contain only the real command (`py -3 ...`).

- Re-ran the wrapper manually and refreshed both DB copies successfully; then triggered `BBGData` once through Task Scheduler and confirmed `Last Result: 0` with the next run still at the expected `HH:15` cadence.



## Recent Changes (2026-04-16)



### Local SQLite Paths for Telegram Responsiveness ✅

**What changed**:

- Updated `src/core/x_macro_store.py`, `src/core/polymarket_stream_store.py`, and `src/servers/polymarket/poll_midpoint.py` so the Telegram/X/Polymarket SQLite databases now default to local user-profile storage under `Path.home() / ".market_data"` instead of the repo/network-share paths.

- Added env-var overrides `X_MACRO_DB_PATH`, `POLYMARKET_STREAM_DB_PATH`, and `POLYMARKET_MARKETS_DB_PATH` so the DB locations can still be customized without code edits.

- Created local SQLite backups at `C:\Users\aasth\.market_data\` for `x_macro_stream.db`, `polymarket_stream.db`, and `polymarket_markets.db` using SQLite's backup API for a consistent copy while the bot was running.

- This change is aimed at removing SMB/network-share latency from approve/reject button handling and Polymarket background polling.



### Polymarket Poll Loop Offload for Telegram Callback Responsiveness ✅

**What changed**:

- Updated `scripts/polymarket_stream_bot.py` so the hot-path blocking work inside `_poll_once()` no longer runs directly on the Telegram event loop.

- `fetch_midpoint(...)` now runs via `run_in_executor(...)`, which prevents synchronous Polymarket HTTP calls from stalling tweet and alert approve/reject callbacks while the background poll cycle is in flight.

- Chart-history loading now runs through a helper that opens its own SQLite connection inside the worker thread before calling `query_recent_timeseries(...)`, avoiding cross-thread SQLite connection reuse.

- `_render_sparkline_chart(...)` now also runs in the executor so matplotlib chart generation cannot pause callback handling during alert creation.



### BBG Daily Scheduler Dual-DB Fanout ✅

**What changed**:

- Updated `scripts/run_bbg_data_daily.bat` so the overnight `BBGDataDaily` task now runs both loader steps against two SQLite targets: the repo-local `workspace/economic_events.db` and `D:\workspace\economic_events.db`.

- Kept the existing fail-fast behavior for each stage, so the scheduler still exits immediately if any loader call fails.

- Added explicit `primary DB` / `secondary DB` markers to `workspace/logs/bbg_data_daily.log` so it is easier to see which target failed or completed.



## Recent Changes (2026-04-14)



### Telegram Callback Lag Fix ✅

**What changed**:

- Updated `scripts/telegram_bot.py` to build the Python Telegram Bot application with `.concurrent_updates(True)`.

- This removes the main source of lag where long-running orchestrator text queries could hold the sequential update processor and delay `tweet_(approve|reject)` callback handling for the X tweets group.

- The X macro stream remains split correctly: `scripts/x_macro_stream_bot.py` still sends tweets only, while `scripts/telegram_bot.py` continues to own callback polling for approvals/rejections.



## Recent Changes (2026-04-13)



### Polymarket Alert Redesign: Charts + Significant-Only ✅

**What changed**:

- Updated `scripts/polymarket_stream_bot.py` so Polymarket alerts now send a chart image plus compact caption instead of text-heavy `What changed:` message blocks.

- Alert delivery now fires only for `significant_change` or `trend`; the old heartbeat path was removed entirely.

- Replaced the fixed `5pp` significant-change rule with a hybrid threshold using `POLYMARKET_MIN_ABS_PP` and `POLYMARKET_REL_FACTOR`.

- Added per-subscription `last_alert_ts` tracking in `src/core/polymarket_stream_store.py` so the chart can mark the previous alert baseline and current point cleanly.

- Approved alerts still forward to `POLYMARKET_PUBLIC_CHAT_ID`, and photo alerts now preserve the image path through the approval/copy flow.

- Refined chart readability after live feedback: legends now sit above the plot instead of covering data, charts only plot the markets that actually triggered the alert, and captions now use full market titles plus `current <- prior (+/-pp)` summaries so date-specific variants are obvious at a glance.

- Refined Telegram caption framing so phone previews always start with `<b>Polymarket Odds Update</b>` and go directly into market titles plus `Odds:` values, with no user-query line in the preview. This avoids misleading headline-style notifications like a market question reading as confirmed news.



## Recent Changes (2026-04-06)



### Polymarket Alert Summary + Approval Flow ✅

**What changed**:

- Updated `scripts/polymarket_stream_bot.py` so every Polymarket alert now includes a plain-English `What changed:` summary describing the biggest mover or trend driver.

- Lowered default alert thresholds to `5pp` significant change, `10` same-direction polls for trend, and `3pp` total trend move; all remain overridable via the existing `POLYMARKET_*` env vars.

- Switched the Polymarket delivery path from rolling edited messages to standalone `send_message(...)` alerts for all reasons (`significant_change`, `trend`, `heartbeat`), so each alert can be approved/rejected independently.

- Wired approve/reject buttons on every Polymarket alert, backed by the existing `pm_alert_labels` store in `src/core/polymarket_stream_store.py`.

- Added forwarding of approved Polymarket alerts to `POLYMARKET_PUBLIC_CHAT_ID` and registered the new `pm_(approve|reject)` callback handler in `scripts/telegram_bot.py`.



## Recent Changes (2026-03-26)



### BBG Stale Alert Threshold Relaxed ✅

**What changed**:

- Updated `data_setup/create_bbg_data_table.py` so the BBG workbook stale warning and Telegram alert only trigger when the workbook is more than 2 days old, instead of any file not refreshed the same day.

- This better matches the overnight `BBGDataDaily` load timing and reduces false positives from normal 1-2 day gaps such as weekends.



## Recent Changes (2026-03-24)



### BBG Workbook Automation + Calendar Ingest Fix ✅

**Scope**: Restored the daily DB population path and added Bloomberg workbook automation support.



**What changed**:

- Updated `config/settings.py` weekly-calendar regex to tolerate filenames with trailing spaces before `.xlsx`, and moved the fallback calendar path to the current `\\100.95.155.81\FRGMSharedSpace\Meini\3.23 - 4.3 .xlsx`.

- Updated Bloomberg workbook paths to the shared `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx` location in `data_setup/create_bbg_data_table.py`, `data_setup/load_bbg_event_fields_to_db.py`, and `scripts/run_bbg_data_daily.bat`.

- Added stale-workbook detection to `data_setup/create_bbg_data_table.py`; when the BBG workbook was not refreshed today it now logs a warning and sends a Telegram alert telling the user which file to open and refresh.

- Updated `data_setup/create_bbg_event_fields_excel.py` so new template workbooks use `=TEXT(TODAY(),"YYYY-MM-DD")` for `B2`, while BDH formulas continue to reference `$B$1/$B$2`.

- Added `scripts/refresh_bbg_excel.py`, `scripts/refresh_bbg_excel.bat`, and `scripts/install_bbg_refresh_task.ps1` for the Bloomberg machine. The refresh script also normalizes any existing hardcoded BDH date arguments back to `$B$1/$B$2` before forcing recalculation and saving.

- Verified `data_setup/ingest_excel_calendar_to_sqlite.py` against the new weekly file: it resolved `3.23 - 4.3 .xlsx` and inserted 85 new `excel_calendar` rows into `workspace/economic_events.db`.



### BBG Refresh Attach + Dedicated Alerts Routing ✅

**What changed**:

- Updated `scripts/refresh_bbg_excel.py` to attach to an already-open interactive Excel workbook by name before falling back to a new hidden instance, so it matches Bloomberg's usual open-workbook workflow.

- Updated `scripts/install_bbg_refresh_task.ps1` to create the scheduled task with `/IT`, keeping it in the logged-in interactive session where Bloomberg Excel is running.

- Updated `scripts/refresh_bbg_excel.bat` and `scripts/install_bbg_refresh_task.ps1` to support a standalone copied-folder deployment where all refresh files live directly in the `Meini` folder on the Bloomberg machine.

- Added dedicated stale-alert routing via `BBG_ALERT_CHAT_ID` in `src/core/telegram_notifier.py` and `data_setup/create_bbg_data_table.py`, with fallback to the first configured `TELEGRAM_CHAT_ID` only when the dedicated channel is absent.

- Added setup doc `docs/bbg_alerts_channel_setup.md` with channel creation, bot admin, config, and test-message steps.

- Verified the revised refresh script on the writable staged workbook `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.regenerated.xlsx`; it saved successfully and preserved the normalized `Summary!B2 = =TEXT(TODAY(),"YYYY-MM-DD")` plus `$B$1/$B$2` BDH formulas.



## Recent Changes (2026-02-13)



## Recent Changes (2026-02-20)



## Recent Changes (2026-02-25)



## Recent Changes (2026-02-26)



### BBG Daily Scheduler Extended with Weekly Excel Calendar Ingestion ✅

**Scope**: Extended the existing `BBGDataDaily` scheduled task flow so it now updates both:

- `bbg_data` table from `Z:/Meini/bbg_event_fields.xlsx` (existing behavior)

- `economic_events` table from the weekly Meini calendar workbook (new behavior)



**What changed**:

- Updated `scripts/run_bbg_data_daily.bat` to execute a second step: `data_setup/ingest_excel_calendar_to_sqlite.py` after `create_bbg_data_table.py`, with fail-fast exits on non-zero status.

- Added `data_setup/ingest_excel_calendar_to_sqlite.py` to parse weekly Calendar-sheet events and insert conservative placeholder rows (`actual/consensus/... = NULL`, `source=excel_calendar`) into `workspace/economic_events.db` without overwriting existing natural-key matches.

- Added auto-detection in `config/settings.py` for weekly calendar files in `Y:/Meini` via date-range filename pattern (`M.D -M.D.xlsx`), choosing the most recently modified match with fallback to `EVENT_UPSTREAM_EXCEL_PATH`.

- Updated `src/services/redis_event_stream/ingestor.py` to use the same path resolver helper, so Postgres ingest also benefits from weekly-file auto-detection.



## Recent Changes (2026-02-27)



### Month-End & Friday Candlestick Dashboard ✅

**Script**: Added `scripts/monthend_friday_candlestick_dashboard.py`.

**What it provides**:

- Standalone HTML dashboard with two tabs: **Month Ends** (last business day of each month, 2022+) and **Recent Fridays** (past 2 months).

- Click any row to view TY 5-minute candlestick chart for ±2 calendar days around the date.

- Tags on rows: month-end Fridays get a "Fri" badge; Fridays that are also month-end get a "MonthEnd" badge.

- Summary bar with date count, range, and active tab indicator.

- Dark theme matching overnight session distribution dashboard style.

**Output**: `workspace/scenarios/monthend_friday_dashboard_{timestamp}.html`.

**CLI**: `python scripts/monthend_friday_candlestick_dashboard.py [--start-year 2022] [--months-back 2] [--days 2]`.



### BBGDataDaily UNC + Failure Logging Hardening ✅

**What changed**:

- Updated `scripts/install_bbg_data_task.ps1` to normalize mapped-drive script paths to UNC when creating Task Scheduler commands.

- Updated `scripts/run_bbg_data_daily.bat` to use UNC BBG workbook path (`\\100.83.215.91\productionspace\Meini\bbg_event_fields.xlsx`) and append run output to `workspace/logs/bbg_data_daily.log`.

- Verified a real failure root-cause from the log file (`Excel file not found` on an incorrect UNC host), corrected the host, and re-ran the task successfully (`LastResult=0`).

- Confirmed `BBGDataDaily` task command now points to a UNC repo script path (`\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\scripts\run_bbg_data_daily.bat`).



### Snapshot API Explorer Dashboard ✅

**Script**: Added `scripts/snapshot_explorer_dashboard.py`.

**What it provides**:

- Flask + embedded HTML dashboard for the Snapshot Query API (`SnapshotAPI` + `Cond`) from `z:/uikitxv2`.

- Query tabs for all supported operations: Live, Point-in-Time, History, Today, Search, Subscribe (SSE), and Diagnostics.

- Backend API endpoints: `/api/live`, `/api/at`, `/api/history`, `/api/today`, `/api/search`, `/api/diagnose`, `/api/subscribe`, plus `/api/meta`.

- Frontend features: dynamic condition builder, column selector, sortable results table, row/timing metadata, and Plotly time-series chart when `last_updated` + `live_px` are present.

- `today(interval=...)` support is handled by an internal fallback to trading-day `history(...)` sampling because native `SnapshotAPI.today()` has no `interval` parameter.

**Validation**:

- Verified endpoint smoke tests locally on `http://127.0.0.1:5080`:

  - `/api/live`, `/api/at`, `/api/history`, `/api/today`, `/api/search`, `/api/diagnose` all returned `ok=true`

  - `/api/subscribe` returned `200 text/event-stream` and streamed `status` + `snapshot` events.



### Monday Forward Breakevens Export (PricingMonkey) ✅

**Script**: Added `pm_monday_breakevens_export.py` at repo root.

**What it does**:

- Pulls forward breakeven rows for the **last N Mondays** (default `50`) from PricingMonkey board `https://pricingmonkey.com/b/898a2390-bb96-405c-b5d2-80648ec352a8`.

- Uses Friday-before-Monday logic for BF date (`monday - 3 days`) and Monday as AF date.

- Reuses the stable keyboard/grid automation and transform pipeline from `pm_nfp_weeks_export.py`.

- Writes output to `monday_breakevens_results.csv` (or `--out`) with `EVENT, DATE, TIME, PRICE_BPS, FWD_BE, IMPLIED_VOL, TRADE_DESCRIPTION`.

**CLI**:

- `python pm_monday_breakevens_export.py`

- `python pm_monday_breakevens_export.py --count 30 --as-of-date 2026-02-20 --no-wait-login`



### Redis Pre-Alert Streaming Service (DB-Centered) ✅

**Scope**: Added a new standalone Redis streaming service under `src/services/redis_event_stream/` that publishes 10-minute pre-alerts for calendar events and session markers (`tokyo_open/close`, `london_open/close`, `us_open/close`).

**Architecture implemented**:

- Excel remains upstream input; ingestor parses/normalizes and upserts into PostgreSQL `scheduled_event_alerts`.

- Publisher polls Postgres via `updated_at` watermark and schedules pre-alert jobs with `APScheduler`.

- Publisher emits to Redis Stream `market:events` with idempotent dedupe keys.

- Subscriber supports Redis consumer groups for multi-subscriber usage.

**Data controls**:

- Added schema DDL (`schema.sql`) with table/index definitions and `updated_at` trigger.

- Added retention controls (archive + purge old rows) and row-count guardrails to limit table growth.

**Config**:

- Added Redis/service/session constants to `config/settings.py`.

- Added required dependencies: `redis`, `apscheduler`, `openpyxl`, `psycopg2-binary`.



### Redis Alerts Live HTML Dashboard ✅

**Script**: Added `scripts/redis_alerts_dashboard.py`.

**What it provides**:

- Browser UI for live stream monitoring (auto-refresh table).

- `/api/alerts` endpoint to view latest Redis stream rows plus stream length/last ID metadata.

**Run**:

- `python scripts/redis_alerts_dashboard.py --host 127.0.0.1 --port 5071`

- Open `http://127.0.0.1:5071`



### US Holiday Candlestick Dashboard ✅

**Script**: Added `scripts/us_holiday_candlestick_dashboard.py` to generate a standalone HTML holiday dashboard with clickable holiday rows and on-demand candlestick rendering.

**Scope**:

- Holiday set mirrors the requested list (New Year, MLK, Washington's Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas).

- Year range is configurable and defaults to `2022 -> current year`.

- Clicking a holiday displays a TY candlestick panel for `+/-3` calendar days around the observed holiday date (configurable).

- Holiday rows are now filtered to include only dates where TY OHLCV exists in the selected window (no blank/no-data rows).

**Interactivity update**: Enabled explicit Plotly zoom interactions (mouse-wheel zoom, drag-zoom mode, visible modebar, double-click reset) for easier chart exploration.

**Output**: Verified generation with `python scripts/us_holiday_candlestick_dashboard.py --start-year 2022 --end-year 2026`, output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_141547.html`.



### FOMC Q&A Microstructure Alignment ✅

**Script**: Added `scripts/fomc_qa_microstructure_alignment.py` for answer-level alignment of historical FOMC transcripts to market windows.

**Parser**: Added `src/core/fomc_qa_parser.py` for deterministic speaker parsing and `question -> CHAIR POWELL answer` block extraction anchored on Powell answer start.

**Metrics**:

- Signed `delta_bps` windows: `0-30s`, `30s-2m`, `0-10m` (no tick outputs).

- Realized-vol spikes vs pre-answer rolling baseline (`rv_*`, `rv_baseline_*`, spike ratio and z-score).

- Direction consistency labels per window: `trend`, `mean_revert`, `mixed` plus efficiency ratio.

**Output**: Per-transcript JSON + aggregate CSV in `workspace/fed/fomc conference/qa_microstructure/`; verified smoke run (`--max-transcripts 1`) wrote 25 answer rows for `2020-01-29`.



### CPI Two-Factor Plan Refresh ✅

**Doc**: Updated `docs/cpi_twofactor_pca_plan.md` to a consolidated implementation/status roadmap.

**Now included**:

- original build steps vs current delivered state

- explicit "what works" and "what does not yet work" sections

- phased forward plan for regime-aware calibration, tail-aware risk reporting, nonlinear lift testing, and positioning proxy integration

- future validation plan centered on rolling OOS + tail calibration metrics (not only R2/RMSE)



### NFP Two-Factor PCA Prototype ✅

**Script**: Added `scripts/nfp_twofactor_pca_dashboard.py` to generate a standalone NFP factor-bucketing dashboard mirroring CPI two-factor behavior.

**Data flow**: Reads `NFP` and `UER` component blocks from Bloomberg workbook (`bbg_event_fields.xlsx`, NFP sheet), joins NFP event timestamps from `workspace/economic_events.db`, and computes TY pre/post bps windows from PostgreSQL OHLCV.

**Modeling**:

- Pre-event factors from uncertainty vector `[nfp_cons_std, uer_cons_std]` (consensus fallback only when flat).

- Post-event factors from standardized surprises using explicit consensus/actual/std-dev fields for both components.

- PCA orthogonal factors, regression-scored quantile buckets, and factor-to-bps attribution panel with clickable drilldowns.

**Boundary**: Historical sample defaults to `2022-01-01` onward.

**Output**: Verified generation at `workspace/scenarios/nfp_twofactor_pca_dashboard_20260213_092437.html`.



## Recent Changes (2026-02-12)



### CPI Two-Factor PCA Prototype ✅

**Script**: Added `scripts/cpi_twofactor_pca_dashboard.py` to generate a standalone HTML prototype for CPI factor bucketing and bps impact.

**Data flow**: Reads CPI components from Bloomberg workbook (`bbg_event_fields.xlsx`), joins CPI event timestamps from `workspace/economic_events.db`, and computes TY pre/post bps windows from PostgreSQL OHLCV.

**Modeling**:

- Pre-event factors from uncertainty vector `[mom_cons_std, core_mom_cons_std]`

- Post-event factors from trend-adjusted surprises using rolling regression expected MoM from YoY levels

- PCA orthogonal factors, variance-weighted composite score, quantile bucket matching, and factor-to-bps attribution panel

**Boundary**: Historical sample defaults to `2022-01-01` onward.

**Output**: Verified generation at `workspace/scenarios/cpi_twofactor_pca_dashboard_20260212_093229.html`.

**Follow-up fix**: Pre-event CPI std-dev features were flat in 2022+ data, causing degenerate pre-factor outputs (`0,0`). Script now auto-falls back to consensus-level pre features and shows an explicit warning; added pre/post bucket boxplots with current bucket highlight. Updated output: `workspace/scenarios/cpi_twofactor_pca_dashboard_20260212_094534.html`.

**Modeling update**: Bucket assignment now uses regression-weighted PCA factors (with intercept) rather than variance-explained PCA composite only, and the UI now displays pre/post regression equations with `R2`/`RMSE` plus economic interpretation for Factor 1/2 in a bottom panel. Latest output: `workspace/scenarios/cpi_twofactor_pca_dashboard_20260212_095313.html`.

**Hourly extension**: Added post-event hourly breakeven-style extent series (`t+1h` to `t+8h`) per historical event and a new dashboard table showing hourly mean B/E (bps) for all history and each post bucket.

**Hourly UX update**: Hourly table is now a post-bucket drilldown table triggered by clicking bucket rows, with clock-time rows from `08:30` through `15:00` ET (`08:30, 09:00, 10:00, ..., 15:00`).



## Recent Changes (2026-02-11)



### Polymarket Scenarios CPI Extension ✅

**Update**: Unified dashboard Polymarket Scenarios now supports CPI in addition to NFP/UER. Backend scenario APIs accept `cpi` (`/api/polymarket/resolve`, `/api/scenarios/table`, `/api/scenarios/hist`, `/api/scenarios/bin_paths`), with CPI market keyword resolution (`inflation in January`) and CPI event-name aliases in `bbg_data` surprise-series lookups.

**UI**: Added CPI controls/table to `scripts/dashboard_tabs/polymarket_scenarios_tab.py`, included CPI in row-selection, expected-row handling, histogram drilldowns, and projected-price ladder aggregation. Dashboard header event dropdown now lists supported events and navigates on selection.



### 10Y/30Y Auction Weeks Dashboard ✅

**Script**: `scripts/ten_thirty_auction_weeks.py` now generates a standalone supply-weeks-style HTML dashboard for TY using only `economic_events.db` date anchors for `10-Year Note Auction` and `30-Year Bond Auction` (no auction fundamentals).

**Output**: `scripts/ten_thirty_auction_weeks_dashboard.html` with normalized TY paths, zoom re-normalization, 10Y/30Y window-based distribution controls (`pre_10y`, `10y_to_30y`, `post_30y`, auction-day windows), and 5-minute candlestick drilldown for selected weeks.



### NFP x UER 4x4 Post-Event Grid (Tinkering) ✅

**Script**: `tinkering/nfp_uer_4x4_postevent_grid.py` generates a standalone interactive HTML that buckets NFP and UER standardized surprise percentiles into a fixed 4x4 (25%-width) grid, shows per-cell t->EOD stats (mean and P5/P50/P95) plus intraday max-up/max-down means, and supports click-to-view normalized post-event TY price paths for events in each cell.



## Recent Changes (2026-02-05)



### JOLTS Job Openings Post-Event Dashboard ✅

**Script**: `scripts/jolts_job_openings_postevent_dashboard.py` generates a standalone HTML dashboard with user-entered actual/consensus, surprise percentile bucketing, and all-vs-similar distribution plots at t+10m/t+30m/t+1h/t+2h with expected rally/selloff stats.



## Recent Changes (2026-02-09)



### Standalone Pre-Event Plotly Inline ✅

**Update**: `scripts/preevent_standalone_dashboard.py` now inlines Plotly (cached at `workspace/scenarios/_assets/plotly-2.27.0.min.js`) to avoid blank charts when CDN access is blocked.



## Recent Changes (2026-02-10)



### Polymarket Scenario Histogram Parity ✅

**Update**: Row-click histogram modal in `scripts/dashboard_tabs/polymarket_scenarios_tab.py` now uses distro-style rendering (density histogram + KDE), includes full distro stats text, and annotates the latest two events.

**API**: `/api/scenarios/hist` in `scripts/live_unified_dashboard.py` now returns `rows`, `stats`, `stats_lines`, and `last_two` in addition to raw `values`.



## Recent Changes (2026-02-06)



### Event Tags CLI ✅

**Script**: `data_setup/populate_event_tags.py` adds interactive entry for `event_tags` in `workspace/economic_events.db`, including overlap detection and optional merge.



### X API Tweet Fetcher ✅

**Script**: `tinkering/x_fetch_tweet.py` fetches a single tweet via X API v2 using stdlib-only HTTP, with `X_BEARER_TOKEN` loaded from `config/keys.env`. Supports latest-by-username with optional retweets/replies.



## Recent Changes (2026-02-04)



### ISM Services PMI Post-Event Dashboard ✅

**Script**: `scripts/ism_services_pmi_postevent_dashboard.py` generates a standalone HTML dashboard with user-entered actual/consensus, surprise percentile bucketing, and all-vs-similar distribution plots at t+10m/t+30m/t+1h/t+2h with expected rally/selloff stats.



## Recent Changes (2026-01-28)



### Unified Dashboard Performance (WIP) ✅

**Update**: `/api/events` now returns lightweight metadata only; per-event price paths + candlesticks are fetched via `/api/event_details` on demand.

**UI**: Event data load is gated behind event selection, and post-event JS syntax error fixed in tab module.

**L1 Builder**: Added optional `stats.arrow` generation (prefix stats + normalized bps) and Windows Task Scheduler helpers for daily auto-build.

**Live Overlay**: Databento snapshot now supplies move/breakeven fields and UI shows live source in status.



### Yearly Overlay Plot ✅

**Script**: `scripts/yearly_overlay_timeseries.py` generates interactive Plotly overlays of full-year OHLCV series from PostgreSQL, aligned on a shared calendar axis with default normalization, auto tick units for Treasuries, and start/end month window controls that re-normalize to the window start. HTML output now serializes x/y as plain lists to keep window normalization correct.



## Recent Changes (2026-01-30)



### Month-End Friday US Open Distribution ✅

**Script**: `scripts/month_end_friday_us_open_distribution.py` generates distro-style histogram plots for month-end Friday returns during US open hours (08:00–17:00 ET) from 2022 onward, with stats box including mean/median/extreme percentiles, expected rally/selloff, and 0th-percentile bps. Outputs PNG + JSON/CSV in `workspace/analysis/`.



**Update**: Added all month-end trading days plot with Friday markers, separate Friday stats/CSV, and conversion via `rules/treasury_price_conversion.py`. `plot_distribution()` now supports overlay markers.



## Recent Changes (2026-01-22)



### Auction File Batch Processor ✅

**Feature**: Batch processor for raw auction CSV files with incremental processing.



**Script**: `auction_data/process_auction_files.py`



**What it does**:

1. Scans current directory for `*_raw.csv` files

2. Generates corresponding `*_processed.csv` files with 13 Nomura-style columns

3. Skips files if processed version is already up-to-date (timestamp comparison)

4. Uses `derive_nomura_fields()` from `data_setup/derive_auction_fields.py`



**Usage**:

```bash

cd auction_data

python process_auction_files.py          # Process new/updated files only

python process_auction_files.py --force  # Reprocess all files

```



**Output columns**: Auction, Size, Coupon, Stop, Bid/Cover, Tail, Yld Gap, Dealer%, Direct%, Indirect%, Dlr Hit, Dir Hit, Indir Hit



### Ad-Hoc Dashboard Session Fix ✅

**Fix**: Ad-hoc tab now treats selected date as the session end (6pm prior day → 5pm selected day), avoiding empty results on weekdays like Monday.



---



## Recent Changes (2026-01-27)



### Unified Dashboard Plan ✅

**Doc**: Added a unified dashboard plan with scope, milestones, acceptance criteria, and risks.

**File**: `docs/unified_dashboard_plan.md`



### Unified Dashboard Plan Update ✅

**Update**:

- UI scope is now **NFP + CPI only** (shared dashboard template; config differs slightly by event)

- Event Analysis: **Pre-event and Post-event are not separate tabs**; they live under a single **Event Analysis** tab and the user selects pre vs post after selecting the event.

- Pre/post analysis windows should be **user-selected** (pre-event start time, post-event end time) rather than hardcoded constants

- Ad‑Hoc and Week Match tabs are **event-agnostic** and should not show the event selection panel or live data panel

- Live price source will move from snapshot DB → **Databento API** (see `scripts/databento_zn_live.py` as reference pattern)

- Treasury futures conversion utilities should come from `rules/treasury_price_conversion.py`

- Plan includes a placeholder **Non‑Economic Events** tab with a chatbox/tag-query UX (e.g., “shutdown”) to build later



---



## Recent Changes (2026-01-21)



### 20Y Auction Supply Return Analysis ✅

**Feature**: Analyzes relationship between 20Y Treasury auction fields and TY price returns at 2h, 4h, 6h, 8h, 12h, and 24h after auction.



**Script**: `scripts/auction_supply_return_analysis.py`



**What it does**:

1. Loads auction data from `Securities_2_nomura_only.csv` (Nomura-style labels)

2. Fetches TY 1-min prices for 24 hours post-auction (starting 1pm ET)

3. Computes returns in bps at each horizon

4. Runs OLS regressions with all numeric auction columns as predictors

5. Performs quantile bucket analysis with rally/selloff rates

6. Generates distribution histograms and rally vs selloff split plots



**Key Findings** (23 auctions, 2020-2025):

- R² ranges 0.32–0.65 across horizons; 8h has highest R² (0.65)

- Significant regressor: **Stop** yield for ret_8h (t=-2.03)

- Monotonic relationships: **Bid/Cover** (increasing with ret_2h/ret_4h), **Coupon/Stop** (decreasing with ret_6h)



**Usage**:

```bash

python scripts/auction_supply_return_analysis.py

python scripts/auction_supply_return_analysis.py --buckets 5 --start-year 2021

```



**Outputs**: `workspace/results/auction_supply_return_analysis/`

- `auction_returns_joined.csv` - Joined auction fields + returns

- `regression_results.csv` - OLS coefficients and t-stats

- `bucket_analysis.csv` - Quantile bucket statistics

- `summary_report.json` - Summary with significant predictors

- `plots/` - Distribution histograms



---



## Recent Changes (2026-01-12)



### Multi-Event Pre-Event Dashboard ✅

**Feature**: Live pre-event dashboard now supports both NFP and CPI events.



**Changes**:

1. New `scripts/event_data_loader.py` - Centralized data loading for NFP and CPI events

2. Updated `scripts/live_preevent_dashboard.py` - Added `--event {NFP,CPI}` CLI argument

3. Dynamic HTML titles and labels based on selected event type

4. Shared data sources: Z:/Meini/NFP.xlsx and Z:/Meini/CPI.xlsx for cons_std and FWD B/E

5. Economic events DB for actual/consensus values



**Usage**:

```bash

python scripts/live_preevent_dashboard.py --event NFP --fwd_be 6.5

python scripts/live_preevent_dashboard.py --event CPI --fwd_be 8.0 --serve

```



**Event Configuration**:

| Event | DB Event Name | Excel | Unit | Default Std |

|-------|---------------|-------|------|-------------|

| NFP | Non Farm Payrolls | Z:/Meini/NFP.xlsx | K | 25.0 |

| CPI | Core Inflation Rate MoM | Z:/Meini/CPI.xlsx | % | 0.05 |



---



## Recent Changes (2026-01-07)



### NFP Live Trigger (NEW) ✅

**Feature**: Automated NFP post-event analysis with Telegram notifications triggered by Bloomberg Excel data.



**Script**: `scripts/nfp_live_trigger.py`



**What it does**:

1. Monitors `Z:/Meini/NFP.xlsx` for actual NFP data (xlwings for live Bloomberg, pandas fallback)

2. On data detection:

   - Gets live TY price from `snapshots_YYYY-MM-DD.db` (auto-discovers latest)

   - Calculates options-implied FWD breakeven from vega-weighted IV

   - Computes surprise percentile using historical standardized surprises

   - Finds matching 10% bucket from historical data

   - Computes B/E statistics: mean, median, 95th, 99th percentile multipliers

   - Projects price paths at 30-min intervals (8:30am → 3pm)

   - Generates full HTML dashboard via `build_postevent_dashboard()`

   - Sends Telegram notification with summary to all ALLOWED_CHAT_IDS



**Telegram summary includes**:

- Actual/Consensus/Surprise with standardized σ

- Surprise percentile

- Current TY price (32nds format)

- Options-implied FWD breakeven

- Bucket statistics (B/E multipliers → bps moves)

- Projected price paths at half-hour intervals



**Usage**:

```bash

python scripts/nfp_live_trigger.py              # Start monitoring

python scripts/nfp_live_trigger.py --test       # Test with mock data

python scripts/nfp_live_trigger.py --poll 10    # Custom poll interval

python scripts/nfp_live_trigger.py --reset      # Clear triggered state

```



---



## Recent Changes (2025-12-24)



### Analytics Plugin System (NEW)

**Feature**: Modular plugin architecture for adding analytics tools via git submodules without modifying core files.



**Implementation**:

1. `@register_tool` in `src/mcp/discovery.py` extended with `agent` parameter for explicit agent assignment

2. `src/servers/analytics/plugins/` directory with auto-discovery `__init__.py`

3. `src/agents/orchestrator_agent/tool_loader.py` now builds `AGENT_TOOL_MAP` dynamically from registry

4. `plugins/README.md` - Complete developer guide for plugin authors

5. `plugins/_example/` - Reference implementation with `example_summary_stats` and `example_normalize_data` tools



**How it works**:

- Plugins are subdirectories in `src/servers/analytics/plugins/` with `__init__.py`

- Each `@register_tool` decorated function is auto-discovered on import

- Agent assignment: (1) explicit `agent=` param, (2) auto-detect from module path, (3) fallback map

- Git submodules can be added without editing any orchestrator code



**Plugin developer workflow**:

```bash

# In your plugin repo

git init my-plugin && cd my-plugin

# Create __init__.py with @register_tool functions

git add . && git commit -m "Initial"

git remote add origin git@github.com:team/my-plugin.git



# In main repo

cd src/servers/analytics/plugins

git submodule add git@github.com:team/my-plugin.git my_plugin

# Restart server - tools are auto-discovered

```



---



## Recent Changes (2025-12-17)



### Soft Dependencies for Tools (NEW) ✅

**Feature**: Tools can now declare `soft_dependencies` in `@register_tool` to have prerequisite tasks auto-injected by the orchestrator.



**Implementation**:

1. `@register_tool` in `src/mcp/discovery.py` extended with `soft_dependencies` param

2. New `DependencyResolver` in `src/agents/orchestrator_agent/dependency_resolver.py`

3. `PlannerStage2` calls resolver after building execution plan

4. `Coder` generates scripts with graceful degradation for soft deps

5. **Context builder** extracts `dates` from `run_multi_date_query` for time range resolution

6. **Tool alias** added: `generate_multi_candlestick_chart` (singular) maps to plural version



**Example**: `generate_candlestick_chart` now declares events as a soft dependency:

```python

soft_dependencies=[{

    "param_name": "events",

    "source_agent": "eventdata_puller_agent",

    "source_tool": "search_events",

    "param_mapping": {

        "start_date": "$context.time_range.start",

        "end_date": "$context.time_range.end",

        "country": "$context.country|US",

        "importance": "high"

    },

    "output_path": "events",

    "required": False  # Graceful degradation

}]

```



**Graceful degradation**: If event fetch fails, chart renders without events and metadata includes `soft_dependency_notes` explaining why.



**Note**: Requires Python process restart to pick up new tool metadata from registry.



### `run_multi_date_query` Time Window Support

**Issue**: When event timestamps included time (e.g., `2025-09-16T13:00:00`), the function stripped the time with `date_str[:10]` and fetched full-day data instead of respecting the event time.



**Fix**: Added `offset_minutes` and `window_minutes` parameters to `run_multi_date_query`:

- `offset_minutes`: Minutes after the timestamp to START fetching data (supports negative for "before")

- `window_minutes`: Duration of time window in minutes (None = full day)



**Example**: "1 hour after bond auction" now works correctly:

```python

run_multi_date_query("TY", ["2025-09-16T13:00:00"], offset_minutes=0, window_minutes=60)

# Returns 60 rows from 13:00-14:00, not 1000+ rows for the full day

```



## Recent Changes (2025-12-22)



### AnalyticsAgent DB Access Removed ✅

`analytics_agent` is now **pure analysis/plotting** and no longer opens PostgreSQL/SQLite connections.



Impacts:

- Disabled standalone `analysis_type`: `surprise_analysis` and `event_impact`

- Disabled `descriptive` mode fallback that fetched from databases when `params["data"]` was missing

- Orchestrator heuristics updated to avoid mapping surprise/event-impact requests to `analytics_agent`



---



## Final Structure



### Source Code (~40 files)

```

src/

├── bus/          # File-based bus (file_bus, manifest, schema)

├── mcp/          # MCP infrastructure (client, discovery, taskmaster_client)

├── servers/      # Tools

│   ├── marketdata/       # Market data SQL queries

│   ├── polymarket/       # Direct Polymarket API tools

│   ├── analytics/        # Plotting infrastructure (core_plotting, plot_factory, plugins/)

│   ├── tradingeconomics/ # Economic calendar & WebSocket tools

│   └── distro_analytics/ # DistroDashboard analytics wrapper

├── agents/       # Agents

│   ├── market_data_agent/       # SQL query producer

│   ├── consumer_agent/          # Statistics consumer (demo)

│   ├── polymarket_agent/        # Polymarket search

│   ├── eventdata_puller_agent/  # Economic events manager

│   ├── analytics_agent/         # Statistical analysis & visualization

│   ├── orchestrator_agent/      # Multi-agent orchestration (11 files)

│   └── runner_agent/            # Final answer consolidation (GPT-5 class)

└── core/         # Utilities (logging_config, telegram_notifier, schedule_store, run_store)

```



### Scripts (utilities)

```

scripts/

├── test_queries.py               # Pre-configured market data queries

├── test_polymarket_unified.py    # Polymarket search queries

├── test_orchestrator.py          # Multi-agent orchestration queries

├── test_distro_analytics.py      # Distro analytics smoke tests

├── test_analytics.py             # Analytics agent tests

├── test_eventdata.py             # Economic event queries

├── live_surprise_tracker.py      # Live Surprise Tracker (Real-time Analytics)

├── scheduled_event_runner.py     # Proactive event scheduling with Telegram

├── jolts_live_analyzer.py        # Reusable event analysis core (analyze_event_data)

├── surprise_predictive_power_analysis.py  # Surprise→price correlation study

├── surprise_mean_reversion_analysis.py    # Surprise→reversal analysis

├── extension_reversion_analysis.py        # Extension→reversal analysis

├── powell_speech_analyzer.py     # Real-time Powell speech sentiment

├── powell_speech_manual.py       # Manual transcript sentiment analysis

├── fetch_us_events.py            # Bulk fetch US events 2020-present

├── build_event_dictionary.py     # Build event name reference dictionary

├── list_event_names.py           # List available event names

├── show_all_scheduled.py         # Show scheduled events DB contents

├── show_logs.py                  # View logs & artifacts

├── run_agent.py                  # CLI for custom queries

└── setup_polymarket_db.py        # Database setup utility

```



### Tests (3 files)

```

tests/e2e/

├── conftest.py

├── test_marketdata_e2e.py   # 7 E2E tests (market data)

└── test_predictions_e2e.py  # 7 E2E tests (predictions)

```



### Documentation (8 files)

```

Root:

├── README.md         # Project overview

└── START_TESTING.md  # Testing guide



docs/

├── ARCHITECTURE.md   # System design

├── USAGE.md          # Usage patterns

└── agents/

    ├── MARKET_DATA_AGENT.md

    ├── DISTRO_ANALYTICS_AGENT.md

    └── EVENTDATA_PULLER_AGENT.md



memory-bank/

├── activeContext.md  # This file

├── code-index.md     # File summaries

├── io-schema.md      # I/O contracts

└── progress.md       # Implementation history

```



---



## System Architecture



**Pure Python code-mode MCP** with:

- File-based inter-agent bus

- Atomic file operations

- Manifest-driven incremental IDs

- Per-execution run logging

- Tools-as-code (direct Python calls)

- Multi-layer validation



**Two Primary Pipelines:**



**Pipeline 1: SQL Market Data**

```

User → MarketDataAgent → MCP Client → run_query Tool → SQLite

                        ↓

                 File Bus (000001.json)

                        ↓

                   ConsumerAgent → Statistics

```



**Pipeline 2: Distro Analytics**

```

User → AnalyticsAgent → Distro Tools (PostgreSQL OHLCV)

                      ↓

            Stats + Plots (PNG)

                      ↓

              File Bus (000001.json)

```



**Pipeline 3: Multi-Agent Orchestration (Two-Stage Planner)**

```

User → OrchestratorAgent (Two-Stage Planner)

                ↓

        ╔═══════════════════════════════════════╗

        ║  STAGE 1: Task Planning               ║

        ║  - AI-powered decomposition           ║

        ║  - Agent assignment                   ║

        ║  - Dependency analysis (DAG)          ║

        ║  - Path extraction                    ║

        ╚═══════════════════════════════════════╝

                ↓

        ╔═══════════════════════════════════════╗

        ║  STAGE 2: Tool Discovery (Per Path)   ║

        ║  - Lazy tool loading                  ║

        ║  - Context isolation                  ║

        ║  - Parameter extraction               ║

        ╚═══════════════════════════════════════╝

                ↓

        ╔═══════════════════════════════════════╗

        ║  CODER: Script Generation             ║

        ║  - Async Python scripts               ║

        ║  - Dependency-aware execution         ║

        ║  - DB + File Bus writes               ║

        ╚═══════════════════════════════════════╝

                ↓

        ╔═══════════════════════════════════════╗

        ║  WORKERS: Parallel Path Execution     ║

        ║  - Independent paths run concurrently ║

        ║  - Shared tasks executed once (DB)    ║

        ║  - Within-path task dependencies      ║

        ║  - SQLite: Metadata + results         ║

        ║  - File Bus: Large datasets           ║

        ╚═══════════════════════════════════════╝

                ↓

        ╔═══════════════════════════════════════╗

        ║  RUNNER: Consolidation                ║

        ║  - Query DB for all outputs           ║

        ║  - Merge data by agent type           ║

        ║  - Generate NL answer                 ║

        ║  - AI validation (optional)           ║

        ╚═══════════════════════════════════════╝

                ↓

        File Bus + DB (orchestrated results)

```



---



## Key Features



✅ **Two-Stage Planner Architecture** (context-efficient orchestration)

✅ **Lazy Tool Loading** (per dependency path for context isolation)

✅ **Full DAG Support** (parallel + sequential task execution)

✅ **Dual Storage** (SQLite for metadata, File Bus for large data)

✅ **Dependency-Aware Execution** (automatic wait for dependencies)

✅ **Soft Dependencies** (tools declare prerequisites, auto-injected with graceful degradation)

✅ **Plugin System** (git submodule support for modular tool development)

✅ Zero external dependencies (stdlib only for core)

✅ **Distro Analytics Integration** (Returns, Volatility, Probability Matrices, Event Distribution)

✅ **Live Surprise Tracker** (Real-time economic data analytics)

✅ **Dual Data Source Support** (SQLite/market_data + PostgreSQL/ohlcv_1m)

✅ **Scheduled Event Runner** (Proactive event analysis with Telegram alerts)

✅ **Powell Speech Analyzer** (Real-time Fed speech sentiment tracking)

✅ **Martingale Strategy Analysis** (Surprise/extension → mean-reversion studies)

✅ **Bond Price 32nds Formatting** (Treasury futures displayed in trader notation)



---



## Conventions



### Bond Price Formatting (32nds)



**ALWAYS use 32nds format for Treasury futures prices in plots and answers.**



| Decimal | 32nds | Meaning |

|---------|-------|---------|

| 108.359375 | 108-11+ | 108 and 11.5/32 |

| 108.703125 | 108-22+ | 108 and 22.5/32 |

| 108.1875 | 108-06 | 108 and 6/32 |

| 108.25 | 108-08 | 108 and 8/32 |



**Suffixes:**

- No suffix = exact 32nd (e.g., 108-06)

- `+` = half 32nd (e.g., 108-22+ = 22.5/32)

- `'2` = quarter 32nd (e.g., 108-22'2 = 22.25/32)

- `'6` = three-quarter 32nd (e.g., 108-22'6 = 22.75/32)



**Utility functions** in `src/servers/analytics/schema.py`:

- `decimal_to_32nds(price)` → "108-22+"

- `thirtytwonds_to_decimal("108-22+")` → 108.703125

- `format_bond_price(value)` → auto-detects and formats

- `is_bond_price(value)` → True if 85 ≤ value ≤ 145



**Candlestick charts** automatically detect Treasury prices and format Y-axis in 32nds.



---



## Quick Commands



```bash

# ⭐ Scheduled Event Runner (Proactive Analysis)

python scripts/scheduled_event_runner.py --build-schedule config/scheduled_events.csv

python scripts/scheduled_event_runner.py --run-once

python scripts/scheduled_event_runner.py --loop

python scripts/scheduled_event_runner.py --status

python scripts/scheduled_event_runner.py --test-telegram



# ⭐ Live Surprise Tracker (Real-Time)

python scripts/live_surprise_tracker.py --country US

python scripts/live_surprise_tracker.py --country US --importance high --update-history



# ⭐ Powell Speech Sentiment Analysis

python scripts/powell_speech_analyzer.py --test  # Test mode with samples

python scripts/powell_speech_manual.py           # Manual paste-in mode



# ⭐ Surprise/Reversion Analysis (Martingale Strategy)

python scripts/surprise_predictive_power_analysis.py --event "JOLTS Job Openings" --symbol TY

python scripts/surprise_mean_reversion_analysis.py --event "Nonfarm Payrolls" --symbol TY

python scripts/extension_reversion_analysis.py --event "JOLTS Job Openings" --symbol TY

python scripts/extension_reversion_analysis.py --category "Labor" --country all



# ⭐ Distro Analytics Test

python scripts/test_distro_analytics.py --test returns

python scripts/test_distro_analytics.py --test all



# ⭐ Multi-Agent Orchestration

python scripts/test_orchestrator.py --list

python scripts/test_orchestrator.py --query 4    # Complex multi-agent query



# Event Data Utilities

python scripts/fetch_us_events.py                # Bulk load US events 2020-present

python scripts/build_event_dictionary.py         # Build event reference dict

python scripts/list_event_names.py               # List available events

python scripts/show_all_scheduled.py             # Debug scheduled events DB



# Market Data Queries

python scripts/test_queries.py --list

python scripts/test_queries.py --query 1



# View results

python scripts/show_logs.py

```



---



## Workspace



Auto-created artifacts:

```

workspace/agents/{agent-name}/

├── out/      # Output files (000001.json, ...)

├── logs/     # Run logs (timestamp.json)

└── meta.json # Manifest

```



---



## Next Steps



System is complete and ready for:

1. Production deployment (minimal dependencies)

2. Integration with other systems (via file bus)

3. Extending predictive markets domains

4. Adding cross-agent workflows (predictions → analysis)



---



## Performance



- SQL execution: ~150-200ms

- File operations: ~2-5ms

- Full pipeline: ~400-500ms

- Zero network latency



---



**Status**: Production-ready, tested, documented, optimized. 🚀



## Recent Changes (2026-01-29)



### PPI Weeks Export (PricingMonkey) ✅

- `pm_nfp_weeks_export.py` can now pull PPI dates from `workspace/economic_events.db` via `--from-db` and optional `--event-name`/`--start-date`/`--end-date`.

- Default output remains CSV next to the script, override via `--out`.

- Added a login pause before scraping; disable with `--no-wait-login`.

- Added `--login-wait-secs` to wait for manual login when stdin is unavailable.

- Output CSV now keeps only one row per DATE.

- Parsing now extracts numeric values from text to keep FWD_BE populated.
