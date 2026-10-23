# Gamma Tracker V6 — implementation note

**As of 14 September 2026.** Live page: `http://127.0.0.1:8055/` (Tailscale `https://aasthajain.tail174727.ts.net:8443/`).
Dash: `Z:\uikitxv2\OptMonitor\gamma_screener\gamma_ladder_standalone\gamma_tracker_dash_v6.py`.
Writer: `gamma_tracker_v3.py` → `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db`.

This note is the implementation record: concept, stack, units, data history, what the page actually computes, why, and what is still only ideated. Formula companion: `docs/gamma_tracker_v6_scientific_record.tex`. The page is a **measurement**. It is not a long/short, not after-cost validated, and not an IV fair-value model.

---

## 1. Concept

V6 answers one question:

> Where is the live TY option surface relative to its own recent history, and is that location ordinary for this clock, vol regime, and still-owned event?

Five views of the same construction:

| Surface | What it is |
|---|---|
| Summary table | Latest fitted snapshot: ATM backbone, RR, fly, weighted skew/curve, ROC, 6h deltas |
| Percentile heatmap | Each delta-bucket ranked 0–100 vs thinned history. Red = high vs that history; blue = low |
| Metric timeseries | The same numbers as a path |
| Smile | Latest snapshot in **strike** space (not percentile space) |
| Event table / panels | Which history bucket the ranks use. **Does not change live IVs** |

A cell at the 90th percentile means “this wing is high versus the chosen history.” It is not a forecast that vol will fall.

The intended research path, of which only the first step is live:

```
where is vol now?
  → where should it be given state?
    → where is this node likely to go?
      → is the gamma plus the mark change worth the price?
```

---

## 2. Why this page exists

Earlier OptMonitor pieces (ladder, CMED Excel, Redis, tracker v1/v2) were quote tables or file-driven snapshots. V6 is a **live rank versus own history**:

- AccessAPI live options, not CMED/Redis (the writer path on 8055 does not use those).
- Persist every ~30s into SQLite so the page can rank the present against weeks of facts.
- Event ownership so a red wing can be read as “rich even among other CPI windows” versus “ordinary for this print.”
- Isolated from aggression monitors (5050 / 5052 / 5063 / …). No SOD, Telegram, or execution wiring.

V6 is the dashboard. The writer is still named **v3** because the persist schema (`snapshot_v2`, `summary_metrics_v2`, …) was frozen there. Live 8055 reads `gamma_tracker_v3.db`. A portable copy (`gamma_tracker_v6_standalone\`, default port 8056, `gamma_tracker_v6.db`) exists so a production box can run without importing v2/v3; it is not what 8055 uses.

---

## 3. Infrastructure

```
AccessAPI 100.79.30.21:8001
  /live/options/snapshot   (1s poll; stale if >10s)
  /live/tbbo/recent?symbols=TY&n=1   (futures fallback)
        |
        v
live_options_adapter.py
  filter C/P, bin |delta|, ATM = closest to 0.50
        |
        v
gamma_tracker_v3.py          every 30s if stream id changed
  remap expiries to 2nd..10th
  dedup one strike per (expiry, type, bucket)
  compute_summary_metrics (v2 library)
  persist snapshot + surface + summary
  heatmap_fact every 60th snapshot (~30 min)
        |
        v
gamma_tracker_v3.db
  snapshot_v2 / summary_metrics_v2 / option_surface_v2
  heatmap_fact (never pruned)
  futures_vol_log (not drawn)
        |
        v
gamma_tracker_dash_v6.py     port 8055, poll 2s
  EventScorer <- economic_events.db (read-only) + event_params.json
  SurfacePercentileCache (rebuild 900s; needs >=3 days)
```

**Launch.** Live dash is started with the Windows-safe nosite launcher:

`_tmp_run_gamma_tracker_dash_v6_nosite.py --db-path ...\gamma_tracker_v3.db --host 127.0.0.1 --port 8055`

`start_gamma_suite.bat` can also start the v3 writer plus this dash. File changes on the dash require a **manual restart**. Do not kill the writer when restarting 8055.

**Freshness badges**

| Badge | Rule |
|---|---|
| TRACKER: LIVE | latest `file_mtime` < 120s |
| STALE | 120–600s |
| OFFLINE | > 600s |
| NO DATA | empty table |
| CACHE: WARMING | snapshot span < 3 days or cache empty |

---

## 4. Units

| Quantity | Unit on the page | Notes |
|---|---|---|
| Implied vol | **daily BP** | `LIVE_IV_TO_DAILY_BP = 1`. API `iv_mid` is stored as-is. No annualization. |
| Backbone, RR, fly, ROC, 6h deltas | daily BP | Level change, not percent |
| Heatmap cell | percentile 0–100 | Rank of wing-spread or raw IV vs history H |
| Wing spread | daily BP | `IV - ATM` (backbone ATM) |
| Event score | unitless | Sum of weights x time decay; then bucketed |
| Surprise z | standardized miss | `(actual-consensus)/std`; not used on the live score after the 2026-09-14 ownership change |
| Futures | TY price (decimal) | Median of option `futures_mid`, else TBBO mid |
| DV01 / DV01 gamma | stored, not drawn | `delta * 1000/64`, `gamma * 1000/64` |
| Expiry cutoff for events | 12:00 **CT** | TY option expiry, not 23:59 ET |
| Clocks | CT on the page; ET for event calendar | Sunday evening Globex weekday bucket counts as Monday |

“Daily BP” here is the tracker’s stored normal-vol unit, not “basis points of yield per calendar day” as a trader identity. Treat it as the quote unit the writer persisted.

---

## 5. History of the data

**Live tape (writer).** AccessAPI option snapshots since the v3 writer has been running against this DB. Raw `snapshot_v2` / `option_surface_v2` / `summary_metrics_v2` / `futures_vol_log` older than **30 days** are deleted.

**Percentile history (`heatmap_fact`).** Written every 60th successful snapshot (~30 minutes when the loop is clean). **Never pruned.** This is why ranks can look back a month (or “full”) after raw snapshots have been deleted. The cache will not color until the snapshot span is at least 3 days.

**Event weights.** `event_params.json`, calibrated **2026-03-25**. Families: ADP, AUCTION, CHALLENGER, CLAIMS, CPI, DURABLES, FOMC, GDP, HOUSING, ISM, JOLTS, NFP, PCE, PMI_DETAIL, PPI, REGIONAL_FED, RETAIL, SENTIMENT. The unused `1st` row is now the **same-calendar-day** weight.

**Event calendar.** Read-only `Y:\Aastha\market_data_puller\workspace\economic_events.db`: `bbg_data` tiers 1–3 plus selected `economic_events` (FOMC, auctions, NFP/CPI/PPI/PCE/ISM/retail/GDP/claims). Reload hourly. Lookback 12h is for the calendar list only; printed events **score 0**.

**Decay panel.** Precomputed ATM transitions in `event_decay_probs.json` (BentoBachelier ~4-year history). Reused as a proxy for every moneyness cell. Not a live wing model.

**Not used by V6.** CMED Excel, Redis, `reference_surface.json` (4h median RR/fly sidecar). `backbone_pct` column exists but the live writer does not fill it (`--` on the table). Working ATM **level** rank is the heatmap ATM column in **raw IV** mode.

**Standalone seed.** The portable package can copy `heatmap_fact` + 30d summaries + last two surfaces from the v3 DB (`mode=ro`) into `gamma_tracker_v6.db`. That is a copy, not a second live writer on 8055.

---

## 6. How a quote becomes a surface row

Keep a row if it has expiration, type in {C,P}, numeric strike, `delta_F_mid`, `iv_mid`. Skip the whole snapshot if futures mid <= 0.

**Ordinals.** Adapter maps weekday expiries after today (weekends dropped) to `1st`…`10th`. Writer remaps again: remaining dates after today become **`2nd`…`10th`**. Front remaining expiry is always labeled `2nd`. `1st` is never persisted. Weekend expiries never enter.

**Business days.** Chicago `busday_count` from today to expiry, minimum 1.

**Moneyness bin** (then keep one strike per `(expiry, type, m)` closest to a target |delta|; ties to lower strike; no interpolation):

| Bucket m | Bin |delta| | Target |delta| | Name |
|---|---|---|---|
| 0 | >= 0.60 | 0.50 | ATM column (heatmap) |
| 25 | [0.45, 0.60) | 0.375 | 25-delta |
| 50 | [0.30, 0.45) | 0.25 | 50-delta |
| 75 | [0.15, 0.30) | 0.125 | 75-delta |
| 100 | < 0.15 | 0.05 | wing |

**Backbone ATM** (copied onto every row as `Atm Vol (Daily BP)`): IV of the call or put whose |delta| is closest to **0.50**. This is **not** the same object as heatmap bucket 0.

That mismatch is why wing-spread mode leaves the ATM column **n/a**: wing spread is `IV - backbone ATM`, and ranking bucket-0 versus backbone ATM is a leftover, not “ATM vs itself.”

---

## 7. Summary metrics (per expiry, daily BP)

- Backbone `B` = ATM IV (closest to 0.50).
- Risk reversal `RR_m` = call IV minus put IV in bucket m. Blank if either leg is missing.
- Butterfly `Fly_m` = average of the two wings minus ATM. Positive = smile curved up.

**Weighted indices.** Raw weights 0.75, 0.50, 0.25, 0.10 for m = 25, 50, 75, 100, then divide by 1.6:

`w_hat = (0.46875, 0.3125, 0.15625, 0.0625)`

`skew_index = sum w_hat * RR_m`, `curve_index = sum w_hat * Fly_m`.

A missing term contributes 0; remaining weights are **not** re-normalized.

**ROC** (`2nd`–`5th` only): `B_t - B_{t-W}` for W in {5, 15, 30} minutes. Level change.

**`ts_slope`** (dashboard only): `B_2nd - B_5th`.

**6h table deltas:** current minus snapshot at or before t-6h, for backbone / skew_index / curve_index.

---

## 8. Heatmap

Columns: P100, P75, P50, P25, ATM, C25, C50, C75, C100.

Metric `x` is `IV - ATM` in wing-spread mode (default) or raw IV. Percentile is `100 * (# of history points strictly less than x) / n`, via `bisect_left`. Equals do not count as less, so a print sitting on a pile of identical history ranks at the **bottom** of that pile.

**ATM column:** blank (`n/a`) in wing-spread mode. Colored only in raw IV.

**History H.** `heatmap_fact` grouped by `(mode, expiry, moneyness, type, 30-min ET clock, event bucket, lookback, regime, weekday)`.

- Lookbacks: 6h / 1w / 1mo / full. 6h has no clock or weekday split. Longer windows: Mon–Wed vs Thu–Fri; Sunday evening counts as Monday.
- Regime: p50 / p80 / p90 of that expiry’s backbone inside the lookback (all / drop top 20% / drop top 10% / keep bottom 50% / keep top 20%).
- Keep a group if n >= n_min (5 / 5 / 10 / 20). If n >= 4, drop points outside [Q1-3 IQR, Q3+3 IQR], then re-check n_min.
- Fallback: exact key, then drop event bucket to `*`, then also drop weekday to `*`.

Lookback, regime, and event mode change **which history** is compared. They do not change live IVs.

---

## 9. Event scoring (current rule)

Rule the page now implements:

> Only options that expire **after** the print are owned by that event, especially the **same calendar day**. Once the number is out, the score is gone.

- Expiry cutoff `T_exp` = 12:00 CT on the expiry date.
- Same-day expiry uses the calibrated **`1st`** weight (previously unused because the writer never persists `1st`).
- Later living expiries use that ordinal’s weight.
- Printed events contribute **0** (no 12h afterglow, no surprise tail).
- No 0.3 floor; no 24/12/6h tier tau_before floors. Distant events decay to ~0.
- Time-only and surprise-aware **live scores match**, because there is no post-print |z| kernel.

Buckets (unchanged cutoffs): `no_event` [0, 1.627), `pre_event` [1.627, 3.238), `event_peak` [3.238, 999). Labels `t:...` / `s:...`.

The score only picks a history bucket. Switching to Event (time only) does not take “event premium” out of the quote. If a red wing turns white, the richness is ordinary for that ownership bucket, not because IV changed.

**Decay panel.** P(ATM vol lower) at 0.5 / 1 / 2 / 3 / 6 hours, event-conditioned vs unconditional, from the BentoBachelier ATM book. Nearest event is restricted to events this expiry **owns**. Still an ATM proxy for every cell.

**Yellow banner.** Global next-events list (up to 5 calendar days). Not per-expiry ownership.

---

## 10. Other charts

- **Event-distribution panel** (click a summary row): ATM backbone histogram (unconditional vs event-conditioned) and surface boxplots of the same H the heatmap used. Wing-spread omits the ATM box.
- **Metric graph:** backbone, skew/curve, RR/fly legs, ROC, `backbone_pct`, `ts_slope`. Default 1 hour.
- **Smile:** latest snapshot, x = strike, y = daily-BP IV. Call+Put at one strike is averaged.

---

## 11. Implemented vs ideated

### On the live page

Quote to bucketed surface; backbone / RR / fly / weighted indices; ROC and `ts_slope`; 6h deltas; wing-spread and raw-IV percentiles; lookback / TOD / DOW / regime filters; event **ownership** score; decay ATM-proxy panel; smile in strike space; ATM column blank in wing-spread.

### Not on the page (scientific-record experiments)

| Idea | Why it is not live |
|---|---|
| Canonical interpolated nodes at exact |delta| | Buckets do not match targets; “25-delta” is not a stable object across days (E1) |
| Minutes-to-expiry / variance time as the tenor | Ordinals are labels; Monday 09:00 `2nd` is not Tuesday 15:45 `2nd` |
| Conditional fair IV | Percentile is a hard-bucket rank, not a model residual (E2) |
| Local residual / Z_local vs today’s fitted smile | No leave-one-strike surface fit (E3) |
| Total variance, forward variance, event J_e^2 | `ts_slope` is a level gap, not incremental variance (E4) |
| Same-x and same-contract forward books | No labeled later values (E5) |
| Per-node delta-IV distribution | Decay panel is ATM history reused on every wing (E6) |
| Forecast QV at the hedge clock | Not computed (E7) |
| G + M - costs hedged P&L | Bachelier gamma carry + vol mark are specified, not run (E8) |
| Event-day scenario reprice | Needs E4–E8 first (E9) |

A future strike card (IV, ranks, local Z, fair IV, delta-IV bands, G, M, E[Pi], event-variance percentile) is a **target if those experiments produce objects worth showing**. It is not a spec for 8055. Any new UI is an isolated copy, not an edit of live V6, unless separately approved.

---

## 12. How to read (not signals)

| Pattern | What you are looking at |
|---|---|
| Put-skew bid | RR < 0, red P75/P100 on wing-spread, quiet calls |
| Call-skew bid | Opposite |
| Rich/cheap wings vs ATM | Both wings red (or both blue). ATM column is n/a in wing-spread; confirm ATM **level** on raw IV |
| ATM vol shock | Backbone jumps, ROC large, wing-spread stays mostly white |
| Event premium | Unconditional red; Event (time only); if reds fade, richness is ordinary for this still-owned print |
| After the print | Score should be `no_event` unless another future event is owned |
| Smile vs heatmap | Steep smile + mid heatmap = typical shape for this clock/regime |
| Stale / warming | Do not read ranks |

The page never becomes a long or short.

---

## 13. Parameter register (live)

| Name | Value |
|---|---|
| Dash port / Tailscale | 8055 / `:8443` |
| Writer interval | 30s |
| Adapter poll / stale | 1s / 10s |
| Dash poll | 2s |
| IV unit | daily BP, multiplier 1 |
| Expiry labels | `2nd`–`10th` |
| Heatmap-fact sample | every 60 snapshots |
| Raw retention | 30 days; facts kept |
| Cache refresh / min history | 900s / 3 days |
| Default lookback | 1 month |
| Lookback n_min | 5 / 5 / 10 / 20 |
| IQR fence | 3.0 |
| Event-day weight | `1st` row |
| Expiry cutoff | 12:00 CT |
| Score buckets | 1.627 / 3.238 |
| Decay horizons | 0.5, 1, 2, 3, 6 h |
| Event params | 2026-03-25 |

---

## 14. Files

| Path | Role |
|---|---|
| `gamma_tracker_dash_v6.py` | Live page |
| `_tmp_run_gamma_tracker_dash_v6_nosite.py` | Windows launcher used on 8055 |
| `gamma_tracker_v3.py` | Writer |
| `live_options_adapter.py` | AccessAPI to snapshot rows |
| `gamma_tracker_v2.py` | Persist + `compute_summary_metrics` |
| `event_score.py` | Ownership kernel |
| `event_params.json` / `event_decay_probs.json` | Weights and ATM decay book |
| `gamma_tracker_v6_standalone\` | Portable copy (not live 8055) |
| `tests/test_event_score_ownership.py` | Ownership tests |

Restart 8055 after dash/scorer file changes. Do not restart the writer unless asked.
