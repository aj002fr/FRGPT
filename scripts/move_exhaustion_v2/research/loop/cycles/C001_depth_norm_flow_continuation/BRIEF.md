# C001 — Depth-normalized aggressive flow vs continuation    tier: EXPLORATORY

## Problem statement

Conditional on comparable preceding price moves (same primary-cell episodes as X1), does stronger depth-normalized confirming aggressive flow at `t*` raise continuation probability?

## Motivation & provenance

- Backlog **BL-1** (retired X2 science). GPT Sol readiness review (2026-08-13) recommended this as the first cycle, on the **same 75-date X1 prefix**, not a different sample.
- X1 prefix baseline on record: primary B=8/x=2/R=4/C=4/H=15, dates 2022-01-05 through 2022-04-25, P(REVERSAL)=0.3529, P(continuation)=0.3336, P(unresolved)=0.3135, 854 resolved / 1244 scored.
- Nothing has been tried on flow yet. OQ-13 (which AF normalization is headline) stays open; this cycle tests **one** choice only: depth-normalized AF at N=100. Other N/normalizations are later cycles.

## Data window

Discovery prefix only: **exactly** `dates_scored` in
`workspace/analysis/move_exhaustion_v2/experiments/X1_20260813T143427Z/manifest.json`
(2022-01-05 through 2022-04-25, 75 dates). Reuse
`labeled_B8_x2.parquet` from that X1 run. Read matching store days from
`C:\FRDBs\FutMBP\move_exhaustion_v2_store\{date}.parquet`.

Do not read validation dates, holdout dates, or any store parquet whose date is not in that `dates_scored` list.

## Suggested approach

Advisory, not binding. A lean version that matches the question:

1. Load X1 labeled episodes. Keep scored outcomes only (drop `UNRESOLVED_AMBIGUOUS` and `CENSORED_INCOMPLETE_HORIZON`). Continuation = outcome `CONTINUATION`.
2. For each episode, on that day's store frame, take events with `source_order < t_star_source_order` (strictly before `t*`; causal). Among those, the last **N=100** rows by canonical order (`ts_event`, `sequence`, `source_order`).
3. Signed aggressive size: `event_class==AGG_BUY` → +size; `AGG_SELL` → −size; other classes 0. Direction-align: `AF_dir = direction * sum(signed_size)` so confirming flow is positive.
4. Depth at `t*`: `pre_bid_sz + pre_ask_sz` on the t* row (fail closed if missing/≤0). `AF_depth = AF_dir / depth`.
5. Match on **move-magnitude tercile** of `|t_star_mid|` path already in the episode via `mae_ticks`/`mfe_ticks` is wrong — use the qualifying move size implied by B=8 (all these episodes already qualified at B). If a within-sample magnitude proxy exists (e.g. `mfe_ticks` tercile as stretch), use **one** tercile of `mfe_ticks` as the matched bucket. Vol regime / TOD / velocity: skip (X1 deferred them).
6. Within each bucket, quintiles of `AF_depth`. Headline: pooled continuation rate Q5 minus Q1, with **date-clustered** bootstrap interval (resample dates). Also report vs X1 prefix P(continuation)=0.3336.
7. Write a small CSV of per-episode (`episode_id`, `trading_date`, `AF_depth`, `outcome`, `bucket`) and a metrics JSON.

Do not run N∈{50,250} or a second normalization in this cycle.

## Success criteria

- **Advance (ITERATE candidate):** Q5−Q1 continuation lift ≥ 5pp and the date-clustered interval is not entirely ≤ 0; n per tail quintile ≥ 30 resolved.
- **Kill / PARK:** lift ≤ 0, or the lift is confined to one calendar month, or n too small to estimate.
- Exploratory: **no FDR verdict**, no PASS/FAIL gate language. Label everything `EXPLORATORY / NOT EVIDENCE`.

## Constraints

- Frozen configs are read-only. Do not edit YAML, X0/X1 contracts, or live dashboards.
- Causality: features use only events with `source_order < t_star_source_order`. No centered windows, no future data.
- `holdout_opened: false`. Never open holdout parquet.
- Writes only under `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/` plus this cycle folder (`RESULT.md`, `meta.json` fields).
- Do not write D:, `*_live/`, SOD, Tailscale, Telegram, or any `scripts/aggression_dashboard_*`.
- Do not print secrets. Do not start C002.

## Deliverables

- `scripts/move_exhaustion_v2/research/loop/cycles/C001_depth_norm_flow_continuation/RESULT.md` (template in LOOP_PROTOCOL.md §5). Must include a non-empty "things tried that failed".
- Update the same folder's `meta.json`: `status=RESULTED`, `worker_model`, `headline_metrics` (include `q5_minus_q1_continuation_pp` and `n_scored`), `holdout_opened=false`, `data_window_used` unchanged.
- Artifacts under `workspace/analysis/move_exhaustion_v2/loop/C001_depth_norm_flow_continuation/` (`metrics.json`, episode CSV).
