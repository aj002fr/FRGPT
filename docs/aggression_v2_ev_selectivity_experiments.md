# Aggression V2 EV Selectivity Experiments

## Executive Read

This was **research-only**. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were **not** modified. No dashboard, no SOD changes, no Telegram alerts, and no writes to `*_live/`.

Script: `scripts/aggression_v2_ev_selectivity_research.py`

Artifacts: `workspace/analysis/aggression_v2_ev_selectivity/`

## Bottom Line

**Gate FAIL. Do not build a dashboard.**

EV gating improves some realized big-move rates, but it still does not convert the short-horizon forward-return signal into positive after-cost expectancy. The best holdout after-cost cell is still negative.

| Horizon | Best EV Slice | n | After Cost | Big-Move AUC | Median Adverse |
| --- | --- | ---: | ---: | ---: | ---: |
| 1m | top 10% EV | 743 | -0.32 ticks | 0.57 | 0.0 |
| 3m | top 10% EV | 717 | -0.33 ticks | 0.59 | 0.0 |
| 5m | top 10% EV | 718 | -0.39 ticks | 0.64 | -1.0 |
| 15m | top 10% EV | 704 | -0.13 ticks | 0.57 | -1.0 |
| 30m | top 10% EV | 649 | -0.24 ticks | 0.56 | -2.0 |

The `model_all` baseline was also negative after cost at every horizon. EV selectivity did not clear the +0.5 tick threshold.

## What Was Tested

The script reused `workspace/analysis/aggression_v2_forward_return/forward_return_predictions.csv` and `forward_return_dataset.csv`.

For each horizon, it trained:

- A big-move classifier.
- An absolute-move Ridge model.
- An EV score: `P(big_move) * predicted_abs_move * direction_confidence - 0.5 tick`.

It selected top EV quantiles using an early chronological selection sample and tested on later holdout.

## Cost/Data Honesty

This is not a sample-size failure. The top-EV cells have hundreds of holdout rows, so the issue is economic: average gross edge remains below the 0.5-tick cost.

Prior AUC-passing but sample-limited work remains different:

- Resolution diagnostics top decile: `n=16`; needs `+34` conditioned rows to reach `n=50`.
- Magnitude top decile: `n=16`; needs `+34` conditioned rows to reach `n=50`.
- At a 10% top-decile selection rate, that means roughly `+340` additional holdout observations before re-gating those classifiers.

## Verdict

EV selectivity was the correct next test, but current 1-minute features still do not produce a tradable all-minutes signal after cost.

No live/dashboard work is recommended.
