# C013 — Trade-clock AF vs reversal                    tier: EXPLORATORY

## Problem statement

C001 used the last **100 LOB events**. Does signed aggressive flow on the last **100 AGG trades** (trade clock) raise P(REVERSAL) on the same prefix?

## Motivation & provenance

- Backlog **BL-10** (spec §21). Human: resume, do **not** expand discovery. Same 75-date X1 prefix.
- LOB-clock AF already failed for continuation (C001) and for LIFOScore (C012). This cycle tests a **different clock**, not a new AF transform.
- One clock only (trades). Do not also run price-change or wall clocks.

## Data window

Exactly `dates_scored` in `workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`.
Episodes: `labeled_B8_x2.parquet`. Drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`.
Store: `C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet` — `source_order`, `size`, `event_class`. One day in RAM.

## Suggested approach

Lookback: last **100 AGG_BUY/AGG_SELL** rows with `source_order < t_star_source_order` (trade count, not LOB rows).

- `AF_trade = direction × sum(+size AGG_BUY, −size AGG_SELL)` over that lookback.
- Require ≥ 8 AGG trades; report skip count.
- Rank-first terciles of `AF_trade`. Headline: P(REVERSAL|T3) − P(REVERSAL|T1), 400 date-clustered bootstrap, seed 42.
- **n-floor:** ≥ 30 per tercile. If failed, do not headline.

Do not depth-normalize. Do not compute LOB/price/wall variants. Do not open C014. No holdout.

## Success criteria

- **Advance:** T3−T1 ≥ 5pp, CI not entirely ≤0, n-floor met.
- **PARK:** lift ≤ 0 or CI-only noise or n-floor fail.
- `EXPLORATORY / NOT EVIDENCE`. No FDR.

## Constraints

Frozen configs read-only. `holdout_opened: false`. Writes only under `workspace/analysis/move_exhaustion_v2/loop/C013_trade_clock_af/` and this cycle folder. No live dashboards, D:, C014. No dates outside the 75-date prefix.

## Deliverables

- `RESULT.md` (LOOP_PROTOCOL §5; non-empty failed-tries).
- `meta.json` RESULTED with `t3_minus_t1_reversal_pp`, `n_scored`, holdout_opened false.
- `metrics.json` + episode CSV in the analysis loop folder.
