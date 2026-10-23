# Aggression V2 executable PnL experiments

Research-only. Live V1 / V1.2 / V2 / V2.1 / V2.2 / V2.3 / V2.4 / V2.6 / V2.7 / V3 were not modified, restarted, or written.
Writes only under `workspace/analysis/aggression_v2_executable_pnl/`. Correction cells write under `corrections/`.

Entry and exit **are** session `|residual|/tick >= G`, same-bar B1_W10 sign agree, V1 aggress-with-fade veto, and first of +N / −X / 10m on bid/ask. They are not flags on the live 10m nowcast fade.

Marks: decision = completed-bar close (`bucket_ts + 1m`). Fill = first TY print `ts_event >= decision`; long pays ask, short sells bid. Round-trip cost = 0.5 tick.
Clocks on AccessAPI TBBO prints/mid changes are labeled `NOT_MBP1_L1`. ME V2 final holdout stays locked.
`--mode corrections` reuses cached TBBO only. Live 5063 stays B1_W10.

A parquet `datetime64[us]` vs `Timestamp.value` nanosecond mix had been silencing take-profit (and, after the first corrections pass, stops). Exit times are now built from the print timestamp object. Official rank is bid/ask after cost. `ship_gate` is false.

## Isolation

| Constraint | Status |
| --- | --- |
| Edit live dashboards / ports / *_live | No |
| SOD / Tailscale / Telegram | No |
| Open ME V2 final holdout | No |
| Output dir | `workspace/analysis/aggression_v2_executable_pnl/` |
| Corrections dir | `workspace/analysis/aggression_v2_executable_pnl/corrections/` |

## Marks (C3)

Every correction cell emits `pnl_last_print`, `pnl_mid`, and `pnl_bidask_after_cost`. Official rank key is bid/ask after 0.5-tick cost. No cell may pass on last-print alone.

On the 2026-08-31 18:00 ET paper tape, last-print mean is near 0 while bid/ask is −1.37 ticks (−1.87 after cost). RTH last-print can look green while official bid/ask is still red.

## Nowcast is not a predictor (C4)

Track A R² lift vs random walk is ~10⁻⁵. Closed. This study does not fit a new Ridge. Live V2.7 median `% via price` = 0 is the same statement.

## Summary schema (I8)

`n_trades` = filled trade count. `n_tp_ticks` / `n_sl_ticks` are barrier parameters. Do not read `n` as the TP grid value.

## Correction cells (one change each)

Baseline is G=2, agree, aggress-with-fade veto, bid/ask +2/−2, re-entry after exit, min-hold 0.

### Today paper (2026-08-31 18:00 ET)

| cell | n_trades | mean_bidask | mean_after_cost | n_tp | n_sl | p_tp_before_sl |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 142 | −1.37 | −1.87 | 4 | 92 | 0.04 |
| C1 G_and_peak | 104 | −1.39 | −1.89 | 4 | 71 | 0.05 |
| C1 G_and_z | 10 | −1.10 | −1.60 | 1 | 7 | 0.13 |
| C1 RTH_only | 49 | −1.35 | −1.85 | 3 | 36 | 0.08 |
| C2 same-sign+INCREASING | 128 | −1.28 | −1.78 | 7 | 82 | 0.08 |
| I1 mid SL2/TP2 | 105 | −1.16 | −1.66 | 9 | 23 | 0.28 |
| I1 mid SL3/TP2 | 96 | −1.06 | −1.56 | 7 | 8 | 0.47 |
| I1 mid SL3/TP3 | 95 | −1.07 | −1.57 | 3 | 8 | 0.27 |
| I1 mid SL4/TP2 | 96 | −1.05 | −1.55 | 8 | 4 | 0.67 |
| I2 no same-sign re-entry | 2 | 0.00 | −0.50 | 1 | 1 | 0.50 |
| I2 15m cooldown | 52 | −1.27 | −1.77 | 2 | 31 | 0.06 |
| I3 min-hold 1m | 134 | −1.28 | −1.78 | 6 | 82 | 0.07 |
| I3 min-hold 5m | 103 | −1.24 | −1.74 | 4 | 53 | 0.07 |
| I4 first print | 142 | −1.37 | −1.87 | 4 | 92 | 0.04 |
| I4 5s TWAP then pay | 140 | −1.37 | −1.87 | 3 | 89 | 0.03 |
| I4 next 1m last | 115 | −0.69 | −1.19 | 13 | 47 | 0.22 |
| I7 passive INCREASING | 129 | −1.38 | −1.88 | 4 | 87 | 0.04 |
| combo mid(3,2)+I2+I7 | 2 | 0.50 | 0.00 | 1 | 0 | 1.00 |

C1 persist: after a baseline alert, `|session ticks| >= 2` still holds on 88% of bars at 30m, 85% at 60m, 78% at 120m. Never back inside 0 in this window. G=2 is a regime, not a 10-minute fade.

### Holdout (2026-05-13–2026-06-05)

| cell | n_trades | mean_bidask | mean_after_cost | n_tp | n_sl | p_tp_before_sl | ship_gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 907 | −1.29 | −1.79 | 91 | 651 | 0.12 | false |
| C1 G_and_peak | 438 | −1.34 | −1.84 | 46 | 332 | 0.12 | false |
| C1 G_and_z | 35 | −1.00 | −1.50 | 5 | 21 | 0.19 | false |
| C1 RTH_only | 481 | −1.27 | −1.77 | 68 | 371 | 0.16 | false |
| C2 | 742 | −1.31 | −1.81 | 70 | 539 | 0.12 | false |
| I1 mid SL3/TP2 | 593 | −1.03 | −1.53 | 99 | 94 | 0.51 | false |
| I2 no same-sign re-entry | 42 | −1.52 | −2.02 | 3 | 33 | 0.08 | false |
| I3 min-hold 5m | 621 | −1.14 | −1.64 | 76 | 346 | 0.18 | false |
| I4 next 1m last | 694 | −0.62 | −1.12 | 141 | 331 | 0.30 | false |
| I7 passive INCREASING | 771 | −1.32 | −1.82 | 69 | 557 | 0.11 | false |
| combo mid(3,2)+I2+I7 | 38 | −1.32 | −1.82 | 5 | 6 | 0.46 | false |

I1 mid(3,2) is the only cell that makes TP and SL about even. After-cost is still about −1.5 ticks. The fade often has no 2-tick mid move in its favor; giving the stop room does not create expectancy. I2 collapses n to episode count and does not lift the mean. I7/C2 cut n without beating the old aggress-with-fade “no mean lift” result.

## I5 / I6 (V1 stamp)

I5 (today only, not inside the holdout loop): TY-only paper stamp vs one-off 5050 historical POST. 04:00 ET is `IMPLIED_PASSIVE_DOMINANT` on TY-only vs `QUIET` on 5050. 12:07 / 12:15 ET are `AGGRESS_DOMINANT` on TY-only vs `QUIET` on 5050. The 5050 POST returned the same live `z_passive≈0.09` for every later stamp — treat it as “current banner,” not a true as-of reconstruction. Curve cache was not fetched (`--fetch-curve` off). I7 on holdout stays on feature-matrix `agg_1m`.

I6: today tape has no aggress series. Five-day holdout sample: 3289 bars, 3113 ready, 0 calendar-vs-session-id state disagreements (overnight included). I7 stays on the existing feature-matrix grouping. Live V1 banner was not changed.

## L1 live V2.7 autopsy (2026-09-01 18:00 ET)

See `workspace/analysis/aggression_v2_executable_pnl/corrections/v27_session_autopsy_20260902.md`.

52 trades in the window, sum dir ticks −3. 37 Z_RESET / 14 FLIP. Median `% via price` = 0. 28 of 37 Z_RESET had `% via price <= 0` (FV chased). 31 of 52 were nowcast-only (`|local z|>=1.5` and `|session ticks|<2`). Paper V1 on this session’s TY cache is `WARMING` (cache is the prior session). A 5050 historical POST at the 11:54Z −4 short timed out.

**not authorization to change 5063.**
