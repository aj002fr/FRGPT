# FOMC Press Conference Transcript Sentiment Plan

## Goal
Build a **real-time + offline** sentiment system for FOMC press conferences that:
- Scores transcript text for **hawkish↔dovish** continuously (LLM)
- Converts that into a **5-class label**:
  - **very dovish**, **dovish**, **neutral**, **hawkish**, **very hawkish**
- Uses **TY (10Y UST futures)** 1-minute price action as a **weak supervision signal** to **train/calibrate the mapping**
- Sends **Telegram alerts** only when sentiment is **extreme** or **meaningfully changing/strengthening**

## Non-goals (for now)
- Fine-tuning GPT weights directly with market data (we will **train the mapping**, not the base LLM)
- Predicting future returns as a standalone alpha model (this is calibration + alerting first)

---

## Data Sources
- **Transcript**: `workspace/fed/fomc conference/transcripts/{date}_{video_id}.json`
- **Event anchor**: `workspace/economic_events.db` row for `event_name='Fed Press Conference'` (US)
- **Market data**: PostgreSQL `ohlcv_1m` for **TY**
- **Price conventions**: use `rules/treasury_price_conversion.py` for ticks/bps/32nds

---

## Step 1 (Implemented): LLM sentiment + deterministic alignment checks
**Script**: `scripts/fomc_press_conference_sentiment_step1.py`

### Outputs
- Per-transcript JSON:
  - `workspace/fed/fomc conference/sentiment/{date}_{video_id}.json`
- Summary CSV:
  - `workspace/fed/fomc conference/sentiment/summary.csv`

### Current behavior (note)
Step 1 was initially tuned for 5-minute chunks; Step 2 will move the system to **1-minute windows**.

---

## Step 2 (Planned): Train/calibrate the mapping using 1-minute price action

### Key idea
Use realized **TY 1-minute move** as a **noisy label** to train a deterministic mapping from:
- **LLM chunk score** (continuous hawkish↔dovish)
- **15-minute running score** (recency-biased or rolling window)
to:
- **5-class sentiment label** (*very dovish … very hawkish*)
- **alert triggers** (extreme or meaningful change)

This is **weak supervision** / **calibration**, not LLM fine-tuning.

### Windowing
- **Transcript chunk size**: **60 seconds**
- **Price action windows**:
  - `same_1m`: chunk_start → chunk_end
  - optional `lag_1m`: chunk_end → chunk_end + 1 minute (configurable)
- **Running score**: **15-minute lookback** (15 × 1-minute chunks)

### Price → label (initial heuristic)
Define tick thresholds (configurable) on `delta_ticks` for TY:
- `<= -T_very`: **very hawkish**
- `(-T_very, -T)`: **hawkish**
- `[-T, +T]`: **neutral**
- `(T, T_very)`: **dovish**
- `>= T_very`: **very dovish**

Convention: **hawkish ⇒ TY down**, **dovish ⇒ TY up**.

### “Train the mapping” (calibration approach)
We will fit a mapping \(f\) that predicts the **price-derived label** from LLM signals:
- Inputs:
  - `chunk_score` (LLM)
  - `running_score_15m` (LLM-derived)
  - optional: topic, volatility proxy, time-into-PC, etc.
- Target:
  - `price_label_same_1m` (or `price_label_lag_1m`, or both)

Implementation should remain **stdlib-only** and deterministic. Two acceptable approaches:
- **Grid-search calibration (recommended first)**:
  - search weights \(w_1, w_2\) and thresholds that maximize agreement with price labels
  - robust, simple, explainable
- **Simple multiclass linear model (optional)**:
  - softmax/logistic style trained in pure Python
  - only if grid-search is insufficient

Deliverable output per chunk should include:
- raw LLM scores
- price move (ticks/bps + direction)
- price-derived label
- calibrated label + calibrated confidence

---

## Telegram Alerts (Planned)
Reuse existing Telegram bot infrastructure (`scripts/telegram_bot.py`, `src/core/telegram_notifier.py`).

### When to alert
Send an alert only if at least one is true:
- **Extreme**: calibrated label is **very hawkish** or **very dovish**
- **Meaningful change**:
  - label crosses from **neutral → {dovish/hawkish}**
  - label crosses into an extreme (e.g., **hawkish → very hawkish**)
- **Strengthening**:
  - same sign but magnitude increases (e.g., dovish → very dovish)

Do **not** alert if:
- label is **neutral**
- label unchanged and not strengthening

### Dedupe / rate limiting
- Keep last-alert state (per transcript/date):
  - last_sent_label
  - last_sent_minute_idx
  - last_sent_time
- Enforce:
  - minimum time between alerts (e.g., 2–5 minutes)
  - suppress repeats unless label changes/strengthens again

---

## Planned Implementation Artifacts
Expected new script(s):
- `scripts/fomc_press_conference_sentiment_step2.py` (calibration + 5-class labels + alert decisions)

Expected new outputs:
- Per-transcript enriched JSON (Step 2):
  - `workspace/fed/fomc conference/sentiment_step2/{date}_{video_id}.json`
- Step 2 summary CSV:
  - `workspace/fed/fomc conference/sentiment_step2/summary.csv`

---

## Acceptance Criteria
- 1-minute chunks + 15-minute running score implemented end-to-end
- Step 2 produces 5-class labels with:
  - price-derived label
  - calibrated label (mapping-trained)
  - clear audit trail per chunk (scores, price move, predicted vs realized)
- Telegram alerts fire only on:
  - extremes, or
  - meaningful changes/strengthening
  - and are deduped/rate-limited


