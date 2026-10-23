# Aggression V2 Options Context Experiments

## Executive Read

This was a read-only experiment. Current V2 was not modified, the gamma tracker database was not modified, and all outputs were written under:

`workspace/analysis/aggression_v2_options_context/`

The strongest result is not the original "put skew bid + fair value lower = continuation" idea. The stronger result is:

> When the gamma tracker skew move agrees with the V2 fade direction, V2 fade PnL improves.

That showed up in both episode-level and minute-level tests. However, the holdout sample is still too small to justify building V2.5 yet.

Decision: **do not build V2.5 yet**. Keep the research artifacts, keep collecting V2 episodes, and rerun after more history accumulates.

## Data Used

- V2 episode log: `workspace/analysis/aggression_v2_live/alert_log.csv`
- V2 minute signal logs: `workspace/analysis/aggression_v2_live/signal_log_*.csv`
- Gamma tracker DB: `C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db`, opened read-only
- Gamma cache used for speed: `workspace/analysis/aggression_v3_options_context/options_context_joined.csv`

Final sample:

- Episode rows: `466`
- Minute rows: `30,119`
- Date span: `2026-06-10 19:48 UTC` through `2026-07-08 14:42 UTC`

RR sign calibration:

- `corr(rr50_chg_30m, contemporaneous TY price change) = +0.0068`
- The correlation is basically zero, so the RR sign convention should be treated as weakly calibrated.
- In the script's convention for this run: positive RR change behaved slightly bullish, negative RR change was labeled put-skew-bid.

## Track A — Episode Outcomes at V2 Alert Open

### What was tested and why

This track asks whether options state at the moment a V2 WATCH/DISLOCATION opens helps predict whether the fade works.

The important test was `A2_skew_agrees_fade`: if V2 says fade long/short and the skew move points the same way, does the 30-minute fade perform better?

### Baseline

All V2 episodes:

- `n = 466`
- Resolution rate: `44.8%`
- 30m hit rate, ties as half: `52.4%`
- Average 30m directional PnL: `+0.0001`
- Average adverse 30m: `-0.0280`

WATCH-only baseline:

- `n = 329`
- Resolution rate: `57.1%`
- 30m hit rate, ties as half: `53.0%`

DISLOCATION-only baseline:

- `n = 137`
- Resolution rate: `15.3%`
- 30m hit rate, ties as half: `50.7%`

### Result

Skew agrees with V2 fade:

- `n = 58`
- Resolution rate: `43.1%` (slightly worse than all-episode baseline)
- 30m hit rate, ties as half: `62.1%`
- Improvement vs all episodes: `+9.7pp`
- Average 30m directional PnL: `+0.0151`
- Median 30m directional PnL: `+0.015625` (1 tick)
- Average adverse 30m: `-0.0267`

WATCH subset of skew-agrees-fade:

- `n = 39`
- Resolution rate: `59.0%`
- 30m hit rate, ties as half: `64.1%`
- Improvement vs WATCH baseline: `+11.1pp`
- Average 30m directional PnL: `+0.0204`

DISLOCATION subset:

- `n = 19`, too thin
- 30m hit rate, ties as half: `57.9%`
- Resolution rate: `10.5%`

### Interpretation

The episode-level result is directionally useful for PnL, not for residual resolution. In other words, skew agreeing with the V2 fade helped the 30-minute trade score, but it did not make the V2 residual more likely to close below `|z| < 1.0`.

This distinction matters. If V2.5 is ever built, this should be shown as a "fade PnL context" badge, not as "higher chance of resolved V2 signal."

Verdict: **promising but not fully validated**.

## Track B — Minute-Level Residual-Z Conditioning

### What was tested and why

This track asks whether skew improves V2's residual fade at the minute level, independent of the episode-open logic.

For minutes where V2 residual z was stretched (`|z| >= 1.5` or `|z| >= 2.0`), the test split outcomes by whether RR50 skew moved with or against the fade.

### Results

For `|z| >= 2.0`, 30-minute fade:

- Baseline: `n = 4,075`, hit rate ties-as-half `49.8%`
- Skew with fade: `n = 553`, hit rate ties-as-half `56.4%`
- Improvement: `+6.6pp`
- Average signed move: `+0.0038`
- Median signed move: `+0.015625` (1 tick)

For `|z| >= 2.0`, 15-minute fade:

- Baseline: `n = 4,119`, hit rate ties-as-half `51.6%`
- Skew with fade: `n = 567`, hit rate ties-as-half `57.9%`
- Improvement: `+6.3pp`

For `|z| >= 1.5`, 30-minute fade:

- Baseline: `n = 8,257`, hit rate ties-as-half `51.6%`
- Skew with fade: `n = 1,146`, hit rate ties-as-half `56.8%`
- Improvement: `+5.2pp`

Time-to-reversion crossings:

- All crossings: `n = 239`, resolved rate `97.1%`, median reversion `9.5m`
- Gamma-pin crossings: `n = 25`, resolved rate `100%`, median reversion `7.0m`
- No-gamma-pin crossings: `n = 214`, resolved rate `96.7%`, median reversion `11.0m`

### Interpretation

The minute-level test supports the same theme as Track A: skew agreeing with the fade seems to improve the fade's directional odds by about 5-7 percentage points.

This is useful but below the formal 8-10 percentage point gate. It is also not a pure options signal; it may simply identify a subset of V2 fades that already had better microstructure.

The gamma-pin reversion result is directionally interesting, but `n = 25` is too thin to trust.

Verdict: **weak-to-moderate support for skew as a fade-quality filter**.

## Track D — Put Skew Bid + Fair Value Lower

### What was tested and why

This was the user hypothesis:

> If put skew is bidding and V2 fair value is moving lower, maybe both hedgers and futures flow agree, so the market should continue lower.

The test treated fair-value direction as the futures-flow signal and skew direction as the options signal. It compared:

- fair-value direction only
- fair value + skew agrees
- fair value + skew disagrees
- fair value + skew flat

### Results

The original continuation idea did **not** work in this sample.

For 30-minute fair-value direction, 60-minute forward price:

- Fair-value direction only: hit rate ties-as-half `49.5%`
- Skew agrees with fair-value direction: `46.6%`
- Skew disagrees with fair-value direction: `54.6%`
- Skew flat: `49.4%`

For 15-minute fair-value direction, 60-minute forward price:

- Fair-value direction only: `49.1%`
- Skew agrees: `45.7%`
- Skew disagrees: `53.8%`

Lead split:

- Skew leading the fair-value move did not help.
- For 30-minute fair-value direction, 60-minute forward price, skew-leading hit rate ties-as-half was `48.6%`, below baseline.

### Interpretation

This rejects the simple "skew + flow agreement = continuation" framing in the current data. If anything, the better result was the opposite: skew disagreement with fair-value direction performed better over 60 minutes.

That may mean options skew is acting more like a contrarian/fade signal than a confirmation signal, or it may mean the RR sign convention is too weakly calibrated to support this interpretation.

Verdict: **reject the continuation version for now**.

## Track C — Compound / Holdout

### What was tested and why

The most promising pooled result was `skew agrees with V2 fade`, so Track C asked whether this survived a chronological holdout.

### Holdout result

Holdout all episodes:

- Baseline not shown in the summary table, but used for deltas

Holdout skew-agrees-fade:

- `n = 35`
- 30m hit rate, ties-as-half: `62.9%`
- Delta vs holdout all episodes: `+5.3pp`
- Resolution rate: `40.0%`
- Resolution delta: `+2.3pp`
- Average 30m directional PnL: `+0.0170`
- Average adverse 30m: `-0.0219`

### Interpretation

The holdout still points in the same direction: skew-agrees-fade improves 30-minute PnL quality. But it fails the pre-set gate:

- `n = 35`, below the required `n >= 50`
- Hit-rate lift `+5.3pp`, below the required `+8-10pp`

Verdict: **supportive but not enough to build V2.5**.

## What To Distrust

1. **RR sign calibration is weak.** The contemporaneous RR/price correlation was only `+0.0068`, basically flat. Sign labels like "put bid" are therefore empirical labels, not a robust structural truth yet.
2. **Episode clustering matters.** Many V2 episodes can fire on the same day/regime, so `n=466` is not 466 independent macro environments.
3. **The best pooled result is small.** `n=58` for skew-agrees-fade is useful, but not large enough to treat as production-grade.
4. **Holdout did not pass.** The effect persisted but weakened: `n=35`, `+5.3pp`.
5. **Gamma pin has too little overlap.** It looked interesting for reversion time, but the real sample is thin.
6. **This is not a fair-value feature.** The test supports options context as a filter/context badge, not a Ridge model input.

## Decision Gate

The plan required V2.5 only if holdout showed:

- `>= 8-10pp` improvement with `n >= 50`, or
- materially lower adverse/MAE with stable direction, or
- Track D skew+flow agreement beating fair-value-only by a stable margin.

The results did not meet that standard.

## Recommendation

Do **not** build V2.5 yet.

Recommended next step:

- Keep logging V2 episodes.
- Rerun `scripts/aggression_v2_options_context_research.py` after another 2-4 weeks.
- If the `skew_agrees_fade` holdout sample grows past `n >= 50` and keeps `+8pp` or better lift, then build V2.5 as a display-only copy of V2.

If V2.5 is built later, the first version should show only one options badge:

> Skew agrees with V2 fade: historically better 30m fade quality.

It should **not** show "put skew bid + fair value lower = continuation" yet, because this experiment rejected that interpretation.

