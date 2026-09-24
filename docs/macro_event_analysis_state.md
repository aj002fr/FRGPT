# Macro Event Analysis Current State

Last updated: 2026-05-19

## Purpose

This is the current-state map for event-driven macro analysis in this repo. It is meant to answer:

- Which events have already been covered.
- Which dashboards, standalone HTML files, data exports, and live workflows exist.
- What data sources those artifacts use.
- What level of analysis has already been implemented.
- Where the obvious next build steps are.

## Status Legend

- **Production-live**: Telegram-wired, scheduled, live-triggered, or part of the active unified dashboard / bot path.
- **Standalone-prototype**: generator works and emits a shareable HTML or artifacts, but it is not yet fully live-triggered.
- **One-off / tinkering**: exploratory analysis, generally under `tinkering/` or `workspace/analysis/`, not maintained as a production workflow.
- **Stale / reference**: useful artifact or legacy HTML, but probably not regenerated recently or superseded by newer code.

## Core Data Surfaces

Most event work pulls from a small set of shared stores:

- **Market data**: PostgreSQL `ohlcv_1m`, usually symbol `TY`, with conversion utilities in `rules/treasury_price_conversion.py`.
- **Event metadata**: `workspace/economic_events.db`, especially `economic_events`, `bbg_data`, event annotations, and trader tags.
- **Bloomberg event fields**: shared workbook `\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx`; older scripts also reference `Z:/Meini/NFP.xlsx` and `Z:/Meini/CPI.xlsx`.
- **Weekly calendar input**: Meini weekly calendar workbooks parsed by `data_setup/ingest_excel_calendar_to_sqlite.py`.
- **Live price / snapshot data**: snapshot DBs under `X:\uikitxv2\data\snapshots\snapshots_YYYY-MM-DD.db` or Databento-backed live state depending on the workflow.
- **Polymarket data**: `polymarket_markets.db`, `polymarket_stream.db`, and live Gamma/CLOB API resolution.
- **Fed transcripts**: `workspace/fed/fomc conference/transcripts/`, sentiment output folders, and FOMC Q&A microstructure outputs.

## Headline Macro Releases

### NFP / Non Farm Payrolls

**Main generators and artifacts**

- `scripts/nfp_postevent_dashboard.py`
- `scripts/non_farm_payrolls_postevent_dashboard.py`
- `scripts/nfp_twofactor_pca_dashboard.py`
- `scripts/preevent_boxplot_dashboard.py`
- `scripts/build_preevent_standalone_html.py`
- `scripts/preevent_standalone_dashboard.py`
- `scripts/nfp_data_loader.py`
- `scripts/nfp_live_trigger.py`
- `scripts/bbg_excel_watcher.py`
- `workspace/scenarios/live_unified_nfp_dashboard_*.html`
- `workspace/scenarios/preevent_nfp_dashboard_*.html`
- `workspace/scenarios/nfp_postevent_dashboard_*.html`
- `workspace/scenarios/non_farm_payrolls_postevent_dashboard_*.html`
- `workspace/scenarios/nfp_twofactor_pca_dashboard_20260213_092437.html`
- `workspace/analysis/nfp_postevent/nfp_postevent_summary.json`
- `workspace/analysis/nfp_similarity_timeseries.html`
- `workspace/analysis/nfp_uer_exploration.html`
- `workspace/analysis/nfp_uer_exploration_preevent.html`

**Data used**

- Historical NFP and UER actual / consensus / std-dev fields from `bbg_event_fields.xlsx` and `workspace/economic_events.db`.
- Older flows also use `Z:/Meini/NFP.xlsx` for NFP std-dev and forward breakeven inputs.
- TY 1-minute OHLCV from PostgreSQL for pre-event paths, post-event returns, max extent, and candlesticks.
- Live trigger uses `Z:/Meini/NFP.xlsx`, latest snapshot DB, and options-implied forward breakeven.

**Analysis level**

- Pre-event B/E analysis by consensus-std buckets.
- Post-event B/E analysis by standardized surprise / percentile buckets.
- Normalized TY price paths, candlestick drilldowns, distribution panels, expected rally/selloff stats, and last-two-event markers.
- Live pre-event dashboard supports NFP with statistical envelope lines.
- Automated NFP post-event trigger computes surprise percentile, bucket-matched B/E stats, projected price paths, and Telegram summary.
- Two-factor PCA prototype uses NFP and UER factors:
  - pre-event: `[nfp_cons_std, uer_cons_std]` uncertainty vector, with consensus fallback if needed.
  - post-event: standardized NFP and UER surprises.
  - output includes PCA factors, regression-scored quantile buckets, factor attribution, clickable drilldowns, and standalone HTML.

**Status**

- **Production-live** for Telegram `/pre_event`, `/post_event`, `/live_pre_event`, NFP live trigger, and Bloomberg watcher paths.
- **Standalone-prototype** for NFP two-factor PCA and several generated standalone scenario HTML files.
- **One-off / tinkering** for NFP x UER grids, similarity timeseries, and older heatmap / scenario lookup scripts.

**Known limits / next steps**

- NFP is the most mature event family, but the analysis is split across legacy standalone scripts, live unified dashboard code, and tinkering notebooks/scripts.
- The two-factor PCA prototype is not yet regime-aware and likely needs tail-aware calibration before it is promoted.
- Live trigger is NFP-specific; a reusable event-trigger framework would let PPI, JOLTS, ISM, and FOMC use the same pattern.
- Polymarket scenario support is strongest for NFP/UER, but similar scenario ladders are not wired for PPI or FOMC.

### CPI

**Main generators and artifacts**

- `scripts/cpi_twofactor_pca_dashboard.py`
- `scripts/live_preevent_dashboard.py` in CPI mode.
- `scripts/live_unified_dashboard.py` in CPI mode.
- `scripts/dashboard_tabs/polymarket_scenarios_tab.py` CPI extension.
- `docs/cpi_twofactor_pca_plan.md`
- `workspace/scenarios/cpi_twofactor_pca_dashboard_*.html`
- `workspace/scenarios/live_unified_cpi_dashboard_*.html`
- `workspace/scenarios/preevent_cpi_dashboard_20260209_091029.html`
- `workspace/scenarios/_served_cpi.html`
- `workspace/scenarios/_served_cpi_after_split.html`
- `workspace/analysis/cpi_preevent/`
- `workspace/analysis/cpi_postevent/`
- `workspace/analysis/cpi_advanced/`, `cpi_corrected/`, `cpi_evidence/`, `cpi_exhaustive/`, `cpi_fomc_revision/`, `cpi_grouping/`, `cpi_pure_preevent/`

**Data used**

- Bloomberg workbook `bbg_event_fields.xlsx` for CPI component blocks.
- `workspace/economic_events.db` / `bbg_data` for CPI timestamps, actuals, consensus, and std-dev.
- Older live pre-event support references `Z:/Meini/CPI.xlsx`.
- TY OHLCV from PostgreSQL for 3pm T-1 to release pre-windows and 8:30am to 5pm post-windows.
- Polymarket CPI mode resolves inflation markets and uses CPI MoM analytics plus CPI YoY odds-only views.

**Analysis level**

- CPI two-factor PCA is the deepest CPI-specific artifact.
- It computes pre and post max-extent bps targets:
  - pre: `3:00pm T-1 -> 8:29am T`.
  - post: `8:30am T -> 5:00pm T`.
- Pre macro factors use `[mom_cons_std, core_mom_cons_std]`, with fallback to `[mom_consensus, core_mom_consensus]` when std-dev fields are flat.
- Pre market factors use trailing realized range / vol strictly before the pre-window start.
- Post factors use trend-adjusted surprises from rolling expected MoM based on YoY levels.
- UI supports PCA factors, regression scoring, quantile buckets, linear vs absolute-extent scoring, bucket rows, boxplots, normalized paths, distro histograms, bootstrap mean CI, hourly post-event B/E table, and candlestick drilldowns.

**Status**

- **Production-live / WIP** inside the unified dashboard and Polymarket scenarios tab.
- **Standalone-prototype** for the two-factor PCA dashboard.
- **One-off / tinkering** for the CPI exploration directories under `workspace/analysis/`.

**Known limits / next steps**

- `docs/cpi_twofactor_pca_plan.md` is the roadmap. It calls out small sample size, time-stationary PCA geometry, weak tail isolation, and missing positioning/state variables.
- Immediate next sprint from that plan:
  - add regime tags.
  - add regime-conditioned bucketing.
  - add tail metrics and `Pr(extent > X)` controls.
  - add one nonlinear term option with rolling OOS comparison.
  - add a basic CTA proxy from TY history.

### PPI / Core PPI / Supercore PPI

**Main generators and artifacts**

- `scripts/ppi_postevent_dashboard.py`
- `scripts/ppi_twofactor_pca_dashboard.py`
- `workspace/scenarios/ppi_twofactor_pca_dashboard_20260225_092538.html`
- `workspace/scenarios/ppi_twofactor_pca_dashboard_20260225_093132.html`
- `ppi_weeks_results.csv`

**Data used**

- PPI, Core PPI, and Supercore PPI blocks from `bbg_event_fields.xlsx`, `PPI` sheet.
- `workspace/economic_events.db` with DB event name `Core PPI MoM`.
- TY OHLCV from PostgreSQL.
- PPI loader support in `scripts/event_data_loader.py` uses DB-only defaults for std-dev and forward breakeven when workbook inputs are absent.

**Analysis level**

- Standalone post-event dashboard follows the same all-events vs bucket-matched distribution pattern used for NFP/JOLTS/ISM.
- Two-factor PCA dashboard mirrors CPI/NFP:
  - pre-event factors: `[ppi_cons_std, core_ppi_cons_std]`, with consensus fallback.
  - post-event factors: standardized PPI/Core PPI surprises, with Supercore incorporated through a historical-z proxy because Supercore lacks full consensus/std fields.
- Latest dashboard includes a current-input field for Supercore PPI MoM actual and runtime scoring against the historical proxy.

**Status**

- **Standalone-prototype**.
- PPI has serious modeling work started, but no obvious live trigger or Telegram scenario workflow yet.

**Known limits / next steps**

- Promote the PPI dashboard to unified dashboard event type if it is not already complete in the live UI.
- Build a PPI live trigger by generalizing `scripts/nfp_live_trigger.py`.
- Add Polymarket PPI scenario resolution if usable markets are available.

## Secondary Macro Releases

### JOLTS Job Openings

**Main generators and artifacts**

- `scripts/jolts_job_openings_postevent_dashboard.py`
- `scripts/jolts_event_study_dashboard.py`
- `scripts/verify_jolts_event_study.py`
- `workspace/scenarios/jolts_event_study_dashboard_*.html`
- `workspace/plots/JOLTS_Job_Openings_TY_*.html`
- `workspace/distro_runs/JOLTS_Job_Openings_TY_surprise_distribution.html`
- `workspace/results/jolts_TY_historical_*.json`
- `workspace/results/jolts_TY_historical_*.txt`

**Data used**

- JOLTS event history and consensus/std-dev from `workspace/economic_events.db`.
- TY OHLCV from PostgreSQL.
- Event-study version also joins ISM Manufacturing PMI proximity/mode to classify JOLTS days as standalone vs with-ISM.

**Analysis level**

- Post-event dashboard accepts actual/consensus, computes surprise percentile and bucket, and compares all-events vs similar events.
- Distribution horizons include t+10m, t+30m, t+1h, and t+2h.
- Event-study dashboard compares JOLTS after ISM Manufacturing PMI vs standalone JOLTS, with pre-window toggles, normalized paths, event-time profiles, distribution plots, and binned analysis by ISM surprise or JOLTS cons-std buckets.

**Status**

- **Standalone-prototype**.
- JOLTS is planned in the unified dashboard spec as a supported event type, but current production maturity appears lower than NFP/CPI.

**Known limits / next steps**

- Wire JOLTS into the same live post-event / Telegram command path as NFP.
- Use `scripts/verify_jolts_event_study.py` before regenerating dashboards because the event-study output depends on timestamp ordering and consensus std-dev coverage.

### ISM Manufacturing PMI

**Main generators and artifacts**

- `scripts/ism_manufacturing_pmi_pre_post_distribution.py`
- `workspace/analysis/ism_mfg_pmi_pre_TY.*`
- `workspace/analysis/ism_mfg_pmi_post_TY.png`
- `workspace/analysis/ism_mfg_pmi_pre_post_TY.json`

**Data used**

- ISM Manufacturing PMI release times from `workspace/economic_events.db`.
- TY 1-minute OHLCV from PostgreSQL.
- Conversion helpers from `rules/treasury_price_conversion.py`.

**Analysis level**

- Two distro-style plots:
  - pre window: t-2h to t.
  - post window: t to t+2h.
- Outputs PNG, JSON, and per-event CSVs.
- Stats boxes include expected rally/selloff and 0 bps percentile rank.

**Status**

- **Standalone-prototype / One-off**.

**Known limits / next steps**

- Add a full HTML post-event dashboard if ISM Manufacturing becomes a recurring event workflow.
- Promote it to the shared post-event template used by JOLTS and ISM Services.

### ISM Services PMI

**Main generators and artifacts**

- `scripts/ism_services_pmi_postevent_dashboard.py`
- `workspace/analysis/ism_services_pmi_postevent/`
- likely outputs under `workspace/scenarios/ism_services_pmi_postevent_dashboard_*.html`

**Data used**

- ISM Services PMI actual/consensus history from `workspace/economic_events.db`.
- TY OHLCV from PostgreSQL.
- Conversion helpers from `rules/treasury_price_conversion.py`.

**Analysis level**

- User-entered actual/consensus.
- Surprise percentile and bucket matching.
- All-events vs bucket-matched return distributions at t+1h, t+2h, and t to 5pm ET.
- Expected rally/selloff, percentiles, and 0 bps percentile stats.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Add live trigger / Telegram wrapper.
- Consider adding ISM Services to the unified dashboard once the L1/event-feature store supports it.

## Fed / FOMC Family

### FOMC Meetings Dashboard

**Main generators and artifacts**

- `scripts/fomc_meetings_dashboard.py`
- `dashboards/fomc_meetings_dashboard_20260128_124530.html`
- generated outputs can also go to `workspace/scenarios/fomc_meetings_dashboard_{timestamp}.html`
- `src/core/fomc_sentiment_cache.py`

**Data used**

- FOMC press conference events from `workspace/economic_events.db`.
- TY OHLCV from PostgreSQL.
- Perplexity API for web-derived sentiment labels and summaries.
- Local JSON cache at `workspace/fed/fomc conference/fomc_web_labels.json`.

**Analysis level**

- Interactive FOMC HTML dashboard with filters for month-end meetings and hawkish/dovish sentiment.
- Normalized TY paths from T-60m to T+180m.
- Candlestick modal with event annotations and web-derived summary.
- Return distribution plots for pre/during windows.

**Status**

- **Standalone-prototype**.
- It has generated dashboard artifacts, but it does not appear to be part of the main live Telegram event trigger path.

**Known limits / next steps**

- Decide whether this becomes a live FOMC event page inside `scripts/live_unified_dashboard.py`.
- Consolidate Perplexity sentiment labels with the more detailed transcript sentiment pipeline below.

### FOMC Press Conference Transcript Sentiment

**Main generators and artifacts**

- `scripts/export_fed_press_conference_dates.py`
- `scripts/fetch_fomc_video_urls.py`
- `scripts/fetch_fed_press_conference_transcripts.py`
- `scripts/fomc_press_conference_sentiment_step1.py`
- `scripts/fomc_press_conference_sentiment_step2.py`
- `scripts/fomc_live_sentiment_alerts.py`
- `scripts/fomc_paste_alerts.py`
- `docs/fomc_press_conference_sentiment_plan.md`
- `workspace/fed/fomc conference/fed_press_conference_videos.csv`
- `workspace/fed/fomc conference/transcripts/{date}_{video_id}.json`
- `workspace/fed/fomc conference/sentiment/{date}_{video_id}.json`
- `workspace/fed/fomc conference/sentiment/summary.csv`
- planned / implemented Step 2 output under `workspace/fed/fomc conference/sentiment_step2/`

**Data used**

- Fed Press Conference dates from `workspace/economic_events.db`.
- YouTube Data API for Federal Reserve channel video lookup.
- `youtube-transcript-api` for timestamped transcript download.
- GPT scoring for chunk-level hawkish/dovish sentiment.
- TY 1-minute OHLCV from PostgreSQL as weak supervision / validation.
- Telegram notifier infrastructure for live alerts.

**Analysis level**

- Step 1 scores transcript chunks, computes a 15-minute running score, and compares expected direction to TY move.
- Step 2 calibrates a 5-class sentiment mapping using price action as weak supervision.
- Live alert script supports replay, stdin, and StreamText scrape modes.
- Alerts are intended only for extremes, meaningful changes, or repeated same-direction strengthening.

**Status**

- **Standalone-prototype / partially production-live**.
- Offline pipeline exists and live alert script exists, but this is not yet described as a fully scheduled production FOMC alert service.

**Known limits / next steps**

- Finish/verify Step 2 output coverage and live alert dedupe.
- Promote FOMC live alerts into the same operational runbook pattern as NFP live trigger.
- Integrate the calibrated labels back into the FOMC meetings dashboard.

### FOMC Q&A Microstructure Alignment

**Main generators and artifacts**

- `src/core/fomc_qa_parser.py`
- `scripts/fomc_qa_microstructure_alignment.py`
- `workspace/fed/fomc conference/qa_microstructure/answer_level_summary.csv`
- `workspace/fed/fomc conference/qa_microstructure/run_summary.json`
- per-transcript JSON files under `workspace/fed/fomc conference/qa_microstructure/`

**Data used**

- Timestamped FOMC transcript JSON.
- Deterministic parser for reporter question to Chair Powell answer blocks.
- TY OHLCV from PostgreSQL.

**Analysis level**

- Extracts answer-level anchors at Powell answer start.
- Computes signed `delta_bps` over `0-30s`, `30s-2m`, and `0-10m`.
- Computes realized-vol spikes versus pre-answer rolling baseline.
- Labels directional consistency as `trend`, `mean_revert`, or `mixed`.
- Smoke run verified 25 answer rows for `2020-01-29`.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- It is offline-only today.
- Natural next build: add live answer-level segmentation during StreamText scrape and send Telegram alerts on unusually large microstructure reactions.

### Powell Speech Analyzers

**Main generators and artifacts**

- `scripts/powell_speech_analyzer.py`
- `scripts/powell_speech_manual.py`
- `config/powell_prompts.py`
- outputs like `workspace/powell_speech_{timestamp}.json`

**Data used**

- StreamText live transcript scraping or manual pasted text.
- OpenAI model scoring with prompt templates in `config/powell_prompts.py`.
- Optional Telegram notification support.

**Analysis level**

- Scores hawkish/dovish sentiment on a -10 to +10 scale.
- Tracks exponential moving average sentiment.
- Classifies chunks by topic: Employment, Inflation, Rate Policy, Economic Outlook, and Financial Conditions.
- Manual mode keeps conversation history and skips reporter questions when possible.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Unify Powell speech scoring with the calibrated FOMC press conference sentiment framework.
- Add market alignment if the speech has reliable timestamps and TY data.

## Treasury Supply / Auctions

### 7Y Auction Z-Score Dashboard

**Main generators and artifacts**

- `scripts/seven_year_auction_zscore_dashboard.py`
- `workspace/scenarios/7y_auction_zscore_dashboard_*.html`
- `workspace/analysis/7y_auction_TY_*.csv`
- `workspace/analysis/7y_auction_TY_*.json`
- `workspace/analysis/7y_auction_TY_*.png`

**Data used**

- 7Y auction dates from `workspace/economic_events.db` or auction-derived sources.
- TY OHLCV from PostgreSQL.
- Treasury price conversion helpers.

**Analysis level**

- Windowed TY distributions around 7Y auction events.
- Scenario dashboard outputs with z-score framing.
- Multiple windows are present in analysis artifacts: `t0_to_t2h`, `t0_to_t4h`, `tm2h_to_t`, `tm4h_to_t`.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Link the dashboard explicitly with auction result fundamentals if those fields are available.

### 10Y / 30Y Auction Weeks

**Main generators and artifacts**

- `scripts/ten_thirty_auction_weeks.py`
- `scripts/ten_thirty_auction_weeks_dashboard.html`
- `dashboards/supply_weeks_dashboard.html`
- `scripts/supply_weeks_dashboard.html`

**Data used**

- `workspace/economic_events.db` date anchors for `10-Year Note Auction` and `30-Year Bond Auction`.
- TY 1-minute OHLCV from PostgreSQL.

**Analysis level**

- Supply-week-style standalone HTML.
- Normalized TY paths.
- Zoom re-normalization.
- Windowed distributions for `pre_10y`, `10y_to_30y`, `post_30y`, and auction-day windows.
- 5-minute candlestick drilldown for selected weeks.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Add auction fundamental fields if available.
- Consolidate with 7Y and 20Y analysis into one supply dashboard.

### January Auction Weeks

**Main generators and artifacts**

- `scripts/january_auction_weeks.py`
- `scripts/january_auction_weeks.html`

**Data used**

- Auction week definitions and TY price data.

**Analysis level**

- Compares January auction weeks with other months.
- Includes selectable windows, statistics panels, Plotly charts, and histogram controls.
- Histograms render distro-style with density curve overlay.

**Status**

- **Standalone-prototype / reference**.

**Known limits / next steps**

- Likely superseded by the broader supply-week dashboards unless January seasonality remains a separate trading question.

### 20Y Auction Supply Return Analysis

**Main generators and artifacts**

- `scripts/auction_supply_return_analysis.py`
- `workspace/results/auction_supply_return_analysis/auction_returns_joined.csv`
- `workspace/results/auction_supply_return_analysis/regression_results.csv`
- `workspace/results/auction_supply_return_analysis/bucket_analysis.csv`
- `workspace/results/auction_supply_return_analysis/summary_report.json`
- `workspace/results/auction_supply_return_analysis/plots/`

**Data used**

- Nomura-style auction fundamentals from `Securities_2_nomura_only.csv` / processed auction CSVs.
- TY 1-minute prices for 24 hours after each auction, starting 1pm ET.

**Analysis level**

- OLS regressions using auction fields against TY returns at 2h, 4h, 6h, 8h, 12h, and 24h horizons.
- Quantile bucket analysis with rally/selloff rates.
- Distribution histograms and split plots.
- Prior run had 23 auctions from 2020-2025; R2 ranged roughly 0.32-0.65, with 8h highest in the recorded run.

**Status**

- **One-off / standalone research**.

**Known limits / next steps**

- Expand to 7Y/10Y/30Y if comparable fundamentals are available.
- Use rolling validation before making this a decision dashboard.

### Treasury Refunding

**Main generators and artifacts**

- `scripts/treasury_refunding_distribution.py`
- `workspace/analysis/treasury_refunding_TY_*.csv`
- `workspace/analysis/treasury_refunding_TY_*.json`
- `workspace/analysis/treasury_refunding_TY_*.png`

**Data used**

- Treasury Refunding Announcement event timestamps.
- TY OHLCV from PostgreSQL.
- Treasury conversion helpers.

**Analysis level**

- Windowed return distributions around refunding timestamps.
- Multiple windows exist, including pre windows, post windows, t0 to t+2h, t0 to t+4h, and US-open-to-supply.
- Outputs include expected rally/selloff, percentiles, 0 bps percentile rank, and last-two-event markers.

**Status**

- **Standalone-prototype / one-off distribution workflow**.

**Known limits / next steps**

- Promote into a supply-event family dashboard alongside auctions.

### Auction Data Processing

**Main generators and artifacts**

- `auction_data/process_auction_files.py`
- `data_setup/derive_auction_fields.py`
- raw and processed auction CSV files under `auction_data/`

**Data used**

- Treasury auction CSV exports.
- Optional Nomura-style labels from `Securities (2).csv`.

**Analysis level**

- Produces normalized auction fields: Auction, Size, Coupon, Stop, Bid/Cover, Tail, Yld Gap, Dealer%, Direct%, Indirect%, Dlr Hit, Dir Hit, Indir Hit.
- Incremental processor skips already-fresh outputs unless `--force` is used.

**Status**

- **Support tooling**.

**Known limits / next steps**

- Use this as the upstream source for a consolidated auction dashboard.

## Calendar / Time-of-Day Effects

### US Holiday Candlestick Dashboard

**Main generators and artifacts**

- `scripts/us_holiday_candlestick_dashboard.py`
- `workspace/scenarios/us_holiday_candlestick_dashboard_*.html`

**Data used**

- Programmatically computed US holiday calendar: New Year, MLK, Washington's Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas.
- TY OHLCV from PostgreSQL.
- Tier-1 event overlays from `workspace/economic_events.db` for CPI, NFP, and PCE.

**Analysis level**

- Standalone HTML with clickable holiday rows.
- 5-minute candlestick windows around observed holiday dates.
- Observed/actual date markers.
- Bps-only move metrics in later versions.
- Custom hover behavior, mouse-wheel zoom, modebar, and filtered rows with available TY candles.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- If used regularly, add a holiday-family distribution panel rather than only per-date candlestick inspection.

### Month-End and Friday Dashboard

**Main generators and artifacts**

- `scripts/monthend_friday_candlestick_dashboard.py`
- `workspace/scenarios/monthend_friday_dashboard_*.html`

**Data used**

- Month-end trading dates and recent Fridays.
- TY OHLCV from PostgreSQL.

**Analysis level**

- Standalone HTML with two tabs:
  - Month Ends: last business day of each month from 2022 onward.
  - Recent Fridays: past 2 months.
- Clickable rows render TY 5-minute candlesticks around selected dates.
- Flags month-end Fridays and Fridays that are month-end.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Add return distributions and compare month-end Friday vs non-Friday month-end behavior in one dashboard.

### Month-End Friday US Open Distribution

**Main generators and artifacts**

- `scripts/month_end_friday_us_open_distribution.py`
- `workspace/analysis/month_end_days_us_open_TY.*`
- `workspace/analysis/month_end_friday_us_open_TY.*`

**Data used**

- Month-end and Friday month-end date sets.
- TY OHLCV from PostgreSQL.
- Treasury conversion helpers.

**Analysis level**

- Distro-style histograms for US open hours `08:00-17:00 ET`.
- All month-end trading days with Friday markers.
- Separate Friday-only output.
- Stats include mean, median, extreme percentiles, expected rally/selloff, and 0 bps percentile rank.

**Status**

- **Standalone-prototype / one-off distribution workflow**.

**Known limits / next steps**

- Combine with the candlestick dashboard for a complete calendar-effect page.

### Yearly Overlay

**Main generators and artifacts**

- `scripts/yearly_overlay_timeseries.py`
- `workspace/analysis/yearly_overlay_ty_2010_2026.html`

**Data used**

- PostgreSQL `ohlcv_1m`, symbol pattern such as `TY%`.

**Analysis level**

- Interactive Plotly full-year overlay.
- Aligns years on a shared calendar axis.
- Supports resampling, normalization to start-of-year change, units in price/ticks/bps, and start/end month selectors that re-normalize the window.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Useful context tool, not event-specific by itself. It could feed seasonal regime overlays into event dashboards.

### Overnight Session Distribution

**Main generators and artifacts**

- `scripts/overnight_session_distribution_dashboard.py`
- `workspace/scenarios/overnight_session_distribution_dashboard_*.html` if generated.

**Data used**

- TY OHLCV from PostgreSQL.
- US overnight session definitions, 6pm to 8am ET.

**Analysis level**

- Self-contained HTML with returns and volatility distributions.
- Distro-style histograms, KDE, stats boxes, and date filters.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Connect overnight-session context to pre-event dashboard features, especially NFP/CPI pre-release drift.

## Policy / Geopolitical Dashboards

### Geopolitical Events Dashboard

**Main generators and artifacts**

- `scripts/geo_analysis.py`
- `scripts/geo_analysis.html`
- `dashboards/geo_analysis.html`

**Data used**

- Curated geopolitical event set covering 21 major events from 2022-2026.
- TY OHLCV from PostgreSQL.
- `workspace/economic_events.db` for nearby macro annotations.
- Optional Perplexity fade lookup and narrative sources.

**Analysis level**

- US/Eastern timestamp normalization from source timezones.
- Multi-day event expansion.
- Weekend/holiday shift to nearest trading day with UI labeling.
- Normalized TY paths, per-event stats, unusual-day detection, candlestick modal, distribution plots, curated summaries, and nearby macro context.
- Spike/fade over plus/minus 2 days of 1-minute data.

**Status**

- **Standalone-prototype**.

**Known limits / next steps**

- Curated event set is strong but manual.
- Could become the Non-Economic Events tab in the unified dashboard, with tag-query UX for categories like shutdown, war, tariff, or oil shock.

### Policy Events Dashboard

**Main generators and artifacts**

- `scripts/policy_events_analysis.py`
- `scripts/policy_events_analysis.html`
- `dashboards/policy_events_analysis.html`

**Data used**

- Curated policy and macro event categories:
  - Yen rate check.
  - Iran.
  - Canada retaliatory tariff.
  - Democrats voting for shutdown.
  - Fed.
- TY OHLCV from PostgreSQL.

**Analysis level**

- Category filter pills.
- Event summaries on cards and full narrative in modal.
- Shutdown episodes include date ranges and market-context summaries.
- Placeholder events for Yen/Iran/Canada/Fed are ready to be filled.
- Reuses geo dashboard mechanics: TY paths, candlestick modal, and distribution plots.

**Status**

- **Standalone-prototype / partial content**.

**Known limits / next steps**

- Fill placeholder categories with exact event dates and sources.
- Promote to the planned Non-Economic Events unified-dashboard tab.

### Legacy Pre/Post Event Reference Dashboards

**Main artifacts**

- `dashboards/preevent_dashboard_dd.html`
- `dashboards/postevent_dashboard_dd.html`
- `dashboard.html`

**Data used**

- Embedded historical event and market data in standalone HTML.

**Analysis level**

- Reference versions of pre/post event analytics from before the newer generator split.

**Status**

- **Stale / reference**.

**Known limits / next steps**

- Treat as design/reference unless a generator still points to them.
- Prefer `scripts/live_unified_dashboard.py`, `scripts/preevent_standalone_dashboard.py`, or event-specific standalone generators for new work.

## Live Infrastructure Feeding Event Analysis

### Unified Dashboard

**Main generators and artifacts**

- `scripts/live_unified_dashboard.py`
- `scripts/dashboard_v2/app.py`
- `scripts/dashboard_tabs/preevent_tab.py`
- `scripts/dashboard_tabs/postevent_tab.py`
- `scripts/dashboard_tabs/adhoc_tab.py`
- `scripts/dashboard_tabs/weekmatch_tab.py`
- `scripts/dashboard_tabs/polymarket_scenarios_tab.py`
- `workspace/scenarios/live_unified_nfp_dashboard_*.html`
- `workspace/scenarios/live_unified_cpi_dashboard_*.html`

**Data used**

- `workspace/economic_events.db` and `bbg_data`.
- PostgreSQL OHLCV or L1 event-feature files depending on path.
- Databento live overlay / snapshot-derived live price depending on version.
- Polymarket stream / markets DB and API resolution.

**Analysis level**

- Shared dashboard with Event Analysis, Ad-Hoc, Week Match, and Polymarket scenarios.
- Event Analysis supports pre and post views.
- Pre-event tab: consensus std buckets, paths, envelopes, distributions.
- Post-event tab: surprise percentile bucket boxplot, normalized paths, optional bucket filtering, window stats, candlestick modal, and distribution explorer.
- Ad-Hoc tab: date/range session analysis and distributions.
- Week Match tab: uploaded calendar matching, tier rules, week candidates, price paths, candlestick modal.
- Polymarket scenarios:
  - NFP mode: NFP + UER tables with analytics rows.
  - CPI mode: CPI MoM analytics table and CPI YoY odds-only table.

**Status**

- **Production-live / WIP**.

**Known limits / next steps**

- `docs/unified_dashboard_plan.md` is the canonical spec.
- Scope there says supported event types should include NFP, CPI, PPI, and JOLTS.
- Continue moving from large embedded datasets to API-driven L1 feature serving.
- Use shared panel contracts to avoid separate one-off implementations diverging.

### Standalone Pre/Post and Live Event Dashboards

**Main generators and artifacts**

- `scripts/live_preevent_dashboard.py`
- `scripts/live_postevent_dashboard.py`
- `scripts/preevent_standalone_dashboard.py`
- `scripts/build_preevent_standalone_html.py`
- `docs/pre_event_standalone_plan.md`

**Data used**

- Shared event loader `scripts/event_data_loader.py`.
- NFP/CPI Excel sources and `workspace/economic_events.db`.
- PPI uses DB-only defaults where configured.
- TY OHLCV from PostgreSQL.
- Snapshot DB for live post-event price overlay.

**Analysis level**

- Live pre-event dashboard supports NFP and CPI with fixed 3pm T-1 to 8:30am window and statistical envelope.
- Live post-event dashboard supports actual/consensus/std/fwd inputs, surprise percentile, percentile buckets, full 3pm T-1 to 3pm T window, live polling, and ngrok tunnel.
- Standalone pre-event path embeds output for sharing without server.

**Status**

- **Production-live** through Telegram command flow for NFP/CPI.
- **Standalone-prototype** for generated HTML artifacts.

**Known limits / next steps**

- Generalize event config enough that PPI/JOLTS/ISM can use the same live pre/post flow.
- Keep `docs/pre_event_standalone_plan.md` data contracts intact: sort time series, use treasury conversion helpers, and keep embedded data blocks honest.

### Telegram Bot Event Commands

**Main scripts**

- `scripts/telegram_bot.py`
- `scripts/telegram_bot_config.py`
- `src/core/telegram_feedback_store.py`
- `src/core/date_tags_store.py`

**Data used**

- Orchestrator outputs.
- Event dashboard generator outputs.
- Telegram bot token / allowed chat config.
- Date tags stored in `workspace/economic_events.db`.

**Analysis level**

- `/pre_event` creates pre-event B/E dashboards.
- `/post_event` creates post-event B/E dashboards.
- `/live_pre_event <cons_std> <fwd_be>` starts live dashboard with ngrok URL.
- `/live_pre_event_stop` stops it.
- `/feedback` captures reply-based feedback.
- Date tagging commands add/retrieve trader notes.

**Status**

- **Production-live**.

**Known limits / next steps**

- Add event-type arguments consistently across live/post commands.
- Build reusable status messages that say which data source and event sample were used.

### NFP Live Trigger and Bloomberg Watcher

**Main scripts**

- `scripts/nfp_live_trigger.py`
- `scripts/bbg_excel_watcher.py`
- `config/bbg_cells.json`

**Data used**

- `Z:/Meini/NFP.xlsx` or open Excel workbook with Bloomberg RTD/BDP.
- Latest snapshot DB for TY price.
- Historical NFP data for surprise percentile and bucket matching.
- Options-implied forward breakeven calculation.

**Analysis level**

- Automatic detection of actual NFP value.
- One-trigger-per-day state.
- Calculates surprise percentile and matching bucket.
- Computes B/E stats and projected path levels.
- Generates HTML dashboard and sends Telegram summary.

**Status**

- **Production-live for NFP**.

**Known limits / next steps**

- Generalize to a reusable `event_live_trigger.py`.
- Add PPI/JOLTS/ISM/FOMC plug-ins with event-specific data extraction.

### Scheduled Events and Redis Pre-Alert Stream

**Main scripts and modules**

- `scripts/scheduled_event_runner.py`
- `src/services/redis_event_stream/excel_parser.py`
- `src/services/redis_event_stream/ingestor.py`
- `src/services/redis_event_stream/publisher.py`
- `src/services/redis_event_stream/subscriber.py`
- `src/services/redis_event_stream/session_config.py`
- `src/services/redis_event_stream/schema.sql`
- `scripts/redis_alerts_dashboard.py`

**Data used**

- Weekly Excel calendar.
- PostgreSQL table `scheduled_event_alerts`.
- Redis stream `market:events`.
- Session marker generation for Tokyo/London/US sessions.

**Analysis level**

- 10-minute pre-alert publisher for calendar events and session markers.
- Ingestor parses Calendar and holiday sheets, normalizes time strings, and upserts event rows.
- Publisher polls by `updated_at`, schedules jobs, and emits idempotent Redis stream entries.
- Dashboard shows recent stream messages and metadata.

**Status**

- **Production-live / operational infrastructure**.

**Known limits / next steps**

- Tie pre-alert events directly to dashboard generation links.
- Add event-family-specific pre-alert summaries using the pre-event dashboard stats.

### Bloomberg Data Refresh / Calendar Ingest

**Main scripts**

- `data_setup/create_bbg_data_table.py`
- `data_setup/load_bbg_event_fields_to_db.py`
- `data_setup/ingest_excel_calendar_to_sqlite.py`
- `scripts/refresh_bbg_excel.py`
- `scripts/run_bbg_data_daily.bat`
- `scripts/install_bbg_data_task.ps1`
- `scripts/install_bbg_refresh_task.ps1`
- `scripts/check_economic_events_db_staleness.py`
- `scripts/install_economic_events_staleness_task.ps1`

**Data used**

- Shared Bloomberg workbook.
- Weekly Meini calendar workbook.
- Repo-local `workspace/economic_events.db`.
- Secondary DB at `D:\FRDBs\workspace\economic_events.db`.

**Analysis level**

- Not analysis itself, but it is critical upstream plumbing for nearly every macro dashboard.
- Keeps `bbg_data` and event calendar current.
- Staleness checks alert when DB copies are too old.

**Status**

- **Production-live / operational infrastructure**.

**Known limits / next steps**

- Current active context notes a secondary DB write-permission issue on `D:\FRDBs\workspace`.
- Keep operational alerts visible because stale Bloomberg data directly compromises dashboards.

### Polymarket Event Scenarios

**Main scripts**

- `scripts/dashboard_tabs/polymarket_scenarios_tab.py`
- `scripts/polymarket_probability_tracker.py`
- `scripts/polymarket_stream_bot.py`
- `src/core/polymarket_stream_store.py`
- `src/servers/polymarket/*`

**Data used**

- Polymarket Gamma/CLOB APIs.
- Local Polymarket SQLite stores.
- Event historical surprise series from `bbg_data`.
- NFP/UER and CPI event mappings.

**Analysis level**

- NFP mode shows NFP + UER scenario tables with surprise percentile and pre/post B/E analytics.
- CPI mode shows CPI MoM analytics plus CPI YoY odds-only table.
- Includes expected probability-weighted rows, histogram drilldowns, and projected-price ladder aggregation.
- Alert bot sends chart-based significant-only updates with approval forwarding.

**Status**

- **Production-live / WIP**.

**Known limits / next steps**

- Current scenario analytics are mainly NFP/UER and CPI.
- Add PPI and FOMC market templates if Polymarket market structure is stable enough.

## Tinkering / Ad-Hoc Explorations

### NFP Family

- `tinkering/nfp_pre_post_heatmap.py`: NFP pre/post B/E heatmap across consensus std and surprise percentile.
- `tinkering/nfp_scenario_lookup.py`: scenario lookup for hypothetical NFP actual values.
- `tinkering/nfp_uer_4x4_postevent_grid.py`: 4x4 NFP vs UER surprise percentile grid with t-to-EOD stats, max-up/max-down, normalized paths, and candlestick drilldown. Generated outputs include `workspace/scenarios/nfp_uer_4x4_postevent_grid_*.html`.
- `tinkering/nfp_uer_exploration.py`: NFP + UER combined uncertainty exploration using PCA/K-Means.
- `tinkering/nfp_uer_exploration_preevent.py`: pre-event NFP + UER focused exploration.
- `tinkering/nfp_ur_3x3_heatmap.py`: NFP + UR 3x3 heatmap visualization.
- `tinkering/nfp_ur_3x3_preevent.py`: NFP + UR 3x3 pre-event analysis.
- `tinkering/nfp_similarity_timeseries.py`: nearest-neighbor matching of similar pre-event drift days using baseline and augmented lag/seasonality features; outputs HTML report and features CSV.

**Status**

- **One-off / tinkering**, with several useful ideas that should be harvested into NFP two-factor or unified-dashboard work.

### Predictor / Breakeven Modeling

- `tinkering/analyze_all_predictors.py`: ad-hoc analysis of predictors for B/E multiplier.
- `tinkering/analyze_predictors.py`: cons_std and surprise percentile predictor analysis.
- `tinkering/be_grouping.py`: simple grouping by cons_std.
- `tinkering/be_prediction_model.py`: B/E multiplier prediction using FWD percentile, FWD/ATR, trend strength.
- `tinkering/regression_analysis.py`: regression of B/E multiplier on surprise percentile and cons_std.
- `tinkering/bucket_analysis_all.py`: comprehensive bucket analysis.
- `tinkering/bucket_analysis_with_dates.py`: bucket analysis with event dates visible.
- `tinkering/bucket_by_ur_std.py`: unemployment-rate std-dev buckets.
- `tinkering/consensus_bucket_analysis.py`: consensus bucket analysis.
- `workspace/analysis/interactive_bucket_analysis.html`: interactive bucket output.

**Status**

- **One-off / tinkering**.

**Next step**

- Mine these scripts for stable features and add them as optional covariates in the two-factor PCA / tail-risk framework.

### Surprise / Reversion

- `tinkering/extension_reversion_analysis.py`: price extension to mean-reversion probability.
- `tinkering/surprise_mean_reversion_analysis.py`: surprise to mean-reversion behavior.
- `tinkering/surprise_predictive_power_analysis.py`: surprise predictive power, correlation, buckets, and R2.
- `tinkering/interactive_extension_analysis.py`
- `tinkering/interactive_surprise_click.py`
- `tinkering/interactive_surprise_distribution.py`

**Status**

- **One-off / tinkering**.

**Next step**

- Add post-event reversion panels to NFP/CPI/PPI dashboards only if walk-forward validation shows stable signal.

### Pre-Event Variants

- `tinkering/preevent_be_divergence_boxplot.py`
- `tinkering/preevent_be_ur_boxplot.py`
- `tinkering/preevent_dashboard.py` (older pre-event dashboard variant).
- `tinkering/preevent_postevent_correlation.py`
- `tinkering/preevent_technicals.py`
- `tinkering/live_preevent_dashboard_2025.py`

**Status**

- **One-off / tinkering / legacy**.

**Next step**

- Only promote features that beat the current consensus-std bucket + envelope UX.

### Fundamental Scoring

- `tinkering/fundamental_scoring.py`
- Fundamental macro section also exists in the post-event boxplot dashboard path.

**Status**

- **One-off / tinkering**.

**Next step**

- Decide whether fundamental scoring should become an explanatory panel or remain an internal diagnostic.

### Auction One-Offs

- `tinkering/auction_events_december_report.py`
- `scripts/auction_supply_return_analysis.py`

**Status**

- **One-off / standalone research**.

**Next step**

- Consolidate into a single Treasury supply research area.

## HTML Inventory Cross-Reference

### Root / Dashboards

- `dashboard.html`: root dashboard artifact; likely reference/stale compared with generated dashboards.
- `dashboards/fomc_meetings_dashboard_20260128_124530.html`: FOMC meetings dashboard.
- `dashboards/geo_analysis.html`: geopolitical events dashboard.
- `dashboards/policy_events_analysis.html`: policy/geopolitical dashboard.
- `dashboards/postevent_dashboard_dd.html`: legacy post-event dashboard reference.
- `dashboards/preevent_dashboard_dd.html`: legacy pre-event dashboard reference.
- `dashboards/supply_weeks_dashboard.html`: supply weeks dashboard.

### Scripts Directory HTML

- `scripts/geo_analysis.html`: generated geopolitical dashboard.
- `scripts/january_auction_weeks.html`: January auction weeks dashboard.
- `scripts/policy_events_analysis.html`: generated policy events dashboard.
- `scripts/supply_weeks_dashboard.html`: generated supply weeks dashboard.
- `scripts/ten_thirty_auction_weeks_dashboard.html`: 10Y/30Y auction weeks dashboard.

### Workspace Analysis HTML

- `workspace/analysis/interactive_bucket_analysis.html`: interactive B/E bucket exploration.
- `workspace/analysis/nfp_similarity_timeseries.html`: NFP pre-event drift similarity explorer.
- `workspace/analysis/nfp_uer_exploration.html`: NFP/UER combined exploration.
- `workspace/analysis/nfp_uer_exploration_preevent.html`: NFP/UER pre-event exploration.
- `workspace/analysis/totalaggress_vs_price_*.html`: Databento TotalAggress vs price/yield charts.
- `workspace/analysis/yearly_overlay_ty_2010_2026.html`: yearly TY overlay.

### Workspace Distro / Plots HTML

- `workspace/distro_runs/JOLTS_Job_Openings_TY_surprise_distribution.html`: JOLTS surprise distribution.
- `workspace/plots/JOLTS_Job_Openings_TY_click_explore.html`
- `workspace/plots/JOLTS_Job_Openings_TY_interactive.html`
- `workspace/plots/JOLTS_Job_Openings_TY_paths_explorer.html`
- `workspace/plots/JOLTS_Job_Openings_TY_surprise_explorer.html`
- `workspace/plots/JOLTS_Job_Openings_TY_surprise_histograms.html`
- `workspace/plots/JOLTS_Job_Openings_TY_surprise_paths.html`

### Workspace Scenarios HTML

- `workspace/scenarios/7y_auction_zscore_dashboard_*.html`: 7Y auction z-score dashboards.
- `workspace/scenarios/cpi_twofactor_pca_dashboard*.html`: CPI two-factor PCA dashboards.
- `workspace/scenarios/live_unified_cpi_dashboard_*.html`: unified dashboard generated in CPI mode.
- `workspace/scenarios/live_unified_nfp_dashboard_*.html`: unified dashboard generated in NFP mode.
- `workspace/scenarios/live_unified_nfp_dashboard_runtimecheck.html`: NFP runtime check artifact.
- `workspace/scenarios/monthend_friday_dashboard_*.html`: month-end / Friday candlestick dashboards.
- `workspace/scenarios/nfp_postevent_dashboard_*.html`: NFP post-event dashboard.
- `workspace/scenarios/nfp_twofactor_pca_dashboard_*.html`: NFP two-factor PCA dashboard.
- `workspace/scenarios/nfp_uer_4x4_postevent_grid_*.html`: NFP x UER post-event grid.
- `workspace/scenarios/non_farm_payrolls_postevent_dashboard_*.html`: Non Farm Payrolls post-event dashboard.
- `workspace/scenarios/ppi_twofactor_pca_dashboard_*.html`: PPI two-factor PCA dashboard.
- `workspace/scenarios/preevent_cpi_dashboard_*.html`: CPI pre-event standalone dashboard.
- `workspace/scenarios/preevent_nfp_dashboard_*.html`: NFP pre-event standalone dashboard.
- `workspace/scenarios/us_holiday_candlestick_dashboard_*.html`: US holiday candlestick dashboards.
- `workspace/scenarios/_served_cpi.html`, `_served_cpi_after_split.html`, `_served_root.html`: served/static debugging artifacts.

## Current Gaps and Candidate Next Builds

### Events with infrastructure but no mature dashboard

- PCE / Core PCE: appears in holiday tier overlays and event data, but no dedicated pre/post dashboard found.
- Retail Sales: likely in event dictionary / event calendar, but no dedicated dashboard found.
- Initial Claims: likely in event dictionary / event calendar, but no dedicated dashboard found.
- ECI: candidate for CPI/PPI-like inflation labor-cost family, but no dashboard found.
- GDP: likely available in calendar data, but no dashboard found.
- FOMC rate decision itself: dashboard work is mostly press-conference focused; rate-decision event reaction could be separated.

### Prototypes without live trigger

- PPI two-factor and post-event dashboards.
- JOLTS post-event and event-study dashboards.
- ISM Manufacturing / Services workflows.
- FOMC meetings dashboard and transcript sentiment pipeline.
- Treasury supply dashboards.

### Candidate reusable trigger architecture

Build a generic event trigger around the NFP trigger pattern:

- event config: event name, release time, unit, workbook/DB mapping, default std-dev, default fwd B/E.
- actual-data resolver: Excel, DB, API, or manual input.
- live price resolver: Databento/snapshot fallback.
- historical surprise percentile resolver.
- dashboard builder callback.
- Telegram formatter.
- one-trigger-per-event-date state.

### Candidate dashboard consolidation

The repo currently has many event-specific standalone dashboards. A clean consolidation path would be:

- Keep `scripts/live_unified_dashboard.py` as the main live UI.
- Keep event-specific standalone scripts only where they explore genuinely different methodology.
- Move reusable panels into `scripts/dashboard_tabs/` or shared helpers.
- Make NFP/CPI/PPI/JOLTS the first-class supported event set, matching `docs/unified_dashboard_plan.md`.
- Add ISM and FOMC after the first four are stable.

### Candidate modeling hardening

For two-factor / PCA dashboards:

- Add regime tags and all-history vs current-regime comparison.
- Add tail-aware buckets and probability of exceeding X bps.
- Add one nonlinear term set only if rolling OOS improves.
- Add a basic CTA / trend proxy from TY history.
- Track bucket stability under leave-one-out and bootstrap perturbations.

### Candidate Fed roadmap

- Merge Perplexity FOMC sentiment, transcript sentiment, and Q&A microstructure into one Fed event stack.
- Add live StreamText capture as an operational service.
- Add answer-level live microstructure alerts.
- Feed calibrated labels into the FOMC meetings dashboard.

### Candidate policy / geopolitical roadmap

- Fill policy dashboard placeholder events with dated sourced entries.
- Move shutdown / tariff / geopolitical shock analysis into the planned Non-Economic Events tab.
- Use event tags in `workspace/economic_events.db` as the long-term storage layer for curated non-economic events.

## Practical Re-Entry Path

If you are restarting this work after time away, the highest-leverage sequence is:

1. Re-open `docs/unified_dashboard_plan.md` and decide whether the main path is still NFP/CPI/PPI/JOLTS.
2. Regenerate one NFP and one CPI unified dashboard to confirm the current code still runs.
3. Validate `workspace/economic_events.db` freshness and Bloomberg workbook refresh status.
4. Pick one of two tracks:
   - **Product track**: make the unified dashboard fast and complete for NFP/CPI/PPI/JOLTS.
   - **Research track**: harden CPI/NFP/PPI two-factor PCA with regime and tail calibration.
5. Avoid adding another one-off standalone dashboard unless the method is materially new.
