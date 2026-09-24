# Aggression V2 Meta-Label Experiments

## Executive Read

This was **research-only**. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were **not** modified. No dashboard, no alert-emission change, and no writes to `*_live/`.

Script: `scripts/aggression_v2_meta_label_research.py`

Artifacts: `workspace/analysis/aggression_v2_meta_label/`

## Bottom Line

**Gate FAIL. Do not build a dashboard.**

Meta-labeling the existing V2 fade signal improves the selected subset versus all alerts, but not enough to become positive after cost. The classifier AUC is only `0.54`.

| Cell | n | AUC | After Cost | Hit Rate | Lift vs All |
| --- | ---: | ---: | ---: | ---: | ---: |
| all alerts | 156 | 0.54 | -0.28 ticks | 46.8% | 0.00 |
| take top 30% | 51 | 0.54 | -0.13 ticks | 54.9% | +0.15 |
| take top 20% | 36 | 0.54 | -0.14 ticks | 55.6% | +0.14 |
| take top 10% | 25 | 0.54 | -0.34 ticks | 56.0% | -0.06 |

The top-30% subset barely clears `n>=50`, but expectancy remains negative and AUC is below 0.6.

## Important Data Constraint

EV feature overlap with live V2 alerts was `0` rows. The forward-return / EV minute research ends before the current live-alert window, so this meta-label test used open-time alert/signal features only:

- `open_abs_z`
- `prior10_realized_vol`
- `prior10_range_ticks`
- `prior10_abs_z_mean`
- `prior10_d_price_ticks`
- `prior10_d_fv_ticks`
- `formation_sign`
- alert `state` / `max_abs_z`

This means the EV-selective machinery cannot yet be validated on the live alert sample without more overlapping history.

## Cost/Data Honesty

This is not a pure sample-size failure. Top-30% holdout has `n=51`, but after-cost expectancy is still `-0.13` ticks.

The real blocker is feature quality: open-time alert features weakly rank profitable fades but do not separate enough to overcome cost.

## Verdict

Meta-labeling is directionally cleaner than the previous toxic stand-aside label, but current live-alert features are not enough. Keep logging; do not change V2 alert emission.
