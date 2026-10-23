# Aggression V2 Depth Research

## Scope

Research-only MBP-10 study for the V3 market-depth plan. No V1/V2 dashboard code was changed.

## Data Coverage

- MBP-10 files inspected: 26
- Usable files (>=100 one-minute rows): 26
- UTC span: 2026-04-05T23:59:59.842214373+00:00 to 2026-05-05T23:59:57.110946003+00:00
- Sources used: prebuilt_research_features, raw_mbp10

Known limitations: ZN only, 26 calendar files, many days are April 2026 only, and this is much shorter than the TBBO model sample. Model integration should remain gated.

## Test 1: Depth Disappears / Gamma Bids


### Quintiles by `depth_chg_30m`

| variable      |   depth_quintile |    n |   median_depth |   median_variable |   rv_15m_mean |   rv_15m_median |   rv_30m_mean |   rv_30m_median |
|:--------------|-----------------:|-----:|---------------:|------------------:|--------------:|----------------:|--------------:|----------------:|
| depth_chg_30m |                1 | 5841 |        98683.6 |       -0.0560789  |    0.00985972 |      0.00835191 |    0.0102748  |      0.00855816 |
| depth_chg_30m |                2 | 5840 |        94420.3 |       -0.00451633 |    0.00845133 |      0.00806872 |    0.00874751 |      0.00813798 |
| depth_chg_30m |                3 | 5841 |        94021.3 |        0.0103118  |    0.00822784 |      0.00806872 |    0.00860109 |      0.00813798 |
| depth_chg_30m |                4 | 5840 |        95850.1 |        0.0270625  |    0.00890971 |      0.00835191 |    0.00926211 |      0.00868833 |
| depth_chg_30m |                5 | 5841 |       105360   |        0.0817982  |    0.00959908 |      0.00927526 |    0.00959718 |      0.00917532 |

Bottom-vs-top quintile 30m realized-vol ratio: `1.07x`.

### Quintiles by `depth_z`

| variable   |   depth_quintile |    n |   median_depth |   median_variable |   rv_15m_mean |   rv_15m_median |   rv_30m_mean |   rv_30m_median |
|:-----------|-----------------:|-----:|---------------:|------------------:|--------------:|----------------:|--------------:|----------------:|
| depth_z    |                1 | 5898 |        96416.6 |        -1.29414   |    0.0098105  |      0.00806872 |    0.0100839  |      0.00813798 |
| depth_z    |                2 | 5898 |        94213.5 |        -0.0955289 |    0.00870297 |      0.00835191 |    0.00904618 |      0.00820665 |
| depth_z    |                3 | 5897 |        96065.1 |         0.673779  |    0.00925848 |      0.00875956 |    0.00943717 |      0.00868833 |
| depth_z    |                4 | 5898 |        96243.5 |         1.20562   |    0.00880446 |      0.00835191 |    0.00911839 |      0.00868833 |
| depth_z    |                5 | 5898 |        97638.4 |         1.92848   |    0.00876061 |      0.00835191 |    0.00898233 |      0.00868833 |

Bottom-vs-top quintile 30m realized-vol ratio: `1.12x`.

### Quintiles by `total_depth_10`

| variable       |   depth_quintile |    n |   median_depth |   median_variable |   rv_15m_mean |   rv_15m_median |   rv_30m_mean |   rv_30m_median |
|:---------------|-----------------:|-----:|---------------:|------------------:|--------------:|----------------:|--------------:|----------------:|
| total_depth_10 |                1 | 5997 |        69198.4 |           69198.4 |    0.0084436  |      0.00715215 |    0.00860882 |      0.00710717 |
| total_depth_10 |                2 | 5996 |        85100.5 |           85100.5 |    0.00877628 |      0.00835191 |    0.00908423 |      0.00820665 |
| total_depth_10 |                3 | 5997 |        95672.8 |           95672.8 |    0.00843908 |      0.00835191 |    0.00880122 |      0.00820665 |
| total_depth_10 |                4 | 5996 |       107993   |          107993   |    0.0103044  |      0.00999907 |    0.0106317  |      0.0101206  |
| total_depth_10 |                5 | 5997 |       140799   |          140799   |    0.0094656  |      0.00875956 |    0.00986066 |      0.00868833 |

Bottom-vs-top quintile 30m realized-vol ratio: `0.87x`.

## Test 2: Depth-Conditioned V2 Dislocations

| depth_regime      |   n |   median_depth |   reversion_rate_30m |   directional_hit_rate_30m |   avg_dir_pnl_30m |   median_dir_pnl_30m |   avg_adverse_30m |   median_adverse_30m |   max_abs_z_30m_mean |
|:------------------|----:|---------------:|---------------------:|---------------------------:|------------------:|---------------------:|------------------:|---------------------:|---------------------:|
| normal            |  24 |       105878   |             0.5      |                   0.875    |          0.046224 |              0.03125 |        -0.014974  |           -0.0078125 |              2.33576 |
| thin_bottom_20pct |   7 |        78993.7 |             0.571429 |                   0.714286 |          0.03125  |              0.03125 |        -0.0245536 |            0         |              2.29779 |

## Test 3: Fair-Value Lift

| feature_set        |   features | depth_features_used                                                                                                                                    |   test_dates |   test_rows |   common_sample_rows |   oos_rmse |     oos_r2 |
|:-------------------|-----------:|:-------------------------------------------------------------------------------------------------------------------------------------------------------|-------------:|------------:|---------------------:|-----------:|-----------:|
| current            |         18 |                                                                                                                                                        |           14 |        9403 |                12734 |   0.15997  |  0.0100943 |
| current_plus_depth |         29 | total_depth_10,depth_imb_10,top_depth_share,depth_z,depth_chg_5m,depth_chg_30m,quote_ofi_1m,signed_trade_1m,cancel_to_add,cancel_imbalance,sweep_count |           14 |        9403 |                12734 |   0.167021 | -0.0790992 |

## Phase 1.5: Extra MBP-10 Diagnostics


### Composite Gamma-Bid Risk Score

|   gamma_bid_score |     n |   rv_15m_mean |   rv_30m_mean |   abs_move_15m_mean |   abs_move_30m_mean |   depth_z_q20 |   depth_chg_30m_q20 |   cancel_to_add_q80 |   rv_30m_trailing_q80 |   sweep_count_q80 |
|------------------:|------:|--------------:|--------------:|--------------------:|--------------------:|--------------:|--------------------:|--------------------:|----------------------:|------------------:|
|                 0 | 11401 |    0.00776893 |    0.00812434 |           0.016988  |           0.0249026 |     -0.604317 |          -0.0159056 |            0.998268 |             0.0119514 |                79 |
|                 1 |  9817 |    0.00909473 |    0.00934879 |           0.0217416 |           0.0299162 |     -0.604317 |          -0.0159056 |            0.998268 |             0.0119514 |                79 |
|                 2 |  5072 |    0.00990836 |    0.0103363  |           0.0248808 |           0.0348112 |     -0.604317 |          -0.0159056 |            0.998268 |             0.0119514 |                79 |
|                 3 |  2103 |    0.0105644  |    0.0109084  |           0.0254213 |           0.0329552 |     -0.604317 |          -0.0159056 |            0.998268 |             0.0119514 |                79 |
|                 4 |   649 |    0.0148476  |    0.014136   |           0.0448406 |           0.0599961 |     -0.604317 |          -0.0159056 |            0.998268 |             0.0119514 |                79 |
|                 5 |    49 |    0.016903   |    0.0158838  |           0.0655293 |           0.0868941 |     -0.604317 |          -0.0159056 |            0.998268 |             0.0119514 |                79 |

### Liquidity Replenishment After Thin Depth

| refill_regime   |    n |   median_refill_5m |   median_refill_15m |   median_refill_30m |   rv_30m_mean |   abs_move_30m_mean |
|:----------------|-----:|-------------------:|--------------------:|--------------------:|--------------:|--------------------:|
| fast_refill     | 2965 |         0.0143174  |           0.0327061 |           0.045282  |     0.0100817 |           0.0311578 |
| slow_refill     | 2964 |        -0.00425339 |          -0.0198087 |          -0.0294132 |     0.0100692 |           0.0295446 |

### Imbalance Persistence

| diagnostic               | variable        |   bucket |    n |   median_value |   rv_15m_mean |   rv_30m_mean |   abs_move_15m_mean |   abs_move_30m_mean |
|:-------------------------|:----------------|---------:|-----:|---------------:|--------------:|--------------:|--------------------:|--------------------:|
| snapshot_imbalance       | depth_imb_10    |        1 | 5997 |   -0.042879    |    0.00951251 |    0.0097508  |           0.0217544 |           0.0298443 |
| snapshot_imbalance       | depth_imb_10    |        2 | 5996 |   -0.0184691   |    0.00906417 |    0.00939667 |           0.0206531 |           0.0286402 |
| snapshot_imbalance       | depth_imb_10    |        3 | 5997 |   -0.000917168 |    0.00909387 |    0.00937427 |           0.0217791 |           0.0305322 |
| snapshot_imbalance       | depth_imb_10    |        4 | 5996 |    0.0177566   |    0.00900018 |    0.00932098 |           0.022158  |           0.0318376 |
| snapshot_imbalance       | depth_imb_10    |        5 | 5997 |    0.0448426   |    0.0087581  |    0.00914369 |           0.0213844 |           0.029404  |
| persistent_imbalance_5m  | imb_persist_5m  |        1 | 5987 |    0.00404852  |    0.00902897 |    0.00938492 |           0.0216015 |           0.0298459 |
| persistent_imbalance_5m  | imb_persist_5m  |        2 | 5986 |    0.0123079   |    0.00916292 |    0.00947562 |           0.021673  |           0.0305739 |
| persistent_imbalance_5m  | imb_persist_5m  |        3 | 5986 |    0.0218718   |    0.00910839 |    0.00931841 |           0.0214263 |           0.0306105 |
| persistent_imbalance_5m  | imb_persist_5m  |        4 | 5986 |    0.0332343   |    0.00899557 |    0.00934661 |           0.0215986 |           0.0300206 |
| persistent_imbalance_5m  | imb_persist_5m  |        5 | 5986 |    0.0543786   |    0.00913306 |    0.00944648 |           0.0214028 |           0.0290339 |
| persistent_imbalance_15m | imb_persist_15m |        1 | 5976 |    0.00408109  |    0.00908787 |    0.00943065 |           0.0219746 |           0.029574  |
| persistent_imbalance_15m | imb_persist_15m |        2 | 5976 |    0.0115645   |    0.009323   |    0.00964639 |           0.0224426 |           0.0315886 |
| persistent_imbalance_15m | imb_persist_15m |        3 | 5975 |    0.0206967   |    0.00912537 |    0.00932705 |           0.0208054 |           0.0303962 |
| persistent_imbalance_15m | imb_persist_15m |        4 | 5976 |    0.0314565   |    0.00916267 |    0.00939348 |           0.0217615 |           0.0304421 |
| persistent_imbalance_15m | imb_persist_15m |        5 | 5976 |    0.051692    |    0.0087267  |    0.00915827 |           0.0206856 |           0.0279373 |

### Sweep / Absorption

| sweep_size_bucket   |    n |   continuation_5m_rate |   continuation_15m_rate |   avg_dir_move_5m |   avg_dir_move_15m |
|:--------------------|-----:|-----------------------:|------------------------:|------------------:|-------------------:|
| small               | 7225 |               0.260484 |                0.339931 |       0.000736375 |        0.000795848 |
| medium              | 6750 |               0.284296 |                0.358667 |      -4.28241e-05 |       -0.000237269 |
| large               | 7099 |               0.338921 |                0.396112 |       0.000625088 |        0.00134372  |

### OFI vs Trade Aggression Divergence

| ofi_trade_regime   |     n |   directional_hit_15m |   avg_dir_move_15m |   rv_15m_mean |
|:-------------------|------:|----------------------:|-------------------:|--------------:|
| agree              | 10589 |              0.388139 |        0.00247013  |    0.00871415 |
| book_only          |  9586 |              0.339662 |       -0.000228197 |    0.00966026 |
| diverge            | 10473 |              0.33066  |       -0.00119653  |    0.0088378  |
| trade_only         |    14 |              0.285714 |       -0.00446429  |    0.0063139  |

### Top-of-Book vs Deep-Book Concentration

| diagnostic      | variable        |   bucket |    n |   median_value |   rv_15m_mean |   rv_30m_mean |   abs_move_15m_mean |   abs_move_30m_mean |
|:----------------|:----------------|---------:|-----:|---------------:|--------------:|--------------:|--------------------:|--------------------:|
| top_depth_share | top_depth_share |        1 | 5997 |      0.0242185 |    0.00807468 |    0.00824006 |           0.0178618 |           0.0235626 |
| top_depth_share | top_depth_share |        2 | 5996 |      0.0307123 |    0.00796568 |    0.00825479 |           0.0174726 |           0.0238857 |
| top_depth_share | top_depth_share |        3 | 5997 |      0.0358013 |    0.00922752 |    0.00963811 |           0.0214599 |           0.0307015 |
| top_depth_share | top_depth_share |        4 | 5996 |      0.041353  |    0.0103777  |    0.0107474  |           0.0262884 |           0.0365556 |
| top_depth_share | top_depth_share |        5 | 5997 |      0.050982  |    0.00978331 |    0.0101061  |           0.0246464 |           0.0355529 |

Phase 1.5 interpretation:

- **Composite gamma-bid score is the strongest diagnostic.** Future 30m realized vol rises almost monotonically from `0.0081` at score 0 to `0.0159` at score 5, and future absolute 30m move rises from `0.0249` to `0.0869` price points. The score-5 bucket is small (`n=49`), but this is much stronger than raw depth alone.
- **Liquidity replenishment is not useful by itself.** Fast refill after thin depth had almost the same 30m realized vol as slow refill (`0.01008` vs `0.01007`). It does not separate regimes in this sample.
- **Imbalance persistence is weak.** Persistent imbalance buckets do not monotonically predict higher volatility or larger moves; if anything, the highest 15m persistence bucket had lower average future moves.
- **Sweeps tend to absorb more than continue.** Even large sweep buckets continued in the sweep direction only `39.6%` over 15m. This argues against blindly chasing sweeps; they may be better as absorption/context markers.
- **OFI + trade agreement is better than divergence.** When book OFI and trade aggression agreed, 15m directional hit was `38.8%` and avg directional move was positive (`0.00247`). Divergence had lower hit (`33.1%`) and negative avg move. Still not a standalone trade signal, but useful context.
- **Top-of-book concentration matters for volatility.** Higher top-depth share buckets had materially larger future moves than low top-share buckets. This may mean liquidity concentrated near the touch is more fragile than a broadly distributed deep book.

## Artifacts

- `depth_feature_matrix`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_feature_matrix.csv`
- `depth_data_audit`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_data_audit.csv`
- `depth_vol_summary`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_vol_summary.csv`
- `depth_conditioned_episodes`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_conditioned_episodes.csv`
- `depth_conditioned_signal_summary`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_conditioned_signal_summary.csv`
- `depth_model_lift_summary`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_model_lift_summary.csv`
- `phase15_gamma_bid_score`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\phase15\gamma_bid_score.csv`
- `phase15_liquidity_replenishment`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\phase15\liquidity_replenishment.csv`
- `phase15_imbalance_persistence`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\phase15\imbalance_persistence.csv`
- `phase15_top_vs_deep`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\phase15\top_vs_deep.csv`
- `phase15_sweep_absorption`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\phase15\sweep_absorption.csv`
- `phase15_ofi_trade_divergence`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\phase15\ofi_trade_divergence.csv`
- `summary_json`: `\\100.95.155.81\FRGMSharedSpace\Aastha\market_data_puller\workspace\analysis\aggression_v2_depth\depth_research_summary.json`

## Recommendation

Review the tables above before any V3 implementation. A live depth panel is safe as display-only/risk context, but depth/OFI should not enter the model until the short ZN-only MBP-10 sample is accepted as sufficient or expanded.
