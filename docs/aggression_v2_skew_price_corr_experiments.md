# TY Skew-Change vs TY Price Correlation Study

## Executive Read

This was read-only research. The gamma tracker DB was not opened; existing
1-minute joined artifacts were used. V1/V2/V3 and all live stores were
untouched.

Script: `scripts/aggression_v2_skew_price_corr_research.py`

Artifacts: `workspace/analysis/aggression_v2_skew_price_corr/`

## Bottom Line

**TY skew changes do not show a stable linear or rank relationship with TY
price changes in the available sample.**

- No positive lag where skew leads price survives Benjamini-Hochberg FDR.
- No contemporaneous or negative-lag relationship survives FDR either.
- The prior RR50 30-minute near-zero result is reproduced:
  - Pearson: `0.0051`
  - 95% date-clustered CI: `[-0.0352, +0.0380]`
  - Spearman: `0.0023`
  - n: `9,488`, skew-active days: `11`

## Data

| Source | Price | Rows with RR50 | Skew-active days |
| --- | --- | ---: | ---: |
| V2 primary | `v2_price` | 9,981 | 11 |
| V3 cross-check | coalesced `price` / `tbbo_price` | 11,579 | 14 |

Metrics tested:

- `rr25`
- `rr50`
- `rr75`
- `skew_index`

All changes and price returns were computed within ET date. `opt_futures_px`
was not used because it is empty in the saved joined artifacts.

## Contemporaneous Results

The largest raw Pearson result was V2 RR75 at 30 minutes:

- Pearson `0.094`
- date-clustered CI `[+0.001, +0.221]`
- BH-adjusted p `0.535`

It does not survive multiple-testing correction and is smaller in the
cross-check source (`0.073`, CI includes zero).

Several short-horizon raw correlations are approximately `-0.04` to `-0.06`,
but none survives FDR or replicates strongly across both sources.

## Lead/Lag Results

Positive lag means the trailing five-minute skew change is measured first and
TY price is measured afterward.

The largest raw positive-lag Pearson relationship was:

- V2 `skew_index`, price +5m: `0.042`
- CI `[-0.004, +0.076]`
- BH-adjusted p `0.662`

The next notable cell was V2 RR50, price +5m:

- Pearson `0.032`
- CI `[+0.001, +0.059]`
- BH-adjusted p `0.535`

Neither is significant after FDR, and neither is strong enough to suggest an
economically useful standalone predictor.

No negative-lag (price leads skew) or zero-lag cell survives FDR either.

## Statistical Method

- Pearson and Spearman correlations.
- 10,000-draw date-clustered bootstrap confidence intervals.
- Daily-correlation tests.
- Benjamini-Hochberg FDR at `q=0.10` pooled across:
  - 32 contemporaneous cells,
  - 88 lead/lag cells,
  - both Pearson and Spearman methods.

The effective history is still short: 11-14 skew-active trading days despite
roughly 10,000 minute observations. The day-clustered inference prevents the
minute count from overstating confidence.

## Verdict

There is no evidence that broad TY skew changes should be added as a direct
price-direction signal.

This does not rule out narrower conditional uses—for example skew around
specific event windows or extreme skew shocks—but those would require a new
pre-registered study. Correlation alone would not justify a dashboard or trade
signal.

## Artifacts

- `contemporaneous_corr.csv`
- `lead_lag_corr.csv`
- `skew_price_corr_summary.json`
- `lead_lag_heatmap.png`
