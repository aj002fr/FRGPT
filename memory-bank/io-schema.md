# I/O Schema



## Overview



Complete I/O contracts for the code-mode MCP system.

## V1 six-state banner spec (`docs/aggression_v1_monitor.tex`) — discussion only, not live 5050

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|-------|------|------------------------|-------------|
| beta_ref | Spec | float | live V3: mean of last 10 session OLS of ΔP_k on ΔA_k over last 3h TOD; yield bp per $1m; later day-tagged | `a = beta_ref * (A_{k(t)} - A_{k(t-W)}) / tau` |
| BETA_REF_LOOKBACK_HOURS | Constant | int | 3 | Last-3h window ending at displayed ET clock on each completed session |
| BETA_VERSION | Constant | str | last_3h_tod_v1 | Entry identity in `aggression_v3_live/beta_ref.json` |
| beta_ref.json.entries | Internal | dict[str, dict] | one payload per (version, price, symbols, dv01, sessions, lookback) | TY-only and curve betas coexist; peek ignores tod_bucket |
| ensure_beta_ref.compute | Input | bool | default True; False returns last-good or `still computing` | Live `/api/aggression` peeks; warmup thread computes |
| ensure_beta_ref.refresh_stale | Input | bool | default False; with `compute=True` recomputes a stale TOD bucket; a failed refresh returns last-good unchanged | V3 `_kick_beta_ref` passes True only on the first kick per chip set per process |
| ensure_beta_ref.bucket_stale | Output | bool | True when last-good tod_bucket ≠ current 5-min ET bucket | Rebuild still kicks; held chip sets return the cached entry and do not re-trigger a rebuild |
| _kick_beta_ref | Internal | (price_symbol, aggression_symbols, dv01_adjust, trades_window=None) -> Thread \| None | None when a compute for the key is already running | `offer_history()` only after a fresh OK beta; key then joins `_BETA_HELD_KEYS` (beta held until restart) |
| CATCH_UP_BATCH | Constant | int | 2000 | V3 `_catch_up_live` applies newer live prints in batches this size so the lock is released between batches |
| LIVE_OVERLAY_MIN_WINDOW | Constant | timedelta | 30 min | V3 `_live_display_trades` uses `LiveOverlayCache` only for windows at least this long (short catch-up windows use plain `apply_live`) |
| MATCH_WINDOW | Constant | Timedelta | 1ms | Live/hist one-to-one match window in `print_reconcile.py` |
| REBUILD_MIN_INTERVAL_SEC | Constant | float | 60.0 | V3 publisher: minimum gap between rebuild starts once a snapshot exists; offers inside it fold into one rebuild |
| V3_GOLDEN_DIR | EnvVar | path | default `C:\dev\mdp_golden\v3\20260923` | `tests/test_v3_reconcile_golden.py` fixture dir (hist_/live_*.parquet, expected_*.npz, manifest.json); golden tests skip if absent |
| WARMING_REBUILD_MIN_INTERVAL_SEC | Constant | float | 5.0 | V3 publisher: minimum gap between rebuilds while no snapshot exists yet |
| MATCH_RATE_FLOOR | Constant | float | 0.5 | Skip unmatched interior hist if match rate is below this |
| q | Spec | float | 1 tick | Quiet if `|d|<=q` and `max(|a|,|b|)<=q` |
| r | Spec | float | 1.5 | Dominant if one abs contribution `>= r` times the other |
| W | Spec | int | 15 minutes wall time | Endpoints are last trades at t and t−15m; increments are trade-to-trade |
| clock | Spec | trade-to-trade | last-trade P; ΔA and ΔP from print k−1 to k | All plots and calculations |
| d, a, b | Spec | float ticks | finite; `d = a + b` | Last-trade price / aggression / implied-passive contributions |
| READY headlines | Spec | str | QUIET, BALANCED, AGGRESS DOMINANT, IMPLIED PASSIVE DOMINANT, JOINTLY DRIVEN, CONTESTED | Six mutually exclusive states when `d=a+b` |

## LIFO-zone research (`scripts/aggression_v2_lifo_zone_research.py`)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| FEATURE_MATRIX | Input | Path | published TBBO matrix, read-only | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |
| OUTPUT_DIR | Output | Path | isolated analysis dir | `workspace/analysis/aggression_v2_lifo_zone/` |
| PRIMARY_H | Constant | int | 15 | Primary forward window (minutes) |
| MEAT_TICKS | Constant | float | 3.0 | Minimum high–low / first-push / retrace |
| CONTAIN_TICKS | Constant | float | 8.0 | Max \|net\| for a zone |
| ADVERSE_TICKS | Constant | float | 2.0 | Path / fade stop |
| lifo_zone_{h}m | Output | 0/1 \| NaN | meat ∧ contained ∧ two-way; NaN if incomplete | Forward LIFO box label |
| h5 | Internal | bool | \|v1 z\|≥1 and intensity ≤ session p33 | Frozen live-style LIFO flag |

## LIFO literature research (`scripts/aggression_v2_lifo_literature_research.py`)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| FEATURE_MATRIX | Input | Path | published TBBO matrix, read-only | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |
| LIFO_LIT_OUTPUT | Output | Path | isolated analysis dir | `workspace/analysis/aggression_v2_lifo_literature/` |
| mod_twosided | Internal | bool | mid intensity tercile ∧ trailing-15m \(\nu\le p_{33}\) | L1 primary flag |
| nu_15 | Internal | float \| NaN | \(\lvert\sum \mathrm{agg}\\_1m\rvert / \sum\lvert\mathrm{agg}\\_1m\rvert\) over 15m | 0 = two-sided, 1 = one-sided |
| absorb_extreme | Internal | bool | high \|agg_1m\| ∧ \|ΔP\|≤1 tick ∧ trail meat ∧ last push | L2 primary flag |
| metaorder_run | Internal | bool | sign-run length of agg_1m ≥ 10 | L3 veto |
| flippy_active | Internal | bool | run in {1,2} ∧ intensity > session p33 | L3 zone hunt |
| V_STAR | Constant | int | 2000 | Discovery-only volume-clock bucket (contracts) |
| MIN_LIFT_PP | Constant | float | 8.0 | Validation letter |
| MIN_N | Constant | int | 50 | Validation letter / L2 edge |
| track_p.same_bar_r2 | Output | float | OLS R² of ΔP ticks on agg_1m | Diagnostic; not a letter |
| track_p.lead1_r2 | Output | float | OLS R² of ΔP ticks on lag-1 agg_1m | Should collapse vs same-bar |
| fwd_twosided | Internal | bool | next-15m ν ≤ session p33 of ν | Track F persistence |
| LIFO_LIT_SHIP_GATE | Constant | bool | false | Never a live banner |

## LIFO flow-forecast diagnostic (`scripts/aggression_v2_lifo_flow_forecast_research.py`)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| FEATURE_MATRIX | Input | Path | published TBBO matrix, read-only | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |
| OUTPUT_DIR | Output | Path | isolated analysis dir | `workspace/analysis/aggression_v2_lifo_flow_forecast/` |
| PAST_W | Constant | tuple[int] | 1, 5, 15, 30 | Trailing signed-agg sums through t |
| FUTURE_H | Constant | tuple[int] | 1, 5, 15 | Strictly forward sums / endpoint ticks after t |
| past_flow_{W} | Internal | float \| NaN | sum agg_1m over last W minutes | Stage-1 feature |
| fwd_flow_{H} | Internal | float \| NaN | sum agg_1m over next H minutes | Stage-1 target |
| fwd_dp_{H} | Internal | float \| NaN | (price_{t+H} − price_t) / tick | Stage-2 target |
| flow_forecast_r2 | Output | float | OOS 1 − SSE/SST; can be negative | Discovery-fit F̂ vs actual future flow |
| direct_forecast_r2 | Output | float | OOS; can be negative | Discovery-fit ΔP ~ past flow |
| twostage_forecast_r2 | Output | float | OOS; can be negative | Same-bar λ × F̂ |
| oracle_r2 | Output | float | in-split OLS R² | ΔP ~ *actual* future flow (not a forecast) |
| SHIP_GATE | Constant | bool | false | Never a live banner |

## LIFO path follow-up (`scripts/aggression_v2_lifo_path_followup_research.py`)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| OUTPUT_DIR | Output | Path | isolated analysis dir | `workspace/analysis/aggression_v2_lifo_path_followup/` |
| trail_meat_15m | Internal | bool | trailing 15-bar high–low ≥ 3 ticks | Frozen swinging-tape flag |
| trail_zone_15m | Internal | bool | last 15m satisfies Branch-0 zone | Range-persistence feature |
| us_morning | Internal | bool | ET hour in [8, 12) | Session bucket |
| post_event_30_90 | Internal | bool | 30 < minutes after last US event ≤ 90 | Event shoulder |
| run_just_ended | Internal | bool | prior sign-run ≥ 10 and now < 10 | First bar after a metaorder run |
| last_push_3t | Internal | bool | \|mom_5m\| ≥ 3 ticks | Fade-clock universe |
| SHIP_GATE | Constant | bool | false | Never a live banner |

---

## Event Parent/Component Mapping

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| event_parent_components.CONFIG_PATH | Constant | Path | `config/event_parent_components.json` | Source of the parent->component mapping |
| event_parent_components.json source | Constant | Path | `Z:\Aastha\US Eco Events.xlsx` Sheet1, `EVENT_NAME` column | Authoritative event_name universe |
| load_event_parent_components() | Output | dict | `{version, source, description, field_notes, parents}` | `cfg = load_event_parent_components()` |
| get_parents() | Output | list[str] | 58 parent keys (`NFP`, `CPI`, `JOLTS`, `FOMC`, ...) | `get_parents()` |
| get_components(parent) | Input/Output | list[dict] | parent key, case-insensitive | `get_components("NFP")` -> 11 components |
| get_parent_of(event_name) | Input/Output | str \| None | component `event_name`, case-insensitive | `get_parent_of("Unemployment Rate")` -> `"NFP"` |
| get_headline_component(parent) | Output | dict \| None | component flagged `headline` (else first) | `get_headline_component("CPI")` -> CPI MoM |
| iter_components() | Output | list[dict] | 147 flattened components w/ `parent`/`category` | `iter_components()` |

---

## Real-Time Iran Headlines Listener

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| CHAT_ID | Constant | int | Telegram chat id for the Iran/geopolitics distribution chat | `-4611662021` |
| TELEGRAM_API_ID | Constant | int | Telegram API app id used by Telethon | `33915885` |
| TELEGRAM_API_HASH | Constant | str | Telegram API hash used by Telethon | Used by `scripts/iran_headlines_stream.py` |
| SESSION_PATH | Constant | Path | Telethon session path under `Path.home() / ".market_data"` | `~/.market_data/telegram_scraper_session` |
| iran_headlines.DB_PATH | Constant | Path | SQLite DB for headlines, Hormuz flows, and oil trades | `workspace/iran_headlines.db` |
| iran_headlines.headlines | Output | SQLite table | Columns: `id`, `timestamp`, `text`, `source`, `telegram_message_id`; unique `(timestamp, text)` | Written by batch and stream scripts |
| iran_headlines_stream.--db | Input | Path | Existing or creatable SQLite path | `python scripts/iran_headlines_stream.py --db workspace/iran_headlines.db` |
| iran_headlines_stream.--catch-up-only | Input | bool | Run startup catch-up then exit before live listening | Validation smoke mode |
| iran_headlines_stream.--heartbeat-seconds | Input | int | `>= 1`, default `30` | `--heartbeat-seconds 15` |
| iran_headlines_stream.--reconnect-seconds | Input | int | `>= 1`, default `15` | Delay after hard listener failure |
| iran_headlines_stream.--verbose | Input | bool | Debug logging | `--verbose` |
| iran_headlines_stream.lock | Internal | Lock file | Advisory single-instance lock held for process lifetime | `%LOCALAPPDATA%\market_data_puller\status\iran_headlines_stream.lock` |
| iran_headlines_stream.heartbeat | Output | Text marker | UTC timestamp + pid; refreshed every heartbeat interval | `%LOCALAPPDATA%\market_data_puller\status\iran_headlines_stream_heartbeat.txt` |
| iran_headlines_stream.last_message | Output | Text marker | Last inserted live message timestamp/id/source | `%LOCALAPPDATA%\market_data_puller\status\iran_headlines_stream_last_message.txt` |
| iran_headlines_stream.logs | Output | Log files | Local primary log plus best-effort shared workspace log | `%LOCALAPPDATA%\market_data_puller\logs\iran_headlines_stream.log`, `workspace/logs/iran_headlines_stream.log` |
| IranHeadlinesStream | Internal | Windows scheduled task | At user logon, long-running, restart on failure, single instance | Created by `scripts/install_iran_headlines_task.ps1` |
| install_iran_headlines_task.ps1.-TaskPassword | Input | str | Optional password for password-backed task registration | `powershell -File scripts/install_iran_headlines_task.ps1 -TaskPassword ...` |


## Macro Path Navigator

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| MACRO_PATH_PORT | EnvVar | int | TCP port for the live dashboard, default `5088` (browser-safe; avoids Chromium unsafe SIP ports 5060/5061) | `set MACRO_PATH_PORT=5089` |
| macro_path.app.--host | Input | str | Flask bind host, default `127.0.0.1` | `python scripts/macro_path_navigator/app.py --host 127.0.0.1` |
| macro_path.app.--port | Input | int | `1..65535`, default `5088` | `--port 5088` |
| /api/cycle | Output | JSON object | `{cycle, events, counts}` for current inter-FOMC window | Browser runway event rail |
| /api/runway | Output | JSON object | Live price snapshot, TY TBBO rows, current price, last update | Browser runway chart |
| /api/price_history.window | Input | str | One of `1D`, `5D`, `1M`, `3M`, `6M`, `1Y`, `All`; default `1M` | `/api/price_history?window=3M` |
| /api/price_history.interval | Input | str \| None | Optional pandas resample interval; defaults by window (`1D->1min`, `5D->5min`, `1M->30min`, `3M->1h`, `6M/1Y->1D`, `All->1W`) | `/api/price_history?window=1M&interval=15min` |
| /api/price_history | Output | JSON object | `{window, interval, coverage_end, rows:[{t,o,h,l,c,v}], row_count}` anchored to PostgreSQL `ohlcv_1m` max timestamp | Historical TY candlestick panel |
| /api/headlines.start | Input | ISO datetime str | Inclusive UTC/offset timestamp | `/api/headlines?start=2026-06-02T00:00:00Z` |
| /api/headlines.end | Input | ISO datetime str | Inclusive UTC/offset timestamp; must be after `start` | `&end=2026-06-03T23:59:00Z` |
| /api/headlines.limit | Input | int | `>= 1`, default `500`; applied before high-signal source filtering | `&limit=2000` |
| /api/headlines.bucket | Input | str \| None | Optional pandas time bucket such as `1h`, `6h`, `1D`, `1W`; returns clustered marker rows | `&bucket=6h` |
| /api/headlines | Output | JSON object | `{rows:[{ts,source,text,telegram_message_id}], clusters:[{ts,count,samples}], filtered_sources}`; drops `discussion`/`other` | Chart annotation rail |
| /api/events_range.start | Input | ISO datetime str | Inclusive timestamp for event-range annotation fetch | `/api/events_range?start=2026-06-02T00:00:00Z` |
| /api/events_range.end | Input | ISO datetime str | Exclusive-ish end passed through event loader | `&end=2026-06-03T23:59:00Z` |
| /api/events_range | Output | JSON object | `{events:[...]}` using the same event shape as `/api/cycle` | Historical chart event rail |
| /api/signals | Output | JSON object | `{generated_at, signals:[{kind,severity,title,summary,confidence,timestamp,source,reasons,assumption}], flow_context, headline_window, counts}` | Signal/watch cards above charts |
| /api/distribution.event_id | Input | str | Upcoming or bbg event id; alternatively pass `event_name` | `/api/distribution?event_id=upcoming:NFP:...` |
| /api/distribution.event_name | Input | str | Canonical or sched-calendar event name | `/api/distribution?event_name=NFP` |
| /api/distribution.window | Input | str | `2h` or `eod`, default `2h` | `&window=eod` |
| /api/distribution.scenario | Input | str | `baseline`, `hot`, `inline`, `cool`; scenario filtering requires z-scores | `&scenario=hot` |
| /api/distribution | Output | JSON object | `{event_name, values_bps, stats, n, n_eff, conditioning, surprise_statuses, projected_price, current_price}` | Event distribution panel |
| /api/distributions.window | Input | str | `2h` or `eod`, default `2h` | `/api/distributions?window=2h` |
| /api/distributions.scenario | Input | str | `baseline`, `hot`, `inline`, `cool`, default baseline | `&scenario=baseline` |
| /api/distributions.limit | Input | int | Number of upcoming distribution events, default `6` | `&limit=6` |
| /api/distributions | Output | JSON object | `{current_price, window, scenario, events:[{id,name,canonical_name,ts,stats,n,n_eff,projected_price}]}` | Projected percentile bands on runway chart |
| /api/adhoc_events | Input/Output | GET returns list; POST JSON creates one event | POST requires `ts` or `datetime`; optional `name`, `type`, `theme`, prior fields | Add "US-Iran meeting tomorrow 2pm" |
| /api/speaker_lean | Output | JSON object | FOMC speaker lean config payload | Reads `config/fomc_speaker_lean.json` |
| /api/stream | Output | text/event-stream | SSE JSON messages with `health` and `runway` | Browser live updates |
| /api/health | Output | JSON object | Consolidated `ok/status/feeds/state/attribution/last_error` | `scripts/check_macro_path_health.py` |
| /api/health/feeds | Output | JSON object | Feed statuses keyed by `ty_price`, `order_flow`, `iran_headlines` | Health strip |
| /api/health/state | Output | JSON object | Last dashboard/cycle/runway/cone state timestamps | Monitor fallback context |
| /api/health/attribution | Output | JSON object | Attribution engine status/open queue fields (initially scaffolded) | Future late-binding monitor |
| config/fomc_speaker_lean.json | Constant | JSON object | `members` mapping name -> `{lean, voter, role, confidence}` | FOMC speaker event scenario skew |
| workspace/state/macro_path_navigator/adhoc_events.json | Output | JSON list | User-added event rows; id/name/ts/type/theme/prior fields | Written by `/api/adhoc_events` POST |
| macro_path.health.markers | Output | JSON marker files | `macro_path_*.txt` under `%LOCALAPPDATA%\market_data_puller\status` | Used by health monitor if HTTP is down |
| check_macro_path_health.--url | Input | URL str | Health endpoint URL, default `http://127.0.0.1:5088/api/health` | `python scripts/check_macro_path_health.py --url ...` |
| check_macro_path_health.--loop | Input | bool | Poll repeatedly instead of once | Health monitor daemon mode |
| check_macro_path_health.--interval | Input | int | Seconds between checks, default `60` | `--loop --interval 30` |
| check_macro_path_health.--dry-run | Input | bool | Print alert text instead of sending Telegram | Smoke validation |
| run_macro_path_navigator.bat | Internal | Windows wrapper | Starts dashboard on `127.0.0.1:5060`; logs locally | Double-click or SOD launcher |
| run_macro_path_health_monitor.bat | Internal | Windows wrapper | Starts monitor loop against local dashboard | Separate monitor process |



## Unified Live Event Dashboard Refresh



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| default_start_year | Constant | int | Year cutoff for the dashboard event regime; events before `YYYY-01-01` are dropped | `2023` in `config/unified_dashboard_event_config.json` |

| outlier_rule_default | Constant | str | cons_std percentile band kept by the regime filter, form `p<lo>_p<hi>` | `p5_p95` (keeps P5-P95) |

| build_dashboard_data.apply_regime_filter | Input | bool | When True, restrict events to `default_start_year`+ and trim cons_std to the percentile band | `build_dashboard_data(rows, event_type="NFP")` (default True) |

| filter_events_to_regime | Internal | (events, event_type) -> list[dict] | Drops pre-regime dates and cons_std outliers using the unified config | Used at the top of `build_dashboard_data` |

| DATABENTO_API_KEY | EnvVar | str | Non-empty Databento historical/live key; aggression backup resolves environment, then `config/keys.env`, then `Z:\Aarsh\Data\AccessAPI\.env` | Used by backfill and `DatabentoLiveBackup` |

| backfill_ty_from_databento.--dates | Input | list[str] | One or more event dates, `YYYY-MM-DD` | `python scripts/backfill_ty_from_databento.py --dates 2026-05-08` |

| backfill_ty_from_databento.--dry-run | Input | bool | Fetch/validate only, no PG insert | `--dry-run` |

| backfill_ty_from_databento.--env-path | Input | Path | Existing `.env` file with `DATABENTO_API_KEY` | `--env-path Z:\Aarsh\Data\AccessAPI\.env` |

| backfill_ty_from_databento.--raw-symbol | Input | str | Raw ZN contract such as `ZNM6`, or `auto` | `--raw-symbol auto` |

| refresh_dashboard_data.--events | Input | str | Comma-separated event types from `NFP,CPI,PPI,JOLTS` | `--events NFP,CPI` |

| refresh_dashboard_data.--force-l1 | Input | bool | Rebuild existing L1 files instead of missing-only | `python scripts/refresh_dashboard_data.py --force-l1` |

| refresh_dashboard_data.--skip-* | Input | bool | Skip staleness, backfill, L1, or cache stages | `--skip-backfill --skip-cache` |

| refresh_dashboard_data.L1_files | Output | Arrow/JSON files | Per event date under `data/event_features/<EVENT>/` | `2026-05-08.price_1m.arrow`, `2026-05-08.meta.json` |

| UnifiedDashboardRefreshNightly | Internal | Windows scheduled task | Daily at `02:30` local by installer default | Created by `scripts/install_dashboard_refresh_task.ps1` |

| UnifiedDashboardRefreshPostRelease | Internal | Windows scheduled task | Weekdays at `09:15` local by installer default | Created by `scripts/install_dashboard_refresh_task.ps1` |

| /api/impulse/ecos | Input | multipart/form-data | `file` upload (`.xlsx`) or `use_default=1`; parses Bloomberg ECOS estimate export | `POST /api/impulse/ecos` |

| /api/impulse/history.event_type | Input | str | Event type, currently most useful for `NFP`; uses regime-filtered cache/L1 files | `/api/impulse/history?event_type=NFP` |

| /api/impulse/history.start_day | Input | int | `-4..-1`, default `-2`; start day relative to event day | `start_day=-2` |

| /api/impulse/history.start_hhmm | Input | str | `HH:MM`, default `15:00` ET | `start_hhmm=15:00` |

| /api/impulse/scenarios | Input | JSON object | Requires `consensus`, positive `std`, and `actuals: [{label, actual}]`; optional `bucket_width`, `start_day`, `start_hhmm` | `POST /api/impulse/scenarios` |

| impulse.ecos.summary | Output | JSON object | Prior, median/avg/high/low, std dev, expected change, percentile, weighted consensus | `{"prior":115,"median":85,"expected_change":-30}` |

| impulse.history.records | Output | list[dict] | Per-event expected change, prior, consensus, drift bps, and normalized path | Used by `impulse_tab.py` charts |

| impulse.scenarios.rows | Output | list[dict] | Hypothetical actuals with `z`, empirical surprise percentile, bucket, pre/+2h/EOD expected signed bps and max-extent bps | Fills the What-if Actuals table |



---



## Treasury Auction Data Puller



### Config + Runtime Contract



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| AUCTION_ALERT_CHAT_ID | EnvVar | str | Optional Telegram channel username or single chat ID; falls back to `TELEGRAM_CHAT_ID` | `set AUCTION_ALERT_CHAT_ID=@auction_alerts` |

| AUCTION_PULLER_STATE_PATH | Constant | Path | Writable JSON file path | `workspace/state/auction_puller_state.json` |

| AUCTION_TERM_MAP | Constant | tuple[AuctionTermConfig, ...] | Coupon terms: 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y | `Bond / 30-Year -> auction_data/30y_note_raw.csv` |

| AUCTIONS_QUERY_PATH | Constant | str | FiscalData auction endpoint path | `/v1/accounting/od/auctions_query` |

| FISCAL_DATA_BASE_URL | Constant | str | HTTPS FiscalData API base URL | `https://api.fiscaldata.treasury.gov/services/api/fiscal_service` |

| auction_puller.--backfill | Input | bool | Full-history pull; seeds CSV/state without historical alerts unless `--send-alerts` is set | `python scripts/auction_data_puller.py --backfill` |

| auction_puller.--days-back | Input | int | `>= 1`, default `14` | `--days-back 30` |

| auction_puller.--dry-run | Input | bool | Logs intended writes/alerts without file or Telegram side effects | `--dry-run` |

| auction_puller.--fast-interval | Input | int | `>= 1`, default `10` seconds; used during the post-auction result window | `--loop --fast-interval 10` |

| auction_puller.--interval | Input | int | `>= 1`, default `60` seconds; fallback interval if `economic_events.db` is unavailable or has no auction schedule | `--loop --interval 60` |

| auction_puller.--loop | Input | bool | Event-aware daemon mode. Reads `workspace/economic_events.db`, sleeps outside auction windows, polls every 30s from T-10m to T, and every `--fast-interval` from T to T+30m or until result capture. | `python scripts/auction_data_puller.py --loop` |

| auction_puller.--once | Input | bool | Single poll then exit; default mode when neither `--once` nor `--loop` is provided | `python scripts/auction_data_puller.py --once` |

| auction_puller.loop_pre_event_interval | Internal | int | Fixed `30` seconds | Poll cadence from 10 minutes before auction time until event time |

| auction_puller.loop_result_window | Internal | int | Fixed `30` minutes after auction time | Stop fast polling after tolerance expires if no result arrives |

| auction_puller.loop_sleep_cap | Internal | int | Fixed `300` seconds | Max idle sleep chunk so daemon stays responsive |

| auction_puller.csv_raw | Output | CSV | Treasury Securities export-style header; keyed by unique CUSIP | `auction_data/10y_note_raw.csv` |

| auction_puller.csv_processed | Output | CSV | Nomura-style derived columns from `process_auction_files.py` | `auction_data/10y_note_processed.csv` |

| auction_puller.state | Output | JSON object | `{cusip: alerted_at_iso}` | `{"91282CQQ7": "2026-05-27T14:50:00+00:00"}` |

| auction_puller.telegram_message | Output | HTML str | Stop/Bid-Cover/Yld Gap plus Dealer/Direct/Indirect take and hit rates | Sent by `send_telegram_message(..., parse_mode="HTML")` |



---



## Redis Event Stream Service



### Config + Runtime Contract



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| EVENT_PUBLISHER_POLL_SECONDS | Constant | int | `>= 1`, default `30` | Poll DB deltas every 30s |

| EVENT_RETENTION_DAYS | Constant | int | `>= 1`, default `14` | Purge/archive stale rows older than 14d |

| EVENT_ROWCOUNT_CRITICAL_THRESHOLD | Constant | int | `> EVENT_ROWCOUNT_WARN_THRESHOLD`, default `500000` | Critical table-growth alert |

| EVENT_ROWCOUNT_WARN_THRESHOLD | Constant | int | `>= 1`, default `200000` | Warning table-growth alert |

| EVENT_UPSTREAM_EXCEL_DATE_PATTERN | Constant | regex | Weekly filename pattern `M.D -M.D.xlsx` with optional whitespace before `.xlsx` | `^\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}\s*\.xlsx$` |

| EVENT_UPSTREAM_EXCEL_DIR | Constant | str | Existing directory containing weekly calendar workbook(s) | `//100.95.155.81/FRGMSharedSpace/Meini` |

| EVENT_UPSTREAM_EXCEL_PATH | Constant | str | Existing Excel path | `//100.95.155.81/FRGMSharedSpace/Meini/3.23 - 4.3 .xlsx` |

| resolve_event_upstream_excel_path | Internal | () -> str | Returns most recently modified matching weekly `.xlsx`; falls back to `EVENT_UPSTREAM_EXCEL_PATH` | Used by Redis/Postgres ingestor and SQLite calendar ingestor |

| REDIS_DB | Constant | int | `>= 0`, default `0` | Redis logical DB |

| REDIS_DEDUPE_PREFIX | Constant | str | Non-empty string prefix | `market:events:dedupe:` |

| REDIS_DEDUPE_TTL_SECONDS | Constant | int | `>= 1`, default `86400` | Dedupe key TTL |

| REDIS_EVENT_STREAM | Constant | str | Redis stream key | `market:events` |

| REDIS_HOST | Constant | str | Hostname or IP | `localhost` |

| REDIS_PASSWORD | Constant | str \| None | Redis auth secret or `None` | `None` |

| REDIS_PORT | Constant | int | `1..65535`, default `6379` | Redis TCP port |

| SCHEDULED_EVENT_ALERTS_ARCHIVE_TABLE | Constant | str | Valid Postgres identifier | `scheduled_event_alerts_archive` |

| SCHEDULED_EVENT_ALERTS_TABLE | Constant | str | Valid Postgres identifier | `scheduled_event_alerts` |

| service.alert_time_et | Internal | datetime | `scheduled_time_et - 10 minutes` | Used for APScheduler trigger |

| service.event_id | Internal | str | SHA1(`event_name|scheduled_time_et|source_file`) | `56f...` |

| service.last_watermark | Internal | datetime | Monotonic `updated_at` cursor | Stored in workspace watermark file |



### Redis Environment Overrides



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| BBG_ALERT_CHAT_ID | EnvVar | str | Telegram channel username or single chat ID for stale BBG alerts | `set BBG_ALERT_CHAT_ID=@company_bbg_alerts` |

| REDIS_HOST | EnvVar | str | Redis hostname/IP | `set REDIS_HOST=100.82.143.79` |

| REDIS_PORT | EnvVar | int | `1..65535` | `set REDIS_PORT=6379` |

| REDIS_DB | EnvVar | int | `>= 0` | `set REDIS_DB=0` |

| REDIS_PASSWORD | EnvVar | str \| empty | Redis password; empty means no auth | `set REDIS_PASSWORD=secret` |



### Postgres Row Shape (`scheduled_event_alerts`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| event_id | Input | str | Deterministic unique ID (PK) | `sha1(...)` |

| event_name | Input | str | Excel event name or session marker (`tokyo_open`, `us_close`, etc.) | `CPI JAN` |

| country | Input | str | Country/region code text | `US`, `JP`, `GB` |

| scheduled_time_et | Input | datetime (tz-aware) | ET timestamp | `2026-02-24T08:30:00-05:00` |

| source_file | Input | str | Excel filename or service source marker | `2.23 -3.6.xlsx` |

| is_active | Input | bool | `true` / `false` | Soft delete marker |

| updated_at | Output | datetime (tz-aware) | Auto-updated on write | Watermark polling field |



### Redis Message Shape (`market:events`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| event_id | Output | str | Deterministic ID from DB row | `56f...` |

| event_name | Output | str | Calendar or session event name | `tokyo_open` |

| country | Output | str | Country/region code | `US` |

| scheduled_time_et | Output | str (ISO datetime) | ISO-8601 ET timestamp | `2026-02-24T08:30:00-05:00` |

| source_file | Output | str | Upstream source identifier | `2.23 -3.6.xlsx` |



---



## US Holiday Candlestick Dashboard



### scripts/us_holiday_candlestick_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| holiday_dash.start_year | Input | int | `>= 1900`, default `2022` | `--start-year 2022` |

| holiday_dash.end_year | Input | int | `>= start_year`, default current year | `--end-year 2026` |

| holiday_dash.symbol | Input | str | OHLCV symbol present in PostgreSQL `ohlcv_1m` | `--symbol TY` |

| holiday_dash.days | Input | int | `>= 1`, window size on each side of holiday | `--days 3` |

| holiday_dash.output | Output | Path | Writable HTML file path | `--output workspace/scenarios/us_holiday_candlestick_dashboard.html` |



### Holiday Dashboard Derived Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| holiday_dash.holiday_name | Output | str | Fixed holiday set (`New Year's Day`, `Martin Luther King, Jr. Day`, `Washington's Birthday`, `Good Friday`, `Memorial Day`, `Juneteenth National Independence Day`, `Independence Day`, `Labor Day`, `Thanksgiving Day`, `Christmas Day`) | Rendered in holiday table |

| holiday_dash.actual_date | Output | str | ISO date (`YYYY-MM-DD`) | `2026-07-04` |

| holiday_dash.observed_date | Output | str | ISO date after weekend-observed adjustment for fixed-date holidays | `2026-07-03` |

| holiday_dash.candles | Output | dict | 5-minute OHLC arrays (`x`, `open`, `high`, `low`, `close`) over holiday window | Used by Plotly candlestick |

| holiday_dash.window_start | Internal | datetime | `observed_date - days` at `00:00` | Query lower bound |

| holiday_dash.window_end | Internal | datetime | `observed_date + days + 1` at `00:00` (exclusive) | Query upper bound |

| holiday_dash.note | Output | str | Empty or observed-date note text when actual and observed differ | `Observed on Friday, July 3` |



---



## CPI Two-Factor PCA Dashboard



### scripts/cpi_twofactor_pca_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| cpi_pca.excel | Input | Path | Existing Bloomberg workbook path (falls back to Y:/Meini or Z:/Meini defaults) | `--excel Y:/Meini/bbg_event_fields.xlsx` |

| cpi_pca.db | Input | Path | Existing SQLite DB path | `--db workspace/economic_events.db` |

| cpi_pca.symbol | Input | str | Futures prefix | `--symbol TY` |

| cpi_pca.start_date | Input | str | `YYYY-MM-DD`, default `2022-01-01` | `--start-date 2022-01-01` |

| cpi_pca.trend_window | Input | int | `>= 8` recommended | `--trend-window 36` |

| cpi_pca.bucket_count | Input | int | `>= 2` (default 5) | `--bucket-count 5` |

| cpi_pca.output | Output | Path | Optional HTML path; default in `workspace/scenarios/` | `--output workspace/scenarios/cpi_twofactor_pca_dashboard.html` |



### CPI Two-Factor Derived Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| cpi_pca.pre_features | Internal | list[float] | `[mom_cons_std, core_mom_cons_std]`, std dev > 0 | Pre-event PCA input vector |

| cpi_pca.pre_mkt_features | Internal | list[float] | `[trail_range_bps, trail_vol_bps]` computed strictly before `3:00pm T-1` | Pre-event market-derived uncertainty vector |

| cpi_pca.post_features | Internal | list[float] | `[mom_z_adj, core_mom_z_adj]` after trend adjustment | Post-event PCA input vector |

| cpi_pca.mom_expected | Internal | float | Rolling regression expectation from YoY: `a + b * yoy_level` | `mom_expected = mom_a + mom_b * mom_yoy_level` |

| cpi_pca.core_expected | Internal | float | Rolling regression expectation from Core YoY | `core_expected = core_a + core_b * core_yoy_level` |

| cpi_pca.mom_z_adj | Internal | float | `(mom_actual - mom_expected) / mom_cons_std` | Standardized trend-adjusted headline surprise |

| cpi_pca.core_z_adj | Internal | float | `(core_actual - core_expected) / core_mom_cons_std` | Standardized trend-adjusted core surprise |

| cpi_pca.pre_window_bps | Output | float | Max extent bps from 3:00pm T-1 to 8:29am event day (B/E-style move) | `max(high-open, open-low)` converted via `points_to_bps` |

| cpi_pca.post_window_bps | Output | float | Max extent bps from 8:30am to 5:00pm ET event day (B/E-style move) | `max(high-open, open-low)` converted via `points_to_bps` |

| cpi_pca.post_hourly_be_bps | Output | list[dict] | Post-event clock-time B/E-style extent for each event at `08:30, 09:00, 10:00, ..., 15:00` ET | `[{minute_offset, clock_label, be_bps}]` used in post-bucket hourly drilldown table |

| cpi_pca.pre_window_close_to_close_bps | Output | float | Signed close-to-close return bps for the pre-event window | `points_to_bps(close_last - close_first)` |

| cpi_pca.post_window_close_to_close_bps | Output | float | Signed close-to-close return bps for the post-event window | `points_to_bps(close_last - close_first)` |

| cpi_pca.trail_range_bps | Internal | float | High-low bps over trailing window ending at `3:00pm T-1` | Used for `pre_mkt_features[0]` |

| cpi_pca.trail_vol_bps | Internal | float | Std dev of 1-min close-to-close bps changes over trailing window ending at `3:00pm T-1` | Used for `pre_mkt_features[1]` |

| cpi_pca.pre_path | Output | list[dict] | Normalized pre-event path points from `3:00pm T-1 -> 8:29am` | `{aligned_ts, t_min, bps}` per point |

| cpi_pca.post_path | Output | list[dict] | Normalized post-event path points from `8:30am -> 5:00pm` | `{aligned_ts, t_min, bps}` per point |

| cpi_pca.composite_score | Internal | float | `w1*factor1 + w2*factor2` where `w` = explained-variance weights | Used for quantile bucket lookup |

| cpi_pca.bucket_stats | Output | list[dict] | Per-bucket bounds, count, mean bps | Displayed in standalone HTML tables |

| cpi_pca.factor_impacts | Output | dict | Regression coefficients for extent mapping (supports `linear` and `abs(|F|)` forms) | Used for factor-to-bps attribution + bucketing score |

| cpi_pca.pre_feature_source | Output | str | `cons_std` \| `consensus_fallback` when std inputs are flat | Displayed in Pre-Event PCA model info |

| cpi_pca.warnings | Output | list[str] | Data/model warnings emitted into dashboard UI | Fallback or data-quality warnings panel |

| cpi_pca.regression_metrics | Output | dict | Per-mode fit metrics (`r2`, `rmse`) from factor->bps regression | Displayed with pre/post equation at dashboard bottom |

| cpi_pca.economic_interpretation | Output | dict | Per-mode text for Factor1/Factor2 economic meaning based on loading signs and dominance | Rendered in bottom interpretation panel |

| cpi_pca.bucket_drilldown_stats | Output | dict | Clicked-bucket conditional stats including bootstrap mean CI and small-sample warnings | Rendered in drilldown stats row |



---



## NFP Two-Factor PCA Dashboard



### scripts/nfp_twofactor_pca_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| nfp_pca.excel | Input | Path | Existing Bloomberg workbook path (falls back to Y:/Meini or Z:/Meini defaults) | `--excel Y:/Meini/bbg_event_fields.xlsx` |

| nfp_pca.db | Input | Path | Existing SQLite DB path | `--db workspace/economic_events.db` |

| nfp_pca.symbol | Input | str | Futures prefix | `--symbol TY` |

| nfp_pca.start_date | Input | str | `YYYY-MM-DD`, default `2022-01-01` | `--start-date 2022-01-01` |

| nfp_pca.bucket_count | Input | int | `>= 2` (default 5) | `--bucket-count 5` |

| nfp_pca.output | Output | Path | Optional HTML path; default in `workspace/scenarios/` | `--output workspace/scenarios/nfp_twofactor_pca_dashboard.html` |



### NFP Two-Factor Derived Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| nfp_pca.pre_features | Internal | list[float] | `[nfp_cons_std, uer_cons_std]`, std dev > 0 | Pre-event PCA input vector |

| nfp_pca.pre_mkt_features | Internal | list[float] | `[trail_range_bps, trail_vol_bps]` computed strictly before `3:00pm T-1` | Pre-event market-derived uncertainty vector |

| nfp_pca.post_features | Internal | list[float] | `[nfp_z, uer_z]` where `z=(actual-consensus)/std_dev` | Post-event PCA input vector |

| nfp_pca.nfp_expected | Internal | float | Consensus baseline for post-event standardization | `nfp_expected = nfp_consensus` |

| nfp_pca.uer_expected | Internal | float | Consensus baseline for post-event standardization | `uer_expected = uer_consensus` |

| nfp_pca.nfp_z | Internal | float | `(nfp_actual - nfp_consensus) / nfp_cons_std` | Standardized NFP surprise |

| nfp_pca.uer_z | Internal | float | `(uer_actual - uer_consensus) / uer_cons_std` | Standardized UER surprise |

| nfp_pca.pre_window_bps | Output | float | Max extent bps from 3:00pm T-1 to 8:29am event day (B/E-style move) | `max(high-open, open-low)` converted via `points_to_bps` |

| nfp_pca.post_window_bps | Output | float | Max extent bps from 8:30am to 5:00pm ET event day (B/E-style move) | `max(high-open, open-low)` converted via `points_to_bps` |

| nfp_pca.post_hourly_be_bps | Output | list[dict] | Post-event clock-time B/E-style extent for each event at `08:30, 09:00, 10:00, ..., 15:00` ET | `[{minute_offset, clock_label, be_bps}]` used in post-bucket hourly drilldown table |

| nfp_pca.pre_window_close_to_close_bps | Output | float | Signed close-to-close return bps for the pre-event window | `points_to_bps(close_last - close_first)` |

| nfp_pca.post_window_close_to_close_bps | Output | float | Signed close-to-close return bps for the post-event window | `points_to_bps(close_last - close_first)` |

| nfp_pca.pre_feature_source | Output | str | `cons_std` \| `consensus_fallback` when std inputs are flat | Displayed in Pre-Event PCA model info |

| nfp_pca.factor_impacts | Output | dict | Regression coefficients for extent mapping (supports `linear` and `abs(|F|)` forms) | Used for factor-to-bps attribution + bucketing score |

| nfp_pca.warnings | Output | list[str] | Data/model warnings emitted into dashboard UI | Fallback or data-quality warnings panel |



---



## PPI Two-Factor PCA Dashboard



### scripts/ppi_twofactor_pca_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| ppi_pca.excel | Input | Path | Existing Bloomberg workbook path (falls back to Y:/Meini or Z:/Meini defaults) | `--excel Z:/Meini/bbg_event_fields.xlsx` |

| ppi_pca.db | Input | Path | Existing SQLite DB path | `--db workspace/economic_events.db` |

| ppi_pca.symbol | Input | str | Futures prefix | `--symbol TY` |

| ppi_pca.start_date | Input | str | `YYYY-MM-DD`, default `2022-01-01` | `--start-date 2022-01-01` |

| ppi_pca.bucket_count | Input | int | `>= 2` (default 5) | `--bucket-count 5` |

| ppi_pca.supercore_mom_actual | Input | float | Any finite numeric value (user-entered current release) | `0.3` |

| ppi_pca.output | Output | Path | Optional HTML path; default in `workspace/scenarios/` | `--output workspace/scenarios/ppi_twofactor_pca_dashboard.html` |



### PPI Two-Factor Derived Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| ppi_pca.pre_features | Internal | list[float] | `[ppi_cons_std, core_ppi_cons_std]`, std dev > 0 | Pre-event PCA input vector |

| ppi_pca.pre_mkt_features | Internal | list[float] | `[trail_range_bps, trail_vol_bps]` computed strictly before `3:00pm T-1` | Pre-event market-derived uncertainty vector |

| ppi_pca.post_features | Internal | list[float] | `[ppi_z, core_super_blend_z]` where `core_super_blend_z=(core_ppi_z + supercore_hist_z)/sqrt(2)` | Post-event PCA input vector |

| ppi_pca.pre_window_bps | Output | float | Max extent bps from 3:00pm T-1 to 8:29am event day (B/E-style move) | `max(high-open, open-low)` converted via `points_to_bps` |

| ppi_pca.post_window_bps | Output | float | Max extent bps from 8:30am to 5:00pm ET event day (B/E-style move) | `max(high-open, open-low)` converted via `points_to_bps` |

| ppi_pca.post_hourly_be_bps | Output | list[dict] | Post-event clock-time B/E-style extent for each event at `08:30, 09:00, 10:00, ..., 15:00` ET | `[{minute_offset, clock_label, be_bps}]` used in post-bucket hourly drilldown table |

| ppi_pca.pre_window_close_to_close_bps | Output | float | Signed close-to-close return bps for the pre-event window | `points_to_bps(close_last - close_first)` |

| ppi_pca.post_window_close_to_close_bps | Output | float | Signed close-to-close return bps for the post-event window | `points_to_bps(close_last - close_first)` |

| ppi_pca.supercore_hist_mean | Internal | float | Expanding mean of prior Supercore prints (`shift(1)`) | Used for Supercore historical-z normalization |

| ppi_pca.supercore_hist_std | Internal | float | Expanding std-dev of prior Supercore prints (`shift(1)`, floor `EPS`) | Used for Supercore historical-z normalization |

| ppi_pca.supercore_hist_z | Internal | float | `(supercore_actual - supercore_hist_mean) / supercore_hist_std` | Supercore proxy surprise component |

| ppi_pca.pre_feature_source | Output | str | `cons_std` \| `consensus_fallback` when std inputs are flat | Displayed in Pre-Event PCA model info |

| ppi_pca.factor_impacts | Output | dict | Regression coefficients for extent mapping (supports `linear` and `abs(|F|)` forms) | Used for factor-to-bps attribution + bucketing score |

| ppi_pca.warnings | Output | list[str] | Data/model warnings emitted into dashboard UI | Fallback or data-quality warnings panel |



---



## X API Tweet Fetcher



### tinkering/x_fetch_tweet.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| x_tweet.tweet_id | Input | str | Numeric tweet ID | `--tweet-id 1861234567890123456` |

| x_tweet.username | Input | str | X username without @ | `--username realDonaldTrump` |

| x_tweet.bearer_token | Input | str | Non-empty token; defaults to `X_BEARER_TOKEN` | `--bearer-token <token>` |

| x_tweet.fields | Input | str | Comma-separated `tweet.fields` list | `--fields created_at,text,public_metrics` |

| x_tweet.expansions | Input | str | Comma-separated expansions or empty | `--expansions author_id` |

| x_tweet.user_fields | Input | str | Comma-separated `user.fields` list | `--user-fields id,username` |

| x_tweet.include_retweets | Input | bool | true/false | `--include-retweets` |

| x_tweet.include_replies | Input | bool | true/false | `--include-replies` |

| x_tweet.out | Output | Path | Writable path for raw JSON | `--out workspace/analysis/tweet.json` |

| x_tweet.summary | Input | bool | true/false | `--summary` |



---



## X Macro Stream Bot



### scripts/x_macro_stream_bot.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| x_macro.no_validate | Input | bool | true/false | `--no-validate` |

| x_macro.no_reconcile | Input | bool | true/false | `--no-reconcile` |



### Tweet Approval Callback + Storage Contract



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| SCORE_DESCRIPTIONS | Constant | dict[int, str] | Keys are every integer from `-5` through `5`; values are the public escalation/de-escalation rubric descriptions | `SCORE_DESCRIPTIONS[3]` appended to approved tweet forwards |

| tweet_callback.approve | Input | str | `tweet_approve:<tweet_id>`; approves and forwards without a score | `tweet_approve:1861234567890123456` |

| tweet_callback.reject | Input | str | `tweet_reject:<tweet_id>`; labels rejected and does not forward | `tweet_reject:1861234567890123456` |

| tweet_callback.score | Input | str | `tweet_score:<tweet_id>:<score>` where `score` is integer `-5..5`; approves, stores score, and forwards with rubric line | `tweet_score:1861234567890123456:3` |

| tweet_labels.label | Output | str \| None | `approved`, `rejected`, or `NULL` before moderation | Stored by `update_tweet_label()` |

| tweet_labels.forwarded_message_id | Output | int \| None | Telegram public-chat message id for successful approved forwards; `NULL` means not forwarded and eligible for retry | Stored by `mark_forwarded()` |

| tweet_labels.score | Output | int \| None | Integer `-5..5` for scored approvals; `NULL` for reject, pending, or approve-with-no-score | Public forwarded line uses `Score: +3 - <description>` |

| x_retry_forwards | Input | command | `/x_retry_forwards [limit]`; `limit` optional integer `1..200`, default `50`; replays approved X tweets with no public forward id | `/x_retry_forwards 25` |



### Bloomberg Email News Approval Callback + Storage Contract



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| bbgnews_callback.approve | Input | str | `bbgnews_approve:<news_id>`; approves and forwards without a score. `news_id` is the integer `news_labels.id`, not the long Graph message id. | `bbgnews_approve:42` |

| bbgnews_callback.reject | Input | str | `bbgnews_reject:<news_id>`; labels rejected and does not forward | `bbgnews_reject:42` |

| bbgnews_callback.score | Input | str | `bbgnews_score:<news_id>:<score>` where `score` is integer `-5..5`; approves, stores score, and forwards with rubric line | `bbgnews_score:42:3` |

| bbg_news.graph_id | Input | str | Source message id stored as unique dedupe key (`Graph id` or `outlook:<EntryID>`); not used in Telegram callback data because it can exceed Telegram's 64-byte callback limit | `outlook:000000...` |

| bbg_news.telegram_text | Output | str | Compact HTML Telegram message containing only escaped email subject and optional italic received timestamp; body/footer are intentionally omitted from rendering | `<b>Mainland to issue 15b yuan worth of sovereign bonds</b>\n<i>2026-06-10T23:00:50Z</i>` |

| news_labels.id | Output | int | SQLite autoincrement row id; stable short callback key for Bloomberg news review buttons | `42` |

| news_labels.label | Output | str \| None | `approved`, `rejected`, or `NULL` before moderation | Stored by `update_news_label()` |

| news_labels.forwarded_message_id | Output | int \| None | Telegram public-chat message id for successful approved forwards; `NULL` means not forwarded and eligible for retry | Stored by `mark_forwarded()` |

| news_labels.score | Output | int \| None | Integer `-5..5` for scored approvals; `NULL` for reject, pending, or approve-with-no-score | Public forwarded line uses `Score: +3 - <description>` |

| bbg_retry_forwards | Input | command | `/bbg_retry_forwards [limit]`; `limit` optional integer `1..200`, default `50`; replays approved Bloomberg news rows with no public forward id | `/bbg_retry_forwards 25` |



### Environment



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| BBG_GRAPH_AUTH_MODE | EnvVar | str | `app` or `delegated`; use `delegated` for device-code sign-in as the mailbox user, or `app` for app-only client credentials. Default `app`. | `set BBG_GRAPH_AUTH_MODE=delegated` |

| BBG_MAIL_SOURCE | EnvVar | str | `outlook_com` or `graph`; selects local Outlook Desktop COM or Microsoft Graph backend. Default `graph`. | `set BBG_MAIL_SOURCE=outlook_com` |

| BBG_OUTLOOK_START_IF_CLOSED | EnvVar | bool-like str | `1`, `true`, or `yes` enables Outlook COM auto-start when using `BBG_MAIL_SOURCE=outlook_com`; the SOD launchers set it to `true` and explicitly start Classic Outlook first | `set BBG_OUTLOOK_START_IF_CLOSED=true` |

| BBG_MAILBOX | EnvVar | str | Outlook mailbox UPN to read through Microsoft Graph. In delegated mode this should match the signed-in user's mailbox. | `set BBG_MAILBOX=you@firm.com` |

| BBG_NEWS_MODE | EnvVar | str | `event` or `poll`; `event` uses Outlook `ItemAdd` real-time push and requires `BBG_MAIL_SOURCE=outlook_com`; default `poll` | `set BBG_NEWS_MODE=event` |

| BBG_NEWS_DB_PATH | EnvVar | str | Optional override for Bloomberg news moderation SQLite DB path. Defaults to local user-profile storage under `Path.home() / ".market_data" / "bbg_news.db"`. | `set BBG_NEWS_DB_PATH=C:\Users\aasth\.market_data\bbg_news.db` |

| BBG_NEWS_POLL_SECONDS | EnvVar | int | `>= 5`; polling interval for `scripts/bbg_news_stream_bot.py` loop mode. Default `60`. | `set BBG_NEWS_POLL_SECONDS=60` |

| BBG_NEWS_SAFETY_POLL_SECONDS | EnvVar | int | `>= 0`; optional backup polling interval while `BBG_NEWS_MODE=event`; `0` disables safety polling. Default `0`. | `set BBG_NEWS_SAFETY_POLL_SECONDS=300` |

| BBG_NEWS_SENDERS | EnvVar | str | Comma-separated Bloomberg sender addresses or domains to accept from the mailbox | `set BBG_NEWS_SENDERS=noreply@bloomberg.net,bloomberg.net` |

| BBG_NEWS_WATERMARK_PATH | EnvVar | str | Optional local watermark file path. Defaults to `Path.home() / ".market_data" / "bbg_news_watermark.txt"`. | `set BBG_NEWS_WATERMARK_PATH=C:\Users\aasth\.market_data\bbg_news_watermark.txt` |

| GRAPH_CLIENT_ID | EnvVar | str \| None | Azure app registration client id for Microsoft Graph auth; not used when `BBG_MAIL_SOURCE=outlook_com` | `set GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000` |

| GRAPH_CLIENT_SECRET | EnvVar | str \| None | Azure app registration client secret, required only for `BBG_GRAPH_AUTH_MODE=app` | `set GRAPH_CLIENT_SECRET=<secret>` |

| GRAPH_TENANT_ID | EnvVar | str | Azure tenant id for Microsoft Graph auth | `set GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000` |

| GRAPH_TOKEN_CACHE_PATH | EnvVar | str | Optional delegated token cache path. Defaults to `Path.home() / ".market_data" / "graph_delegated_token.json"`. | `set GRAPH_TOKEN_CACHE_PATH=C:\Users\aasth\.market_data\graph_delegated_token.json` |

| X_MACRO_CHAT_ID | EnvVar | str | Telegram chat IDs for macro stream delivery (comma-separated numeric IDs) | `set X_MACRO_CHAT_ID=-5216893879,-1001234567890` |

| X_MACRO_DB_PATH | EnvVar | str | Optional override for the X macro SQLite DB path. Defaults to local user-profile storage under `Path.home() / ".market_data" / "x_macro_stream.db"`. | `set X_MACRO_DB_PATH=C:\Users\aasth\.market_data\x_macro_stream.db` |

| X_MACRO_PUBLIC_CHAT_ID | EnvVar | str | Telegram chat ID; numeric string (can be negative for groups) | `set X_MACRO_PUBLIC_CHAT_ID=-3486536567` |

| TELEGRAM_BOT_TOKEN | EnvVar | str | Shared Telegram bot token used by both `telegram_bot.py` (polling) and `x_macro_stream_bot.py` (sending) | `set TELEGRAM_BOT_TOKEN=123456:ABC-DEF...` |



### scripts/bbg_news_stream_bot.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| bbg_news.dry_run | Input | bool | true/false; fetches and logs matching Graph messages without DB, watermark, or Telegram writes | `--dry-run --once` |

| bbg_news.lookback_minutes | Input | int | `>= 1`; initial fallback lookback when no DB/watermark timestamp exists. Default `60` | `--lookback-minutes 180` |

| bbg_news.mode | Input | str | `poll` or `event`; event mode attaches to Outlook Inbox `ItemAdd` and requires `BBG_MAIL_SOURCE=outlook_com` | `--mode event` |

| bbg_news.once | Input | bool | true/false; poll once and exit instead of loop mode | `--once` |

| bbg_news.poll_seconds | Input | int | `>= 5`; loop sleep interval. Defaults to `BBG_NEWS_POLL_SECONDS` or `60` | `--poll-seconds 30` |

| bbg_news.safety_poll_seconds | Input | int | `>= 0`; optional slow backup poll interval in event mode; default `0` (off) | `--safety-poll-seconds 300` |

| bbg_news.since | Input | str \| None | ISO timestamp lower bound for Graph `receivedDateTime` filtering | `--since 2026-06-10T12:00:00Z` |

| bbg_news.top | Input | int | `1..200`; max Graph messages fetched per poll. Default `50` | `--top 100` |



### SOD launcher contracts

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| run_all_sod.bat | Internal | Windows batch launcher | Additive only; starts missing SOD services and skips already-running process/port matches; must not kill or restart processes | `Y:\Aastha\SOD\run_all_sod.bat` |
| run_all_sod.AccessAPI_check | Internal | TCP host:port probe | Warn-only check against externally managed AccessAPI | `100.79.30.21:8001` |
| run_all_sod.PostgreSQL_check | Internal | TCP host:port probe | Warn-only check against externally managed OHLCV PostgreSQL | `100.82.143.79:5432` |
| run_all_sod.OutlookClassicPath | Internal | Path | Classic Outlook executable from `App Paths\OUTLOOK.EXE` with fallback `C:\Program Files\Microsoft Office\Root\Office16\OUTLOOK.EXE`; never use New Outlook `olk.exe` | `OUTLOOK.EXE /recycle` |
| AggressionMonitorV1Watchdog | Internal | Windows scheduled task name | Every 1 minute, interactive logon, hidden window; starts V1 only if port `5050` is down | `schtasks /Query /TN AggressionMonitorV1Watchdog` |
| run_aggression_monitor.bat | Internal | Windows batch launcher | Port `5050`; module `scripts.aggression_dashboard.app`; crash restart loop; `/watchdog` skips browser | `Y:\Aastha\SOD\run_aggression_monitor.bat` |
| run_aggression_monitor_v3.bat | Internal | Windows batch launcher | Port `5053`; module `scripts.aggression_dashboard_v3.app`; deps flask pandas numpy websockets databento | `Y:\Aastha\SOD\run_aggression_monitor_v3.bat` |
| SODAutoStart | Internal | Windows scheduled task name | Optional at-logon task documented only; not auto-registered by code changes | `schtasks /Create /TN "SODAutoStart" /SC ONLOGON ...` |
| watch_aggression_monitor_v1.ps1 | Internal | PowerShell keep-alive | Additive 5050 ensure; never kills other monitors; log `%LOCALAPPDATA%\aggression_v1_watchdog.log` | `Y:\Aastha\SOD\watch_aggression_monitor_v1.ps1` |


## News & Tweet Fade Dashboard



### news_fade_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| news_fade.accounts | Input | list[str] \| None | Comma-separated X usernames; omit for all accounts | `--accounts FirstSquawk,LiveSquawk,DeItaone` |

| news_fade.dedupe_seconds | Input | int | `>= 0`; same-account messages within this many seconds collapse; default `60` | `--dedupe-seconds 120` |

| news_fade.end_date | Input | str \| None | UTC date/datetime; `YYYY-MM-DD` interpreted as end of day | `--end-date 2026-05-08` |

| news_fade.fade_mins | Input | list[int] | Comma-separated positive minute horizons; default `30,60` | `--fade-mins 15,30,60` |

| news_fade.initial_mins | Input | int | `> 0`; minutes after the delay window used for initial peak search; default `5` | `--initial-mins 5` |

| news_fade.label | Input | str | `all`, `pending`, `approved`, or `rejected`; default `all` | `--label approved` |

| news_fade.lag_mins | Input | int | `>= 0`; allows delayed market reaction after message timestamp; default `3` | `--lag-mins 3` |

| news_fade.keywords | Input | list[str] \| None | Comma-separated broad topic keywords; overrides default Iran/Israel/Lebanon entity list | `--keywords iran,israel,hezbollah` |

| news_fade.max_paths | Input | int | `>= 0`; max per-event path arrays embedded in HTML; scalar stats remain for all events; default `600` | `--max-paths 1000` |

| news_fade.move_threshold_bps | Input | float | `>= 0`; `0` includes all events with market data, positive values require abs initial peak move >= threshold; default `2.0` | `--move-threshold-bps 0` |

| news_fade.output | Output | Path | Standalone HTML path; default timestamped file under `workspace/scenarios/` | `--out workspace/scenarios/news_fade_dashboard_latest.html` |

| news_fade.path_pre_mins | Input | int | `>= 0`; minutes before message included in click-through price path; default `10` | `--path-pre-mins 10` |

| news_fade.start_date | Input | str \| None | UTC date/datetime; `YYYY-MM-DD` interpreted as start of day | `--start-date 2026-02-09` |

| news_fade.strict_terms | Input | list[str] \| None | Comma-separated war-only terms used by the dashboard's strict toggle | `--strict-terms strike,attack,missile,drone` |

| news_fade.symbol | Input | str | Futures symbol prefix present in PostgreSQL `ohlcv_1m`; default `TY` | `--symbol TY` |

| news_fade.topic | Input | str | `iran_israel_lebanon` or `none`; default `iran_israel_lebanon` | `--topic iran_israel_lebanon` |

| news_fade.x_macro_db | Input | Path | Existing SQLite DB with `tweet_labels`; defaults to `X_MACRO_DB_PATH` or `~/.market_data/x_macro_stream.db` | `--x-macro-db C:\Users\aasth\.market_data\x_macro_stream.db` |



---



## Bloomberg Event Fields Loader



### load_bbg_event_fields_to_db.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| bbg_loader.dry_run | Input | bool | true/false | `python data_setup/load_bbg_event_fields_to_db.py --dry-run` |

| bbg_loader.excel | Input | Path | Existing Excel file | `--excel //100.95.155.81/FRGMSharedSpace/Meini/bbg_event_fields.xlsx` |



### Output Stats



| Name | Kind | Type | Description |

|------|------|------|-------------|

| bbg_loader.matched | Output | int | Events matched in DB |

| bbg_loader.actual_updated | Output | int | Actual values updated |

| bbg_loader.consensus_updated | Output | int | Consensus values updated |

| bbg_loader.std_dev_updated | Output | int | Std dev values updated |

| bbg_loader.not_found | Output | int | Events not found in DB |

| bbg_loader.unmapped_labels | Output | set[str] | Excel labels without DB mapping |



---



## Bloomberg BBG Data Loader



### create_bbg_data_table.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| bbg_data_loader.dry_run | Input | bool | true/false | `python data_setup/create_bbg_data_table.py --dry-run` |

| bbg_data_loader.replace | Input | bool | true/false | `python data_setup/create_bbg_data_table.py --replace` |

| bbg_data_loader.excel | Input | Path | Existing Excel file | `--excel //100.95.155.81/FRGMSharedSpace/Meini/bbg_event_fields.xlsx` |

| bbg_data_loader.db | Input | Path | Existing SQLite DB | `--db workspace/economic_events.db` |

| bbg_data_daily.log_file | Output | Path | Append-only scheduler run log path | `workspace/logs/bbg_data_daily.log` |

| bbg_data_daily.local_log_file | Output | Path | Local fallback log path under `%LOCALAPPDATA%` | `%LOCALAPPDATA%\\market_data_puller\\logs\\bbg_data_daily.log` |

| bbg_data_daily.error_marker | Output | Path | Last-error marker file written on wrapper/target failure | `%LOCALAPPDATA%\\market_data_puller\\status\\bbg_data_daily_last_error.txt` |

| bbg_data_daily.success_marker | Output | Path | Last-success marker file updated after a clean dual-target run | `%LOCALAPPDATA%\\market_data_puller\\status\\bbg_data_daily_last_success.txt` |

| bbg_data_loader.unmatched_db_event_fallback | Output | int | `>= 0`; rows that used Excel-date fallback timestamp because no DB timestamp match was found | Reported in loader summary |

| bbg_data_loader.stale_excel_age_days | Output | int | `> 2` when workbook is stale | Included in local stale warning output |

| bbg_data_task.name | Output | str | Current Windows Task Scheduler loader task name | `BBGData` |

| bbg_data_task.schedule | Output | str | Repeating loader cadence | `every 3 hours at HH:15, 24x7` |

| bbg_refresh_task.name | Output | str | Current Windows Task Scheduler workbook refresh task name | `BBGRefreshExcel` |

| bbg_refresh_task.schedule | Output | str | Repeating workbook-refresh cadence on the BBG machine | `every 3 hours at HH:00, 24x7` |



---



## Weekly Excel Calendar -> SQLite Loader



### data_setup/ingest_excel_calendar_to_sqlite.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| excel_calendar_sqlite_loader.excel | Input | Path \| None | Existing Excel file; optional (auto-detect newest weekly file when omitted) | `--excel Y:/Meini/2.23 -3.6.xlsx` |

| excel_calendar_sqlite_loader.db | Input | Path | Existing SQLite DB | `--db workspace/economic_events.db` |

| excel_calendar_sqlite_loader.dry_run | Input | bool | true/false | `python data_setup/ingest_excel_calendar_to_sqlite.py --dry-run` |



### Excel Calendar -> SQLite Derived Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| excel_calendar_sqlite_loader.event_id | Internal | str | Stable SHA1 prefix `excel_...` over `event_name + country + event_date` | Deterministic idempotent row ID |

| excel_calendar_sqlite_loader.event_date | Output | str | `YYYY-MM-DDTHH:MM:SS` in US/Eastern | `2026-03-06T08:30:00` |

| excel_calendar_sqlite_loader.country | Output | str | Full country name mapped from 2-letter code when possible | `United States` |

| excel_calendar_sqlite_loader.source | Output | str | Constant marker for inserted rows | `excel_calendar` |

| excel_calendar_sqlite_loader.rows_parsed | Output | int | `>= 0` | Parsed rows from Calendar sheet |

| excel_calendar_sqlite_loader.rows_inserted | Output | int | `>= 0` | Inserted rows (non-overwriting behavior) |

| excel_calendar_sqlite_loader.rows_skipped_existing | Output | int | `>= 0` | Existing natural-key matches skipped |



---



## economic_events.db Staleness Checker



### scripts/check_economic_events_db_staleness.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| economic_events_staleness.db | Input | list[Path] | Existing SQLite file paths; defaults to repo `workspace/economic_events.db` and `D:\FRDBs\workspace\economic_events.db` | `--db workspace/economic_events.db --db D:\FRDBs\workspace\economic_events.db` |

| economic_events_staleness.max_age_hours | Input | float | `> 0`, default `30.0` | `--max-age-hours 30` |

| economic_events_staleness.state_file | Input | Path | Writable JSON path for duplicate-alert suppression | `--state-file C:\Users\aasth\.market_data\alerts\economic_events_db_staleness_state.json` |

| economic_events_staleness.dry_run | Input | bool | true/false | `python scripts/check_economic_events_db_staleness.py --dry-run` |



### economic_events.db Staleness Derived Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| economic_events_staleness.alert_chat_id | Output | str | Preferred `BBG_ALERT_CHAT_ID`, else first configured `TELEGRAM_CHAT_ID` | Used for stale DB Telegram alert |

| economic_events_staleness.age_hours | Output | float \| None | `>= 0` when file exists; `None` when missing | `36.4` |

| economic_events_staleness.last_write_utc | Output | str \| None | ISO-8601 UTC timestamp when file exists | `2026-04-20T12:57:10.704642+00:00` |

| economic_events_staleness.reason | Output | str | `fresh`, `stale`, or `missing` | `stale` |

| economic_events_staleness.state_key | Internal | str | Stable fingerprint over stale paths + mtimes + threshold | Used to suppress duplicate Telegram sends |

| economic_events_staleness.wrapper_log_file | Output | Path | Local append-only task-wrapper log | `%LOCALAPPDATA%\\market_data_puller\\logs\\economic_events_staleness.log` |



---



## PricingMonkey PPI Weeks Export



### pm_nfp_weeks_export.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| pm_weeks.from_db | Input | bool | true/false | `python pm_nfp_weeks_export.py --from-db` |

| pm_weeks.event_name | Input | str | Exact `economic_events.event_name` | `--event-name "Producer Price Index MoM"` |

| pm_weeks.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2020-01-01` |

| pm_weeks.end_date | Input | str | `YYYY-MM-DD` | `--end-date 2025-12-31` |

| pm_weeks.out | Output | Path | Any writable path | `--out ppi_weeks_results.csv` |

| pm_weeks.no_wait_login | Input | bool | true/false | `--no-wait-login` |

| pm_weeks.login_wait_secs | Input | float | `> 0` | `--login-wait-secs 120` |



### pm_monday_breakevens_export.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| pm_monday.count | Input | int | `> 0`; default `50` | `python pm_monday_breakevens_export.py --count 50` |

| pm_monday.as_of_date | Input | str | `YYYY-MM-DD`; optional | `--as-of-date 2026-02-20` |

| pm_monday.no_wait_login | Input | bool | true/false | `--no-wait-login` |

| pm_monday.login_wait_secs | Input | float | `> 0`; default `90.0` | `--login-wait-secs 120` |

| pm_monday.board_url | Input | str | Valid PricingMonkey board URL | `--board-url "https://pricingmonkey.com/b/76a2cae1-92b3-419b-a258-c04aa59b6eea"` |

| pm_monday.profile_dir | Input | str | Writable local path for persistent browser profile | `--profile-dir ".edge_profile_nfp_weeks"` |

| pm_monday.out | Output | Path | Any writable path | `--out monday_breakevens_results.csv` |

| pm_monday.event | Input | str | Non-empty label; default `MONDAY_BE` | `--event MONDAY_BE` |

| pm_monday.pm_url_default | Constant | str | Default PricingMonkey board URL | `https://pricingmonkey.com/b/898a2390-bb96-405c-b5d2-80648ec352a8` |

| pm_monday.profile_dir_default | Constant | Path | Default local browser profile path | `.edge_profile_monday_be` |

| pm_monday.output_columns | Output | list[str] | Fixed schema | `["EVENT","DATE","TIME","PRICE_BPS","FWD_BE","IMPLIED_VOL","TRADE_DESCRIPTION"]` |

| pm_monday.bf_rule | Internal | str | Monday BF date rule | `bf = monday - 3 days` |



## Live Unified Dashboard API



### /api/config Response



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| api_config.defaults | Output | dict | Event-specific UI defaults (cons_std_default, cons_std_step/min/max, bucket_width_default/step/min/max, fwd_be_default, bucket_precision, post_event_end_time) | `{"cons_std_default": 0.1, "fwd_be_default": 8.0, "post_event_end_time": "17:00"}` |

| api_config.defaults.post_event_end_time | Output | str | HH:MM format; defaults to "15:00" for NFP/CPI, "17:00" for PPI | `"17:00"` |



### /api/event_types Response



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| api_event_types.event_types | Output | dict | Map of event_type → {name, time, supported} | `{"NFP": {"name": "Non-Farm Payrolls", "time": "08:30", "supported": true}, "CPI": {...}, "PPI": {...}}` |

| api_event_types.supported | Output | list[str] | List of supported event types with full config | `["NFP", "CPI", "PPI"]` |



## Redis Alerts Dashboard



### scripts/redis_alerts_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| redis_dash.host | Input | str | Valid bind host/IP | `--host 127.0.0.1` |

| redis_dash.port | Input | int | `1..65535`, default `5071` | `--port 5071` |

| redis_dash.debug | Input | bool | true/false | `--debug` |



### `/api/alerts` Response



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| redis_dash.stream | Output | str | Redis stream key | `market:events` |

| redis_dash.count | Output | int | `>= 0` | displayed row count |

| redis_dash.length | Output | int | `>= 0` | total stream length |

| redis_dash.last_generated_id | Output | str | Redis stream ID format | `1740088123456-0` |

| redis_dash.items | Output | list[dict] | Recent alerts (oldest→newest) | Table rows in dashboard |

| redis_dash.items[].event_id | Output | str | Deterministic event identifier | `sha1(...)` |

| redis_dash.items[].event_name | Output | str | Event/session name | `tokyo_open` |

| redis_dash.items[].country | Output | str | Region code | `US` |

| redis_dash.items[].scheduled_time_et | Output | str | ISO datetime | `2026-02-24T08:30:00-05:00` |

| redis_dash.items[].source_file | Output | str | Source identifier | `2.23 -3.6.xlsx` |



## Snapshot Explorer Dashboard



### scripts/snapshot_explorer_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| snapshot_dash.host | Input | str | Valid bind host/IP | `--host 127.0.0.1` |

| snapshot_dash.port | Input | int | `1..65535`, default `5080` | `--port 5080` |

| snapshot_dash.debug | Input | bool | true/false | `--debug` |



### Snapshot Explorer API Surfaces



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| snapshot_dash.api.live | Input | query params | `symbol?: str`, `expiry?: int` | `/api/live?symbol=TYH6%20Comdty&expiry=1` |

| snapshot_dash.api.at | Input | query params | `ts: str` required, `symbol?: str`, `columns?: csv[str]` | `/api/at?ts=2026-02-24%2010:00:00&symbol=TYH6%20Comdty` |

| snapshot_dash.api.history | Input | query params | `start: str`, `end: str`, `symbol?: str`, `interval?: int`, `columns?: csv[str]`, `allow_large?: bool` | `/api/history?start=...&end=...&interval=60` |

| snapshot_dash.api.today | Input | query params | `symbol?: str`, `live?: bool`, `interval?: int`, `columns?: csv[str]`, `allow_large?: bool` | `/api/today?symbol=TYH6%20Comdty&interval=60` |

| snapshot_dash.api.search | Input | JSON body | `{start:str, end:str, where:list[Cond], expand?:bool, interval?:int, columns?:list[str]\|csv[str], allow_large?:bool}` | `POST /api/search` |

| snapshot_dash.api.diagnose | Output | dict | SnapshotAPI health payload (`pg`, `redis`, `trading_day`) | `/api/diagnose` |

| snapshot_dash.api.subscribe | Output | text/event-stream | SSE events: `status`, `snapshot`, `heartbeat` | `/api/subscribe?symbol=TYH6%20Comdty` |

| snapshot_dash.response.ok | Output | bool | true/false | `{"ok": true, ...}` |

| snapshot_dash.response.elapsed_ms | Output | float | `>= 0` milliseconds | `{"elapsed_ms": 42.3}` |

| snapshot_dash.response.row_count | Output | int | `>= 0` | `{"row_count": 338}` |

| snapshot_dash.response.columns | Output | list[str] | DataFrame columns returned by query | `{"columns":["last_updated","bbg_symbol","live_px"]}` |

| snapshot_dash.response.data | Output | list[dict] | JSON-safe DataFrame rows | `{"data":[{"bbg_symbol":"TYH6 Comdty","live_px":110.5}]}` |

| snapshot_dash.search_condition | Input | object | `symbol:str`, `column in FILTERABLE_COLUMNS`, `op in {>,>=,<,<=,=,!=}`, `value:float` | `{"symbol":"TYH6 Comdty","column":"open_position","op":"!=","value":0}` |

| snapshot_dash.ui.all_columns | Constant | list[str] | Fixed display/filter list used by dashboard column selector | `["last_updated","trading_day","bbg_symbol",...,"closed_position"]` |

| snapshot_dash.today_interval_fallback | Internal | str | If `interval` is set on `/api/today`, use trading-day `history(...)` sampling instead of `today(...)` | `/api/today?interval=60` |



## Live Unified Dashboard API



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| api_events_response.events | Output | list[dict] | Lightweight events (date, cons_std, surp_pct, surprise, standardized_surprise, fwd, post_be, nfp_actual, nfp_cons, optional uer_* fields) | `{"events":[{"date":"2025-01-10","cons_std":26.1,"surp_pct":0.4,"fwd":6.38}]}` |

| api_events_response.historical_std_surprises | Output | list[float] | Standardized surprise values; empty list if unavailable | `{"historical_std_surprises":[-2.1,0.3,1.7]}` |

| api_events_response.adhoc_trading_days | Output | list[dict] | Empty list unless ad-hoc days are loaded | `{"adhoc_trading_days":[]}` |

| api_events_response.light | Output | bool | True when /api/events returns lightweight payloads | `{"light":true}` |

| api_polymarket_resolve.kind | Input | str | `nfp` \| `uer` \| `cpi` | `/api/polymarket/resolve?kind=cpi` |

| api_polymarket_resolve.outcomes | Output | list[dict] | Outcome rows with `token_id`, `label`, `mid`, `observed_ts` | `{"outcomes":[{"token_id":"123","label":"150k to 200k","mid":0.31}]}` |

| api_polymarket_latest.token_ids | Input | str | Comma-separated token IDs | `/api/polymarket/latest?token_ids=123,456` |

| api_polymarket_latest.rows | Output | list[dict] | Latest midpoint by token (`token_id`, `mid`, `observed_ts`) | `{"rows":[{"token_id":"123","mid":0.42}]}` |

| api_scenarios_table.component | Input | str | `nfp` \| `uer` \| `cpi` | `/api/scenarios/table?component=cpi&consensus=0.3&cons_std=0.05` |

| api_scenarios_table.rows | Output | list[dict] | Scenario table rows (`actual_range_label`, `polymarket_odds`, `surprise_percentile`, `pre_event_be_bps`, `post_event_be_bps`, bucket bounds) | `{"rows":[{"actual_range_label":"4.0% to 4.1%","polymarket_odds":0.27}]}` |

| api_scenarios_table.expected | Output | dict | Probability-weighted expected B/E (`pre_event_be_bps`, `post_event_be_bps`) | `{"expected":{"pre_event_be_bps":5.2,"post_event_be_bps":8.7}}` |

| api_scenarios_hist.window | Input | float | `start_h` and `end_h` in event-relative hours, default `-12..12` | `/api/scenarios/hist?component=nfp&bucket_min=40&bucket_max=50&start_h=-12&end_h=12` |

| api_scenarios_hist.values | Output | list[float] | Historical B/E samples (bps) for selected percentile bucket and window | `{"values":[4.1,6.3,7.9]}` |

| api_scenarios_hist.rows | Output | list[dict] | Per-event samples with timestamp and value, used for last-two-event annotation | `{"rows":[{"timestamp":"2025-01-10T08:30:00-05:00","value":6.1}]}` |

| api_scenarios_hist.stats | Output | dict | Distro stats (`count`, percentiles, expected rally/selloff, `pct_zero`) | `{"stats":{"count":22,"mean":1.8,"expected_rally":2.6,"pct_zero":47.7}}` |

| api_scenarios_hist.stats_lines | Output | list[str] | Pre-formatted distro-style lines for modal stats panel | `{"stats_lines":["N = 22","Mean = 1.80 bps","..."]}` |

| api_scenarios_hist.last_two | Output | list[dict] | Last 2 event markers (`value`, `label`) for chart annotations | `{"last_two":[{"value":2.4,"label":"2024-12-06 +2.40 bps"}]}` |

| api_scenarios_cpi_yoy_odds.rows | Output | list[dict] | CPI headline YoY odds-only rows (`actual_range_label`, `polymarket_odds`, `observed_ts`) | `/api/scenarios/cpi_yoy_odds` |

| api_scenarios_bin_paths.window | Input | float | `start_h` and `end_h` in event-relative hours (same window as histogram) | `/api/scenarios/bin_paths?component=nfp&bucket_min=40&bucket_max=50&start_h=-12&end_h=12&bin_start=3&bin_end=4` |

| api_scenarios_bin_paths.bin | Input | float | `bin_start`, `bin_end` bounds from clicked histogram bar | `/api/scenarios/bin_paths?...&bin_start=3&bin_end=4` |

| api_scenarios_bin_paths.paths | Output | list[dict] | Event price paths in selected bin (`date`, `value`, `points[{ts,t_min,bps}]`) for timestamp-axis plotting | `{"paths":[{"date":"2025-01-10","value":3.4,"points":[{"ts":"2025-01-09T20:30:00","t_min":0,"bps":0.0}]}]}` |

| api_scenarios_bin_paths.path_count | Output | int | Number of event paths returned for clicked bin | `{"path_count":4}` |



## Live Unified Dashboard HTML



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| unified_dashboard.smoke | Input | bool | `0/1` query param; runs JS smoke checks on load | `http://localhost:8800/?smoke=1` |



## Unified Dashboard Tests



### Environment



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| UNIFIED_DASHBOARD_URL | EnvVar | str | Base URL for unified dashboard server | `UNIFIED_DASHBOARD_URL=http://localhost:8800` |



### Live Unified Dashboard CLI (`scripts/live_unified_dashboard.py`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| live_unified.embed | Input | bool | true/false | `python scripts/live_unified_dashboard.py --event NFP --embed` |

| live_unified.preevent_only | Input | bool | true/false | `python scripts/live_unified_dashboard.py --event NFP --preevent-only` |



### L1 Sample Validation (`scripts/validate_l1_sample.py`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| l1_validate.l1_root | Input | Path | Existing directory | `--l1-root tests/fixtures/l1_event_features` |

| l1_validate.event_types | Input | str | Comma-separated types | `--event-types NFP,CPI,PPI,JOLTS` |

| l1_validate.min_per_type | Input | int | `>= 1` | `--min-per-type 1` |

| l1_validate.strict | Input | bool | true/false | `--strict` |



### Standalone Pre-Event HTML CLI (`scripts/build_preevent_standalone_html.py`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| preevent.template | Input | Path | Existing HTML template | `--template workspace/scenarios/live_preevent_dashboard_20260108_191400.html` |

| preevent.lookback_days | Input | int | `>= 1` | `--lookback-days 5` |

| preevent.min_year | Input | int | `>= 1900` | `--min-year 2022` |

| preevent.cons_std_pct_min | Input | float | `0-100` | `--cons-std-pct-min 10` |

| preevent.cons_std_pct_max | Input | float | `0-100` | `--cons-std-pct-max 90` |

| preevent.out | Output | Path | Any writable path | `--out workspace/scenarios/preevent_nfp_dashboard.html` |



## Month-End Friday US Open Distribution



### month_end_friday_us_open_distribution.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| mef_us_open.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| mef_us_open.start_year | Input | int | `>= 2000` | `--start-year 2022` |

| mef_us_open.session_start | Input | str | HH:MM (ET) | `--session-start 08:00` |

| mef_us_open.session_end | Input | str | HH:MM (ET) | `--session-end 17:00` |

| mef_us_open.output_dir | Input | Path | Any writable path | `--output-dir workspace/analysis` |

| mef_us_open.plot_path_all | Output | Path | PNG file path (all month-ends, Fridays marked) | `month_end_days_us_open_TY.png` |

| mef_us_open.plot_path_friday | Output | Path | PNG file path (Fridays only) | `month_end_friday_us_open_TY.png` |

| mef_us_open.stats_json | Output | Path | JSON stats output | `month_end_days_us_open_TY.json` |

| mef_us_open.rows_csv | Output | Path | Per-date returns CSV (all month-ends) | `month_end_days_us_open_TY.csv` |

| mef_us_open.rows_csv_friday | Output | Path | Per-date returns CSV (Fridays only) | `month_end_friday_us_open_TY.csv` |



## ISM Manufacturing PMI Pre/Post Distribution



### ism_manufacturing_pmi_pre_post_distribution.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| ism_pmi.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| ism_pmi.event_name | Input | str | `economic_events.event_name` pattern | `--event-name "ISM Manufacturing PMI"` |

| ism_pmi.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| ism_pmi.pre_hours | Input | float | `> 0` | `--pre-hours 2` |

| ism_pmi.post_hours | Input | float | `> 0` | `--post-hours 2` |

| ism_pmi.output_dir | Input | Path | Any writable path | `--output-dir workspace/analysis` |

| ism_pmi.plot_path_pre | Output | Path | PNG for pre-event window | `ism_mfg_pmi_pre_TY.png` |

| ism_pmi.plot_path_post | Output | Path | PNG for post-event window | `ism_mfg_pmi_post_TY.png` |

| ism_pmi.stats_json | Output | Path | JSON stats output | `ism_mfg_pmi_pre_post_TY.json` |

| ism_pmi.rows_csv_pre | Output | Path | Per-event pre-window returns | `ism_mfg_pmi_pre_TY.csv` |

| ism_pmi.rows_csv_post | Output | Path | Per-event post-window returns | `ism_mfg_pmi_post_TY.csv` |



## ISM Services PMI Post-Event Dashboard



### ism_services_pmi_postevent_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| ism_services.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| ism_services.event_name | Input | str | `economic_events.event_name` pattern | `--event-name "ISM Services PMI"` |

| ism_services.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| ism_services.bucket_width | Input | int | `1-50` recommended | `--bucket-width 10` |

| ism_services.output | Output | Path | HTML output path (optional) | `--output workspace/scenarios/ism_services_pmi.html` |

| ism_services.summary_json | Output | Path | Summary JSON path | `workspace/analysis/ism_services_pmi_postevent/ism_services_pmi_postevent_summary.json` |



## JOLTS Job Openings Post-Event Dashboard



### jolts_job_openings_postevent_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| jolts_job_openings.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| jolts_job_openings.event_name | Input | str | `economic_events.event_name` pattern | `--event-name "JOLTs Job Openings"` |

| jolts_job_openings.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| jolts_job_openings.bucket_width | Input | int | `1-50` recommended | `--bucket-width 20` |

| jolts_job_openings.output | Output | Path | HTML output path (optional) | `--output workspace/scenarios/jolts_job_openings.html` |

| jolts_job_openings.summary_json | Output | Path | Summary JSON path | `workspace/analysis/jolts_job_openings_postevent/jolts_job_openings_postevent_summary.json` |



## JOLTS Job Openings Pre/Post Event Dashboard



### jolts_prepost_event_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| jolts_prepost.symbol | Input | str | Futures symbol prefix present in PostgreSQL `ohlcv_1m`; default `TY` | `--symbol TY` |

| jolts_prepost.event_name | Input | str | `economic_events.event_name` pattern; default `JOLTs Job Openings` | `--event-name "JOLTs Job Openings"` |

| jolts_prepost.start_date | Input | str | `YYYY-MM-DD`, default `2022-01-01` | `--start-date 2022-01-01` |

| jolts_prepost.bucket_width | Input | int | `1-50` recommended; default `20` | `--bucket-width 20` |

| jolts_prepost.output | Output | Path | Optional standalone HTML path; default timestamped file under `workspace/scenarios/` | `--output workspace/scenarios/jolts_prepost_event_dashboard.html` |

| jolts_prepost.summary_json | Output | Path | Summary JSON path | `workspace/analysis/jolts_prepost_event/jolts_prepost_event_summary.json` |



### JOLTS Pre/Post Embedded Inputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| jolts_prepost.input.consensus | Input | float | JOLTS level in thousands or millions; values `< 1000` are interpreted as millions | `6.8` or `6800` |

| jolts_prepost.input.consensus_std_dev | Input | float | Dispersion in thousands or millions; values `< 10` are interpreted as millions | `105.7` or `0.106` |

| jolts_prepost.input.bank_estimates | Input | list[float] | Optional comma/space-separated bank estimates, consolidated only; values `< 1000` are interpreted as millions | `6.790, 6.700, 6.800` |

| jolts_prepost.input.previous | Input | float | Prior JOLTS level in thousands or millions; values `< 1000` are interpreted as millions | `6.866` |

| jolts_prepost.input.actual | Input | float \| empty | Optional post-release actual in thousands or millions | `7.391` |



### JOLTS Pre/Post Derived Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| jolts_prepost.pre_event_context | Output | dict | Consensus vs prior, MA3, MA6, MA12, and trend z-score inputs | Displayed in Pre-Event Setup cards |

| jolts_prepost.bank_dispersion | Output | dict | `{n, mean, std, min, max, range}` from manual bank estimates; no per-bank display | Used as effective dispersion when `n >= 2` |

| jolts_prepost.impulse_message | Output | str | Copy-pasteable colleague-style note; labels consensus-vs-prior/MA as priced-in narrative only | `JOLTS Job Openings: Consensus vs prior/MA6 -> ...` |

| jolts_prepost.surprise_bias | Output | dict | Historical beat/miss rate and mean/median raw + normalized surprise | `{"beat_pct": 60.4, "mean_surprise": 76.0}` |

| jolts_prepost.driver_analysis | Output | dict | Per-horizon OLS/correlation/R2 for normalized surprise, consensus-vs-prior, and consensus-vs-MA6 | Ranking table in Driver Analysis panel |

| jolts_prepost.magnitude_stats | Output | dict | Per-horizon median/p75/p95/p99 absolute TY move plus p1/p99 signed move | Used in automated message move-stats line |

| jolts_prepost.post_event_metrics | Output | dict | Realized surprise, z-score, percentile bucket, model-predicted TY bps, and similar-bucket stats | Updates after Actual input |



## Non Farm Payrolls Post-Event Dashboard



### non_farm_payrolls_postevent_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| non_farm_payrolls.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| non_farm_payrolls.event_name | Input | str | `bbg_data.event_name` / `bbg_data.db_event_name` pattern | `--event-name "Non Farm Payrolls"` |

| non_farm_payrolls.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| non_farm_payrolls.bucket_width | Input | int | `1-50` recommended | `--bucket-width 20` |

| non_farm_payrolls.actual | Input | float \| None | Optional scenario override for UI default | `--actual 130` |

| non_farm_payrolls.consensus | Input | float \| None | Optional scenario override for UI default | `--consensus 65` |

| non_farm_payrolls.consensus_std_dev | Input | float \| None | Optional scenario override for UI default, must be non-zero when provided | `--consensus-std-dev 26` |

| non_farm_payrolls.output | Output | Path | HTML output path (optional) | `--output workspace/scenarios/non_farm_payrolls_postevent.html` |

| non_farm_payrolls.summary_json | Output | Path | Summary JSON path | `workspace/analysis/non_farm_payrolls_postevent/non_farm_payrolls_postevent_summary.json` |



## NFP UER 4x4 Post-Event Grid



### tinkering/nfp_uer_4x4_postevent_grid.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| nfp_uer_grid.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| nfp_uer_grid.symbol_pattern | Input | str | SQL ILIKE symbol pattern for OHLCV | `--symbol-pattern TY%` |

| nfp_uer_grid.output | Output | Path | HTML output path (optional) | `--output workspace/scenarios/nfp_uer_grid.html` |

| nfp_uer_grid.bucket_width | Constant | float | `25.0` percentile points (4x4 fixed grid) | NFP/UER buckets: `0-25`, `25-50`, `50-75`, `75-100` |

| nfp_uer_grid.eod_et | Constant | str | Fixed EOD cutoff `17:00` ET on event date | t->EOD move uses event time to `5:00pm ET` |

| nfp_uer_grid.cell_stats | Output | dict | Per-cell stats include `count`, `mean_move`, `p5_move`, `p50_move`, `p95_move`, `mean_max_up`, `mean_max_down` (bps) | Clicked-cell panel in generated HTML |

| nfp_uer_grid.path_payload | Output | list[dict] | Per-event normalized post-event path points (`t_min`, `bps`, `ts`) for clicked-cell rendering | Grid click shows all event paths in the selected cell |

| nfp_uer_grid.path_xaxis_mode | Output | str | Time-of-day aligned axis (`HH:MM:SS` on anchor date) | Paths from different days align on same x-axis time scale |

| nfp_uer_grid.path_yaxis_mode | Output | str | Bond-format price axis (32nds), plotted in ticks with tick labels as bond notation | Path y-axis displays price in bond format |

| nfp_uer_grid.event_drilldown | Output | dict | Clicking a path reveals exact NFP/UER values (actual/consensus/std/z/pct) for that event | Path click detail panel in HTML |

| nfp_uer_grid.candlestick_payload | Output | dict | Per-event ±2 day 5m candlestick arrays with NFP event-time marker (`nfp_marker_ts`) | Path click renders candlestick with NFP marker only |



## TotalAggress vs Price/Yield Chart



### totalaggress_vs_price_chart.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| totalaggress.zip | Input | Path(s) | Databento zip file(s) with trades/MBP-1 | `--zip workspace/wenn/*.zip` |

| totalaggress.fetch | Input | bool | true/false; fetch from Databento API | `--fetch` |

| totalaggress.start | Input | str | `YYYY-MM-DD` | `--start 2026-01-29` |

| totalaggress.end | Input | str | `YYYY-MM-DD` | `--end 2026-02-05` |

| totalaggress.dv01 | Input | float | `> 0`; $/bp per contract | `--dv01 85.0` |

| totalaggress.bucket | Input | str | Pandas resample freq (e.g. 1s, 5s, 1min) | `--bucket 5s` |

| totalaggress.reset | Input | str | `none \| daily \| globex` | `--reset none` |

| totalaggress.symbol | Input | str | PostgreSQL OHLCV symbol pattern | `--symbol TY%` |

| totalaggress.no_cache | Input | bool | Skip parquet cache for API fetches | `--no-cache` |

| totalaggress.output_dir | Input | Path | Output directory | `--output-dir workspace/analysis` |

| totalaggress.html | Output | Path | Plotly dual-axis chart | `workspace/analysis/totalaggress_vs_price_<ts>.html` |

| totalaggress.json | Output | Path | Assumptions + stats JSON | `workspace/analysis/totalaggress_vs_price_<ts>.json` |



### Constants



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| DEFAULT_DV01 | Constant | float | 85.0 $/bp | DV01 per TY contract |

| DEFAULT_BUCKET | Constant | str | "5s" | Resample bucket interval |

| DEFAULT_RESET | Constant | str | "none" | TotalAggress reset rule |



## Curve Aggression Dashboard



### `scripts/aggression_v2_feature_audit.py` CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| move_exhaustion.output_root | Output | Path | `workspace/analysis/aggression_v2_move_exhaustion/` only | Offline research artifacts |
| move_exhaustion.decision_grid | Constant | str | Phase-1 freeze `1min` (5s deferred) | `configs/move_exhaustion/base.yaml` |
| move_exhaustion.session | Constant | str | `legacy_v2_01_et` day_open | Must match V2 residual convention |
| move_exhaustion.cli.e00 | Input | command | `py -3 -m scripts.move_exhaustion.cli e00` | Data/causality audit |
| move_exhaustion.cli.e15 | Input | command | `py -3 -m scripts.move_exhaustion.cli e15` | Cross-tenor flow confirmation |
| move_exhaustion.cli.e16 | Input | command | `py -3 -m scripts.move_exhaustion.cli e16` | TOD/macro/auction/roll regimes |
| move_exhaustion.features.curve_directional_flow_factor | Internal | float | duration-norm mean neighbour signed flow | E15 continuation lead |
| move_exhaustion.features.primary_regime | Internal | str | ordinary\|opening\|late\|high_vol\|macro\|auction\|roll | E16 mutually exclusive tag |
| move_exhaustion.holdout_unlocked | Constant | bool | `false` until Phase 5 | Do not inspect final holdout early |
| aggression_v22.port | Constant | int | `5058` manual-only | `py -3 -m scripts.aggression_dashboard_v22.app` |
| aggression_v22.tailscale_url | Constant | str | `https://aasthajain.tail174727.ts.net:8444/` tailnet-only | Tailscale Serve → `127.0.0.1:5058` |
| aggression_v22.store_dir | Output | Path | `workspace/analysis/aggression_v22_live/` | Alerts + LIFO episodes + signal logs |
| aggression_v22.lifo_friendly_label | Constant | str | Fixed `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL` | Banner + stats card |
| aggression_v22.lifo_friendly_fire | Constant | rule | `|V2 residual_z|≥1` AND trade_intensity ≤ session expanding p33 | Display-only; UI text `LIFO FRIENDLY` |
| aggression_v22.api_lifo | Output | JSON | LIFO context, episodes, `lifo_stats` | `GET /api/lifo` |
| aggression_v22.feed | Output | JSON | Buffer freshness plus `source` and nested `failover` health | `/api/live_signal`, `/api/signal_status` |
| aggression_v23.port | Constant | int | `5059` manual-only | `py -3 -m scripts.aggression_dashboard_v23.app` |
| aggression_v23.store_dir | Output | Path | `workspace/analysis/aggression_v23_live/` | Alerts + exhaustion context logs |
| aggression_v23.label | Constant | str | `PROVISIONAL / RESEARCH-ONLY / NOT AFTER-COST VALIDATED` | Fixed banner |
| aggression_v23.api_exhaustion | Output | JSON | Exhaustion context badges + explainers | `GET /api/exhaustion` |
| aggression_v26.api_feedback | Input/Output | JSON | Request `{episode_id: str, alert_kind: "lifo" \| "c018", correct: bool \| null, note?: str}`; note max 500 chars | `POST /api/feedback` |
| aggression_v26.api_live | Output | JSON | V2 live payload plus `lifo`, `c018`, and today-ET `feedback` | `GET /api/live` |
| aggression_v26.c018_episode | Output | JSON | Locked episode with move/range/fade fields and latest human-feedback fields | `c018_episodes.json` |
| aggression_v26.c018_label | Constant | str | `PROVISIONAL / C018-FADE-LEAN / NOT OFFLINE C018 / NON-EXECUTABLE` | C018 rows and banner |
| aggression_v26.c018_rule | Constant | rule | Forming bar excluded; 30 completed `price` bars ending at candidate; 1/64 tick; newly reached 8-tick extension; 15m decluster | Display-only opposite fade lean |
| aggression_v26.c018_t3_range_cutoff_ticks | Constant | float | `10.5`; frozen once from discovery-prefix `range_ticks` 67th percentile | C018 T3 gate |
| aggression_v26.daily_feedback | Output | JSON | Per kind: `issued`, `pending_grade`, `graded`, `marked_correct`, `marked_wrong`, `hit_rate_graded`; today ET | `/api/live.feedback.stats` |
| aggression_v26.feedback_correct | Input | bool \| null | `true` correct; `false` wrong; `null` Skip/pending | `POST /api/feedback` |
| aggression_v26.feedback_log | Output | JSONL | Append-only submissions; prior grade retained; latest episode grade wins | `workspace/analysis/aggression_v26_live/feedback.jsonl` |
| aggression_v26.port | Constant | int | `5062` manual-only; browser-safe replacement for Chromium-blocked SIP-TLS port 5061 | `py -3 -m scripts.aggression_dashboard_v26.app --host 127.0.0.1 --port 5062` |
| aggression_v27.port | Constant | int | `5063` manual-only | `py -3 -m scripts.aggression_dashboard_v27.app --host 127.0.0.1 --port 5063` |
| aggression_v27.tailscale_url | Constant | str | `https://aasthajain.tail174727.ts.net:8445/` tailnet-only | Tailscale Serve → `127.0.0.1:5063`; does not replace V2.2 `:8444` |
| aggression_v27.store | Output | Path | `workspace/analysis/aggression_v27_live/` only | Alert + LIFO + signal logs |
| aggression_v27.construction | Constant | str | `B1_W10` local nowcast; enter 1.5 + session ticks 2–6 same sign, no same-sign re-entry; exit 0.5 after 5m or flip; 10m is a score mark, not a flatten | Live FV and alert clock |
| aggression_v27.session_min_ticks | Constant | float | `2.0` | High-conviction floor vs nowcast-only |
| aggression_v27.session_max_ticks | Constant | float | `6.0` | Regime-tail cap |
| aggression_v27.v2_signal_log | Input | Path | `workspace/analysis/aggression_v2_live/signal_log_*.csv` read-only | Session residual for the entry gate |
| aggression_v27.conviction_gate | Output | dict | `skip_reason`, `session_ticks`, `agree`, `z_hot` | `/api/live_signal` → `alerts.conviction_gate` |
| aggression_v1_v27.banner_alerts_db | Output | Path | `workspace/analysis/aggression_v1_v27_banner_alerts/banner_alerts.sqlite` | Append-only V1 banner changes + V2.7 ENTRY/EXIT |
| aggression_v27.status | Constant | str | `PROVISIONAL / RESEARCH-ONLY / LOCAL-10M-FV / NOT AFTER-COST VALIDATED` | Banner / config |
| aggression_v27.trade_signal | Output | dict | `phase` FLAT/IN_TRADE/Z_EXITED; `entry_action` ENTER SHORT/LONG; z-exit vs 10m clock | `/api/live_signal` → `alerts.trade_signal` |
| aggression_v27.session_open_hour_et | Constant | int | `18` America/New_York | Most recent 18:00 ET ≤ now |
| aggression_v27.stats_cutover_et | Constant | Timestamp | `2026-09-02 12:07` America/New_York | Display-only; this session’s banner/table start. Later sessions use 18:00 ET |
| aggression_v27.session_stats | Output | dict | `ticks_captured`, `n_issued`, `n_closed`, `n_open`, `n_win`, `n_lose`, `n_flat`, `session_start_et`, `stats_window` | `/api/live_signal` → `alerts.session_stats` |
| aggression_v27_v1_zones.windows_json | Input | Path | `.../v1_banner_windows.json` (live week) or `v1_banner_windows_full.json` (all B1_W10 + week) | Stamped ENTRY/EXIT + V1 ±5m path |
| aggression_v27_v1_zones.E | Constant | tuple[str] | `AGGRESS` / `IMPLIED_PASSIVE` / `BALANCED` / `QUIET` | Frozen entry-banner keys; do not add after seeing ticks |
| aggression_v27_v1_zones.S | Constant | tuple[str] | `SINGLE` / `MIXED` | One V1 state vs any change on ENTRY±5m |
| aggression_v27_v1_zones.min_n | Constant | int | `3` | Nomination floor |
| aggression_v27_v1_zones.scores_csv | Output | Path | `.../current_rule_week_replay/zone_scores.csv` | Per-cell n, ticks, hit, nominated |
| aggression_v27_v1_zones.summary_json | Output | Path | `.../current_rule_week_replay/zone_summary.json` | E, ExS, X diagnostic, nominated ≤2 |
| aggression_v27_v1_zones.readout_md | Output | Path | `.../current_rule_week_replay/zone_readout.md` | Nominated + losing cells; not a live gate |
| aggression_v26.status | Constant | str | `PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE` | UI + APIs |
| aggression_v26.store_dir | Output | Path | `workspace/analysis/aggression_v26_live/` only | Alerts, LIFO/C018 episodes, signal logs, feedback |
| aggression_v12.port | Constant | int | `5056` manual-only | `py -3 -m scripts.aggression_dashboard_v12.app` |
| aggression_v12.strategy_status | Constant | str | Fixed `PROVISIONAL / RESEARCH-ONLY` | UI + `/api/config` |
| aggression_v12.caution_label | Constant | str | Fixed `PROVISIONAL / RANGE-CAUTION / NON-DIRECTIONAL` | Caution card |
| aggression_v12.soft_breakout_label | Constant | str | Fixed `PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL` | Soft-breakout card |
| aggression_v12.soft_breakout_pass_bps | Constant | float | `4.0` only; primary 8bps = 0 PASS | Factor-sweep restrictive PASSes |
| aggression_v12.soft_breakout_fire | Constant | rule | `large_persist_10\|20` OR `age_ge_30` OR (`z_ge_1_5` AND `fast_15m`) | Display-only lean |
| aggression_v12.lifo_friendly_label | Constant | str | Fixed `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL` | LIFO card + banner |
| aggression_v12.lifo_friendly_fire | Constant | rule | `|z|≥1` AND trade_intensity ≤ session expanding p33 | Display-only; UI text `LIFO FRIENDLY` |
| aggression_v12.lifo_stats | Output | JSON | Finalized/active LIFO RANGE/TREND/ambiguous rates | `/api/range_caution` → `lifo_stats` |
| aggression_v12.large_abs_z | Constant | float | `1.0` frozen | Caution fire threshold |
| aggression_v12.widening_window_min | Constant | int | `15` | Abs residual compare window |
| aggression_v12.horizon_min | Constant | int | `30` | Forward regime score horizon |
| aggression_v12.store_dir | Output | Path | `workspace/analysis/aggression_v12_live/` | Episodes JSON/CSV + signal logs |
| aggression_v12.api_range_caution | Output | JSON | Live context (+ `soft_breakout`), episodes, stats | `GET /api/range_caution` |
| aggression_v12.api_caution_log | Output | JSON | Append-once episode rows | `GET /api/caution_log?scope=today\|all` |
| aggression_v12.report | Output | Markdown | Isolation + B4-only non-claims | `docs/aggression_v12_range_caution.md` |
| aggression_v13.port | Constant | int | `5055` manual-only | `py -3 -m scripts.aggression_dashboard_v13.app` |
| aggression_v13.strategy_status | Constant | str | Fixed `PROVISIONAL / RESEARCH-ONLY / TICK-TOTALAGGRESS / NON-EXECUTABLE` | UI + `/api/config` |
| aggression_v13.store_dir | Output | Path | `workspace/analysis/aggression_v13_live/` | V1.3 1m flow_history only |
| TICK_PLOT_MAX_POINTS | Constant | int | `80000` | Cap Plotly P1 points at the newest prints |
| aggregate_tick_aggression | Internal | (trades_by_symbol, dv01_table, dv01_adjust) -> (DataFrame, meta) | `total_aggress = cumsum(sign*size*$DV01)` rebased at first print | V1 and V1.3 panel 1 |
| attach_last_traded_price | Internal | (tick_df, price_trades) -> DataFrame | Adds `last_trade_price` via ffill of the selected symbol's prints | V1 panel 1 |
| last_traded_price_at | Internal | (index, trades_df) -> Series | Last print price of the price symbol at each print timestamp | V1 panel 1 |
| _SESSION_TICKS | Internal | Dict[str, Any] | session_start + per-symbol print frames | In-memory V1 and V1.3 tick tape |
| aggression_v1.flow_dominance.api | Output | JSON | Top-level `{status,state,research_state,label,reason,as_of,aggress,implied_passive,transition,model,parameters,observations}` | `POST /api/aggression` → `flow_dominance` |
| aggression_v1.flow_dominance.contributions | Internal | dict[str, float] | Completed 1m `aggress=beta*delta(TotalAggress)`; `implied_passive=delta(intraday residual)` | `flow_dominance_context.py` |
| aggression_v1.flow_dominance.dynamics | Constant | dict | Compare absolute 15m ticks to 5m prior; `>+1` tick INCREASING, `<-1` tick SLOWING, otherwise STEADY | Banner detail |
| aggression_v1.flow_dominance.hover_option | Input | bool | Local UI checkbox, default on; not sent in `/api/aggression` | `#banner-on-hover` |
| aggression_v1.flow_dominance.label | Constant | str | Fixed `DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED` | V1 banner + API |
| aggression_v1.flow_dominance.parameters | Constant | dict | `window_min=15`, `dominance_ratio=1.5`, `quiet_floor_ticks=2`, `tick_size=1/64`; forming bar excluded | State classifier |
| aggression_v1.flow_dominance.series | Output | list[dict] | Clipped `{x,status,state,reason,aggress,implied_passive,transition,as_of}` per completed 1m bar | `POST /api/aggression` → `flow_dominance_series` |
| aggression_v1.flow_dominance.states | Output | str | `AGGRESS_DOMINANT\|IMPLIED_PASSIVE_DOMINANT\|BALANCED\|QUIET\|WARMING\|UNAVAILABLE` | `flow_dominance.state` |
| aggression_v1_dispersion.feature_matrix | Input | Path | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` | Causal residual panel source |
| aggression_v1_dispersion.residual | Internal | float | Prior-day OLS `price_delta = α + β * agg_cum_daily`; first day expanding same-day prior minutes | V1-style implied-price residual |
| aggression_v1_dispersion.regime_horizon | Constant | int | Primary `30` minutes; also scores 5/15/60 | Independent forward RANGE/TREND |
| aggression_v1_dispersion.gate | Constant | rule | `n>=50`, `>=8` dates, `>=8pp` lift, one-sided CI>0, BH-FDR `q=0.10` | PASS / PROVISIONAL / FAIL |
| aggression_v1_dispersion.output_dir | Output | Path | `workspace/analysis/aggression_v1_flow_price_dispersion/` | Panel, journal, leaderboard, models, summary |
| aggression_v1_dispersion.report | Output | Markdown | Regime prediction verdict; no dashboard authorization | `docs/aggression_v1_flow_price_dispersion_experiments.md` |
| aggression_v1_factor_sweep.h | Constant | int | `{15, 30, 60}` minutes | Forward horizon grid |
| aggression_v1_factor_sweep.B | Constant | float | `{4, 6, 8, 12, 16}` yield bps | BREAKOUT threshold (×2 TY ticks/bp) |
| aggression_v1_factor_sweep.k | Constant | float | `{0.5, 1.0, 1.5, 2.0}` | CONTINUED_RANGE vs horizon-scaled prior-day ATR |
| aggression_v1_factor_sweep.primary | Constant | dict | `{h:30, B:8, k:1.0}` | Headline config reported first |
| aggression_v1_factor_sweep.output_dir | Output | Path | `workspace/analysis/aggression_v1_dispersion_factor_breakout/` | Journal, regime mix, gate ladder, stability |
| aggression_v1_factor_sweep.report | Output | Markdown | Factor vs breakout/range verdict; FINAL locked | `docs/aggression_v1_dispersion_factor_breakout_experiments.md` |
| aggression_v1_foundations.h | Constant | int | `{15, 30, 60}` | Forward horizon grid |
| aggression_v1_foundations.B | Constant | float | `{4, 6, 8, 12}` yield bps | BREAKOUT threshold |
| aggression_v1_foundations.k | Constant | float | `{0.5, 1.0, 1.5}` | CONTINUED_RANGE vs horizon-scaled ATR |
| aggression_v1_foundations.primary | Constant | dict | `{h:30, B:8, k:1.0}` | Headline config |
| aggression_v1_foundations.hypotheses | Constant | str | H1–H10 locked catalog | Width/duration/shape/speed/flow/depth/micro/combos/FV |
| aggression_v1_foundations.output_dir | Output | Path | `workspace/analysis/aggression_v1_dispersion_foundations/` | Journal, family CSVs, FINAL eval |
| aggression_v1_foundations.report | Output | Markdown | Foundations verdict; trade_int_lo not shippable | `docs/aggression_v1_dispersion_foundations_experiments.md` |
| aggression_v1_apd.definition | Constant | str | role_absorption_B; QUIET separate 4th bucket | Dominance labeling |
| aggression_v1_apd.windows | Constant | tuple[int] | {1, 5, 15} minutes | Contribution lookback |
| aggression_v1_apd.dominance_ratio | Constant | float | {1.25, 1.5, 2.0}; headline 1.5 | Role dominance threshold |
| aggression_v1_apd.primary | Constant | dict | {w:15, d:1.5, h:30} | Headline RANGE/TREND gate |
| aggression_v1_apd.output_dir | Output | Path | workspace/analysis/aggression_v1_aggress_passive_dominance/ | State mix, journals, leaderboards |
| aggression_v1_apd.report | Output | Markdown | Dominance vs RANGE/TREND verdict; 0 restrictive PASS | docs/aggression_v1_aggress_passive_dominance_experiments.md |
| aggression_v1_apd_sweep.windows | Constant | tuple[int] | {1,3,5,10,15,30} | Expanded lookbacks |
| aggression_v1_apd_sweep.dominance_ratios | Constant | tuple[float] | {1.1,1.25,1.5,2.0,2.5,3.0} | Dominance hardness |
| aggression_v1_apd_sweep.quiet_floors | Constant | tuple[float] | {0.25,0.5,0.75} | QUIET separate bucket |
| aggression_v1_apd_sweep.horizons | Constant | tuple[int] | {5,10,15,30,60,90} | Forward outcome windows |
| aggression_v1_apd_sweep.cutoff_packs | Constant | tuple[str] | frozen/strict/loose | RANGE/TREND level packs |
| aggression_v1_apd_sweep.output_dir | Output | Path | workspace/analysis/aggression_v1_aggress_passive_dominance_param_sweep/ | Staged journals + gates |
| aggression_v1_apd_sweep.report | Output | Markdown | Param-sweep verdict; 0 restrictive PASS | docs/aggression_v1_aggress_passive_dominance_param_sweep_experiments.md |
| aggression_v2_audit.bucket | Input | str | Pandas resample frequency; default `"1min"` | `--bucket 1min` |

| aggression_v2_audit.cache_dir | Input | Path | Directory containing cached `trades_v2_*` parquet files | `--cache-dir workspace/analysis/_totalaggress_cache` |

| aggression_v2_audit.output_dir | Output | Path | Writable output directory | `--output-dir workspace/analysis/aggression_v2` |

| aggression_v2_audit.price_symbol | Input | str | One of `FV`, `TU`, `TY`, `US`; default `TY` | `--price-symbol TY` |

| aggression_v2_audit.feature_matrix | Output | CSV | 1-minute research feature matrix with target `price - day_open_price` | `workspace/analysis/aggression_v2/feature_matrix.csv` |

| aggression_v2_audit.correlation | Output | CSV | Feature correlation matrix | `workspace/analysis/aggression_v2/feature_correlation.csv` |

| aggression_v2_audit.summary | Output | JSON | Audit metadata: rows, dates, missingness, high correlations, contracts, artifacts | `workspace/analysis/aggression_v2/feature_audit_summary.json` |



### `scripts/aggression_v2_tbbo_audit.py` CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression_v2_tbbo.api_base | Input | str | AccessAPI base URL | `--api-base http://100.79.30.21:8001` |

| aggression_v2_tbbo.bucket | Input | str | Pandas resample frequency; default `"1min"` | `--bucket 1min` |

| aggression_v2_tbbo.dates | Input | str | Optional comma-separated ET dates; defaults to latest trade-audit dates | `--dates 2026-06-01,2026-06-02` |

| aggression_v2_tbbo.max_dates | Input | int | Optional cap to last N default dates | `--max-dates 8` |

| aggression_v2_tbbo.output_dir | Output | Path | Writable output directory | `--output-dir workspace/analysis/aggression_v2_tbbo` |

| aggression_v2_tbbo.feature_matrix | Output | CSV | 1-minute TBBO-backed feature matrix with trade-flow + quote-at-trade features | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |

| aggression_v2_tbbo.correlation | Output | CSV | TBBO feature correlation matrix | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_correlation.csv` |

| aggression_v2_tbbo.summary | Output | JSON | Audit metadata: rows, dates, missingness, high correlations, fetch metadata, artifacts | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_audit_summary.json` |

### Aggression autoresearch Phase 0/1

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| aggression_autoresearch.api_base | Input | str | Historical AccessAPI base; default `http://100.79.30.21:8001` | `--api-base http://100.79.30.21:8001` |
| aggression_autoresearch.build_date | Input | date | Complete ET trading date; prior-day 18:00 ET through 17:00 ET | `--date 2026-07-20` |
| aggression_autoresearch.build_range | Input | tuple[date, date] | Inclusive; start ≤ end | `--start-date 2024-07-01 --end-date 2026-07-20` |
| aggression_autoresearch.duplicate_limit | Constant | float | Exact duplicate row fraction ≤ `0.01`; exact duplicates removed and audited | Date manifest `duplicates` |
| aggression_autoresearch.feature_version | Constant | str | Versioned builder contract; changes invalidate shard reuse | `phase0_v3` |
| aggression_autoresearch.front_rule | Constant | str | Current session contract selected only from prior complete-session candidate volumes; bootstrap seed is non-research-eligible | Date manifest `front_contract_selection` |
| aggression_autoresearch.output_root | Output | Path | Must resolve under `workspace/analysis/aggression_autoresearch_data/` | Per-date shards, manifests, parity, versions |
| aggression_autoresearch.parity_range | Input | tuple[date, date] \| None | Both dates required together; ordered and contained in build range; use a contract-stable benchmark | `--parity-start-date 2026-01-27 --parity-end-date 2026-01-29` |
| aggression_autoresearch.publication | Output | Parquet + JSON | Immutable; only complete, causally eligible, checksummed shards; passing parity required | `versions/v20260722_phase0_v3_dense_20260226/` |
| autoresearch.alpha_family | Internal | object | Family ID, `0 < alpha ≤ 0.1`, immutable experiment links | `StateStore.register_alpha_family(...)` |
| autoresearch.candidate_config | Input | dict[str, JSONScalar] | Keys/values must exactly match the declared grid; runner binds config + contract hash into frozen state | `run_candidate(..., config=..., declared_grid=...)` |
| autoresearch.candidate_input | Input | list[dict] | Allowlisted causal columns only; must include UTC `formation_ts`; no target/future/PnL fields | `run_candidate(...)` |
| autoresearch.candidate_output | Output | list[CandidateRow] | One input-derived UTC timestamp plus exact registered finite feature map; evaluator derives cells/direction | Candidate subprocess JSON envelope |
| autoresearch.canonical_spec_hash | Output | str | SHA-256 of canonical spec with only `hashes.spec_hash` zeroed | `canonical_spec_hash(spec)` |
| autoresearch.confirmation_approval | Input | Approval | Hash-bound family/experiment/spec/claim/data/date cutoff/approver; persisted once | `begin_confirmation(...)` |
| autoresearch.decision | Output | str | Includes `screen_pass`, `not_confirmed`, `positive_but_trivial`, `inconclusive`, `invalid`, `blocked_underpowered`, `non_actionable`, `confirmed` variants | `ExperimentResult.decision` |
| autoresearch.discovery_result | Output | ResultBundleV1 | Immutable D001/E001/E002 discovery payload; selected/frozen state or descriptive map, power audit, and `validation_opened=false` | `campaign/results/E001_discovery.json` |
| autoresearch.effective_spec_hash | Internal | str | SHA-256 binding original methodology spec hash to canonical append-only execution contract | `effective_spec_hash(base_hash, contract)` |
| autoresearch.event_manifest | Output | JSON | Read-only point-in-time high-importance US events; blackout `[-10,+30]` minutes | `manifests/event_manifest.json` |
| autoresearch.execution_contract | Input | ExecutionContractV1 | Strict schema v1; base spec hash plus non-empty candidate/evaluator/selection/outcome sections | `registry/execution_contracts/E001.yaml` |
| autoresearch.execution_manifest | Output | JSON | Append-only code/data/spec/approval/result bindings; validation chain ends at v4.2 and non-evidentiary quarantine at v5; all prior versions retained | `manifests/execution_manifest_v5.json` |
| autoresearch.exposure_key | Internal | tuple[str, str, str] | Unique trading date × target × semantic hypothesis family | `state.sqlite:exposure_ledger` |
| autoresearch.historical_reservation | Internal | Reservation | Atomic validation/quarantine run reservation plus full date/target/family exposure before outcomes load; identical failed identities only may retry | `reserve_historical_with_exposure(...)` |
| autoresearch.hypothesis_status | Internal | str | `exploratory`, `proposed`, `blocked`, `superseded` | `registry/hypotheses.yaml` |
| autoresearch.methodology_spec | Input | PredictiveExperimentSpecV2 \| DescriptiveSpecV2 | Strict schema version `2`; attempt cap `5`; overlapping evaluator cells; prospective approval-bound confirmation | `registry/experiments/E001.yaml` |
| autoresearch.preflight | Output | JSON | Formation-only deterministic candidate smoke; every forbidden-call counter must be zero | `integrity/preflight_<manifest>.json` |
| autoresearch.quarantine_result | Output | ResultBundleV1 | Transform-only 24-date historical diagnostic; `evidentiary_status=non_evidentiary`, no scorecard, no validation decision overwrite | `campaign/results/E001_quarantine.json` |
| autoresearch.research_branching | Output | JSON | Hash-bound terminal branch actions; closes failed predictive families and records proposal-only descriptive children without retuning | `campaign/results/research_branching_v1.json` |
| autoresearch.result_bundle | Output | ResultBundleV1 | Payload hash is detached; envelope binds ScorecardV2, base/effective spec, code manifest, and run identity | `fixed_env.results.build_result_bundle(...)` |
| autoresearch.run_identity | Internal | tuple[str, ...] | Experiment/family/split plus candidate, state, evaluator, data, and canonical spec SHA-256 | `RunIdentity(...)` |
| autoresearch.run_status | Internal | str | `RESERVED`, `COMPLETED`, `FAILED_NO_RESULT`, `CLOSED_INVALID`; max five attempts | `state.sqlite:runs` |
| autoresearch.scorecard | Output | CellScorecard | Selected config, effect/CI/raw+adjusted p, class/date counts, calibration, utility, stability, diagnostics | Typed result artifact |
| autoresearch.split_manifest | Output | JSON | 298 discovery, 101 validation, 24 quarantine dates; prospective dates strictly after final freeze | `manifests/split_manifest_v2.json` |
| autoresearch.validation_result | Output | ResultBundleV1 | One-shot transform-only E001/E002 holdout with ScorecardV2, 9,999 resamples, fixed Holm denominator, utility/stability/falsification diagnostics | `campaign/results/E001_validation.json` |

### Autoresearch Hypothesis Lab

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| autoresearch_lab.api.config | Output | JSON object | `{success, port, label, manual_only, campaign_root, output_root}` | `GET /api/config` |
| autoresearch_lab.api.evidence | Output | JSON object | Frozen branching, D001/E001/E002 projections, cohorts, ledger, integrity, preflight, registry | `GET /api/evidence` |
| autoresearch_lab.api.holdout_availability | Output | JSON object | `{execution_endpoint_present:false, all_holdouts_guarded:true, registered, drafts, blocked_hypotheses, global_reasons}` | `GET /api/holdout-availability` |
| autoresearch_lab.api.preregistrations | Input/Output | JSON object | POST a valid preregistration request; returns HTTP 201 with frozen draft metadata, never execution | `POST /api/preregistrations` |
| autoresearch_lab.api.sandbox_job | Output | JSON object | Existing in-memory job by 32-hex `job_id`; 404 if absent | `GET /api/sandbox/jobs/<job_id>` |
| autoresearch_lab.api.sandbox_jobs | Input/Output | JSON object | GET lists jobs; POST accepts `sandbox_request` and returns queued job with HTTP 202 | `POST /api/sandbox/jobs` |
| autoresearch_lab.api.sandbox_journal | Output | JSON object | `{success, counts, recent}` with up to 25 newest append-only records | `GET /api/sandbox/journal` |
| autoresearch_lab.api.sandbox_meta | Output | JSON object | Label, discovery matrix status/hash, candidate defaults, outcomes, allowed inputs, repetitions | `GET /api/sandbox/meta` |
| autoresearch_lab.app.--debug | Input | bool | Flask debug flag; default false; manual local use only | `py -3 -m scripts.autoresearch_lab.app --debug` |
| autoresearch_lab.app.--host | Input | str | Bind host; default `127.0.0.1` | `--host 127.0.0.1` |
| autoresearch_lab.app.--port | Input | int | TCP port; default and reserved lab port `5057` | `--port 5057` |
| autoresearch_lab.BASELINE_RAW | Constant | tuple[str, ...] | `realized_vol`, `trade_intensity`, `spread_mean`, `session_sin`, `session_cos` | Sandbox baseline logit inputs |
| autoresearch_lab.candidate_root | Output | Path | Must remain under lab root; source-addressed `<sha256>.py` files only | `workspace/analysis/autoresearch_lab/candidates/` |
| autoresearch_lab.discovery_matrix.assigned_dates | Input/Output | `(split: str) -> tuple[str, ...]` | `split` must equal `discovery`; all other values raise `PermissionError` | `discovery_matrix().assigned_dates("discovery")` |
| autoresearch_lab.discovery_matrix.load | Input/Output | `MatrixSlice` | Discovery split only; requested dates must be assigned discovery dates; columns must be cached/registered | `matrix.load("discovery", inputs, dates=dates)` |
| autoresearch_lab.journal_path | Output | JSONL | Append-only schema-v1 records with request/result hashes and source digest | `workspace/analysis/autoresearch_lab/journal/searches.jsonl` |
| autoresearch_lab.label | Constant | str | Fixed `EXPLORATORY / DISCOVERY-ONLY / NOT EVIDENCE` | Every sandbox result and dashboard header |
| autoresearch_lab.outcome.bounded_excursion | Input/Output | `int` | `threshold_ticks > 0`, `tick_size > 0`; default 4 ticks and 1/64 | `bounded_excursion(path, anchor, threshold_ticks=4)` |
| autoresearch_lab.outcome.convergence_first_passage | Input/Output | `int` | `0 < convergence_fraction <= 1`, `adverse_fraction > 0`, finite non-zero `formation_gap` | `convergence_first_passage(ts, path, anchor, formation_gap=gap)` |
| autoresearch_lab.outcome.directional_first_passage | Input/Output | `int` | `direction` in `{-1,1}`, `barrier_ticks > 0`, `tick_size > 0` | `directional_first_passage(ts, path, anchor, direction=-1)` |
| autoresearch_lab.outcome_names | Constant | tuple[str, ...] | `bounded_excursion`, `convergence_first_passage`, `directional_first_passage` | Sandbox outcome selector |
| autoresearch_lab.output_root | Output | Path | Sole lab writable root | `workspace/analysis/autoresearch_lab/` |
| autoresearch_lab.preregistration_request | Input | JSON object | Requires new valid `experiment_id`, unblocked `hypothesis_id`, non-empty `claim`, `holdout_split` in `prospective|validation|quarantine`, and exact journaled `search_result` | POST body to `/api/preregistrations` |
| autoresearch_lab.preregistration_result | Output | JSON object | `FROZEN_PREREGISTRATION_NOT_AUTHORIZED`; base/effective hashes, directory, file hashes, exposure precheck, blocker, blocked CLI template | `preregistrations/<id>_<hash>/preregistration.json` |
| autoresearch_lab.preregistration_root | Output | Directory | Frozen, idempotent spec/contract/journal/scaffold/summary files | `workspace/analysis/autoresearch_lab/preregistrations/` |
| autoresearch_lab.repetitions | Constant | int | Fixed `999` discovery clustered resamples | Sandbox `evaluate_predictive_cells` |
| autoresearch_lab.sandbox_job | Output | JSON object | `{job_id,status,stage,percent,timestamps,payload,result,error}`; status `queued|running|completed|failed` | Poll `/api/sandbox/jobs/<job_id>` |
| autoresearch_lab.sandbox_request | Input | JSON object | `split` must be `discovery`; candidate `E001|E002|D001|CUSTOM`; config, treatment, outcome, warmup, shift flag, optional slice; custom also requires source/inputs/features | POST body to `/api/sandbox/jobs` |
| autoresearch_lab.sandbox_result | Output | JSON object | Schema v1, `evidentiary_status=non_evidentiary`, discovery hashes/state/models, train/score counts, evaluation cells, search digest | Completed job `result` |
| autoresearch_lab.shift_repetitions | Constant | int | Fixed `999`; only run when `circular_shift=true`, minimum within-session displacement 30 minutes | Optional sandbox falsification |
| autoresearch_lab.tick_size | Constant | float | `1/64` price point | Outcome tick conversion |



### `scripts/aggression_monthend_flow.py` CLI

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| aggression_monthend.close_window_min | Input | int | `> 0`; default `90`; trailing observed session minutes used for close-window flow | `--close-window-min 60` |
| aggression_monthend.html | Input | bool | Flag; default `False`; writes optional inlined-Plotly timing chart | `--html` |
| aggression_monthend.matrix | Input | Path | Existing TBBO feature matrix CSV with `bucket_ts`, `et_date`, `agg_cum_daily`, `agg_1m`, `minutes_since_open` | `--matrix workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |
| aggression_monthend.month_end_days | Input | int | `> 0`; default `3`; trailing business days of each complete month tagged as month-end | `--month-end-days 3` |
| aggression_monthend.out_dir | Output | Path | Writable output directory; default `workspace/analysis/aggression_v2_tbbo/monthend/` | `--out-dir workspace/analysis/aggression_v2_tbbo/monthend` |
| aggression_monthend.daily_csv | Output | CSV | Per-day rows: `date`, `is_month_end`, `net_flow`, `close_window_flow`, `share_into_close`, close-window/session metadata | `monthend_flow_daily.csv` |
| aggression_monthend.timing_html | Output | HTML | Optional standalone timing chart with inlined Plotly; only written with `--html` | `monthend_flow_timing.html` |


### `scripts/aggression_v2_model_research.py` CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression_v2_model.input | Input | Path | TBBO feature matrix CSV | `--input workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |

| aggression_v2_model.output_dir | Output | Path | Writable model research output directory | `--output-dir workspace/analysis/aggression_v2_tbbo_models` |

| aggression_v2_model.target | Internal | str | Fixed `target_price_delta = price - day_open_price` | Daily-demeaned fair-value target |

| aggression_v2_model.validation | Internal | str | Walk-forward by ET date; no random split | Train prior dates, test next date |

| aggression_v2_model.predictions | Output | CSV | OOS predictions/fair value/residual by model/date/minute | `walkforward_predictions.csv` |

| aggression_v2_model.metrics | Output | JSON | OOS R²/RMSE, residual stats, residual mean-reversion diagnostics, quality filters | `model_metrics.json` |

| aggression_v2_model.coefficients | Output | CSV | Ridge/Lasso/OLS standardized coefficients and RF feature importances | `model_coefficients_importance.csv` |

| aggression_v2_model.train_metrics | Output | CSV | Per-fold train metrics for overfit comparison | `train_fold_metrics.csv` |



### `scripts/aggression_v2_conditional_signal.py` CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression_v2_signal.features | Input | Path | TBBO feature matrix CSV | `--features workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |

| aggression_v2_signal.predictions | Input | Path | Walk-forward predictions CSV | `--predictions workspace/analysis/aggression_v2_tbbo_models/walkforward_predictions.csv` |

| aggression_v2_signal.output_dir | Output | Path | Writable output directory | `--output-dir workspace/analysis/aggression_v2_tbbo_models` |

| aggression_v2_signal.min_n | Input | int | Minimum sample count for displayed best conditions; default `100` | `--min-n 100` |

| aggression_v2_signal.conditional_stats | Output | CSV | Conditional residual mean-reversion stats by condition and horizon | `conditional_signal_tests.csv` |

| aggression_v2_signal.conditional_dataset | Output | CSV | Prediction rows plus condition flags and future moves | `conditional_signal_dataset.csv` |

| aggression_v2_signal.summary | Output | JSON | Best conditions, horizons, hit rates, directional PnL proxy, artifacts | `conditional_signal_summary.json` |



### `scripts/aggression_v2_holdout_signal.py` CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression_v2_holdout.output_dir | Output | Path | Writable output directory | `--output-dir workspace/analysis/aggression_v2_tbbo_models` |

| aggression_v2_holdout.train_frac | Input | float | Chronological split fraction, default `0.6` | `--train-frac 0.6` |

| aggression_v2_holdout.min_n | Input | int | Minimum sample count for displayed rules, default `30` | `--min-n 30` |

| aggression_v2_holdout.stats | Output | CSV | Selection/holdout rule stats by threshold/horizon/condition | `holdout_signal_tests.csv` |

| aggression_v2_holdout.dataset | Output | CSV | Ridge prediction rows plus residual-z and forward moves | `holdout_signal_dataset.csv` |

| aggression_v2_holdout.summary | Output | JSON | Holdout best rules, split dates, thresholds, limitations, artifacts | `holdout_signal_summary.json` |



### Aggression V2 Research Dashboard (`scripts/aggression_dashboard_v2/app.py`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression_v2_dash.port | Input | int | Default `5052` | `py -3 -m scripts.aggression_dashboard_v2.app --port 5052` |

| `GET /api/config` | Output | JSON | Models, dates, model metrics, holdout rules, conditional rules, explainer markdown | Used on page load |

| `GET /api/research` | Input | query params | `model=<model>&date=<YYYY-MM-DD>` | `/api/research?model=ridge&date=2026-06-05` |

| aggression_v2_dash.series | Output | list[dict] | `{x, price, fair_value, residual, residual_z}` rows for selected model/date | Actual vs fair value and residual plots |

| aggression_v2_dash.price_display | Output | UI formatting | Price/fair-value chart axes and hovers render Treasury 32nds (`XX-NN`, `XX-NN+`); live banner also shows decimal price | `109-20+ (109.640625)` |

| aggression_v2_dash.drivers | Output | list[dict] | Standardized coefficients or feature importances for selected model | Driver bar chart |

| `GET /api/live_signal` | Output | JSON | Live prior-blended causal residual-z state, fair-value series, active alert, alert stats/hold expectations, benchmark, feed/model status | Polled by V2 live signal UI |

| `GET /api/signal_status` | Output | JSON | Feed freshness and model fit metadata | Health/status checks |

| aggression_v2_live.state | Output | str | `NONE`, `WATCH`, `DISLOCATION`; `WATCH` at `|z|>=1.5`, `DISLOCATION` at `|z|>=2.0` | Live alert banner |

| aggression_v2_live.success | Output | bool/count | Alert resolves if `|z|<1.0` within 30 minutes | Alert stats panel |

| `GET /api/alert_log` | Output | JSON | `scope=today|all`; returns alert episodes/outcome stats and benchmark | `/api/alert_log?scope=all` |

| aggression_v2_live.alert_log | Output | CSV | Cumulative finalized paper-trading outcomes | `workspace/analysis/aggression_v2_live/alert_log.csv` |

| aggression_v2_live.signal_log | Output | CSV | Daily minute-level live model audit rows `{ts, price, fair_value, residual, residual_z}` | `workspace/analysis/aggression_v2_live/signal_log_20260610.csv` |

| aggression_v2_live.dir_pnl_30m | Output | float | `direction_sign * (price_t+30m - open_price)`; positive means fade paid | Per-alert outcome |

| aggression_v2_live.adverse_30m | Output | float | Worst path PnL over 30m from the fade perspective | Per-alert risk metric |

| aggression_v2_live.mae_to_resolve_ticks | Output | float \| None | Worst adverse price move from alert open to z-resolution/finalization, in 1/64 TY ticks | Hold Expectations card |

| aggression_v2_live.price_share_of_reversion | Output | float \| None | Share of the opening residual gap closed by price movement; clipped to `[-1, 2]`; skipped for gaps `< 1 tick` | `% via price` table column |

| aggression_v2_live.z_method | Output | str | `prior_blend_v1` for new rows; older rows may be missing/legacy expanding z | Alert log segmentation |



### Aggression V2/V3 Depth Research (`scripts/aggression_v2_depth_research.py`)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| aggression_v2_depth.mbp10_dir | Input | Path | Existing directory of ZN MBP-10 daily parquet files | `D:\FRDBs\FutMBP\ZN_outrights_mbp10` |
| aggression_v2_depth.prebuilt_dir | Input | Path | Optional prebuilt event-level feature parquet directory | `D:\FRDBs\FutMBP\research_features` |
| aggression_v2_depth.feature_matrix | Input | Path | Existing V2 TBBO feature matrix CSV | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` |
| aggression_v2_depth.predictions | Input | Path | Existing V2 walk-forward predictions CSV | `workspace/analysis/aggression_v2_tbbo_models/walkforward_predictions.csv` |
| aggression_v2_depth.total_depth_10 | Output | float | Mean one-minute bid+ask size summed across 10 MBP levels | Depth-vol / V3 depth panel research |
| aggression_v2_depth.depth_z | Output | float \| None | 10-level depth vs trailing 60-minute mean/std | Time-adjusted thin-depth regime |
| aggression_v2_depth.quote_ofi_1m | Output | float | One-minute book-update order-flow imbalance; prebuilt OFI where available, raw depth-change fallback otherwise | Depth model lift test |
| aggression_v2_depth.cancel_to_add | Output | float \| None | One-minute cancel quantity divided by add quantity | Liquidity withdrawal proxy |
| aggression_v2_depth.sweep_count | Output | int/float | One-minute sweep/trade-count proxy | Liquidity stress proxy |
| aggression_v2_depth.outputs | Output | CSV/Markdown/JSON | Artifacts under `workspace/analysis/aggression_v2_depth/` plus `docs/aggression_v2_depth_research.md` | Gate 1 research review |
| aggression_v2_depth.gamma_bid_score | Output | int | Composite 0-5 stress score from low `depth_z`, depth depletion, high cancel/add, high trailing vol, and high sweep count | Phase 1.5 gamma-bid diagnostic |
| aggression_v2_depth.refill_regime | Output | str | `fast_refill` or `slow_refill` after bottom-quintile depth-z event | Liquidity replenishment diagnostic |
| aggression_v2_depth.ofi_trade_regime | Output | str | `agree`, `diverge`, `book_only`, `trade_only`, `quiet` | OFI-vs-trade divergence diagnostic |
| aggression_v2_depth.phase15_outputs | Output | CSV | Additional diagnostics under `workspace/analysis/aggression_v2_depth/phase15/` | `gamma_bid_score.csv`, `top_vs_deep.csv` |



### Aggression V3 Trade-to-trade Monitor (`scripts/aggression_dashboard_v3/app.py`)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| aggression_v3_dash.port | Input | int | Default `5053` | `py -3 -m scripts.aggression_dashboard_v3.app --port 5053` |
| `POST /api/aggression` | Output | JSON | Historical rebuild, or the shared live snapshot if one is ready | Historical UI only; live reads the stream |
| `GET /api/live/stream` | Output | SSE | `snapshot` / `trade` / `history` / `beta` / `status` / `loading` | Live V3 viewer; same bytes to every tab |
| aggression_v3.live_clock | Constant | str | trade print (`action == "T"`) | One tick per trade; quotes do not add a point |
| BANNER_SERIES_MAX_POINTS | Constant | int | `4000` | V3 hover-banner sample cap |
| aggression_v3.flow_dominance.hover_option | Input | bool | Local UI checkbox, default on; not sent in `/api/aggression` | `#banner-on-hover` |
| aggression_v3.flow_dominance.series | Output | list[dict] | `{x,status,state,headline,reason,contribution_line,as_of,d_ticks,a_ticks,b_ticks}` at sampled prints | Chart hover banner |
| panels.p1_states.data | Output | list[dict] | `{x0,x1,state,headline,color}` READY runs only | Compact six-state strip under V3 Panel 1; WARMING/UNAVAILABLE omitted |
| aggression_v3.banner.state | Output | str | `QUIET`, `BALANCED`, `AGGRESS_DOMINANT`, `IMPLIED_PASSIVE_DOMINANT`, `JOINTLY_DRIVEN`, `CONTESTED`, `WARMING`, `UNAVAILABLE` | Top banner headline |
| aggression_v3.beta_ref | Output | float \| None | Price-unit mean of last 10 completed-session `ΔP` on `ΔA` slopes | Banner, Panel 4, Panel 5 |
| aggression_v3.beta_display_yield_bp_per_1m | Output | float \| None | `-β_price * 1e6 * point_value / DV01_c` | Banner reference-beta line |
| aggression_v3.tau | Constant | float | TU `1/256`, FV `1/128`, TY `1/64`, US `1/32` | Selected price-symbol tick |
| aggression_v3.live_store | Output | directory | Isolated writes; old logs kept | `workspace/analysis/aggression_v3_live/` |
| FlowEffortConfig.effort_window | Constant | int | Default `5`, minutes; `> 0` | Rolling flow/price window for effort per tick |
| FlowEffortConfig.range_window | Constant | int | Default `30`, minutes; `> 0` | Frozen-range detection lookback |
| FlowEffortConfig.range_max_ticks | Constant | float | Default `4.0`, ticks; `> 0` | Max chop-band width for range state |
| FlowEffortConfig.effort_ratio_breakout | Constant | float | Default `0.6`; `0 < value < 1` | Breakout ease threshold |
| aggression_effort_range.output_dir | Output | directory | CSV/JSON research artifacts | `workspace/analysis/aggression_effort_range/` |
| aggression_v3_options.gamma_db | Input | SQLite path | Opened read-only with `file:...?mode=ro`; default `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db` | `scripts/aggression_v3_options_context_research.py --gamma-db ...` |
| aggression_v3_options.rr_metrics | Output | float \| None | Front-weighted `rr25`, `rr50`, `rr75` plus 5m/15m/30m changes | Lead/lag and residual-fade conditioning tests |
| aggression_v3_options.gamma_near_spot | Output | float \| None | Sum of absolute `dv01_gamma` within +/- N ticks of futures price | Gamma pin / exhaustion hypothesis |
| aggression_v3_options.output_dir | Output | directory | Read-only research artifacts | `workspace/analysis/aggression_v3_options_context/` |
| aggression_v3_options.stats | Output | CSV | Test rows with `test`, `horizon`, `n`, `hit_rate`, `avg_signed_move`, `median_signed_move`, `avg_abs_move` | `options_context_stats.csv` |
| aggression_v2_options.gamma_db | Input | SQLite path | Opened read-only with `file:...?mode=ro`; default `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db` | `scripts/aggression_v2_options_context_research.py --gamma-db ...` |
| aggression_v2_options.gamma_cache | Input | CSV path \| None | Optional cache of prior read-only gamma metrics; default `workspace/analysis/aggression_v3_options_context/options_context_joined.csv` | Speeds up V2 options experiments |
| aggression_v2_options.output_dir | Output | directory | Read-only research artifacts | `workspace/analysis/aggression_v2_options_context/` |
| aggression_v2_options.stats | Output | CSV | Track A/B/C/D rows with `track`, `test`, `baseline`, `horizon`, `n`, hit/resolution/adverse fields and deltas | `v2_options_context_stats.csv` |
| aggression_v2_options.report | Output | Markdown | Explained-results report; no dashboard build implied | `docs/aggression_v2_options_experiments.md` |
| aggression_v2_skew_corr.v2_input | Input | Path | Existing read-only 1-minute joined CSV | `workspace/analysis/aggression_v2_options_context/v2_options_minute_joined.csv` |
| aggression_v2_skew_corr.v3_input | Input | Path | Existing read-only cross-check CSV | `workspace/analysis/aggression_v3_options_context/options_context_joined.csv` |
| aggression_v2_skew_corr.metrics | Constant | tuple[str, ...] | `rr25`, `rr50`, `rr75`, `skew_index` | Skew metrics |
| aggression_v2_skew_corr.horizons | Constant | tuple[int, ...] | `(1, 5, 15, 30)` minutes | Contemporaneous change windows |
| aggression_v2_skew_corr.lags | Constant | tuple[int, ...] | `(-30,-15,-10,-5,-1,0,1,5,10,15,30)`; positive means skew leads price | Lead/lag grid |
| aggression_v2_skew_corr.output_dir | Output | Path | `workspace/analysis/aggression_v2_skew_price_corr/` | Correlation CSVs, summary JSON, heatmap |
| aggression_v2_skew_corr.report | Output | Markdown | Correlation readout; no signal implied | `docs/aggression_v2_skew_price_corr_experiments.md` |
| aggression_v2_skew_leg.gamma_db | Input | Path | Existing SQLite opened with `mode=ro` only | `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db` |
| aggression_v2_skew_leg.moneyness | Constant | tuple[int, ...] | `(25, 50, 75)`; 50 primary | Matched call/put IV legs |
| aggression_v2_skew_leg.z_threshold | Constant | float | `1.5`, causal session-expanding | Shock event threshold |
| aggression_v2_skew_leg.cooldown | Constant | int | `30` minutes per arm | Event de-clustering |
| aggression_v2_skew_leg.arms | Output | str | `PUT_BID_DOWN`, `CALL_BID_UP`, `PUT_SOFTEN_UP`, `CALL_SOFTEN_DOWN` | Single-leg event classifications |
| aggression_v2_skew_leg.output_dir | Output | Path | `workspace/analysis/aggression_v2_skew_leg_events/` | Panel, events, stats, diagnostics, summary, plot |
| aggression_v2_skew_leg.report | Output | Markdown | Holdout/FDR event-study verdict | `docs/aggression_v2_skew_leg_events_experiments.md` |
| aggression_v2_shock_paths.formation | Constant | int | `15` trailing minutes; causal z uses history through t-1 | Shock formation window |
| aggression_v2_shock_paths.z_threshold | Constant | float | `1.5` plus selection p60 absolute floor | Shock trigger |
| aggression_v2_shock_paths.entry | Internal | timestamp | Shock end +1 minute | Prevents pre-signal outcome leakage |
| aggression_v2_shock_paths.horizons | Constant | tuple | `1,5,15,30,60,120,240,480,1440m,RTH_CLOSE,OVERNIGHT` | TY path outcomes |
| aggression_v2_shock_paths.classes | Output | str | `PRICE_QUIET_SHOCK`, `PRICE_LED_VOL`, `MIXED` | Reverse-causality classification |
| aggression_v2_shock_paths.output_dir | Output | Path | `workspace/analysis/aggression_v2_vol_skew_shock_paths/` | Panel, events, outcomes, controls, stats, summary, heatmap |
| aggression_v2_shock_paths.report | Output | Markdown | Direction/magnitude/range/path verdict | `docs/aggression_v2_vol_skew_shock_paths_experiments.md` |
| aggression_v2_shock_persistence.retention | Output | float | Shock-direction-aligned excess divided by absolute initial shock size | 1m–24h option persistence |
| aggression_v2_shock_persistence.half_life | Output | float \| None | First calendar minute retention <=0.5; censored at 24h | Mean-reversion speed |
| aggression_v2_shock_persistence.full_reversion | Output | float \| None | First calendar minute retention <=0; censored at 24h | Full reversion time |
| aggression_v2_shock_persistence.output_dir | Output | Path | `workspace/analysis/aggression_v2_option_shock_persistence/` | Rows, stats, half-life, TY links, correlations, summary, plot |
| aggression_v2_shock_persistence.report | Output | Markdown | Persistence/half-life and TY-path verdict | `docs/aggression_v2_option_shock_persistence_experiments.md` |
| aggression_v2_skew_iterative.iterations | Constant | int | Maximum `5`, followed by one locked FINAL evaluation | Hypothesis/test/learn cycle |
| aggression_v2_skew_iterative.final_split | Internal | list[str] | Latest ~20% option-active dates; outcomes untouched unless validation candidate qualifies | `split_manifest.json` |
| aggression_v2_skew_iterative.taxonomy_grid | Constant | product | formation `{5,15,30}`, z `{1,1.5,2}`, cooldown `{15,30,60}`, quiet quantile `{.4,.5,.6}` | Iteration 1 |
| aggression_v2_skew_iterative.output_dir | Output | Path | `workspace/analysis/aggression_v2_skew_iterative/` | Panel, iteration artifacts/journal, leaderboard, locked final, summary, plot |
| aggression_v2_skew_iterative.report | Output | Markdown | Five-iteration failed-test audit and final decision | `docs/aggression_v2_skew_iterative_experiments.md` |
| TY_OPTION_FLOW_CACHE_DIR | EnvVar | Path | Optional local compact trade-only cache override | Default `~/.market_data/ty_option_flow_cache/` |
| TY_OPTION_PARQUET_DIR | EnvVar | Path | Optional local historical option parquet override | Default `D:\BigMarketDataParquet\OptionsMBP1_v2` |
| aggression_v2_option_flow.api | Input | URL | Read-only `http://100.79.30.21:8001/historical/options` | Hourly Arrow IPC fetch |
| aggression_v2_option_flow.local_source | Input | Path | Read-only daily parquet queried by DuckDB predicate pushdown | `--source local` |
| aggression_v2_option_flow.filters | Internal | rule | `action=T`, ZN underlying, OZN/ZN/VY/WY/GY/HY families, DTE<=20 | Trade-only compact cache |
| aggression_v2_option_flow.aggressor | Output | int | `+1` buy, `-1` sell, `0` unknown; BBO primary, side fallback | Signed option flow |
| aggression_v2_option_flow.greeks | Output | float | Native `delta_F_mid`, `vega_F_mid`, `gamma_F_mid` where available | Greek-weighted flow |
| aggression_v2_option_flow.output_dir | Output | Path | `workspace/analysis/aggression_v2_option_flow/` | Audit, sample features, gated experiment files, summary, plot |
| aggression_v2_option_flow.report | Output | Markdown | Phase-0 data/transfer gate and research status | `docs/aggression_v2_option_flow_experiments.md` |
| aggression_v2_nonflow.event_window_minutes | Constant | int | `30` minutes before/after US event | EVENT_NEAR modifier |
| aggression_v2_nonflow.magnitude_state | Internal | bool | Prior realized vol >= selection p70 OR prior 10m range >= selection p70 | Non-directional move/resolution context |
| aggression_v2_nonflow.quality_stack | Internal | bool | Top tercile of available modifier share, at least 2 components | Composite robustness modifier |
| aggression_v2_nonflow.output_dir | Output | Path | `workspace/analysis/aggression_v2_nonflow_context/` | Episode table, modifier stats, summary, ranking plot |
| aggression_v2_nonflow.value_verdict | Output | str | `YES` or `NO`; YES may still be non-directional only | `nonflow_context_summary.json` |
| aggression_v2_nonflow.report | Output | Markdown | Phase 1 value-check; no dashboard authorization | `docs/aggression_v2_nonflow_context_experiments.md` |
| aggression_v25_dash.port | Input | int | Proposed `5055`; port now used by isolated V1.3. Do not build V2.5 on 5055 | V2.5 placeholder guardrail |
| aggression_v21_dash.port | Input | int | Proposed `5054`, but not built after 2026-07-08 trend/range gate fail and 2026-07-09 convergence sensitivity fail; requires future pass + user approval | V2.1 placeholder guardrail |
| aggression_v2_convergence.walkforward | Input | Path | Default `workspace/analysis/aggression_v2_tbbo_models/walkforward_predictions.csv` | `py -3 scripts/aggression_v2_convergence_research.py` |
| aggression_v2_convergence.output_dir | Input | Path | Default `workspace/analysis/aggression_v2_convergence/` | Gate JSON + fade/formation CSVs |
| aggression_v2_convergence.cost_ticks | Constant | float | Default `0.5` round-trip | After-cost expectancy bar |
| formation PRICE_LED / FLOW_LED | Internal | str | Prior-window `|Δprice|` vs `|ΔFV|` dominance | Convergence research formation tag |
| aggression_trend_range.features | Input | Path | Default `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` | `py -3 scripts/aggression_trend_range_research.py` |
| aggression_trend_range.output_dir | Input | Path | Default `workspace/analysis/aggression_trend_range/` | Gate JSON + regime/trader CSVs |
| aggression_v2_resolution.alert_log | Input | Path | Default `workspace/analysis/aggression_v2_live/alert_log.csv` (read-only) | `py -3 scripts/aggression_v2_resolution_diagnostics_research.py` |
| aggression_v2_resolution.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_resolution/` | Episodes, bucket diagnostics, holdout scores, gate JSON |
| aggression_v2_resolution.cost_ticks | Constant | float | `0.5` round-trip | After-cost lift gate |
| aggression_v2xv3_confluence.alert_log | Input | Path | V2 alert log (read-only) | `py -3 scripts/aggression_v2xv3_confluence_research.py` |
| aggression_v2xv3_confluence.v3_live_dir | Input | Path | Default `workspace/analysis/aggression_v3_live/` (read-only regime logs) | Join V3 flow_effort tags |
| aggression_v2xv3_confluence.output_dir | Output | Path | Default `workspace/analysis/aggression_v2xv3_confluence/` | Cell stats + gate JSON |
| aggression_v2_magnitude.move_ticks | Constant | float | Default `5.0` absolute price move in 30m | Binary magnitude target |
| aggression_v2_magnitude.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_magnitude/` | Episodes, holdout scores, gate JSON |
| aggression_v2_standaside.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_standaside/` | Toxic filter episodes, holdout scores, gate JSON |
| aggression_v2_standaside.aside_percentile | Internal | float | Top 30% toxic probability = stand-aside set | Gate: aside ≤ −0.5 ticks after cost; keep > 0 |
| aggression_v2_short_horizon_fv.input | Input | Path | Default `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` (read-only) | `py -3 scripts/aggression_v2_short_horizon_fv_research.py` |
| aggression_v2_short_horizon_fv.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_short_horizon_fv/` | Metrics, predictions, Track C grid, summary JSON, plots |
| aggression_v2_short_horizon_fv.report | Output | Markdown | Isolation, grids, holdout tables, sweet-spot, no ship gate | `docs/aggression_v2_short_horizon_fv_experiments.md` |
| aggression_v2_short_horizon_fv.track_a_horizons | Constant | tuple[int, ...] | Core `(3, 5, 8, 10)` plus diagnostic `(1, 15)` minutes, same ET date | Forward target `price_{t+h}-price_t` |
| aggression_v2_short_horizon_fv.track_b_anchors | Constant | tuple[int, ...] | `(3, 5, 8, 10, 15, 30)` minutes; `W=5` leaked by `mom_5m` | Local target `price_t-price_{t-W}` |
| aggression_v2_short_horizon_fv.hysteresis | Constant | enter/exit/min-hold | enter `{1.0,1.5,2.0}`, exit `{0.5,1.0}`, min-hold `{0,5}` | Track C display-rule pack |
| aggression_v2_short_horizon_fv.deadzone_ticks | Constant | float | `1.0` tick raw-sign dead zone | Flat lean, not a flip |
| aggression_v2_short_horizon_fv.adverse_tick_cap | Constant | float | `3.0` ticks at 5m and 10m | Hold-profile path score |
| aggression_v2_short_horizon_fv.sweet_duration | Constant | tuple[float, float] | `[5, 15]` minutes | Sweet-spot lean duration band |
| aggression_v2_alert_clocks.oos | Input | Path | `workspace/analysis/aggression_v2_short_horizon_fv/oos_predictions.csv` B1_W10 rows | Residual/z/price for replay |
| aggression_v2_alert_clocks.features | Input | Path | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` (read-only) | `trade_intensity`, `avg_trade_size` |
| aggression_v2_alert_clocks.output_dir | Output | Path | `workspace/analysis/aggression_v2_alert_clocks/` | Metrics, episodes, usable ranking, summary |
| aggression_v2_alert_clocks.report | Output | Markdown | Isolation, 38-clock grid, usable holdout table, no ship gate | `docs/aggression_v2_alert_clock_experiments.md` |
| aggression_v2_alert_clocks.wall_minutes | Constant | tuple[int, ...] | `(3, 5, 8, 10, 15, 20, 30)` | Wall-time deadlines |
| aggression_v2_alert_clocks.print_targets | Constant | tuple[int, ...] | `(25, 50, 100, 150, 250, 400, 800, 1500)` | Prints after open bar |
| aggression_v2_alert_clocks.volume_targets | Constant | tuple[int, ...] | `(100, 250, 500, 1000, 2000, 4000, 8000)` | Contract-volume proxy after open |
| aggression_v2_alert_clocks.enter_exit | Constant | floats | enter `1.5`, exit `0.5`, min-hold `5` minutes | Same as live V2.7; only deadline varies |
| aggression_v2_executable_pnl.output_dir | Output | Path | `workspace/analysis/aggression_v2_executable_pnl/` | Today tape, holdout grid, TBBO cache |
| aggression_v2_executable_pnl.oos | Input | Path | `workspace/analysis/aggression_v2_short_horizon_fv/oos_predictions.csv` | `session` and `B1_W10` constructions |
| aggression_v2_executable_pnl.features | Input | Path | `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` | V1 reconstruct (`agg_1m` / `agg_cum_daily`) |
| aggression_v2_executable_pnl.v2_live | Input | Path | `workspace/analysis/aggression_v2_live/signal_log_*.csv` (read-only) | Session residual for today replay |
| aggression_v2_executable_pnl.v27_live | Input | Path | `workspace/analysis/aggression_v27_live/signal_log_*.csv` (read-only) | Local B1_W10 residual for today |
| aggression_v2_executable_pnl.api_base | Constant | str | `http://100.79.30.21:8001` | AccessAPI historical TBBO + `/db/date-range` |
| aggression_v2_executable_pnl.G | Constant | float | today `2.0`; grid `{1,2,3,4}` ticks | Session residual gap |
| aggression_v2_executable_pnl.N_X | Constant | float | today `2.0`; grid `{1,2,3}` ticks | Take-profit / stop |
| aggression_v2_executable_pnl.cost | Constant | float | `0.5` tick round-trip | After-cost official PnL |
| aggression_v2_executable_pnl.clock_label | Constant | str | `NOT_MBP1_L1` | Print and mid-change clocks |
| aggression_v2_executable_pnl.report | Output | Markdown | Isolation, today slices, holdout grid, correction cells, no live retune | `docs/aggression_v2_executable_pnl_experiments.md` |
| aggression_v2_executable_pnl.corrections_dir | Output | Path | `workspace/analysis/aggression_v2_executable_pnl/corrections/` | One-change-per-cell paper tape + L1 autopsy |
| aggression_v2_executable_pnl.n_trades | Output | int | filled count | I8: not the TP parameter |
| aggression_v2_executable_pnl.n_tp_ticks | Output | float | barrier +N | I8 rename; do not overwrite `n` |
| aggression_v2_executable_pnl.n_sl_ticks | Output | float | barrier −X | I8 |
| aggression_v2_executable_pnl.pnl_mid | Output | float \| None | mid ticks from ask/bid fill | C3 diagnostic |
| aggression_v2_executable_pnl.exit_space | Input | str | `bidask` \| `mid` | I1 mid-space SL/TP |
| aggression_v2_executable_pnl.block_same_sign_reentry | Input | bool | default false | I2 |
| aggression_v2_executable_pnl.apply_passive_increasing_veto | Input | bool | default false | I7 |
| aggression_v2_executable_pnl.L1_session | Constant | str | `2026-09-01 18:00:00` ET | Live V2.7 autopsy window |
| aggression_v2_forward.features | Input | Path | Default `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` (read-only) | `py -3 scripts/aggression_v2_forward_return_research.py` |
| aggression_v2_forward.predictions | Input | Path | Default `workspace/analysis/aggression_v2_tbbo_models/walkforward_predictions.csv` (read-only Ridge predictions) | Reconstruct residual/fair-value features |
| aggression_v2_forward.horizons | Constant | tuple[int, ...] | `(1, 3, 5, 15, 30)` minutes, within ET date only | Forward-return targets |
| aggression_v2_forward.deadzone_ticks | Constant | float | `0.5` tick direction dead-zone | Logistic direction target excludes small moves |
| aggression_v2_forward.cost_ticks | Constant | float | `0.5` round-trip | After-cost expectancy |
| aggression_v2_forward.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_forward_return/` | Dataset, predictions, model metrics, regime stats, gate JSON, plots |
| aggression_v2_forward.report | Output | Markdown | Readout and verdict; no dashboard build implied | `docs/aggression_v2_forward_return_experiments.md` |
| aggression_v2_ev.dataset | Input | Path | Default `workspace/analysis/aggression_v2_forward_return/forward_return_dataset.csv` (read-only) | `py -3 scripts/aggression_v2_ev_selectivity_research.py` |
| aggression_v2_ev.predictions | Input | Path | Default `workspace/analysis/aggression_v2_forward_return/forward_return_predictions.csv` (read-only) | Per-minute forward predictions |
| aggression_v2_ev.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_ev_selectivity/` | EV scores, stats, gate JSON, summary |
| aggression_v2_ev.ev_score | Internal | float | `P(big_move) * predicted_abs_move * direction_confidence - 0.5` ticks | Select top-EV holdout rows |
| aggression_v2_meta.alert_log | Input | Path | Default `workspace/analysis/aggression_v2_live/alert_log.csv` (read-only) | Existing V2 finalized alert outcomes |
| aggression_v2_meta.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_meta_label/` | Dataset, holdout scores, gate JSON, summary |
| aggression_v2_meta.y_profitable | Internal | int | `1` when `dir_pnl_30m / tick_size - 0.5 > 0`, else `0` | Take/skip meta-label |
| aggression_v2_event.events_db | Input | Path | Default `workspace/economic_events.db` (read-only) | US release timestamps |
| aggression_v2_event.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_event_window/` | Event rows, stats, gate JSON, summary |
| aggression_v2_event.first5_flow_sign | Internal | float | Sign of first-5-minute post-release `agg_1m` sum | Flow-follow direction |
| aggression_gate.n_boot | Constant | int | `10000` deterministic bootstrap draws | Date-clustered confidence intervals |
| aggression_gate.bh_q | Constant | float | `0.10` | Benjamini-Hochberg FDR threshold across all tested cells |
| aggression_gate.costs | Constant | tuple[float, ...] | `(0.0, 0.25, 0.5, 1.0)` ticks | Cost sensitivity sweep |
| aggression_gate.tier | Output | str | `PASS`, `PROVISIONAL`, or `FAIL` | Statistical cell verdict |
| aggression_gate.pass | Internal | bool rule | `n>=30`, BH significant, one-sided lower bound `>=0`, context stability passes | Dashboard consideration still requires explicit approval |
| aggression_v2_gate_reeval.output_dir | Output | Path | Default `workspace/analysis/aggression_v2_gate_reeval/` | `reeval_cells.csv`, `reeval_summary.json` |
| aggression_v2_gate_reeval.report | Output | Markdown | Original-vs-statistical verdict comparison | `docs/aggression_v2_gate_reeval_experiments.md` |
| aggression_v2_event_lock.entry | Internal | str | Signal locks at release +5m; entry is observed T+5 price | Executable paper benchmark |
| aggression_v2_event_lock.horizons | Constant | tuple[int, ...] | `(5, 15, 30)` minutes after lock | Post-lock outcomes |
| aggression_v2_event_lock.output_dir | Output | Path | `workspace/analysis/aggression_v2_event_window_lock/` | Rows, stats, gate, summary |
| aggression_v21.port | Input | int | Default `5054`; manual launch only | `py -3 -m scripts.aggression_dashboard_v21.app --port 5054` |
| aggression_v21.store | Output | Path | `workspace/analysis/aggression_v21_live/` only | Event episodes/log and V2.1 signal audit |
| aggression_v21.strategy_status | Output | str | Fixed `PROVISIONAL / RESEARCH-ONLY` | UI/API badge |
| aggression_v21.event_phase | Output | str | `COLLECTING`, `LEAN_LOCKED`, `NO_SIGNAL`, `SCORING`, `FINALIZED` | `/api/event_flow` |
| aggression_v21.event_lean | Output | str | `LONG`, `SHORT`, `NO_SIGNAL`, or null | First-five-minute flow sign |
| aggression_v21.event_log | Output | CSV | Append-once finalized paper episodes | `workspace/analysis/aggression_v21_live/event_paper_log.csv` |
| aggression_v21.magnitude_state | Output | str | `ELEVATED` or `NORMAL`; display-only/non-directional | `/api/live_signal.magnitude_context` |
| aggression_v21.magnitude_vol_threshold | Constant | float | `0.0129490926588241` from Phase-1 selection p70 | `magnitude_context.py` |
| aggression_v21.magnitude_range_threshold | Constant | float | `3.0` TY ticks from Phase-1 selection p70 | `magnitude_context.py` |
| aggression_v21.magnitude_lookback | Constant | int | `15` one-minute rows (legacy research fields were named prior10) | Causal live pre-window |
| aggression_v21.magnitude_label | Output | str | Fixed `PROVISIONAL / NON-DIRECTIONAL` | Magnitude-confidence card |
| `GET /api/event_flow` | Output | JSON | Feed/DB health, upcoming releases, active/recent episodes, paper stats | V2.1 only |
| `GET /api/event_log` | Output | JSON | `scope=today|all`; isolated finalized paper rows | V2.1 only |
| effort_per_tick | Internal | float | Rolling \|signed aggress\| / \|price ticks\| over effort window | Trader effort proxy |
| large_trade_share | Internal | float | Share of minute volume from large prints | Primary block-trade proxy |
| large_share_vs_session | Internal | float | 5m large_trade_share / session expanding median | Relative block intensity |
| avg_trade_size | Internal | float | Volume-weighted average trade size | Secondary block proxy |
| two_sidedness | Internal | float | `0.5 * (1 - |buysell_ratio|/100)` ≈ min(buy,sell)/(buy+sell) | Secondary tape-balance proxy |

### Aggression Live Feed Failover

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| AGG_BACKUP_RECOVER_SEC | EnvVar | float | `> 0`; default `60.0` seconds of sustained healthy AccessAPI records | `set AGG_BACKUP_RECOVER_SEC=60` |
| AGG_BACKUP_STALE_SEC | EnvVar | float | `> 0`; default `30.0` seconds without supported AccessAPI TBBO records | `set AGG_BACKUP_STALE_SEC=30` |
| aggression.live_buffer.backup_recover_sec | Input | float \| None | Constructor override `> 0`; otherwise env/default; V1 and V2.2 only | `LiveTradeBuffer(backup_recover_sec=10)` |
| aggression.live_buffer.backup_stale_sec | Input | float \| None | Constructor override `> 0`; otherwise env/default; V1 and V2.2 only | `LiveTbboBuffer(backup_stale_sec=5)` |
| aggression.live_status.failover | Output | dict | `{stale_after_sec,recover_after_sec,primary,backup}` | Returned by V1 `/api/live_status` and V2.2 `feed` |
| aggression.live_status.source | Output | str | `accessapi` or `databento_backup` | Drives active-source UI text |
| DatabentoLiveBackup.on_record | Input | Callable[[dict], None] | Receives normalized AccessAPI-compatible TBBO trade/BBO records | Dashboard buffer callback |
| align_flow_and_price.ffill_aggress | Input | bool | default `true` for OLS; panel 1 uses `false` | `align_flow_and_price(flow, price, rezero=False, ffill_aggress=False)` |
| align_flow_and_price.rezero | Input | bool | default `true` for OLS fits; panel 1 uses `false` | `align_flow_and_price(flow, price, rezero=False)` |
| DatabentoLiveBackup.start.replay_from | Input | datetime \| None | UTC-convertible; clamped to Databento's last 23h55m | Last accepted event timestamp |
| DatabentoLiveBackup.start.return | Output | bool | `true` when active/started; `false` only if a prior worker is still alive after `START_WAIT_STOPPING_SEC` | Parent source-switch guard |
| DatabentoLiveBackup.start.wait_stopping | Constant | float | `START_WAIT_STOPPING_SEC=15.0`; wait for a stopping worker before starting a new one | Restart after AccessAPI recovery flap |
| DatabentoLiveBackup.stop.join_timeout_sec | Input | float | default `DEFAULT_STOP_JOIN_SEC=3.0`; terminate is immediate | `backup.stop()` / `backup.stop(join_timeout_sec=1.0)` |
| V1._activate_backup.source | Internal | str | stays `accessapi` until `start()` returns true; then `databento_backup` | Prevents DATABENTO BACKUP DOWN during join |
| V1.live_rebuild_key | Internal | tuple | `(symbols, price_symbol, start_iso, bucket, dv01_adjust, lookback_hours, reset)` | Coalesce identical live `/api/aggression` rebuilds |
| V1.LIVE_RESULT_CACHE_TTL_SEC | Constant | float | `1.0` | Extra Tailscale/live tabs reuse last `/api/aggression` payload |
| V1._LIVE_RESULT_CACHE | Internal | dict[tuple, {result, ts}] | Keyed by `V1.live_rebuild_key`; `ts` is `time.monotonic()` | `_cached_live_aggression_http` |
| V1.LIVE_PUBLISH_SEC | Constant | float | `1.0` | Background session 1-minute ingest interval |
| V1.BACKFILL_TICK_TAIL_SEC | Constant | float | `1200` | Raw backfill ticks kept after fold into 1m bars |
| V1.P1_AXIS_DEADBAND | Constant | float | `0.02` of last aggress span | Live panel 1 holds y-range unless min/max move more |
| V1._SESSION_1M | Internal | dict | `{session_start, rebuild_from, refold_tail, bars, per_symbol, ready}` | Live HTTP reads 1m bars, not session ticks |
| V1.flow_history.load_completed_symbol_bars | Output | dict[str, DataFrame] | SYMBOL_BAR_COLUMNS; start inclusive, end exclusive | Live hole-heal from sqlite |
| V1.printed_minute_bars | Internal | DataFrame | `trade_count > 0` | Live upsert skips zero-print gap minutes |
| V1._TENDAY.bars | Internal | dict[str, DataFrame] | 1-minute bars per symbol; raw `trades` dropped after refresh | Panel 5 source |
| V1.flow_history.db_path | Constant | Path | `workspace/analysis/aggression_v1_live/flow_history.sqlite` | Durable 1m buy/sell store |
| V1.flow_history.buy_contracts | Output | float | ≥ 0 contracts | Aggress buy volume (took the ask) |
| V1.flow_history.sell_contracts | Output | float | ≥ 0 contracts | Aggress sell volume (hit the bid) |
| V1.flow_history.buy_flow | Output | float | ≥ 0 $DV01 | Aggress buy dollar DV01 |
| V1.flow_history.sell_flow | Output | float | ≥ 0 $DV01 | Aggress sell dollar DV01 |
| GET /api/flow_history.symbol | Input | str | `FV` `TU` `TY` `US` `CURVE` | Default `TY`; `CURVE` sums the four |
| GET /api/flow_history.start | Input | datetime \| None | NY naive or ISO | Inclusive lower bound |
| GET /api/flow_history.end | Input | datetime \| None | NY naive or ISO | Exclusive upper bound |
| flow_history_dash.port | Constant | int | `5064` | Isolated viewer only |
| flow_history_dash.tailscale | Constant | URL | `https://aasthajain.tail174727.ts.net:8446/` → `127.0.0.1:5064` | Do not reuse 8443/8444/8445 or default :443 |
| flow_history_dash.label | Constant | str | `DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL` | Header + APIs |
| GET /api/daily.symbol | Input | str | `FV` `TU` `TY` `US` `CURVE` | Default `TY` |
| GET /api/daily.family | Input | str | `all` `auction` `supply` `month_end` `tier1` `nfp` `cpi` `fomc` `mon`…`sun` | Day filter |
| GET /api/daily.rows | Output | list[dict] | ET date, weekday, buy/sell, day_ticks, day_bps, bps_per_mm, tags | Bid=took ask; ask=hit bid |
| flow_history_dash.MIN_ABS_NET_DV01 | Constant | float | `$250000` | Floor for daily bps_per_mm ratio |
| GET /api/diagnostic.bps_per_mm_distribution | Output | dict | per cohort `{n, mean, median, p25, p75, min, max}` | Daily signed bps / $1mm after floor |
| GET /api/today | Output | dict | `{today, analog, expected, so_far, row, feed}` | Re-folds the current session only; analog from cached closed days; LIVE badge included |
| flow_history_dash.LIVE_POLL_MS | Constant | int | `15000` | Browser poll of `/api/today` after first Load |
| supply_weeks_live.port | Constant | int | `5065` | Isolated live 2Y/5Y/7Y viewer |
| supply_weeks_live.label | Constant | str | `DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL` | Header + APIs |
| supply_weeks_live.WINDOW_NAMES | Constant | tuple[str] | `pre_2y` `2y_to_5y` `5y_to_7y` `post_7y` | Sequence windows from Sunday 18:00 / 2Y 11:30 / 5Y 13:00 / 7Y 13:00 / Fri 17:00 |
| supply_weeks_live.BPS_PER_POINT | Constant | float | `8` | TY: 4 ticks = 1 bp |
| GET /api/bootstrap.month_end | Input | str | `all` `month_end` `non_month_end` | Month-end type filter for OLS cards |
| GET /api/bootstrap | Output | dict | `{weeks, windows, compare, current, feed}` | Full closed-week cache + current week + auction vs non-supply |
| GET /api/live | Output | dict | `{windows, compare, current, feed}` | Re-enrich current week and today’s compare slice when 1m `MAX(ts_utc)` moves |
| supply_weeks_live.compare.slice | Constant | tuple | 2Y `from_8`=`[08:00,t)` `into`=`[t−3h,t)` `after`=`[t,17:00)`. 2Y `t` is fixed 13:00 ET | Per-tenor auction clocks |
| supply_weeks_live.distro | Constant | clock | 2Y date and 5Y auction date, each `08:00–13:00`, `10:00–13:00`, `13:00–17:00` | One measure control rebuilds all six histograms |
| supply_weeks_live.compare.control | Internal | set[date] | weekdays matching modal 2Y/5Y/7Y clock, excluding every 2/5/7 CSV auction date | Weekends dropped; early close stays 17:00 |
| GET /api/bootstrap.compare | Output | dict | `{clocks, rows[tenor][slice]{supply,control}}` | n plus p25/median/p75 buy, sell, net, bps; week month-end filters supply only |
| GET /api/live.drop_tier1 | Input | bool | `1`/`true` drops NFP/CPI/FOMC/PCE/GDP dates from non-supply | Same tags as flow-history 5064 |
| GET /api/live.drop_month_end | Input | bool | `1`/`true` drops last-3-session month-end dates from non-supply | Does not change the supply week filter |
| supply_weeks_live.typical_net | Output | float \| None | median of week window nets | Not median(bid) − median(ask) |
| supply_weeks_live.median_buy | Output | float \| None | median window buy $DV01 | Took the ask; n_bars>0 sample |
| supply_weeks_live.median_sell | Output | float \| None | median window sell $DV01 | Hit the bid; n_bars>0 sample |
| supply_weeks_live.residual_bps | Output | float \| None | window_bps − intercept − slope_mm × net / 1e6 | Leftover vs OLS, not book identity |
| supply_weeks_live.slope_bps_per_mm | Output | float \| None | OLS `window_bps ~ intercept + slope × net` × 1e6 | bps / $1mm |
| supply_weeks_live.LIVE_POLL_MS | Constant | int | `15000` | Browser poll of `/api/live` |
| GET /api/today.today.considered | Output | list[dict] | always `auction` `month_end` `tier1` `weekday` with `{on, label, detail}` | Written every session |
| flow_history_dash.MIN_ANALOG_DAYS | Constant | int | `3` | Prefer a tighter tag set only when n meets this |
| flow_history_dash.session | Constant | clock | Sun 18:00–Mon 17:00 ET = Monday; closed Fri 17:00–Sun 18:00; halt Mon–Thu 17:00–18:00 | Daily buckets and today analog |
| GET /api/status.feed | Output | dict | `LIVE` `NOT_LIVE` `HALT` `CLOSED` plus session_date, last_bar_et | Last 1m bar age vs 180s while OPEN; `MAX(ts_utc)` only |
| GET /api/bootstrap | Output | dict | `{rows, diagnostic, today, analog, expected, so_far, feed}` | One daily fold plus analog/LIVE; page Load uses this |
| GET /api/intraday.date | Input | date | `YYYY-MM-DD` ET | Click-through session |
| GET /api/diagnostic.cohorts | Output | dict | `all`/`auction`/`month_end`/`tier1`/`nfp`/`cpi`/`fomc` plus weekdays with bid_gt_ask / ask_gt_bid stats | Counts only |
| GET /api/diagnostic.bps_per_mm_dv01 | Output | dict | `all`/`auction`/`month_end`/`tier1` → `{n, slope_bps_per_mm, intercept_bps}` | OLS day bps on net $DV01; slope × 1e6 |
| flow_history_dash.EVENTS_DB | Constant | Path | Existing SQLite; no longer the default source (only an explicit `db_path=` reads SQLite) | `Y:\Yaman\Fundamentals\Economic Events\Data\events.sqlite` |
| ECON_EVENTS_PG_DSN | EnvVar | str (secret) | libpq URI for role `economic_events_reader`; environment first, else `config/keys.env` (gitignored); hosts LAN first, Tailscale second; never `localhost` | `postgresql://economic_events_reader:***@172.30.86.68:5432,100.79.30.21:5432/economic_events` |
| EVENTS_CACHE_DIR | EnvVar | path | optional; default `%LOCALAPPDATA%\market_data_puller\cache` | Per-app last-good file `events_tagged_mdp_<port>.json` |
| flow_history_dash.EVENTS_REFRESH_SEC | Constant | float | `60.0` | Background Postgres refresh period |
| flow_history_dash.EVENTS_COLD_WAIT_SEC | Constant | float | `5.0` | Longest a request waits for the first load before using the last-good file (or `[]`) |
| start_events_cache(app_name) | Input | str | `mdp_<port>`; names the Postgres session (`application_name`) and the cache file | 5064/5065 `main()` |
| events_from_pg_rows(rows) | Internal | list[tuple] -> list[dict] | rows `(parent_event, event_name, country_name, release_date, release_date_time, event_id)`; sorted by (release_date, parent_event, event_id) to match the SQLite reader exactly | Parity with `events.sqlite` proven 2026-09-24 (2,124 events) |
| GET /api/status.events (5064); /api/bootstrap.events, /api/live.events (5065) | Output | dict | `{ok, last_ok_utc, age_sec, rows, host, table_rows, max_event_id, calendar_horizon, latest_actual_et, feed_age_days, last_error, fallback, source}` | `feed_age_days` grows until Yaman's pipeline writes Postgres |
| EVENTS_GOLDEN_SQLITE | EnvVar | path | default `C:\dev\mdp_golden\events\events_20260923_1226.sqlite` (the file loaded into Postgres) | `tests/test_events_pg_cache.py` parity |
| EVENTS_PG_PARITY | EnvVar | `1` or unset | opt-in live Postgres parity test | `EVENTS_PG_PARITY=1 pytest tests/test_events_pg_cache.py` |
| flow_history_dash.backfill.start | Constant | date | `2022-01-03` | First weekday session pulled from AccessAPI |
| flow_history_dash.backfill.seam | Constant | datetime | `2026-06-12 05:00 UTC` | Do not overwrite V1 cache/live bars |
| flow_history_dash.backfill.source | Constant | str | `accessapi_2022` | `v1_aggress_flow_1m.source` for rebuilt minutes |
| flow_history_dash.MONTH_END_DAYS | Constant | int | `3` | Trailing business days of a complete month |
| DatabentoLiveBackup.status | Output | dict | `{running,connected,error,last_record_ts,replay_from,dataset,symbols}` | Nested under `failover.backup` |
| TbboEventDeduplicator.accept | Internal | bool | Bounded exact instrument/time/sequence/payload IDs plus per-instrument nanosecond receive-time replay floor | Source/reconnect boundary guard |
| test_databento_live_backup.--live-seconds | Input | float | `>= 0`; default `30`; `0` skips real live session | `py -3 scripts/test_databento_live_backup.py --live-seconds 30` |


### `config/aggression_dv01.json`



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| FV | Constant | float | `> 0`, dollars per bp per contract; V1 default `42.0` | `signed_flow = sign * size * 42.0` |

| TU | Constant | float | `> 0`, dollars per bp per contract; V1 default `36.0` | `signed_flow = sign * size * 36.0` |

| TY | Constant | float | `> 0`, dollars per bp per contract; V1 default `85.0` | `signed_flow = sign * size * 85.0` |

| US | Constant | float | `> 0`, dollars per bp per contract; V1 default `125.0` | `signed_flow = sign * size * 125.0` |



### `POST /api/aggression` Request



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression_symbols | Input | list[str] | Non-empty subset of `["FV","TU","TY","US"]`; default is all four | `["FV","TU","TY","US"]` |

| bucket | Input | str | Pandas resample frequency; V1 UI sends `"1min"` | `"1min"` |

| dv01_adjust | Input | bool | `true` standardizes flow by configured DV01, `false` sums contracts | `true` |

| end_iso | Input | str \| omitted | ISO datetime; omitted means server-side now; only honored in historical mode | `"2026-04-06T10:00:00-04:00"` |

| lookback_hours | Input | float | Display-only chart window; UI options `2`, `6`, `12`, `24`; default `2` | `2` |

| mode | Input | str | `"live"` (default) or `"historical"` | `"live"` |

| price_symbol | Input | str | One of `FV`, `TU`, `TY`, `US`; dependent price line/model target | `"FV"` |

| start_iso | Input | str | ISO datetime; naive browser value is interpreted as New York time. Live default is `trading_day_start()` (18:00 ET session open). | `"2026-04-06T18:00"` |

| trading_day_start | Internal | datetime | Most recent 18:00 ET ≤ now, returned in UTC | Monday 10:00 ET → Sunday 18:00 ET |

| default_start_time | Internal | datetime | Alias of `trading_day_start`; live window and backfill origin | HTML `datetime-local` default |

| universe | Input | str | V1 supports `"US"` | `"US"` |



### `POST /api/aggression` Response



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| meta.dv01_table | Output | dict[str, float] | Loaded from `config/aggression_dv01.json` | `{"FV":42,"TU":36,"TY":85,"US":125}` |

| meta.fetch_errors | Output | dict[str, str] | Empty or symbol→error message | `{"US":"Databento API fetch failed..."}` |

| meta.flow.buysell_ratio_smoothing | Output | str | Documents smoothing applied to plotted BuySell Ratio | `EMA(span=10) of per-bucket ratio` |

| meta.flow.flow_unit | Output | str | `"$DV01 per bp"` when DV01-adjusted, otherwise `"contracts"` | Displayed in diagnostics |

| meta.flow.per_symbol | Output | dict | Per symbol trade counts, buy/sell/unknown counts, DV01 used, and selected `front_contracts` | Used for diagnostics |

| meta.fair_value | Output | dict | `{intraday,tenday}` model metadata, including `ok`, `alpha`, `beta`, `r2`, `fit_ts`, `refit_cadence`, residual stats, and valid point counts | Footer shows intraday and 10D beta/R² |

| meta.display_lookback | Output | dict | `{hours, cutoff}` for the display-only window applied to panel rows | `{"hours":2,"cutoff":"..."}` |

| meta.hf_model | Output | dict | Backward-compatible alias for `meta.fair_value.intraday` | Legacy consumers |

| meta.live_status | Output | dict | Source freshness; includes `source`, `connected`, ages, `market_open`, `mode`, and nested `failover.primary/backup` | Drives source-named LIVE/STALE/DOWN/HISTORICAL banner |

| meta.price_source | Output | str | Historical values or `<accessapi\|databento_backup>_tbbo_live + databento_trade_backfill` | Live mode overlays active-source BBO mid on historical trade backfill |

| panels.p1.data | Output | list[dict] | `{x,total_aggress,price}` print-clock points | TotalAggress vs last traded price |

| panels.p1.axis_align | Output | dict | Empty or `{aggress_range:[float,float], price_range:[float,float], alpha:float, beta:float, formula:str, beta_ticks_per_1m_dv01:float}` | Sets panel-1 fair-value-aligned y-axis ranges |

| panels.p2.data | Output | list[dict] | `{x,buysell_ratio,price}` chart-only: time-weighted EWM of `buysell_raw` (half-life 30s), then ≤4000 points | BuySell Ratio vs Price/Yield; spec EMA(10) stays on the print frame |

| panels.p3.data | Output | list[dict] | `{x,buy_flow_bucket,sell_flow_bucket,total_aggress,price}` points | Volume bars + overlays |

| panels.p4.data | Output | list[dict] | `{x,price,predicted}` points plus `large_residuals` markers | Intraday fair value, fit once per session |

| panels.p5.data | Output | list[dict] | `{x,price,predicted}` points plus `large_residuals` markers | 10-business-day fair value, refit once per day |



### Dashboard Trade Fetch / Cache Internals



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| aggression.cache_version | Internal | str | Fixed `trades_v2`; v1 caches without `instrument_id` are ignored | `trades_v2_ZF_FUT_20260406.parquet` |

| aggression.front_contracts | Output | list[dict] | Per selected symbol/date: `{date, instrument_id, raw_symbol, volume}` | `{"raw_symbol":"ZFM6","volume":274314}` |

| aggression.instrument_id | Internal | str | Databento instrument identifier preserved from `trades` schema | Used for front-month filtering |

| aggression.raw_symbol | Internal | str | Databento raw futures symbol when available | `ZFM6` |

| aggression.backfill.covered_start | Internal | str \| None | ISO UTC start of background Databento backfill cache | `2026-05-29T05:00:00+00:00` |

| aggression.backfill.covered_end | Internal | str \| None | ISO UTC end of background Databento backfill cache | `2026-05-29T16:48:27+00:00` |

| aggression.backfill.fetched_at | Internal | str \| None | ISO UTC time the background cache was last refreshed | `2026-05-29T16:48:46+00:00` |

| BACKFILL_OVERLAP_SEC | Constant | float | `900.0` seconds | Incremental backfill fetch starts `covered_end - 15m` |

| BACKFILL_REFRESH_SEC | Constant | float | `240.0` seconds | Background Databento refresh cadence |

| CACHE_RETENTION_DAYS | Constant | int | `30` days | `prune_trades_cache()` deletes older parquet files |

| aggression.live_mid.price | Internal | float | BBO midpoint from valid `bid_price`/`ask_price`; sampled about every 5 seconds | `(bid+ask)/2` used in live price line |

| aggression.price_grid | Internal | DatetimeIndex | 1-minute UTC grid from request start to current minute | Trade price overlaid with mid and forward-filled to now |

| prune_trades_cache | Internal | `(max_age_days:int, cache_dir:Path) -> int` | Deletes `trades*.parquet` older than retention cutoff; ignores per-file errors | Called at backfill startup and once per ET-day rollover |

| BUYSELL_DISPLAY_HALFLIFE | Constant | Timedelta | `30s` | V3 Panel 2 chart-only time-weighted EWM of `buysell_raw` |
| BUYSELL_DISPLAY_MAX_POINTS | Constant | int | `4000` | V3 Panel 2 plotted-point cap after the display EWM |
| BUYSELL_EMA_SPAN | Constant | int | `10` prints | Spec print-frame `buysell_ratio`; not drawn on V3 Panel 2 |

| buysell_ratio_raw | Internal | float \| None | Per-bucket `100 * signed_flow / (buy_flow + sell_flow)` before smoothing | Preserved in `flow_df`; not plotted |

| FAIR_VALUE_LARGE_RESIDUAL_Z | Constant | float | `2.0` standard deviations | Marks large fair-value residuals on panels 4/5 |

| HF_SESSION_MIN_PERIODS | Constant | int | `30` aligned points | Minimum points before intraday fair-value fit is cached |

| HF_TENDAY_BUSINESS_DAYS | Constant | int | `10` business days | Business-day history used by the 10D fair-value model |

| TENDAY_REFRESH_SEC | Constant | float | `3600.0` seconds | Background thread check interval; refits at most once per ET date |

| SYMBOL_PARENT_MAP.FV | Constant | str | Databento parent symbol | `ZF.FUT` |

| SYMBOL_PARENT_MAP.TU | Constant | str | Databento parent symbol | `ZT.FUT` |

| SYMBOL_PARENT_MAP.TY | Constant | str | Databento parent symbol | `ZN.FUT` |

| SYMBOL_PARENT_MAP.US | Constant | str | Databento parent symbol | `ZB.FUT` |



### Offline Demo (`aggression_dashboard_demo/`)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| demo.aggression_symbols | Input | list[str] | Non-empty subset of `["FV","TU","TY","US"]`; default is all four | `["FV","TU","TY","US"]` |

| demo.bucket | Input | str | Pandas resample frequency; UI sends `"1min"` | `"1min"` |

| demo.data_dir | Internal | Path | Bundled parquet files under `aggression_dashboard_demo/data/` | `trades_v2_ZN_FUT_20260527.parquet` |

| demo.dv01_table | Constant | dict[str, float] | CTD-style approximations: `FV=47`, `TU=38`, `TY=65`, `US=125`; demo only | `aggression_dashboard_demo/config/aggression_dv01.json` |

| demo.dv01_adjust | Input | bool | `true` uses bundled DV01 config, `false` sums contracts | `true` |

| demo.end_iso | Input | str | Browser `datetime-local`; interpreted as New York time when timezone-naive | `"2026-05-29T17:00"` |

| demo.html_output | Output | Path | Static standalone HTML generated by `export_static.py` | `aggression_dashboard_demo/output/demo_dashboard.html` |

| demo.mode | Output | str | Fixed `"offline_demo"` | `meta.request.mode` |

| demo.price_source | Output | str | Fixed `"bundled_trade_prints"` | `meta.price_source` |

| demo.price_symbol | Input | str | One of `FV`, `TU`, `TY`, `US`; default `TY` | `"TY"` |

| demo.start_iso | Input | str | Browser `datetime-local`; interpreted as New York time when timezone-naive | `"2026-05-26T01:00"` |

| demo.static_payload | Output | dict | Same panel/meta response shape as `/api/aggression`, embedded in static HTML | `DEMO_PAYLOAD` in `output/demo_dashboard.html` |



### `GET /api/live_status` Response



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| connected | Output | bool | `true` when the AccessAPI WebSocket is connected | `true` |

| error | Output | str \| None | Last connection/stream error | `"Connection refused"` |

| historical_lag_sec | Output | float \| None | Lag of latest merged chart bar relative to server now | `104.2` |

| last_recv_age_sec | Output | float \| None | Seconds since this server received the latest live record | `2.1` |

| last_tick_age_sec | Output | float \| None | Seconds since latest exchange timestamp in the live buffer | `3.0` |

| last_tick_ts | Output | str \| None | ISO UTC timestamp of latest live tick | `2026-05-29T15:05:37.189304+00:00` |

| market_open | Output | bool | Approximate CME Treasury futures hours | `true` |

| mode | Output | str | `"live"` or `"historical"` | `live` |

| per_symbol_last_ts | Output | dict[str, str \| None] | Latest tick timestamp by `FV/TU/TY/US` | `{"FV":"...","TY":"..."}` |

| source | Output | str | Fixed `accessapi_live_tbbo` | AccessAPI WebSocket source |

| supported_symbols | Output | list[str] | `["FV","TU","TY","US"]` | UI universe |



## Treasury Refunding Distribution



### treasury_refunding_distribution.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| refunding.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| refunding.event_name | Input | str | `economic_events.event_name` pattern | `--event-name "Treasury Refunding Announcement"` |

| refunding.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| refunding.start_offset_hours | Input | float | Any | `--start-offset-hours 0` |

| refunding.end_offset_hours | Input | float | `> start_offset_hours` | `--end-offset-hours 2` |

| refunding.tag | Input | str | Optional output tag; default auto-tags by window | `--tag t+0m_to_t+10m` |

| refunding.output_dir | Input | Path | Any writable path | `--output-dir workspace/analysis` |

| refunding.plot_path | Output | Path | PNG for selected window | `treasury_refunding_TY_t+0m_to_t+10m.png` |

| refunding.stats_json | Output | Path | JSON stats output | `treasury_refunding_TY_t+0m_to_t+10m.json` |

| refunding.rows_csv | Output | Path | Per-event window returns | `treasury_refunding_TY_t+0m_to_t+10m.csv` |



## Overnight Session Distribution Dashboard



### overnight_session_distribution_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| overnight.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| overnight.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| overnight.end_date | Input | str | `YYYY-MM-DD` | `--end-date 2025-12-31` |

| overnight.output | Output | Path | HTML output path (optional) | `--output workspace/scenarios/overnight_TY.html` |



## JOLTS Event Study Dashboard



### jolts_event_study_dashboard.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| jolts_study.symbol | Input | str | Futures symbol prefix | `--symbol TY` |

| jolts_study.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |

| jolts_study.end_date | Input | str | `YYYY-MM-DD` (optional) | `--end-date 2025-12-31` |

| jolts_study.windows | Input | str | Comma-separated minutes | `--windows 30,60,120,240` |

| jolts_study.out | Output | Path | HTML output path (optional) | `--out custom.html` |

| jolts_study.html_output | Output | Path | Self-contained HTML dashboard | `workspace/scenarios/jolts_event_study_dashboard_YYYYMMDD_HHMMSS.html` |



### Embedded Data Blob



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| jolts_study.data.events | Internal | list[dict] | Per-event metadata (date, mode, surprise, window metrics) | `[{"date":"2024-03-05","jolts_mode":"with_ism","pre_move_ticks_60m":2.5}]` |

| jolts_study.data.paths | Internal | dict | Per-event normalized price paths (ticks + bps) | `{"2024-03-05":[{"rel_min":-60,"ticks":0},...]}` |

| jolts_study.data.candles | Internal | dict | Per-event 5m candlesticks for modal | `{"2024-03-05":[{"ts":"...","open":112.5},...]}` |

| jolts_study.data.profiles | Internal | dict | Aligned average profiles by mode/window | `{"with_ism":{"60m":[0,0.1,0.2,...]}}` |

| jolts_study.data.meta | Internal | dict | Dashboard metadata (counts, windows, timestamp) | `{"total_events":45,"with_ism_count":12}` |



### verify_jolts_event_study.py CLI



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| verify_jolts.start_date | Input | str | `YYYY-MM-DD` | `--start-date 2022-01-01` |



## Analytics Plotting



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| plot_distribution.stats_lines | Internal | list[str] | Optional; overrides default stats box text | `["N = 24", "Mean = 1.2 bps"]` |

| plot_distribution.overlay_values | Internal | list[float] | Optional; plots overlay markers on histogram | `[1.2, -0.4, 0.8]` |

| plot_distribution.vertical_lines | Internal | list[dict] | Optional; vertical lines with labels (value/label/color) | `[{"value":1.2,"label":"2026-01-02"}]` |



### /api/event_details Response



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| api_event_details_response.event | Output | dict | Full hydrated event payload. Contract: always includes `prices`/`postevent_prices` as lists and `be_by_time`/`bps_by_time` as dicts (may be empty). | `{"event":{"date":"2025-01-10","prices":[{"ts":"...","bps":1.2}],"postevent_prices":[],"be_by_time":{"08:30":0.41}}}` |



## Databento (Live Price)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| DATABENTO_API_KEY | EnvVar | str | Non-empty; Databento API key | `set DATABENTO_API_KEY=...` (Windows) / `export DATABENTO_API_KEY=...` (bash) |

| DATABENTO_DATASET | EnvVar | str | Databento dataset identifier | `set DATABENTO_DATASET=GLBX.MDP3` |

| DATABENTO_SCHEMA | EnvVar | str | Databento schema (e.g., trades, mbo, mbp-1) | `set DATABENTO_SCHEMA=trades` |

| DATABENTO_SYMBOL | EnvVar | str | Symbol to stream (e.g., ZN.c.0) | `set DATABENTO_SYMBOL=ZN.c.0` |

| DATABENTO_STYPE_IN | EnvVar | str | Symbol type (e.g., continuous, raw_symbol) | `set DATABENTO_STYPE_IN=continuous` |



## Unified Dashboard L1 Builder (Local Files)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| L1_EVENT_FEATURES_DIR | EnvVar | str | Directory path for L1 event feature files | `set L1_EVENT_FEATURES_DIR=Y:\\Aastha\\market_data_puller\\data\\event_features` |

| l1_event_stats_arrow | Output | Path | Stats file per event with precomputed prefix stats | `data/event_features/NFP/2022-01-07.stats.arrow` |



## Live Unified Dashboard API



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| api_candlestick_query.res | Input | str | `"1m"` or `"5m"` only (precomputed); others rejected | `/api/candlestick/2025-01-10?type=CPI&res=5m&days=2` |



## Market Data Agent (Producer)



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| template | str | Query template name | "by_symbol", "by_date", "by_symbol_and_date", "by_filters", "all", "custom" |

| params | dict | Query parameters (schema depends on template) | {"symbol_pattern": "ZN%", "date": "2025-07-21"} |

| columns | list[str] | Logical columns to select (optional) | ["timestamp", "symbol", "open", "close"] |

| limit | int | Row limit (optional, max 10000) | 100 |



### Outputs



**Output File**: `workspace/agents/market-data-agent/out/{id:06d}.json`



```json

{

  "data": [

    {"timestamp": "2025-07-21T14:30:00Z", "symbol": "ZNZ5", "open": 112.5, "high": 112.7, "low": 112.4, "close": 112.6, "volume": 1530},

    ...

  ],

  "metadata": {

    "query": "SELECT ... FROM ohlcv_1m WHERE ...",

    "timestamp": "2025-11-11T12:00:00Z",

    "row_count": 42,

    "agent": "market-data-agent",

    "version": "1.0"

  }

}

```



**Run Log**: `workspace/agents/market-data-agent/logs/{run_id}.json`



```json

{

  "run_id": "20251111_120000",

"sql": "SELECT ... FROM ohlcv_1m WHERE symbol ILIKE %s AND ts_event::date = %s",

"params": {"symbol_pattern": "ZN%", "date": "2025-07-21"},

  "output_path": "workspace/agents/market-data-agent/out/000001.json",

  "status": "success",

  "row_count": 42,

  "timestamp": "2025-11-11T12:00:00Z",

  "duration_ms": 15.3,

  "agent": "market-data-agent",

  "version": "1.0"

}

```



---



## Consumer Agent



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| input_path | Path | Path to producer output | "workspace/.../out/000001.json" |



### Outputs



**Output File**: `workspace/agents/consumer-agent/out/{id:06d}.json`



```json

{

  "data": [{

    "source": "market-data-agent",

    "source_query": "SELECT ...",

    "source_row_count": 42,

    "timestamp": "2025-11-11T12:00:00Z",

    "statistics": {

      "total_records": 42,

      "records_with_bid": 40,

      "records_with_ask": 40,

      "bid_min": 0.007,

      "bid_max": 1.5,

      "bid_avg": 0.245,

      "ask_min": 0.015,

      "ask_max": 1.6,

      "ask_avg": 0.267

    }

  }],

  "metadata": {

    "query": "Processed from workspace/.../out/000001.json",

    "timestamp": "2025-11-11T12:00:05Z",

    "row_count": 1,

    "agent": "consumer-agent",

    "version": "1.0"

  }

}

```



**Run Log**: `workspace/agents/consumer-agent/logs/{run_id}.json`



```json

{

  "run_id": "20251111_120005",

  "input_path": "workspace/agents/market-data-agent/out/000001.json",

  "output_path": "workspace/agents/consumer-agent/out/000001.json",

  "status": "success",

  "timestamp": "2025-11-11T12:00:05Z",

  "duration_ms": 8.2,

  "agent": "consumer-agent",

  "version": "1.0"

}

```



---



## Manifest Format



**File**: `workspace/agents/{agent-name}/meta.json`



```json

{

  "next_id": 3,

  "last_updated": "2025-11-11T12:00:00Z",

  "total_runs": 2

}

```



| Field | Type | Description |

|-------|------|-------------|

| next_id | int | Next available ID for output file |

| last_updated | str | ISO-8601 timestamp of last update |

| total_runs | int | Total number of runs |



**Filename Pattern**: `{id:06d}.json` (e.g., 000001.json, 000002.json, ...)



---



## Market Data Tool



### run_query Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| template | str | Query template | "by_symbol" |

| columns | list[str] | Logical column list (optional) | ["timestamp", "symbol", "open", "close"] |

| params | dict | Query parameters | {"symbol_pattern": "ZN%"} |

| limit | int | Row limit (optional) | 100 |



**Output**:



```json

{

  "data": [...],

  "metadata": {

    "query": "SELECT ...",

    "params": [...],

    "row_count": 42,

    "sample": [...],  // First 5 rows for logging

    "columns": "symbol, bid, ask"

  }

}

```



---



## Predictive Markets Agent (Producer)



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| query | str | User search query | "Will Trump win 2024?" |

| session_id | str (optional) | Session identifier (auto-generated if not provided) | "20251112143022_a3f2e9" |

| domains | list[str] (optional) | Domains to search (default: all allowed) | ["polymarket.com", "predictit.org"] |

| max_results | int (optional) | Maximum results (default: 10, max: 20) | 10 |



### Outputs



**Output File**: `workspace/agents/predictive-markets-agent/out/{id:06d}.json`



```json

{

  "data": [{

    "query": "Will Trump win 2024?",

    "session_id": "20251112143022_a3f2e9",

    "results": [

      {

        "url": "https://polymarket.com/...",

        "title": "2024 Election Predictions",

        "snippet": "Current prediction markets show...",

        "domain": "polymarket.com"

      }

    ],

    "result_count": 5,

    "domains_searched": "polymarket.com, predictit.org"

  }],

  "metadata": {

    "query": "Polymarket search: Will Trump win 2024?",

    "timestamp": "2025-11-12T14:30:22Z",

    "row_count": 1,

    "agent": "polymarket-agent",

    "version": "1.0"

  }

}

```



**Run Log**: `workspace/agents/polymarket-agent/logs/{run_id}.json`



```json

{

  "run_id": "20251112_143022",

  "query": "Will Trump win 2024?",

  "session_id": "20251112143022_a3f2e9",

  "output_path": "workspace/agents/polymarket-agent/out/000001.json",

  "status": "success",

  "result_count": 5,

  "timestamp": "2025-11-12T14:30:22Z",

  "duration_ms": 1523.45,

  "agent": "polymarket-agent",

  "version": "1.0"

}

```



---



## Query Templates (market data)



| Template | Required Params | Optional / Notes | Example |

|----------|----------------|------------------|---------|

| by_symbol | symbol_pattern | order_by / limit optional | {"symbol_pattern": "ZN%"} |

| by_date | date (YYYY-MM-DD) | order_by / limit optional | {"date": "2025-07-21"} |

| by_symbol_and_date | symbol_pattern, date | order_by / limit optional | {"symbol_pattern": "ZN%", "date": "2025-07-21"} |

| by_filters | none (but at least one filter required) | Filters: symbol_pattern, start_timestamp, end_timestamp, min/max_open/high/low/close/volume | {"symbol_pattern": "ZN%", "start_timestamp": "2025-07-20", "min_close": 112.5} |

| all | none | full table (use limit) | {} |

| custom | conditions (str), values (list optional) | Raw WHERE fragment; use with care | {"conditions": "symbol ILIKE %s AND close > %s", "values": ["ZN%", 112.5]} |



---



## Column Whitelist (market data logical columns)



```python

[

    "timestamp",  # logical alias of ts_event

    "symbol",

    "open",

    "high",

    "low",

    "close",

    "volume"

]

```



---



## Status Values



### Run Log Status



| Value | Description |

|-------|-------------|

| success | Run completed successfully |

| failed | Run failed with error |



### Schema Validation



Output files must pass validation:

- Top-level keys: `data` (list) and `metadata` (dict)

- Metadata must include: `query`, `timestamp`, `row_count`, `agent`, `version`

- Row count must match actual data length



---



## File Bus Operations



### Atomic Write



1. Write to temp file: `{path}.tmp`

2. Flush and fsync

3. Atomic rename to target path



### Manifest Operations



1. Read current manifest

2. Get next_id

3. Update manifest (increment, update timestamp)

4. Atomic write



### Output Path Generation



```python

manifest = Manifest(agent_workspace)

file_id = manifest.increment()  # Returns current, increments for next

filename = f"{file_id:06d}.json"

output_path = agent_workspace / "out" / filename

```



---



## Environment Variables



None required for core agents. API keys (e.g. Telegram bot token) are loaded

from `config/keys.env` via `config.settings.get_api_key`.



Additional optional variables used by offline tooling:



- `OPENAI_API_KEY`: LLM API key for orchestrator task planning and GEPA-based planner optimisation.

- `WANDB_API_KEY`: Weights & Biases API key for experiment tracking.

- `WANDB_PROJECT`: Optional Weights & Biases project name for GEPA optimisation runs.

- `WANDB_RUN_NAME`: Optional Weights & Biases run name for GEPA optimisation runs.

- `WANDB_DIR`: Optional local directory for wandb logs (defaults to `~/wandb_planner1_gepa_logs` or `~/wandb_planner2_gepa_logs`).



### GEPA Optimization Scripts



| Script | Purpose |

|--------|---------|

| `scripts/helpers/planner1_gepa_opt.py` | GEPA optimization for Planner 1 (task decomposition, agent assignment) |

| `scripts/helpers/planner2_gepa_opt.py` | GEPA optimization for Planner 2 (tool selection, parameter mapping) |



**Usage:**

```bash

# Planner 1 GEPA (task decomposition)

python scripts/helpers/planner1_gepa_opt.py --wandb-project planner1-gepa --max-full-evals 30



# Planner 2 GEPA (tool selection)

python scripts/helpers/planner2_gepa_opt.py --wandb-project planner2-gepa --max-full-evals 50

```



---



## Auction Events (December) Report Script



**Script**: `scripts/auction_events_december_report.py`



### Inputs (CLI)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--country` | Input | `str` | Country code/name; default `US` | `--country US` |

| `--roots` | Input | `str` | Comma-separated list; default `TY,ZN` | `--roots TY,ZN` |

| `--years` | Input | `int` | \(\ge 1\); default `5` | `--years 5` |

| `--dec-start` | Input | `int` | 1–31; default `18` | `--dec-start 18` |

| `--dec-end` | Input | `int` | 1–31; default `31` | `--dec-end 31` |

| `--name-like` | Input | `str` | Substring match on `event_name` (case-insensitive); default `auction` | `--name-like auction` |

| `--no-plots` | Input | `bool` | Flag; if set, skip chart generation | `--no-plots` |

| `--events-db` | Input | `str` | Path to `economic_events.db`; default `workspace/economic_events.db` | `--events-db workspace/economic_events.db` |

| `--out-dir` | Input | `str` | Output directory; default `workspace/results/auction_events_dec_.../` | `--out-dir workspace/results/my_run` |

| `--plot-width` | Input | `int` | \(\gt 0\); default `1600` | `--plot-width 1600` |

| `--plot-height` | Input | `int` | \(\gt 0\); default `600` | `--plot-height 600` |



### Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `auction_events_report.json` | Output | `Path` | JSON report in output directory | `workspace/results/.../auction_events_report.json` |

| `auction_events.csv` | Output | `Path` | CSV export in output directory | `workspace/results/.../auction_events.csv` |

| `charts/` | Output | `Path` | Folder of PNG charts (if enabled) | `workspace/results/.../charts/TY/TY_2024-12-19_TYZ4_Comdty.png` |



**Notes**:

- Queries `workspace/economic_events.db` for events with `event_name` containing `--name-like` during Dec `--dec-start`..`--dec-end` for the last `--years` years (inclusive), and filters out events in the future relative to US/Eastern “now”.

- Charts are generated for the **front-month** TY/ZN contract for each auction-event day via `src/servers/marketdata/run_query.py` and rendered using `src/servers/analytics/plotting.py` (`plotly` + `kaleido` required). If dependencies are missing, the script still writes JSON/CSV.



---



## Derive Auction Fields Script



**Script**: `scripts/derive_auction_fields.py`



### Inputs (CLI)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--input` | Input | `str` | Path to CSV (required) | `--input C:/Users/aasth/Downloads/Securities.csv` |

| `--output` | Input | `str` | Output CSV path (default: `<input>_derived.csv`) | `--output workspace/results/Securities_derived.csv` |

| `--percent-decimals` | Input | `int` | \(\ge 0\); default `1` | `--percent-decimals 1` |

| `--ratio-decimals` | Input | `int` | \(\ge 0\); default `2` | `--ratio-decimals 2` |

| `--gap-decimals` | Input | `int` | \(\ge 0\); default `1` | `--gap-decimals 1` |

| `--include-dealer-percent` | Input | `bool` | Flag; include Dealer% = 100 - Direct% - Indirect% | `--include-dealer-percent` |

| `--nomura-labels` | Input | `bool` | Flag; output Nomura-style labels | `--nomura-labels` |

| `--nomura-only` | Input | `bool` | Flag; write only Nomura-style columns | `--nomura-only` |



### Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `derived_auction_fields.csv` | Output | `Path` | CSV with appended derived columns | `workspace/results/Securities_derived.csv` |



**Derived Columns (default mode)**:

- `Auction` (from `Auction Date`)

- `Bid/Cover` (from `Bid-to-Cover Ratio`)

- `Direct%`, `Indirect%` (accepted ÷ competitive accepted)

- `Dir Hit`, `Indir Hit` (accepted ÷ tendered)

- `Dealer%` (optional; 100 - Direct% - Indirect%)



**Derived Columns (Nomura labels)**:

- `Auction` (Auction Date)

- `Size` (Offering Amount)

- `Coupon` (Interest Rate)

- `Stop` (High Yield)

- `Bid/Cover` (Bid-to-Cover Ratio)

- `Tail` (blank; requires WI yield)

- `Yld Gap` (High Yield − Average / Median Yield, in bp)

- `Dealer%` (Primary Dealer Accepted ÷ Competitive Accepted)

- `Direct%` (Direct Bidder Accepted ÷ Competitive Accepted)

- `Indirect%` (Indirect Bidder Accepted ÷ Competitive Accepted)

- `Dlr Hit` (Primary Dealer Accepted ÷ Primary Dealer Tendered)

- `Dir Hit` (Direct Bidder Accepted ÷ Direct Bidder Tendered)

- `Indir Hit` (Indirect Bidder Accepted ÷ Indirect Bidder Tendered)



---



## Process Auction Files Script



**Script**: `auction_data/process_auction_files.py`



Batch processor for raw auction CSV files. Located in `auction_data/` for direct execution. Scans current directory for `*_raw.csv` files and generates corresponding `*_processed.csv` files with 13 Nomura-style derived columns.



### Inputs (CLI)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--force` | Input | `bool` | Flag; reprocess all files regardless of timestamps | `--force` |



### Processing Logic



- Scans `auction_data/` directory for `*_raw.csv` files

- For each raw file, generates `*_processed.csv` (e.g., `2y_note_raw.csv` → `2y_note_processed.csv`)

- Skips processing if processed file exists and is newer than raw file (unless `--force`)

- Uses `derive_nomura_fields()` from `data_setup/derive_auction_fields.py`



### Output Columns (13 Nomura-style fields)



| # | Column | Source Field | Derivation |

|---|--------|--------------|------------|

| 1 | Auction | Auction Date | Direct copy |

| 2 | Size | Offering Amount | Direct copy |

| 3 | Coupon | Interest Rate | Direct copy |

| 4 | Stop | High Yield | Direct copy |

| 5 | Bid/Cover | Bid-to-Cover Ratio | Float, 2 decimal places |

| 6 | Tail | (empty) | Placeholder |

| 7 | Yld Gap | (High Yield - Avg/Med Yield) * 100 | 1 decimal place |

| 8 | Dealer% | Primary Dealer Accepted / Competitive Accepted | Percent, 1 decimal |

| 9 | Direct% | Direct Bidder Accepted / Competitive Accepted | Percent, 1 decimal |

| 10 | Indirect% | Indirect Bidder Accepted / Competitive Accepted | Percent, 1 decimal |

| 11 | Dlr Hit | Primary Dealer Accepted / Primary Dealer Tendered | Percent, 1 decimal |

| 12 | Dir Hit | Direct Bidder Accepted / Direct Bidder Tendered | Percent, 1 decimal |

| 13 | Indir Hit | Indirect Bidder Accepted / Indirect Bidder Tendered | Percent, 1 decimal |



### Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `*_processed.csv` | Output | `Path` | CSV in `auction_data/` with 13 derived columns | `auction_data/2y_note_processed.csv` |



---



## Create BBG Event Fields Excel Script



**Script**: `data_setup/create_bbg_event_fields_excel.py`



### Inputs (CLI)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--output` | Input | `str` | Path ending in `.xlsx` | `--output workspace/results/bbg_event_fields.xlsx` |

| `--start-date` | Input | `str` | `YYYY-MM-DD` | `--start-date 2000-01-01` |

| `--end-date` | Input | `str` | `YYYY-MM-DD` or Excel formula | `--end-date =TEXT(TODAY(),"YYYY-MM-DD")` |

| `--fields` | Input | `str` | Comma-separated Bloomberg fields | `--fields ECO_RELEASE_VALUE,ECO_FCST_MEDIAN,ECO_PREVIOUS,ECO_REVISION,ECO_FCST_STDDEV` |



### Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `bbg_event_fields.xlsx` | Output | `Path` | Excel workbook with BDH formulas | `workspace/results/bbg_event_fields.xlsx` |



### `scripts/refresh_bbg_excel.py`



#### Inputs (CLI)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--workbook` | Input | `Path` | Existing shared workbook path | `--workbook //100.95.155.81/FRGMSharedSpace/Meini/bbg_event_fields.xlsx` |

| `--timeout` | Input | `int` | `>= 1`; max seconds to wait for Excel/Bloomberg recalc | `--timeout 180` |

| `--poll` | Input | `int` | `>= 1`; seconds between recalc-state checks | `--poll 5` |



#### Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `refresh_bbg_excel.updated_cells` | Output | `int` | `>= 0`; workbook cells/formulas normalized before save | Printed in script logs |

| `refresh_bbg_excel.end_date_formula` | Output | `str` | Excel formula string for dynamic text date | `=TEXT(TODAY(),"YYYY-MM-DD")` |

| `refresh_bbg_excel.attached_mode` | Output | `bool` | `true` when script attached to already-open workbook; `false` when it opened a new hidden Excel instance | Printed in script logs |

| `bbg_excel_refresh.log_file` | Output | `Path` | Append-only scheduler refresh log path | `workspace/logs/bbg_excel_refresh.log` |



---



## Ad-hoc Candlestick (Dec Dates) Script



**Script**: `scripts/adhoc_candles_dec_dates.py`



Generates **three TY charts** for the **last 30 days ending** on fixed dates:

- 2023-12-27 (daily candles)

- 2022-12-28 (daily candles)

- 2020-12-28 (hourly candles)



Also supports an explicit inclusive date range (single chart):

- Example: 2021-12-01 to 2021-12-27 (inclusive)



### Inputs (CLI)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--symbol` | Input | `str` | Root symbol; default `TY` | `--symbol TY` |

| `--start-date` | Input | `str` | Optional; `YYYY-MM-DD` (inclusive). Must be used with `--end-date`. | `--start-date 2021-12-01` |

| `--end-date` | Input | `str` | Optional; `YYYY-MM-DD` (inclusive). Must be used with `--start-date`. | `--end-date 2021-12-27` |

| `--interval` | Input | `str` | `1d` or `1h`; used for range mode (default `1d`) | `--interval 1d` |

| `--out-dir` | Input | `str` | Output dir; default `workspace/results/adhoc_candles_<timestamp>/` | `--out-dir workspace/results/my_run` |

| `--theme` | Input | `str` | `dark` or `light`; default `dark` | `--theme dark` |

| `--width` | Input | `int` | 200–2000; default `1600` | `--width 1600` |

| `--height` | Input | `int` | 150–1500; default `600` | `--height 600` |

| `--include-events` | Input | `bool` | Flag; overlay events (can be busy for 30D); default `False` | `--include-events` |



### Outputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `manifest.json` | Output | `Path` | Written to `--out-dir` | `workspace/results/.../manifest.json` |

| `*_last30d_1d.png` | Output | `Path` | Daily charts (2 files) | `workspace/results/.../TY_2023-12-27_last30d_1d.png` |

| `*_last30d_1h.png` | Output | `Path` | Hourly chart (1 file) | `workspace/results/.../TY_2020-12-28_last30d_1h.png` |

| `*_to_*_1d.png` | Output | `Path` | Range-mode daily chart (1 file) | `workspace/results/.../TY_2021-12-01_to_2021-12-27_1d.png` |

| `*_to_*_1h.png` | Output | `Path` | Range-mode hourly chart (1 file) | `workspace/results/.../TY_2021-12-01_to_2021-12-27_1h.png` |



**Notes**:

- Pulls 1-minute OHLCV via `src/servers/marketdata/run_query.py` (PostgreSQL + `psycopg2-binary`) using `run_multi_date_query` to select **front-month per day**.

- Renders PNG via `src/servers/analytics/plotting.py` (`plotly` + `kaleido`).

- Always aggregates candles in pure Python (stdlib).



## Polymarket Agent (Producer - Reasoning Mode)



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| query | str | Natural language search query | "What was opinion on Jan 1 2025 about Bitcoin?" |

| session_id | str (optional) | Session identifier (auto-generated if not provided) | "20251113143022_a3f2e9" |

| limit | int (optional) | Maximum markets to consider (default: 10, max: 50) | 10 |



### Outputs



**Output File**: `workspace/agents/polymarket-agent/out/{id:06d}.json`



```json

{

  "data": [{

    "query": "What was opinion on Jan 1 2025 about Bitcoin?",

    "parsed": {

      "topic": "bitcoin",

      "date": "2025-01-01",

      "confidence": 0.95

    },

    "comparison_date": "2025-01-01",

    "date_source": "specified",

    "result": {

      "query": "What was opinion on Jan 1 2025 about Bitcoin?",

      "parsed": {

        "topic": "bitcoin",

        "date": "2025-01-01",

        "confidence": 0.95

      },

      "comparison_date": "2025-01-01",

      "date_source": "specified",

      "markets": [

        {

          "title": "Bitcoin to reach $100,000 by end of 2025?",

          "url": "https://polymarket.com/event/bitcoin-100k-2025",

          "url_valid": true,

          "prices": {"Yes": 0.65, "No": 0.35},

          "volume": 1250000,

          "low_volume_flag": false,

          "volume_note": "Volume: $1,250,000",

          "relevance_score": 0.95,

          "historical_price": {"yes": 0.60, "no": 0.40},

          "historical_date": "2025-01-01",

          "historical_note": "Data from Polymarket CLOB API",

          "price_change": {

            "yes_change": 5.0,

            "direction": "up"

          }

        }

      ],

      "metadata": {

        "total_markets": 5,

        "low_volume_count": 1,

        "comparison_note": "Comparing current vs 2025-01-01 (specified)"

      }

    }

  }],

  "metadata": {

    "query": "Polymarket reasoning: What was opinion on Jan 1 2025 about Bitcoin?",

    "timestamp": "2025-11-13T14:30:22Z",

    "row_count": 1,

    "agent": "polymarket-agent",

    "version": "2.0",

    "comparison_date": "2025-01-01",

    "date_source": "specified",

    "low_volume_threshold": 1000

  }

}

```



**Run Log**: `workspace/agents/polymarket-agent/logs/{run_id}.json`



```json

{

  "run_id": "20251113_143022",

  "query": "What was opinion on Jan 1 2025 about Bitcoin?",

  "session_id": "20251113143022_a3f2e9",

  "output_path": "workspace/agents/polymarket-agent/out/000001.json",

  "status": "success",

  "timestamp": "2025-11-13T14:30:22Z",

  "agent": "polymarket-agent",

  "version": "2.0"

}

```



---



## Polymarket Tools



### search_polymarket_with_history Tool ⭐ **PRIMARY TOOL**



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| query | str | Search query | "Bitcoin predictions" |

| session_id | str (optional) | Session identifier (auto-generated) | "20251113143022_a3f2e9" |

| limit | int (optional) | Result limit (default: 5, max: 50) | 10 |

| historical_date | str (optional) | Historical date (YYYY-MM-DD) | "2024-11-01" |

| days_back | int (optional) | Days to look back (default: 7) | 7 |



**Output**:



```json

{

  "markets": [

    {

      "title": "Bitcoin to reach $100k?",

      "market_id": "0x123...",

      "clob_token_ids": ["71321045679252212788969529556922467531..."],

      

      "current_price": {"yes": 0.65, "no": 0.35},

      "historical_price": {"yes": 0.55, "no": 0.45},

      

      "price_change": {

        "yes": 0.10,

        "no": -0.10,

        "yes_percent": 18.2,

        "no_percent": -22.2,

        "direction": "up"

      },

      

      "current_date": "2025-11-27",

      "historical_date": "2025-11-20",

      "data_points": 24,

      "volume": 1234567,

      "url": "..."

    }

  ],

  "metadata": {

    "query": "Bitcoin predictions",

    "session_id": "...",

    "result_count": 5,

    "current_date": "2025-11-27",

    "historical_date": "2025-11-20",

    "days_back": 7,

    "platform": "polymarket",

    "timestamp": "..."

  }

}

```



**Notes**: 

- **USE THIS TOOL** - Combines current prices + historical prices from API + price change calculation

- **API-based**: Fetches historical prices from Polymarket CLOB API (`/prices-history`)

- Uses token IDs (`clob_token_ids`) from search results to query historical prices

- Default: compares with 1 week ago, or use custom `historical_date`

- Returns price deltas (absolute and percentage) for each market

- Shows sentiment direction (up/down/stable)



---



### search_polymarket_markets Tool (Internal)



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| query | str | Search query | "Bitcoin predictions" |

| session_id | str | Session identifier | "20251113143022_a3f2e9" |

| limit | int (optional) | Result limit (default: 5, max: 50) | 10 |



**Output**:



```json

{

  "markets": [

    {

      "market_id": "0x123...",

      "title": "Bitcoin to reach $100k?",

      "description": "...",

      "prices": {"yes": 0.65, "no": 0.35},

      "volume": 1250000,

      "liquidity": 85000,

      "status": "active",

      "url": "https://polymarket.com/event/bitcoin-100k"

    }

  ],

  "metadata": {

    "query": "Bitcoin predictions",

    "session_id": "20251113143022_a3f2e9",

    "result_count": 5,

    "platform": "polymarket",

    "timestamp": "2025-11-13T14:30:22Z"

  }

}

```



**Notes**: 

- Simple API call to Polymarket `/public-search` endpoint



- Used internally by `search_polymarket_with_history`



### get_polymarket_history Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| session_id | str (optional) | Filter by session | "20251113143022_a3f2e9" |

| limit | int (optional) | Max results (default: 10) | 20 |

| start_date | str (optional) | Start date (ISO format) | "2025-11-01T00:00:00Z" |

| end_date | str (optional) | End date (ISO format) | "2025-11-13T23:59:59Z" |



**Output**:



```json

{

  "history": [

    {

      "id": 1,

      "session_id": "20251113143022_a3f2e9",

      "user_query": "Will Bitcoin reach $100k?",

      "timestamp": "2025-11-13T14:30:22Z",

      "results": [...],

      "platform": "polymarket",

      "market_ids": ["0x123...", "0x456..."],

      "avg_probability": 65.5,

      "total_volume": 2500000,

      "result_count": 5,

      "created_at": "2025-11-13T14:30:22Z"

    }

  ],

  "metadata": {

    "count": 1,

    "filters": {

      "session_id": "20251113143022_a3f2e9",

      "start_date": null,

      "end_date": null,

      "limit": 10,

      "platform": "polymarket"

    },

    "timestamp": "2025-11-13T14:35:00Z"

  }

}

```



### get_market_price_history Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| market_id | str | Market/token ID | "0x123..." |

| date | str | Target date (ISO format YYYY-MM-DD) | "2024-11-01" |

| date_range_hours | int (optional) | Hours to search around date (default: 12) | 12 |



**Output**:



```json

{

  "market_id": "0x123...",

  "date": "2024-11-01",

  "price": {

    "yes": 0.65,

    "no": 0.35

  },

  "data_points": 24,

  "data_source": "polymarket_clob_api",

  "note": "Historical price from 24 data points (Polymarket CLOB API)"

}

```



**Data Source**: Uses Polymarket CLOB API `prices-history` endpoint. Fetches time-series price data and finds the price closest to the target date using weighted averaging of nearby points.



### get_market_price_range Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| market_id | str | Market/token ID | "0x123..." |

| start_date | str | Start date (YYYY-MM-DD) | "2024-10-01" |

| end_date | str | End date (YYYY-MM-DD) | "2024-11-01" |

| interval_days | int (optional) | Days between data points (default: 1) | 1 |



**Output**:



```json

{

  "market_id": "0x123...",

  "start_date": "2024-10-01",

  "end_date": "2024-11-01",

  "prices": [

    {"date": "2024-10-01", "yes": 0.60, "no": 0.40},

    {"date": "2024-10-02", "yes": 0.62, "no": 0.38},

    ...

  ],

  "data_points": 31,

  "price_change": {

    "yes": +0.05,

    "no": -0.05

  },

  "note": "Historical price trend from Polymarket CLOB API (480 raw data points)"

}

```



**Data Source**: Uses Polymarket CLOB API `prices-history` endpoint. Fetches entire date range at once and samples at requested intervals for efficiency.



---



## Database Schemas



### Central Metadata Database (Run Store)



**Database**: `data/metadata.db`



Lightweight metadata store for orchestration runs, agent artifacts, and Telegram feedback.



#### runs Table



| Column | Type | Description |

|--------|------|-------------|

| run_id | TEXT PRIMARY KEY | Orchestration run ID |

| user_query | TEXT NOT NULL | Original user query text |

| created_at | TEXT NOT NULL | ISO-8601 timestamp |

| status | TEXT NOT NULL | `running` \| `success` \| `failed` |



#### agent_outputs Table



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| run_id | TEXT NOT NULL | Orchestration run ID |

| agent | TEXT NOT NULL | Logical stage/agent name (`planner1`, `planner2`, `coder`, `runner`, etc.) |

| step | INTEGER NOT NULL | Pipeline stage number |

| task_id | TEXT | Optional task identifier |

| summary | TEXT | Optional summary |

| data_pointer | TEXT NOT NULL | Relative/absolute path to artifact |

| created_at | TEXT NOT NULL | ISO-8601 timestamp |



#### telegram_run_messages Table



Maps **bot-sent Telegram message IDs** to `run_id` so users can reply to a bot answer with `/feedback ...`.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| run_id | TEXT NOT NULL | Orchestration run ID |

| chat_id | INTEGER NOT NULL | Telegram chat ID |

| message_id | INTEGER NOT NULL | Telegram message ID (bot message) |

| message_kind | TEXT NOT NULL | e.g. `answer_chunk`, `plot_png`, `json_output` |

| created_at | TEXT NOT NULL | ISO-8601 timestamp |



**Unique Constraint**: (chat_id, message_id)



#### telegram_feedback Table



Self-contained feedback snapshot linked to a specific `run_id`.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| run_id | TEXT NOT NULL | Orchestration run ID |

| chat_id | INTEGER NOT NULL | Telegram chat ID |

| from_user_id | INTEGER NOT NULL | Telegram user ID |

| from_username | TEXT | Telegram username (nullable) |

| reply_to_message_id | INTEGER NOT NULL | The bot message being replied to |

| label | TEXT NOT NULL | Feedback label |

| comment | TEXT | Optional free-text comment |

| query_text | TEXT NOT NULL | Snapshot of original query |

| answer_text | TEXT NOT NULL | Snapshot of replied-to bot output |

| output_path | TEXT | Optional pointer to orchestrator output JSON |

| created_at | TEXT NOT NULL | ISO-8601 timestamp |



#### Feedback Labels (Telegram)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| feedback_label | Internal | `str` | `correct`, `incorrect`, `incomplete`, `unclear`, `other` | `/feedback correct Looks right` |



#### Telegram Follow-up Context Functions



| Function | Kind | Input | Output | Description |

|----------|------|-------|--------|-------------|

| `read_run_final_answer(run_id)` | Internal | `str` run_id | `Optional[str]` | Reads final_answer from runner output via agent_outputs table lookup |

| `_build_followup_context(run_id, new_query)` | Internal | `str` run_id, `str` new_query | `Optional[str]` | Builds enriched query with conversation context (prev query + truncated answer) |



**Context Format** (prepended to follow-up queries):

```

=== CONVERSATION CONTEXT ===

Previous Query: <original query text>

Previous Answer: <truncated to 500 chars>...

================================



Follow-up Question: <user's new query>

```



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| MAX_CONTEXT_ANSWER_LENGTH | Constant | `int` | 500 | Max chars from previous answer in context |



### Orchestrator Results Database



**Database**: `workspace/orchestrator_results.db`



#### worker_runs Table



Stores execution metadata for each worker task.



| Column | Type | Description |

|--------|------|-------------|

| run_id | TEXT NOT NULL | Orchestration run ID (timestamp-based) |

| task_id | TEXT NOT NULL | Task ID from planner |

| agent_name | TEXT NOT NULL | Agent that executed the task |

| status | TEXT NOT NULL | 'running', 'success', or 'failed' |

| started_at | TEXT NOT NULL | ISO-8601 timestamp of task start |

| completed_at | TEXT | ISO-8601 timestamp of task completion |

| duration_ms | REAL | Task execution duration in milliseconds |

| error | TEXT | Error message if failed |

| output_file_path | TEXT | Path to file bus output |

| created_at | TEXT | Record creation timestamp |



**Primary Key**: (run_id, task_id)



**Indices**:

- `idx_worker_run_id` on `run_id`

- `idx_worker_task_id` on `task_id`



#### task_outputs Table



Stores task output data and metadata.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| run_id | TEXT NOT NULL | Orchestration run ID |

| task_id | TEXT NOT NULL | Task ID |

| agent_name | TEXT NOT NULL | Agent name |

| output_data | TEXT NOT NULL | JSON string of output data |

| metadata | TEXT NOT NULL | JSON string of metadata |

| created_at | TEXT | Record creation timestamp |



**Foreign Key**: (run_id, task_id) REFERENCES worker_runs(run_id, task_id)



**Indices**:

- `idx_run_id` on `run_id`

- `idx_task_id` on `task_id`

- `idx_agent` on `agent_name`



**Usage**: Enables dependency resolution, result consolidation, and queryable task history.



#### planner1_tasks Table (Unified Planning)



Unified planning table storing the complete task graph per orchestration run.

Planner 1 writes base task data, Planner 2 enriches with tools/params.

All planning stages write to and read from this single table using `run_id`.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| run_id | TEXT NOT NULL | Orchestration run ID |

| task_id | TEXT NOT NULL | Task ID (e.g. `"task_1"`) |

| description | TEXT | Natural-language task description |

| assigned_agent | TEXT | Agent name assigned by Planner 1 (may be NULL) |

| mappable | INTEGER NOT NULL | 1 if task is mappable to an agent, else 0 |

| dependencies | TEXT | JSON array of direct dependency task IDs |

| dependency_paths | TEXT | JSON array of full dependency path (root→task) |

| priority | INTEGER | Optional priority (default 1) |

| agent_params | TEXT | JSON object of agent-specific parameters (Planner 1) |

| tools | TEXT | JSON array of tool names (Planner 2) |

| tool_params | TEXT | JSON object mapping tool_name → params (Planner 2) |

| created_at | TEXT | Record creation timestamp |



**Indices**:

- `idx_planner1_run_id` on `run_id`

- `idx_planner1_task_id` on `task_id`



**Usage**: 

- **Stage 1 (Planner 1)**: Writes one row per normalized subtask with base fields (task_id, description, assigned_agent, dependencies, dependency_paths, agent_params)

- **Stage 2 (Planner 2)**: Updates each row with `tools` and `tool_params` after AI tool selection

- **Stage 5 (Runner)**: Reads complete unified plan (Planner 1 + Planner 2 data) for final consolidation

- **Analytics/Monitoring**: Can reconstruct the entire planning flow from a single table using `run_id`



All tasks for a given orchestration run share the same `run_id`. This unified design eliminates the need for intermediate JSON artifacts or separate planning tables.



---



## Runner Agent (Final Consolidation)



### Inputs



Called by the orchestrator with:



| Name | Type | Description | Example |

|------|------|-------------|---------|

| query | str | Original natural language query | "What were Bitcoin predictions and how do they compare to futures?" |

| worker_outputs | list[dict] | Task outputs from `WorkersDB.get_all_task_outputs` | `[{"task_id": "task_1", "agent_name": "polymarket_agent", ...}]` |

| planning_table | list[dict] (optional) | Planning rows from `WorkersDB.get_task_plan` | `[{"task_id": "task_1", "agent_name": "polymarket_agent", ...}]` |

| run_id | str (optional) | Orchestration run identifier | "20251125_120000" |



### Outputs



**Output File**: `workspace/agents/runner-agent/out/{id:06d}.json`



```json

{

  "data": [{

    "final_answer": "…final user-facing answer…",

    "reasoning_metadata": {

      "provider": "openai",

      "model": "gpt-5",

      "temperature": 0.2,

      "max_tokens": 1500

    },

    "worker_outputs": [...],

    "planning_table": [...]

  }],

  "metadata": {

    "query": "RunnerAgent final answer for: <user query>",

    "timestamp": "2025-11-25T12:00:00Z",

    "row_count": 1,

    "agent": "runner-agent",

    "version": "1.0",

    "run_id": "<run id>"

  }

}

```



---



## Runner MCP Tools



### generate_structured_output Tool



**Inputs**:



| Parameter | Type | Description | Example |

|----------|------|-------------|---------|

| query | str | Original natural language query | "What were Bitcoin predictions and how do they compare to futures?" |

| worker_outputs | list[dict] | Task outputs from `WorkersDB.get_all_task_outputs` | `[{"task_id": "task_1", "agent_name": "polymarket_agent", ...}]` |

| planning_table | list[dict] (optional) | Planning rows from `WorkersDB.get_task_plan` | `[{"task_id": "task_1", "agent_name": "polymarket_agent", ...}]` |

| run_id | str \| null (optional) | Orchestration run identifier | "20251125_120000" |



**Output**:



```json

{

  "summary": {

    "query": "<original query>",

    "run_id": "<run id>",

    "total_tasks": 3,

    "agents_used": ["market_data_agent", "polymarket_agent"],

    "tasks_per_agent": {

      "market_data_agent": 1,

      "polymarket_agent": 2

    }

  },

  "worker_outputs": [...],

  "planning_table": [...]

}

```



### build_runner_answer Tool



**Inputs**:



Same as `generate_structured_output`:



| Parameter | Type | Description |

|----------|------|-------------|

| query | str | Original natural language query |

| worker_outputs | list[dict] | Task outputs from `WorkersDB.get_all_task_outputs` |

| planning_table | list[dict] (optional) | Planning rows from `WorkersDB.get_task_plan` |

| run_id | str \| null (optional) | Orchestration run identifier |



**Output**:



```json

{

  "query": "<original query>",

  "run_id": "<run id>",

  "final_answer": "…final user-facing answer…",

  "reasoning_metadata": {

    "provider": "openai",

    "model": "gpt-5",

    "temperature": 0.2,

    "max_tokens": 1500

  },

  "worker_outputs": [...],

  "planning_table": [...],

  "summary": {

    "total_tasks": 3,

    "agents_used": ["market_data_agent", "polymarket_agent"]

  }

}

```



---



## Agent Enable/Disable Toggle



Each agent in `AGENT_CAPABILITIES` has an `enabled` flag for inclusion/exclusion from planning.



### Configuration



| Name | Kind | Type | Default | Description |

|------|------|------|---------|-------------|

| enabled | Config | bool | True | Toggle agent visibility in orchestrator planning |



**Location**: `src/agents/orchestrator_agent/config.py`



### Helper Functions



| Function | Returns | Description |

|----------|---------|-------------|

| `get_enabled_agent_capabilities()` | dict | Agent capabilities for enabled agents only |

| `get_enabled_agent_names()` | list[str] | List of enabled agent names |

| `is_agent_enabled(agent_name)` | bool | Check if specific agent is enabled |

| `get_planner_agent_registry()` | list[dict] | Planner registry for enabled agents only |



### ToolLoader Methods



| Method | Returns | Description |

|--------|---------|-------------|

| `get_enabled_agent_tool_map()` | dict[str, list[str]] | Agent-to-tool mapping for enabled agents |

| `get_tool_metadata(tool_name)` | dict | Tool metadata including `soft_dependencies` |



### Soft Dependencies



Tools can declare `soft_dependencies` in `@register_tool` to have prerequisite tasks auto-injected by the orchestrator.



**Soft Dependency Schema**:



| Field | Type | Required | Description |

|-------|------|----------|-------------|

| `param_name` | str | Yes | Target parameter to fill (e.g., "events") |

| `source_agent` | str | Yes | Agent to call (e.g., "eventdata_puller_agent") |

| `source_tool` | str | Yes | Tool to invoke (e.g., "search_events") |

| `param_mapping` | dict | Yes | Maps tool params to values; use `$context.path` for dynamic values |

| `output_path` | str | No | Path in result to extract (default: whole result) |

| `required` | bool | No | If True, fail if dependency fails (default: False) |



**Context Variables** (available in `param_mapping`):



| Path | Source | Example |

|------|--------|---------|

| `$context.time_range.start` | Prior task params | "2025-12-10" |

| `$context.time_range.end` | Prior task params | "2025-12-12" |

| `$context.symbol` | Prior task params | "ZN%" |

| `$context.country` | Prior task params or default | "US" |



**Example**:



```python

@register_tool(

    name="generate_candlestick_chart",

    description="...",

    soft_dependencies=[{

        "param_name": "events",

        "source_agent": "eventdata_puller_agent",

        "source_tool": "search_events",

        "param_mapping": {

            "start_date": "$context.time_range.start",

            "end_date": "$context.time_range.end",

            "country": "$context.country|US",  # With default

            "importance": "high"

        },

        "output_path": "events",

        "required": False  # Graceful degradation

    }]

)

```



**Graceful Degradation**: When `required=False`, if the dependency task fails:

- Chart renders without events

- `soft_dependency_notes` in metadata explains why (task failed, no events, missing timestamps)



### Example



Disable an agent by setting `"enabled": False` in `AGENT_CAPABILITIES`:



```python

AGENT_CAPABILITIES = {

    "market_data_agent": {

        "enabled": True,   # This agent is discovered

        ...

    },

    "polymarket_agent": {

        "enabled": False,  # This agent is hidden from planners

        ...

    },

}

```



---



## Orchestrator Planner Structural Metrics



Additional structural metric computed for planner evaluation:



| Name | Kind | Type | Description | Allowed values / range |

|------|------|------|-------------|------------------------|

| agent_params_ok | Internal | bool | True if all subtasks with assigned agents have well-formed agent parameters consistent with that agent’s expected inputs (e.g. DB fields for `market_data_agent`, concise natural-language queries and limits for `polymarket_agent`, structured event/time params for conceptual agents like `event_data_puller_agent`). | `true` or `false` |



---



### prediction_queries Table (Polymarket)



**Database**: `polymarket_markets.db`

**Table**: `prediction_queries`



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| session_id | TEXT NOT NULL | Unique session identifier |

| user_query | TEXT NOT NULL | User's original search query |

| expanded_keywords | TEXT NOT NULL | Empty JSON array (legacy, kept for compatibility) |

| timestamp | TEXT NOT NULL | Query timestamp (ISO-8601) |

| results | TEXT NOT NULL | JSON string of market data (may include relevance_score from LLM) |

| platform | TEXT | Platform name (default: 'polymarket') |

| market_ids | TEXT | JSON array of market IDs |

| avg_probability | REAL | Average probability across markets |

| total_volume | INTEGER | Total volume across markets |

| result_count | INTEGER NOT NULL | Number of markets returned |

| created_at | TEXT NOT NULL | Record creation timestamp |



**Indices**:

- `idx_session_id` on `session_id`

- `idx_timestamp` on `timestamp`

- `idx_platform` on `platform`



---



### tracked_markets Table (Polymarket)



**Database**: `polymarket_markets.db`

**Table**: `tracked_markets`



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| condition_id | TEXT NOT NULL | Polymarket condition/market ID |

| market_slug | TEXT | Market slug (URL-friendly) |

| title | TEXT | Market question/title |

| yes_token_id | TEXT | CLOB token ID for Yes outcome |

| no_token_id | TEXT | CLOB token ID for No outcome |

| created_at | TEXT | Record creation timestamp |

| active | INTEGER | 1=active, 0=inactive |



**Indices**:

- `idx_tracked_markets_condition_id` on `condition_id`

- `idx_tracked_markets_active` on `active`



---



### market_prob_timeseries Table (Polymarket)



**Database**: `polymarket_markets.db`

**Table**: `market_prob_timeseries`



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| token_id | TEXT NOT NULL | CLOB token ID being tracked |

| condition_id | TEXT | Polymarket condition/market ID |

| market_slug | TEXT | Market slug |

| observed_ts | TEXT NOT NULL | Observation timestamp (ISO-8601 UTC) |

| mid | REAL | Midpoint price (probability) |

| best_bid | REAL | Best bid (optional) |

| best_ask | REAL | Best ask (optional) |

| source | TEXT NOT NULL | Source tag (e.g., rest_midpoint_poll) |

| raw_payload | TEXT | JSON payload snapshot (optional) |

| created_at | TEXT | Record creation timestamp |



**Indices**:

- `idx_market_prob_ts_token` on `(token_id, observed_ts)`



---



### polymarket_probability_tracker.py CLI Inputs



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|-------------|

| query | Input | str | Non-empty | `--query "Bitcoin $100k by 2025"` |

| token_id | Input | str | CLOB token ID | `--token-id 713210...` |

| condition_id | Input | str | Condition/market ID | `--condition-id 0xabc...` |

| max_markets | Input | int | ≥1 | `--max-markets 5` |

| poll_interval_s | Input | int | ≥1 | `--poll-interval-s 60` |

| epsilon | Input | float | ≥0 | `--epsilon 0.0005` |

| max_gap_s | Input | int | ≥1 | `--max-gap-s 600` |

| max_not_found | Input | int | ≥1 | `--max-not-found 3` |

| reresolve_every_mins | Input | int | ≥0 | `--reresolve-every-mins 0` |

| track_no_token | Input | bool | true/false | `--track-no-token` |

| db_path | Input | str | Valid path | `--db-path polymarket_markets.db` |



---



## EventData Puller Agent (Producer)



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| action | str | Action to perform | "update_calendar", "query_event", "search_events", "stream_start", "stream_stop", "stream_status", "get_live_events" |

| event_id | str (optional) | Event ID/ticker from Trading Economics | "USANFP", "USCPIYOY" |

| event_name | str (optional) | Event name for search | "Non-Farm Payrolls", "CPI" |

| country | str (optional) | Country code filter | "US", "GB", "EU" |

| lookback_timestamp | str (optional) | Only events after this date (ISO format) | "2024-01-01" |

| lookback_days | int (optional) | Days to look back from today | 365 |

| importance | str (optional) | Importance filter | "low", "medium", "high" |

| limit | int (optional) | Maximum results (default: 100) | 50 |



**Validation notes**:

- For `action="query_event"`, you must provide at least one of `event_id` or `event_name`.



### Outputs



**Output File**: `workspace/agents/eventdata-puller-agent/out/{id:06d}.json`



```json

{

  "data": [{

    "success": true,

    "events": [

      {

        "event_id": "USANFP",

        "event_name": "Non-Farm Payrolls",

        "country": "US",

        "category": "Labour",

        "importance": "high",

        "event_date": "2025-01-10T13:30:00+00:00",

        "actual": 256.0,

        "forecast": 165.0,

        "previous": 212.0,

        "unit": "K"

      }

    ],

    "count": 12,

    "summary": {

      "total_instances": 12,

      "actual_avg": 187.5,

      "beat_rate": 0.583

    }

  }],

  "metadata": {

    "query": "EventData action: query_event",

    "timestamp": "2025-11-27T12:00:00Z",

    "row_count": 1,

    "agent": "eventdata-puller-agent",

    "version": "1.0",

    "action": "query_event"

  }

}

```



---



## Trading Economics Tools



### fetch_economic_calendar Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| start_date | str (optional) | Start date (YYYY-MM-DD) | "2024-01-01" |

| end_date | str (optional) | End date (YYYY-MM-DD) | "2025-01-01" |

| country | str (optional) | Country code filter | "US" |

| importance | str (optional) | Importance filter | "high" |

| full_refresh | bool (optional) | Full history refresh (default: false) | true |



**Output**:



```json

{

  "success": true,

  "events_fetched": 150,

  "events_inserted": 45,

  "events_updated": 12,

  "date_range": {"start": "2024-01-01", "end": "2025-01-01"},

  "country_filter": "US",

  "database_path": "workspace/economic_events.db"

}

```



### query_event_history Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| event_id | str (optional) | Event ID/ticker | "USANFP" |

| event_name | str (optional) | Event name (partial match) | "Non-Farm Payrolls" |

| country | str (optional) | Country filter | "US" |

| lookback_timestamp | str (optional) | Start date filter - lower bound (ISO: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS) | "2024-01-01" |

| lookback_days | int (optional) | Days to look back from today (alternative to lookback_timestamp) | 365 |

| end_timestamp | str (optional) | End date filter - upper bound (ISO: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS). Use with lookback_timestamp for date ranges | "2024-12-31" |

| year | int (optional) | Filter by year. Can combine with month_filter | 2024 |

| month_filter | int (optional) | Filter by month (1-12). Can combine with year | 12 |

| limit | int (optional) | Max results (default: 100) | 50 |



**Output**:



```json

{

  "success": true,

  "events": [...],

  "count": 12,

  "summary": {

    "total_instances": 12,

    "actual_available": 12,

    "actual_avg": 187.5,

    "beat_rate": 0.583

  }

}

```



### get_events_by_date_range Tool



Get all economic events within a specific **date/time range**.



**Important behavior**:

- If you pass a **date-only** string (`YYYY-MM-DD`), it is treated as a **full-day window**:

  - start becomes `YYYY-MM-DDT00:00:00`

  - end becomes `YYYY-MM-DDT23:59:59`

- If `importance` is omitted / null, **no importance filter** is applied (i.e., returns **all** events for the window).



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| start_date | str | Start date or timestamp (ISO: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`) | `"2024-12-11"` |

| end_date | str | End date or timestamp (ISO: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`) | `"2024-12-11"` |

| country | str (optional) | Country code or full name (default: `US`) | `"US"` |

| importance | str \| null (optional) | Importance filter (`low`/`medium`/`high`) or `null` for all | `"high"` / `null` |

| limit | int (optional) | Max results (default: 100) | 100 |



**Output**:



```json

{

  "success": true,

  "events": [

    {"timestamp": "2024-12-11T08:30:00", "name": "CPI", "importance": "medium"}

  ],

  "count": 1,

  "metadata": {

    "start_date": "2024-12-11T00:00:00",

    "end_date": "2024-12-11T23:59:59",

    "country": "United States",

    "importance": null,

    "db_path": "workspace/economic_events.db"

  }

}

```



### WebSocket Streaming Tools



**start_event_stream**: Start real-time WebSocket stream

- Inputs: countries (list), categories (list), importance (list)

- Output: status, subscriptions



**stop_event_stream**: Stop WebSocket stream

- Output: status, final_stats



**get_live_events**: Get events from buffer

- Inputs: limit (int), since (str), include_db (bool)

- Output: events (list), stream_status, buffer_size



**get_stream_status**: Get stream status

- Output: status, is_running, subscriptions, statistics



---



## Timezone Configuration



The economic events database stores all timestamps in **US/Eastern local time** (EST/EDT). 

UTC timestamps from TradingEconomics API are automatically converted to local time during insertion using `zoneinfo.ZoneInfo("America/New_York")`, which handles DST automatically.



**Note**: No timezone offset configuration needed - DST is handled automatically based on the event date.



### Event Query Response Fields



| Field | Type | Description |

|-------|------|-------------|

| event_date | str | Local time timestamp (US/Eastern, EST/EDT) from database |

| timestamp | str | Alias for event_date (local time, for backward compatibility) |



---



## Economic Events Database



**Database**: `workspace/economic_events.db`



### economic_events Table



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| event_id | TEXT NOT NULL | Event identifier from Trading Economics |

| event_name | TEXT NOT NULL | Event name |

| country | TEXT NOT NULL | Country code (US, GB, EU, etc.) |

| category | TEXT | Event category (Labour, Prices, GDP, etc.) |

| importance | TEXT | Importance level (low, medium, high) |

| event_date | TEXT NOT NULL | Event date (ISO-8601) |

| actual | REAL | Actual value |

| forecast | REAL | Forecast/expected value |

| previous | REAL | Previous period value |

| revised | REAL | Revised value |

| unit | TEXT | Measurement unit |

| ticker | TEXT | Trading Economics ticker |

| source | TEXT | Data source (default: tradingeconomics) |

| created_at | TEXT NOT NULL | Record creation timestamp |

| updated_at | TEXT NOT NULL | Last update timestamp |



**Unique Constraint**: (event_id, event_date)



**Indices**:

- `idx_event_id` on `event_id`

- `idx_event_date` on `event_date`

- `idx_country` on `country`

- `idx_importance` on `importance`

- `idx_event_name` on `event_name`

- `idx_ticker` on `ticker`



### event_tags Table



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| start_timestamp | TEXT NOT NULL | Event start timestamp (ISO-8601) |

| end_timestamp | TEXT | Event end timestamp (ISO-8601), nullable |

| exact_event_time_known | INTEGER NOT NULL | 0/1 flag indicating exact event time is known |

| event_comments | TEXT | Freeform event notes |

| tags | TEXT | Tag list (comma-separated or JSON array) |

| market_impact | TEXT | Freeform market impact notes |

| created_at | TEXT NOT NULL | Record creation timestamp |



### live_event_stream Table



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| event_id | TEXT NOT NULL | Event identifier |

| event_name | TEXT NOT NULL | Event name |

| country | TEXT NOT NULL | Country code |

| category | TEXT | Event category |

| importance | TEXT | Importance level |

| event_date | TEXT NOT NULL | Event date |

| actual | REAL | Actual value |

| forecast | REAL | Forecast value |

| previous | REAL | Previous value |

| unit | TEXT | Measurement unit |

| ticker | TEXT | Trading Economics ticker |

| received_at | TEXT NOT NULL | WebSocket receive timestamp |

| source | TEXT | Source (default: websocket) |



**Unique Constraint**: (event_id, event_date, received_at)



**Indices**:

- `idx_live_event_id` on `event_id`

- `idx_live_received_at` on `received_at`

- `idx_live_event_date` on `event_date`



### date_tags Table



Trader notes and annotations for specific dates/times.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| tag_date | TEXT NOT NULL | Date (YYYY-MM-DD) for grouping |

| tag_datetime | TEXT | Full ISO datetime if time specified |

| has_time | INTEGER DEFAULT 0 | 1 if specific time given, 0 if date-only |

| comment | TEXT NOT NULL | Trader's note text |

| hashtags | TEXT | JSON array of hashtags (e.g., `["nfp", "fed"]`) |

| created_by | TEXT | Telegram username |

| created_at | TEXT NOT NULL | Tag creation timestamp |



**Indices**:

- `idx_date_tags_date` on `tag_date`

- `idx_date_tags_datetime` on `tag_datetime`

- `idx_date_tags_created_by` on `created_by`



---



## Date Tag Telegram Commands



### /tag Command



Add a date tag with flexible date/time parsing and inline hashtags.



**Format**: `/tag <datetime> [#hashtags] <comment>`



**Supported Date/Time Formats**:



| Format | Example | Parsed As |

|--------|---------|-----------|

| Natural date | `jan 10`, `10th january` | January 10, current year |

| Relative date | `yesterday`, `friday`, `3 days ago` | Past date (most recent) |

| Numeric date | `10-01`, `10/01` | DD-MM (January 10) |

| ISO date | `2026-01-10` | January 10, 2026 |

| With time | `jan 10 8:30am`, `jan 10 14:30` | Specific time EST |

| Flexible time | `8am`, `830am`, `2:30pm`, `noon` | Various time formats |

| Natural time | `morning`, `afternoon`, `evening` | ~09:00, ~13:00, ~17:00 |



**Settings**:

- Date order: DMY (DD-MM-YYYY)

- Prefer dates from: past

- Timezone: US/Eastern (EST/EDT)



**Examples**:

```

/tag jan 10 8:30am #nfp NFP +91K, TY sold off 15 ticks

/tag yesterday 2pm #fed Powell hawkish

/tag last friday #liquidity Thin markets

/tag 10-01 #technical Gap fill at 112-08

```



**Output**:

```json

{

  "success": true,

  "id": 47,

  "tag_date": "2026-01-10",

  "tag_datetime": "2026-01-10T08:30:00",

  "has_time": true,

  "comment": "NFP +91K, TY sold off 15 ticks",

  "hashtags": ["nfp"],

  "created_by": "username"

}

```



### /tags Command



View tags with optional filters.



**Format**: `/tags [date] [#hashtag]`



**Examples**:

```

/tags                    → Last 10 tags

/tags jan 10             → Tags for specific date

/tags #nfp               → Tags with hashtag

/tags jan 1 to jan 31    → Tags in date range

```



### /tagstats Command



Show tag statistics by hashtag.



**Output**:

```

📊 Tag Statistics



#nfp            ████████████ 24

#fed            █████████ 18

#liquidity      ██████ 12

(uncategorized) ████ 8



Total: 62 tags

```



### /untag Command



Delete a tag by ID.



**Format**: `/untag <id>`



---



## Plot Configuration



**File**: `config/plot_config.yaml`



Configuration file controlling all plot styling for core_plotting functions.



### Structure



```yaml

output:

  format: png

  dpi: 150

  scale: 2



colors:

  default:

    background: "#FFFFFF"

    primary: "#2E86AB"

    secondary: "#E94F37"

    accent: "#F6AE2D"

    text: "#333333"

    grid: "#E0E0E0"

    axis: "#666666"

    bullish: "#26A69A"

    bearish: "#EF5350"



dimensions:

  histogram: {width: 800, height: 500, bins: 20}

  candlestick: {width: 1600, height: 600}

  line_chart: {width: 800, height: 400}

  scatter: {width: 800, height: 600, point_size: 5}

  bar_chart: {width: 800, height: 400}



histogram:

  show_kde: true

  alpha: 0.7



stats_box:

  show: true

  position: top_left

  alpha: 0.9

```



### Core Plotting Functions



| Function | Description | Output |

|----------|-------------|--------|

| `plot_distribution(values, title, ...)` | Histogram with optional KDE | PNG path + metadata |

| `plot_timeseries(data, title, ...)` | Candlestick or line chart | PNG path + metadata |

| `plot_categorical(values, labels, ...)` | Bar chart | PNG path + metadata |

| `plot_correlation(x_values, y_values, ...)` | Scatter with regression | PNG path + metadata |

| `plot_multi_distribution(data_dict, ...)` | Grid of distributions | PNG path + metadata |

| `create_plot(data, context, ...)` | Auto-detect plot type | PNG paths + metadata |



### Plot Factory Contexts



| Context | Description | Auto-plots |

|---------|-------------|------------|

| `event_distribution` | Event return distributions | Multi-distribution grid |

| `returns_distribution` | Session-based returns | Session histograms |

| `volatility_distribution` | Session-based volatility | Session histograms |

| `candlestick` | OHLC price chart | Candlestick PNG |

| `correlation` | Scatter with regression | Scatter PNG |

| `surprise_analysis` | Surprise vs returns | Scatter + distribution |

| `probability_matrix` | Probability bar chart | Bar + distributions |

| `generic` | Auto-detect from data | Based on data structure |



---



## Distribution Analytics Tools



**Note**: These tools require optional dependencies. Install with `pip install pandas numpy scipy matplotlib plotly kaleido`



### compute_returns_distribution Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| symbol | str | Symbol to analyze | "ZN", "ZB", "ES" |

| interval | str (optional) | Data interval (default: "1h") | "1m", "15m", "1h", "1d" |

| start_date | str (optional) | Start date filter (YYYY-MM-DD) | "2024-01-01" |

| end_date | str (optional) | End date filter | "2025-01-01" |

| bps_factor | int (optional) | BPS multiplier (auto-detected if not provided) | 16 |

| output_dir | str (optional) | Plot output directory | "workspace/plots" |

| data_source | str (optional) | "database" or "parquet" | "database" |



**Output**:



```json

{

  "success": true,

  "symbol": "ZN",

  "interval": "1h",

  "bps_factor": 16,

  "data_points": 1250,

  "date_range": {"start": "2024-01-01", "end": "2025-12-04"},

  "statistics": {

    "London 0-7 ET": {"count": 250, "mean": 2.5, "std": 1.2, "50%": 2.1, "95%": 4.8},

    "US Open 7-10 ET": {...},

    "US Mid 10-15 ET": {...},

    "US Close 15-17 ET": {...},

    "Asia 18-24 ET": {...},

    "All day": {...}

  },

  "latest": {

    "date": "2025-12-04",

    "return": 3.2,

    "percentile": 75.3,

    "zscore": 1.1

  },

  "plot_paths": ["workspace/plots/ZN_1h_Returns_Distribution.png"],

  "stats_csv_path": "workspace/plots/ZN_1h_Returns_stats.csv"

}

```



### compute_volatility_distribution Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| symbol | str | Symbol to analyze | "ZN" |

| interval | str (optional) | Data interval | "1h" |

| start_date | str (optional) | Start date filter | "2024-01-01" |

| end_date | str (optional) | End date filter | "2025-01-01" |

| bps_factor | int (optional) | BPS multiplier | 16 |

| output_dir | str (optional) | Plot output directory | "workspace/plots" |



**Output**:



```json

{

  "success": true,

  "symbol": "ZN",

  "interval": "1h",

  "data_points": 1250,

  "statistics": {

    "London 0-7 ET": {"mean": 4.5, "std": 2.1, ...},

    ...

  },

  "latest": {

    "date": "2025-12-04",

    "volatility": 5.2,

    "percentile": 82.1,

    "zscore": 1.5

  },

  "plot_paths": ["workspace/plots/ZN_1h_Volatility_Distribution.png"]

}

```



### compute_probability_matrix Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| symbol | str | Symbol to analyze | "ZN" |

| interval | str (optional) | Data interval | "1h" |

| target_bps | float | Target basis points threshold | 2.0 |

| target_hrs | int | Target time horizon in hours | 6 |

| version | str (optional) | Analysis version: "Absolute", "Up", "Down", "NA" | "Absolute" |

| data_type | str (optional) | "Event" or "Non-Event" | "Non-Event" |

| output_dir | str (optional) | Plot output directory | "workspace/plots" |



**Output**:



```json

{

  "success": true,

  "symbol": "ZN",

  "target_bps": 2.0,

  "target_hrs": 6,

  "version": "Absolute",

  "probabilities": {

    "open-close": {"prob_greater": 45.2, "prob_less_equal": 54.8},

    "open-high/low": {...},

    "high-low": {...}

  },

  "matrices": {

    "open-close": {...}

  },

  "latest_movements": {

    "open-close": [{"Datetime": "...", "Bps moved": 1.5}, ...]

  },

  "plot_paths": ["workspace/plots/ProbMatrix_ZN_Absolute_open-close.png", ...],

  "description": "Probability of ZN moving 2.0 bps within 6 hours"

}

```



### compute_event_distribution Tool



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| symbol | str | Symbol to analyze | "ZN" |

| event_name | str | Economic event name | "NFP", "CPI", "FOMC" |

| interval | str (optional) | Data interval | "1h" |

| delta_hrs | int (optional) | Hours to analyze around event | 1 |

| delta_omit | int (optional) | Hours to omit before/after | 0 |

| country | str (optional) | Country filter | "US" |

| filter_isolated | bool (optional) | Only isolated events | false |

| last_x_obs | int (optional) | Limit to last X observations | 50 |

| output_dir | str (optional) | Plot output directory | "workspace/plots" |



**Output**:



```json

{

  "success": true,

  "symbol": "ZN",

  "event_name": "NFP",

  "interval": "1h",

  "delta_hrs": 1,

  "event_count": 24,

  "statistics": {

    "Absolute Return": {"count": 24, "mean": 5.2, "std": 2.1, "95%": 9.5},

    "Return": {...},

    "Volatility Return": {...}

  },

  "latest": {

    "date": "2025-12-04",

    "absolute_return": 6.5,

    "return": 4.2,

    "volatility_return": 8.1,

    "percentile": 78.2

  },

  "events_analyzed": [

    {"date": "2025-12-04", "event_name": "Non-Farm Payrolls", "absolute_return": 6.5},

    ...

  ],

  "plot_paths": ["workspace/plots/EventDistro_ZN_NFP_Absolute_Return.png", ...]

}

```



### plot_log_bps_distribution Tool



Plots event distribution with log-transformed BPS on x-axis and KDE overlay.

Histogram bins have fixed width of 1 in log scale.



**Inputs**:



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| symbol | str | Symbol to analyze | "TY", "ZN", "ES" |

| event_name | str | Economic event name (must match SUB_EVENT_DICT keys) | "Non Farm Payrolls", "CPI" |

| return_type | str (optional) | "absolute", "signed", or "volatility" | "absolute" |

| delta_hrs | int (optional) | Hours to analyze around event | 1 |

| country | str (optional) | Country filter | "US" |

| last_x_obs | int (optional) | Limit to last X observations | 50 |

| log_base | float (optional) | Base for log transform | 10.0 |



**Output**:



```json

{

  "success": true,

  "symbol": "TY",

  "event_name": "Non Farm Payrolls",

  "return_type": "absolute",

  "log_base": 10.0,

  "event_count": 35,

  "log_bps_stats": {

    "count": 35,

    "mean": 0.84,

    "std": 0.31,

    "min": 0.0,

    "max": 1.29,

    "median": 0.89,

    "skewness": -1.34,

    "kurtosis": 1.36,

    "percentile_5": 0.07,

    "percentile_95": 1.19

  },

  "bps_stats": {

    "mean": 7.4,

    "std": 4.3,

    "min": 0.0,

    "max": 18.5

  },

  "bin_edges": [0.0, 1.0, 2.0, 3.0],

  "plot_path": "workspace/distro_runs/LogBPS_TY_Non Farm Payrolls_absolute_20251212_232218.png"

}

```



**Note**: Event names must match SUB_EVENT_DICT keys exactly (e.g., "Non Farm Payrolls" not "NFP" or "Nonfarm Payrolls").



---



## Scheduled Event Runner



The Scheduled Event Runner is a proactive scheduling system for economic event analysis.

It pre-fetches calendar data, runs live surprise analysis before events, and pushes alerts to Telegram.



### Scheduled Events CSV Input



**File**: `config/scheduled_events.csv`



CSV file defining which economic events to track and analyze.



| Column | Type | Required | Description | Example |

|--------|------|----------|-------------|---------|

| event_name | str | Yes | Event name (matched against Trading Economics) | "Non-Farm Payrolls" |

| country | str | No | Country code (default: US) | "US", "GB", "EU" |

| importance | str | No | Importance filter | "low", "medium", "high" |

| symbols | str | No | Comma-separated symbols to analyze (default: TY) | "TY,ES,NQ" |

| telegram_chat_id | str | No | Override chat ID for this event | "-1001234567890" |



**Example CSV**:



```csv

event_name,country,importance,symbols,telegram_chat_id

"Non-Farm Payrolls",US,high,"TY,ES",

"JOLTS Job Openings",US,medium,TY,

"CPI YoY",US,high,"TY,ES,NQ",

```



### Scheduled Events Database



**Database**: `workspace/scheduled_events.db`



#### scheduled_events Table



Stores scheduled event instances with status tracking.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| event_id | TEXT | Event ID/ticker from Trading Economics |

| event_name | TEXT NOT NULL | Event name |

| country | TEXT | Country code |

| importance | TEXT | Importance level |

| event_timestamp | TEXT NOT NULL | Event datetime (ISO-8601) |

| symbols | TEXT | Comma-separated symbols to analyze |

| telegram_chat_id | TEXT | Override Telegram chat ID |

| status | TEXT NOT NULL | 'scheduled', 'running', 'completed', 'failed', 'skipped' |

| csv_source_row | INTEGER | Row number from source CSV |

| created_at | TEXT NOT NULL | Record creation timestamp |

| last_run_at | TEXT | Last run timestamp |

| result_summary | TEXT | Summary of results (for completed) |

| error_message | TEXT | Error message (for failed) |



**Unique Constraint**: (event_id, event_timestamp)



**Indices**:

- `idx_scheduled_events_status_timestamp` on (status, event_timestamp)



#### event_run_history Table



Stores execution history for each scheduled event run.



| Column | Type | Description |

|--------|------|-------------|

| id | INTEGER PRIMARY KEY | Auto-incrementing ID |

| scheduled_event_id | INTEGER NOT NULL | FK to scheduled_events.id |

| run_timestamp | TEXT NOT NULL | Run execution timestamp |

| status | TEXT NOT NULL | 'completed' or 'failed' |

| percentile_surprise | REAL | Percentile rank of surprise |

| z_score | REAL | Z-score of surprise |

| market_return_bps | REAL | Market return in basis points |

| telegram_sent | INTEGER | 1 if Telegram alert sent |

| telegram_message_id | TEXT | Telegram message ID if sent |

| plot_paths | TEXT | JSON array of plot file paths |

| full_result | TEXT | JSON of full analysis result |

| error_message | TEXT | Error message if failed |

| duration_ms | REAL | Execution duration in milliseconds |



### Environment Variables



| Name | Kind | Type | Description | Example |

|------|------|------|-------------|---------|

| X_BEARER_TOKEN | EnvVar | str | X API v2 bearer token (filtered stream + users lookup) | "AAAAAAAA..." |

| BBG_GRAPH_AUTH_MODE | EnvVar | str | `app` for app-only client credentials or `delegated` for device-code user sign-in. Default `app`. | "delegated" |

| BBG_MAIL_SOURCE | EnvVar | str | `outlook_com` or `graph`; selects local Outlook Desktop COM or Microsoft Graph backend. Default `graph`. | "outlook_com" |

| BBG_NEWS_MODE | EnvVar | str | `event` or `poll`; event mode uses Outlook `ItemAdd` push. Default `poll`. | "event" |

| GRAPH_TENANT_ID | EnvVar | str | Azure tenant id for Microsoft Graph mailbox access | "00000000-0000-0000-0000-000000000000" |

| GRAPH_CLIENT_ID | EnvVar | str | Azure app registration client id for Microsoft Graph mailbox access | "00000000-0000-0000-0000-000000000000" |

| GRAPH_CLIENT_SECRET | EnvVar | str \| None | Azure app registration client secret; required only for app-only mode | "..." |

| GRAPH_TOKEN_CACHE_PATH | EnvVar | str | Optional delegated token cache path; defaults to `~/.market_data/graph_delegated_token.json` | "C:\\Users\\aasth\\.market_data\\graph_delegated_token.json" |

| BBG_MAILBOX | EnvVar | str | Outlook mailbox UPN read by `scripts/bbg_news_stream_bot.py`; in delegated mode should match the signed-in user | "you@firm.com" |

| BBG_NEWS_SENDERS | EnvVar | str | Comma-separated Bloomberg sender addresses/domains accepted from the mailbox | "noreply@bloomberg.net,bloomberg.net" |

| BBG_NEWS_POLL_SECONDS | EnvVar | int | Bloomberg email news collector polling interval in seconds; default `60` | "60" |

| BBG_NEWS_DB_PATH | EnvVar | str | Optional override for Bloomberg email news moderation DB; defaults to `~/.market_data/bbg_news.db` | "C:\\Users\\aasth\\.market_data\\bbg_news.db" |

| BBG_NEWS_WATERMARK_PATH | EnvVar | str | Optional override for Bloomberg email news received-time watermark file | "C:\\Users\\aasth\\.market_data\\bbg_news_watermark.txt" |

| TELEGRAM_BOT_TOKEN | EnvVar | str | Telegram Bot API token | "123456:ABC-DEF..." |

| TELEGRAM_CHAT_ID | EnvVar | str | Default Telegram chat/channel ID | "-1001234567890" |

| X_MACRO_CHAT_ID | EnvVar | str | Override chat/channel IDs for X macro stream bot (comma-separated) | "-5216893879" |

| X_MACRO_PUBLIC_CHAT_ID | EnvVar | str | Public group chat ID for approved macro tweets | "-3486536567" |



**Configuration File**: `config/keys.env`



Add these lines to enable Telegram alerts:



```

TELEGRAM_BOT_TOKEN=your_bot_token_here

TELEGRAM_CHAT_ID=your_chat_id_here

X_MACRO_CHAT_ID=-5216893879

X_MACRO_PUBLIC_CHAT_ID=-3486536567

GRAPH_TENANT_ID=your_azure_tenant_id

GRAPH_CLIENT_ID=your_app_client_id

BBG_GRAPH_AUTH_MODE=delegated

BBG_MAIL_SOURCE=outlook_com

BBG_NEWS_MODE=event

GRAPH_CLIENT_SECRET=your_app_client_secret

BBG_MAILBOX=you@firm.com

BBG_NEWS_SENDERS=noreply@bloomberg.net,bloomberg.net

BBG_NEWS_POLL_SECONDS=60

```



### CLI Commands



```bash

# Build schedule from CSV (weekly)

python scripts/scheduled_event_runner.py --build-schedule config/scheduled_events.csv



# Run once (check for due events)

python scripts/scheduled_event_runner.py --run-once



# Run in continuous loop

python scripts/scheduled_event_runner.py --loop



# Show schedule status

python scripts/scheduled_event_runner.py --status



# Test Telegram connection

python scripts/scheduled_event_runner.py --test-telegram

```



### CLI Options



| Option | Type | Default | Description |

|--------|------|---------|-------------|

| --trigger-minutes | int | 2 | Minutes before event to trigger analysis |

| --days-ahead | int | 7 | Days ahead to fetch events for |

| --check-interval | int | 30 | Loop check interval in seconds |

| --symbols | str | "TY" | Default symbols to analyze |

| --output-dir | str | workspace/plots | Output directory for plots |

| --no-telegram | flag | false | Disable Telegram alerts |

| --verbose | flag | false | Enable verbose logging |



### Analysis Output



The scheduler calls `analyze_event_data()` from `jolts_live_analyzer.py` which returns:



```json

{

  "success": true,

  "event_name": "Non-Farm Payrolls",

  "event_timestamp": "2025-01-10T13:30:00+00:00",

  "country": "United States",

  "latest_event": {

    "actual": 256.0,

    "consensus": 165.0,

    "previous": 212.0,

    "surprise": 91.0

  },

  "historical_count": 72,

  "current_surprise": 91.0,

  "percentile_surprise": 94.5,

  "z_score": 2.1,

  "market_results": {

    "success": true,

    "symbol": "TY",

    "events_analyzed": 72,

    "latest": {

      "return_bps": -8.5,

      "absolute_return_bps": 8.5,

      "percentile": 85.2,

      "zscore": 1.8

    }

  },

  "plot_paths": [

    "workspace/plots/NFP_surprise_distribution.png",

    "workspace/plots/NFP_TY_reaction_distribution.png"

  ],

  "duration_ms": 1523.4

}

```



### Telegram Alert Format



Alerts are formatted as HTML messages:



```

📊 Non-Farm Payrolls

2025-01-10T13:30:00+00:00



Actual: 256.0K

Consensus: 165.0K

Previous: 212.0K



Surprise: +91.0K 🟢 BEAT



Analysis:

  • Percentile: 94.5%

  • Z-Score: +2.10



TY Reaction: -8.5 bps

```



---



## Reasoning/Runner Agent



The runner agent (`RunnerAgent`) generates final consolidated answers using GPT-class models.



### Configuration



| Name | Kind | Type | Default | Description |

|------|------|------|---------|-------------|

| REASONING_MODEL_NAME | EnvVar | str | "gpt-4o" | Model name for reasoning |

| REASONING_TEMPERATURE | EnvVar | float | 0.2 | Temperature for response generation |

| REASONING_MAX_TOKENS | EnvVar | int | 1500 | Maximum tokens for response |



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| query | str | Original user query | "What were Bitcoin predictions and how do they compare to futures?" |

| worker_outputs | list[dict] | Task outputs from WorkersDB | `[{"task_id": "task_1", "agent_name": "polymarket_agent", ...}]` |

| planning_table | list[dict] (optional) | Planning rows from WorkersDB | `[{"task_id": "task_1", ...}]` |

| run_id | str (optional) | Orchestration run identifier | "20251125_120000" |



### Outputs



**Output File**: `workspace/agents/reasoning-agent/out/{id:06d}.json`



```json

{

  "data": [{

    "final_answer": "…final user-facing answer…",

    "reasoning_metadata": {

      "provider": "openai",

      "model": "gpt-4o",

      "temperature": 0.2,

      "max_tokens": 1500

    },

    "worker_outputs": [...],

    "planning_table": [...]

  }],

  "metadata": {

    "query": "RunnerAgent final answer for: <user query>",

    "timestamp": "2025-12-10T12:00:00Z",

    "row_count": 1,

    "agent": "reasoning-agent",

    "version": "1.0",

    "run_id": "<run id>"

  }

}

```



---



## Analytics Agent



The analytics agent performs statistical analysis and visualization on **provided** data (no DB access).



### Inputs



| Name | Type | Description | Example |

|------|------|-------------|---------|

| analysis_type | str | Type of analysis | "descriptive", "percentile_rank", "comparison", "correlation" |

| params | dict | Analysis-specific parameters | `{"data": [1,2,3], "title": "My Distribution"}` |

| generate_plot | bool (optional) | Generate visualization | true |



### Analysis Types



| Type | Description | Required Params |

|------|-------------|-----------------|

| descriptive | Basic statistics | data |

| percentile_rank | Rank value in distribution | value, reference_data |

| comparison | Compare two distributions | data_a, data_b, label_a, label_b |

| correlation | Correlation between variables | data_x, data_y |



### Outputs



**Output File**: `workspace/agents/analytics-agent/out/{id:06d}.json`



```json

{

  "data": {

    "success": true,

    "statistics": {

      "count": 20,

      "mean": 12.85,

      "median": 12.75,

      "std_dev": 1.36,

      "min": 10.5,

      "max": 15.1,

      "percentiles": {"5": 10.8, "50": 12.75, "95": 15.0}

    },

    "histogram_path": "workspace/agents/analytics-agent/plots/distribution.svg"

  },

  "metadata": {

    "query": "descriptive: {...}",

    "timestamp": "2025-12-10T12:00:00Z",

    "analysis_type": "descriptive",

    "duration_ms": 120.5

  }

}

```



---



## Surprise/Reversion Analysis Scripts



These scripts analyze economic event surprises and price mean-reversion for martingale strategy evaluation.



### surprise_predictive_power_analysis.py



| CLI Arg | Type | Default | Description |

|---------|------|---------|-------------|

| --event | str | required | Event name (e.g., "JOLTS Job Openings") |

| --symbol | str | "TY" | Market symbol |

| --buckets | int | 5 | Number of percentile buckets |

| --min-bucket-size | int | 5 | Minimum events per bucket |

| --list-events | flag | false | List available events |



### surprise_mean_reversion_analysis.py



| CLI Arg | Type | Default | Description |

|---------|------|---------|-------------|

| --event | str | required | Event name |

| --country | str | "US" | Country filter |

| --symbol | str | "TY" | Market symbol |

| --history-limit | int | 100 | Max events to analyze |

| --post-event-mins | int | 120 | Minutes after event to analyze |

| --json | flag | false | Output JSON instead of report |



### extension_reversion_analysis.py



| CLI Arg | Type | Default | Description |

|---------|------|---------|-------------|

| --event | str | - | Event name (mutually exclusive with --category) |

| --category | str | - | Event category to pool (e.g., "Labor") |

| --country | str | "US" | Country filter ("all" for all) |

| --symbol | str | "TY" | Market symbol |

| --history-limit | int | 100 | Max events per type |

| --post-event-mins | int | 180 | Minutes to analyze |



---



## Event Dictionary JSON



**File**: `config/event_dictionary.json`



Pre-built dictionary of all available economic events from Trading Economics API. Generated by `scripts/build_event_dictionary.py`.



```json

{

  "by_country": {

    "united states": {

      "Non-Farm Payrolls": {

        "category": "Labour",

        "importance": 3,

        "ticker": "USANFP",

        "example_date": "2025-01-10"

      },

      "CPI YoY": {...}

    },

    "germany": {...}

  },

  "by_event": {

    "Non-Farm Payrolls": ["united states"],

    "CPI YoY": ["united states", "germany", "united kingdom", ...]

  },

  "metadata": {

    "generated_at": "2025-12-10T10:00:00Z",

    "countries_count": 25,

    "unique_events": 450,

    "total_country_event_pairs": 1200

  }

}

```



**Usage**: Reference for exact event naming in analysis scripts without repeated API calls.



---



## Powell Speech Sentiment Analyzer



**Script**: `scripts/powell_speech_analyzer.py`



### Constants



| Name | Kind | Type | Value/Range | Description |

|------|------|------|-------------|-------------|

| DEFAULT_STREAMTEXT_URL | Constant | str | "https://www.streamtext.net/player?event=CFI-FRB&chat=false" | Default StreamText URL for Fed events |

| POLL_INTERVAL_SECONDS | Constant | float | 2.0 | Seconds between transcript polls |

| MIN_CHUNK_SIZE | Constant | int | 50 | Minimum characters to trigger LLM analysis |

| SHIFT_THRESHOLD | Constant | float | 2.0 | Score change that triggers alert |

| RECENCY_WEIGHT | Constant | float | 0.7 | Weight for exponential moving average |



### CLI Arguments



| Name | Kind | Type | Default | Description |

|------|------|------|---------|-------------|

| --url | Input | str | DEFAULT_STREAMTEXT_URL | StreamText URL to monitor |

| --test | Input | bool | False | Run test mode with sample speech excerpts |

| --no-color | Input | bool | False | Disable colored console output |

| --output-dir | Input | str | workspace/ | Directory for JSON output files |

| --verbose | Input | bool | False | Enable debug logging |



### ChunkAnalysis Output Schema



| Field | Type | Range/Values | Description |

|-------|------|--------------|-------------|

| timestamp | str | HH:MM:SS | Time of analysis |

| text | str | ≤200 chars | Analyzed text (truncated) |

| score | float | -10 to +10 | Sentiment score |

| label | str | VERY DOVISH/DOVISH/NEUTRAL/HAWKISH/VERY HAWKISH | Sentiment label |

| topic | str | Employment/Inflation/Rate Policy/Economic Outlook/Financial Conditions/Other | Primary topic |

| key_phrase | str | ≤60 chars | Most significant phrase |

| reasoning | str | ≤100 chars | Brief explanation |



### Sentiment Score Ranges



| Score Range | Label | Market Interpretation |

|-------------|-------|----------------------|

| +7 to +10 | VERY HAWKISH | Rates staying high, inflation concerns |

| +3 to +6 | HAWKISH | Cautious about cutting |

| -2 to +2 | NEUTRAL | Data-dependent, balanced |

| -6 to -3 | DOVISH | Leaning toward cuts |

| -10 to -7 | VERY DOVISH | Clear cut signals |



### Output File



**Path**: `workspace/powell_speech_{timestamp}.json`



```json

{

  "meta": {

    "generated_at": "2025-12-10T10:00:00",

    "chunks_analyzed": 10,

    "final_score": -4.0,

    "final_label": "DOVISH"

  },

  "summary": {

    "scores": [5, 6, 5, 0, -2, -6, -4, -4, -4, -4],

    "average_score": -0.8

  },

  "chunks": [

    {

      "timestamp": "10:00:43",

      "text": "The labor market remains...",

      "score": 5.0,

      "label": "HAWKISH",

      "topic": "Employment",

      "key_phrase": "labor market remains strong",

      "reasoning": "Strong employment signals rate maintenance"

    }

  ]

}

```



---



## Pre-Event B/E Dashboard



**Script**: `scripts/preevent_boxplot_dashboard.py`



Interactive HTML dashboard for analyzing pre-event B/E multiplier across **consensus std dev buckets** with price paths from 3pm previous day to 8:30am event day.



### build_preevent_dashboard() Function



**Inputs**:



| Parameter | Type | Default | Description |

|-----------|------|---------|-------------|

| event_name | str | "NFP" | Event name (currently only NFP supported) |

| consensus | float | None | Current consensus value |

| cons_std | float | None | Current consensus std dev |



**Output**:



```json

{

  "success": true,

  "event_name": "NFP",

  "html_path": "workspace/scenarios/preevent_dashboard_20260105_120000.html",

  "event_count": 72,

  "cons_std_range": "18.5-42.3"

}

```



### CLI Usage



```bash

python scripts/preevent_boxplot_dashboard.py

python scripts/preevent_boxplot_dashboard.py --consensus 180 --cons_std 25

```



### Telegram Command



```

/pre_event NFP

/pre_event NFP 180 25

```



**Output Path**: `workspace/scenarios/preevent_dashboard_{timestamp}.html`



### Embedded Data Structures



Each event in the dashboard contains:



| Field | Type | Description |

|-------|------|-------------|

| date | str | Event date (YYYY-MM-DD) |

| fwd | float | Forward breakeven in bps |

| cons_std | float | Consensus standard deviation |

| be_by_time | Dict[str, float] | B/E multiplier by end time (HH:MM) |

| prices | List[Dict] | 1-min normalized price path (ts, bps, price, high) |

| candlestick_5m | List[Dict] | 5-min OHLC bars for +/-5 days (ts, open, high, low, close, volume) |

| event_time | str | Event timestamp (YYYY-MM-DDTHH:MM:SS) for vertical marker |

| other_events | List[Dict] | Other tier 1/2 events on same day |



### Candlestick Modal



Interactive Plotly candlestick chart with:

- Duration slider: +/-1 to +/-5 days

- 5-minute OHLC bars (~2400 candles per event)

- NFP release vertical marker (8:30am)

- Zoom/pan/rangeslider support

- Dark theme matching dashboard



---



## Post-Event B/E Dashboard



**Script**: `scripts/postevent_boxplot_dashboard.py`



Interactive HTML dashboard for analyzing post-event B/E multiplier across **surprise percentile buckets** with price paths from 8:30am to 3pm on event day.



### Key Calculations



| Name | Formula | Description |

|------|---------|-------------|

| Surprise | `actual - consensus` | Raw surprise (difference between actual and expected) |

| Standardized Surprise | `(actual - consensus) / cons_std` | Z-score normalized surprise |

| Surprise Percentile | `count(historical_std <= std_surprise) / total` | Rank of standardized surprise vs history (0-1) |

| B/E Multiplier | `(high - open) * 32 / 2 / FWD` | Breakeven move relative to forward |



### build_postevent_dashboard() Function



**Inputs**:



| Parameter | Type | Default | Description |

|-----------|------|---------|-------------|

| event_name | str | "NFP" | Event name (currently only NFP supported) |

| actual | float | None | Actual NFP value (K) |

| consensus | float | None | Consensus value (K) |

| cons_std | float | None | Consensus std dev (K), used to calculate standardized surprise |



**Output**:



```json

{

  "success": true,

  "event_name": "NFP",

  "html_path": "workspace/scenarios/postevent_dashboard_20260105_120000.html",

  "event_count": 72,

  "surprise_range": "-91K to 256K",

  "surp_pct_range": "3.6% to 100.0%",

  "user_surprise": 91.0,

  "user_std_surprise": 3.64,

  "user_surp_pct": 0.82

}

```



### CLI Usage



```bash

python scripts/postevent_boxplot_dashboard.py

python scripts/postevent_boxplot_dashboard.py --actual 256 --consensus 165

python scripts/postevent_boxplot_dashboard.py --actual 256 --consensus 165 --cons-std 25

```



### Telegram Command



```

/post_event NFP

/post_event NFP 256

/post_event NFP 256 165

/post_event NFP 256 165 25

```



### Dashboard HTML Features



Self-contained HTML file with embedded JavaScript for:

- **Input fields**: Actual value, Consensus, Cons Std Dev, Bucket width (percentile)

- **Calculated values panel**: Live surprise, standardized surprise, and percentile calculation

  - Standardized Surprise = (Actual - Consensus) / Cons Std Dev

  - Percentile is computed as rank of standardized surprise among historical values

- **Box plot**: B/E distribution by surprise percentile bucket (blue/green theme)

- **Price paths**: Normalized to 8:30am start for events in user's bucket

- **Event details**: Click to see NFP actual/consensus, cons_std, surprise, standardized surprise, B/E, prices

- **Time slider**: Adjustable end time (8:30am → 3pm)



**Output Path**: `workspace/scenarios/postevent_dashboard_{timestamp}.html`



---



## January Auction Weeks Dashboard



**File**: `scripts/january_auction_weeks.html`



Interactive HTML dashboard comparing January auction weeks vs other months with price path overlays and distribution histograms.



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| histogram_hours_input | Input | float | `> 0`; snaps to nearest available dataset in `histogramData` | `3.5` |

| histogram_hours_resolved | Internal | str | One of `histogramData` keys (e.g., `"1h"`, `"2h"`, `"4h"`) | `"2h"` |



---



## 2Y/5Y/7Y Supply Weeks Dashboard



**Script**: `scripts/supply_weeks_dashboard.py`  

**Outputs**: `scripts/supply_weeks_dashboard.html`, `dashboards/supply_weeks_dashboard.html`



Standalone TY dashboard using processed auction CSVs from `auction_data/` and TY 1-minute OHLC from PostgreSQL `ohlcv_1m`.



### Inputs (CLI / Constants)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--dashboards-output` | Input | `Path \| ""` | Writable HTML path, or empty string to skip mirror output | `--dashboards-output dashboards/supply_weeks_dashboard.html` |

| `--min-size` | Input | `float` | `>= 0`; default `40000000000`; preferred-size threshold when choosing among same-tenor auctions in the same ISO week | `--min-size 40000000000` |

| `--output` | Input | `Path` | Writable HTML path | `--output scripts/supply_weeks_dashboard.html` |

| `--start-year` | Input | `int` | `>= 1900`; default `2015` | `--start-year 2020` |

| `AUCTION_TIMES` | Constant | `dict[str, str]` | Fixed approximate auction times: `2Y=11:30`, `5Y=13:00`, `7Y=13:00` | Used to align dashboard windows |

| `VALID_TY_MIN` | Constant | `float` | TY close lower bound; default `80.0` | `80.0` |

| `VALID_TY_MAX` | Constant | `float` | TY close upper bound; default `150.0` | `150.0` |



### Core Output/Data Shapes



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `candles_5m` | Output | `dict[str, list]` | 5-minute OHLC arrays, trimmed to 2Y/5Y/7Y auction days | `{"timestamps":[...], "opens":[...], ...}` |

| `distro_annotate_january` | Input | `bool` | Default `true`; controls January marker lines and labels only | Uncheck `Annotate January` for a clean distribution chart |

| `distro_annotate_month_end` | Input | `bool` | Default `true`; controls Month-End marker lines and labels only | Uncheck `Annotate Month-End` for a clean distribution chart |

| `distro_measure` | Input | `str` | `max_move`, `end_move`, `max_up`, `max_down`, `range` | `"max_move"` |

| `distro_window` | Input | `str` | `full`, `pre_2y`, `2y_to_5y`, `5y_to_7y`, `post_7y`, `auction_day_2y`, `auction_day_5y`, `auction_day_7y` | `"2y_to_5y"` |

| `price_data.closes` | Output | `list[float]` | TY close prices in valid range filter | `[109.75, 109.734375, ...]` |

| `price_data.hours` | Output | `list[float]` | Hours from Sunday 6pm in week window | `[0.0, 0.0167, ...]` |

| `type_filter` | Input | `str` | `all`, `month_end`, `non_month_end`, `january`, `january_month_end` | `"month_end"` |

| `week_info.2y_date` | Output | `str` | `YYYY-MM-DD HH:MM` | `"2026-05-26 11:30"` |

| `week_info.5y_date` | Output | `str` | `YYYY-MM-DD HH:MM` | `"2026-05-27 13:00"` |

| `week_info.7y_date` | Output | `str` | `YYYY-MM-DD HH:MM` | `"2026-05-28 13:00"` |

| `week_info.is_consecutive` | Output | `bool` | `true` when unique auction dates are consecutive calendar days | `true` |

| `week_info.is_january` | Output | `bool` | `true` when 7Y auction month is January | `false` |

| `week_info.is_month_end` | Internal | `bool` | `true` when 7Y auction day is in the last 7 calendar days of its month | `true` |

| `week_info.week_start` | Output | `str` | `YYYY-MM-DD` Monday of matched ISO week | `"2026-05-25"` |



---



## 10Y/30Y Auction Weeks Dashboard



**Script**: `scripts/ten_thirty_auction_weeks.py`  

**Output**: `scripts/ten_thirty_auction_weeks_dashboard.html`



Standalone TY dashboard using only auction dates from `workspace/economic_events.db` (`10-Year Note Auction`, `30-Year Bond Auction`) and market data from PostgreSQL `ohlcv_1m`.



### Inputs (CLI / Constants)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `--start-year` | Input | `int` | `>= 1900`; default `2015` | `--start-year 2020` |

| `TEN_EVENT_NAME` | Constant | `str` | Fixed event filter | `"10-Year Note Auction"` |

| `THIRTY_EVENT_NAME` | Constant | `str` | Fixed event filter | `"30-Year Bond Auction"` |

| `EVENT_DB` | Constant | `Path` | Existing SQLite file | `workspace/economic_events.db` |

| `VALID_TY_MIN` | Constant | `float` | TY close lower bound; default `80.0` | `80.0` |

| `VALID_TY_MAX` | Constant | `float` | TY close upper bound; default `150.0` | `150.0` |



### Core Output/Data Shapes



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| `week_info.week_start` | Output | `str` | `YYYY-MM-DD` Monday of matched week | `"2025-11-10"` |

| `week_info.10y_date` | Output | `str` | `YYYY-MM-DD HH:MM` | `"2025-11-12 13:00"` |

| `week_info.30y_date` | Output | `str` | `YYYY-MM-DD HH:MM` | `"2025-11-13 13:00"` |

| `week_info.is_consecutive` | Output | `bool` | `true` when 30Y date = 10Y date + 1 day | `true` |

| `price_data.hours` | Output | `list[float]` | Hours from Sunday 6pm in week window | `[0.0, 0.0167, ...]` |

| `price_data.closes` | Output | `list[float]` | TY close prices in valid range filter | `[109.75, 109.734375, ...]` |

| `candles_5m` | Output | `dict[str, list]` | 5-minute OHLC arrays for modal drilldown | `{"timestamps":[...], "opens":[...], ...}` |

| `distro_window` | Input | `str` | `full`, `pre_10y`, `10y_to_30y`, `post_30y`, `auction_day_10y`, `auction_day_30y` | `"10y_to_30y"` |

| `distro_measure` | Input | `str` | `max_move`, `end_move`, `max_up`, `max_down`, `range` | `"max_move"` |



---



## Geopolitical Events Analysis Dashboard



**Script**: `scripts/geo_analysis.py`  

**Output**: `scripts/geo_analysis.html`



Interactive HTML dashboard showing TY price paths for 21 major geopolitical events (2022-2026) including Ukraine/Russia, Israel-Iran, Houthis/Red Sea, Hamas Oct 7, and US Venezuela.



### CLI Arguments



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| geo_analysis.output | Input | Path | Any writable path | `--output scripts/geo_analysis.html` |

| geo_analysis.no_perplexity | Input | bool | true/false | `--no-perplexity` |

| geo_analysis.perplexity_cache | Input | Path | Any writable path | `--perplexity-cache workspace/analysis/geo_fade_cache.json` |

| geo_analysis.perplexity_refresh | Input | bool | true/false | `--perplexity-refresh` |



### GEO_EVENTS_RAW Fields



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| id | Constant | int | 1-21 | `1` |

| name | Constant | str | Event headline | `"Russia invades Ukraine (full-scale)"` |

| category | Constant | str | Event category | `"Ukraine-Russia"`, `"Israel-Iran"`, `"Houthis-Red Sea"`, `"Venezuela"` |

| notes | Constant | str | Event description | `"Putin announces 'special military operation'"` |

| summary | Constant | str | Curated market-impact narrative | `"Sharp risk-off into early NY; TY spiked then faded..."` |

| date | Constant | str | YYYY-MM-DD | `"2022-02-24"` |

| event_time_local | Constant | str | HH:MM or empty if unknown | `"05:00"` |

| source_tz | Constant | str | IANA timezone | `"Europe/Moscow"`, `"Asia/Jerusalem"`, `"America/New_York"` |

| day_of_week | Constant | str | Day name | `"Thursday"` |

| multi_day | Constant | list[str] | Optional list of dates if event spans multiple days | `["2024-04-13", "2024-04-14"]` |



### Per-Event Stats (computed)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| stats.day_return | Output | float | bps | `-2.5` |

| stats.day_volatility | Output | float | bps, >= 0 | `8.3` |

| stats.max_up_day | Output | float | bps | `+4.2` |

| stats.max_down_day | Output | float | bps | `-3.8` |

| stats.max_up_5m | Output | float | bps, max move in first 5 minutes | `+1.2` |

| stats.max_down_5m | Output | float | bps | `-0.8` |

| stats.max_up_1h | Output | float | bps, max move in first hour | `+2.5` |

| stats.max_down_1h | Output | float | bps | `-1.9` |

| stats.has_data | Output | bool | True if price data available | `true` |

| unusual | Output | bool | True if max move >= cross-event P95 | `false` |



### Spike Window Stats (computed)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| spike_start_ts | Output | str | ISO timestamp for spike start | `"2024-04-14T01:05:00"` |

| spike_end_ts | Output | str | ISO timestamp for spike end | `"2024-04-14T02:05:00"` |

| spike_return_bps | Output | float | bps | `+4.6` |

| spike_volatility_bps | Output | float | bps, >= 0 | `7.2` |

| spike_max_up_bps | Output | float | bps | `+5.1` |

| spike_max_down_bps | Output | float | bps | `-3.4` |

| spike_max_move_bps | Output | float | bps | `5.1` |

| spike_window_minutes | Output | int | Minutes actually used for spike window (may be < target) | `540` |

| fade_bps | Output | float | bps, >= 0; retracement from max excursion within spike window | `2.1` |

| fade_pct | Output | float \| null | Percent of max excursion retraced (0-100) | `41.2` |

| us_involved | Output | bool | True if event text indicates US involvement | `true` |



### Perplexity Fade (computed)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| perplexity_fade.success | Output | bool | true/false | `true` |

| perplexity_fade.fade_bps | Output | float | bps or null if unavailable | `-3.5` |

| perplexity_fade.fade_amount | Output | float | raw amount or null | `14` |

| perplexity_fade.fade_unit | Output | str | `bps` \| `ticks` \| `points` \| `unknown` | `"ticks"` |

| perplexity_fade.summary | Output | str | Short summary | `"Rates reversed part of the early rally..."` |

| perplexity_fade.window | Output | str | Window description or null | `"from morning peak to NY close"` |

| perplexity_fade.confidence | Output | float | 0-1 or null | `0.4` |

| perplexity_fade.citations | Output | list[dict] | Source URLs if available | `[{"url":"https://...","title":""}]` |



### Narrative Window Stats (computed)



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| narrative_window | Output | str \| null | Narrative time window hint | `"around 2pm ET"` |

| narrative_stats.spike_start_ts | Output | str | ISO timestamp for estimated window start | `"2024-04-14T13:00:00"` |

| narrative_stats.spike_end_ts | Output | str | ISO timestamp for estimated window end | `"2024-04-14T14:00:00"` |

| narrative_stats.spike_return_bps | Output | float | bps | `+1.8` |

| narrative_stats.spike_volatility_bps | Output | float | bps | `3.4` |

| narrative_stats.fade_bps | Output | float | bps | `0.7` |

| narrative_stats.fade_pct | Output | float \| null | Percent of max excursion retraced | `38.9` |



### Distribution Window Options



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| distro.window_mode | Input | str | `spike` \| `event_window` \| `extended` \| `day` | `"extended"` |

| event_window_stats.return_bps | Output | float \| null | Event window return (if time known) | `+1.4` |

| event_window_stats.vol_bps | Output | float \| null | Event window volatility | `3.1` |

| extended_stats.return_bps | Output | float \| null | ±2 day net return | `-2.7` |

| extended_stats.vol_bps | Output | float \| null | ±2 day volatility | `9.8` |



### UI Features



| Name | Kind | Type | Description |

|------|------|------|-------------|

| source_date | Output | str | Original event date from table |

| date | Output | str | Date actually plotted (may differ due to Globex shift) |

| date_shifted | Output | bool | True if plot date differs from source date |

| time_unknown | Output | bool | True if event time not known (no window shading) |

| event_start | Output | str | Event window start in ET (if known) |

| event_end | Output | str | Event window end in ET (if known) |

| macro_events_nearby | Output | list[dict] | US macro events within ±2 days (for context) |



---



## Policy & Macro Events Analysis Dashboard



**Script**: `scripts/policy_events_analysis.py`  

**Output**: `scripts/policy_events_analysis.html`



Interactive HTML dashboard showing TY price paths for policy/macro events across 5 categories: Yen rate check, Iran, Canada retaliatory tariff, Democrats voting for shutdown, Fed. Based on `geo_analysis.py` with category filtering and per-event summaries.



### CLI Arguments



| Argument | Type | Default | Description |

|----------|------|---------|-------------|

| --output | str | `scripts/policy_events_analysis.html` | Output HTML file path |



### POLICY_EVENTS_RAW Fields



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| id | Constant | int | Unique event ID | `1`, `101`, `201`, `301`, `401` |

| name | Constant | str | Event headline | `"DACA Shutdown (Day 1)"` |

| category | Constant | str | Event category | `"Democrats voting for shutdown"`, `"Yen rate check"`, `"Iran"`, `"Canada retaliatory tariff"`, `"Fed"` |

| notes | Constant | str | Short event description | `"Three-day shutdown; markets largely viewed as political theater"` |

| summary | Constant | str | Multi-paragraph narrative for modal display | (full text of shutdown market context) |

| date | Constant | str | YYYY-MM-DD | `"2018-01-20"` |

| event_time_local | Constant | str | HH:MM or empty if unknown | `""` |

| source_tz | Constant | str | IANA timezone | `"America/New_York"`, `"Asia/Tokyo"` |

| day_of_week | Constant | str | Day name | `"Saturday"` |

| multi_day | Constant | list[str] | Optional list of dates if event spans multiple days | `["2018-01-20", "2018-01-21", "2018-01-22"]` |



### Category Colors



| Category | Color |

|----------|-------|

| Democrats voting for shutdown | #ef4444 (red) |

| Yen rate check | #f97316 (orange) |

| Iran | #a855f7 (purple) |

| Canada retaliatory tariff | #3b82f6 (blue) |

| Fed | #22c55e (green) |



### UI Features



| Feature | Description |

|---------|-------------|

| Category filter pills | Click to filter events by category; "All Categories" shows all |

| Summary snippet | Card shows first ~180 chars of summary with ellipsis |

| Full summary in modal | Modal displays complete multi-paragraph summary |

| Same as geo dashboard | Price paths, candlestick modal, distributions |



---



## Live Pre-Event Dashboard (Telegram Command)



**Script**: `scripts/live_preevent_dashboard.py`  

**Telegram Command**: `/live_pre_event`



Starts a LIVE pre-event dashboard with real-time TY price tracking. Supports both **NFP** and **CPI** events. Creates an ngrok tunnel for public access. Anyone with the URL can view the dashboard.



### CLI Usage



```bash

python scripts/live_preevent_dashboard.py --event NFP --fwd_be 6.5

python scripts/live_preevent_dashboard.py --event CPI --fwd_be 8.0 --serve

```



### CLI Arguments



| Argument | Type | Default | Description |

|----------|------|---------|-------------|

| --event | str | NFP | Event type: "NFP" or "CPI" |

| --consensus | float | None | Current consensus value |

| --cons_std | float | None | Current consensus std dev |

| --fwd_be | float | **Required** | Forward breakeven in basis points |

| --serve | flag | False | Start live data server after generating dashboard |



### Data Sources



| Event | Excel Path | Std Col | FWD Col | DB Event Name |

|-------|------------|---------|---------|---------------|

| NFP | Z:/Meini/NFP.xlsx | Column F | Column P | Non Farm Payrolls |

| CPI | Z:/Meini/CPI.xlsx | Column D | Column K | Core Inflation Rate MoM |



### Telegram Usage



```

/live_pre_event <cons_std> <fwd_be>

/live_pre_event 25 5.0

/live_pre_event_stop

```



### Parameters



| Parameter | Type | Description | Example |

|-----------|------|-------------|---------|

| cons_std | float | Consensus standard deviation for bucket assignment | 25 |

| fwd_be | float | Forward breakeven in basis points | 5.0 |



### Output



Returns a public ngrok URL (e.g., `https://abc123.ngrok.io`) that:

- Serves the live dashboard HTML

- Provides `/api/live` endpoint for real-time TY price

- Updates every 10 seconds with current price from snapshot DB

- Shows live B/E multiplier as red dot on boxplot



### Functions



| Function | Description |

|----------|-------------|

| `start_live_server_with_tunnel(html_path, port)` | Start Flask server + ngrok, returns `{success, public_url, local_url}` |

| `stop_live_server_tunnel()` | Disconnect ngrok tunnel and stop tracking |



### Prerequisites



- ngrok authtoken configured: `ngrok authtoken <YOUR_TOKEN>`

- Free token from: https://dashboard.ngrok.com/get-started/your-authtoken

- Snapshot DB at `X:\uikitxv2\data\snapshots\snapshots_YYYY-MM-DD.db`



---



## Bloomberg Excel Watcher



**Script**: `scripts/bbg_excel_watcher.py`



Monitors a running Excel workbook for Bloomberg RTD/BDP data and automatically triggers dashboard generation when actual economic data appears.



### Prerequisites



| Requirement | Description |

|-------------|-------------|

| Excel running | Workbook must be open (RTD only updates when Excel is running) |

| Bloomberg connected | Terminal must be logged in for data flow |

| xlwings installed | `pip install xlwings>=0.30.0` |



### CLI Usage



```bash

python scripts/bbg_excel_watcher.py

python scripts/bbg_excel_watcher.py --workbook "NFP_Data.xlsx" --poll 5

python scripts/bbg_excel_watcher.py --actual-cell B5 --consensus-cell B4 --cons-std-cell B6

python scripts/bbg_excel_watcher.py --telegram-chat "123456789"

python scripts/bbg_excel_watcher.py --reset  # Clear triggered state for today

```



### Configuration File (`config/bbg_cells.json`)



```json

{

  "defaults": {

    "workbook_name": "NFP_Data.xlsx",

    "actual_cell": "B5",

    "consensus_cell": "B4",

    "cons_std_cell": "B6",

    "poll_interval": 5,

    "telegram_chat_id": null

  }

}

```



### Safeguards



| Safeguard | Description |

|-----------|-------------|

| Duplicate prevention | Only triggers once per calendar day (state saved to file) |

| State cleanup | Auto-removes triggered dates older than 30 days |

| Value validation | Ignores N/A, #VALUE!, #REF!, blank cells |

| Connection resilience | Retries if Excel disconnects temporarily |

| Error isolation | Dashboard/Telegram errors don't crash watcher |



### State File



**Path**: `workspace/.bbg_watcher_state.json`



Persists triggered dates to prevent duplicate triggers on script restart.



### Telegram Integration



When `telegram_chat_id` is configured, the watcher will:

1. Send a notification message with NFP actual/consensus/surprise

2. Send the generated dashboard HTML file



---



## NFP Live Trigger



**Script**: `scripts/nfp_live_trigger.py`



Automated NFP post-event analysis with Telegram notifications. Monitors Excel for actual data, fetches live prices from snapshot DB, and sends alerts to authorized Telegram groups.



Supports two modes:

- **Standard mode**: Watches `NFP.xlsx` which contains actual, consensus, and std dev

- **NFP_A mode**: Watches `NFP_A.xlsx` (actual only) with user-provided consensus/std dev



### CLI Commands



```bash

# Standard mode - monitoring NFP.xlsx

python scripts/nfp_live_trigger.py



# Test with mock data

python scripts/nfp_live_trigger.py --test



# Custom poll interval

python scripts/nfp_live_trigger.py --poll 10



# Reset triggered state

python scripts/nfp_live_trigger.py --reset



# NFP_A mode - monitoring NFP_A.xlsx with user-provided consensus

python scripts/nfp_live_trigger.py --watch-nfp-a --consensus 165 --std-dev 25



# NFP_A mode with custom FWD override

python scripts/nfp_live_trigger.py --watch-nfp-a -c 165 -s 25 --fwd-bps 15.5



# NFP_A mode with custom poll interval

python scripts/nfp_live_trigger.py --watch-nfp-a -c 165 -s 25 --poll 10

```



### CLI Arguments



| Argument | Type | Default | Description |

|----------|------|---------|-------------|

| --test, -t | flag | - | Run with mock NFP data (256K actual, 165K consensus) |

| --poll, -p | int | 5 | Excel polling interval in seconds |

| --reset | flag | - | Clear triggered state (allow re-trigger today) |

| --watch-nfp-a | flag | - | Watch NFP_A.xlsx instead of NFP.xlsx (requires --consensus and --std-dev) |

| --consensus, -c | float | None | User-provided consensus value in thousands (e.g., 165 for 165K). Required with --watch-nfp-a |

| --std-dev, -s | float | None | User-provided consensus standard deviation in thousands (e.g., 25 for 25K). Required with --watch-nfp-a |

| --fwd-bps | float | None | Optional FWD breakeven override in bps (default: auto-detect from snapshot DB or use 15.0) |



### Data Sources



| Source | Path/Pattern | Description |

|--------|--------------|-------------|

| NFP Excel | `Z:/Meini/NFP.xlsx` | Bloomberg-linked Excel with actual/consensus/std (standard mode) |

| NFP_A Excel | `Z:/Meini/NFP_A.xlsx` | Bloomberg-linked Excel with actual only (NFP_A mode) |

| Snapshot DB | `snapshots_YYYY-MM-DD.db` | Live positions with TY prices and options IV |

| Historical | `get_nfp_data_combined()` | NFP events from economic_events.db + Excel std dev |



### NFP_A.xlsx Structure



Minimal Bloomberg real-time feed file:



| Column | Name | Description |

|--------|------|-------------|

| 0 | Date | NFP release date |

| 1 | Actual | NFP actual value in thousands |



### Snapshot DB Schema



| Table | Column | Type | Description |

|-------|--------|------|-------------|

| positions_snapshots | timestamp_unix | int | Unix timestamp |

| positions_snapshots | timestamp_iso | str | ISO 8601 timestamp |

| positions_snapshots | snapshot_json | str | JSON with rows[], prev_prices |

| snapshot_metadata | key, value | str | Metadata k/v store |



### Snapshot JSON Structure (rows)



| Field | Type | Description |

|-------|------|-------------|

| symbol | str | Bloomberg symbol (e.g., "TYH6 Comdty") |

| instrument_type | str | "FUTURE" or "OPTION" |

| live_px | float | Current price (decimal) |

| implied_vol | float | Options IV (annualized %) |

| vega | float | Options vega (for weighting) |



### FWD Breakeven Calculation



Options-implied FWD breakeven from vega-weighted average IV:



```

daily_vol_pct = avg_iv * sqrt(1/252)  # ~0.063

expected_move_points = ty_price * (daily_vol_pct / 100)

fwd_bps = expected_move_points * 16 * 1.5  # 16 bps/point, NFP vol factor

```



### Telegram Message Structure



```

🚨 NFP RELEASED! 🚨



📊 Data Summary

• Actual: 256K

• Consensus: 165K

• Surprise: +91K (+3.27σ)

• Cons Std Dev: 27.8K



📈 Surprise Percentile: 82.9%

📍 Current TY: 112-18 (112.562500)

💰 FWD Breakeven: 9.8 bps (options-implied)



📉 BUCKET STATISTICS (80-90%)

Based on 7 historical events



• Mean B/E: 1.04x → 10.2 bps DOWN

• Median B/E: 0.82x → 8.1 bps

• 95th B/E: 1.68x → 16.4 bps

• 99th B/E: 1.68x → 16.4 bps



📉 PROJECTED PRICE PATHS

(Mean B/E move from 112-18)

• 09:00: 112-13 to 112-22 (95th: 112-09 - 112-26)

...

• 15:00: 112-12 to 112-23 (95th: 112-09 - 112-26)

```



### State File



**Path**: `workspace/.nfp_live_trigger_state.json`



Persists triggered dates to prevent duplicate triggers.



---



## NFP Manual Trigger



**Script**: `scripts/nfp_manual_trigger.py`



One-shot NFP post-event analysis with manual input. No file watching - takes values as CLI arguments and immediately runs analysis and sends Telegram notification.



### CLI Commands



```bash

# Basic usage

python scripts/nfp_manual_trigger.py --actual 180 --consensus 165 --std-dev 25



# Short form

python scripts/nfp_manual_trigger.py -a 256 -c 165 -s 27.8



# With FWD override

python scripts/nfp_manual_trigger.py -a 180 -c 165 -s 25 --fwd-bps 15



# Preview only (no Telegram)

python scripts/nfp_manual_trigger.py -a 180 -c 165 -s 25 --no-telegram



# Test mode (uses mock live price if needed)

python scripts/nfp_manual_trigger.py -a 180 -c 165 -s 25 --test-mode

```



### CLI Arguments



| Argument | Type | Required | Description |

|----------|------|----------|-------------|

| --actual, -a | float | Yes | NFP actual value in thousands (e.g., 180 for 180K) |

| --consensus, -c | float | Yes | NFP consensus value in thousands (e.g., 165 for 165K) |

| --std-dev, -s | float | Yes | Consensus std dev in thousands (e.g., 25 for 25K) |

| --fwd-bps | float | No | Override FWD breakeven in bps (default: auto-detect) |

| --no-telegram | flag | No | Skip Telegram notifications (preview only) |

| --test-mode | flag | No | Use mock live price if snapshot DB unavailable |



---



## Roll Calendar (Treasury Futures)



**Module**: `src/servers/marketdata/roll_calendar.py`



Calendar-based front-month contract determination for treasury futures.

Replaces volume-based detection with deterministic roll logic.



### Roll Logic



| Component | Rule |

|-----------|------|

| Quarterly contracts | H (Mar), M (Jun), U (Sep), Z (Dec) |

| Roll month | Expiry month - 1 (Feb, May, Aug, Nov) |

| Roll date | Last Friday of roll month |

| Exception | If < 2 business days between last Friday and month end, roll shifts 7 days earlier |



### Symbol Mapping



| Parquet/CME | Bloomberg | Description |

|-------------|-----------|-------------|

| ZN | TY | 10-Year Treasury Note |

| ZB | US | 30-Year Treasury Bond |

| ZT | TU | 2-Year Treasury Note |

| ZF | FV | 5-Year Treasury Note |



### Functions



| Function | Input | Output | Description |

|----------|-------|--------|-------------|

| `get_front_contract_for_date(prefix, dt, bloomberg_format=True)` | str, datetime, bool | str | Returns contract symbol (e.g., "TYZ4 Comdty") |

| `is_treasury_symbol(symbol)` | str | bool | Check if symbol uses quarterly roll logic |

| `assign_contract_symbols(timestamps, prefix, bloomberg_format)` | list[datetime], str, bool | list[str] | Batch assign contracts to timestamps |

| `build_roll_calendar(start_year, end_year)` | int, int | list[dict] | Build roll calendar for date range |



### Configuration (config/settings.py)



| Name | Kind | Type | Description |

|------|------|------|-------------|

| SYMBOL_MAPPING | Constant | dict[str, str] | CME to Bloomberg symbol mapping |

| TREASURY_SYMBOLS | Constant | set[str] | Symbols that use calendar-based roll |

| PARQUET_DATA_PATH | Constant | str | Default path to parquet data files |



---



## Parquet Data Loader



**Script**: `scripts/load_parquet_to_postgres.py`



Loads parquet files into PostgreSQL ohlcv_1m table with automatic contract symbol assignment.



### CLI Commands



```bash

# List available files

python scripts/load_parquet_to_postgres.py --list



# Dry run (preview without loading)

python scripts/load_parquet_to_postgres.py --symbol ZN --dry-run



# Load single symbol

python scripts/load_parquet_to_postgres.py --symbol ZN



# Load all 1m files

python scripts/load_parquet_to_postgres.py --all

```



### CLI Arguments



| Argument | Type | Default | Description |

|----------|------|---------|-------------|

| --list | flag | - | List available parquet files |

| --symbol | str | - | Load specific symbol (e.g., ZN, CL) |

| --all | flag | - | Load all 1m parquet files |

| --timeframe | str | "1m" | Timeframe to load |

| --dry-run | flag | - | Preview without loading |

| --data-path | str | PARQUET_DATA_PATH | Path to parquet files |



### Data Flow



1. Read parquet file (e.g., `Intraday_data_ZN_1m_...parquet`)

2. Extract symbol from filename → "ZN"

3. Map to Bloomberg format → "TY"

4. For each row, use roll calendar to assign contract → "TYZ4 Comdty"

5. DELETE existing rows in date range (replace strategy)

6. INSERT new rows



### Expected Parquet Schema



| Column | Type | Description |

|--------|------|-------------|

| Datetime (index) | datetime64 | Timestamp (resampled to ts_event) |

| Open | float | Open price |

| High | float | High price |

| Low | float | Low price |

| Close | float | Close price |

| Adj Close | float | Adjusted close (optional, uses Close if missing) |

| Volume | int | Volume |



---



## Treasury Futures BPS Conversion Utilities



**Module**: `src/servers/analytics/schema.py`



Centralized utilities for converting between decimal price moves, ticks, and basis points for treasury futures.



### ZN Outright Tick Specification (CME)



| Property | Value | Notes |

|----------|-------|-------|

| Minimum tick | 1/64 of a point | Half of a 32nd |

| Tick value | 0.015625 | = $15.625 per contract |

| Ticks per point | 64 | |

| BPS per point | 16 | Duration-based approximation |

| Ticks per BPS | 4 | 1 bps = 4 ticks |

| BPS per tick | 0.25 | 1 tick = 0.25 bps |



### Constants



| Name | Kind | Type | Value | Description |

|------|------|------|-------|-------------|

| TICKS_PER_POINT | Constant | int | 64 | Ticks per point (ZN outright: 1/64 min tick) |

| TICKS_PER_BPS | Constant | int | 4 | Ticks per basis point (4 ticks = 1 bps) |

| DEFAULT_BPS_FACTOR | Constant | int | 16 | BPS per point (1 point = 16 bps) |

| DISPLAY_32NDS_PER_POINT | Constant | int | 32 | For price display (32nds with + suffix) |

| BPS_FACTORS | Constant | dict | {"ZN": 16, "TY": 16, ...} | BPS factors by symbol |



### BPS Factors by Instrument



| Symbol | BPS Factor | Description |

|--------|------------|-------------|

| ZN/TY | 16 | 10-Year Treasury Note |

| ZB | 16 | 30-Year Treasury Bond |

| ZT | 16 | 2-Year Treasury Note |

| ZF | 16 | 5-Year Treasury Note |

| UB/TN | 16 | Ultra bond/Ultra 10-year |

| FGBL | 100 | German Bund |

| ES/NQ | 1 | S&P 500/Nasdaq futures |



### Conversion Functions



**Core Tick Conversions (CME Spec)**:

| Function | Input | Output | Description |

|----------|-------|--------|-------------|

| `decimal_to_ticks(price_move)` | float | float | Decimal → Ticks (×64) |

| `ticks_to_decimal(ticks)` | float | float | Ticks → Decimal (÷64) |



**Tick ↔ BPS Conversions (4 ticks = 1 bps)**:

| Function | Input | Output | Description |

|----------|-------|--------|-------------|

| `ticks_to_bps(ticks)` | float | float | Ticks → BPS (÷4) |

| `bps_to_ticks(bps_move)` | float | float | BPS → Ticks (×4) |



**Decimal ↔ BPS (via ticks for clarity)**:

| Function | Input | Output | Description |

|----------|-------|--------|-------------|

| `decimal_to_bps(price_move)` | float | float | Decimal → Ticks → BPS |

| `bps_to_decimal(bps_move)` | float | float | BPS → Ticks → Decimal |



**Utility**:

| Function | Input | Output | Description |

|----------|-------|--------|-------------|

| `get_bps_factor(symbol)` | str | int | Get BPS factor for symbol (prefix match supported) |



### Conversion Examples (ZN 10-Year Outright)



| Price Move | Ticks | BPS | Dollar Value |

|------------|-------|-----|--------------|

| 1.0 point | 64 ticks | 16 bps | $1,000 |

| 0.0625 (1/16) | 4 ticks | 1 bps | $62.50 |

| 0.015625 (1/64) | 1 tick | 0.25 bps | $15.625 |



### Usage Examples



```python

from src.servers.analytics.schema import (

    decimal_to_ticks, ticks_to_decimal,  # Core

    ticks_to_bps, bps_to_ticks,          # Tick <-> BPS

    decimal_to_bps, bps_to_decimal,      # Convenience (via ticks)

    TICKS_PER_POINT, TICKS_PER_BPS

)



# Core: decimal <-> ticks (CME spec: 1 tick = 1/64 point)

decimal_to_ticks(1.0)       # → 64.0 ticks (1 point)

decimal_to_ticks(0.015625)  # → 1.0 tick

ticks_to_decimal(64)        # → 1.0 (1 point)

ticks_to_decimal(1)         # → 0.015625



# Tick <-> BPS (4 ticks = 1 bps)

ticks_to_bps(4)    # → 1.0 bps

ticks_to_bps(1)    # → 0.25 bps

bps_to_ticks(1.0)  # → 4.0 ticks

bps_to_ticks(16.0) # → 64.0 ticks (1 point)



# Decimal <-> BPS (via ticks)

decimal_to_bps(1.0)       # 1 point → 64 ticks → 16 bps

decimal_to_bps(0.015625)  # 1 tick → 0.25 bps

bps_to_decimal(16.0)      # 16 bps → 64 ticks → 1 point

bps_to_decimal(1.0)       # 1 bps → 4 ticks → 0.0625

```



### JavaScript Equivalent (Dashboard Templates)



```javascript

// ZN Outright Tick Specification (CME)

// 1 tick = 1/64 point = 0.015625 = $15.625

// 4 ticks = 1 bps, 1 tick = 0.25 bps

const TICKS_PER_POINT = 64;  // 1 point = 64 ticks

const TICKS_PER_BPS = 4;     // 4 ticks = 1 bps



// Core tick conversions (CME spec)

function decimalToTicks(priceMove) {

    return priceMove * TICKS_PER_POINT;

}



function ticksToDecimal(ticks) {

    return ticks / TICKS_PER_POINT;

}



// Tick <-> BPS (4 ticks = 1 bps)

function ticksToBps(ticks) {

    return ticks / TICKS_PER_BPS;

}



function bpsToTicks(bps) {

    return bps * TICKS_PER_BPS;

}



// Decimal <-> BPS via ticks (explicit path)

function decimalToBps(priceMove) {

    return ticksToBps(decimalToTicks(priceMove));

}



function bpsToDecimal(bpsMove) {

    return ticksToDecimal(bpsToTicks(bpsMove));

}

```



---



## 20Y Auction Supply Return Analysis



**Script**: `scripts/auction_supply_return_analysis.py`



### CLI Arguments



| Argument | Type | Default | Description |

|----------|------|---------|-------------|

| --input | str | `workspace/results/Securities_2_nomura_only.csv` | Path to auction CSV file |

| --output-dir | str | `workspace/results/auction_supply_return_analysis` | Output directory |

| --buckets | int | 4 | Number of quantile buckets for bucketed analysis |

| --start-year | int | 2020 | Only include auctions from this year onwards |

| --no-plots | flag | false | Skip generating plots |



### Input CSV Columns (Nomura-style)



| Column | Kind | Type | Description |

|--------|------|------|-------------|

| Auction | Input | str | Auction date (MM/DD/YYYY) |

| Size | Input | str | Offering amount (e.g., "$16,000,000,000") |

| Coupon | Input | str | Interest rate (e.g., "4.625%") |

| Stop | Input | str | High yield (e.g., "4.706%") |

| Bid/Cover | Input | float | Bid-to-cover ratio |

| Tail | Input | str | Tail (usually blank) |

| Yld Gap | Input | float | High Yield − Average Yield in bp |

| Dealer% | Input | str | Primary dealer % of competitive accepted |

| Direct% | Input | str | Direct bidder % of competitive accepted |

| Indirect% | Input | str | Indirect bidder % of competitive accepted |

| Dlr Hit | Input | str | Dealer accepted / tendered ratio |

| Dir Hit | Input | str | Direct accepted / tendered ratio |

| Indir Hit | Input | str | Indirect accepted / tendered ratio |



### Outputs



| File | Kind | Type | Description |

|------|------|------|-------------|

| auction_returns_joined.csv | Output | CSV | Auction fields + TY returns at 2h/4h/6h/8h/12h/24h in bps |

| regression_results.csv | Output | CSV | OLS coefficients, t-stats, R² for each horizon |

| bucket_analysis.csv | Output | CSV | Quantile bucket statistics per predictor per horizon |

| summary_report.json | Output | JSON | Summary with significant regressors and monotonic relationships |

| plots/*.png | Output | PNG | Return distribution histograms and rally/selloff splits |



### Return Horizons



| Label | Minutes | Description |

|-------|---------|-------------|

| ret_2h | 120 | Return 2 hours after 1pm ET auction |

| ret_4h | 240 | Return 4 hours after auction |

| ret_6h | 360 | Return 6 hours after auction |

| ret_8h | 480 | Return 8 hours after auction |

| ret_12h | 720 | Return 12 hours after auction |

| ret_24h | 1440 | Return 24 hours after auction |



---



## TY Price Validity Constants (Data Quality Filter)



Location: `scripts/live_unified_dashboard.py`



| Name | Type | Value | Description |

|------|------|-------|-------------|

| TY_PRICE_MIN | float | 50.0 | Minimum valid TY futures price |

| TY_PRICE_MAX | float | 150.0 | Maximum valid TY futures price |



### Background



The OHLCV database contains ~110,000 erroneous data points with near-zero prices (0.05-0.06) 

that are NOT valid TY futures prices. These bad ticks cause extreme bps spikes when included 

in price path calculations.



The `is_valid_ty_price(price)` function filters these out, and all data fetching functions

now include SQL-level filtering: `close >= TY_PRICE_MIN AND close <= TY_PRICE_MAX`.



### Historical TY Price Range

- 2010-2026: TY has traded between approximately 95-140

- The 80-150 range provides a safety margin for edge cases



---



## Fed Press Conference Transcripts



### Environment Variable



| Name | Kind | Type | Description |

|------|------|------|-------------|

| YOUTUBE_API_KEY | EnvVar | str | YouTube Data API v3 key for video search |



**Location**: `config/keys.env`



### CSV Mapping File



**Path**: `workspace/fed/fomc conference/fed_press_conference_videos.csv`



| Column | Kind | Type | Description | Example |

|--------|------|------|-------------|---------|

| date | Input | str | Event date (YYYY-MM-DD) from economic_events.db | `2024-01-31` |

| youtube_url | Input | str | YouTube video URL (auto-filled or user-provided) | `https://www.youtube.com/watch?v=xyz123` |



**Generated by**: `scripts/export_fed_press_conference_dates.py`

**Auto-populated by**: `scripts/fetch_fomc_video_urls.py` (searches Fed YouTube channel)



### Transcript JSON Output



**Path**: `workspace/fed/fomc conference/transcripts/{date}_{video_id}.json`



```json

{

  "date": "2024-01-31",

  "video_id": "xyz123",

  "youtube_url": "https://www.youtube.com/watch?v=xyz123",

  "fetched_at": "2026-01-27T14:30:00-05:00",

  "segment_count": 450,

  "segments": [

    {"start": 0.0, "duration": 5.2, "text": "Good afternoon everyone..."},

    {"start": 5.2, "duration": 3.8, "text": "Today we announced..."}

  ]

}

```



| Field | Kind | Type | Description |

|-------|------|------|-------------|

| date | Output | str | Event date (YYYY-MM-DD) |

| video_id | Output | str | YouTube video ID (11 chars) |

| youtube_url | Output | str | Full YouTube URL |

| fetched_at | Output | str | ISO timestamp of transcript fetch (US/Eastern) |

| segment_count | Output | int | Number of transcript segments |

| segments | Output | list | Array of `{start, duration, text}` objects |

| segments[].start | Output | float | Segment start time in seconds |

| segments[].duration | Output | float | Segment duration in seconds |

| segments[].text | Output | str | Transcript text for segment |



**Generated by**: `scripts/fetch_fed_press_conference_transcripts.py`



### Run Summary JSON



**Path**: `workspace/fed/fomc conference/transcript_run_summary.json`



| Field | Kind | Type | Description |

|-------|------|------|-------------|

| run_at | Output | str | ISO timestamp of run |

| input_csv | Output | str | Path to input CSV |

| transcripts_dir | Output | str | Path to transcripts output folder |

| total_rows | Output | int | Number of rows in CSV |

| successes | Output | int | Number of successfully downloaded transcripts |

| failures | Output | int | Number of failed downloads |

| skipped | Output | int | Number of skipped rows (no URL, already exists) |

| success_details | Output | list | Details of each successful download |

| failure_details | Output | list | Details of each failed download with error info |

| skipped_details | Output | list | Details of skipped rows with reason |



### Sentiment Analysis Output (Step 1)



**Per-Transcript JSON Path**: `workspace/fed/fomc conference/sentiment/{date}_{video_id}.json`



```json

{

  "date": "2024-01-31",

  "video_id": "xyz123",

  "anchor_utc": "2024-01-31T19:30:00+00:00",

  "duration_seconds": 2769.0,

  "chunk_count": 46,

  "final_score": 2.3,

  "match_rates": {

    "chunk_score_same": 0.62,

    "chunk_score_lag1m": 0.58,

    "running_score_same": 0.65,

    "running_score_lag1m": 0.60

  },

  "chunks": [

    {

      "idx": 0,

      "start_s": 12.0,

      "end_s": 72.5,

      "text": "Good afternoon. My colleagues and I...",

      "chunk_score": 1.5,

      "running_score": 1.5,

      "topic": "Rate Policy",

      "key_phrase": "inflation is still too high",

      "reasoning": "Signals caution on cuts",

      "price_windows": {

        "same": {"delta_ticks": -2.0, "delta_bps": -0.5, "direction": "down"},

        "lag1m": {"delta_ticks": -1.0, "delta_bps": -0.25, "direction": "down"}

      },

      "match_chunk_score": {"same": true, "lag1m": true},

      "match_running_score": {"same": true, "lag1m": true}

    }

  ]

}

```



| Field | Kind | Type | Description |

|-------|------|------|-------------|

| date | Output | str | Transcript date (YYYY-MM-DD) |

| video_id | Output | str | YouTube video ID |

| anchor_utc | Output | str | UTC timestamp of press conference start |

| duration_seconds | Output | float | Total transcript duration |

| chunk_count | Output | int | Number of analyzed chunks |

| final_score | Output | float | Final running score after all chunks |

| match_rates | Output | dict | Match rates by score type and window |

| chunks | Output | list | Array of chunk results |

| chunks[].chunk_score | Output | float | Hawkish (+) to dovish (-) score, range [-10, +10] |

| chunks[].running_score | Output | float | Running score at this chunk (15-min lookback) |

| chunks[].price_windows | Output | dict | Price deltas for same/lag1m windows |

| chunks[].match_chunk_score | Output | dict | Match flags (bool or null) for chunk_score vs observed |

| chunks[].match_running_score | Output | dict | Match flags for running_score vs observed |



**Aggregate CSV Path**: `workspace/fed/fomc conference/sentiment/summary.csv`



| Column | Kind | Type | Description |

|--------|------|------|-------------|

| date | Output | str | Transcript date |

| video_id | Output | str | YouTube video ID |

| chunk_idx | Output | int | Chunk index |

| window | Output | str | Window type: same, lag1m |

| chunk_score | Output | float | Chunk hawkish/dovish score |

| running_score | Output | float | EMA running score |

| topic | Output | str | Chunk topic category |

| delta_ticks | Output | float | TY price move in ticks |

| delta_bps | Output | float | TY price move in bps |

| observed_direction | Output | str | Observed TY direction (up/down/neutral) |

| match_chunk_score | Output | bool or null | Whether chunk_score direction matched |

| match_running_score | Output | bool or null | Whether running_score direction matched |



**Generated by**: `scripts/fomc_press_conference_sentiment_step1.py`



### Sentiment Analysis Output (Step 2)



**Per-Transcript JSON Path**: `workspace/fed/fomc conference/sentiment_step2/{date}_{video_id}.json`



```json

{

  "date": "2024-01-31",

  "video_id": "xyz123",

  "price_window": "same",

  "calibration": {

    "weight_chunk": 0.5,

    "weight_running": 0.5,

    "score_neutral": 2.0,

    "score_very": 6.0,

    "accuracy": 0.41,

    "sample_count": 420

  },

  "final_combined_score": 1.3,

  "final_calibrated_label": "hawkish",

  "calibrated_match_rate": 0.43,

  "chunks": [

    {

      "idx": 0,

      "start_s": 12.0,

      "end_s": 72.5,

      "chunk_score": 1.5,

      "running_score": 1.3,

      "combined_score": 1.4,

      "price_delta_ticks": -2.0,

      "price_label": "very hawkish",

      "calibrated_label": "hawkish",

      "alert": true,

      "alert_reason": "change"

    }

  ]

}

```



| Field | Kind | Type | Description |

|-------|------|------|-------------|

| price_window | Output | str | Calibration window: same or lag1m |

| calibration | Output | dict | Best-fit mapping parameters and accuracy |

| calibration.weight_chunk | Output | float | Weight on chunk_score |

| calibration.weight_running | Output | float | Weight on running_score |

| calibration.score_neutral | Output | float | Neutral threshold for 5-class label |

| calibration.score_very | Output | float | Extreme threshold for 5-class label |

| calibration.accuracy | Output | float | Match rate vs price-derived label |

| calibration.sample_count | Output | int | Training samples used |

| final_combined_score | Output | float | Average combined score across chunks |

| final_calibrated_label | Output | str | Final 5-class label |

| calibrated_match_rate | Output | float | Overall match rate vs price-derived labels |

| chunks[].combined_score | Output | float | Weighted score (chunk + running) |

| chunks[].price_label | Output | str | Price-derived 5-class label |

| chunks[].calibrated_label | Output | str | Mapping-calibrated 5-class label |

| chunks[].alert | Output | bool | Whether alert should fire for this chunk |

| chunks[].alert_reason | Output | str | Alert trigger reason (extreme/change/strengthening) |



**Aggregate CSV Path**: `workspace/fed/fomc conference/sentiment_step2/summary.csv`



| Column | Kind | Type | Description |

|--------|------|------|-------------|

| date | Output | str | Transcript date |

| video_id | Output | str | YouTube video ID |

| chunk_idx | Output | int | Chunk index |

| start_s | Output | float | Chunk start (seconds) |

| end_s | Output | float | Chunk end (seconds) |

| price_window | Output | str | Calibration window (same/lag1m) |

| combined_score | Output | float | Weighted score |

| price_delta_ticks | Output | float | TY price move (ticks) |

| price_label | Output | str | Price-derived 5-class label |

| calibrated_label | Output | str | Mapping-calibrated label |

| alert | Output | bool | Alert flag |

| alert_reason | Output | str | Alert reason |



**Generated by**: `scripts/fomc_press_conference_sentiment_step2.py`



### Live Sentiment Alerts



**Script**: `scripts/fomc_live_sentiment_alerts.py`



| Mode | Description |

|------|-------------|

| `--replay <date>` | Replay historical transcript with simulated timing |

| `--stdin` | Accept pasted transcript text from terminal |

| `--scrape` | Poll StreamText live captioning service |



| Constant | Type | Value | Description |

|----------|------|-------|-------------|

| CHUNK_SECONDS | int | 60 | 1-minute chunks for real-time scoring |

| LOOKBACK_CHUNKS | int | 15 | 15-minute running score window |

| EXTREME_THRESHOLD | float | 6.0 | Trigger alert on very hawkish/dovish |

| CHANGE_THRESHOLD | float | 3.0 | Trigger alert on running score shift |

| STRENGTHENING_CONSECUTIVE | int | 3 | Consecutive same-direction chunks for alert |

| STREAMTEXT_URL | str | `https://www.streamtext.net/player?event=CFI-FRB` | Fed's live captioning source |



**Alert Triggers**:

1. Extreme score: `abs(chunk_score) >= 6.0`

2. Running score shift: `abs(running_score_delta) >= 3.0`

3. Strengthening pattern: 3+ consecutive hawkish or dovish chunks



### Q&A Microstructure Alignment



**Script**: `scripts/fomc_qa_microstructure_alignment.py`



Aligns each Powell answer to market microstructure windows and writes answer-level metrics.



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|---------------|

| `fomc_qa.anchor_fallback_time_et` | Input | str | `HH:MM:SS` ET | `--anchor-fallback-time-et 14:30:00` |

| `fomc_qa.answer_idx` | Output | int | `>= 1` within transcript | `1` |

| `fomc_qa.answer_start_s` | Output | float | `>= 0` seconds from transcript start | `470.826` |

| `fomc_qa.delta_bps_0_30` | Output | float \| null | Signed bps move from `t0 -> t0+30s` | `0.25` |

| `fomc_qa.delta_bps_30_120` | Output | float \| null | Signed bps move from `t0+30s -> t0+120s` | `-0.125` |

| `fomc_qa.delta_bps_0_600` | Output | float \| null | Signed bps move from `t0 -> t0+10m` | `0.625` |

| `fomc_qa.direction_label_0_30` | Output | str | `trend`, `mean_revert`, `mixed` | `mixed` |

| `fomc_qa.direction_label_30_120` | Output | str | `trend`, `mean_revert`, `mixed` | `trend` |

| `fomc_qa.direction_label_0_600` | Output | str | `trend`, `mean_revert`, `mixed` | `mixed` |

| `fomc_qa.direction_sign_*` | Output | str | `up`, `down`, `flat` | `up` |

| `fomc_qa.efficiency_ratio_*` | Output | float \| null | `0..1` when path exists | `0.56` |

| `fomc_qa.missing_price_*` | Output | bool | `true/false` | `false` |

| `fomc_qa.parse_confidence` | Output | float | `0..1` | `1.0` |

| `fomc_qa.primary_symbol` | Input | str | Root symbol prefix | `--primary-symbol ZN` |

| `fomc_qa.qa_count` | Output | int | `>= 0` | `25` |

| `fomc_qa.question_speaker` | Output | str | Speaker tag parsed from transcript | `CHRISTOPHER CONDON` |

| `fomc_qa.resolution` | Input | str | `auto`, `1s`, `1m` | `--resolution auto` |

| `fomc_qa.resolution_used` | Output | str | `1s`, `1m` | `1m` |

| `fomc_qa.rv_*` | Output | float \| null | `>= 0` realized-vol per window | `0.00046` |

| `fomc_qa.rv_baseline_*` | Output | float \| null | Median rolling baseline RV | `0.00036` |

| `fomc_qa.symbol_used` | Output | str \| null | Symbol root actually used | `TY` |

| `fomc_qa.vol_spike_ratio_*` | Output | float \| null | `RV_window / RV_baseline` | `1.29` |

| `fomc_qa.vol_spike_z_*` | Output | float \| null | Standardized RV spike | `2.21` |



**Output paths**:

- Per-transcript JSON: `workspace/fed/fomc conference/qa_microstructure/{date}_{video_id}.json`

- Aggregate CSV: `workspace/fed/fomc conference/qa_microstructure/answer_level_summary.csv`

- Run summary JSON: `workspace/fed/fomc conference/qa_microstructure/run_summary.json`



---



## FOMC Meetings Dashboard



**Script**: `scripts/fomc_meetings_dashboard.py`



Interactive HTML dashboard for analyzing FOMC press conferences with sentiment filtering, normalized price paths, and return distributions.



### CLI Arguments



| Argument | Type | Default | Description |

|----------|------|---------|-------------|

| --output | str | `workspace/scenarios/fomc_meetings_dashboard_{timestamp}.html` | Output HTML file path |

| --force-refresh | flag | False | Force re-fetch web sentiments from Perplexity (bypass cache) |



### Data Sources



| Source | Type | Description |

|--------|------|-------------|

| `workspace/economic_events.db` | SQLite | Fed Press Conference events (US) |

| PostgreSQL `ohlcv_1m` | PostgreSQL | TY 1-minute OHLCV data |

| `workspace/fed/fomc conference/fomc_web_labels.json` | JSON | Cached sentiment labels from Perplexity |



### Output HTML Structure



The dashboard embeds all data inline (no server required):



| Data Blob | Content |

|-----------|---------|

| eventsData | Array of meeting objects with paths, returns, candlesticks, sentiment |

| returnDistros | Dict mapping window name to list of bps returns |

| returnStats | Dict mapping window name to stats (mean, std, percentiles) |



### Event Object Schema



| Field | Type | Description |

|-------|------|-------------|

| date | str | Meeting date (YYYY-MM-DD) |

| anchor_time | str | ISO timestamp of press conference start (ET) |

| is_month_end | bool | True if date is in last 7 calendar days of month |

| sentiment_label | str | "hawkish" / "dovish" / "neutral" / "unknown" |

| sentiment_summary | str | Brief web-derived summary of what happened |

| sentiment_confidence | float | Confidence score 0-1 |

| citations | list | List of `{url, title}` citation dicts |

| has_data | bool | True if price data available |

| normalized_path | dict | `{minutes_from_anchor, bps_changes, ref_price}` |

| return_windows | dict | `{pre_60m, during_60m, during_120m, during_180m}` in bps |

| candlestick_data | dict | `{timestamps, opens, highs, lows, closes, volumes}` for ±2 days |

| economic_events | list | Other events on this date from economic_events.db |



### Return Windows



| Window | Range | Description |

|--------|-------|-------------|

| pre_60m | T-60m → T | Pre-conference return |

| during_60m | T → T+60m | First hour of conference |

| during_120m | T → T+120m | First two hours |

| during_180m | T → T+180m | Full conference window |



### Path Normalization



- Reference price: Close at T-60m

- BPS conversion: Uses `rules/treasury_price_conversion.py` (1 point = 8 bps)

- Path range: T-60m to T+180m



---



## FOMC Sentiment Cache



**Module**: `src/core/fomc_sentiment_cache.py`



Persistent JSON cache for web-derived FOMC sentiment labels.



### Cache File



**Path**: `workspace/fed/fomc conference/fomc_web_labels.json`



### Cache Entry Schema



| Field | Type | Description |

|-------|------|-------------|

| label | str | "hawkish" / "dovish" / "neutral" / "unknown" |

| confidence | float | Confidence score 0-1 |

| summary | str | Brief summary of what happened |

| citations | list | List of `{url, title}` dicts |

| prompt_version | str | Version of prompt used (e.g., "v1") |

| source | str | Data source (typically "perplexity") |

| cached_at | str | ISO timestamp of cache write (UTC) |



### Key Functions



| Function | Description |

|----------|-------------|

| `get_cached_sentiment(date, prompt_version)` | Get cached entry if version matches |

| `set_cached_sentiment(date, label, confidence, summary, citations, prompt_version)` | Store entry |

| `get_or_fetch_sentiment(date, force_refresh)` | Get from cache or fetch from Perplexity |

| `batch_get_or_fetch_sentiments(dates, force_refresh, progress_callback)` | Process multiple dates |

| `get_all_cached_sentiments()` | Return full cache dict |

| `clear_cache()` | Delete cache file |



---



## Perplexity API (FOMC Sentiment)



**Module**: `src/servers/anomaly_detection/perplexity_search.py`



### Environment Variable



| Name | Kind | Type | Description |

|------|------|------|-------------|

| PERPLEXITY_API_KEY | EnvVar | str | Perplexity API key for web search |



**Location**: `config/keys.env`



### search_fomc_sentiment() Return Schema



| Field | Type | Description |

|-------|------|-------------|

| success | bool | Whether API call succeeded |

| label | str | "hawkish" / "dovish" / "neutral" / "unknown" |

| confidence | float | Confidence score 0-1 |

| summary | str | Brief summary of meeting outcome |

| citations | list | List of `{url, title}` citation dicts |

| source | str | "perplexity" |

| date | str | Input date (YYYY-MM-DD) |

| prompt_version | str | Version of prompt used |

| model | str | Perplexity model used (e.g., "sonar") |

| error | str | Error message if success=False |



### Constants



| Constant | Type | Value | Description |

|----------|------|-------|-------------|

| PERPLEXITY_API_URL | str | `https://api.perplexity.ai/chat/completions` | API endpoint |

| DEFAULT_MODEL | str | "sonar" | Perplexity model with web search |

| FOMC_SENTIMENT_PROMPT_VERSION | str | "v1" | Current prompt version for cache invalidation |



---



## Yearly Overlay Timeseries Script



**Script**: `scripts/yearly_overlay_timeseries.py`



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| yearly_overlay_timeseries.symbol_pattern | Input | str | SQL ILIKE pattern for symbol | `"TY%"` |

| yearly_overlay_timeseries.start_year | Input | int | Four-digit year; optional | `2019` |

| yearly_overlay_timeseries.end_year | Input | int | Four-digit year; optional | `2025` |

| yearly_overlay_timeseries.resample | Input | str | Pandas offset alias; `"1min"` for no resample | `"15min"` |

| yearly_overlay_timeseries.normalize | Input | bool | If true, use change from year start (default true) | `true` |

| yearly_overlay_timeseries.units | Input | str | `price`, `ticks`, `bps` (default auto; ticks/bps require normalize) | `"ticks"` |

| yearly_overlay_timeseries.output | Output | Path | HTML file path | `workspace/analysis/yearly_overlay_ty_2019_2025.html` |



---



## Polymarket Telegram Tracking



**Script**: `scripts/polymarket_stream_bot.py`  

**Store**: `src/core/polymarket_stream_store.py`



| Name | Kind | Type | Allowed values / range | Example Usage |

|------|------|------|------------------------|--------------|

| POLYMARKET_CHAT_ID | EnvVar | str | Comma-separated Telegram chat ids; optional override for Polymarket commands/alerts. Defaults to `TELEGRAM_CHAT_ID`. | `"-1001234567890,8133479159"` |

| POLYMARKET_CHAT_IDS | EnvVar | str | Alias of `POLYMARKET_CHAT_ID`. | `"-1001234567890"` |

| POLYMARKET_MARKETS_DB_PATH | EnvVar | str | Optional override for the Polymarket time-series SQLite DB path used by `poll_midpoint.py`. Defaults to local user-profile storage under `Path.home() / ".market_data" / "polymarket_markets.db"`. | `"C:\Users\aasth\.market_data\polymarket_markets.db"` |

| POLYMARKET_MIN_ABS_PP | EnvVar | float | `> 0`; minimum absolute move in percentage points required for `significant_change`. Default `3.0`. | `"3.0"` |

| POLYMARKET_POLL_INTERVAL_S | EnvVar | int | `>= 5`; polling interval in seconds. Default `30`. | `"30"` |

| POLYMARKET_STREAM_DB_PATH | EnvVar | str | Optional override for the Polymarket Telegram-state SQLite DB path. Defaults to local user-profile storage under `Path.home() / ".market_data" / "polymarket_stream.db"`. | `"C:\Users\aasth\.market_data\polymarket_stream.db"` |

| POLYMARKET_PUBLIC_CHAT_ID | EnvVar | int \| None | Target chat id for forwarding approved Polymarket alerts from the inline approve/reject workflow. Optional. | `"-1002222222222"` |

| POLYMARKET_REL_FACTOR | EnvVar | float | `> 0`; relative significance multiplier applied to current midpoint (`max(MIN_ABS_PP, mid * REL_FACTOR * 100)`). Default `0.08`. | `"0.08"` |

| POLYMARKET_TRACK_MAX_MARKETS | EnvVar | int | `1..25`; max markets shown in `/pm_track` selection list. Default `8`. | `"8"` |

| POLYMARKET_TREND_STREAK_POLLS | EnvVar | int | `>= 2`; consecutive same-direction polls needed for trend alert. Default `10`. | `"10"` |

| POLYMARKET_TREND_TOTAL_MOVE_THRESHOLD | EnvVar | float | `0 < x <= 1`; total probability move across trend streak. Default `0.03` (3pp). | `"0.03"` |

| polymarket_stream.alert_reason | Output | str | `significant_change` \| `trend` | `"trend"` |

| polymarket_stream.alert_callback | Input | str | `pm_approve:<alert_id>` \| `pm_reject:<alert_id>` | `"pm_approve:9f0fbc2d2d7a1e0f"` |

| polymarket_stream.alert_caption | Output | str | Compact Telegram caption: header plus top movers with current odds and delta. | `"PM | Fed rate cut | Sig change"` |

| polymarket_stream.forwarded_message_id | Output | int \| None | Telegram public-chat message id for successful approved forwards; `NULL` means the approval has not been forwarded and can be retried | `12345` |

| polymarket_stream.selection_callback | Input | str | `pm_pick:<selection_id>:<idx>` \| `pm_done:<selection_id>` \| `pm_cancel:<selection_id>` | `"pm_pick:7a3f4b12:2"` |

| polymarket_stream.state.last_alert_ts | Internal | str \| None | ISO timestamp of the last alert baseline used to annotate the previous point on chart alerts. | `"2026-04-13T10:30:00+00:00"` |

| polymarket_stream.subscription | Internal | object | `{id, chat_id, condition_id, market_slug, title, yes_token_id, no_token_id, active}` | `{"id": 4, "chat_id": -1001234567890, "condition_id": "0xabc", "yes_token_id": "12345"}` |

| polymarket_stream.timeseries_row.source | Constant | str | Insert source tag in `market_prob_timeseries`. | `"telegram_pm_poll"` |

| pm_list | Input | command | List active tracked markets for current chat. | `"/pm_list"` |

| pm_track | Input | command | Resolve natural-language query into candidate markets and start multi-select confirmation flow. | `"/pm_track us recession 2026"` |

| pm_untrack | Input | command | Disable one tracked market by index from `/pm_list`. | `"/pm_untrack 2"` |


## Move Exhaustion V2 harness (scripts/move_exhaustion_v2/)

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|---------------|
| move_exh_v2.archive.authorized_batch | Input | dict | One shot only: GLBX.MDP3 / ZN.FUT / parent / mbp-1 / 2022-01-01 inclusive→2026-04-02 exclusive / monthly CSV+Zstd / quoted $0 | `config/frozen/data_store.yaml` |
| move_exh_v2.episodes.B_ticks.primary | Constant | int | 8 (grid {4,6,8,12}); frozen 2026-08-11 | `config/frozen/episodes.yaml` |
| move_exh_v2.episodes.x_ticks.primary | Constant | int | 2 (grid {1,2,3,4}) | `config/frozen/episodes.yaml` |
| move_exh_v2.labels.classes | Constant | list[str] | [REVERSAL, CONTINUATION, OSCILLATORY_UNRESOLVED] | `config/frozen/labels.yaml` |
| move_exh_v2.labels.primary | Constant | dict | R=4, C=4 ticks; H=15 min wall | `config/frozen/labels.yaml` |
| move_exh_v2.splits.status | Internal | str | frozen after X0 PASS (2026-08-13) | `split_manifest.json` |
| move_exh_v2.data.x0_run.replay-qc | Output | dict | Rewrites Q1/Q3 on `build_state.json`; parquet bytes unchanged; `holdout_opened=false` | `py -3 scripts/move_exhaustion_v2/data/x0_run.py replay-qc` |
| move_exh_v2.data.x0_run.parity | Output | dict | `parity_report.json`; Q7 corr>0.95 and 0 sign flips; Q8 max abs ≤1 tick on comparable dates | `py -3 scripts/move_exhaustion_v2/data/x0_run.py parity` |
| move_exh_v2.data.x0_run.finalize | Output | dict | `eligible_dates.json`, `split_manifest.json`, `experiments/X0_*/{manifest,metrics,conclusion}.json`; verdict PASS/FAIL | `py -3 scripts/move_exhaustion_v2/data/x0_run.py finalize` |
| move_exh_v2.x1_run | Output | dict | `experiments/X1_<run_id>/{days/B*_x*/{date}.parquet,labeled_*.parquet,metrics.json,conclusion.json,manifest.json}`; discovery+validation only unless `--stop-when-sufficient`; `holdout_opened=false` | `py -3 scripts/move_exhaustion_v2/x1_run.py --primary-only --stop-when-sufficient` |
| move_exh_v2.splits.windows | Constant | dict | Discovery 2022–2024; Validation 2025; locked Holdout 2026-01-01→last full Q3-pass session ≤2026-04-01 | `config/frozen/splits.yaml` |
| move_exh_v2.splits.holdout_opened | Constant | bool | false until gate pass + explicit user approval | every run `manifest.json` |
| move_exh_v2.gates.sample_floors | Constant | dict | n>=50 episodes, >=8 dates per cell | `config/frozen/gates.yaml` |
| move_exh_v2.gates.sesoi | Constant | dict | 8pp lift; 0.005 Brier; 2pp AUC; no cost gates | `config/frozen/gates.yaml` |
| move_exh_v2.store.derived_store | Constant | path | C:\FRDBs\FutMBP\move_exhaustion_v2_store | `config/frozen/data_store.yaml` |
| move_exh_v2.store.source_root | Constant | path | C:\FRDBs\FutMBP\move_exhaustion_v2_source | `config/frozen/data_store.yaml` |
| move_exh_v2.data.x0_run.volume | Output | dict | Per-file volume parquet + merged `volume.parquet` / `day_plan.parquet` / `summary.json`; `holdout_opened=false` | `py -3 scripts/move_exhaustion_v2/data/x0_run.py volume` |
| move_exh_v2.data.x0_run.pilot | Output | dict | Isolated `pilot_YYYYMM/` store + `pilot_YYYYMM_disk.json`; 20% headroom gate; default month 2026-03 | `py -3 scripts/move_exhaustion_v2/data/x0_run.py pilot` |
| move_exh_v2.data.x0_run.store | Output | dict | Full selected-row store at frozen C: `derived_store` + `store_build_summary.json`; checkpoint `build_state.json`; `holdout_opened=false` | `py -3 scripts/move_exhaustion_v2/data/x0_run.py store` |
| move_exh_v2.freeze_manifest | Internal | file | SHA-256 pins; regenerate only via governance addendum | `tools/freeze_manifest.py --write` |

| move_exh_v2.loop.journal_entry | Internal | dict | {seq:int sequential, ts_utc, actor in {advisor,worker,human}, event in {governance,backlog,brief,launch,result,feedback,decision,escalation,human_input,correction,retro,process_change}, cycle: 'C###'|null, summary, refs:list[str]} | `tools/loop_journal.py append` |
| move_exh_v2.loop.meta_json | Internal | dict | {cycle, slug, tier in {EXPLORATORY,CONFIRMATORY}, status, data_window_used, holdout_opened=false, advisor_decision, contract (confirmatory only), worker_model (required once RESULTED; e.g. 'deepseek-v4-flash (codex exec)'), worker_agent_id: str|None, advisor_models: list[str] (required once REVIEWED; <2 entries requires solo_review_reason and forbids PROMOTE_REQUEST), headline_metrics: dict (required once RESULTED), wall_clock_minutes: int|None} | `research/loop/cycles/C###_*/meta.json` |
| move_exh_v2.loop.retro_cadence | Constant | int | 1 retrospective per 5 closed cycles (closed = advisor_decision recorded); enforced by `test_retrospective_cadence` | `research/loop/retros/R###.md` |
| move_exh_v2.loop.tier | Constant | str | EXPLORATORY (discovery dates only, NOT EVIDENCE) or CONFIRMATORY (frozen contract + gates) | `LOOP_PROTOCOL.md` section 2 |
| move_exh_v2.loop.bl3.spec | Constant | path | `research/loop/specs/BL3_impact_shortfall_spec.md` | Drafted 2026-08-13; run only after human approval as C014 |
| move_exh_v2.loop.bl3.N_lookback | Constant | int | 100 store rows, `source_order < t_star` | BL-3 spec §3 |
| move_exh_v2.loop.bl3.model | Constant | str | OLS `Δm_W = α + β AF_W`; expanding WF, first 20 dates train | BL-3 spec §3 |
| move_exh_v2.loop.bl3.IS | Internal | float | `(α̂+β̂ AF_W) − Δm_W`, then / expanding prior-date SD | BL-3 spec §3 |
| move_exh_v2.loop.bl3.e07_corr_flag | Constant | float | Flag if `|corr(IS, Δm_W)| > 0.85` | BL-3 spec §4 |
| move_exh_v2.contract.status | Constant | str | FROZEN (X0,X1) / RETIRED_TO_BACKLOG (X2-X10) / future XC## via promotion | `experiments/X*/CONTRACT.md` |

## Standalone Gamma Tracker V6

| Name | Kind | Type | Allowed values / range | Example Usage |
|------|------|------|------------------------|-------------|
| GT_V6_DB | EnvVar | str | Path to `gamma_tracker_v6.db`; must not be `gamma_tracker_v3.db` | `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v6.db` |
| GT_V6_EVENTS_DB | EnvVar | str | Optional local `economic_events.db` | package-local `economic_events.db` |
| YAMAN_EVENTS_DB_PATH | Constant | str | Live V6 upcoming-events banner, read-only `events` table | `Y:\Yaman\Fundamentals\Economic Events\Data\events.sqlite` |
| GT_V6_API_SERVER | EnvVar | str | AccessAPI `host:port` | `100.79.30.21:8001` |
| GT_V6_PORT | EnvVar | int | Standalone dash port; default 8056 so live 8055 is unused | `8056` |
| EVENT_DAY_PARAM_ORDINAL | Constant | int | Weight row for same-calendar-day expiry | `1` |
| OPTION_EXPIRY_HOUR_CT | Constant | int | Event-ownership cutoff hour in Chicago | `12` |
| STRUCTURAL_EVENT_FLOOR | Constant | float | Unused min pre-event factor; live kernel uses 0 | `0.0` |
