# Aggression V2.1 Trend vs Range Experiments

## Executive Read

This was a **read-only** experiment. Live Aggression V2 (`scripts/aggression_dashboard_v2/`, port `5052`) was **not** modified. All outputs live under:

`workspace/analysis/aggression_trend_range/`

We tested:

1. **Price structure** labels (`TREND_UP` / `TREND_DOWN` / `RANGE`) for next 15–60m persistence / stay-in-band.
2. A trader claim that **trends take less effort**, while **ranges show more block / large trades** (clarified: not tape color — large prints).

**Decision: FAIL the V2.1 build gate. Do not build `scripts/aggression_dashboard_v21/`.**

## Clarification: “black trades” = block / large prints

The trader meant **large / block trades**, not a UI color. Proxies used:

- `large_trade_share` = share of minute volume from large prints (already in the TBBO feature matrix)
- `avg_trade_size`
- Session-relative versions: `large_share_vs_session`, `avg_size_vs_session`

Two-sidedness was kept as secondary context only.

## Data Used

- Feature matrix (read-only): `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv`
- Sample: **57,149** scored minute bars across **92** ET dates (`2026-01-27` → `2026-06-05`)
- Chronological split: train `64` days, holdout `28` days (`2026-04-29` → `2026-06-05`)
- Gate counts use every-15-minute downsample (`n_holdout_episodes = 1011`)

Script: `scripts/aggression_trend_range_research.py`

## Gate Rules

Pass if either:

- Structure: holdout trend and range episode `n ≥ 50`, and **+8pp** lift on 30m persistence (trend vs baseline) **or** stay-in-band (range vs baseline)
- Trader/blocks: RANGE shows higher large-share or avg-size than TREND, **and** a block-conditioned overlay improves persistence or stay-in-band by **+8pp** with `n ≥ 50`

## Results

### Structure labels (holdout episodes)

| Regime | n | Persist 30m | Stay-in-band 30m |
|---|---:|---:|---:|
| TREND (UP+DOWN) | 94 | 31.0% | — |
| RANGE | 917 | — | 59.5% |
| Baseline (all) | 1011 | 38.2% | 60.6% |

Lift vs baseline:

- Trend persistence: **-7.2pp**
- Range stay-in-band: **-1.0pp**

Structure labels are **not** predictive enough on this sample.

### Block / large-trade claim (holdout)

| Bucket | n (minutes) | Median large share (5m) | Large share vs session | Median avg size (5m) | Avg size vs session | Effort vs session |
|---|---:|---:|---:|---:|---:|---:|
| TREND | 1359 | **0.478** | **1.30×** | **17.6** | **1.23×** | 2.20× |
| RANGE | 13620 | 0.463 | 1.18× | 16.7 | 1.12× | 1.47× |

Interpretation:

- **Opposite of the block-trade claim.** Structure-labeled TREND minutes had *higher* large-trade share and average trade size than RANGE, both raw and vs session.
- Effort also remained higher on TREND than RANGE (same as the earlier effort-only pass).
- Likely reason: trending stretches often include aggressive large prints that *are* the move; quiet/chop ranges can be smaller retail-sized two-way flow.

Gate flags:

- `trader_block_sep_ok = false`
- `trader_avg_size_sep_ok = false`
- `trader_effort_sep_ok = false`

### Overlay predictive lift (holdout episodes)

| Rule | n | Metric | Result |
|---|---:|---|---|
| structure_trend_only | 94 | persist 30m | 30.3% |
| trader_trend_support (low blocks + one-sided) | 1 | persist 30m | too rare |
| structure_range_only | 917 | stay-in-band 30m | 59.5% |
| trader_range_fight (elevated blocks in range) | 403 | stay-in-band 30m | 59.2% (**-0.3pp**) |

Elevated block activity inside a range does **not** improve stay-in-band vs structure-only range.

## Gate Verdict

```text
passed: false
structure_ok: false
trader_ok: false
recommendation: FAIL: do not build V2.1; keep research artifacts and leave V2 untouched
```

Artifacts:

- `workspace/analysis/aggression_trend_range/trend_range_summary.json`
- `workspace/analysis/aggression_trend_range/trend_range_gate.json`
- `workspace/analysis/aggression_trend_range/trend_range_regime_stats.csv`
- `workspace/analysis/aggression_trend_range/trend_range_trader_claim.csv`
- `workspace/analysis/aggression_trend_range/trend_range_annotated_sample.csv`

## Plain-English Takeaways

1. **Do not ship V2.1** on this evidence.
2. Interpreting “black trades” as **block/large prints** still does **not** validate the claim that ranges are where the big prints cluster — on this TY 1m sample, trends show *more* large-print intensity.
3. Path-efficiency / swing labels alone did not beat baseline for “will this keep going?”
4. V3 flow-effort remains the better live place for effort-vs-price research; this V2.1 track stays offline.

## What Would Justify Reopening V2.1

- A labeler with **≥ +8pp** holdout lift and **n ≥ 50**
- Or a block-trade overlay that both separates in the claimed direction **and** improves forward outcomes by the same bar
- Next research angle if revisited: condition on V2 alert episodes / residual-z contexts, or define “block” as absolute large-print count spikes rather than share of volume

Until then: **no** `scripts/aggression_dashboard_v21/`, **no** port `5054` dashboard, V2 on `5052` unchanged.
