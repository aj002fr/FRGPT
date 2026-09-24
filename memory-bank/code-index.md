# Code Index



## Dashboard operations (`ops/`)

### release0/deploy_release0.ps1

Release 0 deploy tool for a 17:00–18:00 ET window, one item at a time: `v3` (5053), `v1` (5050), `events` (5064 + 5065, plus the `ECON_EVENTS_PG_DSN` line in Y: `config/keys.env` and the 5064 calendar note in Y: `.cursorrules`). `-Action status|deploy|restore|verify`. Deploy refuses unless each Y: file still equals the `yshare-sync` merge base, backs originals up once to `C:\dev\mdp_backups\release0_20260924\<item>\` (SHA-256 manifest), writes the `ops/local-runtime` version with the Y: file's own line endings, restarts the item's apps headless (tasks `MDP R0 v3 5053`, `MDP R0 flow 5064`, `MDP R0 supply 5065`; V1 via its launcher loop), verifies, and adds a dated note to Y: `memory-bank/progress.md` + `activeContext.md`. Restore writes the backed-up bytes back and removes that note. `-SkipRestart` rehearses file handling on a scratch target.

### release0/stream_probe.py

Reads V3's `/api/live/stream` for N seconds and prints event counts, bytes, time to the first picture, and live-delta print lag (receive time minus the print's ET time, so it includes feed delay). Replayed prints (after a subscribe or rebuild; no `strip`) are counted separately.

### supervisor/profiles.json

Process profiles for `scripts/dashboard_runtime/supervisor.py`: `rehearsal` (writer + V3/V2.2/V2.7/flow/supply on 7053/7058/7063/7064/7065, rehearsal DBs under `C:\dev\mdp_rehearsal\data`) and `production` (6053-6065 so the cutover switch is a Tailscale repoint, C: checkout DBs, writer with `--provisional`). Both set `MDP_PG_HOST=127.0.0.1`. `{python}` is replaced by the interpreter passed to the supervisor.

### docs/rehearsal_report_20260924.md

Milestone 7 results so far: supervisor, writer kill/graceful stop, 20-viewer load, share-loss independence, V3 compact payload and stream continuity, state copy (24 MB 1.7 s), flow DB migration on today's 1,077 MB DB (copy 16.7 s, VACUUM INTO 35.1 s, quick_check ok), Tailscale commands verified; open items (US-open p95, shadow gate, Starlette deferred) and the two V3 correctness findings.

### docs/cutover_runbook.md

Milestone 8 procedure: 16:45 go/no-go gates and open decisions (banner alerts, V2 signal log, local 50xx links), pre-window tag/backups/rollback worktree, halt steps with exact commands (disable the V1 watchdog, stop Y: apps, copy-then-`VACUUM INTO` migration, state copies, supervisor task, five `tailscale serve --https=<port>` repoints), reopen checks, and rollbacks A (before 18:00), B (one app, `<app>.disabled`), C (writer, V1 fallback on the local DB).

## Canonical flow store (`scripts/flow_store/`)

### normalize.py

One normalization for AccessAPI live WebSocket TBBO records and `/historical/trades` rows: dashboard symbol from the contract root, exact price in nanos, integer size, B/A/N side, event time in ns and in µs rounded half-even (the historical store's rounding; 126/126 live trades matched). Identity is `(contract, ts_us, price_nanos, size, side)` plus an occurrence number, because identical fills can share a CME packet.

### sessions.py

CME Globex interest-rate sessions labelled by their 17:00 ET close (Sun 18:00-Mon 17:00 = Monday; halt and weekend are outside), exact integer ns bounds across DST, `previous_session`, and `choose_front` (highest prior-session volume, ties alphabetical).

### store.py

`FlowStore`: `flow_tape.sqlite` (trade_tape, provisional_tape, coverage, front_contract + override, writer_lease, flow_revisions, sealed_minutes, meta, schema_version) and the canonical bars in `flow_history.sqlite` (`v1_aggress_flow_1m`, V1 schema and ts keys). Single-writer lease (heartbeat 10 s, stale 60 s, `BEGIN IMMEDIATE`); inserts are `append` (live) or set-based `set` (historical window; only the per-key shortfall is inserted, so replays are no-ops); bars before `canonical_start_us` are never written and every change to an existing bar is logged.

### aggregate.py

Sparse 1-minute bars with V1's DV01 formulas (B +1, A -1, N excluded entirely), bucketed by the µs event time both endpoints share; `trade_price` is the minute's last B/A price.

### backfill.py

Gap-only AccessAPI historical backfill: uncovered intervals clipped to sessions, never the forming minute or the last 2 minutes, whole-second requests filtered locally (AccessAPI returns empty JSON for µs bounds), set-based inserts, re-seal of touched minutes (which replaces provisional bars with a revision). Also `ensure_front`, `seal_minute`, `seal_provisional`.

### writer_main.py

Standalone writer: AccessAPI WebSocket -> bounded queue (overflow epochs close coverage) -> 1 s batches -> seal at minute end + 10 s -> 60 s backfill; lease heartbeat and `status.json` every 10 s; no stream de-duplication (identical same-packet fills are real trades). Coverage follows the one connection (any record within 60 s; start 1 s after the first batch, end 2 s behind the newest event). `--provisional` runs the Databento backup during AccessAPI silence and seals uncovered minutes as `databento_provisional`.

### shadow_compare.py

Milestone 3 check: for an interval, per-key trade counts vs AccessAPI historical, front contracts, sealed bars vs bars rebuilt from historical (counts exact, flows 1e-9 relative), in-session coverage gaps and unsealed minutes; JSON report, exit 0 only when clean.

## Local dashboard runtime (`scripts/dashboard_runtime/`)

### serve.py

`serve` (waitress threads 16 / connection_limit 500 / channel_timeout 120, or werkzeug), `add_server_args` (`--server`, default `MDP_SERVER`), `configure_logging` (rotating app log and slow-request log under `MDP_LOG_DIR`, request logs at WARNING), `install_request_timing`, and `ResponseCache` / `cached_response` (one build per key and data tick, bounded waits, last-good, gzip, ETag/304) used by 5064 `/api/today`, `/api/bootstrap` and 5065 `/api/live`, `/api/bootstrap`.

### supervisor.py

Milestone 6 supervisor: starts the writer and the five apps headless, data-based health every 10 s (writer status/lease/sealed minute/queue; HTTP; newest-bar age for 5064/5065 only while OPEN), restart after three failed checks or on exit with 5-300 s backoff, `alerts.jsonl` + Windows Event Log, `metrics.jsonl` (CPU/RSS/threads/check ms), `supervisor_status.json`. A `<app>.disabled` file in the state dir stops that app and keeps it down.

## Event Parent/Component Mapping (`config/`)

### event_parent_components.json

Canonical parent-event -> component mapping for the US economic-event universe (58 parents, 147 components). Keyed on `event_name`, which matches the `EVENT_NAME` column of `Z:\Aastha\US Eco Events.xlsx` (Sheet1) and is the cross-system join key. A "parent" is a release family (e.g. `NFP` = Employment Situation) and "components" are the line items released together (e.g. Change in Nonfarm Payrolls, Unemployment Rate, Average Hourly Earnings). Each component carries `event_name`, `bbg_ticker`, `scaling_factor`, and an optional `headline` flag; each parent carries `display_name`, `category`, and `headline_event_name`. Generated by `data_setup/build_event_parent_components.py` (do not hand-edit; edit the generator's GROUPS and rerun).

### event_parent_components.py

Lightweight loader (mirrors `unified_dashboard_event_config.py`) for the JSON mapping. Exposes `load_event_parent_components()` (lru-cached), `get_parents()`, `get_components(parent)` (case-insensitive), `get_parent_of(event_name)`, `get_headline_component(parent)`, and `iter_components()` (flattened rows annotated with their `parent`/`category`).

### ../data_setup/build_event_parent_components.py

Generator for `config/event_parent_components.json`. Reads `EVENT_NAME`/ticker/`SCALING_FACTOR` from `Z:\Aastha\US Eco Events.xlsx` (Sheet1), groups every unique EVENT_NAME into a parent via the `GROUPS` table, and asserts 100% coverage (errors on any missing/extra/duplicated event before writing). Rerun after editing `GROUPS` or when the workbook gains new events.

## Real-Time Iran Headlines (`scripts/`)

### scrape_iran_headlines.py

Batch Telegram history scraper for Iran/Israel/Lebanon conflict headlines. It now exposes shared message-processing helpers (`process_telegram_message`, source classification, filtering, DB initialization, and dedup insert) so both batch catch-up and the real-time listener use identical cleaning/filtering/classification semantics. `insert_headline()` returns true only when `INSERT OR IGNORE` actually inserts a new row.

### iran_headlines_stream.py

Persistent Telethon listener for the same distribution chat used by `scrape_iran_headlines.py`. It performs startup catch-up from `MAX(timestamp)-1h`, then listens to `events.NewMessage(chats=CHAT_ID)` and writes matching headlines to `workspace/iran_headlines.db` within seconds. Includes a cross-platform single-instance lock, reconnect supervisor, dual local/shared logging, heartbeat marker, last-message marker, and `--catch-up-only` validation mode.

### run_iran_headlines_stream.bat

Task Scheduler wrapper for `scripts/iran_headlines_stream.py`. Resolves the repo root, points the listener at `workspace\iran_headlines.db`, appends wrapper logs under `%LOCALAPPDATA%\market_data_puller\logs`, and writes wrapper success/error markers under `%LOCALAPPDATA%\market_data_puller\status`.

### install_iran_headlines_task.ps1

Installs the persistent `IranHeadlinesStream` Windows scheduled task at user logon with unlimited execution time and `RestartOnFailure`. It removes/replaces the old 3-hour `IranHeadlinesRefresh` batch task when run, normalizes mapped-drive paths to UNC for Task Scheduler, and supports optional `-TaskPassword` for password-backed task registration.

## Macro Path Navigator (`scripts/macro_path_navigator/`)

### app.py

Live Flask dashboard entrypoint for the Macro Path Navigator on port `5060` by default. Exposes `/`, `/api/cycle`, `/api/runway`, `/api/adhoc_events`, `/api/speaker_lean`, `/api/stream` (SSE), and health endpoints (`/api/health`, `/api/health/feeds`, `/api/health/state`, `/api/health/attribution`). Starts the live TY price and TBBO flow managers with safe degradation if dependencies/credentials are unavailable.

### events.py

Inter-FOMC cycle and event loading engine. Reads `Fed Press Conference` anchors and `bbg_data` releases from `workspace/economic_events.db`, tags events by theme and surprise availability (`full-z`, `proxy-z`, `partial`, `none`), applies alias-based proxy surprise fallback where possible, merges user ad-hoc events, and loads the FOMC speaker lean config.

### live.py

Live feed adapter for the runway. Wraps `DatabentoLiveState` for TY price, `LiveTbboBuffer` for AccessAPI TBBO/order flow, and `workspace/iran_headlines.db` plus the Iran stream heartbeat marker for geopolitics headline freshness. Produces runway payloads and feed health snapshots used by the dashboard and monitor.

### price_history.py

Historical TY candlestick loader for the Macro Path Navigator. Uses PostgreSQL `ohlcv_1m` through `get_ohlcv_from_db("TY%", ...)`, anchors requested windows to the database coverage max (currently useful when live data is ahead of historical OHLCV), resamples into TradingView-style OHLCV intervals (`1D->1m`, `5D->5m`, `1M->30m`, `3M->1h`, `6M/1Y->1D`, `All->1W`), and returns compact candlestick rows plus `coverage_end`.

### headlines.py

Iran/geopolitics headline annotation loader for the Macro Path Navigator. Reads `workspace/iran_headlines.db` and filters to high-signal source classes (`bbg_headline`, forwarded squawk/FinancialJuice/live-squawk/tweet sources, and pasted FinancialJuice), dropping discussion/other rows. Supports optional pandas time-bucket clustering for dense chart windows, returning cluster counts and top headline samples for hover text.

### signals.py

First-pass signal/watch engine for the Macro Path Navigator. Builds conservative headline risk/de-escalation watches from recent high-signal Iran/geopolitics headlines using keyword rules, attaches current AccessAPI TBBO flow context (buy/sell-aggressive imbalance), and adds upcoming event-watch signals from the current cycle. Outputs include confidence, reasons, timestamps, caveats, and explicit assumptions so the UI does not overstate causality.

### distributions.py

Per-event projected move distribution engine for the Macro Path Navigator. Resolves upcoming calendar names (e.g. `NFP JUN`, `JOLTS MAY`) to canonical `bbg_data` names, loads historical instances with z-score/surprise-status metadata, computes TY reaction moves over `2h` or `eod` windows from PostgreSQL `ohlcv_1m`, caches results under `workspace/state/macro_path_navigator/dist_cache/`, and builds unconditional or scenario-filtered (`hot`, `inline`, `cool`) bps distributions with N/N_eff and percentile stats. Also projects percentile moves into price space using the live current price.

### health.py

Health/status utilities for the Macro Path Navigator. Computes feed age/status (`live`, `stale`, `down`), writes local runtime markers under `%LOCALAPPDATA%\market_data_puller\status`, and provides `NavigatorHealth` for consolidated `/api/health` payloads.

### templates/index.html, static/dashboard.css, static/dashboard.js

Browser UI for the live "runway": signal/watch cards, event distribution panel with scenario/window selectors and histogram stats, projected event percentile bands on the live price chart, central TY session candlesticks + upcoming event/headline annotation rail, health strip, historical TY candlestick panel with window dropdown/rangeslider + annotation rail, upcoming event cards, ad-hoc event form, user-tunable factor sliders, and live headline feed. Uses Plotly and SSE/polling to update without adding a Flask-SocketIO dependency; price axes are formatted in Treasury 32nds and annotation details pin into a small detail box on marker click.

### check_macro_path_health.py

Standalone external health monitor for the Macro Path Navigator. Polls `/api/health`, falls back to local marker files if HTTP is down, and sends deduped Telegram stale/down/recovery alerts using `src.core.telegram_notifier`. Supports `--loop`, `--interval`, and `--dry-run`.

### run_macro_path_navigator.bat / run_macro_path_health_monitor.bat

Windows wrappers for launching the dashboard and the separate health monitor. Logs to `%LOCALAPPDATA%\market_data_puller\logs`; the dashboard wrapper writes `macro_path_last_error.txt` if the app exits non-zero.

## File Bus (`src/bus/`)



### file_bus.py

Atomic file operations for inter-agent communication. Provides `write_atomic()` using temp file + rename pattern, `read_json()` for safe reading, and `ensure_dir()` for directory creation. All file writes are atomic to prevent corruption.



### manifest.py

Manifest management for incremental filename generation. `Manifest` class manages `meta.json` per agent workspace, tracking `next_id`, `last_updated`, and `total_runs`. Provides `increment()` for atomic ID allocation and `get_next_filepath()` for generating output paths like `000001.json`.



### schema.py

Output schema validation and templates. `OutputSchema` validates structure (data + metadata). `validate_market_data()` checks market-specific requirements. `create_output_template()` generates standardized output format.



## MCP Infrastructure (`src/mcp/`)



### client.py

Simple MCP client for code-mode tool execution. `MCPClient` discovers and calls tools as direct Python functions (no network protocol). Provides `call_tool()` for execution and `discover_tools()` for scanning servers.



### discovery.py

Tool discovery via decorator registration. `@register_tool` decorator registers tools in global registry with optional `soft_dependencies` for declaring prerequisite tasks (auto-injected by orchestrator) and optional `agent` parameter for explicit agent assignment. `discover_tools()` scans `src/servers/` and imports modules to trigger registration. `get_tool()` retrieves function by name.



### taskmaster_client.py

AI-powered task planning and validation client. Contains `PLANNER1_PROMPT_CORE` (task decomposition) and `PLANNER2_PROMPT_CORE` (tool selection and parameter mapping). Planner 2 prompt references io-schema.md tool specifications and is evaluated using `planner2_metrics.py` (schema validity, tool selection validity, parameter validity, coverage). `TaskPlannerClient` uses OpenAI/Anthropic APIs for intelligent task decomposition, tool selection, and answer validation. Includes a deterministic fallback for “candlestick/chart + dates” queries (and for empty/failed planner outputs) to avoid runs failing with 0 subtasks.



## Market Data Tool (`src/servers/marketdata/`)



### run_query.py

SQL execution tool with progressive disclosure. `@register_tool` decorated `run_query()` function executes parameterized queries on SQLite. Returns full dataset + small sample for logging. Supports templates (by_symbol, by_date, etc.) with column whitelist. `run_multi_date_query()` supports time-based windows via `offset_minutes` and `window_minutes` parameters - e.g., "1 hour after event" queries.



### schema.py

Database schema and security. Defines `ALLOWED_COLUMNS` whitelist, `QUERY_TEMPLATES` for parameterized queries, and validation functions. `build_column_list()` creates safe SQL fragments.



### roll_calendar.py

Calendar-based front-month contract determination for treasury futures. Implements deterministic roll logic: roll on last Friday of month before expiry, with 7-day earlier shift if < 2 business days to month end. Key functions: `get_front_contract_for_date()` returns contract symbol (e.g., "TYZ4 Comdty"), `assign_contract_symbols()` for batch processing, `is_treasury_symbol()` for type checking. Symbol mapping: ZN→TY, ZB→US, ZT→TU, ZF→FV. Pre-builds roll calendar 2019-2030 for fast lookups.



## Treasury Auction Data Puller (`src/servers/auctiondata/`)



### schema.py

Treasury FiscalData auction schema and formatting constants. Defines the `auctions_query` base URL/path, coupon term-to-CSV mapping for 2Y/3Y/5Y/7Y/10Y/20Y/30Y, API field-to-existing-Securities.csv header mapping, and formatters for Treasury date/money/percent fields.



### fiscal_data_client.py

Stdlib-only client for the Treasury FiscalData `auctions_query` endpoint. Fetches recent or full-history coupon auction rows with server-side filters, field selection, pagination, retries, and JSON response validation.



### csv_writer.py

Schema-preserving raw auction CSV writer. Bootstraps missing raw files from the canonical `10y_note_raw.csv` header, converts FiscalData API rows into the existing Treasury CSV column names, appends only unseen CUSIPs, sorts by auction date descending, and writes atomically.



### state.py

JSON state helper for Telegram alert dedupe. Persists CUSIP -> UTC alerted timestamp at `workspace/state/auction_puller_state.json` with atomic writes and corrupt-state fallback to empty.



### alert_formatter.py

Formats Treasury auction Telegram alerts using the same `derive_nomura_fields()` function as the processed CSV pipeline. Produces Stop/Bid-Cover/Yld Gap plus Dealer/Direct/Indirect take and hit-rate summary lines.



## Polymarket Tool (`src/servers/polymarket/`)



### schema.py

Polymarket tool schema and constants. Defines `POLYMARKET_API_BASE_URL` (CLOB API), `POLYMARKET_GAMMA_BASE_URL`, result limits, market status constants, database table name, and validation functions. Provides `format_market_result()` for standardizing API responses, `parse_probability_from_price()`, `calculate_avg_probability()`, and `calculate_total_volume()` for market data processing.



### get_price_history.py

Historical price data tool using Polymarket CLOB API. `fetch_price_history_from_polymarket()` calls the `prices-history` endpoint to get time-series price data. `find_price_at_target_time()` uses weighted averaging to find prices at specific timestamps. `@register_tool` decorated `get_market_price_history()` returns prices for a specific date. `get_market_price_range()` fetches price trends over date ranges. Uses stdlib urllib only (no web3 or blockchain dependencies).



### search_markets.py

Polymarket market search tool built on the Gamma API. `@register_tool` decorated `search_polymarket_markets()` can fetch recent/popular markets, run keyword filtering, and (optionally) apply LLM-based relevance scoring. Validates market data and stores results in `polymarket_markets.db` with prices, volume, liquidity, and outcomes. The main Polymarket agent now uses a simpler `/markets` volume-sorted flow instead of the full hybrid LLM pipeline.



### llm_relevance_scorer.py

LLM-powered relevance scoring utilities for Polymarket search. Provides `score_market_relevance_batch()` for batch GPT-4 scoring of markets (0-10 scale with reasons), `score_market_relevance_streaming()` for individual scoring, and `hybrid_search()` combining fast keyword filtering with accurate LLM re-ranking. Auto-loads OpenAI API key from `config/keys.env` or environment. Gracefully falls back to keyword-only if API key unavailable or LLM calls fail. Always returns at least 1 result if any markets exist (multi-layer fallback: LLM → keyword → top by volume).



### get_history.py

Query history retrieval tool for Polymarket. `@register_tool` decorated `get_polymarket_history()` function queries `polymarket_markets.db` table `prediction_queries` by session_id, date range, or limit. Returns historical queries with market results (parsed JSON including relevance scores if LLM was used).



### poll_midpoint.py

Polling utilities for CLOB midpoint prices. Provides REST calls to `/midpoint` and `/price`, creates/ensures the polling tables, inserts probability time-series rows, and now exposes `query_recent_timeseries()` for chart-friendly retrieval of the last N hours of midpoint history.



## Analytics Plotting Infrastructure (`src/servers/analytics/`)



### core_plotting.py

Core PNG plotting functions using matplotlib/plotly. Config-driven styling from `config/plot_config.yaml`. Four main functions: `plot_distribution()` for histograms with KDE, `plot_timeseries()` for candlestick/line charts, `plot_categorical()` for bar charts, `plot_correlation()` for scatter plots with regression. Also includes `plot_multi_distribution()` for grid layouts. All functions return `{success, png_path, metadata}`.



### plot_factory.py

Unified plotting interface with context-aware auto-detection. `create_plot(data, context, title, output_dir)` dispatches to appropriate core function based on context (event_distribution, returns_distribution, volatility_distribution, candlestick, correlation, surprise_analysis, probability_matrix, generic). Includes context handlers for each distribution type.



### plotting.py

SVG plotting tools (stdlib-only) plus re-exports of core PNG functions. Pure Python SVG generation for histograms, line charts, scatter plots, bar charts. Also includes `generate_candlestick_chart()` using Plotly with bond price formatting (32nds) and economic event markers - declares `soft_dependencies` for automatic event fetching via orchestrator. Re-exports `plot_distribution`, `plot_timeseries`, `plot_categorical`, `plot_correlation`, `create_plot` from core modules.



### schema.py

Analytics schema and constants. Defines plot dimensions, color schemes, bond price conversion functions (`decimal_to_32nds`, `thirtytwonds_to_decimal`), and treasury futures BPS conversion utilities (`decimal_to_bps`, `bps_to_decimal`, `ticks_to_bps`, `bps_to_ticks`, `decimal_to_ticks`, `ticks_to_decimal`). Also includes `BPS_FACTORS` dictionary and `get_bps_factor()` for instrument-specific conversions. Data validation functions and output schema definitions.



### plugins/__init__.py

Plugin auto-discovery module. Scans `src/servers/analytics/plugins/` for subdirectories with `__init__.py` files and imports them to trigger `@register_tool` decorators. Plugins are loaded alphabetically; directories starting with `_` (except `_example`) are skipped. Failed imports are logged but don't crash the system.



### plugins/README.md

Complete plugin developer guide. Documents plugin structure, `@register_tool` decorator usage, input schema format (JSON Schema), return format conventions (`{success, data, metadata}`), error handling patterns, available utilities (core_plotting, schema, plot_factory), git submodule setup, and testing guidelines. Includes checklist for plugin compatibility.



### plugins/_example/__init__.py

Reference plugin implementation with two example tools: `example_summary_stats` (computes mean, min, max, range, std) and `example_normalize_data` (min-max normalization). Demonstrates proper use of `@register_tool` with explicit `agent="analytics_agent"` parameter, input schema, return format, and error handling.



## Distribution Analytics Server (`src/servers/distro_analytics/`)



### __init__.py

Module registration for distribution analytics MCP tools. Imports and exposes seven tools: `compute_returns_distribution`, `compute_volatility_distribution`, `compute_probability_matrix`, `compute_event_distribution`, `plot_log_bps_distribution`, `compute_surprise_correlation`, `compute_surprise_price_distribution`. All tools use core_plotting for standardized PNG output.



### db_reader.py

Database reader utilities for distribution analytics. `get_ohlcv_from_db()` queries PostgreSQL `ohlcv_1m` table and returns pandas DataFrame; **automatically resolves generic futures patterns (e.g., "TY%") to front-month contract (highest volume) to avoid multi-contract artifacts**. `_get_front_month_symbol()` and `_resolve_to_front_month()` helper functions handle front-month lookup. `get_events_from_db()` queries `economic_events.db`. `add_timezone()` converts timestamps to US/Eastern. Re-exports BPS utilities (`BPS_FACTORS`, `get_bps_factor`, `decimal_to_bps`, etc.) from `src/servers/analytics/schema.py`.



### returns_distribution.py

MCP tool for session-based returns distribution. `@register_tool` decorated `compute_returns_distribution()` computes returns across trading sessions (London, US Open, US Mid, US Close, Asia). Uses `plot_factory.create_plot()` for auto-generated PNG plots. Returns statistics per session (mean, std, percentiles), latest return with percentile rank and z-score.



### volatility_distribution.py

MCP tool for volatility (High-Low) distribution. `@register_tool` decorated `compute_volatility_distribution()` computes session-based volatility using (High - Low) * bps_factor. Uses core_plotting for PNG output.



### probability_matrix.py

MCP tool for probability matrices. `@register_tool` decorated `compute_probability_matrix()` computes probability of price moving X bps within Y hours. Supports versions: Absolute, Up, Down. Uses core_plotting for distribution and bar chart PNG output.



### event_distribution.py

MCP tool for event-specific return distributions. `@register_tool` decorated `compute_event_distribution()` analyzes price movements around economic events (NFP, CPI, etc.). Uses `plot_factory.create_plot()` for auto-generated multi-distribution PNG. Also includes `plot_log_bps_distribution()` for log-transformed BPS histograms with KDE.



### surprise_analysis.py

Tools for surprise vs price return analysis. `compute_surprise_correlation()` computes Pearson/Spearman correlations between surprise and price percentiles with bucket statistics. `compute_surprise_price_distribution()` creates interactive HTML with histogram exploration and PNG correlation plots via core_plotting.



## EventData Puller (`src/servers/eventdata_puller/`) & TradingEconomics (`src/servers/tradingeconomics/`)



### schema.py

Trading Economics API schema, constants, and helper functions. Defines API endpoints, database paths, event categories, importance levels, country codes, and database schema SQL. Key functions: `format_event_result()` standardizes API responses, `_parse_event_date()` converts UTC timestamps to US/Eastern local time (EST/EDT with automatic DST handling), `normalize_country()` maps codes to API names, `build_api_url()` constructs authenticated API URLs.



### query_events.py

Tools for querying historical economic events. `@register_tool` decorated `query_event_history()` supports rich filtering by id, name, country, and date. Returns events with both UTC (`event_date`) and local (`event_date_local`) timestamps. `search_events()` returns distinct event types with occurrence counts. `get_events_by_date_range()` retrieves events in a given date/time range. `get_next_event_instance()` finds the next future occurrence of an event with full component data (actual, consensus, previous) plus related events at the same timestamp. `get_historical_event_instances()` returns historical occurrences with full component data for scenario analysis.



### update_calendar.py

Tool for fetching new calendar data from TradingEconomics API. `@register_tool` decorated `update_calendar()` stores data in `economic_events.db`.



## Anomaly Detection (`src/servers/anomaly_detection/`)



### perplexity_search.py

Perplexity API client for market news, fade lookup, and FOMC sentiment search (stdlib-only, uses `urllib.request`). Key functions: `_call_perplexity_api(messages, model, max_tokens, temperature, timeout)` low-level API call with error handling; `search_market_news(date, symbol, context)` MCP tool for finding news explaining unusual market moves; `search_market_fade(date, event_name, symbol, context)` fetches an approximate post-event fade size with unit parsing and bps conversion; `search_fomc_sentiment(date, force_refresh)` searches for FOMC meeting sentiment classification returning `{label, confidence, summary, citations}` with structured JSON parsing. Uses `PERPLEXITY_API_KEY` from config/keys.env. Prompt version tracked via `FOMC_SENTIMENT_PROMPT_VERSION` constant.



### annotate.py

High-level annotation tools combining detection, event matching, and search. `annotate_date_range(start_date, end_date, symbol, z_threshold, use_perplexity)` full annotation pipeline: detect anomalies → match economic events → search Perplexity for unexplained moves → store notes. `explain_move(date)` provides context for a specific day's market move.



### detect.py

Anomaly detection logic for identifying unusual market moves based on z-score thresholds.



### storage.py

SQLite storage for anomaly notes and annotations.



## Market Data Agent (`src/agents/market_data_agent/`)



### run.py

Producer agent main logic. `MarketDataAgent` class orchestrates: input validation, MCP tool calling, manifest management, atomic output writing, and run logging. `run()` method returns output path. Each execution writes `out/{id}.json` and `logs/{timestamp}.json`.



### config.py

Agent configuration constants. Defines `AGENT_NAME`, workspace paths, `DEFAULT_COLUMNS`, `AVAILABLE_TEMPLATES`, `REQUIRED_PARAMS` per template, and `MAX_ROWS` limit.



### prompt.md

Agent instructions and examples. Documents purpose, inputs, outputs, process flow, and example usage patterns.



## Consumer Agent (`src/agents/consumer_agent/`)



### run.py

Consumer agent that processes producer output. `ConsumerAgent` reads file from producer, validates schema, computes summary statistics (bid/ask min/max/avg), and emits derived artifact. Demonstrates inter-agent communication.



### config.py

Consumer configuration constants. Defines `AGENT_NAME`, workspace paths, output directories.



## Polymarket Agent (`src/agents/polymarket_agent/`)



### run.py

Unified Polymarket agent implementing a simple, API-only search. `PolymarketAgent` calls the Gamma `/markets` endpoint to fetch active markets sorted by volume, filters them by keyword relevance and high volume, validates prices, and writes standardized outputs to the file bus (`out/{id}.json`) and logs (`logs/{timestamp}.json`).



### config.py

Agent configuration constants. Defines `AGENT_NAME`, workspace path helper, output/log directory names, `LOW_VOLUME_THRESHOLD`, `DEFAULT_LOOKBACK_DAYS`, `MAX_MARKETS_TO_RETURN`, and `MAX_QUERY_LENGTH`.



### prompt.md

Agent instructions and examples. Documents purpose, inputs (query, session_id, limit), unified reasoning flow, volume flagging, and integration with Polymarket tools.



## Orchestrator Agent (`src/agents/orchestrator_agent/`)



### run.py

Meta-agent with **Two-Stage Planner Architecture**. `OrchestratorAgent` implements workflow: Planner 1 (decomposition), Planner 2 (tool selection), Coder (script gen), Workers (execution), Runner (consolidation).



### config.py

Orchestrator configuration. Defines `AGENT_CAPABILITIES` registry with per-agent `enabled` toggle, database paths, execution timeouts, and directory structure. Helper functions: `get_enabled_agent_capabilities()`, `get_enabled_agent_names()`, `is_agent_enabled()`, `get_planner_agent_registry()`. The `enabled` flag controls whether agents and their tools are discovered during planning.



### planner_stage1.py

Stage 1 Planner - Task decomposition and dependency analysis. Uses `TaskPlannerClient`, `TaskMapper`, and `DependencyAnalyzer`.



### planner_stage2.py

Stage 2 Planner - Tool discovery per dependency path. Uses `ToolLoader` for lazy loading to isolate context. After AI tool selection, calls `DependencyResolver` to inject soft dependency tasks.



### dependency_resolver.py

Soft dependency resolution for tool prerequisites. `DependencyResolver` scans execution plans for tools with `soft_dependencies` metadata (declared in `@register_tool`), generates prerequisite tasks, and wires outputs to dependent tools. Supports context resolution (`$context.time_range.start`), graceful degradation (fallback values on failure), and resolution notes tracking. `build_context_from_plan()` extracts time ranges, symbols, and countries from task parameters for dependency resolution.



### dependency_analyzer.py

DAG analysis and path extraction. Builds dependency graphs, detects cycles, extracts execution paths, and computes parallel groups.



### tool_loader.py

Lazy tool loading for context isolation with **dynamic agent-tool mapping**. `AGENT_TOOL_MAP` property is now dynamically built from registry metadata instead of being hardcoded. Agent assignment uses: (1) explicit `agent` param in `@register_tool`, (2) auto-detection from module path via `MODULE_TO_AGENT` mapping, (3) fallback to `_FALLBACK_AGENT_TOOL_MAP`. Key methods: `get_enabled_agent_tool_map()`, `load_tools_for_agents()`, `get_tools_for_agent()`, `rebuild_agent_tool_map()`. Respects agent `enabled` flag - disabled agents return no tools.



### coder.py

Python script generation with DB persistence and shared task optimization. Generates async execution scripts that call MCP tools, coordinate shared task execution via atomic DB claims (prevents duplicate work when multiple paths share tasks), and write results to SQLite. Scripts check if tasks are already completed or running, claim unclaimed tasks atomically, and reuse results when tasks are completed by parallel scripts.



### workers_db.py

SQLite database for worker task outputs and unified planning. Manages `worker_runs`, `task_outputs`, and `planner1_tasks` (unified Planner 1 + Planner 2) tables. Planner 1 writes base task data, Planner 2 enriches with tools/params, all in one table per run_id.



### worker_executor.py

Task execution with parallel path execution and dependency handling. Executes generated scripts in parallel (independent paths run concurrently), manages DB persistence, handles dependency waiting. Single paths run directly; multiple paths use asyncio.gather for concurrent execution.



### planner_metrics.py

Structural and LLM-as-judge metrics for Planner 1 evaluation. Validates DAG structure, schema validity, agent parameter extraction. Provides scoring for coverage, granularity, dependency precision, faithfulness, and agent mapping.



### planner2_metrics.py

Metrics for Planner 2 evaluation. Validates tool selection, parameter completeness, schema validity, and coverage against expected outputs.



### tool_schema_generator.py

Generates comprehensive tool documentation from registered tool schemas. Extracts input parameters, types, requirements, and output schemas for Planner 2 prompt context. Provides `validate_tool_params()` and `check_data_flow()` for parameter validation.



### task_mapper.py

Task-to-agent mapping with parameter extraction. Maps task descriptions to agents via keyword scoring.



## Runner Agent (`src/agents/runner_agent/`)



### run.py

Standalone GPT-class runner agent for final consolidation and answer generation. Called by Orchestrator after worker agents complete. `RunnerAgent` class loads OpenAI client from `config/keys.env`, calls GPT model with system prompt for research analyst persona, and generates final user-facing answer. Inputs: query, worker_outputs (from WorkersDB), planning_table, run_id. Writes standardized output to file bus. Falls back to structured summary if no AI client available. Configurable via env vars: `REASONING_MODEL_NAME`, `REASONING_TEMPERATURE`, `REASONING_MAX_TOKENS`.



### config.py

Runner agent configuration. Defines `AGENT_NAME` ("runner-agent"), `AGENT_VERSION`, `DEFAULT_MODEL` ("gpt-4o"), `DEFAULT_TEMPERATURE` (0.2), `DEFAULT_MAX_TOKENS` (1500). Provides path helpers for workspace and output directories.



### prompt.md

Agent instructions documenting purpose, inputs (query, worker_outputs, planning_table), output format with final_answer and reasoning_metadata, and integration notes.



## Analytics Agent (`src/agents/analytics_agent/`)



### run.py

Analytics agent for statistical analysis and visualization (no DB access). `AnalyticsAgent` provides statistical methods (`compute_statistics`, `compute_percentile_rank`, `compare_distributions`, `compute_correlation`) and visualization methods (`generate_histogram`, `generate_line_chart`, `generate_scatter_plot`, `generate_bar_chart`, candlesticks). The standalone `run()` supports only in-memory analysis types (descriptive, percentile_rank, comparison, correlation) and will error if asked to fetch from databases.



### config.py

Analytics agent configuration. Defines `AGENT_NAME` ("analytics-agent"), workspace path helpers, `DEFAULT_PERCENTILES` [5,10,25,50,75,90,95], plot defaults, `ANALYSIS_TYPES` (DB-backed types removed), and validation constants.



### prompt.md

Agent instructions documenting analytical capabilities, input formats, output formats, and example usage.



## Core (`src/core/`)



### logging_config.py

Centralized logging setup. `setup_logging()` configures file + console handlers.



### telegram_notifier.py

Telegram Bot API notifier for one-way push alerts. Uses stdlib-only HTTP (urllib.request) with no external dependencies. Provides `send_telegram_message()` for text alerts and `send_telegram_photo()` for image alerts. Includes `format_event_alert()` for formatting economic event alerts with surprise data, percentiles, and market reactions. Loads credentials from `config/keys.env` via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.



### x_api_client.py

Stdlib-only X API v2 client for filtered stream rules and streaming. Supports list/add/delete rules, user handle validation via `/users/by`, and streaming consumption via `iter_stream_events()` with newline JSON parsing. HTTP failures now include X response details plus selected rate-limit headers (`retry-after`, `x-rate-limit-*`) so filtered-stream `429` issues can be diagnosed as connection-limit vs broader throttling.



### graph_mail_client.py

Stdlib-only Microsoft Graph mail client for Bloomberg email news ingestion. Supports `BBG_GRAPH_AUTH_MODE=app` with app-only OAuth2 client credentials (`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`) and `BBG_GRAPH_AUTH_MODE=delegated` with device-code sign-in plus a local refresh-token cache (`~/.market_data/graph_delegated_token.json`). Reads app-only mail from `GET /v1.0/users/{mailbox}/messages` or delegated mail from `GET /v1.0/me/messages`, normalizing sender, subject, preview, received time, and web link while filtering configured Bloomberg senders/domains client-side.



### outlook_mail_client.py

Local Outlook Desktop COM client for Bloomberg email news ingestion. Uses `win32com.client` against the primary Outlook profile Inbox (`GetDefaultFolder(6)`), sorts by `ReceivedTime` descending for catch-up polling, filters configured senders/domains such as `nlrt@bloomberg.net`, and emits the same normalized message shape as the Graph client with an `outlook:<EntryID>` dedupe key. Also provides `watch_inbox()` using the Outlook `Items.ItemAdd` COM event with a `pythoncom.PumpWaitingMessages()` loop so new Bloomberg mail can be pushed to Telegram immediately without interval polling.



### x_macro_store.py

SQLite-backed store for X macro stream subscriptions (handles + tiers + modes) and keywords, plus `tweet_labels` for approval tracking. Tweet labels now include an optional escalation `score` (-5..+5) and use an atomic conditional label update (`label IS NULL`) so concurrent Telegram taps cannot double-forward a tweet.



### bbg_news_store.py

SQLite-backed store for Bloomberg email news moderation state. Uses `news_labels.id` as a short Telegram callback key and `graph_id` as the unique Microsoft Graph dedupe key, with label/score/labeled_by/labeled_at and `forwarded_message_id` fields matching the X tweet forward-before-commit approval workflow.



### polymarket_stream_store.py

SQLite-backed store for Telegram Polymarket tracking state. Persists chat subscriptions, pending inline multi-select sessions, per-alert approval labels, and per-subscription alert evaluation fields (`last_mid`, `last_alert_mid`, `last_alert_ts`, trend streak fields) used by the background polling loop.



### x_rules.py

Rule builder for X filtered stream rules. Generates account-only rules and keyword-gated rules with safe tag prefixing, keyword quoting, and 2048-character chunking logic.



### schedule_store.py

SQLite-based schedule store for upcoming economic events. Manages `scheduled_events` table with status tracking (scheduled/running/completed/failed) and `event_run_history` table for execution logs. Provides CRUD functions: `add_scheduled_events()`, `get_due_events()`, `get_upcoming_events()`, `update_event_status()`, `record_run_history()`. Also includes `parse_csv_events()` for loading event definitions from CSV files.



### run_store.py

Central run and agent output persistence for orchestration runs. Manages `data/metadata.db` SQLite database with `runs` and `agent_outputs` tables, plus Telegram integration tables (`telegram_run_messages`, `telegram_feedback`) for reply-to feedback logging. Provides `create_run()`, `update_run_status()`, `log_agent_output()`, `write_run_artifact()`, `get_run_dir()`. Stores artifacts in `runs/{run_id}/` directory structure (query.json, planner1.json, planner2/, scripts/, workers/, runner_output.json).



### telegram_feedback_store.py

Telegram feedback persistence helpers (stdlib-only). Provides APIs to log bot message→`run_id` mappings (`log_telegram_run_message`, `get_run_id_for_telegram_message`) and insert self-contained feedback snapshots into `data/metadata.db` (`insert_telegram_feedback`), including helpers to read query text and orchestrator output paths for a given `run_id`. Also provides `read_run_final_answer(run_id)` to retrieve the final answer from runner output for conversational follow-up context.



### date_tags_store.py

CRUD operations for trader notes/annotations with flexible date/time parsing. Stores date-tagged notes in `workspace/economic_events.db` in the `date_tags` table. Uses `dateparser` library for flexible date/time input (e.g., "jan 10 8:30am", "yesterday 2pm", "last friday"). Key functions: `add_tag(raw_input, created_by)` parses datetime from text start and extracts hashtags, `get_tags(date, hashtag, limit)` retrieves tags with filters, `get_tag_stats()` returns hashtag usage statistics, `delete_tag(tag_id)` removes a tag, `format_tag_for_display(tag)` formats for Telegram output. Supports inline hashtags (e.g., `#nfp #fed`) for categorization. Date parsing settings: DMY order, prefer past dates, US/Eastern timezone.



### fomc_sentiment_cache.py

Persistent JSON cache for per-date FOMC sentiment labels and summaries. Cache location: `workspace/fed/fomc conference/fomc_web_labels.json`. Key functions: `get_cached_sentiment(date, prompt_version)` retrieves cached entry if version matches, `set_cached_sentiment(date, label, confidence, summary, citations, prompt_version)` stores entry, `get_or_fetch_sentiment(date, force_refresh)` returns cached or fetches from Perplexity, `batch_get_or_fetch_sentiments(dates, force_refresh, progress_callback)` processes multiple dates with progress tracking. Cache entries include: label (hawkish/dovish/neutral/unknown), confidence (0-1), summary, citations, prompt_version, cached_at timestamp.



### fomc_qa_parser.py

Deterministic transcript parser for FOMC Q&A structure. Builds speaker-attributed utterances from timestamped transcript segments, detects `question -> CHAIR POWELL answer` boundaries, and emits answer blocks anchored on Powell answer start times with parse-confidence metadata.



## Redis Event Streaming Service (`src/services/redis_event_stream/`)



### models.py

Dataclasses for the Redis pre-alert pipeline. `ScheduledEvent` defines the canonical Postgres row shape (`event_id`, `event_name`, `country`, `scheduled_time_et`, `source_file`, `is_active`, `updated_at`) and provides deterministic SHA1 `event_id` generation. `EventMessage` maps scheduled events into Redis stream payload fields.



### session_config.py

DST-aware session marker generator for Tokyo, London, and US bond sessions. Builds local-market open/close events and converts them to `America/New_York` timestamps using `zoneinfo`, with weekday-only and holiday-date suppression support.



### excel_parser.py

Parses the weekly Excel `Calendar` sheet into normalized scheduled events for DB upsert, including mixed time-format handling (`HH:MM:SS`, `H:MM AM/PM`, ranged strings like `3:15 - 4 AM`). Also parses `holidays`/`holiday` sheets into country holiday maps and emits parse quality counters.



### schema.sql

PostgreSQL DDL for `scheduled_event_alerts` and `scheduled_event_alerts_archive`, including indexes for active-time and watermark polling queries plus an `updated_at` trigger on row updates.



### ingestor.py

Excel-to-Postgres ingestion pipeline. Ensures schema, upserts parsed calendar and generated session rows, soft-deactivates missing rows per source, archives/purges old rows per retention policy, and enforces row-count guardrail logging.



### publisher.py

Standalone daemon that bootstraps active events from Postgres, then polls deltas by `updated_at` watermark and updates APScheduler jobs. Publishes a single 10-minute pre-alert per event to Redis stream `market:events`, guarded by Redis dedupe keys.



### subscriber.py

Redis consumer-group subscriber client and CLI. Ensures group creation, reads messages via `XREADGROUP`, parses stream fields into typed event messages, and acknowledges entries (`XACK`) for reliable multi-subscriber operation.



## Configuration (`config/`)



### settings.py

Application-wide settings. Defines paths, DB configs (PG/SQLite), API key loaders, and agent capabilities registry.



## Tests (`tests/`)



### e2e/test_marketdata_e2e.py

End-to-end test suite for market data pipeline.



### e2e/test_predictions_e2e.py

End-to-end test suite for predictive markets agent.



### e2e/test_polymarket_e2e.py

End-to-end test suite for Polymarket agent.



### e2e/test_orchestrator_e2e.py

End-to-end test suite for Orchestrator Agent.



### unified_dashboard/test_api_smoke.py

Smoke tests for unified dashboard API endpoints (config, event types, events, candlesticks). Skips if server is not running.



### unified_dashboard/test_composition_smoke.py

Composition smoke test that fetches the dashboard HTML and asserts key panel containers exist. Skips if server is not running.



### unified_dashboard/test_tab_html_smoke.py

Unit smoke tests for tab HTML generators (pre/post/adhoc/weekmatch) to ensure key panel IDs render.



### test_x_rules.py

Unit tests for X filtered stream rule building (handle normalization, keyword quoting, and chunking under the 2048-character rule limit).



### scripts/validate_l1_sample.py

Validates a small fixture set of L1 event files (meta + price_1m/price_5m) for CI gating.



## Scripts (`scripts/`)



### pm_monday_breakevens_export.py

Standalone PricingMonkey Monday forward-breakeven exporter at the repo root. Reuses stable Playwright/grid helpers from `pm_nfp_weeks_export.py`, but targets the Monday board URL and auto-generates the last N Mondays (`--count`, default 50). For each Monday it sets BF to the prior Friday (`d-3`) and AF to Monday, then writes transformed rows (`EVENT, DATE, TIME, PRICE_BPS, FWD_BE, IMPLIED_VOL, TRADE_DESCRIPTION`) to `monday_breakevens_results.csv`.



### telegram_bot.py

Telegram bot interface for the Market Data Orchestrator. Receives natural language queries via Telegram, runs them through the OrchestratorAgent, and returns answers, plots, and JSON output. Features: authorization via ALLOWED_CHAT_IDS, group chat support with @mention requirement, reply-to context for conversational follow-ups (detects when user replies to a previous bot answer, loads query/answer context via `_build_followup_context()`, and enriches follow-up query), `/query` command for explicit queries, `/feedback` command for reply-based feedback submission, `/pre_event` command for pre-event B/E analysis dashboards (buckets by consensus std dev), `/post_event` command for post-event B/E analysis dashboards (buckets by surprise percentile), `/live_pre_event <cons_std> <fwd_be>` command for starting a LIVE dashboard with ngrok tunnel (returns public shareable URL), `/live_pre_event_stop` command to stop the live dashboard, automatic plot and JSON file delivery. **Date tagging commands**: `/tag <datetime> <comment>` adds trader notes with flexible date/time parsing and inline hashtags, `/tags [date] [#hashtag]` views tags with filters, `/tagstats` shows hashtag usage breakdown, `/untag <id>` deletes a tag. Also integrates X macro stream handlers (including `tweet_score` callbacks and `/x_retry_forwards`), Bloomberg email-news approval callbacks (`bbgnews_*` plus `/bbg_retry_forwards`), and both Polymarket selection callbacks and Polymarket alert approve/reject callbacks; the separate `x_macro_stream_bot.py` and `bbg_news_stream_bot.py` services handle collection and Telegram sending. The application enables concurrent Telegram update handling, uses longer Bot API HTTP timeouts, and registers a top-level error handler so long-running orchestrator queries and transient Telegram delays do not silently stall callback buttons. Uses `telegram_feedback_store.py` for run_id↔message mapping and `date_tags_store.py` for trader annotations.



### polymarket_probability_tracker.py

Polling-first Polymarket tracker. Resolves a natural-language query to markets via Gamma search, then polls the CLOB `/midpoint` endpoint for token IDs on a fixed cadence, inserting probability time-series rows into `polymarket_markets.db`. Supports change-only inserts, max-gap heartbeat, and optional re-resolve cadence.



### polymarket_stream_bot.py

Telegram-integrated Polymarket tracking module for the main polling bot. Adds `/pm_track` (query -> market candidates -> inline multi-select confirm), `/pm_list`, `/pm_untrack`, callback handlers for both market selection and per-alert approve/reject actions, optional forwarding of approved alerts to `POLYMARKET_PUBLIC_CHAT_ID`, and a background polling task that samples midpoint probabilities (default 30s), writes rows to `market_prob_timeseries`, and emits chart-based Telegram alerts only for `significant_change` or `trend`. Alert approvals now retry transient Telegram send failures and commit labels only after public-chat forwarding succeeds, leaving unforwarded approvals retryable. Alerts render a readability-focused chart snapshot: only mover markets are plotted, the legend sits above the plot instead of on top of data, captions use full market titles with explicit `Odds:` lines, and the notification preview now always starts with `Polymarket Odds Update` without echoing the user query so market-question text is not mistaken for confirmed news.



### x_macro_stream_bot.py

Always-on X filtered-stream collector/sender service. Seeds a foundation handle list and keywords, reconciles X stream rules, validates handles, and forwards matching posts to Telegram using the consolidated `TELEGRAM_BOT_TOKEN` (no polling loop in this process). Adds a single-step 13-button moderation keyboard per tweet: scores -5..+5 approve+forward with a rubric line, `Approve (no score)` forwards without an opinion, and `Reject` labels without forwarding; approval forwards now retry transient Telegram timeouts and only commit new labels after successful public-chat sends. Provides `/x_retry_forwards [limit]` to replay approved-but-unforwarded historical tweets; command handlers are imported by `scripts/telegram_bot.py`.



### bbg_news_stream_bot.py

Always-on Bloomberg email news collector/sender service. Selects its mailbox backend via `BBG_MAIL_SOURCE`: `outlook_com` reads the local Outlook Desktop primary Inbox with COM, while `graph` reads Outlook/Office 365 via Microsoft Graph. It supports `--mode event` (Outlook `ItemAdd` real-time push with startup catch-up and optional `--safety-poll-seconds`) and `--mode poll` (interval polling), posts each new message to the same macro review chat(s) as X (`X_MACRO_CHAT_ID`) with compact headline-only rendering (subject + received timestamp), `bbgnews_score:<id>:<score>`, `bbgnews_approve:<id>`, and `bbgnews_reject:<id>` buttons, and forwards approved items to `X_MACRO_PUBLIC_CHAT_ID` only after a successful public send. The full email body remains stored in local SQLite moderation state (`~/.market_data/bbg_news.db`) for audit/analytics. Supports a watermark file, `--once`, `--dry-run`, and `/bbg_retry_forwards [limit]` through the main polling bot.



### SOD launchers (`Y:\Aastha\SOD\*.bat`)

Operational Windows launchers for the SOD suite. `run_all_sod.bat` starts the full stack additively (aggression dashboards v1/v2/v3 plus Telegram, Bloomberg news, X tweets, and Polymarket bots), performs consolidated dependency checks, validates keys with warnings only, checks externally managed AccessAPI/PostgreSQL reachability, explicitly starts Outlook Classic for Bloomberg COM ingestion, and skips already-running processes/ports without killing or restarting anything. `run_aggression_monitor.bat` starts V1 on port `5050` and loops after a crash (`/watchdog` skips the browser). `watch_aggression_monitor_v1.ps1` plus scheduled task `AggressionMonitorV1Watchdog` start V1 only when `5050` is down, using a hidden PowerShell window so the one-minute check does not pop a console. `run_aggression_monitor_v3.bat` starts the V3 depth dashboard on port `5053` with the extra `msgpack` dependency; standalone bot launchers now include duplicate guards and lightweight dependency checks.


### docs/sod_auto_start.md

Operator guide for the SOD auto-start suite. Documents `run_all_sod.bat`, its additive/no-restart safety contract, the approved V1 crash keep-alive (`AggressionMonitorV1Watchdog`), externally managed dependencies, explicit Outlook Classic startup behavior, and optional at-logon Task Scheduler commands for `SODAutoStart` without auto-registering that task.


### overnight_session_distribution_dashboard.py

Standalone HTML dashboard generator for US overnight sessions (6pm→8am ET). Loads OHLCV from PostgreSQL, computes per-session returns and volatility in bps, embeds data into a self-contained HTML with Distro-style histograms + KDE, stats boxes (mean/percentiles/expected rally-selloff/0 bps percentile), and filters for date range, year, month, and day-of-week. Outputs to `workspace/scenarios/`.



### build_preevent_standalone_html.py

Standalone pre-event NFP HTML builder that injects fresh event data into the legacy template HTML, enforces time-series sorting, updates window constants, and adds expected rally/selloff stats in distribution boxes. Outputs a self-contained HTML in `workspace/scenarios/`.



### preevent_standalone_dashboard.py

Standalone **pre-event-only** HTML dashboard generator (embedded, no server). Wraps `scripts/live_unified_dashboard.py` as a library to produce a shareable HTML file in `workspace/scenarios/`, with user overrides for `--fwd_be` and `--cons_std` (and optional `--consensus`), similar to the standalone post-event scripts pattern.



### backfill_ty_from_databento.py

Backfills missing continuous `TY` 1-minute OHLCV bars into PostgreSQL `ohlcv_1m` using Databento as upstream. It loads `DATABENTO_API_KEY` from the environment or `Z:\Aarsh\Data\AccessAPI\.env`, auto-selects the next quarterly ZN contract for event dates (overrideable with `--raw-symbol`), filters Treasury prices to `80..150`, and inserts idempotently via `(ts_event, symbol)` conflicts. Used by the unified live dashboard refresh path before rebuilding L1 event features.



### refresh_dashboard_data.py

Orchestrates the unified live dashboard refresh cycle: optional economic-events staleness check, Databento TY backfill, L1 feature rebuild, and live unified dashboard cache rebuild. CLI supports event filtering, `--force-l1`, skip flags for each stage, and backfill dry-run mode. Intended for manual use and the Windows scheduled refresh wrappers.



### run_dashboard_refresh.bat / install_dashboard_refresh_task.ps1

Windows Task Scheduler wrappers for `scripts/refresh_dashboard_data.py`. The installer creates `UnifiedDashboardRefreshNightly` (daily 02:30) and `UnifiedDashboardRefreshPostRelease` (weekdays 09:15), normalizing mapped-drive script paths to UNC and logging refresh output locally plus under `workspace/logs/`.



### preevent_boxplot_dashboard.py

Pre-event B/E analysis dashboard. Generates interactive HTML dashboard for analyzing pre-event B/E multiplier across **consensus std dev buckets** with price paths from 3pm previous day to 8:30am event day. Features: box plots by cons_std bucket, adjustable end time slider, price path visualization, event details on click, **interactive Plotly candlestick chart modal** (click price path → View Candlestick button → opens 5-min candlestick chart with +/-1 to +/-5 day duration slider, zoom/pan support, NFP release vertical marker). Calculates B/E = (high - open) * 32/2 / FWD. Prefetches 5-min OHLC data for +/-5 days around each event. CLI: `python scripts/preevent_boxplot_dashboard.py --consensus 180 --cons_std 25`. Called by `/pre_event` Telegram command. Outputs HTML to `workspace/scenarios/`.



### live_preevent_dashboard.py

Live pre-event analysis dashboard with statistical envelope overlay. Supports both **NFP** and **CPI** events via `--event {NFP,CPI}` CLI argument. Based on `preevent_boxplot_dashboard.py` but with **fixed time window** (3pm T-1 → 8:30am event day) and **dynamic statistical envelope** showing mean, median, and percentile lines (P5, P25, P75, P95) that evolve across the time axis. Features: price paths normalized to window start, cross-sectional statistics calculated at each 5-min time bucket, scrollable/zoomable within fixed window bounds, envelope legend with color-coded stat lines (white=mean, cyan=median, gold=IQR, red=outer percentiles), **ngrok tunnel support** via `start_live_server_with_tunnel()` for public URL sharing, `stop_live_server_tunnel()` for cleanup. CLI: `python scripts/live_preevent_dashboard.py --event NFP --fwd_be 6.5 [--serve]` or `--event CPI --fwd_be 8.0`. Called by `/live_pre_event` Telegram command. Outputs HTML to `workspace/scenarios/live_preevent_{event}_dashboard_{timestamp}.html`.



### event_data_loader.py

Centralized data loading for pre-event and post-event analysis dashboards. Supports **NFP**, **CPI**, and **PPI** events with shared interface. Data sources: economic_events.db (actual, consensus), Z:/Meini/NFP.xlsx and Z:/Meini/CPI.xlsx (cons_std and FWD B/E); PPI uses DB-only data with default cons_std=0.1 and fwd_be=8.0 bps. Key functions: `load_std_from_excel(event_type)`, `load_fwd_from_excel(event_type)`, `load_events_from_db(event_type)`, `get_event_data_combined(event_type)`, `get_event_config(event_type)`, `get_supported_events()`. Configuration in `EVENT_CONFIG` dict with db_event_name, display_name, release_time, unit, and optional post_event_end_time ("17:00" for PPI). PPI maps to DB event name "Core PPI MoM". Backwards compatibility via `get_nfp_data_combined()` and `load_nfp_std_from_excel()` wrappers.



### live_postevent_dashboard.py

Live post-event analysis dashboard with **surprise percentile bucketing**. Extended time window (3pm T-1 → 3pm T, 24 hours total) covering both pre-event and post-event sessions. User provides actual, consensus, cons_std, and fwd_be; calculates standardized surprise and percentile rank. Features: box plots by surprise percentile bucket, price paths normalized to window start, statistical envelope overlay, live data server polling snapshot DB (`X:\uikitxv2\data\snapshots\snapshots_YYYY-MM-DD.db`) for real-time TY price, ngrok tunnel support via `start_live_server_with_tunnel()`. CLI: `python scripts/live_postevent_dashboard.py --actual 50 --consensus 70 --cons_std 26.1 --fwd_be 6.83 [--serve]`. Outputs HTML to `workspace/scenarios/`.



### us_holiday_candlestick_dashboard.py

Standalone HTML dashboard generator for the requested US holiday set (New Year, MLK, Washington's Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas) from a configurable year range (default `2022` onward). Computes holiday dates (including observed-date rules and Good Friday from Easter), prefetches TY OHLCV from PostgreSQL for each holiday window, aggregates to 5-minute candles, and embeds all data in one HTML. UI renders a clickable holiday table; selecting a row updates a Plotly candlestick chart for `+/-N` days around the observed holiday date (default `N=3`) with observed/actual date markers.



## Dashboard Tabs (`scripts/dashboard_tabs/`)



### postevent_tab.py

Post-event tab module for the unified dashboard. Generates HTML and JavaScript for post-event analysis tab. Features: **surprise percentile bucket boxplot** with current bucket highlighted (green) and small sample warnings (⚠️ for n<3), **normalized price paths** with zoom/pan re-normalization using Plotly relayout handlers, **optional bucket filtering** to show only paths in user's percentile bucket, **window stats** (mean/median) that update on zoom/pan, **click-to-candlestick** opening modal with ±2 days and economic event annotations, and **Distribution Explorer** with fine-grained time presets (t-24h to t+8.5h), histogram+KDE plot, stats panel (N, mean, std, median, P5/P95, min/max), and **0 bps percentile rank** highlighted with vertical line.



### adhoc_tab.py

Ad-hoc tab module for the unified dashboard. Generates HTML and JavaScript for event-agnostic date selection and session analysis. Features: date picker (individual dates + ranges), session price paths with dynamic re-normalization on zoom/pan, distribution plots (returns, absolute returns, volatility) with Gaussian KDE and DistroDashboard-style stats boxes, click-to-candlestick modal with event annotations.



### weekmatch_tab.py

Week Match tab module for the unified dashboard. Generates HTML and JavaScript for finding historical weeks matching an uploaded event calendar. Features: Excel calendar upload, tier-based event matching, historical week candidate display, price path comparison across matched weeks, click-to-candlestick modal.



### impulse_tab.py

Pre-Event Impulse tab module for the unified dashboard. Provides ECOS Excel upload / default-file load, setup metrics for the current NFP survey (prior, median, expected change, dispersion, prior percentile, rank/recency-weighted consensus), selectable pre-event window start (`T-4` to `T-1`, default `T-2` at 15:00 ET), a normalized TY drift chart comparing all regime events vs setup-matched events, and a scatter of expected change (`consensus - prior`) vs realized pre-event drift. Also includes a What-if Actuals table: editable label/actual rows are mapped to z-score, empirical surprise percentile, and surprise-bucket expected/max TY moves for pre-event, +2h, and EOD windows. Calls `/api/impulse/ecos`, `/api/impulse/history`, and `/api/impulse/scenarios` in `scripts/live_unified_dashboard.py`.



### polymarket_scenarios_tab.py

Polymarket scenarios tab for the unified dashboard. In NFP mode, renders NFP+UER scenario tables with surprise-percentile and pre/post B/E analytics. In CPI mode, renders two separate headline tables: CPI MoM (analytics table) and CPI YoY (odds-only table). Includes DB-backed odds, expected probability-weighted rows for analytics tables, histogram drilldown for analytics rows, and projected-price ladder aggregation from analytics rows.



### postevent_boxplot_dashboard.py

Post-event B/E analysis dashboard. Generates interactive HTML dashboard for analyzing post-event B/E multiplier across **surprise percentile buckets** with price paths from 8:30am to 3pm on event day. User inputs actual value, consensus, and cons_std; calculates standardized surprise `(actual - consensus) / cons_std` and percentile rank. Features: box plots by surprise percentile bucket, pre-event B/E display, other-events-on-day detection, adjustable end time slider (8:30am → 3pm), **Fundamental Macro Analysis section** with Labour/Economy/Inflation indicators scored 1-5 using z-scores (level, trend via regression slope, curvature). CLI: `python scripts/postevent_boxplot_dashboard.py --actual 256 --consensus 165 --cons-std 25`. Called by `/post_event` Telegram command. Outputs HTML to `workspace/scenarios/`.



### january_auction_weeks.html

Standalone HTML dashboard comparing January auction weeks vs other months. Includes selectable time windows, statistics panels, and Plotly charts. Histogram controls now accept flexible hour inputs and render distro-style histograms with a normalized density curve overlay.



### supply_weeks_dashboard.py

Standalone generator for the 2Y/5Y/7Y supply-weeks dashboard. Loads processed auction CSVs from `auction_data/`, selects the largest same-tenor auction within each ISO week to avoid FRN/TIPS duplicates while preserving older smaller standard auctions, computes January and month-end flags (`7Y` auction in the last 7 calendar days of the month), pulls TY 1-minute OHLC from PostgreSQL, and emits mirrored HTML outputs at `scripts/supply_weeks_dashboard.html` and `dashboards/supply_weeks_dashboard.html`. The dashboard includes filter-aware paths, distributions, 5-minute candlestick drilldown, and independent display-only toggles for January and Month-End distribution annotations. Those two HTML dumps stay as-is; the live slim+flow UI is `scripts/supply_weeks_live/`.

### supply_weeks_live/

Isolated read-only Flask viewer on port `5065`. Reuses `build_auction_weeks` and postgres TY 1m for the 1D return distro; folds 1m aggress flow from `flow_history.sqlite` (plain sqlite + `query_only=ON`) onto `pre_2y` / `2y_to_5y` / `5y_to_7y` / `post_7y`. UI is four 2D net-vs-bps heatmaps, OLS cards, the auction-day board, and return distributions for the 2Y day and the 5Y auction day (08:00–13:00, 10:00–13:00, 13:00–17:00 on each auction date). The week clock and the auction-timeline chart are not on the page. `compare.py` folds 2Y/5Y/7Y `[t−3h, t)` and `[t, 17:00 ET)` vs same-weekday non-2/5/7-auction days (2022+). Cards show p25 / median / p75. Optional non-supply drops: Tier-1 (NFP/CPI/FOMC/PCE/GDP) and last-3-session month-end. The week month-end filter applies to supply only. Cache closed weeks plus the compare scan; `/api/live` refreshes the current supply week and today’s unfinished compare slice when `MAX(ts_utc)` moves. Typical net is the median of week window nets. Bootstrap omits `flow_path`. Label `DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL`. Does not import live V1 `app.py`. Tests: `tests/test_supply_weeks_live.py`.



### ten_thirty_auction_weeks.py

Standalone generator for a 10Y/30Y auction weeks dashboard keyed only off `workspace/economic_events.db` dates (`10-Year Note Auction`, `30-Year Bond Auction`). Pulls TY 1-minute OHLC from PostgreSQL, builds matched weekly cycles with a consecutive-day flag, and emits `scripts/ten_thirty_auction_weeks_dashboard.html` with normalized paths, 10Y/30Y windowed distributions, and 5-minute candlestick drilldown.



### geo_analysis.py

Geopolitical events analysis dashboard generator. Creates interactive HTML dashboard showing TY price paths for **21 major geopolitical events** spanning 2022-2026 (Ukraine/Russia, Israel-Iran, Houthis/Red Sea, Hamas, Venezuela). Features:

- **US/Eastern time basis** with `zoneinfo` conversion from source timezones (Moscow, Kiev, Jerusalem, Tehran, etc.)

- **Multi-day event expansion**: Events spanning multiple days (e.g., "Night 13-14 Apr") split into one entry per calendar day

- **Globex date resolution**: Weekend/holiday events automatically shifted to nearest trading day with explicit UI labeling

- **Normalized price paths** (bps change from calendar day open) with multi-colored traces

- **Per-event stats**: Day return, volatility, max up/down in 5m/1h/full day windows displayed on cards and modal

- **Unusual day detection**: Events with P95+ max moves flagged with "UNUSUAL" badge

- **Clickable event cards** that open modal with candlestick chart (5-min resampled), event window shading (if time known), and economic events listed in a box below

- **Distribution plots** for returns and volatility with KDE overlay and stats boxes (Mean, P5-P95, Min/Max)

- **Curated summaries** per event (policy dashboard style) rendered in modal for higher-quality context

- **Perplexity fade lookup**: optional web narrative + sources; **fade is computed from market data**, with an estimated-window stats block when narrative timing is available

- **Macro context**: spike/fade computed over ±2 days of 1m data and modal lists nearby US macro events

Uses PostgreSQL ohlcv_1m for price data and economic_events.db for event annotations. CLI: `python scripts/geo_analysis.py [--output custom.html] [--no-perplexity] [--perplexity-cache PATH] [--perplexity-refresh]`. Outputs to `scripts/geo_analysis.html`.



### policy_events_analysis.py

Policy & macro events analysis dashboard generator. Based on `geo_analysis.py` but covers **5 categories**: Yen rate check, Iran, Canada retaliatory tariff, Democrats voting for shutdown, Fed. Features:

- **Category filter pills** (All / per-category filtering)

- **Event summaries**: Short snippet on cards (~180 chars), full multi-paragraph narrative in modal

- **Shutdown episodes fully populated** with date ranges and market context summaries (Jan 2018 DACA, Dec 2018–Jan 2019 wall, Oct 2025 ACA subsidies)

- **Placeholder events** for Yen/Iran/Canada/Fed (TBD dates) ready to be filled in

- Same TY price paths, candlestick modal, distribution plots as geo dashboard

Uses PostgreSQL ohlcv_1m for price data. CLI: `python scripts/policy_events_analysis.py [--output custom.html]`. Outputs to `scripts/policy_events_analysis.html`.



### yearly_overlay_timeseries.py

Interactive Plotly overlay of full-year price series from PostgreSQL `ohlcv_1m`, aligned to a shared calendar axis so each year is stacked on the same timeline. Supports resampling (default 15m), normalization to start-of-year change (default on), units in price/ticks/bps, and in-HTML start/end month selectors that re-normalize to the window start. CLI: `python scripts/yearly_overlay_timeseries.py --symbol-pattern TY% --start-year 2019 --end-year 2025 --units ticks`. Outputs HTML to `workspace/analysis/`.



### monthend_friday_candlestick_dashboard.py

Standalone HTML dashboard with two tabs: **Month Ends** (last business day of each month from 2022 onward) and **Recent Fridays** (past 2 months). Click any row to view TY 5-minute candlestick chart for ±N days around the date. Dark theme matching overnight session dashboard style. Tags month-end Fridays and Friday month-ends with badges. Uses PostgreSQL OHLCV with 5-min candle aggregation. CLI: `--start-year`, `--months-back`, `--days`, `--output`. Outputs to `workspace/scenarios/`.



### month_end_friday_us_open_distribution.py

Generates distro-style histograms of month-end returns during US open hours (08:00–17:00 ET) from PostgreSQL `ohlcv_1m`, including an all-month-end plot with Friday markers plus a Friday-only plot. Computes mean/median/extreme percentiles, expected rally/selloff, and 0th-percentile bps move using `rules/treasury_price_conversion.py`, outputs PNG + JSON/CSV to `workspace/analysis/`.



### treasury_refunding_distribution.py

Generates a Treasury Refunding Announcement return distribution over a configurable window relative to the event timestamp, using `rules/treasury_price_conversion.py` conversions via shared helpers. Outputs window-tagged PNG + JSON/CSV to `workspace/analysis/` with expected rally/selloff, percentiles, 0 bps percentile rank, and last-two-event markers.



### ism_manufacturing_pmi_pre_post_distribution.py

Builds two distribution plots around **ISM Manufacturing PMI** release times (t-2h→t and t→t+2h) using 1-minute OHLCV from PostgreSQL and events from `workspace/economic_events.db`. Uses `rules/treasury_price_conversion.py` for decimal→ticks→bps conversions and produces custom stats boxes with expected rally/selloff and 0 bps percentile rank. Outputs PNG + JSON + per-event CSVs to `workspace/analysis/`.



### ism_services_pmi_postevent_dashboard.py

Standalone HTML post-event dashboard for **ISM Services PMI**. User inputs actual/consensus to compute surprise percentile and bucket, then compares all-events vs bucket-matched return distributions at t+1h/t+2h/t→EOD (5:00pm ET same day) with expected rally/selloff, percentiles, and 0 bps percentile stats (using `rules/treasury_price_conversion.py`). Outputs a self-contained HTML to `workspace/scenarios/` and a summary JSON to `workspace/analysis/ism_services_pmi_postevent/`.



### jolts_job_openings_postevent_dashboard.py

Standalone HTML post-event dashboard for **JOLTS Job Openings**. User inputs actual/consensus to compute surprise percentile and bucket, then compares all-events vs bucket-matched return distributions at t+10m/t+30m/t+1h/t+2h with expected rally/selloff, percentiles, and 0 bps percentile stats using `rules/treasury_price_conversion.py`. Outputs a self-contained HTML to `workspace/scenarios/` and a summary JSON to `workspace/analysis/jolts_job_openings_postevent/`.



### news_fade_dashboard.py

Standalone HTML event-study dashboard for X macro stream headlines/tweets stored in `tweet_labels` (`~/.market_data/x_macro_stream.db` by default). Bulk-loads TY 1-minute OHLCV from PostgreSQL, aligns each message timestamp to the nearest market reaction window, optionally filters to the Iran/Israel/Lebanon topic with a broad-vs-war-only UI toggle, and quantifies fade/retracement at configurable horizons with summary cards, Plotly distributions, account breakdowns, scatter, and click-through normalized price paths. `--move-threshold-bps 0` includes all events with market data; positive values restrict to larger initial reactions. `--max-paths` caps embedded per-minute paths while keeping scalar metrics for all events. CLI: `--x-macro-db`, `--symbol`, `--start-date`, `--end-date`, `--accounts`, `--topic`, `--keywords`, `--strict-terms`, `--label`, `--dedupe-seconds`, `--path-pre-mins`, `--initial-mins`, `--lag-mins`, `--move-threshold-bps`, `--fade-mins`, `--max-paths`, `--out`. Outputs to `workspace/scenarios/news_fade_dashboard_{topic}_{timestamp}.html` by default.



### jolts_prepost_event_dashboard.py

Standalone HTML pre/post-event dashboard for **JOLTS Job Openings**. It combines pre-event consensus context (prior/MA3/MA6/MA12), manual bank-estimate dispersion consolidation, historical surprise bias, a copy-pasteable colleague-style automated message with quantified TY move stats, driver analysis ranking surprise z-score vs pre-event setup, and post-event surprise/bucket distributions at t+10m/t+30m/t+1h/t+2h. Outputs a self-contained HTML to `workspace/scenarios/` and summary JSON to `workspace/analysis/jolts_prepost_event/`.



### non_farm_payrolls_postevent_dashboard.py

Standalone HTML post-event dashboard for **Non Farm Payrolls** sourced from `bbg_data` (`workspace/economic_events.db`). Uses standardized surprise (`(actual-consensus)/consensus_std_dev`) and compares all-events vs bucket-matched return distributions at t+1h/t+2h/t→EOD (5:00pm ET same day), with stats-box conventions and last-two-event markers. Supports CLI-prefilled scenario inputs (`--actual`, `--consensus`, `--consensus-std-dev`) and outputs self-contained HTML + summary JSON.



### cpi_twofactor_pca_dashboard.py

Standalone HTML prototype for CPI two-factor bucketing using PCA in pre-event and post-event modes. Loads CPI components from Bloomberg workbook (`bbg_event_fields.xlsx`), joins CPI timestamps from `workspace/economic_events.db`, and computes TY pre/post window **max-extent** bps moves from PostgreSQL OHLCV. Pre mode supports (a) workbook uncertainty features (`mom_cons_std`, `core_mom_cons_std`) with a consensus-level fallback when std-dev columns are flat, and (b) an alternate **market-derived uncertainty** PCA using trailing realized range/vol (bps) computed strictly before the `3:00pm T-1` pre-window start.



Regression/bucketing supports both **linear (signed)** and **abs(|F|) extent-friendly** factor→bps mappings, with UI toggle to compare R²/RMSE and see bucket assignments update. Bucket rows and boxplots are clickable: drilldown renders bucket-member normalized paths on a shared aligned timestamp axis and a distro-style histogram+KDE panel with conditional stats and bootstrap 95% mean CI (with small-sample warnings). Post-event bucket/drilldown analytics now use `8:30am -> 5:00pm` ET.



### nfp_twofactor_pca_dashboard.py

Standalone HTML prototype for NFP two-factor bucketing using PCA in pre-event and post-event modes, built as a sibling to the CPI prototype. Loads NFP and UER component blocks from the Bloomberg workbook (`bbg_event_fields.xlsx`, `NFP & Subcomponents` sheet), joins NFP event timestamps from `workspace/economic_events.db`, and computes TY pre/post window **max-extent** bps moves from PostgreSQL OHLCV.



Pre mode uses workbook uncertainty features (`nfp_cons_std`, `uer_cons_std`) with consensus fallback when needed; post mode uses direct standardized surprises from user-facing fields (`actual`, `consensus`, `std dev`) for NFP and UER: `(actual-consensus)/std_dev`. Reuses CPI prototype UI/panels (bucket rows, clickable boxplots, drilldown paths/distro/candles, regression score toggle) with NFP/UER labels and outputs standalone HTML files in `workspace/scenarios/`.



### ppi_twofactor_pca_dashboard.py

Standalone HTML prototype for PPI two-factor bucketing using PCA in pre-event and post-event modes, reusing the CPI/NFP two-factor engine. Loads PPI, Core PPI, and Supercore PPI component blocks from the Bloomberg workbook (`bbg_event_fields.xlsx`, `PPI` sheet), joins PPI event timestamps from `workspace/economic_events.db` (`Core PPI MoM`), and computes TY pre/post window **max-extent** bps moves from PostgreSQL OHLCV.



Pre mode uses workbook uncertainty features (`ppi_cons_std`, `core_ppi_cons_std`) with consensus fallback when needed; post mode blends Core and Supercore into the second post signal (Core standardized surprise + Supercore historical-z proxy, because Supercore has no consensus/std fields in the workbook). Reuses CPI prototype UI/panels (bucket rows, clickable boxplots, drilldown paths/distro/candles, regression score toggle) with PPI labels and outputs standalone HTML files in `workspace/scenarios/`.



### totalaggress_vs_price_chart.py

Generates a dual-axis **TotalAggress vs Price/Yield** chart (HTML or PNG) from Databento trade data and OHLCV. TotalAggress = cumulative signed $DV01 flow, where each trade is classified as buy-aggressor (+1, side='B') or sell-aggressor (-1, side='A') using the Databento `side` field, then scaled by a configurable DV01 per contract (default $125/bp, calibrated to match reference chart). Filters spread trades (price < 80 or > 150). Uses OHLCV index as the authoritative time grid (trading-hours only, no weekend flat lines). Axis ranges are deliberately aligned to define an implicit fair-value mapping: `P_implied = 111.925 + Aggress × 4.375e-8` (1M $DV01 ≈ 1.4 ticks). Residual (actual − implied) measures passive absorption signal. Auto-detects `workspace/wenn/ohlcv.zip` for price, and fetches trades from Databento API with parquet caching + auto-retry on end-date boundaries. CLI: `python scripts/totalaggress_vs_price_chart.py [--png] [--zip PATH] [--fetch] [--ohlcv-zip PATH] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--dv01 125] [--bucket 1min] [--reset none|daily|globex] [--plot-start ISO] [--plot-end ISO]`.



### aggression_v2_feature_audit.py

Research-only Phase 0 audit script for a future aggression dashboard V2. Reads cached `trades_v2_*` parquet files, filters each symbol/day to the front-month outright using the V1 front-month selector, builds a 1-minute feature matrix for `FV/TU/TY/US` with daily-reset aggression, multi-horizon signed flow, buy/sell ratios, intensity, size profile, streak, realized vol, momentum, cross-tenor flow, and time-of-day features, then writes `feature_matrix.csv`, `feature_correlation.csv`, and `feature_audit_summary.json` under `workspace/analysis/aggression_v2/`. This script is audit/research-only and is intentionally not wired into the live monitor.



### aggression_v2_tbbo_audit.py

Research-only Phase 0.5 audit script for TBBO-backed V2 features. Pulls historical `schema=tbbo` data from AccessAPI (`source=database`, Arrow format) for `TY/FV/TU/US`, builds a 1-minute feature matrix with trade-flow features plus quote-at-trade features such as spread, bid/ask size imbalance, microprice deviation, displayed depth, trade-size-vs-depth, and depth-adjusted signed flow, and writes `tbbo_feature_matrix.csv`, `tbbo_feature_correlation.csv`, and `tbbo_feature_audit_summary.json` under `workspace/analysis/aggression_v2_tbbo/`. This script is audit/research-only and is not wired into V1.



### aggression_monthend_flow.py

Standalone flow-only month-end stats script for the TBBO aggression feature matrix. Reads `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv`, tags the trailing N business days of complete calendar months as month-end, computes daily net DV01-weighted signed flow, last-observed close-window flow, share-into-close, and Normal vs Month-end summary stats, then writes `monthend_flow_daily.csv` under `workspace/analysis/aggression_v2_tbbo/monthend/`. Optional `--html` emits an inlined-Plotly timing chart; the script does not import or modify the live V2/V3 monitors.


### aggression_v2_model_research.py

Research-only walk-forward model comparison for TBBO-backed V2 features. Reads `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv`, filters out low-quality days, targets within-day price deviation (`price - day_open_price`), and evaluates OLS baseline, Ridge, Lasso, and Random Forest benchmark in a date-wise walk-forward split. Writes OOS predictions, metrics, coefficient/importances, and train-fold diagnostics under `workspace/analysis/aggression_v2_tbbo_models/`. This is not wired into any dashboard and is gated for user review before UI/live work.



### aggression_v2_conditional_signal.py

Research-only conditional signal tester for TBBO-backed V2 Ridge residuals. Reads walk-forward predictions plus the TBBO feature matrix, constructs condition buckets (large residual thresholds, absorption, flow deceleration, depth regimes, spread regimes), and tests whether `sign(-residual)` predicts 5/15/30-minute future price changes. Writes `conditional_signal_tests.csv`, `conditional_signal_dataset.csv`, and `conditional_signal_summary.json` under `workspace/analysis/aggression_v2_tbbo_models/`. This is a signal-validation artifact only and is not wired into the live dashboard.



### aggression_v2_holdout_signal.py

Research-only chronological holdout validator for the TBBO-backed Ridge residual signal. Reads the V2 walk-forward predictions and feature matrix, splits OOS dates into an early threshold-selection sample and later holdout sample, then retests fixed residual-z thresholds (`1.0`, `1.5`, `2.0`) at 15/30-minute horizons with optional flow-deceleration condition. Writes `holdout_signal_tests.csv`, `holdout_signal_dataset.csv`, and `holdout_signal_summary.json` under `workspace/analysis/aggression_v2_tbbo_models/`. This is a stricter validation artifact and is not wired into any dashboard.

### aggression_v2_depth_research.py

Research-only Phase 1/1.5 MBP-10 depth study for the proposed V3 market-depth dashboard. Reads ZN MBP-10 daily parquet from `D:\FRDBs\FutMBP\ZN_outrights_mbp10`, prefers prebuilt `D:\FRDBs\FutMBP\research_features\*_ZNM6_features.parquet` definitions where available, aggregates to 1-minute depth/OFI/cancel/sweep features, and tests (1) depth -> future realized vol, (2) depth-conditioned V2 Ridge dislocation outcomes, (3) fair-value lift from adding depth features, and extra diagnostics for composite gamma-bid risk, liquidity refill, imbalance persistence, sweeps/absorption, OFI-vs-trade divergence, and top-vs-deep concentration. Writes CSV artifacts under `workspace/analysis/aggression_v2_depth/` (including `phase15/*.csv`) and `docs/aggression_v2_depth_research.md`; current result supports a V3 display-only depth/risk panel but does not support adding depth to the fair-value model yet.



## Aggression Databento Live Backup

### `scripts/databento_live_backup.py`

Shared on-demand `DatabentoLiveBackup` client for aggression V1 and V2.2. It resolves the Databento key without importing AccessAPI, subscribes directly to `GLBX.MDP3` TBBO for `ZN/ZB/ZT/ZF.v.0`, handles live symbology mappings, preserves nanosecond timestamps, normalizes fixed-point trade/BBO records into the AccessAPI contract, advances its bounded replay cursor on reconnect, and exposes thread-safe source status. `start()`/`stop()` are serialized; a restart waits up to `START_WAIT_STOPPING_SEC` (15s) for a stopping worker before returning the still-stopping error. `TbboEventDeduplicator` combines bounded exact instrument/time/sequence/payload identities with per-instrument Databento-receive-time replay floors, preserving multi-trade packets while rejecting old replays even after cache eviction; no market data is written.

### `scripts/test_databento_live_backup.py`

Standalone non-dashboard smoke harness. It deterministically simulates AccessAPI failure and sustained recovery for V1/V2.2 with fake backup records, and optionally opens a short real Databento Live session (`--live-seconds`, default 30) to validate all four Treasury symbols and the normalized AccessAPI-compatible field set.

### `tests/test_aggression_feed_failover.py`

Collectible integrity tests for exact sequence dedupe, equal-nanosecond multi-event retention, backup reconnect replay, recovery-overlap handoff, shutdown activation guards, replay-cursor advancement, waiting out a stopping Databento worker, and V1 not advertising backup until `start()` succeeds. Uses fake transports only and does not open live sessions.

## Aggression Dashboard V2 Research (`scripts/aggression_dashboard_v2/`)



### app.py

Research/V2 signal Flask app on port `5052` for reviewing TBBO-backed V2 model artifacts and monitoring live prior-blended causal residual-z dislocations. It reads precomputed research outputs from `workspace/analysis/aggression_v2_tbbo_models/`, serves `/api/config` and `/api/research`, starts a V2 TBBO live buffer on demand, exposes `/api/live_signal` and `/api/signal_status`, appends a minute-level live signal audit log to `workspace/analysis/aggression_v2_live/signal_log_YYYYMMDD.csv`, and never touches the V1 live dashboard.



### templates/index.html, static/dashboard.js, static/dashboard.css

Browser UI for V2 research and live signal review: model/date selectors, actual-vs-fair-value chart, residual z-score chart with thresholds, OOS model comparison, feature driver chart, holdout/conditional rule tables, explainer text, plus a live signal banner for `WATCH` (`|z|>=1.5`) and `DISLOCATION` (`|z|>=2.0`) states, live fair-value/residual-z charts, issued/resolved/expired alert stats, a Hold Expectations card, outcomes-table close/MAE/share columns, and a plain-English stats glossary. Price/fair-value chart axes and hover text use Treasury 32nds (`XX-NN`, `XX-NN+`), and the live banner includes a decimal + 32nds current-price readout. Live alert success is defined as `|z|<1.0` within 30 minutes.



### README.md

Short run instructions and scope statement for the V2 research dashboard. It emphasizes that the app is not a live trading monitor and only reads existing research artifacts.



### live_buffer.py, features.py, model_state.py, alerts.py

Live V2 signal modules. `live_buffer.py` streams AccessAPI TBBO records and keeps trade plus BBO price/size in memory, seeding from `/live/tbbo/recent` for startup warmup; `features.py` converts live TBBO rows into the research feature set; `model_state.py` loads/fits the Ridge model once per day from the TBBO research matrix and computes prior-blended causal residual z-scores using historical residual mean/std plus today's expanding stats; `alerts.py` manages `WATCH`/`DISLOCATION` episodes, resolves them when `|z|<1.0` within 30 minutes, tracks directional/adverse 15m/30m outcome fields plus hold-expectation metrics (`mae_to_resolve_ticks`, `close_price`, `close_fair_value`, `price_share_of_reversion`, `z_method`), appends finalized rows to `workspace/analysis/aggression_v2_live/alert_log.csv`, and persists live state in `alert_episodes.json`.



## Aggression Dashboard V3 Trade-to-trade (`scripts/aggression_dashboard_v3/`)

### app.py
Isolated Flask monitor on port `5053`. Implements `docs/aggression_v1_monitor.tex`: trade-to-trade clock, default whole-curve chips (FV+TU+TY+US vs TY price), 10-session last-3-hour TOD change-on-change `beta_ref` (request path peeks cache; a background kick computes once per chip set per process, then holds it until restart, and re-triggers a rebuild only after a fresh beta), a per-rebuild live-first session tape (`_live_display_trades`: live prints, then Databento fills holes; no separate hist-only store), a backfill whose `covered_end` is the earliest symbol's last fetched print, and the six-state banner. Live mode is one publisher plus `GET /api/live/stream` (snapshot, then per-trade deltas). Live `POST /api/aggression` only reads that shared picture. Historical mode still posts. Empty-tape WARMING is not published; first paint can use the parquet 15-minute cache. Flask is threaded. Writes `workspace/analysis/aggression_v3_live/` (keeps old logs). Does not edit V1/V1.3/V2. Manual restart required. Label `PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE`.

### core.py
Databento fetch/cache (`trades_v3` parquet; trade `ts` is Databento `ts_event`, exchange time, so it pairs with AccessAPI live prints) plus `build_print_frame`: sign, held last-trade price, session `TotalAggress`, BuySell EMA(10). Sign is looked up once per distinct side value and `session_id` via `session_labels` (one `strftime` per distinct date). `load_cached_trades_window` reads parquet only. CME `tau` and point values live here.

### live_publisher.py
One default live view. Each `action == "T"` print is a clock tick: apply, recompute the 15-minute six-state, broadcast the same SSE bytes. Quotes are freshness only. Rebuilds run from the 18:00 ET session open, so TotalAggress and the Panel 4 path are session-to-date and lookback only clips the charts; the tail applied during a rebuild is re-accumulated onto the rebuilt sum. Hole-fill (beyond `PREFIX_TOLERANCE`) / `beta_ref` changes are `history` / `beta` revisions. Past strip colors stay put across ordinary trades. Rebuilds run only on the rebuild thread and coalesce (`REBUILD_MIN_INTERVAL_SEC` 60 s, 5 s while warming); the trade thread only requests one. `_prepare_picture` builds the display tape arrays, seen-keys and the JSON off the lock; `_store_picture` swaps under the lock and queues the revision as the snapshot's bytes plus one `trade` replay of prints that arrived mid-rebuild. Those carried prints are re-accumulated onto the rebuilt sum (they were applied to the old tape, which after warming starts at 0); if that moved them beyond `PREFIX_TOLERANCE`, the replay is corrected and the revision is at least `history`. Trade messages are queued under the lock; catch-up applies in `CATCH_UP_BATCH` (2000) batches. The live snapshot is `build_compact_dashboard_payload`. On the wire (`_wire_point`) a print carries the hover banner (hover fields only) only when its state changed or 5 s passed; each message carries the latest banner as `flow_dominance`, plus `server_ts` and `stream_lag_sec` (1,887 → ~176 B per print). A subscriber whose queue overflows is resynced with the picture and a replay (≤ `REPLAY_MAX` 20,000 prints); the stream opens with `retry: 5000`.

### panels.py (V3)
Plotly panel payloads. `build_dashboard_payload` is the row format (historical POST). `build_compact_dashboard_payload` is the live snapshot: panels 1/3/4 share one columnar `tape` (`TAPE_FIELDS`), history older than `COMPACT_TAIL` (5 min) is min/max decimated to `COMPACT_BUCKETS` (800; first/last/min/max per field per bucket, all real prints), panels 2/5 carry their own `compact` block, times are delta-encoded ET wall-clock µs (`x0` + `dt`). `slim_banner_series` keeps the hover fields at ≥5 s spacing. `expand_payload`/`block_rows` are the Python twins of dashboard.js `expandCompactPayload`/`compactRows`. 9/24 rehearsal: 29.2 MB → 0.68 MB.

### static/dashboard.js
Live viewer. `renderPayload` first runs `expandCompactPayload` (compact live snapshots back to row objects, appended tip rows kept after them). Trade deltas are queued and charts redraw at most once per second (one `Plotly.react` per panel, `uirevision` keeps zoom); banner/freshness text updates on every message. Drawn points are min/max-decimated per pixel bucket (full detail when zoomed). Panel 3 buy/sell are WebGL per-print spikes; their values show on the TotalAggress hover.

### print_reconcile.py
Live-then-hist session upsert. One-to-one match on size, normalized side, tick-rounded price, `|Δt|≤1ms`; closest unused row, ties to the lowest stored row. `_matched_incoming` is vectorized (bucket/time-rank searchsorted; only rows competing for the same stored row run a loop) and equals `_matched_incoming_reference` (the time-sorted bisect loop, also the fallback for NaT/non-numeric keys). Keep stored. Hist may fill left-of-store and interior holes; interior unmatched is skipped if match rate < 0.5; hist is never inserted after the live tail.

### golden_fixture.py / bench_reconcile.py
Offline tools. `golden_fixture.py` records a reconciliation fixture (Databento cache = stored side, AccessAPI `/historical/trades` = live side) and, with `--expected-only`, the reference matcher's outputs on 2-hour US-open windows. `bench_reconcile.py` times the live-first session merge (and optionally the bisect reference matcher) on that fixture and prints merged row counts. Fixture for 9/23: `C:\dev\mdp_golden\v3\20260923` (outside the repo).

### banner.py / beta_ref.py / panels.py / live_buffer.py
Six-state READY/WARMING/UNAVAILABLE banner (beta / 15m / gap / session — not a foreign-request config flip) plus hover `flow_dominance_series` (≤4000 as-of snapshots) and a compact READY-state bar strip above Panel 1 (`panels.p1_states`; WARMING/UNAVAILABLE are gaps); last-3-hour TOD 10-session `ΔP` on `ΔA` mean cached as multi-entry `beta_ref.json` (`last_3h_tod_v1`, last-good reused across 5-minute TOD buckets); print-clock Plotly panels 1–5; Panel 2 draws a 30s time-weighted EWM of the raw print ratio, thinned on epoch-aligned bins (chart-only; spec EMA(10) is not plotted). `print_reconcile.py` matches live vs hist within 1ms and keeps the stored row. AccessAPI + Databento failover copied from V1.3. Live buffer invokes `on_trade` / `on_quote` after the lock is released.

## Flow-effort research helper (`scripts/aggression_flow_effort.py`)

Former V3 `flow_effort.py`. Offline research only (`annotate_flow_effort`, `evaluate_regime`). Not used by live 5053.

## Aggression Effort Range Research (`scripts/aggression_effort_range_research.py`)

Offline chronological-holdout script for the V3 flow-effort layer. It loads the existing TBBO feature matrix and Ridge walk-forward predictions, reuses `scripts.aggression_flow_effort.annotate_flow_effort()` to avoid live/backtest drift, scores exhaustion/breakout/false-break/baseline rules across parameter sweeps, and writes CSV/JSON artifacts to `workspace/analysis/aggression_effort_range/`.

## Aggression V3 Options Context Research (`scripts/aggression_v3_options_context_research.py`)

Read-only research script for testing whether the gamma tracker v3 DB adds useful options context to the aggression monitor. It opens `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db` with SQLite `mode=ro`, builds one-minute front-weighted `rr25/rr50/rr75`, skew/backbone metrics, and near-spot gamma aggregates, joins them to V3 signal/regime logs and TBBO history where available, and writes joined data/statistics/summary artifacts to `workspace/analysis/aggression_v3_options_context/`. It does not modify the gamma tracker dashboard/writer or any aggression dashboard.

## Aggression V2 Options Context Research (`scripts/aggression_v2_options_context_research.py`)

Read-only research script for testing gamma-tracker skew/gamma context against real V2 WATCH/DISLOCATION episode outcomes and minute-level V2 residual-z logs. It reads V2 `alert_log.csv` and `signal_log_*.csv`, opens the gamma tracker DB read-only (or uses the existing options-context cache), calibrates the RR sign empirically, tests episode-open filters, residual-z fade filters, skew/fair-value directional agreement, and holdout compounds, and writes artifacts under `workspace/analysis/aggression_v2_options_context/`. It does not modify V2; the paired explained report is `docs/aggression_v2_options_experiments.md`.

## Aggression V2 Skew-Price Correlation Research (`scripts/aggression_v2_skew_price_corr_research.py`)

Read-only study of TY options skew changes (`rr25`, `rr50`, `rr75`, `skew_index`) versus TY price changes using existing V2/V3 1-minute joined CSVs, never `opt_futures_px` or the raw gamma DB. It computes within-day contemporaneous 1/5/15/30m Pearson/Spearman correlations and a -30..+30m lead/lag grid, with 10,000-draw date-clustered CIs and pooled BH-FDR. Outputs live under `workspace/analysis/aggression_v2_skew_price_corr/` and pair with `docs/aggression_v2_skew_price_corr_experiments.md`. 2026-07-10 result: RR50 30m Pearson `0.005`; no skew-leading-price or other correlation survives FDR.

## Aggression V2 Skew-Leg Event Research (`scripts/aggression_v2_skew_leg_events_research.py`)

Read-only asymmetric event study that opens the gamma tracker DB with `mode=ro`, reconstructs matched Call/Put IV legs at moneyness 25/50/75, verifies `RR = call_iv - put_iv`, and tests causal z>=1.5 single-leg shocks with 30m cooldown against TY +5/+15/+30/+60m moves. Outputs under `workspace/analysis/aggression_v2_skew_leg_events/` and pair with `docs/aggression_v2_skew_leg_events_experiments.md`. 2026-07-10: no holdout/FDR pass; put-IV-bid and especially call-IV-soften show provisional downside association at longer horizons, but holdout has only four skew-active days.

## Aggression V2 Skew/Vol Shock Path Research (`scripts/aggression_v2_vol_skew_shock_paths_research.py`)

Read-only path event study that combines matched put/call IV legs with ATM/backbone, RR50 and skew-index shocks, enters one minute after a causal 15m shock, separates price-quiet predictive candidates from price-led vol reactions, and compares TY direction, absolute move, range, realized-vol ratio and trend/chop paths against cross-date matched quiet-option controls from 1m through strict calendar 24h. Outputs under `workspace/analysis/aggression_v2_vol_skew_shock_paths/` and pair with `docs/aggression_v2_vol_skew_shock_paths_experiments.md`. 2026-07-13: no holdout/FDR pass; pooled price-quiet shocks show only a weak 5–120m range-expansion hint, with 6 holdout events across 3 dates.

## Aggression V2 Option Shock Persistence Research (`scripts/aggression_v2_option_shock_persistence_research.py`)

Read-only follow-up that reuses existing shock panel/events/outcomes to measure aligned option-metric retention from 1m to strict calendar 24h, 50% half-life/full-reversion time, and persistent-vs-reverted TY path differences. Outputs under `workspace/analysis/aggression_v2_option_shock_persistence/` and pair with `docs/aggression_v2_option_shock_persistence_experiments.md`. 2026-07-13: no holdout/FDR pass; pooled ATM shocks have ~17m half-life and ~29–36m full reversion, while persistence-to-TY range/RV hints are based on only 6 events across 3 dates.

## Aggression V2 Iterative Skew Research (`scripts/aggression_v2_skew_iterative_research.py`)

Read-only five-iteration hypothesis/test/learn engine over the existing matched-leg/options/TY path panel. It reserves latest active dates as untouched FINAL, sweeps finite taxonomy thresholds on research dates, then tests magnitude/range, persistence/regime, directional asymmetry and parsimonious context with clustered CIs/FDR, preserving all 1,360 validation candidates and a JSONL iteration journal. Outputs under `workspace/analysis/aggression_v2_skew_iterative/` and pair with `docs/aggression_v2_skew_iterative_experiments.md`. 2026-07-13: no candidate met n/date/FDR guards, so FINAL outcomes were deliberately not inspected.

## Aggression V2 Option Flow Research (`scripts/aggression_v2_option_flow_*.py`)

Staged read-only historical option-flow pipeline. `aggression_v2_option_flow_fetch.py` supports AccessAPI Arrow audit windows and local DuckDB predicate-pushdown over `D:\BigMarketDataParquet\OptionsMBP1_v2`, retaining only TY monthly/weekly trades in a resumable local cache. `features.py` builds contract/premium/native-Greek 1/5/15/30m flow, ATM/wing/expiry, large-print and sweep proxies; `research.py` runs chronological pilot experiments and gates FINAL/expansion. Outputs under `workspace/analysis/aggression_v2_option_flow/`, paired with `docs/aggression_v2_option_flow_experiments.md`. 2026-07-13: 20-date pilot ran; no directional edge and no candidate passed, though extreme/large/ATM flow showed provisional range-lift hints. Six-month expansion remains gated.

## Aggression V2 Non-Flow Context Research (`scripts/aggression_v2_nonflow_context_research.py`)

Phase-1-only read-only study that joins V2 alert outcomes with options/skew episode context, pre-alert realized-vol/range magnitude features, scheduled-event proximity, and optional depth overlap, then evaluates all modifiers on a common chronological holdout with date-clustered improvement CIs and pooled BH-FDR. Outputs under `workspace/analysis/aggression_v2_nonflow_context/` and pair with `docs/aggression_v2_nonflow_context_experiments.md`. 2026-07-10: magnitude state adds validated big-move (+12.4pp) and resolution (+12.6pp) context but not after-cost direction/PnL; skew-agrees-fade is provisional (n=23); gamma/event/depth were not evaluable. No dashboard was built.

## Move Exhaustion Research (`scripts/move_exhaustion/`)

Offline package for predicting Treasury futures move exhaustion after a directional shock. Reads V2 TBBO matrix + ridge walk-forward residuals only; writes exclusively under `workspace/analysis/aggression_v2_move_exhaustion/`. Never imports/writes/restarts live V1–V3 dashboards or streams. E00–E30 complete; holdout locked. Final report: `docs/aggression_v2_move_exhaustion_final_report.md`. CLI: `py -3 -m scripts.move_exhaustion.cli eXX`.


## V1 banner / V2.7 alert log (`scripts/aggression_v1_v27_banner_alerts/`)

Append-only SQLite of live V1 5050 banner **state changes** and V2.7 5063 ENTRY/EXIT events, with the latest V1 banner stamped on each alert. Writes only under `workspace/analysis/aggression_v1_v27_banner_alerts/`. Does not change banner or clock behavior. Docs: `docs/aggression_v1_v27_banner_alerts.md`. Tests: `tests/test_v1_v27_banner_alerts.py`.

## V2.7 × V1 zone score (`scripts/aggression_v27_v1_zone_score.py`)

Offline scorer for frozen V1-banner zones on the current-rule V2.7 tape. Full history is `replay_full_history.py` (B1_W10 OOS 2026-02-03..2026-06-05 + live week); week-only stamp remains `stamp_v1_windows.py`. Reads `v1_banner_windows_full.json` when present. Writes `zone_scores.csv`, `zone_summary.json`, and `zone_readout.md` in that same folder. Nomination: n_closed≥3, ticks>0, hit>50%; at most two cells. Does not import or restart V1/V2.7. Tests: `tests/test_v27_v1_zones.py`.

## Aggression V2.7 Local 10m Fair Value (`scripts/aggression_dashboard_v27/`)

Isolated manual-only copy of V2.2 on port 5063. Replaces session-from-open Ridge with the 2026-08-31 B1_W10 local nowcast (`price_{t-10} + Ridge(short pack)`), and uses enter `|z|≥1.5` plus a high-conviction session gate (`2 ≤ |session ticks| ≤ 6`, same sign as local residual, no same-sign re-entry after a finalized episode). Exit remains fade-side through 0.5 after 5m or flip at `|z|≥1`; 10 minutes is a score/display mark, not a flatten. Session residual is a read-only join of live V2 `signal_log_*.csv`. Top banner sums paper ticks and trade counts since the last 18:00 ET open, except this cutover session which starts at 2026-09-02 12:07 ET. Keeps V2.2 LIFO FRIENDLY display. Writes only under `workspace/analysis/aggression_v27_live/`. Label `PROVISIONAL / RESEARCH-ONLY / LOCAL-10M-FV / NOT AFTER-COST VALIDATED`. Named V2.7 because V2.3 is already the move-exhaustion monitor. Docs: `docs/aggression_v27_monitor.tex` (full live-page note) and `docs/aggression_v27_local_fv.md`. Tests: `tests/test_v27_local_fv.py`. Live V2/V2.2/V2.3 unchanged. File changes need a manual V2.7 restart.

## Aggression V2.6 C018 + LIFO Feedback (`scripts/aggression_dashboard_v26/`)

Isolated manual-only V2.2-derived dashboard on browser-safe port 5062 (migrated from Chromium-blocked SIP-TLS port 5061). It writes only under `workspace/analysis/aggression_v26_live/`, keeps LIFO FRIENDLY, adds the reduced C018 fade lean, and records human grades; fixed status is `PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE`.

`app.py` owns Flask routes and isolated instances; `c018_context.py`, `c018_store.py`, `feedback_store.py`, and `lifo_store.py` own detection and persistence. Thin wrappers support the copied runtime; the template/static files provide banners, daily cards, re-grading, and a compact non-modal corner prompt; focused coverage is `tests/test_v26_c018_feedback.py`, with launch/isolation details in `README.md`.

## Aggression V2.3 Exhaustion Context (`scripts/aggression_dashboard_v23/`)

Isolated manual-only copy of V2 on port 5059. Preserves V2 residual WATCH/DISLOCATION and adds display-only move-exhaustion context badges from research-supported families (E03/E11/E12/E13/E15/E16 + reduced E18/E21/E22). Writes only under `workspace/analysis/aggression_v23_live/`. Fixed label `PROVISIONAL / RESEARCH-ONLY / NOT AFTER-COST VALIDATED`. Live V2 unchanged. Docs: `docs/aggression_v23_move_exhaustion.md`. Tests: `tests/test_v23_exhaustion_context.py`.

## Aggression V2.2 LIFO Copy (`scripts/aggression_dashboard_v22/`)

Isolated manual-only copy of V2 on port 5058. Preserves V2 residual WATCH/DISLOCATION research UI and adds display-only **LIFO FRIENDLY** (`|residual_z|≥1` + low trade intensity; alert text only + observational stats). Its V2.2-owned `live_buffer.py` keeps AccessAPI WS/HTTP seed as primary, automatically starts direct Databento Live TBBO after configured disconnect/staleness, replays/deduplicates the boundary, and returns after sustained AccessAPI recovery; the UI names the active source. Writes only under `workspace/analysis/aggression_v22_live/`; live V2 remains unchanged. Docs: `docs/aggression_v22_lifo.md`. Tests: `tests/test_v22_lifo.py`.

## Aggression V1 Six-State Banner Spec (`docs/aggression_v1_monitor.tex`)

Revised V1 measurement note: five charts as of 20 Aug 2026, banner classification revised 16 Sep 2026. READY headlines are QUIET, BALANCED, AGGRESS DOMINANT, IMPLIED PASSIVE DOMINANT, JOINTLY DRIVEN, CONTESTED. Attribution uses `d=a+b` in ticks on a trade-to-trade last-trade clock and `beta_ref` = last-10-session mean of last-3-hour TOD `ΔP` on `ΔA` slopes (later day-tagged). Live on V3 5053 only. Live Panel 4/5 OLS stay diagnostic. This is not the live 5050 implementation.

## Aggression V1 Flow Dominance Banner (`scripts/aggression_dashboard/`)

Live V1 Panel 1 plots session print-clock TotalAggress (`cumsum(sign*size*$DV01)`, first print = 0) against the selected symbol's last traded price, held across other-symbol prints. Panels 2–5, OLS, and the dominance banner stay on completed 1-minute bars. The session publisher keeps an untrimmed `_SESSION_TICKS` tape for P1 and still folds 1-minute bars for the rest of the page.

Live V1 on port 5050 now exposes a top-level `flow_dominance` block and compact top banner from completed one-minute bars. `flow_dominance_context.py` decomposes the frozen intraday fair-value model into `beta * delta(TotalAggress)` and `delta(residual)`, classifies local 15-minute price sums with a 1.5 dominance ratio and a 2-tick Quiet floor (no session-expanding z), and reports transitions plus five-minute strengthening/slowing in ticks. `POST /api/aggression` also returns `flow_dominance_series` (full-history snapshots, clipped to chart lookback) so the local **Banner on hover** option can show the completed-bar banner at any timestamp without changing the live top banner. Live `POST /api/aggression` reuses the last payload for `LIVE_RESULT_CACHE_TTL_SEC` (1s) so extra Tailscale tabs share one pandas rebuild.

The fixed UI/API label is `DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED`; implied passive is residual flow, not resting-order identity. Documentation is `docs/aggression_v1_flow_dominance_banner.md`, and focused API/UI/calculation coverage is `tests/test_v1_flow_dominance_context.py`; the live process requires a manual restart.

## Aggression Program LaTeX Monograph (`docs/aggression_v1_v22_research_program.tex`)

Currently a V1-only description of the live Aggression Monitor (`scripts/aggression_dashboard/`, port 5050): flow construction, five panels, contemporaneous OLS fair-value lines, and the descriptive dominance banner. Other versions and the experimental lineage were removed from this file on 2026-08-22.

## Gamma Tracker V6 Implementation Note (`docs/gamma_tracker_v6_implementation.md`)

Current implementation record for live V6 (port 8055): concept, writer/dash stack, units, data retention, surface/metrics/heatmap/event-ownership kernel, implemented vs E1–E9 ideas. Companion to the TeX measurement note.

## Gamma Tracker V6 Scientific Record (`docs/gamma_tracker_v6_scientific_record.tex`)

V1-style measurement note for the live Gamma Tracker V6 page (`Z:\uikitxv2\OptMonitor\gamma_screener\gamma_ladder_standalone\gamma_tracker_dash_v6.py`, port 8055). Covers the v3 writer stack, delta-bucket surface, RR/fly/backbone formulas, percentile heatmap, event conditioning, and how to read the page. Wing-spread mode leaves the ATM column blank (`n/a`). Event score is ownership-only: still-ahead events before 12:00 CT expiry, same-day uses `1st` weights, printed events are 0. The yellow upcoming-events banner reads Yaman `events.sqlite`; EventScorer still uses `economic_events.db`. Also notes the additive portable package `Z:\uikitxv2\OptMonitor\gamma_screener\gamma_tracker_v6_standalone\` (own writer + `gamma_tracker_v6.db`). Live 8055 still reads `gamma_tracker_v3.db`.

## Standalone Gamma Tracker V6 (`Z:\uikitxv2\OptMonitor\gamma_screener\gamma_tracker_v6_standalone\`)

Copyable writer + dashboard + slim SQLite for a production machine. Vendors the live-options adapter (no v2 ordinals import), event scorer, and a dash copy that defaults to `gamma_tracker_v6.db`. Writer refuses the live v3 path. Seed copies heatmap_fact + 30d summaries + last two surfaces from `gamma_tracker_v3.db` with `mode=ro`. Does not edit or restart live 8055. Tests: `test_standalone.py`.

## Aggression V1.2 RANGE-Caution Monitor (`scripts/aggression_dashboard_v12/`)

Isolated manual-only copy of V1 on port 5056. Adds non-directional provisional `CAUTIOUS_RANGE` (large `|z|≥1` + widening 15m), display-only `SOFT_BREAKOUT_LEAN` (B=4 only), and display-only `LIFO FRIENDLY` (H5: `|z|≥1` + low trade intensity; UI shows alert text only, no factor callouts). Logs caution + LIFO episodes with separate observational stats under `workspace/analysis/aggression_v12_live/`. Fixed labels include LIFO `PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL`. Does not emit long/short or edit live V1/V2/V3. Pairs with `docs/aggression_v12_range_caution.md` and `tests/test_v12_range_caution.py`.

## Aggression V1.3 Tick TotalAggress (`scripts/aggression_dashboard_v13/`)

Isolated copy of current V1 on manual-only port 5055. Panel 1 uses a per-print session cumsum (`aggregate_tick_aggression`) stored in `_SESSION_TICKS`, not the 1-minute banner/history bars. Panels 2–5 and the dominance banner stay on completed 1m bars. Writes only under `workspace/analysis/aggression_v13_live/`. Label `PROVISIONAL / RESEARCH-ONLY / TICK-TOTALAGGRESS / NON-EXECUTABLE`. Named V1.3 because V1.2 is already 5056. Launcher: `Y:\Aastha\SOD\run_aggression_monitor_v13.bat`. Tests: `tests/test_v13_tick_aggress.py`. Does not write V1 sqlite, banner alerts, or edit live V1/V1.2/V2.

## Aggression V1 Flow–Price Dispersion Research (`scripts/aggression_v1_flow_price_dispersion_research.py`)

Research-only causal V1 residual study: prior-day OLS of TY price delta on DV01-weighted TotalAggress cumsum, then residual gap/velocity/acceleration/lead-lag dynamics as predictors of independently defined forward RANGE vs TREND regimes. Reads `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv`, uses chronological discovery/validation/locked-FINAL splits with date-clustered bootstrap/BH-FDR gates, and writes only under `workspace/analysis/aggression_v1_flow_price_dispersion/`. Pairs with `docs/aggression_v1_flow_price_dispersion_experiments.md` and `tests/test_v1_flow_price_dispersion.py`. 2026-07-16 gate: 0 PASS / 1 PROVISIONAL / 69 FAIL; no dashboard.

## Aggression V1 Dispersion Factor Breakout Sweep (`scripts/aggression_v1_dispersion_factor_breakout_sweep.py`)

Research-only grid of residual dispersion factors (value/direction/speed/persistence/shape/combos) against horizon-scaled ATR CONTINUED_RANGE vs yield-bps BREAKOUT labels over `h∈{15,30,60}`, `B∈{4,6,8,12,16}`, `k∈{0.5,1,1.5,2}`. Reuses causal residual helpers; writes journal/leaderboard/stability/gate-ladder artifacts only under `workspace/analysis/aggression_v1_dispersion_factor_breakout/`. Report: `docs/aggression_v1_dispersion_factor_breakout_experiments.md`. 2026-07-16: primary `(30,8,1)` 0 restrictive PASS; 18 restrictive PASSes only at soft B=4; FINAL not opened; no live/dashboard edits.

## Aggression V1 Dispersion Foundations (`scripts/aggression_v1_dispersion_foundations_research.py`)

Research-only H1–H10 hypothesis catalog (width/duration/shape/speed/flow-in-episode/flow×sign/depth-passive/microstructure/combos/FV-parity) vs ATR/bps CONTINUED_RANGE vs BREAKOUT. Adds TBBO depth/spread proxies never gated before for V1 dispersion. Writes under `workspace/analysis/aggression_v1_dispersion_foundations/`. Report: `docs/aggression_v1_dispersion_foundations_experiments.md`. 2026-07-17: validation primary PASS `H5_trade_int_lo`→RANGE (+10.6pp); FINAL +8.15pp but CI fails — not shippable; no V1.2 retune.

## Aggression V1 Aggress/Passive Dominance Param Sweep (scripts/aggression_v1_aggress_passive_dominance_param_sweep.py)

Staged offline expansion of dominance lookbacks/ratios/QUIET floors, forward horizons, and frozen/strict/loose path-efficiency RANGE/TREND cutoff packs. Writes under workspace/analysis/aggression_v1_aggress_passive_dominance_param_sweep/. Report: docs/aggression_v1_aggress_passive_dominance_param_sweep_experiments.md. 2026-07-17: Stage-2 0 restrictive PASS; FINAL locked; no V1.2 retune.

## Aggression V1 Aggress vs Passive Dominance (scripts/aggression_v1_aggress_passive_dominance_research.py)

Research-only role/absorption dominance (AGGRESS_DOM / PASSIVE_DOM / NEITHER; QUIET separate) from causal beta*dA vs dR contributions, scored on forward path-efficiency RANGE vs TREND (ATR/bps diagnostic). Writes under workspace/analysis/aggression_v1_aggress_passive_dominance/. Report: docs/aggression_v1_aggress_passive_dominance_experiments.md. 2026-07-17: 0 restrictive PASS; FINAL locked; no V1.2 retune.

## Aggression Trend vs Range Research (`scripts/aggression_trend_range_research.py`)

Read-only offline script that labels TY 1m bars as `TREND_UP` / `TREND_DOWN` / `RANGE` from path efficiency, momentum, range width, ADX-lite, and swing structure, then tests the trader claim that ranges show more block/large prints (`large_trade_share`, `avg_trade_size`) while trends take less effort. It reads `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` only, writes stats/gate JSON under `workspace/analysis/aggression_trend_range/`, and pairs with `docs/aggression_v21_trend_range_experiments.md`. It does not modify V2; V2.1 dashboard copy is gated and was not built after the 2026-07-08 fail (including the block-trade reinterpretation).

## Aggression V2 Convergence Research (`scripts/aggression_v2_convergence_research.py`)

Read-only offline script for fade-vs-continuation and price-led vs flow-led residual formation tests. It reads walkforward Ridge predictions, V2 `alert_log.csv` / `signal_log_*.csv`, and `workspace/economic_events.db` (event blackout), writes after-cost stats and gate JSON under `workspace/analysis/aggression_v2_convergence/`, and pairs with `docs/aggression_v2_convergence_experiments.md`. It must not modify V1/V2/V3 dashboards or live stores. 2026-07-09: PRICE_LED fade letter-passed the gate but failed RTH/live confirmation; V2.1 not built.

## Aggression V2 Resolution Diagnostics (`scripts/aggression_v2_resolution_diagnostics_research.py`)

Read-only Tier-1 tradability script. Buckets V2 alert episodes into PRICE_TO_FV / FV_TO_PRICE / EXPIRED from `price_share_of_reversion`, builds open/midlife/close feature trajectories from V2 `signal_log_*.csv`, writes median+IQR trajectory PNGs, fits open-time logistic classifiers per bucket on a chronological holdout, and writes diagnostics + gate JSON under `workspace/analysis/aggression_v2_resolution/`. Paired report: `docs/aggression_v2_tradability_tier1_experiments.md`. 2026-07-09 gate FAIL (AUC ok for EXPIRED/FV_TO_PRICE but top-decile n=16).

## Aggression V2×V3 Confluence Research (`scripts/aggression_v2xv3_confluence_research.py`)

Read-only join of V2 `alert_log.csv` with V3 `regime_log_*.csv` flow-effort tags plus formation/PRICE_LED-FLOW_LED cells; reports after-cost expectancy per confluence cell and a gate JSON under `workspace/analysis/aggression_v2xv3_confluence/`. Does not import or edit live V2/V3 dashboard code. 2026-07-09 gate FAIL (only ~4 days of V3 regime overlap; best cell +0.35 ticks after cost).

## Aggression V2 Magnitude Research (`scripts/aggression_v2_magnitude_research.py`)

Read-only classifier for whether `|price move|` from alert open exceeds 5 ticks within 30m (direction ignored). Uses open-time z/vol/range/formation and optional V3 regime features; writes episodes/holdout scores/gate under `workspace/analysis/aggression_v2_magnitude/`. 2026-07-09 gate FAIL (AUC 0.73 and +36pp top-decile lift but n=16).

## Aggression V2 Stand-Aside Research (`scripts/aggression_v2_standaside_research.py`)

Read-only toxic-alert filter (fade after cost ≤ −1 tick or adverse ≥ favorable). Scores open-time features, measures stand-aside vs keep after-cost expectancy on chronological holdout, writes under `workspace/analysis/aggression_v2_standaside/`. 2026-07-09 gate FAIL (AUC 0.46; filter does not isolate a toxic pool).

## Aggression V2.7 Alert Clocks (`scripts/aggression_v2_alert_clock_research.py`)

Offline-only replay of V2.7 enter 1.5 / exit 0.5 / min-hold 5 alerts on B1_W10 walk-forward residuals, varying only the deadline/score clock. Frozen grid: wall 3–30m, print counts, volume-proxy counts, minutes-with-a-tick, discovery-only TOD adaptive prints, and first/both hybrids. Writes under `workspace/analysis/aggression_v2_alert_clocks/` and pairs with `docs/aggression_v2_alert_clock_experiments.md`. 2026-08-31: 1-minute print/volume clocks fire in ~1 minute; usable 5–15m clocks keep median % via price = 0; `tickmins_3` holdout hit 53% / all-OOS 50%. `ship_gate` false; live V2.7 stays on 10-minute wall time. Not MBP-1 event time.

## Aggression V2 Executable PnL (`scripts/aggression_v2_executable_pnl_research.py`)

Offline-only session-gap fade study. Entry/exit ARE session `|residual|/tick >= G`, same-bar B1_W10 sign agree, V1 aggress-with-fade veto, and first of +N / −X / 10m on AccessAPI TBBO bid/ask (long pays ask, short sells bid). `--today` replays the 2026-08-31 18:00 ET session from live V2/V2.7 logs; `--holdout` fits G×N×X on earlier OOS and confirms on 2026-05-13–2026-06-05. `--corrections` scores one frozen miss per cell (C1–C4, I1–I8, combo, L1 autopsy) via `scripts/aggression_v2_executable_pnl_corrections.py` and writes `workspace/analysis/aggression_v2_executable_pnl/corrections/`. Print/midchg clocks labeled `NOT_MBP1_L1`. Does not open ME V2 final holdout or change live V2.7.

## Aggression V2 Short-Horizon Fair Value (`scripts/aggression_v2_short_horizon_fv_research.py`)

Offline-only study of two new V2 fair-value constructions plus a frozen flip-vs-sticky lean sweep aimed at a 5–10 minute hold and ≤3-tick wrong-way path. Reads the published TBBO matrix only; writes under `workspace/analysis/aggression_v2_short_horizon_fv/`; pairs with `docs/aggression_v2_short_horizon_fv_experiments.md`. Track A is a same-day `price_{t+h}−price_t` Ridge (3/5/8/10m core). Track B is a rolling-anchor nowcast (`W∈{3,5,8,10,15,30}`, packs B0/B1). Track C scores raw and hysteresis leans (enter 1.0/1.5/2.0, exit 0.5/1.0, min-hold 0/5). 2026-08-31 holdout: Track A letter-informative but R² lift vs random walk is ~10⁻⁵; `W=5` is a `mom_5m` leak; sweet-spot candidate `B1_W10` + enter 1.5 / exit 0.5 / min-hold 5 (median lean 5.5m). `ship_gate` false; live V2.2 session FV unchanged.

## Aggression V2 Forward-Return Research (`scripts/aggression_v2_forward_return_research.py`)

Research-only generic signal experiment that reframes the V2 monitor from fixed residual fade into horizon-based forward-return prediction. It reads `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` plus Ridge `walkforward_predictions.csv`, builds within-day 1/3/5/15/30m forward-return targets, adds flow-dynamics features and read-only V3 `annotate_flow_effort()` regime ingredients, fits walk-forward Ridge/logistic models, then tests regime-conditioned FADE/FOLLOW/STAND_ASIDE translations against baselines with adverse/Sortino stats. Outputs live only under `workspace/analysis/aggression_v2_forward_return/` and pairs with `docs/aggression_v2_forward_return_experiments.md`; 2026-07-09 gate FAIL (short-horizon AUC exists but after-cost trade translation remains negative), so no dashboard.

## Aggression V2 EV Selectivity Research (`scripts/aggression_v2_ev_selectivity_research.py`)

Research-only follow-up to the forward-return experiment. It reads `workspace/analysis/aggression_v2_forward_return/forward_return_dataset.csv` and `forward_return_predictions.csv`, retrains per-horizon big-move and absolute-move models, computes an EV score (`P(big) * predicted_abs_move * direction_confidence - 0.5 tick`), and tests top-EV holdout quantiles with adverse/Sortino stats. Outputs under `workspace/analysis/aggression_v2_ev_selectivity/` and pairs with `docs/aggression_v2_ev_selectivity_experiments.md`; 2026-07-09 gate FAIL because top-EV slices remain negative after cost.

## Aggression V2 Meta-Label Research (`scripts/aggression_v2_meta_label_research.py`)

Research-only V2 alert take/skip meta-label test. It reads `workspace/analysis/aggression_v2_live/alert_log.csv` and `signal_log_*.csv`, optionally joins EV scores when timestamps overlap, trains a secondary classifier on whether the existing V2 fade is profitable after 0.5-tick cost, and compares taken subsets against all alerts. Outputs under `workspace/analysis/aggression_v2_meta_label/` and pairs with `docs/aggression_v2_meta_label_experiments.md`; 2026-07-09 gate FAIL (AUC 0.54; top-30% improves but remains negative).

## Aggression V2 Event-Window Research (`scripts/aggression_v2_event_window_research.py`)

Research-only post-release flow persistence test. It reads `workspace/economic_events.db`, de-duplicates release components by timestamp, joins to the forward-return dataset, and tests whether first-5-minute post-release signed aggressor flow predicts 5/15/30m TY moves after costs, with optional EV gating. Outputs under `workspace/analysis/aggression_v2_event_window/` and pairs with `docs/aggression_v2_event_window_experiments.md`; 2026-07-09 strict gate FAIL only on sample size (`n=49<50`) despite +1.3 to +2.2 ticks after cost, so it is a re-gate lead, not a dashboard.

## Aggression Statistical Gate Library (`scripts/aggression_gate_lib.py`)

Pure offline statistics helpers for aggression research: deterministic bootstrap confidence intervals (optionally clustered by date), one-sided positive-mean tests, 0/0.25/0.5/1.0-tick cost sweeps, Benjamini-Hochberg FDR correction, and `PASS` / `PROVISIONAL` / `FAIL` classification. It imports no live dashboard code and performs no application I/O.

## Aggression V2 Gate Re-evaluation (`scripts/aggression_v2_gate_reeval_research.py`)

Additive research-only harness that reads per-observation holdout artifacts from event-window, meta-label, EV-selectivity, forward-return, resolution, magnitude, stand-aside, and confluence experiments. It applies date-clustered 10,000-draw bootstrap CIs, one-sided tests, BH-FDR across all headline cells, cost sensitivity, adverse stats, and context-stability checks; outputs `reeval_cells.csv` and `reeval_summary.json` under `workspace/analysis/aggression_v2_gate_reeval/`, paired with `docs/aggression_v2_gate_reeval_experiments.md`. 2026-07-09 result: 47 cells -> 0 PASS, 11 PROVISIONAL, 36 FAIL; event-window 5m/15m survives FDR but remains PROVISIONAL because no-event stability is inapplicable.

## Aggression V2 Event-Window Lock Research (`scripts/aggression_v2_event_window_lock_research.py`)

Read-only correction to the original event-window study. It forms the flow signal over the first five post-release minutes, enters at the observed T+5 lock price, then scores +5/+15/+30m outcomes with costs and date-clustered statistical gates. Outputs under `workspace/analysis/aggression_v2_event_window_lock/` and pairs with `docs/aggression_v2_event_window_lock_experiments.md`. The corrected executable benchmark has no validated edge (5m -0.23, 15m +0.19, 30m +0.03 ticks after cost on holdout).

## Aggression V2.1 Event-Flow Paper Dashboard (`scripts/aggression_dashboard_v21/`)

Fresh isolated V2-context dashboard on manual-only port `5054`. It creates independent buffer/model instances, reads the economic-event DB and V2 research artifacts, collapses event components by release timestamp, locks a LONG/SHORT/NO_SIGNAL paper lean at T+5, and tracks executable +5/+15/+30m outcomes from the lock price. It also exposes a display-only magnitude-confidence block from `magnitude_context.py`: ELEVATED when the validated 15-row pre-window vol/range threshold is met, explicitly `PROVISIONAL / NON-DIRECTIONAL`, with no alert-direction changes. All writes are isolated under `workspace/analysis/aggression_v21_live/`; no SOD, Tailscale, Telegram, or automated execution integration exists.

## Curve Aggression Dashboard (`scripts/aggression_dashboard/`)



### core.py

Reusable data layer for the interactive curve aggression dashboard. Fetches the four live-supported Treasury futures (`FV`, `TU`, `TY`, `US`) from Databento `GLBX.MDP3` for historical/backfill mode with a dashboard-specific v2 cache (`trades_v2_<parent>_<YYYYMMDD>.parquet`) that preserves `instrument_id`/`raw_symbol`, filters each parent-symbol day to the highest-volume outright contract, classifies `side` into buy/sell aggressor signs, applies configurable per-contract DV01 from `config/aggression_dv01.json`, aggregates signed flow onto a 1-minute grid via `trades_to_symbol_minute_bars` / `combine_symbol_minute_bars` / `upsert_minute_bars`, and fetches the selected price symbol from PostgreSQL OHLCV with front-month Databento trade-print fallback. `upsert_minute_bars` merges by index so a gappy resample cannot delete closed minutes; `printed_minute_bars` drops zero-print buckets before live upsert. `trading_day_start()` / `default_start_time()` return the current Globex open (18:00 ET). BuySell Ratio now preserves `buysell_ratio_raw` and plots an EMA-smoothed `buysell_ratio` (`BUYSELL_EMA_SPAN=10`) to reduce minute-to-minute jumpiness while keeping TotalAggress and volume bars unchanged. Also provides `prune_trades_cache()` with `CACHE_RETENTION_DAYS=30` so old v1/v2 parquet trade caches do not grow unbounded.



### live_buffer.py

AccessAPI-primary live-TBBO consumer for near-real-time dashboard mode. It streams `FV/TU/TY/US`, stores rolling trades and bounded-cadence BBO mids, and monitors primary freshness. After `AGG_BACKUP_STALE_SEC`, it starts the shared direct-Databento TBBO client with intraday replay and per-symbol boundary dedupe, and only then sets `source=databento_backup`; after `AGG_BACKUP_RECOVER_SEC` of healthy AccessAPI records it stops the backup. `status()` exposes the active source plus nested primary/backup health.



### hf_model.py

Fair-value model helpers for the curve aggression dashboard. Aligns daily-reset cumulative TotalAggress to the selected price grid and supports two fixed-coefficient references: `fit_session_model()` for an intraday OLS fit held once per session, and `fit_tenday_model()` for a pooled 10-business-day daily-reset model fit once per day. `align_flow_and_price(..., rezero=True)` still subtracts the first aligned point for those fits; panel 1 calls it with `rezero=False` and `ffill_aggress=False` so lookback only zooms and mid-only minutes do not paint a stale TotalAggress ruler. OLS keeps the default ffill. Returns actual/predicted price, residuals, residual z-scores, large-residual markers, and metadata including alpha/beta/R², fit timestamp, cadence, and valid point count.



### panels.py

Builds JSON-safe Plotly payloads for the five dashboard panels: TotalAggress vs price, BuySell ratio vs price, buy/sell flow bars with TotalAggress and price overlays, intraday fair value, and 10-business-day fair value. Panel 1 plots session TotalAggress without a lookback re-zero so a finished minute’s hover value stays put, leaves mid-only minutes as gaps (`connectgaps=false`, step `hv`), and includes `axis_align`. Panels 4/5 use cached fixed-coefficient fair-value model outputs from `app.py` rather than refitting inside the payload builder.



## V1 Flow History Viewer (`scripts/aggression_flow_history_dash/`)

Isolated read-only Flask viewer on port `5064`. Reads `workspace/analysis/aggression_v1_live/flow_history.sqlite` and economic events from Yaman's Postgres `economic_events` (read-only role) via the in-memory `EventsCache` in `events.py` (60 s refresh, last-good file, `events` block on `/api/status`; `db_path=` still reads an events SQLite for tests). Daily buckets are Globex sessions (Sun 18:00–Mon 17:00 ET = Monday; closed Fri 17:00–Sun 18:00). Store is 1-minute completed bars from every trade print. 2022-01-03 through 2026-06-11 is filled from AccessAPI `schema=trades` by `backfill_accessapi.py` (source `accessapi_2022`); 2026-06-12 onward stays V1 cache/live. Load uses `GET /api/bootstrap` plus an in-process cache of closed session days (`cache.py`); the current Globex session is re-folded on each `/api/today` poll (15s). LIVE uses `MAX(ts_utc)` via a plain sqlite open (`query_only=ON`, no `file:` URI — UNC `\\100.95...` paths fail that form). Journal: `workspace/analysis/aggression_flow_history/accessapi_2022_journal.sqlite`. Tags coupon supply, month-end, tier-1, and session weekday only — no residual QUIET label. OLS, distributions, and today analog stay descriptive. Does not import live V1 `app.py`. Manual launcher: `Y:\Aastha\SOD\run_v1_flow_history_dash.bat`.

### flow_history.py

Durable SQLite log of completed V1 1-minute aggress buy/sell volume under `workspace/analysis/aggression_v1_live/flow_history.sqlite`. Stores per-symbol `buy_contracts`/`sell_contracts` (ask-taken / bid-hit) and matching `$DV01` flow, seeded from the existing `trades_v2_*.parquet` cache without calling Databento, then appended by the live session publisher. `query_flow_history()` can return one symbol or a `CURVE` sum. `load_completed_symbol_bars()` is a read-only helper so live ingest can fill in-memory holes from already-persisted minutes.

### app.py

Flask app for the local curve aggression dashboard. Serves `GET /`, `GET /api/config`, `GET /api/live_status`, `GET /api/flow_history`, `GET /api/flow_history/status`, and `POST /api/aggression`; parses New York start/end times, starts the `LiveTradeBuffer`, supports `mode=live|historical`, and returns all five panel payloads plus metadata for browser rendering. Live mode publishes session 1-minute bars in the background and serves `/api/aggression` from that store (lookback remains display-only). The 1s publisher rewrites only the forming minute; a backfill cycle sets `refold_tail` to re-fold the last 15 minutes once. Closed bars are not zeroed by a gappy resample. In-memory holes are healed from `flow_history.sqlite`. Raw session backfill is trimmed to 20 minutes only after bars are ready. Live panel 1 y-range uses a 2% deadband. Concurrent identical live posts still share one in-flight result and a 1s payload cache. Completed session minutes are upserted into `flow_history.sqlite`.



### templates/index.html, static/dashboard.js, static/dashboard.css

Browser UI for the live aggression dashboard with US universe, FV/TU/TY/US aggression checkboxes, selected price symbol (TY by default), Live/Historical mode switch, chart lookback selector (`2h` default, `6h`, `12h`, `1 day`), Pause, Historical end-time picker, Eastern start-time picker, DV01-adjust toggle, disabled V2 yield toggle, manual refresh, and auto-refresh using `Plotly.react` (1-second live cadence with an in-flight guard, slower in historical mode). Uses Plotly's default grey-blue chart theme and reference-like spacing/legends; panel 1 applies server-provided fair-value-aligned y-axis ranges and displays the implied axis `alpha`/`beta` in the footer. TotalAggress honors `connectgaps` / `shape` from the server so mid-only minutes gap instead of drawing a ruler. The freshness banner explicitly names ACCESSAPI or DATABENTO BACKUP and reports LIVE/STALE/DOWN/HISTORICAL.



### config/aggression_dv01.json

Static V1 per-contract DV01 table for dashboard aggregation: `FV=42`, `TU=36`, `TY=85`, `US=125` dollars per bp. Intended as an editable config until daily curve-derived DV01 refresh is added.



## Offline Aggression Dashboard Demo (`aggression_dashboard_demo/`)



### app.py, core.py, hf_model.py, panels.py

Standalone offline copy of the curve aggression dashboard for external/demo sharing. It reads bundled parquet trade files from `data/`, computes front-month-filtered DV01-adjusted aggression and trade-print price series, retains all five panel payloads including the causal rolling-OLS HF model, and has no live-feed, database, or credentialed market-data dependency.



### templates/index.html, static/dashboard.js, static/dashboard.css

Offline-only browser UI for the demo. It removes live-mode, pause, freshness, and polling controls while keeping price-symbol selection, aggression checkboxes, date window inputs, DV01 toggle, and Plotly rendering for all five panels.



### export_static.py, output/demo_dashboard.html

Static export generator and generated standalone HTML artifact. `export_static.py` builds a TY-default payload from the bundled data and embeds the chart payload, CSS, and rendering JS into `output/demo_dashboard.html` so the demo can be opened without running Flask.



### data/*.parquet, config/aggression_dv01.json, requirements.txt, README.md

Bundled sample data and packaging for the offline demo. The data subset covers selected May 2026 sessions for `FV/TU/TY/US`; `requirements.txt` lists the minimal Python dependencies, and `README.md` documents running the local demo app and regenerating the static HTML.



### bbg_excel_watcher.py

Bloomberg Excel watcher for automated dashboard triggers. Monitors a running Excel workbook via COM automation (xlwings) for NFP actual data from Bloomberg RTD/BDP formulas. When actual value appears in the configured cell, automatically generates the post-event dashboard and sends it via Telegram. Features: state persistence (prevents duplicate triggers same day), 30-day state cleanup, connection resilience (retries if Excel disconnects), value validation (ignores N/A/#VALUE!/blank). CLI: `python scripts/bbg_excel_watcher.py --workbook "NFP_Data.xlsx" --poll 5`. Config: `config/bbg_cells.json`. Requires Excel to be open with Bloomberg add-in connected.



### nfp_live_trigger.py

Automated NFP post-event analysis with Telegram notifications. Monitors `Z:/Meini/NFP.xlsx` for actual NFP data. When detected: (1) gets live TY price from snapshot DB (`snapshots_YYYY-MM-DD.db`) with options-implied FWD breakeven from vega-weighted IV, (2) calculates surprise percentile using historical standardized surprises, (3) finds matching historical bucket (10% width) and computes B/E statistics (mean/median/95th/99th multipliers), (4) projects price paths at 30-min intervals from 8:30am to 3pm, (5) generates HTML dashboard via `build_postevent_dashboard()`, (6) sends Telegram notification with full summary to all ALLOWED_CHAT_IDS. Features: dynamic snapshot DB discovery (pattern `snapshots_YYYY-MM-DD.db`), options-implied FWD calculation, xlwings for live Bloomberg data (falls back to pandas), state persistence (one trigger per day). CLI: `--test` (mock data), `--poll N` (interval), `--reset` (clear state).



### load_parquet_to_postgres.py

Loads parquet files from configured data directory into PostgreSQL ohlcv_1m table. Extracts base symbol from filename (e.g., "Intraday_data_ZN_1m_..." → "ZN"), maps to Bloomberg format (ZN→TY), and assigns contract symbols using calendar-based roll logic for treasury futures. For non-treasury symbols, uses static symbols. Replace strategy: deletes existing rows in date range before insert. CLI: `--list` shows files, `--symbol ZN` loads one, `--all` loads all 1m files, `--dry-run` previews.



### load_databento_ohlcv_volume_roll.py

Loads Databento OHLCV 1-minute data from ZIP archives into PostgreSQL `ohlcv_1m` with **daily volume-based contract rolling**. Two-pass approach: Pass 1 aggregates daily volume by `instrument_id` (filtering out calendar spreads and user-defined instruments), then determines active contract per day as the highest-volume outright. Pass 2 extracts only the active contract's bars and assigns `target_symbol` (default `TY`). Uses streaming zstd decompression (`stream_reader`) for large files. Batch inserts with `ON CONFLICT DO NOTHING` for idempotent re-runs. Roll dates are logged. CLI: `python data_setup/load_databento_ohlcv_volume_roll.py <path_to_zip> [--target-symbol TY] [--batch-size 10000] [--no-skip-existing]`.



### run_l1_auto_builder.bat

Windows wrapper to run `scripts/run_l1_auto_builder.py` from Task Scheduler. Assumes `py -3` is available on PATH and the repo root is the current working directory.



### install_l1_builder_task.ps1

Creates (or replaces) a Windows Task Scheduler entry named `L1AutoBuilder` to run `run_l1_auto_builder.bat` daily at 02:00 local time. Validates script paths and prints the scheduled command.



### run_bbg_data_daily.bat

Windows wrapper to run the Bloomberg `bbg_data` loader via Task Scheduler. Runs two steps in order for both configured SQLite targets: (1) `data_setup/create_bbg_data_table.py` for BBG component ingestion into `bbg_data`, and (2) `data_setup/ingest_excel_calendar_to_sqlite.py` for weekly calendar ingestion into `economic_events`. The default targets are the repo-local `workspace/economic_events.db` plus `D:\FRDBs\workspace\economic_events.db`. Uses the shared UNC workbook path `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx`, mirrors output to both the shared repo log and a local fallback log under `%LOCALAPPDATA%\market_data_puller\logs\`, writes last-success/last-error marker files under `%LOCALAPPDATA%\market_data_puller\status\`, and attempts the secondary DB even if the primary target fails. Important cmd guardrail: `:run_and_capture` must receive only the executable command via `%*`; the human-readable step label is now passed through `CURRENT_STEP` because cmd's `%*` does not change after `shift`, which previously caused scheduled runs to execute the quoted label text and fail with exit code `9009`.



### install_bbg_data_task.ps1

Creates (or replaces) a Windows Task Scheduler entry named `BBGData` to run `run_bbg_data_daily.bat` every 3 hours at `HH:15`, 24x7. Includes mapped-drive to UNC command-path normalization so Task Scheduler stores a UNC task command (avoids dependency on session-mapped repo drive letters), deletes older task names (`BBGDataDaily`, `BBGDataHourly`, etc.) on install, computes `StartBoundary` from the next valid 3-hour slot, writes the task via XML with `StartWhenAvailable=true`, battery restrictions disabled, `ExecutionTimeLimit=PT15M`, and accepts optional `-TaskPassword` to create a password-backed “run whether user is logged on or not” task when credentials are available.



### check_economic_events_db_staleness.py

Lightweight operational checker for the two `economic_events.db` copies (`workspace/economic_events.db` and `D:\FRDBs\workspace\economic_events.db`). Computes age from file mtime, treats missing files as stale, sends a Telegram alert when any copy is older than `30` hours, and suppresses duplicate alerts via a small local JSON state file under `Path.home() / ".market_data" / "alerts"`.



### run_economic_events_staleness_check.bat

Windows wrapper for `scripts/check_economic_events_db_staleness.py`. Intended for Task Scheduler and writes to a local append-only log under `%LOCALAPPDATA%\market_data_puller\logs\` so stale-check failures are visible even if the repo share is unavailable.



### install_economic_events_staleness_task.ps1

Creates (or replaces) a Windows Task Scheduler entry named `EconomicEventsDbStalenessCheck` to run `run_economic_events_staleness_check.bat` daily at 09:00 local time. Uses the same UNC-path normalization/XML registration pattern as `install_bbg_data_task.ps1`, disables battery restrictions, enables `StartWhenAvailable`, and supports optional `-TaskPassword` for password-backed/logged-off execution.



### enable_task_scheduler_operational_log.ps1

Small elevated helper to enable `Microsoft-Windows-TaskScheduler/Operational` via `wevtutil`. Exits with a clear error when run from a non-admin PowerShell session, because enabling this log requires elevation on the current machine.



### refresh_bbg_excel.py

Bloomberg-machine workbook refresh helper. First tries to attach to the already-open interactive Excel workbook (matching Bloomberg's normal usage), and only falls back to opening a new hidden Excel instance if needed. Normalizes workbook formulas so sheet `B2` uses `=TEXT(TODAY(),"YYYY-MM-DD")` and BDH calls reference `$B$1/$B$2`, then forces recalculation, saves, and leaves the interactive workbook open when it attached to an existing session.



### refresh_bbg_excel.bat

Windows wrapper for `scripts/refresh_bbg_excel.py`. Intended for Task Scheduler on the Bloomberg machine; now supports a standalone deployment where the `.bat` and `.py` live side-by-side in a copied folder such as `Meini`. Writes append-only logs to a local `logs/` subfolder and uses the shared UNC workbook path.



### install_bbg_refresh_task.ps1

Creates (or replaces) a Windows Task Scheduler entry named `BBGRefreshExcel` to run `refresh_bbg_excel.bat` every 3 hours at `HH:00`, 24x7, on the Bloomberg machine. Supports a standalone copied-folder deployment by locating `refresh_bbg_excel.bat` beside the installer itself, recommends a local folder such as `C:\bbg_refresh\`, computes `StartBoundary` from the next valid 3-hour slot, and keeps `/IT` so the task runs in the logged-in Bloomberg/Excel session instead of a detached background session.



### derive_auction_fields.py

Utility script to derive auction table fields from Treasury auction CSV exports. Default mode adds basic fields from `Securities.csv`; `--nomura-labels` outputs Nomura-style labels (`Auction`, `Size`, `Coupon`, `Stop`, `Bid/Cover`, `Tail`, `Yld Gap`, `Dealer%`, `Direct%`, `Indirect%`, `Dlr Hit`, `Dir Hit`, `Indir Hit`) using `Securities (2).csv` fields when available. Outputs a new CSV with appended columns.



### auction_data_puller.py

Official Treasury auction result puller. Calls the FiscalData `auctions_query` API for coupon auctions, merges new CUSIPs into `auction_data/*_raw.csv`, regenerates processed Nomura-style CSVs, seeds alert state during backfill, and sends Telegram alerts only once result fields are populated. CLI supports `--once`, `--loop`, `--backfill`, and `--dry-run`; `--loop` reads `workspace/economic_events.db` to sleep outside auction windows, poll every 30s in the 10-minute pre-event window, and poll every 10s for up to 30 minutes after auction time until the result is captured.



### run_auction_data_puller.bat

Windows Task Scheduler wrapper for the long-running `scripts/auction_data_puller.py --loop` daemon. Writes both shared and local fallback logs, maintains last-success/last-error markers under `%LOCALAPPDATA%\market_data_puller\status`, and uses the same `%*` command-capture pattern as the hardened BBG wrapper.



### install_auction_data_puller_task.ps1

Creates (or replaces) a Windows Task Scheduler entry named `AuctionDataPuller` to start `run_auction_data_puller.bat` once each weekday at 6:00 AM local time. The task runs the event-aware loop daemon with `ExecutionTimeLimit=PT18H`, `MultipleInstancesPolicy=IgnoreNew`, mapped-drive to UNC normalization, `StartWhenAvailable=true`, battery restrictions disabled, and optional `-TaskPassword`.



## Auction Data (`auction_data/`)



### process_auction_files.py

Batch processor for raw auction CSV files. Located in `auction_data/` folder for direct execution. Scans for `*_raw.csv` files and generates corresponding `*_processed.csv` files with 13 Nomura-style derived columns. Uses `derive_nomura_fields()` from `data_setup/derive_auction_fields.py`. Features: incremental processing (skips if processed file is newer than raw), `--force` flag to reprocess all. CLI: `cd auction_data && python process_auction_files.py [--force]`.



## Data Setup (`data_setup/`)



### create_bbg_event_fields_excel.py

Builds a Bloomberg Excel workbook with BDH formulas for economic event fields (Actual, Consensus, Prior, Revised, Std Dev). Creates one sheet per event group, grouping subcomponents on the same sheet, and includes a Summary sheet listing all tickers. New workbooks now default `B2` / end-date to `=TEXT(TODAY(),"YYYY-MM-DD")` so Bloomberg BDH receives a date string instead of an Excel serial number. CLI supports output path, date range, and custom field list.



### load_bbg_event_fields_to_db.py

Loads Bloomberg event fields from `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx` into `workspace/economic_events.db`. Parses each sheet (one per event group) and extracts component blocks with Date, Actual, Consensus, Std Dev columns. Maps Excel labels (e.g., "NFP", "UER", "CPI MoM") to DB event names via `LABEL_TO_DB_EVENT` dict. Updates `actual`, `consensus`, and `consensus_std_dev` columns, trusting Excel values over existing DB values on conflict. Supports `--dry-run` for preview and `--excel PATH` for custom Excel path. CLI: `python data_setup/load_bbg_event_fields_to_db.py [--dry-run] [--excel PATH]`.



### create_bbg_data_table.py

Creates and populates `bbg_data` in `workspace/economic_events.db` from the shared Bloomberg workbook. Attempts to match rows to `economic_events` for precise timestamp/unit enrichment, but falls back to Excel-date timestamps (`YYYY-MM-DDT00:00:00`) when no DB match exists so upcoming/placeholder rows are still inserted and later updatable. Also keeps rows even when actual/consensus/std-dev are blank. Before loading, it checks the workbook modification date and sends a Telegram alert only when the file is more than 2 days old, now deduped to at most one alert per `(resolved workbook path, local date)` via `Path.home() / ".market_data" / "alerts" / "bbg_excel_stale_state.json"`. Supports `--dry-run`, `--replace`, and custom `--excel`/`--db` paths. CLI: `python data_setup/create_bbg_data_table.py [--dry-run] [--replace] [--excel PATH] [--db PATH]`.



### ingest_excel_calendar_to_sqlite.py

Ingests weekly event-calendar Excel rows into `workspace/economic_events.db` using the existing calendar parser (`src/services/redis_event_stream/excel_parser.py`). Resolves the weekly source file via `settings.resolve_event_upstream_excel_path()`, maps short country codes to full names, generates stable `excel_...` event IDs, and inserts conservative placeholder rows (`actual/consensus/... = NULL`, `source=excel_calendar`) without overwriting existing natural-key matches (`event_name + country + event_date`).



### populate_event_tags.py

Interactive CLI to add event windows and annotations to `event_tags` in `workspace/economic_events.db`. Prompts for start/end timestamps, exact-time-known flag, comments, tags (with predefined options + custom), and market impact, with overlap detection and optional merge. CLI: `python data_setup/populate_event_tags.py`.



### load_databento_status.py

Loads Databento status data from zip file and creates `ohlcv_1m_status` table by joining with `ohlcv_1m`. Status data contains trading status events (halts, opens, closes) with columns: `action`, `reason`, `trading_event`, `is_trading`, `is_quoting`, `is_short_sell_restricted`. The script: (1) extracts zstd-compressed status CSV files from zip, (2) loads symbology mapping (instrument_id → raw_symbol), (3) creates combined table with OHLCV + status columns, (4) uses LATERAL JOIN to find the most recent status at or before each OHLCV timestamp. CLI: `python data_setup/load_databento_status.py <path_to_zip>`. Note: Status files may be empty for instruments without trading halts.



### bbg_regime_feature_exporter.py

Generates a Bloomberg-Excel (DAPI) workbook that pulls daily regime series via `BDH` (e.g., MOVE/VIX/2Y/10Y/DXY/SPX) and maps them onto all events from `workspace/economic_events.db` using `XLOOKUP`. Workflow: run `template` to write `Z:/Meini/bbg_regime_features.xlsx`, refresh in Bloomberg Excel, then run `export` to write `Z:/Meini/bbg_regime_features_by_event.csv`.



### telegram_bot_config.py

Configuration for Telegram bot. Loads BOT_TOKEN, ALLOWED_CHAT_IDS from environment or keys.env. Defines MAX_MESSAGE_LENGTH, ORCHESTRATOR_TIMEOUT, SEND_PLOTS, SEND_JSON_OUTPUT, PLOTS_DIR.



### live_surprise_tracker.py

Real-time economic event surprise tracker. Combines historical analysis with live WebSocket streaming.



### run_agent.py

CLI for running individual agents.



### scheduled_event_runner.py

Proactive scheduling system for economic event analysis. Builds weekly schedules from CSV event definitions, fetches upcoming event timestamps from Trading Economics calendar, and triggers analysis 2 minutes before each event. Sends one-way Telegram alerts with surprise percentiles, z-scores, and market reactions. CLI modes: `--build-schedule` (weekly calendar refresh), `--run-once` (check and process due events), `--loop` (continuous monitoring), `--status` (show schedule), `--test-telegram` (verify connection). Uses `src/core/schedule_store.py` for SQLite persistence and `src/core/telegram_notifier.py` for alerts. Reuses `analyze_event_data()` from `jolts_live_analyzer.py` for analysis.



### redis_alerts_dashboard.py

Lightweight Flask dashboard for live monitoring of Redis pre-alert stream messages. Serves an HTML table with auto-refresh and a JSON API (`/api/alerts`) that reads recent entries from `market:events` via `XREVRANGE`, includes stream metadata (`length`, `last-generated-id`), and renders latest alert rows for operational visibility.



### snapshot_explorer_dashboard.py

Interactive Flask dashboard for the Snapshot Query API (`z:\\uikitxv2\\lib\\trading\\snapshot\\api`). Provides tabbed UI + backend endpoints for `live`, `at`, `history`, `today`, `search`, `diagnose`, and real-time `subscribe` (SSE), with sortable result tables and optional Plotly `live_px` vs `last_updated` charts. Includes dynamic condition-builder controls (`Cond` rows), column selectors, and a sampling fallback for `today(interval=...)` by querying trading-day `history(...)`.



### powell_speech_analyzer.py

Real-time Powell speech sentiment analyzer. Uses Playwright to scrape StreamText live transcripts (`https://streamtext.net/player?event=CFI-FRB`) and OpenAI GPT-4o-mini for hawkish/dovish classification (-10 to +10 scale). Features: `StreamTextScraper` class for async browser automation with incremental text detection, `FedSentimentAnalyzer` class for LLM-powered sentiment scoring with exponential moving average, `ConsoleDisplay` class for color-coded real-time output with shift alerts. Classifies text by topic (Employment, Inflation, Rate Policy, Economic Outlook, Financial Conditions). Supports `--test` mode with sample Fed speech excerpts, `--url` for custom StreamText events. Outputs JSON logs to `workspace/powell_speech_{timestamp}.json`. Dependencies: playwright, openai (see `requirements-powell.txt`).



## Configuration (`config/`)



### powell_prompts.py

LLM prompt templates for Fed speech sentiment analysis. Contains `SENTIMENT_SYSTEM_PROMPT` (expert Fed watcher persona with hawkish/dovish scoring guide), `SENTIMENT_ANALYSIS_PROMPT` (JSON output template for score, topic, key_phrase, reasoning), and `SAMPLE_SPEECHES` (10 sample Fed speech excerpts for testing: 3 hawkish, 2 neutral, 5 dovish). Also includes `TOPIC_EXAMPLES` dictionary mapping topics to relevant keywords.



### event_dictionary.json

Pre-built dictionary of all available economic events from Trading Economics API. Generated by `scripts/build_event_dictionary.py`. Structure: `by_country` (country → {event_name → {category, importance, ticker, example_date}}), `by_event` (event_name → [countries]), `metadata` (generated_at, countries_count, unique_events, total_country_event_pairs). Used as reference for exact event naming in queries without repeated API calls. Covers all tracked countries including US, UK, EU, Japan, China, etc.



### show_all_scheduled.py

Simple utility script to display all scheduled events from the SQLite database. Connects to `workspace/scheduled_events.db`, prints table schema, then lists all records from `scheduled_events` table with full row details. Used for debugging and monitoring the scheduled event runner.



### fetch_us_events.py

Bulk data loader for US economic events. Fetches all US events from 2020-01-01 to present using Trading Economics API via `fetch_economic_calendar()`. Performs full database refresh with `full_refresh=True`. Reports events fetched, inserted, updated, and filtered. Provides sample verification commands after completion. Typical runtime: 2-3 minutes.



### build_event_dictionary.py

Builds comprehensive dictionary of all available economic events from Trading Economics API. Fetches events for all tracked countries (`ALL_TRACKED_COUNTRIES`) over a rolling 37-day window. Produces JSON dictionary with two views: `by_country` (country → event_name → details) and `by_event` (event_name → [countries]). Saves to `config/event_dictionary.json`. Includes metadata (counts, generation timestamp). Shows sample US events and common events appearing in 5+ countries. Used as reference for exact event naming without repeated API calls.



### list_event_names.py

Simple utility to list all unique US event names from the economic events database. Queries `SELECT DISTINCT event_name FROM economic_events WHERE country LIKE '%United States%' ORDER BY event_name`. Prints one event name per line. Useful for discovering available event names for analysis scripts.



### powell_speech_manual.py

Manual input version of Powell speech sentiment analyzer. Accepts paste-in transcript text via multi-line input (end with '.' or '---'). Uses GPT-4o with full conversation history for context-aware sentiment analysis. Features: `FedSentimentAnalyzer` class with conversation history tracking (can use full history or compressed summary), `ConsoleDisplay` class with colored output, `SentimentState` class for running EMA score tracking. Supports Telegram alerts (`--telegram`). Commands during session: 'q' quit, 's' status, 'h' history, 'save'. Detects speaker (Powell vs Question) and skips reporter questions. Outputs JSON to workspace with meta, summary, and chunk details. Dependencies: openai.



## GEPA Optimization Scripts (`scripts/helpers/`)



### planner1_gepa_opt.py

DSPy/GEPA optimization harness for Planner 1 (task decomposition). Optimizes `PLANNER1_PROMPT_CORE` from `taskmaster_client.py` using genetic evolution of prompts. Contains 16 training examples across 6 categories: candlestick charts, correlation analysis, timeseries visualization, price distribution, event distribution, and complex multi-step queries. Uses `planner_metrics.py` for structural evaluation (DAG validity, schema validity, agent params) and LLM-as-judge scoring (coverage, granularity, dependency precision, faithfulness, agent mapping, candlestick compliance, list-aware tool compliance). Integrates with Weights & Biases for experiment tracking. CLI: `--model`, `--max-full-evals`, `--num-threads`, `--wandb-project`, `--wandb-run-name`. Outputs optimized prompt to `scripts/gepa_logs_planner1/optimized_planner1_prompt.txt`.



### planner2_gepa_opt.py

DSPy/GEPA optimization harness for Planner 2 (tool selection). Optimizes `PLANNER2_PROMPT_CORE` from `taskmaster_client.py` for tool selection and parameter mapping. Uses `planner2_metrics.py` for evaluation: schema validity, tool selection validity, parameter completeness, coverage. Contains training examples for market data queries, polymarket search, event data queries, analytics tools. Integrates with Weights & Biases for experiment tracking. CLI: `--model`, `--max-full-evals`, `--num-threads`, `--wandb-project`, `--wandb-run-name`. Outputs optimized prompt to `scripts/gepa_logs_planner2/optimized_planner2_prompt.txt`.



## Test Scripts (`test_scripts/`)



Standalone test scripts for validating various components. Not part of the main pipeline.



- **test_analytics.py** - Test script for Analytics Agent capabilities

- **test_bbg_watcher.py** - Test script for Bloomberg Excel watcher

- **test_candlestick.py** - Test script for candlestick chart generation

- **test_db_query.py** - Test script for database queries

- **test_eventdata.py** - Test script for event data tools

- **test_orchestrator.py** - CLI for testing Orchestrator Agent (18 sample queries)

- **test_polymarket_unified.py** - Test script for unified Polymarket agent

- **test_postgres.py** - Test script for PostgreSQL connection

- **test_queries.py** - CLI for testing market data queries

- **test_telegram_trigger.py** - Test script for Telegram triggers



## Ad-Hoc Analysis Scripts (`tinkering/`)



This folder contains exploratory and one-off analysis scripts that are NOT part of the main orchestrator pipeline. These scripts are used for research, debugging, and prototyping.



### Predictor/Model Exploration

- **analyze_all_predictors.py** - Ad-hoc analysis of all predictors for B/E multiplier

- **analyze_predictors.py** - Ad-hoc predictor analysis (cons_std, surprise percentile → B/E)

- **be_grouping.py** - Simple B/E grouping by cons_std to check relationship

- **be_prediction_model.py** - B/E multiplier prediction model exploration (FWD percentile, FWD/ATR, trend strength)

- **regression_analysis.py** - Regression analysis of B/E multiplier on surprise percentile and cons_std



### Bucket/Distribution Analysis

- **bucket_analysis_all.py** - Comprehensive bucket analysis for cons_std and surprise percentile

- **bucket_analysis_with_dates.py** - Bucket analysis with event dates visible

- **bucket_by_ur_std.py** - Bucket analysis by unemployment rate std dev

- **consensus_bucket_analysis.py** - Consensus-based bucket analysis



### Interactive Explorers

- **interactive_bucket_analysis.py** - Interactive HTML bucket analysis with hover details

- **interactive_extension_analysis.py** - Interactive extension/reversion analysis

- **interactive_surprise_click.py** - Interactive surprise click analysis

- **interactive_surprise_distribution.py** - Interactive surprise distribution explorer



### NFP Exploration

- **nfp_pre_post_heatmap.py** - NFP pre/post event B/E heatmap (cons_std × surprise percentile)

- **nfp_scenario_lookup.py** - NFP scenario lookup tool for hypothetical actual values

- **nfp_uer_4x4_postevent_grid.py** - Builds a standalone interactive HTML 4x4 grid of NFP vs UER surprise-percentile buckets (25% width) using `bbg_data` + TY OHLCV; each cell shows t->EOD mean/P5/P50/P95, mean intraday max-up/max-down, and supports click-to-view normalized post-event price paths with a mean overlay.

- **nfp_uer_exploration_preevent.py** - NFP + UER combined analysis (pre-event focused)

- **nfp_uer_exploration.py** - Comprehensive NFP + UER combined uncertainty exploration with PCA/K-Means

- **nfp_ur_3x3_heatmap.py** - NFP + UR 3x3 heatmap visualization

- **nfp_ur_3x3_preevent.py** - NFP + UR 3x3 pre-event analysis

- **nfp_similarity_timeseries.py** - Matches similar **pre-event drift** days using nearest-neighbors on (a) baseline features (month + NFP/UR/AHE cons_std) vs (b) augmented **lag/seasonality** features; scores similarity by path RMSE/corr/DTW and outputs an interactive HTML report + features CSV.



### Surprise/Reversion Analysis

- **extension_reversion_analysis.py** - Price extension → mean-reversion probability analysis

- **surprise_mean_reversion_analysis.py** - Surprise → mean-reversion behavior analysis

- **surprise_predictive_power_analysis.py** - Surprise predictive power analysis (correlation, buckets, R²)



### Pre-event Analysis Variants

- **preevent_be_divergence_boxplot.py** - Pre-event B/E divergence boxplot analysis

- **preevent_be_ur_boxplot.py** - Pre-event B/E with UR boxplot analysis

- **preevent_dashboard.py** - Older pre-event dashboard variant (superseded by preevent_boxplot_dashboard.py)

- **preevent_postevent_correlation.py** - Pre-event to post-event correlation analysis

- **preevent_technicals.py** - Pre-event technical analysis (ATR, range compression, trend)

- **live_preevent_dashboard_2025.py** - 2025-only variant of live pre-event dashboard (experimental)



### Debug/Verification Scripts

- **debug_context.py** - Debug orchestrator context building

- **debug_events_wiring.py** - Debug events wiring in orchestrator

- **verify_cpi_query.py** - Verify CPI query results

- **verify_dashboard_data.py** - Verify dashboard data correctness

- **verify_preevent_data.py** - Verify pre-event data loading

- **check_data_sources.py** - Check available data sources

- **check_execution_plan.py** - Check orchestrator execution plan

- **check_soft_deps.py** - Check soft dependencies resolution



### API/Integration Helpers

- **x_fetch_tweet.py** - Stdlib-only X API v2 tweet fetcher. Loads `X_BEARER_TOKEN` from `config/keys.env`, supports tweet ID or latest-by-username, custom fields/expansions, and prints JSON or a compact summary.



### Auction Supply Analysis

- **auction_supply_return_analysis.py** - Analyzes 20Y Treasury auction fields vs TY returns at 2h/4h/6h/8h/12h/24h horizons. Runs OLS regression, quantile bucket analysis, and generates distribution plots. Outputs: `auction_returns_joined.csv`, `regression_results.csv`, `bucket_analysis.csv`, `summary_report.json`, and histograms in `plots/` subdirectory.



### Fed Press Conference Transcripts

- **export_fed_press_conference_dates.py** - Queries `workspace/economic_events.db` for past "Fed Press Conference" events (United States, excludes future) and writes `workspace/fed/fomc conference/fed_press_conference_videos.csv` with `date` + blank `youtube_url` columns for user to fill.

- **fetch_fomc_video_urls.py** - Uses YouTube Data API v3 to search the official Federal Reserve channel (`UCTvLj7u9uMQvZqafcWrh5JQ`) for press conference videos matching each date in the CSV. Populates the `youtube_url` column for verification before transcript download. Requires `YOUTUBE_API_KEY` in `config/keys.env`.

- **fetch_fed_press_conference_transcripts.py** - Reads the above CSV, extracts video IDs from YouTube URLs, downloads timestamped transcripts via `youtube-transcript-api`, and writes JSON artifacts to `workspace/fed/fomc conference/transcripts/{date}_{video_id}.json` plus a run summary.

- **fomc_press_conference_sentiment_step1.py** - Scores each FOMC press conference transcript chunk (hawkish↔dovish) using GPT and computes a 15-minute running score. Fetches TY 1m OHLCV from PostgreSQL and computes per-chunk price deltas (ticks/bps via `rules/treasury_price_conversion.py`) for same-window and lag-1m windows. Deterministically compares expected direction (from chunk_score and running_score) vs observed TY move. Outputs per-transcript JSON to `workspace/fed/fomc conference/sentiment/{date}_{video_id}.json` and aggregate `summary.csv`.

- **fomc_press_conference_sentiment_step2.py** - Calibrates a 5-class sentiment mapping using TY 1-minute price action as weak supervision. Combines chunk score + 15-minute running score, trains thresholds/weights, and outputs per-transcript JSON + summary CSV with calibrated labels and alert flags.

- **fomc_live_sentiment_alerts.py** - Real-time FOMC sentiment scoring + Telegram alerts during live press conferences. Three modes: `--replay <date>` replays historical transcript with simulated timing for testing; `--stdin` accepts pasted text from terminal; `--scrape` polls StreamText live captioning service (https://www.streamtext.net/player?event=CFI-FRB). Sends alerts on extremes (±6), meaningful running-score shifts (±3), or 3+ consecutive same-direction chunks.

- **fomc_qa_microstructure_alignment.py** - Offline answer-level event-study pipeline for FOMC Q&A microstructure. Segments transcripts into reporter question + Powell answer blocks, anchors on answer start, aligns each answer to `0-30s`, `30s-2m`, and `0-10m` windows, then computes signed `delta_bps`, realized-vol spikes vs pre-answer rolling baselines, and direction-consistency labels (`trend`, `mean_revert`, `mixed`). Writes per-transcript JSON plus aggregate CSV in `workspace/fed/fomc conference/qa_microstructure/`.

- **fomc_meetings_dashboard.py** - Generates an interactive HTML dashboard for FOMC press conferences with: (1) filter pills for month-end meetings (last 7 days of month) and hawkish/dovish sentiment, (2) normalized TY price paths in bps (T-60m to T+180m), (3) click-modal with candlestick chart + economic event annotations + web-derived summary, (4) return distribution plots for pre/during windows. Uses Perplexity API for sentiment labeling with local JSON cache. CLI: `--force-refresh` re-fetches web sentiments, `--output` specifies output path. Outputs HTML to `workspace/scenarios/fomc_meetings_dashboard_{timestamp}.html`.

- **jolts_event_study_dashboard.py** - Generates an interactive HTML dashboard comparing JOLTS days that follow ISM Manufacturing PMI ("with_ism") vs standalone JOLTS days. Features: (1) toggle between JOLTS modes and pre-window lengths (30m/1h/2h/4h), (2) normalized TY price paths with click-to-candlestick modal, (3) average event-time profiles per mode, (4) distribution plots + binned analysis by ISM surprise or JOLTS cons-std buckets. All conversions use `rules/treasury_price_conversion.py`. CLI: `--symbol`, `--start-date`, `--end-date`, `--windows`, `--out`. Outputs HTML to `workspace/scenarios/jolts_event_study_dashboard_{timestamp}.html`.

- **verify_jolts_event_study.py** - Sanity-checks JOLTS event study data: verifies JOLTS and ISM event counts in the DB, checks mode distribution (with_ism vs standalone), validates timestamp ordering, and reports consensus_std_dev availability. CLI: `--start-date`. Run before generating the dashboard to ensure data quality.



### One-Off Exports/Reports

- **export_nfp_event_data.py** - Export NFP event day OHLC data from PostgreSQL to SQLite

- **export_ohlc_to_sqlite.py** - Export all OHLC data from PostgreSQL to SQLite

- **auction_events_december_report.py** - Generate December auction events report with candlestick charts

- **fundamental_scoring.py** - Ad-hoc fundamental macro scoring exploration



## Documentation (`docs/`)

### aggression_v2_lifo_zone_experiments.md

Offline 2026-09-07 study of LIFO zones (15m two-way box, ≥3-tick meat, |net|<8) vs frozen H5 and other flow flags. H5 *lowers* zone rate (−4.3pp validation). Quiet leftover is not a 3-tick clip environment. `ship_gate` false. Script: `scripts/aggression_v2_lifo_zone_research.py`. Tests: `tests/test_v2_lifo_zone.py`. L1–L4 run: `docs/aggression_v2_lifo_literature_experiments.tex`.

### aggression_v2_lifo_literature_experiments.tex

Preregistered 2026-09-08 offline follow-ups plus the 2026-09-08 run. Track P: same-bar R² ~0.25, lead-1 ~0. L1/L3 FAIL. L2 zone PASS (+8.3pp) but fade clock FAIL (48%) and absorb_bar alone fails — not a ship. `ship_gate` false.

### aggression_v2_lifo_literature_research.py

Offline L1–L4 + Track P/F on the published TBBO matrix. Reuses Branch-0 labels from `aggression_v2_lifo_zone_research.py`. Writes only `workspace/analysis/aggression_v2_lifo_literature/`. Tests: `tests/test_v2_lifo_literature.py`. Does not touch live monitors or H5.

### aggression_v2_lifo_flow_forecast_research.py

Offline diagnostic: past signed flow over \(W\in\{1,5,15,30\}\) → future flow over \(H\in\{1,5,15\}\), then frozen Cont \(\lambda\times\hat F\) vs direct future \(\Delta P\sim\) past flow. Writes only `workspace/analysis/aggression_v2_lifo_flow_forecast/`. Tests: `tests/test_v2_lifo_flow_forecast.py`. `ship_gate` false. 2026-09-08: flow→flow \(R^2\le 0.003\); oracle price map \(0.25/0.40/0.52\); two-stage does not help.

### aggression_v2_lifo_path_followup_research.py

Offline trail-meat / range-persistence / meat-vs-two-way / fade-without-H5 / session / run-end study. Writes only `workspace/analysis/aggression_v2_lifo_path_followup/`. Tests: `tests/test_v2_lifo_path_followup.py`. 2026-09-08: trail meat letters meat only; trail two-way fails validation; fade 53%; US morning letters zone. `ship_gate` false.

### aggression_v22_v27_from_v1.tex

Companion LaTeX note to the V1 measurement document. Walks a first-time reader from V1's residual (as description) through the V2 session-Ridge step, then specifies isolated V2.2 (session fade clock + H5 LIFO FRIENDLY) and V2.7 (B1_W10 local nowcast + conviction gate) with goals, inputs, outputs, definitions, and processing. Does not retune live monitors.

### unified_dashboard_plan.md

Plan and scope for the unified dashboard, including current state, milestones, acceptance criteria, and risks.



### unified_dashboard_runbook.md

Operational runbook for unified dashboard backfill and daily incremental updates.



### pre_event_standalone_plan.md

Standalone pre-event HTML dashboard plan. Documents the current panels, controls, embedded data, and edit levers for the generated pre-event HTML, plus iteration workflow and generator reference.



### fomc_press_conference_sentiment_plan.md

Plan for FOMC press conference transcript sentiment: Step 1 (LLM scoring + deterministic alignment checks) and Step 2 (1-minute windows, 5-class labels, and **mapping calibration trained using TY price action** as weak supervision), plus Telegram alert rules (extremes and meaningful changes only).



### bbg_alerts_channel_setup.md

Short runbook for routing BBG data-health alerts to a dedicated Telegram channel. Covers channel creation, bot admin setup, choosing a channel username or numeric ID, setting `BBG_ALERT_CHAT_ID` in `config/keys.env`, and notes that stale workbook checks now log locally instead of sending Telegram messages.

## Aggression Autoresearch Infrastructure

### `scripts/aggression_autoresearch_data_build.py`

Research-only Phase 0 TBBO snapshot builder. It verifies explicit quarterly contracts, selects fronts from prior-complete-session volume, removes and attributes exact duplicates, records empty/incomplete dates as exclusions, supports contract-stable parity benchmark ranges, and publishes only complete checksummed shards. The authoritative dense campaign publication ends 2026-02-26; the live-consumed legacy matrix is never overwritten.

### `scripts/autoresearch/`

Cooperative, tamper-evident research harness. Schema v2 plus append-only `ExecutionContractV1` represent predictive/descriptive methodology, exact executable preprocessing, feature-map candidate output, evaluator-owned overlapping cells, detached result bundles, and hash bindings. `fixed_env/discovery.py` executes discovery, while `fixed_env/validation.py` executes separately authorized one-shot validation and non-evidentiary quarantine from frozen state. Matrix, evaluator, result, and transactional-state layers enforce data separation, fixed inference, atomic exposure, canonical publication, and crash-safe retries.

### `workspace/analysis/aggression_autoresearch_campaign/results/`

Immutable discovery, validation, and quarantine bundles plus `research_branching_v1.json`. E001/E002 validation is transform-only and `not_confirmed`; quarantine is explicitly non-evidentiary and cannot overwrite those decisions. The branching artifact closes both predictive families and keeps D001's two nominations proposal-only.

### `scripts/autoresearch/fixed_env/validation.py`

Fail-closed historical holdout runner. It verifies append-only authorization and frozen discovery bindings, reserves/ledgers exposures before outcomes load, applies frozen candidate/logit state, runs registered cells with 9,999-resample inference and serial shifts, reports E002 delayed-entry economics, and publishes immutable validation or diagnostic-only quarantine bundles.

### `scripts/autoresearch/candidates/{e001,e002,d001}.py`

Target-free candidate modules for residual oscillation, frozen-FV formation, and dominance mapping. They receive only allowlisted formation rows, emit exact registered finite feature maps, freeze discovery preprocessing in runner-bound state, and never construct cells, direction, future outcomes, or PnL.

### `scripts/autoresearch/registry/execution_contracts/`

Append-only executable addenda bound to the original frozen Phase 2 spec hashes. They freeze ordered model inputs, imputation/scaling, E001 residual fitting, E002 nested ridge/lookback selection, and D001's fixed four-tick descriptive outcome without modifying the original methodology files.

### `docs/autoresearch_phase4_execution_plan.md`

Plan-only staged roadmap from the verified Phase 3 execution layer to real E001/E002/D001 runs. Details the authorized `run-discovery`/`run-validation` command design, fold/anchor/outcome wiring, state sealing, power gating, one-shot validation, quarantine diagnostics, and confirmation, with append-only manifest v3/v4 authorization gates. Nothing in it authorizes execution.

### `docs/autoresearch_campaign_design.md`

Authoritative paper-style methodology for the frozen design-only campaign. It records the dense data cutoff and coverage limitation, exact cohorts/hashes, common inference/power/usefulness rules, complete E001/E002/D001 designs and gates, confirmation policy, and the no-execution/no-live boundary.

### `tests/test_autoresearch_*.py`

Synthetic/mocked coverage for the data builder, protocol, execution contracts, checksummed matrix adapter, candidates, nested selection, predictive gates, D001 map, validation authorization, transform-only quarantine, sparse-session serial shifts, finite result serialization, transactional one-shot exposure/publication, attempt caps, approval, and confirmation retirement.

## Autoresearch Hypothesis Lab (`scripts/autoresearch_lab/`)

### `__init__.py`

Package marker and semantic version for the isolated Hypothesis Lab. It intentionally exports only `__version__` and has no startup or campaign side effects.

### `app.py`

Manual-only Flask entrypoint on port `5057`, with no-cache responses and a background warm-up of the checksum-bound discovery matrix. It serves read-only evidence, sandbox metadata/jobs/journal, preregistration authoring, and holdout-availability APIs; no holdout execution route exists.

### `lab_readers.py`

Path-contained readers for immutable JSON/YAML campaign artifacts and the active exposure ledger. It opens `campaign.sqlite` with SQLite `mode=ro` and `query_only=ON` (without `immutable=1`), aggregates runs/exposures/receipts, and never falls back to the empty `campaign_state.sqlite` freeze stub.

### `lab_integrity.py`

Recomputes SHA-256 bindings across specs, authorizations, execution manifests, results, branching inputs, and publication metadata. It also reports the declared-ledger/runtime-ledger mismatch as an explicit warning while keeping integrity failures separate.

### `lab_evidence.py`

Projects frozen D001/E001/E002 artifacts into dashboard-ready evidence without recomputing research results. It expands every predictive cell into the eight preregistered gates, enriches all 243 D001 cells for filtering, and attaches cohorts, ledger, registry, preflight, and integrity views.

### `lab_matrix.py`

`DiscoveryOnlyMatrix` is the compute firewall around the frozen matrix adapter: all non-discovery splits and dates outside the assigned discovery cohort raise. The singleton validates publication/roll hashes, caches the full discovery slice once, and exposes only registered candidate/evaluator columns.

### `lab_outcomes.py`

Parameterized discovery outcomes compose the hash-bound primitives from `fixed_env/outcomes.py` without editing that file. It provides bounded excursion, convergence first passage, directional first passage, and strict future-window slicing with validated parameters.

### `lab_sandbox.py`

Discovery-only scoring pipeline for frozen E001/E002/D001 candidates or isolated custom candidate modules. It uses a deterministic 70/30 chronological discovery sub-split, evaluator-owned outcomes/anchors, frozen-style logits, 999 clustered resamples, optional 999 circular shifts, registered slices, and always returns a non-evidentiary exploratory result.

### `lab_jobs.py`

Thread-safe in-memory registry for queued, running, completed, and failed sandbox jobs. Background workers report stage/percent progress, invoke the sandbox, and append both successes and failures to the audit journal.

### `lab_journal.py`

Append-only JSONL journal for every sandbox attempt under `workspace/analysis/autoresearch_lab/journal/`. It hashes requests/results, replaces custom source text with a source digest, tracks session and historical multiplicity, and retrieves the exact search history linked to a preregistration.

### `lab_prereg.py`

Turns a journal-bound discovery result into validated `PredictiveExperimentSpecV2` and `ExecutionContractV1` files with canonical/effective hashes and an exposure precheck. It freezes files only under the lab preregistration root and emits a non-authorizing manifest scaffold plus blocked future CLI template; it cannot open a holdout.

### `lab_holdout.py`

Read-only explanation layer for registered experiments, frozen lab drafts, and blocked hypothesis families. Every returned item is unavailable and carries concrete reasons such as completed identities, closed branching, empty prospective cohorts, or missing executor/manifest authorization.

### `templates/index.html`

Three-lane dashboard shell for Evidence, Discovery Sandbox, and Preregister views. It keeps the exploratory/non-evidentiary label visible and delegates data loading and interaction to the versioned static assets.

### `static/dashboard.js`

Client controller for loading campaign evidence, rendering gate tables/D001 Plotly maps/ledger data, submitting and polling sandbox jobs, and freezing selected results as preregistration drafts. It builds only `split="discovery"` sandbox requests and presents holdout unavailability instead of an execution control.

### `static/dashboard.css`

Responsive dark-theme styles for campaign summaries, provenance notices, gate tables, filters, charts, forms, progress indicators, and holdout cards. The layout supports the dense evidence tables while keeping research-status labels visually prominent.

### `README.md`

Operator runbook for the manual-only port-5057 lab, its three lanes, and its research boundaries. It documents the sole writable root, generated artifact layout, launch command, cache warm-up behavior, and focused verification command.

### `tests/test_autoresearch_lab.py`

Focused unit/regression coverage for the split firewall, frozen-outcome equivalence, hash-bound-file preservation, forbidden custom source, tamper detection, real ledger selection, exposure checks, no-state-write guarantee, evidence projection, preregistration hash binding, and absent holdout path. The tests use real frozen bundles where useful while keeping campaign state byte-identical.



### macro_event_analysis_state.md

Comprehensive current-state inventory for all event-driven macro analysis in the repo. Covers headline macro releases (NFP/CPI/PPI), secondary releases (JOLTS/ISM), Fed/FOMC/Powell workflows, Treasury supply/auction dashboards, calendar effects, policy/geopolitical dashboards, live dashboard and Telegram infrastructure, and tinkering/ad-hoc scripts. For each family, records generator paths, output artifacts, data sources, analysis depth, status, known limits, and candidate next builds.



### jolts_labour_playbook.md

Analyst knowledge-transfer playbook for JOLTS and the broader US labour-market complex. Explains JOLTS internals, data lag/revisions, Fed-relevant metrics such as V/U and the Beveridge curve, when JOLTS matters for TY/rates, how to cross-check it against NFP/claims/ADP/ECI/ISM/Challenger, and how to run the repo's JOLTS post-event and JOLTS-ISM event-study dashboards.



### aggression_v2_signal_research_summary.md

Research summary for the TBBO-backed aggression V2 signal work. Documents the daily-demeaned fair-value target, residual fade signal definition, exploratory conditional results, chronological holdout degradation, limitations, and the current recommendation not to deploy V2 as a live trading signal until more independent TBBO dates validate the rule.



## Cursor Commands (`.cursor/commands/`)



### pre_event.md

Playbook for generating a **standalone pre-event HTML dashboard** for a user-specified event type using `scripts/live_unified_dashboard.py` in `--embed` + `--preevent-only` mode (historical-only by default).



### post_event.md

Playbook for creating event-specific post-event dashboards by copying `scripts/ism_services_pmi_postevent_dashboard.py` and adjusting defaults; emphasizes normalized surprise, percentile bucketing, “all-events vs bucket-matched” comparisons, and default horizons t+1h/t+2h/t→EOD (5:00pm ET same day).



### distro_adhoc.md

Playbook for generating distro / histogram plots of returns around an event using `scripts/treasury_refunding_distribution.py`, including required stats-box conventions and last-two-event markers.



## Memory Bank (`memory-bank/`)



### progress.md

Lightweight changelog of recent work and outcomes. Keep entries short with date and a concise summary of updates made.


## Move Exhaustion V2 Research Harness (`scripts/move_exhaustion_v2/`)

### research/PROGRAM.md

Frozen program of record for the Move Exhaustion V2 information-content study. Scientific changes require user-approved §10 addenda plus config re-hash. The 2026-08-11 X0/X1 addendum authorizes one 2022–2026 replacement MBP-1 batch, Discovery 2022–2024, Validation 2025, a locked 2026 holdout, fixed tick B/x parameters, and explicit deferral of the under-specified variants. `OPEN_QUESTIONS.md` retains all decisions/audit history; seven non-X0/X1 questions remain open.

### experiments/X*/CONTRACT.md

Eleven audit-preserved contracts: X0/X1 remain FROZEN prerequisites; X2–X10 are `RETIRED_TO_BACKLOG`. Preregistered-science sections remain hash-pinned; metadata/run references may append.

### config/frozen/ and tools/freeze_manifest.py

Machine-readable frozen scientific surface: fixed tick B/x grids, 3-class wall-clock R/C/H labels, approved 2022–2026 split rules with holdout lock, statistical gates, and the exact authorized replacement-batch/session/QC specification. `freeze_manifest.py` pins all YAMLs, contract science sections, and PROGRAM.md sections 0–9.

### tests/

Mechanical harness tests (`py -3 -m pytest scripts/move_exhaustion_v2/tests`): frozen-surface hash integrity, config schemas, OQ reference resolution, split chronology/disjointness/embargo, holdout lock plus artifact scans for holdout-date leakage, and temporal-leakage scaffolds that activate when Phase 0/1/2 modules land (`data/mbp1_store_builder.py`, `episodes/builder.py`, `features/leakage.py`).

### AGENTS.md (repo root)

Repo-wide agent instructions: live-surface isolation invariants, source-of-truth map, Move Exhaustion V2 scientific invariants, allowed/forbidden modifications, and the required per-experiment workflow.

### research/loop/ (added 2026-08-11)

Advisor/worker autoresearch loop. `LOOP_PROTOCOL.md` defines roles (advisor orchestrates, workers execute one brief per cycle, human at checkpoints), the two evidence tiers (EXPLORATORY = discovery-only / NOT EVIDENCE; CONFIRMATORY = human-approved frozen contract + gates), the cycle lifecycle (BRIEF -> RESULT -> FEEDBACK -> ITERATE/PARKED/PROMOTE_REQUEST/ESCALATED), and the promotion protocol. `BACKLOG.md` is the advisor-owned idea queue (BL-1..BL-15, seeded from retired X2-X10 + spec themes). `journal/loop_journal.jsonl` is the append-only event log written via `tools/loop_journal.py` (sequential seq, actor/event vocabulary, validated by tests). `cycles/C###_<slug>/` folders hold per-cycle BRIEF/RESULT/FEEDBACK/meta.json. `tests/test_loop_integrity.py` mechanically enforces journal validity, cycle-folder shape, discovery-only data for exploratory cycles, and that retired contracts (X2-X10) carry no evidentiary status while X0/X1 stay FROZEN. Loop-hardening additions (2026-08-11): `AGENT_ROSTER.md` (user-directed model assignment: workers = DeepSeek `deepseek-v4-flash` via Codex CLI — invoke `npx --yes @openai/codex exec` (not on PATH; verified working, provider `deepseek` from `~/.codex/config.toml`); advisors = dual panel Fable 5 + GPT Sol, session parent chairs, other reviews as subagent; independent opinions consolidated by chair, disagreement resolves conservatively, PROMOTE_REQUEST requires unanimity, solo review needs `solo_review_reason` and can never promote; mandatory `worker_model`/`worker_agent_id`/`advisor_models` in meta.json; replication-before-promotion rule), `SCOREBOARD.md` (priority-question status, candidate-findings ledger vs the X1 price-only baseline, loop-health KPI table), `PLAYBOOK.md` (advisor-editable process memory fed by retros), and LOOP_PROTOCOL section 9 mandatory retrospectives (`retros/R###.md`, one per 5 closed cycles, journal events `retro`/`process_change`; retros may change process but never the frozen scientific surface). Tests additionally enforce worker_model + headline_metrics on completed cycles and the retro cadence.

### data/event_semantics.py, mbp1_store_builder.py (updated 2026-08-12)

MBP-1 event classes for the X0 store. Databento action `F` (fill) is classified as a trade with the same aggressor map as `T`. Signed modifies with no L1 price/size/count change map to `MOD_UNKNOWN` (priority/time change), not a fail-closed error. Unknown actions/sides and modify-before-established-L1 still fail closed.

### data/x0_run.py (updated 2026-08-13)

X0 execution CLI. `volume` / `pilot` / `store` as before. `replay-qc` re-evaluates canonical Q1 and Globex Q3 last-event bounds on `build_state.json` without rewriting parquets. `parity` runs Q7/Q8 vs the frozen TBBO matrix (price-mismatch dates excluded from comparable overlap). `finalize` freezes eligible dates and splits after X0 PASS. D: writes are rejected. Holdout stays closed.

### data/x0_parity.py / data/x0_qc_replay.py / data/qc.py (updated 2026-08-13)

Q7/Q8 comparable overlap requires a 1m panel: sparse matrix days and dates whose store mid vs matrix price exceeds 1 tick (2026-02-24/25 roll mismatch) are recorded and dropped from the gate. Q3 last-event bound accepts calendar close, nominal 17:00 ET, or the 18:00 ET Globex wrap. Annual 95% rate is the Q1–Q3 hard-gate pass rate (Q4–Q6 remain review flags). QC replay sets canonical-store Q1 pass.

### x1_run.py (added 2026-08-13)

X1 price-only baseline runner. Requires X0 PASS. Reads discovery+validation store days only, never holdout parquet. Primary cell B=8/x=2/R=4/C=4/H=15; optional 4×4 B×x grid. Writes per-date shards under `days/B*_x*/` then combined labeled parquet + metrics; resume concatenates shards so checkpointed days are not dropped. Writes `workspace/analysis/move_exhaustion_v2/experiments/X1_<run_id>/`.

### research/loop/specs/BL3_impact_shortfall_spec.md (added 2026-08-13)

Exploratory BL-3 expected-impact spec, prefix-only. Pins OLS `Δm_W ~ AF_W` on the last 100 pre-t* store rows, walk-forward IS, E07 corr/Δm-tercile cross-check. Not a frozen X4 contract. Approved and run as C014; PARKED (IS ≈ −Δm).

### workspace/analysis/move_exhaustion_v2/loop/C016_event_day/run_c016.py (added 2026-08-13)

C016: event vs non-event day from read-only `economic_events.db` (US high-importance + E16 auction names). Session cross-check. Discovery prefix only.

### workspace/analysis/move_exhaustion_v2/loop/C017_direction/run_c017.py (added 2026-08-13)

C017: up vs down P(REVERSAL) on labeled parquet. Session cross-check. Labels-only.

### workspace/analysis/move_exhaustion_v2/loop/C018_vol_regime/run_c018.py (added 2026-08-13)

C018: 30m pre-t* mid range terciles vs reversal. Store `mid`/`ts_event` only. Session cross-check.

### research/loop/retros/R003.md (added 2026-08-13)

Third loop retrospective (C011–C015). Adopts session-before-flow: future flow briefs must control RTH vs non-RTH. Descriptive ADVANCE does not spawn AF×TOD on this prefix.

### workspace/analysis/move_exhaustion_v2/loop/C015_tod_baseline/run_c015.py (added 2026-08-13)

C015 worker: ET session buckets from `t_star_ts_event`; RTH vs non-RTH reversal contrast; date-clustered bootstrap. Labels-only. Discovery prefix only.

### workspace/analysis/move_exhaustion_v2/loop/C014_impact_shortfall/run_c014.py (added 2026-08-13)

C014 worker: last-100 LOB lookback AF_W/Δm_W, expanding OLS walk-forward IS, date-clustered T3−T1, E07 corr and Δm-tercile cross-check. Writes `episodes_c014.csv` + `metrics.json`. Discovery prefix only.
