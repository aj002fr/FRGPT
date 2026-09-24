# Progress

## 2026-09-24

- V3 5053 page freeze + day-so-far running sum (afternoon). The page redrew every chart on every live message (per-message `extendTraces` + `relayout`, ~128k SVG bars in Panel 3), so tabs froze ("page isn't responding") and the banner fell ~5 minutes behind while the server sat idle. `dashboard.js` now queues prints, redraws once per second, min/max-decimates per pixel, and draws Panel 3 bars in WebGL. Verified live in the browser on the running server: banner lag 0–1 s, main thread ~12% busy (was ~39% after batching alone, frozen before). Server (on disk, tests pass, **not yet live**): TotalAggress and the Panel 4 path are session-to-date from 18:00 ET (spec; the old rebuild restarted the sum at the 2-hour window edge, which caused a 41 MB `history` resend every rebuild and steps at the live tail); live-first session merge per rebuild; backfill `covered_end` = last fetched print (fixes the repeating Databento 422 gaps); faster `build_print_frame`; BuySell smoothing no longer resets on same-timestamp prints. Tests: 55 pass. The V3 restart could not run because Cursor's shell hit `EMFILE: too many open files`.

- V3 5053 quick fix for "delayed / not responding". The 9/23 21:07 process had used one full core for 14 hours (~52k CPU-s, ~3 GB). Two causes: `print_reconcile._matched_incoming` scanned whole side/size/price buckets for every print (quadratic; minutes per rebuild on the 2-hour tape), and the rebuild never stopped, because `ensure_beta_ref(compute=True)` returned the stale 21:05-bucket entry and `_kick_beta_ref` still called `offer_history()`. Fixed with a time-bounded matcher (identical matches; 0.12 s for 30k vs 30k), `ensure_beta_ref(refresh_stale=...)`, beta computed once per process then held (user choice), `offer_history()` only after a fresh beta, and no per-rebuild deep copies of the 10-session frames. Tests: `tests/test_v3_trade_to_trade.py` + `tests/test_v3_live_stream.py` (46 pass). Restarted 5053 at 11:47 ET (Python PID 19800): idle ~0.6% of a core, ~17% averaged over a backfill cycle (one ~12–23 s rebuild every ~4 min), live trade events ~0.6 s behind the exchange. Beta refreshed for the 11:45 ET bucket (curve 3.16e-08, 10 sessions) and held. Open items are listed in `activeContext.md`.

## 2026-09-23

- 5065 return distributions now include the 5Y auction day: 08:00–13:00, 10:00–13:00, and 13:00–17:00 on the 5Y date, next to the same three 2Y-day charts. Static supply-weeks HTML dumps unchanged. Manual 5065 restart required.
- Restarted isolated V2.2 (5058) and V2.7 (5063) so the LIFO FRIENDLY lean colors are live. V1/V2/V3/5064/5065 left running.

## 2026-09-22

- Isolated V2.2 / V2.7 LIFO FRIENDLY banner is now green on fade-long (`residual_z < 0`) and red on fade-short (`residual_z > 0`). Banner text unchanged. Display-only; live processes not restarted.
- V3 5053 is a live viewer: one publisher, `GET /api/live/stream`, trade-clock deltas. Empty-tape WARMING is not the shared picture; cold start can paint 15 minutes from parquet. Past strip colors stay put unless a hole-fill changes cumsum or `beta_ref` changes. Tests: `tests/test_v3_live_stream.py`. Live V1/V2 untouched.

## 2026-09-21

- Live Gamma Tracker V6 upcoming-events banner now reads Yaman `events.sqlite` (US parent prints). Old `economic_events.db` has no future rows after 2026-08-14. EventScorer / percentile conditioning unchanged. Tests: `tests/test_upcoming_events_banner.py`. Dash restart required for 8055.
- 5065 compare rows show a flow-implied bps: normal-day OLS at the auction median net, plus so-far implied while a clock is open. Auction-day move stays the observed median price change.
- 5065 net-vs-bps plots show the OLS beta (bps per $1mm net) on each window.
- 5065 auction-vs-normal board now tracks the current 2/5/7 week live. This week (2026-W39, auctions Tue–Thu) comes from the events calendar because the processed auction CSV still ends in June. Open clocks show so-far flow and stay out of the historical median until they finish.
- V3 5053: compact READY-state color bars under Panel 1 (`p1_states`). Same lookback as TotalAggress; WARMING/UNAVAILABLE left blank. Tests in `test_v3_trade_to_trade.py`. Live 5050 untouched.
- Flow-history 5064 polls `/api/today` every 15s after Load (so-far cards, LIVE badge, today’s intraday). Closed days stay cached for the Globex session; only the current session is re-folded. Boxplot / dist-tag controls are hidden. Live 5050 untouched.
- Started isolated Aggression V2.7 on 5063 (PID 45952, AccessAPI connected, B1_W10 fit 2026-09-21). V1/V2/V2.2/V3/5064 left running.

## 2026-09-20

- V3 live `/api/aggression` 400'd on duplicate print timestamps in `apply_beta_ref_path`. First priced row is taken by position. Test added.

- Flow-history viewer (5064): Globex session days, LIVE badge, Yaman tags, OLS/distributions, today analog. Residual QUIET tag removed. 2022+ AccessAPI backfill complete. Load latency: single `/api/bootstrap` + in-process daily cache; LIVE uses `MAX(ts_utc)` only. Tests: `tests/test_v1_flow_history_dash.py`. Live 5050 untouched.

## 2026-09-17

- V3 5053: second hover banner classifies the 15-minute six-state at any chart timestamp. Live banner unchanged. Tests in `test_v3_trade_to_trade.py`.

- V3 WARMING “configuration change” was a false gate: global banner key oscillated (TY-only cache vs curve chips) before the missing-beta check. Banner uses the real 15m/beta reasons; `beta_ref.json` is multi-entry and reuses last-good across TOD buckets. Restarted 5053 at 14:05 ET. Live 5050 not edited.
- V3 session store is hole-only: live first, then hist, 1ms identity match, no `hist_max` cut. Panel 2 bins use epoch origin. Tests 23/23. 5053 not restarted. 5050 not edited.
- V3 `beta_ref` uses the last 3 hours to the displayed ET clock on each of the last 10 sessions (TY vs TY default). Restarted 5053. Live 5050 not touched.
- Live V1 Panel 1 is now trade-to-trade last traded price vs session print-clock TotalAggress cumsum. P2–P5, OLS, banner, and sqlite history stay 1-minute. Tests: `test_v1_tick_aggress.py`, `test_v1_panel1_hover_stable.py`, `test_v1_session_minute_bars.py` 15/15. Restarted 5050 at 08:52 ET (PID 24880, AccessAPI connected). Watchdog left running. Other monitors not restarted.

## 2026-09-16

- V3 Panel 2 now plots a 30s time-weighted EWM of the raw ±100 print ratio (capped at 4000 points, y-axis ±100). Spec EMA(10) stays on the print frame and is not drawn. Banner / β_ref unchanged.
- Replaced live V3 (5053) with the trade-to-trade six-state monitor from `docs/aggression_v1_monitor.tex`. Package `scripts/aggression_dashboard_v3/`. Store `aggression_v3_live/` (old logs kept). Flow-effort moved to `scripts/aggression_flow_effort.py`. Running 5053 was not killed; V1/V1.3/V2 untouched. Tests: `tests/test_v3_trade_to_trade.py`.
- Filed the revised V1 charts + six-state banner note as `docs/aggression_v1_monitor.tex`. Discussion spec only: fixed reference beta, `d=a+b`, six READY headlines, `q=1`, `r=1.5`, no dynamics. Live 5050 four-state banner and stores were not changed.
- Added Panel 2 BuySell readability caption and percentage map to that note. Display spec only; live 5050 not edited.
- Spec `beta_ref` is now a 10-session rolling mean (ideal: last 3h to display TOD; later day-tagged). Live dashboard unchanged.
- Spec clock is last-trade price and trade-to-trade increments on every plot and calculation. Live 5050 unchanged.
- Spec betas are change-on-change (`ΔP` on `ΔA`); not absolute `P` on `A`. Live dashboard unchanged.
- Spec: one `beta_ref` for banner and Panels 4–5 (10-session mean). Charts no longer have their own session/pooled OLS.

## 2026-09-15

- Isolated V1.3 tick-TotalAggress copy (`scripts/aggression_dashboard_v13/`) on manual-only port 5055. P1 jumps on every print; P2–P5/banner stay 1m. Store `workspace/analysis/aggression_v13_live/`. Named V1.3 because V1.2 is already 5056 RANGE-caution. Tests: `tests/test_v13_tick_aggress.py` 4/4. Live V1/V1.2 not started or restarted.
- V1 panel 1 TotalAggress no longer paints a stale ruler across live mids when flow bars have a hole. Forming-minute-only 1s ingest, index-merge upsert, sqlite hole-heal, 2% axis deadband. Tests: panel1 hover, session bars, flow history. Live 5050 not restarted (needs a manual V1 restart). V2/V2.2/V2.6/V2.7 untouched.

## 2026-09-14

- Isolated read-only V1 flow-history viewer on 5064: daily bid/ask volume, auction/month-end/NFP/CPI/FOMC tags, and bid-vs-ask vs same-day ticks. Package `scripts/aggression_flow_history_dash/`. Tests: `tests/test_v1_flow_history_dash.py` 4/4. Live 5050/5063 untouched.
- Added `docs/gamma_tracker_v6_implementation.md` (V6 stack, units, data history, ownership scores, implemented vs ideated). Runtime unchanged.
- Added durable V1 1-minute aggress buy/sell volume history (`workspace/analysis/aggression_v1_live/flow_history.sqlite`). Cache backfill wrote 180,107 rows; after the 10:41 ET V1 restart the store is 225,848 rows through 2026-09-14 14:42Z and live minutes are appending. Tests: `tests/test_v1_flow_history.py`.
- Started isolated Aggression V2.7 on 5063 (PID 32372, AccessAPI connected). V1 on 5050 left running. V2/V2.2 not started.
- Gamma Tracker V6 event score: only still-ahead events owned by the expiry; same-day uses `1st` weights; post-print contribution is 0. No 0.3 floor or tier τ_before floors. Tests: `test_event_score_ownership.py` 5/5. Restarted live 8055 at 10:53 ET (PID 7984). Writer untouched.
- Gamma Tracker V6: wing-spread heatmap ATM column is now `n/a` (same on the event-dist surface boxes). Raw IV still ranks ATM. Restarted live 8055 (PID 15444, nosite launcher). Writer untouched.

## 2026-09-11

- V1 hover banner stays on the last hovered timestamp (live refresh / Plotly unhover no longer reset it to WARMING). Restarted 5050 at 16:48 ET (PID 67072, AccessAPI connected). V2/V2.2/V2.7 were not restarted.
- V1 flow-dominance banner now classifies local 15-minute price sums (no session z). Quiet is both legs below 2 TY ticks; INCREASING/SLOWING uses a 1-tick deadband. Hover series follows the same rule. Tests: `tests/test_v1_flow_dominance_context.py`. Restarted 5050 at 13:49 ET (PID 39096, AccessAPI connected). V2/V2.2/V2.7 were not restarted.
- V1 charts can show the descriptive flow-dominance banner at any hovered timestamp. `flow_dominance_series` is built from the same completed-bar path as the live banner, then clipped to lookback. The live top banner stays current. Local **Banner on hover** checkbox (default on). No `banner_alerts.sqlite` writes from hover. Tests: `tests/test_v1_flow_dominance_context.py`. Restarted 5050 at 12:01 ET (PID 44332, AccessAPI connected). V2/V2.2/V2.7 were not restarted.
- V1 panel 1 no longer re-zeros TotalAggress at the lookback left edge. A finished minute’s hover value stays on the session sum (18:00 = 0). OLS fits still re-zero internally. Tests: `tests/test_v1_panel1_hover_stable.py` 2/2. Restarted 5050 at 11:32 ET (PID 67376, AccessAPI connected). V2/V2.2/V2.6/V2.7 were not restarted.

## 2026-09-10

- Hid `AggressionMonitorV1Watchdog`: the every-minute PowerShell check no longer pops a console. V1 on 5050 was not killed or restarted.
- V1 live path now keeps session 1-minute bars, publishes them in the background, and serves Tailscale/HTTP from that snapshot instead of rebuilding session ticks every second. 10-day raw ticks are dropped after resample. Tests: `tests/test_v1_session_minute_bars.py`. Restarted 5050 at 11:22 ET (PID 67944, ~168 MB at start). V2/V2.2/V2.7 left running.

## 2026-09-09

- V1 live `/api/aggression` now caches the last payload for 1s (`LIVE_RESULT_CACHE_TTL_SEC`) so extra Tailscale/browser sessions reuse one pandas rebuild. Banner/math unchanged. Tests: `tests/test_v1_live_result_cache.py`. Restarted 5050 at 17:10 ET (new PID 27352). V2/V2.2/V2.7 left running.

## 2026-09-08

- Ran offline LIFO literature study (`scripts/aggression_v2_lifo_literature_research.py`): Track P same-bar R² 0.25 vs lead-1 ~0; L1/L3 FAIL; L2 zone +8.3pp but fade 48% and absorb_bar alone fails. `ship_gate` false. Tests: `tests/test_v2_lifo_literature.py`. No live monitor or H5 changes.
- Flow-forecast diagnostic (`scripts/aggression_v2_lifo_flow_forecast_research.py`): past flow does not predict future flow (\(R^2\le 0.003\)); oracle future-flow→price is strong; two-stage λ×F̂ is not. `ship_gate` false. Tests: `tests/test_v2_lifo_flow_forecast.py`.
- Path follow-up (`scripts/aggression_v2_lifo_path_followup_research.py`): trail meat letters meat only; range persistence fails validation; fade 53%; US morning letters zone. Event mask actually applied this run. `ship_gate` false.

## 2026-09-07

- Offline LIFO-zone study (`scripts/aggression_v2_lifo_zone_research.py`): frozen 15m two-way ≥3-tick label. Live H5 LIFO FRIENDLY *lowers* P(zone) (−4.3pp validation). Quiet tape is anti-clip. `ship_gate` false. No live process changes. Tests: `tests/test_v2_lifo_zone.py`.
- Rewrote the V2.7 goal in `docs/aggression_v27_monitor.tex` and the lineage note: useful job is a 10-minute consistency check (explained vs unexplained stretch), not a fade target. Paper clock documented as diagnostic only. Docs only.
- Added `docs/aggression_v22_v27_from_v1.tex`: V1-companion researcher note for isolated V2.2 and V2.7 (lineage, definitions, clocks, LIFO). No dashboard or live-store changes.
- Restarted live V1 on 5050 after the 2026-09-05 19:08 ET share/OpenBLAS crash. Added crash keep-alive: `run_aggression_monitor.bat` restart loop plus scheduled task `AggressionMonitorV1Watchdog` (`watch_aggression_monitor_v1.ps1`) that starts V1 only when 5050 is down. V2/V2.2/V2.6/V2.7 were not restarted.

## 2026-09-03

- Added standalone Gamma Tracker V6 package under `Z:\uikitxv2\OptMonitor\gamma_screener\gamma_tracker_v6_standalone\`. Own writer/store/dash copy; no v2/v3 imports; seeded slim `gamma_tracker_v6.db` (147 MB vs 1.1 GB v3). Live 8055 / v3 writer / v3 DB untouched. Tests: `test_standalone.py` 6/6.
- Offline V2.7 × V1 zone score on **all available** current-rule trades: 185 B1_W10 OOS (2026-02-03..2026-06-05) + 14 live week = 199. Gap Jun 6–Aug 30 has no B1_W10. Only `AGGRESS_SINGLE` nominated (n=7, +7, 57%). Historical-only: no nominee (AGGRESS +43 ticks at 50% hit). Not a live gate; 5063/5050 unchanged. Tests: `tests/test_v27_v1_zones.py`.
- Added append-only SQLite `workspace/analysis/aggression_v1_v27_banner_alerts/banner_alerts.sqlite`: live V1 banner **changes** (5050) and V2.7 ENTRY/EXIT (5063) with the latest V1 banner stamped on each alert. Banner/clock behavior unchanged. 5050 and 5063 need a manual restart to start writing. Tests: `tests/test_v1_v27_banner_alerts.py`.
- Added `docs/aggression_v27_monitor.tex`: V1-style live-page note for isolated V2.7 (B1_W10, conviction gate, paper clock, LIFO overlay, research provenance). No dashboard or live-store changes.
- Fixed V1 `DATABENTO BACKUP DOWN - previous Databento backup thread is still stopping` flicker: shared `DatabentoLiveBackup` serializes start/stop and waits 15s for a stopping worker before failing; V1 only advertises `databento_backup` after `start()` succeeds; live `POST /api/aggression` coalesces identical in-flight rebuilds. Tests: 10/10 `tests/test_aggression_feed_failover.py`. Live V1/V2.2/V2.6/V2.7 were not restarted; V1 needs a manual restart to pick this up.

## 2026-09-02

- Isolated V2.7 no longer flattens at +10m. 10m stays a score/display mark. Exits remain z-reset after 5m min-hold or flip. `ship_gate` false.
- Isolated V2.7 session-day banner and Today’s table start at 2026-09-02 12:07 ET for this Globex session only; after the next 18:00 ET open they use the full trading day. Same-sign re-entry still uses the full 18:00 session.
- Isolated V2.7 on 5063 now gates paper entries: `|z|≥1.5` plus session residual 2–6 ticks, same sign as the local residual, and no same-sign re-entry after a finalized episode. Session residual is a read-only join of live V2 `signal_log`. Skip reasons `SESSION_UNAVAILABLE` / `NOWCAST_ONLY` / `REGIME_TAIL` / `DISAGREE` / `SAME_SIGN_OPEN`. `ship_gate` still false.
- Added `--mode corrections` (`scripts/aggression_v2_executable_pnl_corrections.py`). One frozen miss per cell on cached TBBO; writes `workspace/analysis/aggression_v2_executable_pnl/corrections/`. Official rank is bid/ask after 0.5-tick cost. `ship_gate` false.
- C1: `|session ticks|>=2` still holds ~88/85/78% at 30/60/120m on yesterday’s tape. C4 closed (no new Ridge). I1 mid(3,2) equalizes TP/SL on holdout but after-cost stays ~−1.5. I2/I7 cut n without a mean lift. L1: 52 live trades, 28/37 Z_RESET had `% via price <= 0`, 31 nowcast-only. Those cells authorized the V2.7 alert-rate gate above, not an after-cost PASS.

## 2026-09-01

- Added `scripts/aggression_v2_executable_pnl_research.py` and `tests/test_v2_executable_pnl.py`. Entry/exit ARE session G + agree + V1 aggress veto + price +N/−X (not flags). Official 2026-09-01 tape with AccessAPI bid/ask is under `workspace/analysis/aggression_v2_executable_pnl/`. Live V2.7 5063 unchanged. `ship_gate` false.
- V2.7 top banner now shows session-day ticks captured and issued/closed/open counts since the last 18:00 ET open. Today’s table uses that same window. Research display only.
- V2.7 outcomes table now scores **trade pnl** from entry print to that trade’s exit (z-reset or 10m). Each entry→exit is a separate trade; z-reset frees a new entry instead of blocking until +10m. Research display only.
- V2.7 live screen now names paper ENTRY (SHORT/LONG at last print) and the two EXIT clocks (z-reset after 5m, 10m mark). Chart markers + outcomes table labels. Research display only. Manual V2.7 restart required. No other monitor touched.

## 2026-08-31

- Added `scripts/aggression_v2_alert_clock_research.py` and `tests/test_v2_alert_clocks.py`. Offline 38-clock replay of V2.7 alert rules on B1_W10 residuals (wall / prints / volume / tick-minutes / TOD-adaptive / hybrid). Writes `workspace/analysis/aggression_v2_alert_clocks/`. Report: `docs/aggression_v2_alert_clock_experiments.md`. Usable holdout clocks keep median % via price = 0; fixed print/volume counts collapse to ~1 minute; `tickmins_3` is a research lead only. Live V2.7 10m wall clock unchanged. `ship_gate` false.
- Added isolated Aggression V2.7 (`scripts/aggression_dashboard_v27/`) on manual-only port 5063: V2.2 copy with B1_W10 10-minute local fair value and enter 1.5 / exit 0.5 / min-hold 5 / 10m alert clock. Store `workspace/analysis/aggression_v27_live/`. Named V2.7 because V2.3 is already the move-exhaustion monitor. Live V2/V2.2/V2.3 untouched. Docs: `docs/aggression_v27_local_fv.md`.
- Added `scripts/aggression_v2_short_horizon_fv_research.py` and `tests/test_v2_short_horizon_fv.py` (8/8). Offline-only writes under `workspace/analysis/aggression_v2_short_horizon_fv/`. Report: `docs/aggression_v2_short_horizon_fv_experiments.md`.
- 74 clean dates, locked last-20% holdout 2026-05-13–2026-06-05. Track A letter-informative but R² lift vs random walk is ~10⁻⁵ (same empty forward-price fit as the 2026-07-09 after-cost FAIL). Track B local R² is real except `W=5` (`mom_5m` leak). Track C sweet-spot candidate: `B1_W10` + enter 1.5 / exit 0.5 / min-hold 5 (median lean 5.5m, 5.8 flips/hour, `P(5m adverse≤3 ticks)=0.98`, n=506). `ship_gate` false. Live V2/V2.2 untouched.

## 2026-08-24

- Added `docs/gamma_tracker_v6_scientific_record.tex`, matching the V1 monitor note: concept, writer/dashboard stack, formulas, each chart, parameter register, and how to watch. OptMonitor runtime unchanged.

## 2026-08-22

- Added a closing ``What we are watching'' section to the V1 note: nine reading types (explained move, aggress-led breakout, implied-passive range, unexplained extension, absorption, exhaustion, ignition, session vs 10-day, curve vs chosen price). Explicitly not RANGE/TREND or long/short.
- V1 note now defines one session TotalAggress: running sum from 18:00 ET, first minute kept, lookback does not re-zero, same \(A_t\) on charts and both fits. Live dashboard still has the old split path (edit later).
- V1 default start is now the Globex trading-day open (18:00 ET): most recent 18:00 ≤ now. Panel 5's 10-business-day window starts at that first date's 18:00 ET open. Port 5050 was not restarted.
- Replaced the aggression monograph with a V1-only live-monitor note: panels, banner, parameters. Other versions removed from that file for now.

## 2026-08-21

- Rewrote only the monograph conceptual-state block: price-path exhaustion/breakout/balanced/compression (bp + 100-print aftermath); former flow wording retained as hypotheses; trader-facing example aligned.
- Continued the approved rewrite of `docs/aggression_v1_v22_research_program.tex` after [Audit ontology and LaTeX](1b4f7faa-91e4-4b59-b727-cd645227608d). Applied remaining clock, denominator, qualification, and notation defects (ME-II price-point \(B\delta\)/\(x\delta\) with running-extreme pullback; six distinct residual z-scores; z-resolved vs barrier-resolved; F7/RepR/\(g^{\mathrm{FV}}\); options 70/30 later cohort; D1 decluster and \(\max(\cdot,\eps)\)).
- Three later claim/notation/scientific audits failed on model-usage limits and produced no additional findings. Environments/braces balance (79/79, depth 0). `pdflatex` still missing. Memory Bank updated; `io-schema.md` unchanged.

## 2026-08-19

- Added `docs/aggression_v1_v22_research_program.tex`, a chronology-first, self-contained LaTeX monograph covering the full V1/V2/V3 monitor lineage; dispersion, dominance, tradability, event, depth, options, and non-flow research; Move Exhaustion E00–E30; Move Exhaustion V2 X0/X1/retired X2–X10/C001–C018; and autoresearch D001/E001/E002 plus the Hypothesis Lab.
- Reconciled exact live V1/V2.2 terminology and executable definitions, separated the three LIFO constructs, recorded cohorts/gates/findings/verdicts/sources, and retained negative/provisional results, locked-FINAL boundaries, governance conflicts, and live/offline parity gaps.
- Completed independent content and static-LaTeX audits with no actionable defects remaining. `latexmk`/`pdflatex` are unavailable locally, so PDF compilation was not executed. Updated `code-index.md` and `activeContext.md`; `io-schema.md` is unchanged because no runtime I/O surface changed.

## 2026-08-17

- Added the user-approved descriptive V1 flow-dominance banner under `scripts/aggression_dashboard/`. `flow_dominance_context.py` uses completed 1m bars and the frozen intraday model to compare `beta * delta(TotalAggress)` with implied-passive `delta(residual)`, reporting fixed 15m/1.5/0.5 states, transitions, and five-minute increasing/slowing/steady dynamics.
- `POST /api/aggression` now returns `flow_dominance`; the compact UI keeps the fixed `DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED` label. No RANGE/TREND or execution claim, episode/store write, SOD/Tailscale/Telegram wiring, or automatic process restart was added.
- Added `tests/test_v1_flow_dominance_context.py` and stabilized the existing live-buffer regression timestamps so they remain inside the 18-hour retention window. Focused banner/API/HTML plus feed-failover tests pass 14/14; Python compilation and JavaScript syntax pass. Port 5050 was not restarted.
- Completed isolated Aggression V2.6 (`scripts/aggression_dashboard_v26/`), manual-only on browser-safe port `5062` (migrated from Chromium-blocked SIP-TLS port 5061), with output restricted to `workspace/analysis/aggression_v26_live/`.
- Preserved V2.2 LIFO FRIENDLY and added reduced C018 display context with a hard-coded `10.5`-tick prefix T3 cutoff, a 30-bar completed window ending at each candidate, forming-bar exclusion, 8-tick edge trigger, 15-minute declustering, and opposite fade lean. Fixed status is `PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE`; C018 is explicitly not offline C018.
- Added append-only human feedback for LIFO/C018 (`POST /api/feedback`, Yes/No/Skip prompt, last grade wins in episode JSON) and today-ET cards for issued, pending, graded, correct, wrong, and graded hit rate. Human feedback alone defines “right.”
- Converted the grade UI to a small non-modal bottom-corner prompt that does not block dashboard interaction or steal the user’s existing focus.
- Added focused tests: 9 V2.6 tests pass; 14 pass with V2.2 regressions. Python compilation, `dashboard.js` syntax, HTTP checks, and real browser rendering pass on port 5062. The old 5061 process was stopped and verified closed; V2.6 is now running on 5062. No other dashboard/store or SOD/Tailscale/Telegram surface was edited or restarted.

## 2026-08-13

- Completed the 75-date Move Exhaustion V2 loop without expanding discovery (C001–C018). C016/C017 null; C018 30m range +18.1 pp descriptive caveat. Remaining BLs parked as blocked. Holdout locked. Not evidence.
- C015 TOD baseline on the 75-date prefix: RTH − non-RTH reversal +10.4 pp. PARKED as descriptive X1 caveat. R003 written (15 closed cycles). No AF×TOD. Holdout locked.
- Ran approved BL-3 spec as C014 on the 75-date prefix. PARKED: IS T3−T1 −11.3 pp; corr(IS, Δm)=−0.96 (E07). No C015. Holdout locked.
- Drafted Move Exhaustion V2 BL-3 expected-impact spec (`research/loop/specs/BL3_impact_shortfall_spec.md`). Prefix-only; C014 not opened; holdout locked. Awaiting human run approval.
- Added automatic direct-Databento Live TBBO failover to aggression V1 and isolated V2.2. AccessAPI remains primary; stale/disconnected feeds switch after 30s by default with intraday replay/per-symbol boundary dedupe, then return after 60s sustained recovery. Active source is exposed in both UIs. Live V2 and all runtime/SOD/Tailscale/Telegram surfaces stayed unchanged; manual V1/V2.2 restarts are required.
- Added shared `scripts/databento_live_backup.py`, exact sequence/nanosecond-time/payload dedupe plus receive-time replay floors (preserving multi-trade CME messages while blocking evicted old replays), advancing replay cursors, shutdown guards, a standalone smoke harness, and collectible integrity tests. Deterministic V1/V2.2 simulations pass; real Databento smokes received 590 rows/30s and 32 rows/10s across FV/TU/TY/US; focused failover + existing V2.2 suites pass 13/13.
- X0 selected-row store complete (1095 days, 88.247 GiB). QC replay: 1060 research-eligible. Q7/Q8 PASS (36 comparable dates; Feb 24–25 excluded). `finalize_x0` **PASS**: 1042 eligible; splits discovery 731 / validation 234 / holdout 57 locked. Holdout unopened. C001 not opened.
- X1 sufficient-prefix baseline recorded (`X1_20260813T143427Z`): 75 discovery dates 2022-01-05→04-25, 854 resolved, P(REVERSAL)=0.3529. Human waived Fable 5. C001 (BL-1) launched on that prefix. Holdout unopened.

## 2026-08-12

- X0 volume prepass completed over the verified C: archive (52 monthly files; 1096 plan rows / 1095 research-eligible; holdout locked).
- March 2026 selected-row store pilot PASS: 22 parquet days, 3.185 GiB, projected full store 158.7 GiB vs ~411 GiB free (`disk_pass=True`). Isolated store `C:\FRDBs\FutMBP\move_exhaustion_v2_store\pilot_202603`.
- Real Databento MBP-1 mappings: `F` fill = trade; no-op signed modify = `MOD_UNKNOWN` (L1-invisible priority/time change). Chunked pandas/zstd scan replaced DuckDB full-month materialization (OOM).
- Full selected-row store build started (`py -3 scripts/move_exhaustion_v2/data/x0_run.py store`) to `C:\FRDBs\FutMBP\move_exhaustion_v2_store`. Q7/Q8 parity and `finalize_x0` wait on completion. No live dashboards, D:, SOD, Tailscale, or Telegram writes. C001 not opened.

## 2026-08-11

- Restructured Move Exhaustion V2 into an advisor/worker autoresearch loop (user-directed; PROGRAM.md §10 addendum). New `research/loop/`: LOOP_PROTOCOL.md (roles, two evidence tiers, cycle lifecycle, human checkpoints, promotion protocol), BACKLOG.md (BL-1…BL-15 seeded from retired contracts + spec), append-only JSONL journal + `tools/loop_journal.py`.
- X2–X10 contracts flipped to `RETIRED_TO_BACKLOG` (metadata only; hash-pinned science sections intact — manifest verified unchanged). X0/X1 remain FROZEN prerequisites. EXPLORATORY tier = discovery data only, `NOT EVIDENCE`; CONFIRMATORY tier = human-approved frozen contract + one-shot validation under gates.
- Added `tests/test_loop_integrity.py` (journal validity, cycle-folder shape, discovery-only enforcement for exploratory cycles, confirmatory-contract citation, retired-status guard): suite now 25 pass / 3 scaffold-skips. Root AGENTS.md workflow replaced with the loop workflow; STATE.md updated (0 cycles, loop blocked on X0/X1 approval).
- Loop hardening after user review of assignment/self-improvement/metrics/logging: added AGENT_ROSTER.md (model policy + mandatory worker provenance + replication-before-promotion), SCOREBOARD.md (north-star PQs, candidate ledger vs X1 baseline, KPI table), PLAYBOOK.md (process memory), LOOP_PROTOCOL §9 mandatory retrospectives (1/5 closed cycles, `retro`/`process_change` journal events, process-only scope). meta.json completed cycles now require `worker_model` + `headline_metrics`; tests enforce provenance, metrics, and retro cadence. Suite: 27 pass / 3 scaffold-skips. Journal seq=2 governance entry recorded.
- Model assignment locked in (user-directed; journal seq=3): DeepSeek `deepseek-v4-flash` workers via `npx --yes @openai/codex exec` (smoke-tested: banner shows model/provider deepseek, replied OK); Fable 5 + GPT Sol dual-advisor panel with independent-then-consolidated reviews, conservative disagreement resolution, unanimity for promotion. AGENT_ROSTER/LOOP_PROTOCOL/AGENTS.md/STATE.md updated; `advisor_models` enforced in tests. Suite: 27 pass / 3 scaffold-skips.
- User authorized all work required for loop readiness (journal seq=4): exact
  one-shot `$0` ZN MBP-1 replacement batch for 2022-01-01→2026-04-02, X0, and
  X1 contingent on X0 PASS. Discovery narrowed to 2022–2024; Validation remains
  2025; final holdout remains locked. OQ-1/2/5/6/10/15 answered and OQ-3/4
  deferred. PROGRAM §10, frozen configs, contracts metadata, STATE, and the
  human-facing design log updated before implementation.

## 2026-08-10

- Initialized the Move Exhaustion V2 agentic research harness under `scripts/move_exhaustion_v2/` (no experiments run; no scientific definitions changed). Program of record `research/PROGRAM.md` preserves the design from `docs/move_exhaustion_v2_experiments.md` verbatim; `research/STATE.md` starts at X0 with the final holdout (proposed 2026-01→2026-04) LOCKED.
- Frozen machine-readable config in `config/frozen/` (episodes, labels, splits, gates, data_store) hash-pinned via `MANIFEST.sha256` (`tools/freeze_manifest.py`). Frozen contracts X0–X8 plus skeletal X9/X10 under `experiments/`. Repo-wide `AGENTS.md` created at root mapping to the program's source-of-truth documents.
- 15 design ambiguities recorded unresolved in `research/OPEN_QUESTIONS.md` (OQ-1…OQ-15); OQ-5/OQ-6/OQ-15 block X0 completion. Mechanical tests: 18 pass, 3 scaffolds skipped pending Phase 0–2 code (`py -3 -m pytest scripts/move_exhaustion_v2/tests`).

## 2026-07-28

- Completed move-exhaustion E23–E30 offline under `scripts/move_exhaustion/` / `workspace/analysis/aggression_v2_move_exhaustion/`. Holdout never opened.
- E23/E25 unavailable (MBP-1 0 parquet; no MBO). E24 MBP-10 overlap incremental not supported (ΔAUC=0). E26 after-cost policies fail (primary mean net −0.54 ticks). E27 selective top-decile promising but sparse. E28 ablation completed. E29 placebos 5/6 pass. E30 unstable.
- Final report: `docs/aggression_v2_move_exhaustion_final_report.md`. Ports 5050/5052/5056/5058 still listening; no dashboard/`*_live` changes.

## 2026-07-27

- Updated `scripts/supply_weeks_dashboard.py` with independent, default-on January and Month-End annotation checkboxes for the return-distribution chart.
- Regenerated `scripts/supply_weeks_dashboard.html` and `dashboards/supply_weeks_dashboard.html` from 138 matched auction weeks through `2026-W26`. June's week is included; PostgreSQL currently has zero TY points for `2026-W13`, `2026-W18`, and `2026-W26`.
- Verified the generator compiles and both HTML outputs contain the current timestamp, June week, and annotation controls.

## 2026-07-24

- Completed the manual-only Autoresearch Hypothesis Lab at `scripts/autoresearch_lab/` on port `5057`. Its Evidence lane renders frozen campaign decisions, per-cell eight-gate diagnostics, discovery/power/tradability audits, the D001 243-cell map, manifest integrity, and the active read-only exposure ledger.
- Added a hard discovery-only matrix firewall, cached discovery slice, isolated frozen/custom candidate execution, parameterized outcome wrappers, deterministic chronological sub-splitting, 999-resample exploratory scoring, optional circular shifts, background jobs, and an append-only search/multiplicity journal. All outputs stay under `workspace/analysis/autoresearch_lab/` and are labeled `EXPLORATORY / DISCOVERY-ONLY / NOT EVIDENCE`.
- Added schema-validating preregistration authoring with canonical/effective hashes, exposure prechecks, frozen specs/contracts/journal excerpts, and non-authorizing manifest scaffolds. Holdout availability is explanation-only: no validation, quarantine, confirmation, `StateStore`, or execution endpoint is present.
- Added 11 focused regression tests and verified the full 110-test autoresearch suite. Browser smoke loaded the port-5057 app and all APIs without application console errors; no hash-bound campaign or live aggression file changed.
- Added fail-closed `run-validation` and `run-quarantine` execution paths with atomic pre-outcome exposure reservation, discovery-result/state hash verification, transform-only candidate reuse, frozen logistic models, 9,999-resample clustered inference, fixed Holm denominators, serial shift falsification, utility/stability gates, and E002 delayed-entry economics.
- Completed E001 validation as `not_confirmed` (ALL effect `0.00000506`, CI `[-0.00008769, 0.00009930]`) and E002 validation as `not_confirmed` (ALL_ELIGIBLE effect `-0.00042768`, CI `[-0.00080779, -0.00005070]`). E002 delayed-entry net expectancy was `-0.4774` tick after cost.
- Completed non-evidentiary quarantine; its 24 dates are below the fixed 25-date predictive floor and cannot overwrite validation. Published `research_branching_v1.json`, closing E001/E002 and preserving D001 nominations as new-spec/prospective-cohort proposals only.
- Retained append-only failure audits while fixing finite calibration serialization and sparse date/session shift handling. Current authorization chain ends at `execution_manifest_v5.json`; confirmation/live use remain unauthorized. All 99 autoresearch tests pass.

## 2026-07-22

- Completed Phase 4 discovery-only execution for D001/E001/E002 with immutable result bundles and no validation exposure. D001 nominated two descriptive cells; E001 selected 60m/1-tick/2m (pooled OOF Brier lift `0.00010274`); E002 selected 30m/ridge-10/2-tick entry gap (pooled lift `0.00000159`). Both predictive power gates pass at 25 required vs 101 available validation dates.
- Fixed fail-closed E002 serialization of numeric audit-map keys, added regression coverage, and advanced the append-only discovery chain to `execution_manifest_v3_7.json` / `discovery_authorization_v8.json`. All 93 autoresearch tests pass. Validation, quarantine, confirmation, and live use remain unauthorized.
- Completed the Aggression Autoresearch Phase 3 execution bridge without opening outcomes. Added machine-readable execution contracts, checksummed matrix/candidate separation, runner-bound state envelopes, E001/E002/D001 candidates, nested selection and validation machinery, D001's 243-cell descriptive evaluator, and non-self-referential result bundles.
- Frozen current append-only `execution_manifest_v2.json` (v1 retained as superseded audit); original Phase 2 specs/manifests were unchanged. Formation-only real-data preflight passed for 18 E001, 9 E002, and 1 D001 candidate configuration with all forbidden-call counters at zero; 89 isolated autoresearch tests pass. Real discovery, outcomes, validation, quarantine, and decisions remain unauthorized.
- Completed the design-only Aggression Autoresearch Phase 2 protocol. Published and hash-verified dense matrix `v20260722_phase0_v3_dense_20260226` (580,860 rows; 423 eligible dates); database incompleteness after 2026-02-26 remains audited in the broader publication.
- Added methodology schema v2, evaluator-owned overlapping cells, common-anchor/censoring enforcement, E001/E002 outcomes, clustered inference/falsification contracts, typed results, two-phase state sealing, five-attempt closure, exposure ledger, and atomic spec-bound confirmation.
- Frozen final split/event/exposure/spec manifests and preregistered E001, E002, and D001. Added `docs/autoresearch_campaign_design.md`. No experiment was executed and no live surface was changed.

## 2026-07-21

- Implemented Aggression Autoresearch Phase 0/1 infrastructure only. Added a research-only, versioned TBBO shard builder with contract/symbol integrity, causal prior-session roll selection, duplicate/coverage/parity gates, plus a target-isolated harness with strict H001-H004 schemas, clustered incremental evaluation primitives, transactional run/alpha/approval state, and synthetic tests. No E001 or real experiment was created; live V1/V1.2/V2/V2.1/V3 were untouched.




## 2026-07-17

- Added `scripts/aggression_v1_aggress_passive_dominance_param_sweep.py` and `docs/aggression_v1_aggress_passive_dominance_param_sweep_experiments.md`. Staged sweep of w/d/q/h and frozen/strict/loose RANGE-TREND packs. Stage-2 restrictive PASS: **0** (30 standard/moderate browsing cells ~5–6.7pp, BH fail); FINAL locked. No V1/V1.2 edits. Artifacts under `workspace/analysis/aggression_v1_aggress_passive_dominance_param_sweep/`.
- Added `scripts/aggression_v1_aggress_passive_dominance_research.py` and `docs/aggression_v1_aggress_passive_dominance_experiments.md`. Role/absorption aggress vs implied-passive dominance (QUIET separate) vs forward path-efficiency RANGE/TREND; ATR diagnostic only. Validation primary restrictive PASS: **0**; headline lifts ≤+2.35pp all BH fail; FINAL locked. No V1/V1.2 edits. Artifacts under `workspace/analysis/aggression_v1_aggress_passive_dominance/`.
- Added `scripts/aggression_v1_dispersion_foundations_research.py` and `docs/aggression_v1_dispersion_foundations_experiments.md`. H1–H10 hypothesis catalog (73 factors, depth/passive included) vs ATR/bps labels; 21,024 journal rows. Primary validation restrictive PASS: low trade intensity while large residual → CONTINUED_RANGE (+10.6pp); FINAL +8.15pp but CI fails — not shippable. Soft B=4 persist/speed PASSes exploratory. No V1/V1.2 edits. Artifacts under `workspace/analysis/aggression_v1_dispersion_foundations/`.

## 2026-07-16

- Updated V1.2 to explicitly surface soft **B=4** factor-sweep restrictive PASSes as display-only `SOFT_BREAKOUT_LEAN` (persistence/speed/large); label `PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL`. Primary `(30m,8bps,k=1)` still 0 PASS. Docs/UI/API/tests updated; manual restart required on 5056. Tests: `tests/test_v12_range_caution.py` (10 passed).
- Added `scripts/aggression_v1_dispersion_factor_breakout_sweep.py` and `docs/aggression_v1_dispersion_factor_breakout_experiments.md`. Offline grid of 39 residual factors × 60 `(h,B,k)` ATR/bps labels; 18,840 journal rows. Primary `(30m,8bps,k=1)`: 0 restrictive PASS (best BO lift ~1.3pp). Restrictive ladder: 18 PASS / 19 PROV / 9383 FAIL — all PASSes at soft B=4 only. FINAL not opened; no V1.2/V1/V2/V3 edits. Artifacts under `workspace/analysis/aggression_v1_dispersion_factor_breakout/`.
- Added offline move-exhaustion research package (`scripts/move_exhaustion/`). **E00–E30 complete**; holdout locked. Best pre-cost lead still E03 V4 (~+0.84t); E18 F7 research-promising but E26 fails after costs. Final report: `docs/aggression_v2_move_exhaustion_final_report.md`. No dashboard/stream changes.
- Added isolated Aggression V2.2 (`scripts/aggression_dashboard_v22/`) on manual-only port 5058: V2 copy + LIFO FRIENDLY alert/stats only; store `workspace/analysis/aggression_v22_live/`. Live V2 untouched. Docs: `docs/aggression_v22_lifo.md`. Tests: 5 passed.
- Added isolated Aggression V1.2 (`scripts/aggression_dashboard_v12/`) on manual-only port 5056 with provisional non-directional RANGE-caution (`|z|≥1` + 15m widening), episode logging, and 30m RANGE/TREND observational stats under `workspace/analysis/aggression_v12_live/`. Fixed PROVISIONAL labels; no SOD/Telegram; V1/V2/V3 untouched. Docs: `docs/aggression_v12_range_caution.md`. Tests: `tests/test_v12_range_caution.py` (8 passed).
- V1.2 LIFO FRIENDLY (H5/D001 family): alert text only + `lifo_stats` in live-week box; episodes tagged `alert_kind=lifo`. Tests: 12 passed. Manual restart of 5056 required to pick up UI.
- Added `scripts/aggression_v1_flow_price_dispersion_research.py`, `tests/test_v1_flow_price_dispersion.py`, and `docs/aggression_v1_flow_price_dispersion_experiments.md`. Causal V1 residual (prior-day OLS TotalAggress→price) dynamics were tested as RANGE/TREND predictors on 78 clean TBBO dates (43/19/16 discovery/validation/FINAL). Gate: **0 PASS / 1 PROVISIONAL / 69 FAIL**; provisional large-widening→RANGE (+8.13pp validation) is null on discovery and fails FDR. Dispersion classifiers underperform price+flow (AUC 0.530 vs 0.562). FINAL not inspected. Outputs only under `workspace/analysis/aggression_v1_flow_price_dispersion/`; no live apps/ports/`*_live/` writes.

## 2026-07-13

- Added staged TY option-flow scripts (`aggression_v2_option_flow_fetch.py`, `features.py`, `research.py`) and `docs/aggression_v2_option_flow_experiments.md`. API audit found excellent signed-flow/native-Greek quality but huge unfiltered transfer; direct local DuckDB predicate pushdown over 1,278 parquet dates solved acquisition. A 20-date/34,096-trade pilot ran with 6 discovery, 3 validation, 3 untouched FINAL dates. No direction or persistence edge and no candidate passed; extreme/large/ATM option flow showed provisional +0.5–1.35 tick range-lift hints over 5–60m, but only 3 validation dates and no FDR. Six-month expansion remains gated.
- Added `scripts/aggression_v2_skew_iterative_research.py` and `docs/aggression_v2_skew_iterative_experiments.md`. Five read-only adaptive iterations tested 81 taxonomy configurations, 864 magnitude/range cells, 360 persistence/regime cells, 88 direction cells and 48 context cells while reserving four FINAL dates. No validation candidate met n>=30, >=8 dates, positive CI and FDR; all 1,360 candidates were retained and FINAL outcomes were intentionally not inspected. Conclusion: current skew/vol history cannot support a stable TY signal or regime explanation; further threshold tuning on the same dates would overfit.
- Added `scripts/aggression_v2_option_shock_persistence_research.py` and `docs/aggression_v2_option_shock_persistence_experiments.md`. The read-only study measures option shock retention/half-life from 1m to strict 24h and tests persistent-vs-reverted TY paths. No holdout/FDR pass: price-quiet holdout has only 1–2 events per arm and 6 usable TY-link events across 3 dates. Pooled ATM vol shocks show ~17m half-life and ~29–36m full reversion; any persistent-shock TY range/RV hint is unvalidated.
- Added `scripts/aggression_v2_vol_skew_shock_paths_research.py` and `docs/aggression_v2_vol_skew_shock_paths_experiments.md`. The read-only study tests causal ATM/put/call/RR50/skew-index shocks against TY direction, absolute move, delivered range/RV and trend/chop from 1m to strict calendar 24h, entering t0+1m and using cross-date matched quiet-option controls. No holdout/FDR cell passes: only 6 price-quiet holdout shocks across 3 dates. Pooled diagnostics show a weak +0.4–0.7 tick range lift at 5–60m, but no stable RV, direction, trend/range, 8h or 24h effect. No dashboard/live changes.

## 2026-07-10

- Added a display-only magnitude-confidence card to the isolated V2.1 package only (`scripts/aggression_dashboard_v21/magnitude_context.py` + `/api/live_signal` + UI). It uses the Phase-1 selection thresholds over the validated 15-row pre-window (vol p70 `0.012949`, range p70 `3 ticks`) and displays ELEVATED/NORMAL with holdout move/resolution rates. It is fixed `PROVISIONAL / NON-DIRECTIONAL` and does not change residual/fade or event-flow logic. 15 targeted V2.1 tests pass. The currently running external V2.1 instance on 5054 served an older template and was not killed/restarted; manual restart is required to load the new card. V1/V2/V3 untouched.
- Added `scripts/aggression_v2_nonflow_context_research.py` and `docs/aggression_v2_nonflow_context_experiments.md` for the Phase 1 read-only non-flow value check. Clear verdict: **YES for magnitude/resolution context, NO for validated directional PnL**. High prior vol/range raises holdout >=5-tick move rate from 13.8% to 26.2% (+12.4pp, FDR pass) and resolution from 39.9% to 52.5% (+12.6pp, FDR pass), but 30m after-cost improvement CI crosses zero. Skew-agrees-fade 15m is provisional (n=23); gamma/event/depth had no conditioned overlap. Phase 2 dashboard not built; current processes untouched.
- Added `scripts/aggression_v2_skew_leg_events_research.py` and `docs/aggression_v2_skew_leg_events_experiments.md`. The read-only study reconstructs matched call/put IV legs exactly (`RR50` reconstruction corr ≈1), detects causal z>=1.5 single-leg shocks, and scores de-clustered TY outcomes. No arm passes holdout/FDR due only four skew-active holdout days. Provisional downside patterns: put-IV bid (holdout +1.47 ticks/80% at 30m, n=15) and call-IV soften (holdout +1.65 ticks/71% at 30m, n=17); call-IV bid and put-IV soften move opposite the expected rally direction. Gamma DB was mode=ro; no live/dashboard changes.
- Added `scripts/aggression_v2_skew_price_corr_research.py` and `docs/aggression_v2_skew_price_corr_experiments.md` for a read-only TY skew-change vs TY price study using existing V2/V3 1-minute joined artifacts. Across rr25/rr50/rr75/skew_index, contemporaneous 1/5/15/30m correlations, and a -30..+30m lead/lag grid, no Pearson or Spearman cell survives pooled BH-FDR. The prior RR50 30m near-zero result reproduces at Pearson `0.005` (95% date-clustered CI `[-0.035,+0.038]`). No dashboard/signal change; gamma DB and live stores untouched.

## 2026-07-09

- Added corrected executable event-window research (`scripts/aggression_v2_event_window_lock_research.py`) and isolated V2.1 paper dashboard (`scripts/aggression_dashboard_v21/`) on manual-only port 5054. Corrected T+5 lock-price holdout results remove the original release-price look-ahead: 5m -0.23, 15m +0.19, 30m +0.03 ticks after cost, none statistically significant. V2.1 therefore stays `PROVISIONAL / RESEARCH-ONLY`, tracks LONG/SHORT/NO_SIGNAL at T+5 and +5/+15/+30m post-lock paper outcomes, and writes only under `workspace/analysis/aggression_v21_live/`. Added 9 targeted tests; no SOD/Tailscale/Telegram/automation and no V1/V2/V3 edits.
- Added `scripts/aggression_gate_lib.py` and `scripts/aggression_v2_gate_reeval_research.py` for an additive statistical re-evaluation of aggression gates. The harness tested 47 headline cells using date-clustered 10,000-draw bootstrap CIs, one-sided date-cluster tests, BH-FDR (`q=0.10`), cost sweeps, adverse stats, and context stability. Result: **0 PASS, 11 PROVISIONAL, 36 FAIL**. Event-window flow-follow 5m/15m survives FDR with CIs above zero but remains PROVISIONAL because an event-only strategy cannot satisfy RTH × no-event stability; magnitude remains diagnostic-only. Report: `docs/aggression_v2_gate_reeval_experiments.md`. No dashboard; existing verdict files and V1/V2/V3 untouched.
- Added EV-selective aggression research track (all research-only, no dashboard): `scripts/aggression_v2_ev_selectivity_research.py`, `scripts/aggression_v2_meta_label_research.py`, and `scripts/aggression_v2_event_window_research.py`, with reports under `docs/aggression_v2_ev_selectivity_experiments.md`, `docs/aggression_v2_meta_label_experiments.md`, and `docs/aggression_v2_event_window_experiments.md`. EV selectivity gate FAIL (top-EV slices still negative after 0.5-tick cost). Meta-label gate FAIL (AUC 0.54; top-30% improves to -0.13 ticks but still negative; no EV overlap with live alerts). Event-window gate FAIL strictly after de-duplicating release timestamps: post-release first-5m flow-follow is strong (+1.30/+2.15/+1.89 ticks at 5/15/30m) but holdout `n=49<50`. Treat event-window as a re-gate lead only. V1/V2/V3 untouched.
- Added `scripts/aggression_v2_forward_return_research.py` and `docs/aggression_v2_forward_return_experiments.md` for the research-only generic regime-conditional forward-return signal. The script builds 1/3/5/15/30m within-day forward-return targets from the TBBO feature matrix, adds explicit flow dynamics plus read-only V3 flow-effort regimes, fits walk-forward Ridge/logistic models, and tests FADE/FOLLOW/STAND_ASIDE regime translations with adverse-aware stats. Gate FAIL: 1m/3m/5m direction AUC is 0.659/0.618/0.601, but expected moves are below the 0.5-tick cost and regime trade translations remain negative after costs. No dashboard; V1/V2/V3 untouched.
- Ran Tier-1 tradability brainstorm (research-only): resolution diagnostics, V2×V3 confluence, magnitude classifier, stand-aside filter. Scripts: `aggression_v2_resolution_diagnostics_research.py`, `aggression_v2xv3_confluence_research.py`, `aggression_v2_magnitude_research.py`, `aggression_v2_standaside_research.py`. Report: `docs/aggression_v2_tradability_tier1_experiments.md`. **All four gates FAIL** (resolution/magnitude AUC promising but top-decile n=16; confluence underpowered with 4 days of V3 regimes; stand-aside AUC 0.46). V1/V2/V3 untouched; ports 5050/5052/5053 still up; Tier 2/3 remain queued. No V2.1.
- Added `scripts/aggression_v2_convergence_research.py` (read-only vs walkforward + V2 alert/signal logs + economic_events blackout) and `docs/aggression_v2_convergence_experiments.md`. Continuation after 0.5-tick costs fails. Formation cell PRICE_LED fade `|z|>=2` 30m holdout episodes is +0.68 ticks after cost (n=73) and letter-passes the gate, but RTH×no-event and live-signal slices fail confirmation. V1/V2/V3 code/stores/ports untouched; V2.1 and meta-labeling deferred pending explicit approval. Artifacts under `workspace/analysis/aggression_v2_convergence/`.

## 2026-07-08

- Added `scripts/aggression_trend_range_research.py` (read-only vs TBBO feature matrix) and `docs/aggression_v21_trend_range_experiments.md` for trend-vs-range + trader block/large-trade tests (`large_trade_share` / `avg_trade_size`). Chronological holdout failed the V2.1 gate (structure persistence lift −7.2pp; TREND had higher large-print intensity than RANGE). Decision: do not build `scripts/aggression_dashboard_v21/`; V2 on `5052` untouched. Artifacts under `workspace/analysis/aggression_trend_range/`.
- Disabled stale Bloomberg workbook Telegram notifications in `data_setup/create_bbg_data_table.py`. The loader still prints a local warning when `bbg_event_fields.xlsx` is more than 2 days old, but no longer resolves `BBG_ALERT_CHAT_ID`, sends Telegram, or writes `bbg_excel_stale_state.json` dedupe state.
- Updated `docs/bbg_alerts_channel_setup.md`, `.cursorrules`, `memory-bank/code-index.md`, and `memory-bank/io-schema.md` so the documented BBG Telegram alert path now points to `scripts/check_economic_events_db_staleness.py` only.

## 2026-07-06

- Extended `scripts/aggression_dashboard_v3/` as the isolated V2-copy-plus-drivers dashboard with a new display-only flow-effort regime layer. New `flow_effort.py` computes 5-minute effort per tick, absorption, flow deceleration, cross-tenor agreement/disagreement, a frozen-range state machine, false-break detection, event-blackout downgrades, dwell-time hysteresis, tiered exhaustion/breakout labels, and conviction parts.
- Wired V3 `/api/live_signal` to include a `flow_effort` block while preserving the existing V2-style residual-z payload and `drivers_now` output. The V3 UI now renders a separate flow-effort banner, flag chips, effort/range cards, and a TotalAggress-vs-price chart with active/frozen range bands so the new research labels are not conflated with V2 WATCH/DISLOCATION.
- Added daily V3 regime audit persistence to `workspace/analysis/aggression_v3_live/regime_log_YYYYMMDD.csv`, separate from V2 and from V3 `signal_log_YYYYMMDD.csv`.
- Added `scripts/aggression_effort_range_research.py`, an offline chronological-holdout script that reuses the live `annotate_flow_effort()` code path, sweeps range/effort parameters, and writes rule stats/summary artifacts under `workspace/analysis/aggression_effort_range/`.
- Added `tests/test_flow_effort.py` with synthetic tests for exhaustion, trend-follow suppression, breakout, false-break, warmup, and frozen-range break-bar behavior. Verified `python -m pytest tests/test_flow_effort.py -q` passes (`6 passed`).
- Added `scripts/aggression_v3_options_context_research.py` to test gamma-tracker put/call skew and near-spot gamma as potential context for aggression V3/V4. The script opens `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db` read-only, builds front-weighted RR25/RR50/RR75 and near-spot `dv01_gamma` metrics, joins to V3 signal/regime logs and TBBO history, and writes artifacts to `workspace/analysis/aggression_v3_options_context/`.
- Phase 1 options-context result did **not** pass the V4 build gate: best RR-conditioned residual-fade tests were only ~44% hit, direct RR lead/lag was ~37-40%, gamma-pin tests were weak/zero on current overlap, and there were no flow-effort breakout samples to validate skew-confirmation. Decision: do not build `scripts/aggression_dashboard_v4/` yet; revisit after more V3 regime history accumulates.
- Added `scripts/aggression_v2_options_context_research.py` and `docs/aggression_v2_options_experiments.md` to run the cleaner V2 x gamma-tracker experiment against real V2 episode outcomes. Result: skew agreeing with the V2 fade improved pooled 30m episode hit rate from 52.4% to 62.1% (`n=58`) and minute-level `|z|>=2` fade quality by ~6.6pp, but the chronological holdout was only `n=35` with +5.3pp lift, so the V2.5 build gate did **not** pass. Track D rejected the original continuation idea (`put skew bid + fair value lower`); skew+flow agreement underperformed fair-value direction alone while skew disagreement did better. Decision: do not build `scripts/aggression_dashboard_v25/` yet.

## 2026-06-29

- Added `scripts/aggression_monthend_flow.py`, a light flow-only month-end stats script over `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv`. It tags trailing business days of complete months as month-end, reports Normal vs Month-end daily net DV01-weighted signed flow, close-window flow, and share-into-close, writes `workspace/analysis/aggression_v2_tbbo/monthend/monthend_flow_daily.csv`, and optionally emits an inlined-Plotly timing chart via `--html`. It is standalone and does not touch the live V2/V3 aggression monitors.

- Added per-event projected move distributions to Macro Path Navigator. New `scripts/macro_path_navigator/distributions.py` resolves upcoming schedule names to canonical `bbg_data` event names, loads historical instances, computes TY reaction windows from PostgreSQL `ohlcv_1m`, caches results under `workspace/state/macro_path_navigator/dist_cache/`, builds baseline/hot/inline/cool distributions with N/N_eff and percentile stats, and projects p5/p25/p50/p75/p95 moves into price space using the current live TY price.
- Added `/api/distribution` and `/api/distributions`. The runway chart now draws projected percentile bands/boxes at upcoming event times, and the UI includes an `Event Distribution` panel with event/window/scenario selectors, histogram, stats pills, surprise-status/N_eff badges, and the explicit caveat that projection is centered on current price and ignores pre-event drift.
- Optimized distribution history loading from slow per-instance OHLCV queries to a single interval-batched SQL query per event/window using explicit UTC `timestamptz` casts. Fixed stale empty distribution caches from an interrupted first run by making empty cache files rebuild. Validation: NFP baseline `N=50`, hot `N_eff=26`; JOLTS baseline `N=53`; Initial Claims cool `N=206/N_eff=84`; compact `/api/distributions` returned six upcoming projected event bands with projected prices.
- Made Macro Path Navigator Flask startup non-blocking for live feeds: `LiveDataManager` starts price/order-flow feeds in a background thread on first request instead of blocking port binding. This avoids startup hangs if Databento reference lookup or AccessAPI seeding is slow.

- Added a first-pass Macro Path Navigator signal/watch layer. New `scripts/macro_path_navigator/signals.py` converts recent high-signal Iran/geopolitics headlines plus current AccessAPI TBBO buy/sell-aggressive flow context into conservative headline risk/de-escalation watches, and adds upcoming event-watch signals from the current cycle. Each signal includes confidence, reasons, timestamp/source, and a caveat that it is a watch signal until price/flow confirms.
- Added `/api/signals` and included signals in the SSE stream. The UI now renders a `Signals & Watches` panel above the charts with flow context and live signal cards. Validation showed `/api/signals` returning 5 current watches, including diplomacy/de-escalation and geopolitical-risk watches from recent Iran headlines with flow context.

- Added legible event/headline annotation rails to Macro Path Navigator candlestick charts. New `scripts/macro_path_navigator/headlines.py` reads `workspace/iran_headlines.db`, keeps high-signal source classes, drops `discussion`/`other`, and supports bucketed clustering for dense windows. New endpoints `/api/headlines` and `/api/events_range` feed the chart rails.
- Updated `scripts/macro_path_navigator/static/dashboard.js` and `templates/index.html` with annotation controls (events/headlines/theme toggles), dedicated Events/Headlines rail lanes below both live and historical candlestick charts, sparse/capped event labels, headline cluster markers, hover detail, and click-to-pin details without browser alerts. Live chart gates headline markers when the Iran headline feed is stale/down; historical chart defaults headline markers off for windows larger than 5D unless the user explicitly toggles them.
- Validated annotation endpoints with Flask test client: 1D-style headline window returned 67 high-signal rows, 5D clustered window returned 135 rows / 41 clusters, 1M clustered window returned 467 rows / 72 clusters, and `/api/events_range` returned 10 events for a sample 2-day range. Python compile passed for annotation modules.

- Upgraded Macro Path Navigator price charts. The default/dashboard wrapper port moved from unsafe Chromium SIP ports `5060/5061` to browser-safe `5088`; monitor defaults and wrappers now point to `5088`.
- Added `scripts/macro_path_navigator/price_history.py` and `/api/price_history`, returning historical TY candlesticks from PostgreSQL `ohlcv_1m` with window dropdown support (`1D`, `5D`, `1M`, `3M`, `6M`, `1Y`, `All`) and `coverage_end` anchored to the current database max (`2026-06-03T15:19:00+00:00` at validation time).
- Reworked `scripts/macro_path_navigator/live.py` so the runway payload includes `live_candles`: full current-session (~24h) 1-minute OHLC resampled from live AccessAPI TBBO trades, with a line fallback if too sparse; raw flow tail remains available for diagnostics.
- Updated the UI (`templates/index.html`, `static/dashboard.css`, `static/dashboard.js`) to render the live session as candlesticks with event markers and a separate TradingView-style historical TY candlestick panel with rangeslider, crosshair/spikes, 32nds y-axis labels, and a visible "data through" coverage note.
- Validated with `py_compile` and Flask test-client smoke across `/api/price_history` windows: `1D` 994 candles, `5D` 779, `1M` 905, `3M` 634, `6M` 118, `1Y` 275, `All` 833, all returning HTTP 200.

- Built the first live Macro Path Navigator MVP under `scripts/macro_path_navigator/`. The Flask app runs on browser-safe port `5088`, exposes `/api/cycle`, `/api/runway`, `/api/adhoc_events`, `/api/speaker_lean`, `/api/stream`, and health endpoints, and renders a Plotly "runway" UI with live TY/TBBO path, upcoming event markers, feed health strip, ad-hoc event form, factor sliders, and headline feed.
- Added the inter-FOMC cycle/event engine (`events.py`) reading `Fed Press Conference` anchors and `bbg_data` rows from `workspace/economic_events.db`; events are theme-tagged and surprise-tagged (`full-z`, `proxy-z`, `partial`, `none`) with alias-based proxy fallback. Added `config/fomc_speaker_lean.json` as a conservative FOMC-only placeholder config with explicit manual-review confidence flags.
- Added live/health adapters (`live.py`, `health.py`) wrapping `DatabentoLiveState`, AccessAPI `LiveTbboBuffer`, and the real-time Iran headline stream heartbeat/DB. Runtime markers write under `%LOCALAPPDATA%\market_data_puller\status`.
- Added external health monitoring via `scripts/check_macro_path_health.py` plus `scripts/run_macro_path_navigator.bat` and `scripts/run_macro_path_health_monitor.bat`. The monitor pings `/api/health`, falls back to marker files when HTTP is down, and sends deduped Telegram stale/down/recovery alerts (dry-run tested).
- Validated Macro Path Navigator with `py_compile`, Flask test-client endpoint smoke tests, live server smoke on `http://127.0.0.1:5088` (`/api/health`, `/api/cycle`, `/api/runway`), and dry-run monitor checks. The server was left running for review on port `5088`.

- Added a real-time Telegram listener for Iran/Israel/Lebanon conflict headlines. `scripts/scrape_iran_headlines.py` now exposes `process_telegram_message()` so the batch scraper and stream listener share the exact same clean/filter/classify/insert path, and `insert_headline()` now reports true inserts via `rowcount > 0` so duplicate `INSERT OR IGNORE` rows are not overcounted.
- Added `scripts/iran_headlines_stream.py`, a persistent Telethon `events.NewMessage` listener for chat `-4611662021`. It runs startup catch-up from the DB watermark overlap, writes matching headlines to `workspace/iran_headlines.db` within seconds, supervises reconnects, enforces a single-instance lock, logs to local + workspace files, and writes heartbeat / last-message / success-error markers under `%LOCALAPPDATA%\market_data_puller\status`.
- Added `scripts/run_iran_headlines_stream.bat` and replaced `scripts/install_iran_headlines_task.ps1` so running the installer creates the long-running `IranHeadlinesStream` scheduled task at user logon with restart-on-failure and removes the old 3-hour `IranHeadlinesRefresh` batch task. The old batch wrapper remains available for manual backfill but is no longer the scheduled path.
- Verified `py_compile` for `scripts/scrape_iran_headlines.py` and `scripts/iran_headlines_stream.py`, PowerShell parse for `scripts/install_iran_headlines_task.ps1`, listener `--help`, an isolated SQLite dedup test showing first insert succeeds and duplicate is skipped, and a nested-lock smoke test confirming duplicate stream instances are rejected without cleanup errors.


## 2026-06-17

- Repointed Tailscale Serve `https://aasthajain.tail174727.ts.net:8444` from local V2 (`127.0.0.1:5052`) to V2.2 (`127.0.0.1:5058`). SOD `run_all_sod.bat` now starts V2.2 via `run_aggression_monitor_v22.bat`. Live V2 package unchanged; local 5052 no longer on `:8444`.

- Reset V3 to a fresh V2 copy plus live fair-value driver decomposition. Fully decommissioned the prior depth V3 by stopping the 5053 MBP-10 process, deleting `scripts/aggression_dashboard_v3/`, and deleting `workspace/analysis/aggression_v3_live/`; separate depth research artifacts remain. New V3 now runs on 5053 with store `workspace/analysis/aggression_v3_live/`, adds `drivers_now` level/change contribution arrays to `/api/live_signal`, and renders a Fair-Value Drivers panel showing current contributors and one-bucket movers in 1/64 ticks.

## 2026-06-15

- Updated the V2 research/live signal dashboard to display TY/ZN prices in Treasury 32nds convention on the actual-vs-fair-value charts. `scripts/aggression_dashboard_v2/static/dashboard.js` now mirrors the canonical `decimal_to_32nds` display (`XX-NN`, `XX-NN+`), applies 32nds y-axis tick labels and hover text to research/live fair-value charts, and fills a new live price readout in the banner. `templates/index.html` and `static/dashboard.css` add/style the readout, and `app.py` bumps `ASSET_VERSION` for cache busting. Verified conversion parity vs `src/servers/analytics/schema.py`, V2 home includes the new asset/readout, and V2/V3 live endpoints remain healthy.

- Added `Y:\Aastha\SOD\run_all_sod.bat`, a skip-only master launcher for the full SOD suite: aggression dashboards v1/v2/v3 plus `telegram_bot.py`, `bbg_news_stream_bot.py`, `x_macro_stream_bot.py`, and `polymarket_probability_tracker.py`. It performs one consolidated dependency check/install, validates key presence with warnings only, checks externally managed AccessAPI/PostgreSQL reachability without blocking, explicitly starts Outlook Classic for Bloomberg COM ingestion, and uses process/port guards so already-running services are reused rather than stopped or restarted.

- Added `Y:\Aastha\SOD\run_aggression_monitor_v3.bat` for the V3 market-depth dashboard on port `5053`, including the V2 dependency set plus `msgpack` for MBP-10 decoding.

- Hardened `Y:\Aastha\SOD\run_bbg_news_bot.bat`, `run_telegram_bot.bat`, `run_tweets_bot.bat`, and `run_polymarket_tracker.bat` with duplicate-process guards and lightweight dependency checks. The Bloomberg launcher now sets `BBG_OUTLOOK_START_IF_CLOSED=true` and starts Classic Outlook via the registered `OUTLOOK.EXE` path before attaching through COM.

- Added `docs/sod_auto_start.md` with the one-click launcher contract and optional at-logon Task Scheduler command. The scheduled setup is documented only; no task was auto-registered.

## 2026-06-11

- Built Phase 2 V3 market-depth dashboard in `scripts/aggression_dashboard_v3/` on port `5053`, isolated from V2. V3 reads existing V2 research artifacts, writes live state to `workspace/analysis/aggression_v3_live/`, streams live MBP-10 via `live_buffer_mbp10.py`, computes depth regimes/`gamma_bid_score` in `depth_state.py`, exposes depth context in `/api/live_signal`, adds Gamma Bid Risk / Depth Regime / Book Pressure / Fragility cards plus a depth chart, tags alert episodes with depth context, and writes daily `depth_log_YYYYMMDD.csv` for future retraining. Verified V3 and V2 endpoints healthy.

- Extended the MBP-10 depth research with Phase 1.5 diagnostics in `scripts/aggression_v2_depth_research.py`: composite gamma-bid risk score, liquidity replenishment after thin depth, imbalance persistence, sweep/absorption, OFI-vs-trade divergence, and top-vs-deep concentration. Outputs now include `workspace/analysis/aggression_v2_depth/phase15/*.csv`, and `docs/aggression_v2_depth_research.md` includes interpretation. Key findings: composite score is the strongest diagnostic (score 0 -> 5: 30m RV `0.0081 -> 0.0159`, abs 30m move `0.0249 -> 0.0869`), refill and imbalance persistence are weak, sweeps tend to absorb more than continue, OFI/trade agreement is better than divergence, and high top-of-book concentration coincides with larger future moves. Recommendation remains: build V3 depth as display/risk context first; do not add depth to the fair-value model yet.

## 2026-06-10

- Ran Phase 1 MBP-10 depth research for the planned V3 aggression dashboard. Added `scripts/aggression_v2_depth_research.py`, which builds a 1-minute ZN depth matrix from `D:\FRDBs\FutMBP\ZN_outrights_mbp10`, reuses prebuilt `research_features` where available, and writes artifacts to `workspace/analysis/aggression_v2_depth/`. Key finding: raw total depth does not support the gamma-bid thesis (bottom-vs-top 30m RV ratio `0.87x`), time-adjusted depth is weakly supportive (`depth_z` ratio `1.12x`), thin-depth dislocations had worse directional hit/adverse stats on only 7 episodes, and adding depth/OFI worsened common-sample Ridge OOS fit (`RMSE 0.1600 -> 0.1676`). Recommendation: V3 live depth panel is safe as display/risk context only; do not add depth to the fair-value model yet.

- Added a Windows named mutex single-instance guard to `scripts/bbg_news_stream_bot.py` so accidental duplicate launches exit with code `2` instead of double-sending Bloomberg alerts. Verified by starting a second collector while the event-mode instance was running.

- Simplified Bloomberg news Telegram rendering to headline-only output: `_format_news_message()` now sends only the escaped email subject plus optional received timestamp, while the full email body remains stored in `news_labels.body` for audit/analytics.

- Upgraded the V2 aggression live signal to use a prior-blended causal residual z-score: daily Ridge fitting now also estimates historical residual mean/std from training days, and live z blends that prior with today's expanding residual stats (`z_method=prior_blend_v1`). Added minute-level live signal audit logs (`signal_log_YYYYMMDD.csv`), Hold Expectations metrics (`median_minutes_to_close`, `median_mae_to_resolve_ticks`, price-share of reversion), CSV header migration for older `alert_log.csv` rows, a Hold Expectations UI card, outcomes-table close/MAE/share columns, and an in-dashboard glossary explaining resolved/expired vs 30m trade stats. Added and registered `Y:\Aastha\SOD\run_aggression_monitor_v2_sched.bat` as daily Task Scheduler task `AggressionMonitorV2` at 18:00.

- Upgraded Bloomberg news Outlook ingestion from interval-only polling to event-driven push. `src/core/outlook_mail_client.py:watch_inbox()` attaches to the primary Inbox `Items.ItemAdd` COM event and only enqueues normalized message dicts; `scripts/bbg_news_stream_bot.py --mode event` owns SQLite/Telegram work on the asyncio loop, runs a startup catch-up, and supports optional `--safety-poll-seconds` as a backup.

- Set `BBG_NEWS_MODE=event` and updated `Y:\Aastha\SOD\run_bbg_news_bot.bat` to launch `scripts\bbg_news_stream_bot.py --mode event --lookback-minutes 1` for near-instant Bloomberg mail delivery while avoiding backlog spam.

- Added `Y:\Aastha\SOD\run_bbg_news_bot.bat`, a double-click launcher for the Bloomberg news Outlook collector. It mirrors the other SOD startup scripts, uses the repo venv when present, falls back to `py -3`, starts `scripts\bbg_news_stream_bot.py --mode event --lookback-minutes 1`, and reminds the user to keep classic Outlook Desktop open.

- Added local Outlook Desktop COM ingestion for Bloomberg news as a no-Graph fallback. `src/core/outlook_mail_client.py` reads the primary Outlook Inbox via `win32com.client`, filters sender/domain matches (currently `nlrt@bloomberg.net`), and emits normalized message rows using `outlook:<EntryID>` as the dedupe key. `scripts/bbg_news_stream_bot.py` now selects the backend via `BBG_MAIL_SOURCE`, with current config set to `outlook_com`.

- Added Bloomberg email news ingestion into the same Telegram approve/reject/score workflow used by X macro tweets. `src/core/graph_mail_client.py` supports app-only Microsoft Graph client-credentials auth and delegated device-code auth with a local refresh-token cache; current config uses delegated auth to read the signed-in user's mailbox without tenant-wide app-only consent.

- Added `src/core/bbg_news_store.py` with local-user-profile SQLite default `~/.market_data/bbg_news.db`; `news_labels.id` is the short Telegram callback key and `graph_id` is the unique Microsoft Graph dedupe key. Label updates remain atomic (`label IS NULL`) and approved-but-unforwarded rows can be retried.

- Added `scripts/bbg_news_stream_bot.py` as a sender-only collector with `--once`, `--dry-run`, `--since`, `--lookback-minutes`, `--top`, and `--poll-seconds`, plus `bbgnews_score:<id>:<score>`, `bbgnews_approve:<id>`, `bbgnews_reject:<id>`, and `/bbg_retry_forwards [limit]`. Approval forwarding reuses `X_MACRO_PUBLIC_CHAT_ID` and commits labels only after successful public-chat sends.

- Wired Bloomberg news callbacks into `scripts/telegram_bot.py` while keeping polling centralized in the main bot. Updated Memory Bank `code-index.md` and `io-schema.md` for new files, env vars, callback contracts, and CLI inputs.



## 2026-06-03

- Updated V2 paper-trading stats to split WATCH vs DISLOCATION outcomes separately. `/api/live_signal` and `/api/alert_log` now include `watch_stats` and `dislocation_stats` with issued/resolved/expired/finalized counts, resolution rate, 30m directional hit rate, average 30m directional PnL, and average adverse move; the V2 UI cards now show separate WATCH and DISLOCATION resolution/hit rates.

- Extended V2 live alerts into a paper-trading validator. `alerts.py` now records open price/fair value/residual/z, closes dislocations when `|z|<1.0`, scores directional and adverse PnL at 15m/30m, appends finalized rows to `workspace/analysis/aggression_v2_live/alert_log.csv`, and exposes `/api/alert_log` with today/all scopes. Added background server-side evaluation so alerts are logged without an open browser tab, added the UI outcomes table/live-vs-backtest stats, seeded the live buffer from `/live/tbbo/recent`, and verified synthetic alert finalization plus V1/V2 HTTP health.

- Extended `scripts/aggression_dashboard_v2/` with a live V2 dislocation alert mode. Added live TBBO buffering with bid/ask size, live feature construction, daily Ridge model state, causal residual-z scoring, and `WATCH`/`DISLOCATION` alert episodes that resolve when `|z|<1.0` within 30 minutes. The UI now shows a live banner, live fair-value/residual-z charts, issued/resolved/expired stats, and a backtest benchmark; V2 runs on `5052` and V1 remains untouched on `5050`. Added `Y:\Aastha\SOD\run_aggression_monitor_v2.bat`.

- Added `scripts/aggression_dashboard_v2/`, a research-only Flask dashboard on port `5052` for reviewing TBBO-backed V2 artifacts. It reads existing research outputs only (no live fitting), renders model comparison, Ridge fair-value/residual charts, feature drivers, holdout and conditional signal tables, and the V2 explainer. Verified V2 responds on `http://127.0.0.1:5052` while V1 remains up on `5050`.

- Expanded the TBBO-backed research sample to the maximum AccessAPI DB window found (`2026-01-27` through `2026-06-05`, 92 dates with all four symbols present at the probe time) and reran the V2 model/conditional/holdout tests. The larger clean sample had 54,835 rows across 74 clean dates; OLS baseline had the best descriptive OOS fit (`R²≈0.295`, `RMSE≈0.134`), while the Ridge extreme-residual signal survived chronological holdout strongly: `abs(residual_z)>=2` at 30 minutes produced `n=492`, hit rate ~81.1%, average directional move ~0.0647 price points, median ~0.046875. Updated `docs/aggression_v2_signal_research_summary.md`; recommendation is to proceed to a research/explainability dashboard, not live automation yet.

- Added and ran `scripts/aggression_v2_conditional_signal.py`, a conditional signal test for TBBO-backed Ridge residuals. The unconditional residual had little edge, but large residual regimes were materially better: `|residual_z|>=2` over 30 minutes showed `n=216`, residual/future-change correlation `-0.37`, mean-reversion hit rate `71%`, and average directional move `0.032` price points before costs. This suggests V2 may be useful only as a gated rich/cheap signal, not as a continuous predictor.

- Added and ran `scripts/aggression_v2_model_research.py`, a TBBO-backed walk-forward model comparison for OLS baseline, Ridge, Lasso, and Random Forest benchmark. On the current clean-day sample, Ridge had the best OOS fair-value fit (`R²≈0.42`, `RMSE≈0.143`) vs OLS baseline (`R²≈0.32`, `RMSE≈0.154`), but residual mean-reversion evidence was effectively absent, so the research recommendation is not to deploy a trading signal/dashboard yet without further target/signal work and more clean days.

- Added and ran `scripts/aggression_v2_tbbo_audit.py`, a TBBO-backed Phase 0.5 research audit pulling `source=database&schema=tbbo&format=arrow` from AccessAPI for `TY/FV/TU/US`. The audit confirmed quote-at-trade fields are available for sampled May/June dates and produced artifacts under `workspace/analysis/aggression_v2_tbbo/`; key findings were usable spread/depth/microprice features, highly correlated size-imbalance vs microprice features, missing/invalid Arrow output on 2026-05-31, and short/partial sessions on 2026-05-27/05-29 that should be excluded before modeling.

- Added `scripts/aggression_v2_feature_audit.py` for the V2 research-first Phase 0 audit. It builds a cached-trades-only 1-minute feature matrix for `FV/TU/TY/US`, writes artifacts to `workspace/analysis/aggression_v2/`, and surfaced key caveats before modeling: sparse cached dates, roll transitions around May 27, partial current/June sessions, missing raw buy/sell ratio in zero-flow minutes, and the need to gate model work on reviewed data quality.

- Added a display-only chart lookback selector to the live curve aggression dashboard (`2h` default, plus `6h`, `12h`, `1 day`). `/api/aggression` now accepts `lookback_hours` and slices flow/price/fair-value panel rows before payload construction, while keeping full-session aggression/fair-value fits unchanged.

- Added `Y:\Aastha\SOD\run_aggression_monitor.bat`, a double-click launcher for the live curve aggression monitor. It uses the repo venv when present, auto-checks/installs `flask`, `pandas`, `numpy`, `websockets`, and `databento`, avoids duplicate starts if port `5050` is already listening, opens `http://127.0.0.1:5050`, and runs `scripts.aggression_dashboard.app`.

- Added `scripts/news_fade_dashboard.py`, a standalone X macro/news headline reaction dashboard. It reads `tweet_labels` from `~/.market_data/x_macro_stream.db`, optionally filters accounts/labels, same-account dedupes repeated squawks, bulk-loads TY 1-minute OHLCV from PostgreSQL, measures initial peak reaction with a configurable delayed-reaction window, filters reaction events by `--move-threshold-bps`, and computes fade/retracement plus persistence at `--fade-mins` horizons.

- Generated and validated `workspace/scenarios/news_fade_dashboard_latest.html` for the current overlapped data window (`2026-02-09` through `2026-05-08`, matching current TY OHLCV coverage): 11,812 messages after dedupe, 3,421 with enough TY bars, 23 default-threshold reaction events, median initial peak 4.12 bps, and median 30m fade 27.6%. Also smoke-generated `workspace/scenarios/news_fade_dashboard_smoke.html`.

- Updated `memory-bank/code-index.md` and `memory-bank/io-schema.md` for the new dashboard script, CLI arguments, default X macro DB path, and HTML output.

- Extended `scripts/news_fade_dashboard.py` for the Iran/Israel/Lebanon conflict workflow: default topic filter, broad-vs-war-only UI toggle, fade severity buckets (`>50%`, `>80%`, full `>=95%`), `--move-threshold-bps 0` all-events mode, and `--max-paths` to cap embedded path arrays while preserving scalar metrics for all events.

- Backfilled continuous TY 1-minute OHLCV from Databento into PostgreSQL for missing May/June 2026 windows using `scripts/backfill_ty_from_databento.py`; Databento dry-run for `2026-06-02` returned 1,259 valid ZNU6 bars, and the idempotent insert advanced TY coverage to `2026-06-03 11:19 ET` (partial current-day availability).

- Regenerated `workspace/scenarios/news_fade_dashboard_iran_israel_latest.html` with `--topic iran_israel_lebanon --move-threshold-bps 0`: 4,937 broad conflict headlines after dedupe, 1,340 with enough TY bars, broad median initial peak 0.25 bps; broad fade rates were 30m `>50%=49.7%`, `>80%=45.7%`, full `45.4%`, and 60m `>50%=49.8%`, `>80%=45.7%`, full `45.6%`. War-only subset: 484 measured events, median initial peak 0.25 bps; 30m `>50%=50.6%`, `>80%=45.4%`, full `45.2%`, and 60m `>50%=48.1%`, `>80%=44.0%`, full `43.8%`.

- Added a Pre-Event Impulse tab to the unified dashboard. Backend additions in `scripts/live_unified_dashboard.py`: `parse_ecos_estimates()`, `/api/impulse/ecos` for Bloomberg ECOS upload/default parsing (`Z:\Aastha\ECOS_Estimates1.xlsx`), and `/api/impulse/history` for T-4..T-1 pre-event TY drift relationships using the 2023+ regime L1 store. Frontend addition: `scripts/dashboard_tabs/impulse_tab.py`, wired into the dashboard tab list. Verified default and uploaded ECOS parsing (prior 115, median 85, expected change -30k, 68 estimates) and T-2/T-4 history loads (35 NFP regime records).

- Extended the Pre-Event Impulse tab with a What-if Actuals table. Added `build_surprise_bucket_stats()` and `/api/impulse/scenarios` in `scripts/live_unified_dashboard.py` to map hypothetical actuals to z-score, empirical surprise percentile, and bucket mean signed/max TY moves for pre-event, +2h, and EOD windows. The tab now has editable scenario rows (default bofa/rbc/nomura/marex/pantheon) and auto-computes after ECOS load. Verified with consensus 85, std 20.51: pantheon 50 maps to low bucket (pre +4.73 bps exp / 7.10 max), 95/99/110 map to the 40-60 bucket (pre -1.21 bps exp / 4.88 max).



## 2026-06-02

- Reworked the live curve aggression dashboard fair-value model into two stable references: Panel 4 is an intraday OLS fit cached once per ET session, and Panel 5 is a 10-business-day daily-reset pooled OLS with source data refreshed once per ET day. The 1-second browser poll still updates live flow/price, but fair-value betas now come from caches instead of refitting every request.

- Restricted the unified dashboard event universe to the current regime: set `default_start_year` to 2023 in `config/unified_dashboard_event_config.json` and added `filter_events_to_regime()` in `scripts/live_unified_dashboard.py`, called at the top of `build_dashboard_data` (toggle `apply_regime_filter=True`). It drops pre-2023 events (incl. the 2020 COVID cons_std spike up to 2928 and elevated 2021-2022) and trims cons_std to the config P5-P95 band, then stats/embed/cache/`/api/events` all use the clean set. NFP now resolves to 36 events (2023-2026, cons_std 22-41); CPI 31, PPI 36, JOLTS 41. This fixes the blown-out boxplot buckets.

- Auto-rendered the pre-event heavy panels: `updatePreeventDashboard` now samples the user's bucket via `selectHeavyEvents`, loads details, and renders both price paths and the distribution explorer (`updatePreeventDistroWindow`) without requiring the "Render price paths (heavy)" click.

- Implemented the unified live event dashboard migration: patched NFP loading to keep headline releases when UER std is missing, added Databento-backed `scripts/backfill_ty_from_databento.py`, backfilled missing TY bars into PostgreSQL `ohlcv_1m`, and rebuilt recent L1 features for `NFP/CPI/PPI/JOLTS`.

- Added `scripts/refresh_dashboard_data.py`, `scripts/run_dashboard_refresh.bat`, and `scripts/install_dashboard_refresh_task.ps1`; installed `UnifiedDashboardRefreshNightly` and `UnifiedDashboardRefreshPostRelease` scheduled tasks for automatic data refresh.

- Updated L1 building to merge recent `bbg_data` releases, apply configured release times when `bbg_data.timestamp` is midnight, and include `consensus_std_dev` in metadata. Updated `/api/event_details` to preserve std values from L1/cache/`bbg_data`.

- Verified the local server at `http://localhost:8800/`: event selector exposes `NFP/CPI/PPI/JOLTS`, Pre/Post tabs render, latest NFP details through `2026-05-08` return 8:30 ET slices with price paths and std values, and `/api/live` reports Databento.

- Added `aggression_dashboard_demo/`, a standalone offline copy of the curve aggression dashboard for external/demo sharing. It bundles selected May 2026 `FV/TU/TY/US` parquet trade data, computes front-month-filtered DV01-adjusted aggression from local files, keeps all five panels including the causal rolling-OLS HF model, and removes live-feed/database/credentialed data dependencies.

- Added offline demo packaging: Flask app on port `5051`, cleaned offline-only frontend, `requirements.txt`, `README.md`, and `export_static.py`. Generated the shareable static output at `aggression_dashboard_demo/output/demo_dashboard.html`.

- Corrected the offline demo DV01 table to CTD-style approximations (`FV=47`, `TU=38`, `TY=65`, `US=125`) and regenerated `aggression_dashboard_demo/output/demo_dashboard.html`; the live dashboard config remains unchanged.

- Removed the visible header title/brand from both the live aggression dashboard and the offline demo, and regenerated the demo static HTML.

- Updated `memory-bank/code-index.md`, `memory-bank/io-schema.md`, and `.cursorrules` for the new demo folder and its I/O surface.



## 2026-06-01

- Added `scripts/jolts_prepost_event_dashboard.py`, a standalone JOLTS Job Openings pre/post-event analytics dashboard. It loads JOLTS events from `workspace/economic_events.db`, computes consensus-vs-prior/MA context, manual bank-estimate dispersion consolidation, historical surprise bias, surprise-driven TY sensitivity/regression stats, setup-vs-surprise driver rankings, interaction splits, and post-event all-vs-bucket return distributions at t+10m/t+30m/t+1h/t+2h.

- The dashboard generates a copy-pasteable colleague-style automated message that clearly separates the priced-in hawkish/dovish narrative from the surprise-driven release reaction, and includes quantified move stats such as bps per +1 sigma, R², typical absolute move bands, beat bias, and realized post-release predicted move once Actual is entered.

- Verified `python -m py_compile scripts/jolts_prepost_event_dashboard.py` and generated `workspace/scenarios/jolts_prepost_event_dashboard_20260601_092448.html` successfully against 2022+ JOLTS history and TY OHLCV; output summary written to `workspace/analysis/jolts_prepost_event/jolts_prepost_event_summary.json`.



## 2026-05-29

- Added `docs/jolts_labour_playbook.md`, a JOLTS-centric US labour-market analyst playbook covering JOLTS components, data mechanics, Fed-relevant derived metrics, when JOLTS matters for TY/rates, full labour-data sequencing, cross-check frameworks, worked read-throughs, and operating commands for the existing JOLTS dashboards.

- Updated `memory-bank/code-index.md` with the new playbook summary.

- Made the curve aggression dashboard genuinely live using the AccessAPI live TBBO WebSocket (`ws://100.79.30.21:8001/ws/live/stream`) for the supported front-month symbols `FV/TU/TY/US`. Reverted UXY/WN because the AccessAPI live TBBO stream does not publish them, added `LiveTradeBuffer` for in-memory live trades, merged live tail data with Databento Historical backfill in `mode=live`, added `/api/live_status`, and added Live/Historical mode, Pause, historical end-time lookback, and a LIVE/STALE/DOWN/HISTORICAL freshness banner. Validated live endpoint with single-digit-second freshness and historical lookback mode.

- Removed live dashboard latency caused by per-request Databento day re-pulls: `app.py` now refreshes a Databento historical backfill cache on a background thread and live `/api/aggression` reads that cache plus the in-memory AccessAPI live tail only. The live price line now overlays AccessAPI BBO mid prices on historical trade prices and forward-fills to the current minute; TY is now the default price symbol. Validation showed live requests returning in ~0.3-0.6s after warmup with no Databento fetches tied to each request.

- Smoothed the BuySell Ratio panel by preserving the raw per-minute imbalance as `buysell_ratio_raw` and plotting `buysell_ratio` as an EMA (`BUYSELL_EMA_SPAN=10`) in `aggregate_curve_aggression`. This reduces the panel-2 jaggedness without changing TotalAggress, buy/sell volume bars, or the price/HF model panels; validation confirmed all five panels still populate and the smoothing metadata is returned.

- Made the live Databento backfill incremental and added cache retention. `_backfill_refresher` now performs one full session fetch after startup/new session, then fetches only `covered_end - 15m -> now` every 240s and merges/dedups the chunk into `_BACKFILL`; `core.py:prune_trades_cache()` removes `trades*.parquet` older than 30 days. Startup validation logged cache pruning and a full fetch; subsequent refreshes logged incremental spans.

- Replaced the HF fair-value model's full-window in-sample OLS with a causal trailing-window OLS (`HF_BETA_WINDOW=240`, `HF_BETA_MIN_PERIODS=30`) so predicted/residual values use only data available as of each timestamp. Validation compared historical `[start,T]` vs `[start,T-30m]` requests and found identical overlapping predicted/residual values, confirming no look-ahead.



## 2026-05-28

- Added `scripts/supply_weeks_dashboard.py`, a reusable 2Y/5Y/7Y supply-weeks dashboard generator. It loads processed auction CSVs, chooses the largest same-tenor auction inside each ISO week to avoid FRN/TIPS duplicates while preserving older standard cycles, computes `is_month_end` as 7Y auction day in the last 7 calendar days of the month, pulls TY 1-minute OHLC from PostgreSQL, and writes mirrored HTML outputs.

- Regenerated `scripts/supply_weeks_dashboard.html` and `dashboards/supply_weeks_dashboard.html` through the latest auction CSV rows (May 2026). PostgreSQL OHLCV currently has no TY rows for the Mar/Apr/May 2026 supply weeks, so those latest cards are present but have empty path/candlestick payloads until market data is loaded.

- Updated the curve aggression dashboard to visually match the SSHE reference screenshots: added `UXY`/`WN` to the default universe with editable DV01 defaults (`95`, `220`), mapped them to Databento parents `TN.FUT`/`UB.FUT`, switched chart rendering to Plotly's grey-blue default theme, removed white chart cards, matched trace colors/legend placement, and swapped the P3 right-axis identity to inner Price/Yield and outer TotalAggress. Validated the `2026-04-06 01:00-10:00 ET` six-symbol session: selected `ZFM6/ZTM6/ZNM6/ZBM6/TNM6/UBM6`, generated 72,817 front-month trades, and populated all five panels.



## 2026-05-27

- Added `src/servers/auctiondata/`, a stdlib-only Treasury FiscalData `auctions_query` puller for coupon auction results. It maps official API fields back into the existing Treasury Securities CSV headers, preserves the current raw/processed CSV workflow, and uses CUSIP-based state to dedupe Telegram alerts.

- Added `scripts/auction_data_puller.py` with `--once`, `--loop`, `--backfill`, and `--dry-run` modes. Backfill seeds raw CSVs and alert state without historical Telegram spam by default; scheduled polling sends HTML Telegram summaries for newly posted results using the same Nomura-style derived fields as `auction_data/process_auction_files.py`.

- Added Windows scheduler helpers `scripts/run_auction_data_puller.bat` and `scripts/install_auction_data_puller_task.ps1`, mirroring the hardened BBG wrapper pattern with local fallback logs, status markers, UNC path normalization, and a weekday 12:55 PM to 2:30 PM five-minute cadence.

- Patched the puller after a live preview showed FiscalData can publish same-day announcement rows before result fields are populated. Existing CUSIP rows now update when result fields arrive, alerts/state marking only happen once competitive result fields are present, and incomplete state entries for the current 10Y/5Y/7Y announcement rows were removed so live results can alert normally.

- Reworked auction polling to be event-aware from `workspace/economic_events.db`: `--loop` now sleeps outside auction windows, polls every 30s from T-10m to the scheduled auction time, polls every 10s through T+30m or until the result is captured, and logs scheduler state transitions. The Task Scheduler wrapper now runs the loop daemon, and the installer starts it once each weekday at 6:00 AM with an 18-hour execution limit.



## 2026-05-20

- Added `scripts/aggression_dashboard/`, an interactive Flask + Plotly SSHE-style curve aggression dashboard for TU/FV/TY/US. It fetches Databento `GLBX.MDP3` trades, classifies CME aggressor side from `side`, sums DV01-adjusted signed flow using `config/aggression_dv01.json`, renders the five requested panels, and falls back to selected-symbol Databento trade prints when PostgreSQL OHLCV is unavailable.

- Validated the historical screenshot window (`2026-04-06 01:00-10:00 ET`) with 55,568 Databento trades, 540 one-minute flow rows, FV trade-print price fallback, all five panel payloads populated, and HF residual diagnostics generated. Python compile checks and Flask config endpoint smoke test passed.

- Fixed jagged dashboard price plots and panel-1 scaling: dashboard Databento trade fetches now preserve `instrument_id`/`raw_symbol`, use a new `trades_v2_*` cache, and filter each TU/FV/TY/US parent-symbol day to the highest-volume outright before aggregation. Panel 1 now emits and applies fair-value-aligned TotalAggress/price axis ranges plus `alpha`/`beta` in the footer. Revalidated `2026-04-06 01:00-10:00 ET`: kept 55,505 front-month trades, selected `ZTM6/ZFM6/ZNM6/ZBM6`, and final DV01 aggression stayed within ~4.3% of the unfiltered run.



## 2026-05-19

- Added `docs/macro_event_analysis_state.md`, a comprehensive current-state report mapping event-driven macro dashboards/scripts to artifacts, data sources, analysis depth, status, and next-build candidates; linked it from `memory-bank/activeContext.md` and indexed it in `memory-bank/code-index.md`.



## 2026-05-12

- Investigated why the secondary `economic_events.db` stopped updating after 2026-05-08 12:16 and confirmed the file moved on the mapped `D:` share from `D:\workspace\economic_events.db` to `D:\FRDBs\workspace\economic_events.db`.

- Updated `scripts/run_bbg_data_daily.bat` and `scripts/check_economic_events_db_staleness.py` to use the relocated secondary DB path.

- Manually ran the wrapper and confirmed it now reaches `D:\FRDBs\workspace\economic_events.db`, but the write fails with `sqlite3.OperationalError: attempt to write a readonly database`; ACLs on the folder/file grant full control to a different SID plus Administrators/SYSTEM, not the current `aasthajain\aastha` SID.



## 2026-05-11

- Fixed recurring Telegram approval-forward loss for X tweet score buttons: `scripts/x_macro_stream_bot.py` now retries Telegram `TimedOut`/`NetworkError` sends, forwards approved tweets before committing new labels, and keeps failed forwards retryable instead of silently marking them complete.

- Added `/x_retry_forwards [limit]` and `src/core/x_macro_store.py:list_approved_but_unforwarded()` to recover historical X tweets with `label='approved'` and no `forwarded_message_id`.

- Increased Telegram Bot API request timeouts in `scripts/telegram_bot.py` and added a top-level error handler so callback failures are logged with traceback and update context.

- Applied the same forward-then-label retry semantics to Polymarket alert approvals in `scripts/polymarket_stream_bot.py`, backed by `src/core/polymarket_stream_store.py:mark_alert_forwarded()`.

- Confirmed this root cause is distinct from prior callback-lag and local-DB fixes: the lost forwards came from committing labels before a single-shot public-group Telegram send had succeeded.



## 2026-05-07

- Updated X macro tweet moderation to use a single-step 13-button Telegram keyboard: score buttons `-5..+5` approve+forward with the escalation rubric, `Approve (no score)` forwards without a rating, and `Reject` labels without forwarding.

- Added optional `tweet_labels.score` persistence in `src/core/x_macro_store.py` with an idempotent migration and atomic conditional label updates to prevent duplicate forwards under concurrent callback handling.

- Widened only the X tweet callback regex in `scripts/telegram_bot.py` to include `tweet_score`; Polymarket `pm_*` alert handlers and approval keyboards remain unchanged.



## 2026-04-21

- Investigated stale `economic_events.db` copies after the 3-hour cadence rollout and found the actual failure in `scripts/run_bbg_data_daily.bat`, not Task Scheduler: `:run_and_capture` used `shift /1` and then `%*`, so cmd still expanded the original quoted step label (e.g. `"primary create_bbg_data_table"`) as the command and every scheduled run failed with exit code `9009`.

- Fixed `scripts/run_bbg_data_daily.bat` by removing the positional step-name argument from `:run_and_capture` and using a `CURRENT_STEP` env var set by `:run_target` immediately before each python invocation.

- Manually re-ran the patched wrapper, which refreshed both `workspace/economic_events.db` and `D:\workspace\economic_events.db` successfully and rewrote `%LOCALAPPDATA%\market_data_puller\status\bbg_data_daily_last_success.txt`.

- Triggered `BBGData` once through Task Scheduler and confirmed `Last Result: 0`; next run remains on the expected 3-hour cadence (`HH:15`).



## 2026-04-20

- Recovered both `economic_events.db` targets by manually re-running `data_setup/create_bbg_data_table.py` and `data_setup/ingest_excel_calendar_to_sqlite.py` for `workspace/economic_events.db` and `D:\workspace\economic_events.db`; both files now have fresh 2026-04-20 mtimes again.

- Hardened `scripts/run_bbg_data_daily.bat` so it logs locally under `%LOCALAPPDATA%\market_data_puller\logs\`, writes last-success/last-error marker files under `%LOCALAPPDATA%\market_data_puller\status\`, and still attempts the secondary DB even if the primary target fails.

- Added per-day stale-workbook Telegram dedupe in `data_setup/create_bbg_data_table.py`, persisted via `Path.home() / ".market_data" / "alerts" / "bbg_excel_stale_state.json"` with 7-day trim; verified a second stale check on the same day prints the warning but suppresses the duplicate Telegram send.

- Reworked `scripts/install_bbg_data_task.ps1` to delete `BBGDataDaily`/older experimental names and recreate the loader as `BBGData`, repeating every 3 hours at `HH:15` with dynamic next-slot `StartBoundary`, `ExecutionTimeLimit=PT15M`, `StartWhenAvailable=true`, battery restrictions disabled, and optional `-TaskPassword` support.

- Ran the new loader installer on AASTHAJAIN and verified `BBGData` now repeats every 3 hours from the next `HH:15`; the old `BBGDataDaily` / `BBGDataHourly` entries are gone.

- Reworked `scripts/install_bbg_refresh_task.ps1` to recreate `BBGRefreshExcel` as a 3-hour `/IT` task anchored at `HH:00`, with dynamic next-slot `StartBoundary` and deployment guidance for a local `C:\bbg_refresh\` folder on the Bloomberg machine.

- Added `scripts/check_economic_events_db_staleness.py`, `scripts/run_economic_events_staleness_check.bat`, and `scripts/install_economic_events_staleness_task.ps1` for daily stale-DB monitoring/alerting, then installed the new `EconomicEventsDbStalenessCheck` task and verified the checker reports both DBs fresh.

- Added `scripts/enable_task_scheduler_operational_log.ps1` because direct `wevtutil ... /e:true` failed in the current shell with `Access is denied`; enabling the Task Scheduler Operational log still requires an elevated PowerShell session.



## 2026-04-16

- Moved the Telegram/X/Polymarket SQLite defaults off the `Y:` network share and onto local user-profile storage under `Path.home() / ".market_data"` in `src/core/x_macro_store.py`, `src/core/polymarket_stream_store.py`, and `src/servers/polymarket/poll_midpoint.py`.

- Added env-var overrides `X_MACRO_DB_PATH`, `POLYMARKET_STREAM_DB_PATH`, and `POLYMARKET_MARKETS_DB_PATH` for deployments that need explicit DB locations.

- Created local backup copies of `x_macro_stream.db`, `polymarket_stream.db`, and `polymarket_markets.db` in `C:\Users\aasth\.market_data\` using SQLite's backup API so the switch can happen without relying on raw SMB file copies.

- Fixed Telegram approve/reject lag caused by the Polymarket background poll loop blocking the bot event loop in `scripts/polymarket_stream_bot.py`.

- Wrapped `_poll_once()` midpoint fetches in `run_in_executor(...)` so synchronous `fetch_midpoint()` HTTP calls no longer stall callback query handling.

- Moved chart-history loading into a helper that opens its own SQLite connection inside the executor thread before calling `query_recent_timeseries(...)`, avoiding cross-thread SQLite usage.

- Wrapped `_render_sparkline_chart(...)` in `run_in_executor(...)` so matplotlib chart generation does not delay tweet or Polymarket moderation button responses.

- Updated `scripts/run_bbg_data_daily.bat` so `BBGDataDaily` now runs both overnight loaders twice: once for the repo-local `workspace/economic_events.db` and once for `D:\workspace\economic_events.db`.

- Added per-target log markers (`primary DB` / `secondary DB`) while preserving fail-fast behavior so the task still exits immediately on the first loader error.



## 2026-04-14

- Fixed tweet-group approve/reject lag in `scripts/telegram_bot.py` by enabling `.concurrent_updates(True)` on the main polling bot.

- Root cause was sequential Telegram update processing: long-running orchestrator query handlers could occupy the update processor and delay `tweet_(approve|reject)` callback queries even though the handler work was already offloaded with `run_in_executor(...)`.

- Left the split X architecture unchanged: `scripts/x_macro_stream_bot.py` still only sends tweets, while `scripts/telegram_bot.py` remains the sole polling process for X and Polymarket callback buttons.



## 2026-04-13

- Redesigned `scripts/polymarket_stream_bot.py` Polymarket alerts to send a chart image with a compact caption instead of the older text-heavy `What changed:` standalone message.

- Removed heartbeat notifications entirely; Polymarket alerts now fire only on `significant_change` or `trend`.

- Replaced the fixed `POLYMARKET_SIG_CHANGE_THRESHOLD` logic with `POLYMARKET_MIN_ABS_PP` plus `POLYMARKET_REL_FACTOR`, keeping the existing trend streak / total move controls.

- Added `query_recent_timeseries()` to `src/servers/polymarket/poll_midpoint.py` and `last_alert_ts` persistence in `src/core/polymarket_stream_store.py` so each alert chart can mark the previous alert baseline versus the current move.

- Updated Polymarket approve/reject handling so approved photo alerts copy forward correctly and labeled photo alerts edit their caption instead of failing on `edit_text(...)`.

- Refined Polymarket chart readability in `scripts/polymarket_stream_bot.py`: moved the legend above the chart, restricted plotted lines to the actual movers for the alert, and made captions more descriptive with full market titles and `current <- prior (+/-pp)` context.

- Reframed Polymarket Telegram captions in `scripts/polymarket_stream_bot.py` so notification previews always lead with `Polymarket Odds Update`; market titles now sit on their own line and the probability line is explicitly labeled `Odds:` to avoid headline-like alerts that could be mistaken for actual news.

- Follow-up caption tweak: removed the user query/context line from Polymarket Telegram previews so phone notifications show only `Polymarket Odds Update` plus the market question and explicit `Odds:` line.

- Verified the touched Polymarket files with `py -m py_compile` and a runtime smoke test that rendered a sample chart successfully.



## 2026-04-06

- Updated `scripts/polymarket_stream_bot.py` so Polymarket Telegram alerts now include a plain-English `What changed:` summary line describing the biggest move or trend driver in each alert.

- Lowered Polymarket alert defaults to `SIG_CHANGE_THRESHOLD=0.05`, `TREND_STREAK_POLLS=10`, `TREND_TOTAL_MOVE_THRESHOLD=0.03`, while keeping environment overrides in place.

- Switched Polymarket alert delivery from rolling `edit_message_text(...)` updates to standalone messages for every alert reason (`significant_change`, `trend`, `heartbeat`).

- Wired Polymarket alert approve/reject callbacks into the live bot: every alert now gets inline buttons, approved alerts can forward to `POLYMARKET_PUBLIC_CHAT_ID`, and labels persist through `src/core/polymarket_stream_store.py`.

- Registered the new `pm_(approve|reject)` callback handler in `scripts/telegram_bot.py` and verified both edited Python files compile cleanly with `py -m py_compile`.



## 2026-03-26

- Updated `data_setup/create_bbg_data_table.py` so the BBG workbook stale warning/Telegram alert only fires when the workbook is more than 2 days old, reducing false positives from the overnight scheduler around weekends and similar gaps.



## 2026-03-24

- Fixed the weekly calendar filename matcher in `config/settings.py` so filenames like `3.23 - 4.3 .xlsx` resolve correctly, and re-ran `data_setup/ingest_excel_calendar_to_sqlite.py` successfully (`Rows inserted: 85`).

- Moved Bloomberg workbook defaults to `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx` in the BBG loaders / scheduler wrapper.

- Added stale-workbook detection + Telegram alerting to `data_setup/create_bbg_data_table.py`; dry-run now logs when the workbook was not refreshed today.

- Updated `data_setup/create_bbg_event_fields_excel.py` to generate dynamic end dates with `=TEXT(TODAY(),"YYYY-MM-DD")`.

- Added Bloomberg-machine automation helpers: `scripts/refresh_bbg_excel.py`, `scripts/refresh_bbg_excel.bat`, and `scripts/install_bbg_refresh_task.ps1`.

- Verified the regenerated template output on the share as `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.regenerated.xlsx` with `Summary!B2 = =TEXT(TODAY(),"YYYY-MM-DD")` and BDH formulas referencing `$B$1,$B$2`.

- Updated `scripts/refresh_bbg_excel.py` to attach to the already-open interactive workbook first, leaving that Excel session open after refresh instead of always creating/quitting a hidden Excel instance.

- Updated `scripts/install_bbg_refresh_task.ps1` to create `BBGRefreshExcel` with `/IT` for the logged-in Bloomberg session.

- Updated `scripts/refresh_bbg_excel.bat` and `scripts/install_bbg_refresh_task.ps1` to work when copied directly into the `Meini` folder instead of relying on repo-relative `scripts\...` paths.

- Added `BBG_ALERT_CHAT_ID` support in `src/core/telegram_notifier.py` / `data_setup/create_bbg_data_table.py` and documented channel setup/testing in `docs/bbg_alerts_channel_setup.md`.

- Verified the revised refresh script on the staged workbook and confirmed normalized alert chat resolution now picks a single target instead of returning the raw comma-separated `TELEGRAM_CHAT_ID` list.



## 2026-03-10

- Updated `src/core/x_api_client.py` so X API failures now surface the response body and key rate-limit headers (`retry-after`, `x-rate-limit-*`) instead of a generic `HTTP Error 429`.

- This specifically improves `scripts/x_macro_stream_bot.py` diagnostics for filtered-stream reconnect loops, making it easier to distinguish `TooManyConnections` from broader app/token throttling.

- Verified the updated client compiles cleanly with `py -m py_compile`.



## 2026-02-27

- Fixed `data_setup/load_databento_ohlcv_volume_roll.py` spread filtering: Pass 1 volume aggregation now skips calendar spreads (symbol contains `-`) and user-defined instruments (symbol starts with `UD:`) before determining the active outright contract per day.

- Cleaned 3,739 spread-priced rows (open < 0.10, clearly ZNH6-ZNM6 spread prices) from PostgreSQL `ohlcv_1m` table (symbol `TY`, dates Feb 22-25, 2026). These were loaded by a previous run without spread filtering.

- Re-ran `load_databento_ohlcv_volume_roll.py` with `GLBX-20260227-Q5A476VQLM.zip` (ZN futures, 2025-11-27 to 2026-02-26, ~103k rows). After spread filtering: 94,038 outright rows processed, 9,111 spreads/UD skipped. 1 roll detected (2026-02-25: ZNH6 → ZNM6). 78,831 bars extracted; all skipped on insert (already existed as correct outright data).

- Verified all TY data Feb 20-26 now has correct outright prices (112-113 range), 0 spread-priced rows remain. Total TY rows: 4,945,874 spanning 2010 to 2026-02-26.

- Updated `data_setup/create_bbg_data_table.py` to skip hard dependency on `economic_events` matching: unmatched rows now use Excel-date fallback timestamps (`YYYY-MM-DDT00:00:00`) instead of being dropped.

- Updated the same loader to keep rows even when actual/consensus/std-dev are blank, enabling placeholder insertion first and value updates later.

- Verified via dry-run summary that all parsed rows are now eligible for upsert (`RowsSeen=9962`, `RowsUpserted=9962`) with explicit fallback count reporting.

- Updated `scripts/run_bbg_data_daily.bat` to use UNC path `\\100.83.215.91\productionspace\Meini\bbg_event_fields.xlsx` and append scheduler output to `workspace/logs/bbg_data_daily.log`.

- Updated `scripts/install_bbg_data_task.ps1` so mapped-drive script paths are normalized to UNC in Task Scheduler command creation.

- Reinstalled and re-ran `BBGDataDaily`; task now executes via UNC command path and returns `LastResult=0`.

- Diagnosed the immediate failure cause from `workspace/logs/bbg_data_daily.log`: incorrect UNC host in BBG Excel path (`100.82...` instead of `100.83...`).

- Attempted to enable Task Scheduler Operational log; access was denied in current shell context (requires elevated/admin privileges).



## 2026-02-26

- Extended `scripts/run_bbg_data_daily.bat` so the `BBGDataDaily` Task Scheduler run now executes both the existing BBG loader and a new weekly-calendar SQLite ingest step.

- Added `data_setup/ingest_excel_calendar_to_sqlite.py` to parse `Calendar` sheet events and insert placeholder upcoming events into `workspace/economic_events.db` (`source=excel_calendar`) without overwriting existing natural-key rows.

- Added weekly file auto-detection in `config/settings.py` (`resolve_event_upstream_excel_path`) to pick the newest date-range workbook in `Y:/Meini/*.xlsx` (pattern `M.D -M.D.xlsx`) with fallback to `EVENT_UPSTREAM_EXCEL_PATH`.

- Updated `src/services/redis_event_stream/ingestor.py` to reuse the same resolver helper so Postgres ingestion also follows weekly workbook changes automatically.

- Updated Memory Bank docs: `memory-bank/activeContext.md`, `memory-bank/code-index.md`, and `memory-bank/io-schema.md`.



## 2026-02-25

- Added `scripts/snapshot_explorer_dashboard.py`, a standalone Flask + embedded HTML dashboard for Snapshot Query API exploration.

- Implemented backend endpoints for all query types: `/api/live`, `/api/at`, `/api/history`, `/api/today`, `/api/search`, `/api/diagnose`, and SSE `/api/subscribe`, plus `/api/meta` for UI metadata.

- Added tabbed frontend with forms for all query modes, dynamic `Cond` condition builder, column selector, sortable result table, query timing/row-count display, and Plotly line chart rendering for time-series outputs.

- Added `today(interval=...)` sampling fallback by mapping to current-trading-day `history(...)` because native `SnapshotAPI.today()` does not support an interval argument.

- Validated locally on port `5080`: endpoint smoke tests passed for live/at/history/today/search/diagnose and SSE subscribe stream returned `status` + `snapshot` events.

- Updated Memory Bank docs: `memory-bank/code-index.md`, `memory-bank/io-schema.md`, and `memory-bank/activeContext.md`.

- Added `scripts/ppi_twofactor_pca_dashboard.py`, a standalone PPI two-factor PCA dashboard mirroring the CPI/NFP prototypes with PPI-specific parsing (`PPI` sheet in `bbg_event_fields.xlsx`) and DB event timestamp join (`Core PPI MoM` in `workspace/economic_events.db`).

- Pre-event factors use `[PPI_consensus_std_dev, Core_PPI_consensus_std_dev]` (with consensus fallback if flat); post-event factors use standardized surprises for both components: `(actual - consensus) / std_dev`.

- Reused the existing standalone HTML UI (bucket tables, clickable boxplots, drilldown paths/distributions/candles, hourly post-event B/E panel) with PPI label retitling and output to `workspace/scenarios/ppi_twofactor_pca_dashboard_*.html`.

- Updated Memory Bank docs: `memory-bank/code-index.md` and `memory-bank/io-schema.md` for the new PPI dashboard script and CLI/derived I/O surfaces.

- Extended `scripts/ppi_twofactor_pca_dashboard.py` to incorporate **Supercore PPI MoM** in post-event factor construction:

  - Added workbook parsing for Supercore block.

  - Added backward as-of alignment for Supercore values to release dates (month-end series alignment).

  - Added Supercore historical-z proxy using prior-release expanding mean/std (`shift(1)`), then blended with Core surprise for post-event feature 2.

- Added a current-input field in the generated HTML for `Supercore PPI MoM Actual` and wired runtime scoring to use it with latest historical mean/std normalization.

- Regenerated dashboard with supercore-aware logic: `workspace/scenarios/ppi_twofactor_pca_dashboard_20260225_093132.html`.



## 2026-02-20

- Added `pm_monday_breakevens_export.py` (repo root) to export forward breakevens for the last N Mondays (default 50) from PricingMonkey board `898a2390-bb96-405c-b5d2-80648ec352a8`.

- Monday run logic uses BF as prior Friday (`monday - 3 days`) and AF as Monday, reusing stable grid automation/parsing helpers from `pm_nfp_weeks_export.py`.

- Updated Memory Bank docs: `activeContext.md`, `code-index.md`, and `io-schema.md` with the new script behavior and CLI contract.

- Added Redis environment overrides in `config/settings.py` (`REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`) so publisher/subscriber/dashboard can point to a shared Redis without code edits.

- Added `scripts/redis_alerts_dashboard.py`, a live Flask HTML monitor for Redis stream `market:events` with auto-refresh and `/api/alerts` JSON endpoint.

- Added new DB-centered Redis pre-alert streaming package at `src/services/redis_event_stream/` with:

  - `excel_parser.py` (Calendar + holidays parsing, normalization, parse metrics)

  - `session_config.py` (DST-aware Tokyo/London/US session event generation)

  - `ingestor.py` (Excel -> Postgres upsert, soft-deactivate missing rows, retention cleanup, row-count guardrails)

  - `publisher.py` (Postgres watermark polling + APScheduler pre-alert scheduling + Redis Stream publish with dedupe)

  - `subscriber.py` (consumer-group stream listener CLI)

  - `models.py`, `schema.sql`, package `__init__.py`

- Added Redis/service/session constants to `config/settings.py`.

- Added dependencies to `requirements.txt`: `openpyxl`, `redis`, `apscheduler`, `psycopg2-binary`.

- Implemented deterministic `event_id` generation and pre-alert-only publish semantics (10 minutes before scheduled event time).

- Implemented table growth controls via archive+purge retention logic and row-count threshold logging.



## 2026-02-13

- Reverted holiday chart downsampling in `scripts/us_holiday_candlestick_dashboard.py` to preserve full 5-minute candlestick continuity (removed thinning that made plots look sparse/disconnected in some windows).

- Regenerated raw-5m output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_163012.html`.

- Finalized Thanksgiving/holiday candlestick readability fix in `scripts/us_holiday_candlestick_dashboard.py`: switched dense-view rendering to **candle thinning** (sampling original 5m candles) instead of OHLC bucket aggregation, preventing artificial wick inflation while still reducing crowding.

- Removed temporary debug instrumentation fetch logs from the dashboard script after validation.

- Regenerated clean production output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_162713.html`.

- Updated holiday candlestick hover behavior to custom-only tooltip path (`hovermode: 'closest'`) in `scripts/us_holiday_candlestick_dashboard.py`, preventing Plotly unified/default rows from appearing alongside user-formatted values.

- Regenerated custom-hover output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_155911.html`.

- Added Tier-1 event overlays to `scripts/us_holiday_candlestick_dashboard.py` candlestick plots: vertical markers for US `CPI` (blue), `NFP` (purple), and `PCE` (orange) within each holiday window.

- Implemented tier classification and window fetch from `workspace/economic_events.db` (`economic_events` table), embedded as `tier1_events` per holiday payload.

- Regenerated Tier-1 overlay output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_153519.html`.

- Switched `scripts/us_holiday_candlestick_dashboard.py` candlestick display to **decimal-only** price format (y-axis and hover values), removing bond-notation ticks from the plot.

- Regenerated decimal-format output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_152558.html`.

- Investigated Thanksgiving 2024 candlestick oddity in `scripts/us_holiday_candlestick_dashboard.py`: root cause was bond-price label rounding producing invalid edge labels near 32nds boundaries.

- Fixed JS price formatter to carry-safe 1/128-point quantization with explicit suffix mapping (`'2`, `+`, `'6`) so labels never overflow to invalid `..'32`.

- Regenerated with formatter fix: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_152455.html`.

- Simplified holiday dashboard move metrics to **bps only** in `scripts/us_holiday_candlestick_dashboard.py` (removed ticks/points display from Window Return and Holiday-Day Move cards).

- Regenerated bps-only output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_144237.html`.

- Added per-selection stats panel to `scripts/us_holiday_candlestick_dashboard.py` with:

  - **Window Return (t-3d -> t+3d)** net move in bps, ticks, and points (first close -> last close in plotted window)

  - **Holiday-Day Move** close-open move on observed date, with automatic nearest-session fallback when observed date has no trading candles

- Regenerated stats-enhanced output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_144139.html`.

- Updated `scripts/us_holiday_candlestick_dashboard.py` to render candlestick Y-axis in Treasury bond notation (32nds) with custom tick labels and bond-formatted hover OHLC values.

- Regenerated bond-format output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_143927.html`.

- Updated `scripts/us_holiday_candlestick_dashboard.py` to drop holiday rows that have no TY candles in the configured window, so the table only shows dates with available data.

- Regenerated filtered output: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_143613.html` (2022-2026 sample now includes 41 holidays with data).

- Updated `scripts/us_holiday_candlestick_dashboard.py` chart interactivity: enabled Plotly mouse-wheel zoom (`scrollZoom`), persistent modebar, drag-zoom default, and reset-on-double-click behavior for faster holiday window inspection.

- Regenerated output after interactivity update: `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_141742.html`.

- Added `scripts/us_holiday_candlestick_dashboard.py` to generate a standalone HTML holiday dashboard (default years `2022 -> current`) with the requested US holiday list and click-to-render TY candlestick windows (`+/-3` days around observed holiday date).

- Verified generation with `python scripts/us_holiday_candlestick_dashboard.py --start-year 2022 --end-year 2026`; output written to `workspace/scenarios/us_holiday_candlestick_dashboard_20260213_141547.html`.

- Added `src/core/fomc_qa_parser.py` to deterministically parse FOMC transcript segments into speaker utterances and extract reporter-question -> Powell-answer blocks, anchored on Powell answer start times.

- Added `scripts/fomc_qa_microstructure_alignment.py` to align each historical Powell answer to market windows (`0-30s`, `30s-2m`, `0-10m`) and compute **signed bps** reaction metrics, realized-vol spike diagnostics, and trend/mean-revert consistency labels.

- Implemented output artifacts for the new pipeline in `workspace/fed/fomc conference/qa_microstructure/`: per-transcript JSON, aggregate `answer_level_summary.csv`, and `run_summary.json`.

- Verified end-to-end smoke run with `python scripts/fomc_qa_microstructure_alignment.py --max-transcripts 1`; generated 25 answer rows for `2020-01-29` using available `ohlcv_1m` + `TY` fallback.

- Rewrote `docs/cpi_twofactor_pca_plan.md` into a consolidated status + roadmap doc covering: original step plan, current implementation state, what works, known failure modes, and phased future implementation/analysis plan.

- Added explicit future priorities in that plan: regime-conditioned calibration, tail-aware buckets/metrics, limited nonlinear terms, and positioning proxy integration.

- Added `fcastofthemonth` to the default X macro handle seed list in `src/core/x_macro_store.py` as `tier=3`, `mode=gated`.

- Inserted the same handle into the live `workspace/x_macro_stream.db` `handles` table so it is active immediately without waiting for a reseed.

- Added `scripts/nfp_twofactor_pca_dashboard.py`, a standalone NFP two-factor PCA dashboard mirroring the CPI prototype with NFP+UER factors from `bbg_event_fields.xlsx` (`NFP & Subcomponents` sheet).

- Pre-event factors use `[NFP_consensus_std_dev, UER_consensus_std_dev]` (with consensus fallback if flat); post-event factors use standardized surprises for both components: `(actual - consensus) / std_dev`.

- Verified generation with `python scripts/nfp_twofactor_pca_dashboard.py --start-date 2022-01-01`, output: `workspace/scenarios/nfp_twofactor_pca_dashboard_20260213_092437.html`.



## 2026-02-12

- Reworked dashboard Telegram share to true bot-based delivery (no download/paste): plot-panel `Send to Telegram` now captures PNG client-side, opens a chat picker populated from `/api/telegram/chats`, and posts directly to `/api/telegram/send_snapshot` for `sendPhoto` delivery.

- Added new unified dashboard backend APIs in `scripts/live_unified_dashboard.py`:

  - `GET /api/telegram/chats` -> returns allowed bot chats with resolved human-readable names (`title` / `@username` / first+last fallback).

  - `POST /api/telegram/send_snapshot` -> accepts multipart `image` + `chat_id` (+ optional caption), validates against `ALLOWED_CHAT_IDS`, and sends via Telegram Bot API.

- Added Telegram chat-name resolution cache (TTL) and stdlib multipart upload helper for direct `sendPhoto` calls.

- Added dashboard-wide Telegram share UX in `scripts/live_unified_dashboard.py`: each chart/table card now gets a **Send to Telegram** button that captures the panel as PNG (Plotly export for charts, `html2canvas` fallback for generic panels), downloads the image, and opens Telegram share (`t.me/share/url`) so users can send to any chat.

- Added optional clipboard image copy on share (when browser supports `ClipboardItem`) so users can paste directly in Telegram after the share window opens.

- Post-Event first-load defaults now hydrate from recommended values in the unified dashboard flow: `surprisePercentileInput` defaults to the implied percentile of the current `(actual-consensus)/std` against historical standardized surprises, and `postBucketWidthInput` defaults to `20` (5 percentile buckets).

- Wired these defaults through `scripts/live_unified_dashboard.py` -> `scripts/dashboard_tabs/postevent_tab.py` config payload and added JS guardrails so Post-Event still initializes if inputs are blank/invalid at first render.

- Unified dashboard event pages now auto-infer default analysis inputs from historical data in `scripts/live_unified_dashboard.py`: `user_cons_std` defaults to historical median consensus std dev, `user_consensus`/`user_actual` default to the latest finite values in history, and these resolved defaults are emitted in `dashboard_data["ui_defaults"]` for consistent client hydration.

- Pre-event bucket-width defaults now auto-load from historical consensus-std range with a target of ~5 visible buckets, and event-specific numeric constraints (step/min/max) are now passed through into pre-event controls instead of relying on generic NFP-style hardcoded values.

- Updated pre-event runtime bucket recommendation in `scripts/dashboard_tabs/preevent_tab.py` to use a 5-bucket target model (adjusted for inclusive boundary bucket rendering) so first-load bucketing is aligned with requested density.

- Added `scripts/cpi_twofactor_pca_dashboard.py`, a standalone CPI two-factor PCA prototype that reads CPI fields from Bloomberg workbook, joins event timestamps from `workspace/economic_events.db`, computes TY pre/post bps windows, and emits a self-contained HTML dashboard.

- Implemented pre-event factor construction from component uncertainty (`mom_cons_std`, `core_mom_cons_std`) and post-event factor construction from trend-adjusted standardized surprises using rolling regression expected MoM from YoY levels.

- Added quantile bucketing + lookup (bounds, matched N, mean bps) and factor-to-bps attribution (`beta1*f1 + beta2*f2 + intercept`) in the embedded UI.

- Enforced 2022+ sample boundary via default CLI `--start-date 2022-01-01` and verified runtime generation: `workspace/scenarios/cpi_twofactor_pca_dashboard_20260212_093229.html`.

- Follow-up fix: pre-event CPI std-dev fields were flat across the 2022+ sample (collapsing PCA to near-zero factors), so the script now auto-falls back to `[mom_consensus, core_mom_consensus]` for pre-mode factorization and surfaces a warning in the UI.

- Added embedded SVG bucket boxplots for pre and post modes, showing all buckets and highlighting the currently matched bucket; verified updated output: `workspace/scenarios/cpi_twofactor_pca_dashboard_20260212_094534.html`.

- Updated bucketing logic to use **regression-weighted PCA factors** (factor->bps linear model with intercept) instead of variance-weighted PCA composite for bucket assignment; UI now shows regression score, equation, `R2`, and `RMSE`.

- Added a bottom panel with pre/post economic interpretation text for factors (common-direction vs spread/mix interpretation from loadings) and regenerated output: `workspace/scenarios/cpi_twofactor_pca_dashboard_20260212_095313.html`.

- Added mockup extensions to the CPI two-factor dashboard: pre-event **market-derived uncertainty** PCA inputs (trailing realized range/vol in bps, computed strictly before `3pm T-1`) and an **abs(|F|)** extent-friendly regression scoring option with UI toggle to compare fit metrics and bucket assignments.

- Implemented remaining CPI dashboard drilldown UX: bucket rows/boxplots are now clickable for pre/pre-market/post modes, with selected-bucket state and conditional drilldown rendering.

- Added bucket drilldown charts to the standalone HTML: normalized price paths on a shared aligned timestamp axis and a distro-style histogram+KDE panel for bucket-member outcomes.

- Added bucket conditional stats line with bootstrap 95% mean CI and small-sample warning (`N < 5`) in the drilldown panel.

- Extended post-event bucket analytics window from `8:30am -> 3:00pm` to `8:30am -> 5:00pm` ET for consistency with requested drilldown/stat horizon.

- Added per-event normalized path payload fields (`pre_path`, `post_path`) so drilldowns do not require server calls.

- Fixed `render_html()` JS template escaping in `scripts/cpi_twofactor_pca_dashboard.py` (`defaultSelected` object literals) by doubling braces inside the Python f-string, resolving runtime `NameError: name 'mode' is not defined`.

- Fixed a frontend parse failure in `scripts/cpi_twofactor_pca_dashboard.py`: removed an extra `}` after `renderBucketDistro()` that produced `Uncaught SyntaxError: Unexpected token '}'` and prevented all dashboard data hydration in the generated HTML.

- Upgraded CPI bucket drilldown visuals in `scripts/cpi_twofactor_pca_dashboard.py`: normalized paths chart now has interactive trace selection + hover readout, both drilldown charts now render explicit x/y axes and axis labels, and the distribution panel now includes an in-chart stats box (N/mean/std/P5/P50/P95).

- Split CPI metrics by panel: table/regression/bucket means remain **B/E-style extent** (`max(high-open, open-low)`), while drilldown distro now uses **signed close-to-close** window returns (`points_to_bps(close_last - close_first)`).

- Added per-event full-window candlestick payload (`3:00pm T-1 -> 5:00pm event day`) and wired drilldown interaction so clicking a normalized price path renders that event’s candlestick chart in the new drilldown panel.

- Added hourly post-event B/E series (`t+1h` to `t+8h`) to `scripts/cpi_twofactor_pca_dashboard.py` for every historical event and exposed a new dashboard panel with hourly mean B/E in bps for all history plus each post-event bucket.

- Refined CPI post-event hourly drilldown UX: hourly table now opens from clicked bucket rows (post mode), uses explicit clock snapshots (`08:30, 09:00, 10:00, ..., 15:00` ET), and shows per-time mean B/E bps with sample count `N`.



## 2026-02-11

- Fixed CPI Pre-Event empty plots: `build_dashboard_data()` now populates `be_by_time`/`bps_by_time` at release time and backfills `surp_pct` from `standardized_surprise` when missing, so `/api/events?event_type=CPI` returns the fields expected by Pre-Event boxplot/distribution panels.



- CPI Polymarket tab split implemented per market type: separate **Headline MoM** analytics table and **Headline YoY** odds-only table on CPI mode. Added backend endpoint `/api/scenarios/cpi_yoy_odds` and constrained `/api/scenarios/table?component=cpi` to monthly inflation labels only.



- Patched `scripts/databento_live_state.py` live stream loop to auto-reconnect after Databento gateway/network errors instead of exiting permanently. This prevents `/api/live` from staying stale in the Polymarket live strip after a transient timeout.



- Adjusted Polymarket Scenarios tab mode behavior: when dashboard event is NFP, only NFP+UER scenario cards load/refresh; when dashboard event is CPI, only CPI scenario card loads/refreshes. Added `event_type` passthrough from `generate_html_dashboard()` into `generate_polymarket_scenarios_tab_html()` and switched JS refresh/resolution/ladder loops to an `pmEnabledComponents` set.



- Extended unified dashboard Polymarket Scenarios flow to support **CPI** alongside NFP/UER: backend APIs now accept `kind/component=cpi`, DB keyword resolution includes January inflation markets, and scenario surprise/bucket computations include CPI event-name aliases from `bbg_data`.

- Updated `scripts/dashboard_tabs/polymarket_scenarios_tab.py` to add a CPI table (`pmCpiTable`) with consensus/std inputs, expected-row calculations, histogram drilldown integration, and inclusion in the projected-price ladder.

- Updated generated unified dashboard header event dropdown to include all supported event types (including CPI) and navigate to per-event routes on selection.

- Added regression coverage in `tests/unified_dashboard/test_tab_html_smoke.py` asserting CPI table presence in Polymarket tab HTML.



- Added `scripts/ten_thirty_auction_weeks.py` to generate a standalone 10Y/30Y auction weeks TY dashboard using only `economic_events.db` date anchors (`10-Year Note Auction`, `30-Year Bond Auction`) and PostgreSQL `ohlcv_1m` market data.

- Generated `scripts/ten_thirty_auction_weeks_dashboard.html` with supply-weeks-style controls: normalized paths with zoom re-normalization, 10Y/30Y windowed distribution analysis (`pre_10y`, `10y_to_30y`, `post_30y`, auction-day windows), and selected-week 5-minute candlestick modal.

- Updated `tinkering/nfp_uer_4x4_postevent_grid.py` path-chart y-axis to bond-format prices (32nds), plotting in ticks with axis tick labels shown as bond notation; conversions use `rules/treasury_price_conversion.py` (`decimal_to_ticks`, `decimal_to_bond_notation`, `points_to_bps`).

- Regenerated HTML with bond-format price paths: `workspace/scenarios/nfp_uer_4x4_postevent_grid_20260211_123235.html`.

- Updated `tinkering/nfp_uer_4x4_postevent_grid.py` candlestick drilldown to remove non-NFP event annotations and keep only the NFP event-time marker; regenerated HTML at `workspace/scenarios/nfp_uer_4x4_postevent_grid_20260211_122835.html`.

- Updated `tinkering/nfp_uer_4x4_postevent_grid.py` to align path x-axis by **time-of-day** (not calendar day), so events from different dates overlay on the same clock-time axis.

- Added path-click drilldown in the same dashboard: clicking a path now shows exact NFP/UER values (actual, consensus, std dev, z-surprise, percentile) and renders a ±2 day 5-minute candlestick chart.

- Regenerated HTML with these interactions: `workspace/scenarios/nfp_uer_4x4_postevent_grid_20260211_122544.html`.

- Updated `tinkering/nfp_uer_4x4_postevent_grid.py` path-chart x-axis to event timestamps (`type:"date"`) and enabled Plotly scroll zoom (`scrollZoom:true`) for direct zoom/scroll interaction; regenerated HTML at `workspace/scenarios/nfp_uer_4x4_postevent_grid_20260211_122051.html`.

- Updated `tinkering/nfp_uer_4x4_postevent_grid.py` to remove mean-path overlay from clicked-cell plots and add per-cell `Rally Rate` to both grid-box stats and selected-cell stats panel.

- Re-ran generator and produced updated HTML: `workspace/scenarios/nfp_uer_4x4_postevent_grid_20260211_121817.html`.

- Added `tinkering/nfp_uer_4x4_postevent_grid.py` to generate a standalone interactive HTML 4x4 grid of NFP surprise percentile vs UER surprise percentile (25% buckets) using `bbg_data` standardized surprises and TY post-event paths.

- New grid cells display t->EOD mean and P5/P50/P95 moves, plus mean intraday max-up/max-down and rally rate; clicking a cell shows normalized post-event event paths for events in that bucket.

- Ran script successfully for TY and generated output: `workspace/scenarios/nfp_uer_4x4_postevent_grid_20260211_121205.html` (paired events: 37, with TY paths: 37).

- Updated Memory Bank `code-index.md`, `io-schema.md`, and `activeContext.md` for the new tinkering dashboard script.

- Updated post-event command playbook (`.cursor/commands/post_event.md`) default horizons from `t+10m/t+30m/t+1h/t+2h` to `t+1h/t+2h/t->EOD`, explicitly defining EOD as **5:00pm ET same day**.

- Updated the canonical post-event template (`scripts/ism_services_pmi_postevent_dashboard.py`) to remove `t+10m`/`t+30m` and compute the EOD distribution as `t -> 5:00pm ET` on the event date.

- Synced Memory Bank `code-index.md` summaries for the post-event template and command playbook.

- Updated `scripts/non_farm_payrolls_postevent_dashboard.py` to use horizons `t+1h`, `t+2h`, and `t->EOD` (5:00pm ET same day), and added CLI scenario defaults (`--actual`, `--consensus`, `--consensus-std-dev`).

- Ran NFP post-event generation with `/post_event` scenario inputs (actual=130k, consensus=65k, std dev=26k): output HTML `workspace/scenarios/non_farm_payrolls_postevent_dashboard_20260211_090839.html`.

- Updated Memory Bank `io-schema.md` and `code-index.md` for the NFP post-event script I/O and behavior.



## 2026-02-10

- Unified dashboard runner now supports all configured event types (`NFP`, `CPI`, `PPI`, `JOLTS`) via `scripts/run_server.py --event ...` so non-CPI/NFP launches use the same refreshed UI shell.

- Polymarket Scenarios tab UI polish in `scripts/dashboard_tabs/polymarket_scenarios_tab.py`: upgraded card/live-strip/table/modal styling, sticky headers, expected-row highlighting, row-hover polish, and responsive control/table behavior.

- Unified dashboard Event Analysis UI pass (Pre/Post only): added clearer panel status badges (axis/mode/small-sample), data-quality summary strips, busy-state update buttons, and reset-view controls for price-path windows; scoped to `scripts/dashboard_tabs/preevent_tab.py` and `scripts/dashboard_tabs/postevent_tab.py` with no Polymarket tab changes.

- Fixed unified dashboard Pre/Post hydration path: `/api/event_details` now bypasses lightweight cache entries unless they already contain hydrated `prices` and `postevent_prices`, so Pre/Post tabs load full event details instead of staying at `0 events`.

- Normalized `/api/event_details` payload shape to always emit `prices`/`postevent_prices` lists and `be_by_time`/`bps_by_time` dicts (empty defaults when unavailable) to keep Pre/Post client logic stable.

- Added regression unit coverage in `tests/unified_dashboard/test_tab_html_smoke.py` for detail-payload detection (`_event_has_detail_payload`) while keeping Polymarket guardrails unchanged.

- Added projected price ladder panel above scenario tables with two columns (`Projected @8:30`, `Projected @3pm`), bond-price vertical axis, and box tags for NFP/UER ranges (green rally / red selloff markers).

- Added projected price columns to NFP/UER scenario tables: `Projected @8:30` (from pre-event B/E) and `Projected @3pm` (from post-event B/E), each displayed as rally/selloff bond prices (`a/b`, green/red) using current live price plus remaining expected move after realized move.

- Polymarket live-strip B/E realized now shows explicit sign and direction color: `+` when `high-open` dominates and `-` when `open-low` dominates.

- Enforced pre/post B/E window convention in unified dashboard logic: pre-event uses 3:00pm to 8:29am (excludes 8:30 print), post-event starts at 8:30am and runs to 3:00pm.

- Polymarket Scenarios top section now shows live Databento status/current TY price, 3pm reference price with reference date, and current realized B/E; expected-value footer rows are highlighted in yellow for visibility.

- Updated Polymarket bin-drilldown paths chart X-axis to actual timestamps (datetime axis) instead of relative minutes.

- Polymarket Scenarios histogram now supports click-to-bin path drilldown: clicking a histogram bar fetches and plots event price paths for that bin with event dates shown in legend.

- Added new backend endpoint `/api/scenarios/bin_paths` in `scripts/live_unified_dashboard.py` to return bin-filtered event paths (`date`, `value`, and per-minute `t_min`/`bps` points) for the active component/bucket/window.

- Polymarket Scenarios selection behavior updated to global single-select: clicking a row in NFP or UER now clears any existing highlight in both tables and highlights only the newly clicked row.

- Polymarket Scenarios table UX: clicking any NFP/UER row now applies a persistent selected-row highlight so users can clearly see which scenario is currently active in the histogram modal.

- Polymarket Scenarios modal polish: row-click modal title fixed to "Historical Returns Distribution", chart axis/title switched to returns wording, and modal stats panel restyled to white background with black text for readability.

- Updated Polymarket Scenarios row-click histogram modal to distro style: density histogram + KDE overlay, full distro stats lines (mean/percentiles/expected rally/expected selloff/0 bps percentile), and last-2-event vertical annotations.

- Extended `/api/scenarios/hist` in `scripts/live_unified_dashboard.py` to return `rows` (timestamp + value), `stats`, `stats_lines`, and `last_two` markers for UI parity with distro_adhoc behavior.

- Added unified dashboard Polymarket Scenarios tab (`scripts/dashboard_tabs/polymarket_scenarios_tab.py`) with NFP/UER scenario tables, probability-weighted expected pre/post B/E rows, and row-click histogram modal (t-12h to t+12h).

- Added new unified dashboard APIs in `scripts/live_unified_dashboard.py`: `/api/polymarket/resolve`, `/api/polymarket/latest`, `/api/scenarios/table`, and `/api/scenarios/hist`, all wired to `polymarket_markets.db` odds plus 2022+ `bbg_data` surprise buckets.

- Updated `scripts/databento_live_state.py` to initialize the live reference price from a 3pm ET Databento historical lookup (fallback to first live tick if unavailable).

- Restored missing unified dashboard helper functions in `scripts/live_unified_dashboard.py` (`_get_latest_dashboard_file`, cache load/save helpers, `build_dashboard_data`, `generate_html_dashboard`, `build_live_unified_dashboard`) so run/standalone paths can resolve.

- Added Telegram-native Polymarket tracking service: `/pm_track` multi-select confirmation flow, `/pm_list`, and `/pm_untrack` management commands.

- Added `scripts/polymarket_stream_bot.py` with background polling task (default 30s), midpoint time-series inserts to `polymarket_markets.db`, and alert triggers for significant change (>=10pp), trend streak (6 polls + >=5pp), and 15-minute heartbeat.

- Added inline approve/reject callbacks for Polymarket alerts with label persistence and optional forwarding to `POLYMARKET_PUBLIC_CHAT_ID`.

- Added `src/core/polymarket_stream_store.py` for subscriptions, pending selections, alert labels, and alert-state persistence across restarts.

- Wired Polymarket handlers into `scripts/telegram_bot.py` and updated `/help` command text.

- Updated Memory Bank `code-index.md` and `io-schema.md` with the new Polymarket Telegram service and environment variables.



## 2026-02-09

- Fixed a startup `SyntaxError` in `src/servers/tradingeconomics/schema.py` by separating concatenated SQL constants for `date_tags` and `event_tags` table/index DDL blocks.

- Refactored `scripts/x_macro_stream_bot.py` into a sender-only X stream service (no Telegram polling loop); it now always uses the consolidated `TELEGRAM_BOT_TOKEN`.

- Removed in-process X stream startup from `scripts/telegram_bot.py` while keeping `/x_*` command handlers and approve/reject callbacks in the main polling bot.

- Updated Memory Bank docs (`code-index.md`, `io-schema.md`) to remove `X_MACRO_BOT_TOKEN` and document the service split (X collection service + Telegram polling bot).

- Standalone pre-event HTML now inlines Plotly (cached asset) to avoid blank charts when CDN is blocked.



## 2026-02-09

- Added X filtered-stream → Telegram macro bot (separate service) with global handle/keyword store, rule builder, stdlib X API client, and Telegram command interface.

- Seeded foundation handle list + keyword set; rule builder supports tag prefixing and 2048-char chunking.

- Added unit tests for X rule building and updated Memory Bank (code-index + io-schema).

- Scoped X macro bot delivery to `X_MACRO_CHAT_ID` override and added the new chat ID to defaults.

- Added X macro bot note + commands to existing Telegram bot `/help`.

- Added inline approve/reject flow for X macro tweets with SQLite label tracking and optional forwarding to a public chat.

- Added tweet approval workflow: inline approve/reject buttons, label storage in SQLite, and forwarding to a public group on approval.

- Integrated X macro stream into `scripts/telegram_bot.py` (single process), registering `/x_*` and tweet callback handlers there to avoid `getUpdates` conflicts from multi-process polling.



## 2026-02-06

- Unified dashboard: pre-event bucket warnings for n<3 and realized B/E now vs window.

- Unified dashboard: auto bucket-width recommendation extended across CPI/PPI/JOLTS.

- Unified dashboard: moved “Analyze From Year” into pre-event component/time-window panel.

- Unified dashboard: pre-event window now uses end-time slider + lookback days (x, 1–5) with start at 3pm T‑x.

- Unified dashboard: added auto‑recommended bucket width (3–5 buckets) and selected‑bucket realized B/E stats (avg multiplier + avg bps).

- Added `event_tags` table to `workspace/economic_events.db` for tagging events with time ranges, comments, and impact notes.

- Added `data_setup/populate_event_tags.py` interactive CLI and schema constants in `src/servers/tradingeconomics/schema.py` for event tag ingestion with overlap merging.

- Unified dashboard: multi-component pre-event buckets now use total z-score (sum of per-component z-scores) and display individual + total scores.

- Unified dashboard: pre-event heavy panels now sample events and decimate price points to avoid browser OOM on Update Analysis.

- Unified dashboard: Update Analysis now skips heavy panels; added “Render price paths (heavy)” with scattergl + Plotly.react, rangeslider off, and bucket-only paths.

- Unified dashboard: hard cap total plotted points for pre-event paths; skip render with warning when exceeded.

- Unified dashboard: prune cached per-event price arrays when heavy panels are disabled to reduce memory pressure.

- Unified dashboard: add epsilon to std dev and clip multi-component z-scores to observed min/max range.

- Added `tinkering/x_fetch_tweet.py` (stdlib-only X API v2 tweet fetcher) and new `X_BEARER_TOKEN` slot in `config/keys.env`.

- Extended `tinkering/x_fetch_tweet.py` to fetch the latest tweet by username (optional retweets/replies).



## 2026-02-05

- Added `scripts/totalaggress_vs_price_chart.py` to generate a dual-axis **TotalAggress vs Price/Yield** chart from Databento trade data + OHLCV. Iterations:

  - **v1**: Basic Plotly HTML with Databento API trades (ZN.FUT) + PostgreSQL OHLCV (empty for this period).

  - **v2**: Fixed Databento `side` field mapping (empirically verified: B=buy-aggress +1, A=sell-aggress -1).

  - **v3**: Added `--ohlcv-zip` support; auto-detects `workspace/wenn/ohlcv.zip` for price series (front-month ZNH6 by daily volume).

  - **v4**: Added `--png` mode (matplotlib), `--plot-start`/`--plot-end` for time trimming, `--bucket 1min` for matching OHLCV resolution.

  - **v5**: Filtered spread trades (price < 80 or > 150, removed 276 non-outright trades from ZN.FUT parent).

  - **v6**: Calibrated DV01 to **$125/bp** to match reference chart axis range (-12M→+4M). Typical TY CTD-derived DV01 is ~$120-130.

  - **v7**: Fixed axis ranges to reference mapping: left=-12M→+4M, right=111.4→112.1. Added axis-aligned fair-value model: `P_implied = 111.925 + Aggress × 4.375e-8`. Verified: Aggress=-4M → P_implied=111.75 (matches colleague's "price should be 111.75"). Added residual stats to JSON.

  - **v8**: Eliminated weekend flat lines by using OHLCV index as the authoritative time grid (trading-hours only). TotalAggress forward-filled onto OHLCV timestamps only.

- Added `data_setup/create_bbg_data_table.py` to create/populate `bbg_data` from the Bloomberg workbook, enriching rows with event timestamps/units from `economic_events` plus category/tier/parent metadata. Added daily Task Scheduler helpers (`scripts/run_bbg_data_daily.bat`, `scripts/install_bbg_data_task.ps1`).

- Added `data_setup/load_bbg_event_fields_to_db.py` to load Bloomberg event fields from `Z:/Meini/bbg_event_fields.xlsx` into `economic_events.db`. Parses each sheet (one per event group), extracts Date/Actual/Consensus/Std Dev per component block, and updates DB with Excel values taking precedence on conflicts. Supports `--dry-run` for preview.

- Added unit normalization in the Bloomberg loader so events with DB unit "K" (thousands) scale down Excel values when they appear 1000x too large (e.g., Jobless Claims).

- Filled `post_event.md` playbook with steps to build event-specific post-event dashboards from the ISM Services PMI template.

- Clarified normalized surprise formula, percentile basis, and 20-point bucket width in `post_event.md`.

- Noted that similar-events plots must filter to the current surprise-percentile bucket and updated run example to `--bucket-width 20`.

- JOLTS post-event dashboard now scales actual/consensus by 1000 when values are too small to fix unit inconsistency.

- JOLTS post-event dashboard now uses consensus std dev for normalized surprise and percentile bucketing, with a matching input field in the HTML.

- Added `scripts/jolts_job_openings_postevent_dashboard.py` as a post-event dashboard template for JOLTS Job Openings (surprise percentile buckets, t+10m/t+30m/t+1h/t+2h distributions, stats boxes, last-two-event markers).



## 2026-02-04

- Added Perplexity fade lookup to `scripts/geo_analysis.py` with cached results and optional CLI flags, surfacing fade estimates in cards and modal.

- Geo analysis dashboard now computes fade **from market data** (retracement within detected spike window) and uses web lookup only for narrative/sources (UI avoids provider naming).

- Geo analysis dashboard now parses narrative time hints to compute an **estimated window** (return/vol/fade) when event time is unknown.

- Added curated per-event `summary` narratives for geo events, and render summaries in the modal (policy dashboard style).

- Increased geo analysis spike/fade window to 12 hours for broader capture.

- Geo analysis now computes spike/fade over ±2 days using extended data and surfaces nearby macro events in the modal.

- Geo analysis distributions now support selectable windows (spike, event window, ±2d net, day), and candlestick modal falls back reliably.

- Added `scripts/ism_services_pmi_postevent_dashboard.py` to generate a standalone HTML post-event dashboard for ISM Services PMI with surprise percentile bucketing, all-vs-similar distro plots at t+10m/t+30m/t+1h/t+2h, and expected rally/selloff stats using treasury conversions.

- Added `scripts/treasury_refunding_distribution.py` to generate Treasury Refunding Announcement distribution histograms with expected rally/selloff, percentiles, and JSON/CSV outputs using `rules/treasury_price_conversion.py` conversions.

- Treasury refunding distribution plot now marks the last two events with labeled vertical lines and includes those labels in the stats box footer.

- Treasury refunding distribution script now supports non-overwriting output filenames via an auto-generated window `tag` (and optional `--tag` override).

- Treasury refunding distribution title now uses the selected event name to support non-refunding events.



## 2026-02-02

- Added `scripts/overnight_session_distribution_dashboard.py` to generate a standalone HTML dashboard for US overnight sessions (6pm→8am ET) with returns/volatility distributions, Distro-style stats boxes (mean/percentiles/expected rally/selloff/0 bps percentile), and filters for date range, year, month, and day-of-week.

- Added `scripts/ism_manufacturing_pmi_pre_post_distribution.py` to generate ISM Manufacturing PMI pre/post (t-2h/t+2h) distribution plots from 2022+ data, using `rules/treasury_price_conversion.py` conversions and emitting PNG/JSON/CSV outputs to `workspace/analysis/`.

- Added vertical line annotations for the last two event returns with date + bps labels on each ISM PMI distribution plot, and forced histogram bins to integer boundaries.

- Updated `docs/unified_dashboard_plan.md` to require histogram bins be aligned so **0 bps is always a bin boundary** (no straddling 0) in Distribution Explorer plots.

- Rewrote `docs/unified_dashboard_plan.md` as a panel-by-panel requirements spec with explicit per-panel test plans (unit mocked-data tests + composition smoke tests).

- Updated `docs/unified_dashboard_plan.md` to include **JOLTS** as a supported event type in the unified dashboard plan.

- Added `docs/pre_event_standalone_plan.md` documenting the current standalone pre-event HTML dashboard structure and edit levers.

- Expanded `docs/pre_event_standalone_plan.md` with per-panel data references and shared top-level data sources.

- Updated `docs/pre_event_standalone_plan.md` to allow an extended pre-event chart lookback window (start from 3pm T-5 up to 3pm T-1).

- Updated `docs/pre_event_standalone_plan.md` to remove the live price panel (historical-only pre-event HTML plan).

- Updated `docs/pre_event_standalone_plan.md` with non-negotiable contracts: time-series must be time-sorted before processing, and ticks/bps/decimal conversions must use `rules/treasury_price_conversion.py`.

- Updated `docs/pre_event_standalone_plan.md` to add a Week Match filter concept (select anchor events → match weeks → filter the dataset → regenerate all panels from the filtered set).

- Updated `docs/pre_event_standalone_plan.md` to require distribution stats boxes include expected rally and expected selloff.

- Implemented week match filtering hook for pre-event analysis (selected weeks filter the active event set) and swapped dashboard conversions to `rules/treasury_price_conversion.py` in `scripts/live_unified_dashboard.py`.

- Added embedded-data HTML mode for `scripts/live_unified_dashboard.py` (new `--embed` flag, no API dependency) and documented it in `io-schema.md`.

- Added pre-event-only render mode for `scripts/live_unified_dashboard.py` (new `--preevent-only` flag) and documented it in `io-schema.md`.

- Added `scripts/build_preevent_standalone_html.py` to generate the legacy standalone pre-event NFP HTML with embedded data and expected rally/selloff stats.

- Added `--min-year` filter to `scripts/build_preevent_standalone_html.py` to allow limiting events (default 2022+).

- Added percentile-based filtering for extreme consensus std dev values in `scripts/build_preevent_standalone_html.py` (defaults to P5–P95).

- Tightened default extreme filtering for consensus std dev to P10–P90.

- **Added `scripts/jolts_event_study_dashboard.py`** for JOLTS↔ISM event-study analysis:

  - Standalone HTML dashboard generator comparing JOLTS days after ISM ("with_ism") vs standalone JOLTS days.

  - Toggle pre-window lengths (30m/1h/2h/4h), JOLTS mode (with_ism/standalone/both), and unit (ticks/bps).

  - Binned analysis by ISM surprise (previous day) or JOLTS cons-std buckets (Low/Medium/High).

  - Normalized TY price paths with click-to-candlestick modal (5m candles, ±2 days).

  - Average event-time profiles per mode and distribution plots.

  - All conversions use `rules/treasury_price_conversion.py`.

  - CLI: `--symbol`, `--start-date`, `--end-date`, `--windows`, `--out`.

  - Outputs to `workspace/scenarios/jolts_event_study_dashboard_{timestamp}.html`.

- **Added `scripts/verify_jolts_event_study.py`** to sanity-check JOLTS event study data before generating the dashboard:

  - Verifies JOLTS and ISM event counts in `economic_events.db`.

  - Checks mode distribution (with_ism vs standalone).

  - Validates timestamp ordering.

  - Reports consensus_std_dev availability.

  - CLI: `--start-date`.



## 2026-02-03

- Policy events dashboard: enforced timestamp sorting before candlestick resampling to guarantee time-ordered OHLCV input for chart generation.

- Policy events dashboard: refreshed shutdown timeline narratives and modal sections (Politics/Econ/Auction/Price Action), added duration badges, and computed approx move bps from ticks.

- Policy events dashboard: shutdown timelines now use start/end interval ±5d for candlesticks, highlight the shutdown span, compute shutdown-only stats, and add duration-bucket distro filtering.

- Unified dashboard Event Analysis: component std dev inputs now render dynamically from unified config, with headline preserved and bucket mode hint updated.

- Unified dashboard Event Analysis: combo bucket labels now use component labels instead of hardcoded NFP/uer text.

- Unified dashboard Event Analysis: Pre/Post boxplot controls now support Y-axis toggle (B/E vs bps) and No-filters (all events) mode.

- Unified dashboard Event Analysis: pre-event price paths panel now supports stats-lines toggle (mean/percentiles).

- Unified dashboard Event Analysis: post-event price paths panel now supports stats-lines toggle (mean/percentiles).

- Unified dashboard Event Analysis: fixed pre-event no-filters summary templating in JS.

- Unified dashboard Event Analysis: fixed stats-lines JS template braces for pre/post price paths.

- Unified dashboard Event Analysis: fixed stats-lines trace object literals for pre/post price paths.

- Unified dashboard Event Analysis: fixed template literal escaping in component std dev input rendering.

- Unified dashboard Event Analysis: fixed post-event stats-lines object literal escaping.

- Geo analysis dashboard: added US-involved filter toggle and switched distro/summary stats to spike-window moves with auto-detected spike windows.

- Unified dashboard Event Analysis: added event comments storage helpers to back `/api/comments`.

- Unified dashboard Event Analysis: `/api/comments` now returns JSON errors instead of HTML 500 pages.

- Unified dashboard Event Analysis: `/api/comments` now catches import errors in handler.

- Unified dashboard Event Analysis: read-only comment fetch to avoid DB lock errors.

- Unified dashboard Event Analysis: added API error handler to return JSON for API exceptions.

- Unified dashboard Event Analysis: `/api/comments` now always returns JSON (even on failure).

- Unified dashboard Event Analysis: candlestick modal now sorts candles before rendering.

- Unified dashboard Event Analysis: pre-event paths now support click-to-candlestick details.

- Unified dashboard Event Analysis: post-event paths now support click-to-candlestick details.

- Unified dashboard Event Analysis: fixed pre-event event-details template escaping.

- Unified dashboard Event Analysis: distribution explorer now supports outlier toggle and bin drilldown.

- Unified dashboard Event Analysis: distribution explorer stats list matches plan (expected rally/selloff, rally rate, P5/P95).

- Unified dashboard Event Analysis: histogram bins now anchored to 0 with Scott's rule and filtered-range alignment.

- Unified dashboard Event Analysis: distro drilldown dates are clickable and select/highlight paths.

- Unified dashboard Live Stats: move up/down now use treasury conversion utilities and down moves are negative in API.

- Unified dashboard Phase 5: shared candlestick renderer added and reused in Ad-Hoc + Week Match tabs.

- Phase 6 checks: API smoke for NFP/CPI/PPI/JOLTS `/api/events` returns data for all types.

- Phase 7: added L2 caches keyed by query shape/build_version for event details, candlesticks, ad‑hoc sessions, and weekmatch endpoints.

- Phase 8: added unified dashboard API smoke tests and runbook.

- Phase 8: unified dashboard API smoke tests pass.

- Phase 3: unified dashboard now uses shared helpers for outlier filtering, B/E multipliers, and price-path normalization; helper tests expanded.

- Phase 3: helper unit tests pass (`tests/test_dashboard_helpers.py`).

- Phase 4: pre/post normalized price paths now use shared helpers in event build.

- Phase 4: added pre-event distribution explorer (outlier toggle + bin drilldown).

- Phase 4: fixed post-event distro bin template literals and pre-event distro time mapping (no JS errors on load).

- Phase 4: data loading fixed by serving cached events + event details (prices/postevent_prices now populate).

- Unified dashboard: fallback to `economic_events.consensus_std_dev` when Excel sheets are missing, so NFP loads without Z:\Meini\NFP.xlsx.

- Unified dashboard: CPI fallback to default FWD when Excel is missing; API smoke expanded for CPI/PPI/JOLTS event_details (tests pass).

- Unified dashboard: added HTML composition smoke test for key panel containers (tests pass).

- Unified dashboard: added `?smoke=1` URL flag to auto-run JS smoke checks (candlestick modal) after event load.

- Unified dashboard: verified Ad‑Hoc + Week Match candlestick rendering uses shared renderer; full duplication review pending.

- Unified dashboard: added tab HTML unit smoke tests (pre/post/adhoc/weekmatch).

- Unified dashboard: pre-event path selection now highlights the selected trace.

- Unified dashboard: consolidated shared JS helpers (smooth/percentile/KDE/interpolate) across tabs.

- Unified dashboard: added fixture-based L1 validation script + GitHub Actions CI gate.

- Unified dashboard: `?smoke=1` now validates bin selection + candlestick modal open for pre/post (non-interactive).

- Unified dashboard: centralized paths rendering for Ad‑Hoc + Week Match via shared renderer.

- Unified dashboard: Phase 5 reuse complete (shared candlestick + paths renderers; tab-specific panels remain).

- Unified dashboard: API smoke tests expanded to cover /api/adhoc_sessions_batch.

- Unified dashboard: API smoke tests expanded to cover weekmatch candidates + pricepaths.

- Unified dashboard: API smoke tests expanded to cover weekmatch candlesticks.

- Unified dashboard: `?smoke=1` now validates adhoc + weekmatch path rendering.

- Unified dashboard: `?smoke=1` now validates weekmatch candlestick rendering.

- Unified dashboard: API smoke tests expanded to cover `/api/live` (skips on timeout).

- Unified dashboard: expanded tab HTML unit smoke tests and composition smoke coverage.



## 2026-01-29

- Extended `pm_nfp_weeks_export.py` to load PPI dates from `workspace/economic_events.db` with `--from-db` and optional event/date filters, and documented new CLI inputs in `io-schema.md`.

- Added a login pause option (`--no-wait-login` to disable) so manual PricingMonkey login can be completed before scraping.

- Added `--login-wait-secs` to wait for manual login when stdin is unavailable.

- Adjusted PPI/NFP export output to keep only one row per DATE in the CSV.

- Improved numeric parsing to preserve FWD_BE/PRICE/IV when fields include non-numeric text.

- **Added PPI Core MoM dashboard support** to the unified dashboard:

  - Added `EVENT_CONFIG["PPI"]` in `scripts/event_data_loader.py` mapping to DB event name "Core PPI MoM" with default cons_std=0.1, fwd_be=8.0 bps.

  - Added PPI defaults in `get_event_defaults()` including `post_event_end_time: "17:00"` for 5pm ET post-event window.

  - Exposed PPI in `/api/event_types` alongside NFP and CPI.

  - Enhanced post-event tab with:

    - **Bucket highlighting**: Current surprise percentile bucket highlighted in green with star marker.

    - **Small sample warnings**: Buckets with <3 data points flagged with ⚠️ annotation.

    - **Bucket filtering**: Optional checkbox to show only paths in the user's bucket.

    - **Window stats recalculation**: Mean/median moves update on zoom/pan to reflect visible window.

    - **Click-to-candlestick**: Click on price path opens candlestick modal with ±2 days (expandable) and economic event annotations.

  - Added **Distribution Explorer** section at bottom of post-event tab:

    - Fine-grained time presets from t-24h to t+8.5h (5pm).

    - Histogram + KDE plot for return distribution in selected window.

    - Stats panel showing N, mean, std dev, median, P5/P95, min/max.

    - **0 bps percentile rank** highlighted in green with vertical line on chart.



## 2026-01-27

- Documented unified dashboard plan with scope, milestones, and acceptance criteria in `docs/unified_dashboard_plan.md`.

- Updated plan: UI scope is **NFP + CPI only** (shared template; event config differs). **Event Analysis** is a single tab (pre vs post chosen inside it), pre/post windows are **user-selected** (pre start time, post end time) rather than hardcoded, and **Ad‑Hoc + Week Match are event-agnostic** (no event selector, no live panel). Live price source is now planned as **Databento API** (not snapshot DB), and treasury futures conversions should use `rules/treasury_price_conversion.py`. Also includes a placeholder **Non‑Economic Events** tab with a chatbox/tag-query UX (to build later).

- Expanded unified dashboard acceptance criteria to explicitly cover price plotting, treasury formatting/transform correctness, and distribution plots + stats panels.



## 2026-01-28

- Unified dashboard: `/api/events` now returns lightweight metadata only, with `/api/event_details` added for per-event price paths/candlesticks on demand.

- UI: event detail fetch is lazy with caching; initial data load is gated behind event selection/preselection.

- Fixed JS destructuring syntax in post-event tab and aligned candlestick modal to load details lazily.

- L1 builder: writes per-event `stats.arrow` with prefix stats and normalized bps; live overlay now exposes move/breakeven fields for Databento snapshot.

- Added Windows Task Scheduler helpers to run the L1 auto-builder daily (`scripts/run_l1_auto_builder.bat`, `scripts/install_l1_builder_task.ps1`).

- Added `docs/fomc_press_conference_sentiment_plan.md` documenting FOMC sentiment Step 2: **train/calibrate the mapping** from LLM scores to 5-class sentiment using TY 1-minute price action as weak supervision, with Telegram alerts only on extremes or meaningful changes.

- Updated Step 1 defaults to **1-minute chunks** with **15-minute running score** and same/lag1m windows.

- Added `scripts/fomc_press_conference_sentiment_step2.py` for mapping calibration + 5-class labels and alerts, and wired `/fomc_alerts` in `scripts/telegram_bot.py`.

- Added `scripts/fomc_live_sentiment_alerts.py` for real-time FOMC sentiment scoring + Telegram alerts. Supports `--replay <date>` (test with historical), `--stdin` (paste text), and `--scrape` (poll StreamText live captioning at `CFI-FRB`).

- Added `scripts/yearly_overlay_timeseries.py` to generate interactive Plotly overlays of full-year OHLCV series aligned on a shared calendar axis, with optional normalization and tick/bps units.



## 2026-01-30

- Added `scripts/month_end_friday_us_open_distribution.py` to plot month-end Friday returns during US open hours (08:00–17:00 ET) with custom stats box and JSON/CSV outputs, and added optional `stats_lines` override to `plot_distribution()`.

- Expanded month-end distribution script to plot all month-end trading days with Friday markers and separate Friday stats/outputs, and added histogram overlay support in `plot_distribution()`.

- Fixed `/pre_event` bot handler by restoring its `try/except` block and HTML delivery, resolving a syntax error that broke `telegram_bot.py`.

- Updated `docs/unified_dashboard_plan.md` to require chronological candle ordering before candlestick rendering and before any candle aggregation/resampling (prevents flipped/inverse candles).

- Updated `docs/unified_dashboard_plan.md` to explicitly incorporate key UX patterns from the existing standalone dashboards (live stats panel, time-window controls, renormalize-on-zoom, bucket highlighting, distribution explorer, lazy candlestick-on-click) and to enforce them via acceptance criteria.

- Refactored `docs/unified_dashboard_plan.md` for coherence: aligned scope to include **PPI**, consolidated non-negotiable contracts and API shape, and reorganized into a single narrative (current state → contracts → architecture → UX → delivery → acceptance → ops).



## 2026-01-29

- Yearly overlay script now normalizes by default and auto-selects tick units for Treasury symbols.

- Enabled Plotly scroll zoom in yearly overlay HTML output.

- Added start/end month window selector with normalization to the selected window start.

- Fixed yearly overlay HTML to serialize x/y arrays as plain lists so window normalization uses correct values.



## 2026-01-23

- **Expanded geo_analysis.py** to use a comprehensive 21-event table covering major geopolitical events from 2022-2026 (Ukraine/Russia, Israel-Iran, Houthis/Red Sea, Hamas Oct 7, Venezuela).

- Added **US/Eastern timezone conversion** using `zoneinfo` to convert source local times (Moscow, Kiev, Jerusalem, Tehran, etc.) to ET.

- Implemented **multi-day event expansion**: Events spanning multiple days (e.g., "Night 13-14 Apr 2024") are automatically split into one entry per calendar day.

- Added **Globex date resolution**: Weekend/holiday events automatically shifted to nearest trading day with explicit labeling in UI.

- Added **per-event stats**: Day return, volatility, max up/down for 5m/1h/full day displayed on event cards and modal.

- Implemented **unusual day detection**: Events with P95+ max moves flagged with "UNUSUAL" badge for quick identification.

- Updated event cards to show source_date vs plot_date when shifted, time unknown indicator, day-of-week, and per-event bps stats.

- Enhanced modal to show detailed stats grid and event window timing info.



## 2026-01-26

- Added `tinkering/nfp_similarity_timeseries.py` to test whether **lagged prints + seasonality** improve matching of **pre-event drift** price paths into NFP vs baseline (month + NFP/UR/AHE cons_std). Generates `workspace/analysis/nfp_similarity_timeseries.html` and `workspace/analysis/nfp_similarity_timeseries_features.csv`.

- Added `data_setup/bbg_regime_feature_exporter.py` to generate a Bloomberg-Excel workbook (BDH-based) that exports daily regime series and maps them to all events from `workspace/economic_events.db`, writing artifacts to `Z:/Meini/`.



## 2026-01-22

- **FIXED: Extreme BPS spikes in price paths** - Root cause was ~110k erroneous near-zero prices (0.05-0.06) in OHLCV database that shouldn't exist for TY futures. Added `TY_PRICE_MIN=80` / `TY_PRICE_MAX=150` filter constants and `is_valid_ty_price()` function. All data fetching queries now filter at SQL level with `close >= TY_PRICE_MIN AND close <= TY_PRICE_MAX`.

- Added `scripts/diagnose_spikes.py` diagnostic tool to analyze price data quality and detect anomalies.

- Fixed ad-hoc dashboard sessions to use selected date as session end (6pm prior day → 5pm selected day), so weekdays like Monday resolve correctly.

- Guarded ad-hoc trace debug logging against Date/string x-values to prevent render errors.

- Added full-session fallback when the default 6am–2pm window has no data so ad-hoc paths still render.

- January auction HTML: distro-style histograms (normalized + KDE overlay) and flexible hour input with nearest-key resolution.



## 2026-01-20

- Updated dashboard server logging to show reachable URLs when binding to 0.0.0.0.

- Unified dashboard: URL suffix preloads only requested event, client applies config defaults from `/api/config`.

- Unified dashboard: NFP now uses fallback FWD and fixes other-events query date column.

- Live server: reference price init now runs in background to unblock startup.

- Added URL-suffix event preloading and cache warmup for unified dashboard.



## 2026-01-21

- Added `scripts/derive_auction_fields.py` to derive auction fields and support Nomura-style labels, plus Memory Bank updates.

- Added `scripts/auction_supply_return_analysis.py` to analyze 20Y Treasury auction fields vs TY returns at multiple horizons (2h–24h). Includes OLS regression, quantile bucketing, rally/selloff analysis, and distribution plots.

- Added `data_setup/create_bbg_event_fields_excel.py` to generate Bloomberg Excel workbooks with BDH formulas for event fields, grouping subcomponents per sheet.

- Added bad-tick filtering for Week Match price paths and candlesticks (drop near-zero/negative prices and large 1-minute jumps) to prevent unrealistic bps spikes.

- Switched Week Match price paths to use `Plotly.react` without render guards to prevent charts disappearing when multiple weeks are selected.

- Added tiered candidate pruning for Week Match (Tier 1→4 ordered anchors) to speed up matching while allowing partial matches per tier.

- Enabled dynamic normalization to visible window on zoom/scroll by restyling traces instead of full replot.
