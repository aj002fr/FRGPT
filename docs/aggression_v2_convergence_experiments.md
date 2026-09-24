# Aggression V2 Convergence / Continuation / Formation Experiments

## Executive Read

This was **read-only** research. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were **not** modified. No writes to `aggression_v2_live/` or `aggression_v3_live/`. Outputs only under:

`workspace/analysis/aggression_v2_convergence/`

### Bottom line

1. **Continuation (follow the residual) fails after costs.** Pooled follow at `|z|>=2`, 30m holdout episodes: **−1.17 ticks** after 0.5-tick costs.
2. **Formation matters.** The interesting cell is **PRICE_LED + fade** (price jumped away from FV; fade back): holdout episodes **+0.68 ticks after cost**, n=73.
3. **Gate technically PASSES** on that compound (n≥50 and ≥+0.5 ticks after cost), with a formation split vs FLOW_LED.
4. **Do not build V2.1 yet without reading the caveats.** The same PRICE_LED fade rule is **not confirmed** on RTH×no-event holdout minutes (−0.65 after cost) or on live signal logs (−0.40 after cost). Train-episode after-cost is only +0.10.

**Recommendation: treat as a fragile research lead, not a ship decision.** Keep logging. Leave V1/V2/V3 untouched. Ask before any V2.1 copy.

## Isolation check


| Constraint                           | Status                                                          |
| ------------------------------------ | --------------------------------------------------------------- |
| Edit `scripts/aggression_dashboard`* | No                                                              |
| Write live V2/V3 stores              | No                                                              |
| Touch ports / SOD / Tailscale        | No                                                              |
| Outputs                              | `workspace/analysis/aggression_v2_convergence/` + this doc only |


Script: `scripts/aggression_v2_convergence_research.py`

## Data

- Walkforward Ridge predictions: `workspace/analysis/aggression_v2_tbbo_models/walkforward_predictions.csv` (~50k minutes, chronological 70/30 date split)
- Live alerts: `workspace/analysis/aggression_v2_live/alert_log.csv` (484 episodes; dates do **not** overlap walkforward holdout)
- Live signal logs: `signal_log_*.csv` (cross-check only)
- Events DB (read-only): `workspace/economic_events.db` for ±10m blackout
- Cost assumption: **0.5 tick** round-trip
- `residual_z` on walkforward: causal expanding within-day proxy (not identical to live `prior_blend_v1`)

## Idea 1 — Fade vs continuation (side-by-side)

Holdout episodes, 30m, ALL formations:


| Rule     | n   | Hit    | Mean ticks | After cost |
| -------- | --- | ------ | ---------- | ---------- |
| Fade `   | z   | >=2`   | 157        | 41.5%      |
| Follow ` | z   | >=2`   | 157        | 38.4%      |
| Fade `   | z   | >=2.5` | 74         | 44.7%      |
| Follow ` | z   | >=2.5` | 74         | 40.8%      |


Live alert episodes (fade = logged `dir_pnl`_*): after-cost expectancy is **negative** at every threshold (e.g. `|z|>=1.5` 30m fade −0.48 ticks). Continuation on alerts is also ~0 after costs.

**Verdict:** flipping to continuation does **not** create a tradeable edge. Unconditioned fade is barely positive on walkforward holdout and negative on live alerts.

## Idea 2 — Formation (PRIMARY)

Definition over prior 10 minutes: compare `|Δprice|` vs `|Δfair_value|`.

- **PRICE_LED**: price move dominates; residual change aligns with price
- **FLOW_LED**: FV/flow move dominates; residual change aligns with −ΔFV

Holdout episodes, `|z|>=2`, 30m:


| Formation | Side     | n   | Hit   | After cost |
| --------- | -------- | --- | ----- | ---------- |
| PRICE_LED | **fade** | 73  | 49.3% | **+0.68**  |
| PRICE_LED | follow   | 73  | 33.3% | −1.68      |
| FLOW_LED  | fade     | 64  | 38.2% | +0.11      |
| FLOW_LED  | follow   | 64  | 41.2% | −1.11      |


Interpretation in plain English:

- When the gap was created by **price running away from FV**, fading (expecting price to give some back / gap to shrink via price) looks better than following.
- When the gap was created by **flow/FV moving**, neither side clears a clean after-cost bar.
- This is closer to “which line moved first” than to “always fade residual z.”

### Sensitivity (required)

PRICE_LED fade `|z|>=2` 30m across slices:


| Slice                           | n    | After cost            |
| ------------------------------- | ---- | --------------------- |
| Train minutes                   | 2806 | **−0.26**             |
| Train episodes                  | 185  | +0.10                 |
| Holdout minutes                 | 925  | +0.37                 |
| Holdout episodes                | 73   | **+0.68** (gate cell) |
| Holdout RTH                     | 485  | +0.52                 |
| Holdout RTH × no-event          | 399  | **−0.65**             |
| Holdout episodes RTH × no-event | 26   | +0.85 (n<50)          |
| Live signals                    | 1264 | **−0.40**             |
| Live signals RTH × no-event     | 523  | **−0.68**             |


**RTH × no-event gate: FAIL** (n too small on episode downsample; minute slice negative).

### `price_share_of_reversion` caveat

- Filled on **196 / 484** alert episodes only
- Among filled rows, median share ≈ 0 (FV does most of the closing when the field exists)
- Do **not** over-claim the earlier ~94% figure as universal; expired episodes (265) are different

## Gate verdict

```text
passed: true
formation_ok: true          # split + positive winner (PRICE_LED fade)
expectancy_ok: true         # via best compound >= +0.5 ticks after cost
best_compound: PRICE_LED fade |z|>=2 30m  n=73  +0.68 ticks after cost
rth_no_event_passed: false
recommendation: PASS on letter of gate — but DO NOT build V2.1 without explicit approval after reading sensitivity failures
```

Artifacts:

- `fade_vs_follow_stats.csv`
- `formation_stats.csv`
- `alert_episode_stats.csv`
- `live_signal_stats.csv`
- `convergence_gate.json`
- `convergence_summary.json`
- `annotated_holdout_sample.csv`

## What this means for trading the dashboard

V2 today still mostly says “fair value is here.” This research suggests a **conditional** message that might eventually be useful:

> If `|z|` is large **and** the last 10m gap is **price-led**, lean **fade** (stand aside on follow).  
> If the gap is **flow-led**, do not lean continuation; edge is weak.

That is **not** ready for a live V2.1 banner until RTH/no-event and live-log confirmation improve.

## Phase 2 status (deferred)

- **Meta-labeling:** not run. Phase 1 did not separate cleanly across sensitivities.
- **V2.1 dashboard (`5054`):** not built. Requires your explicit approval after this report. Isolation rule still applies: fresh copy only, never edit live V2.

## What would justify shipping later

- PRICE_LED fade holds **after cost** on RTH×no-event holdout with n≥50, **and**
- Live signal-log / alert-era confirmation is non-negative after cost, **and**
- You explicitly approve a V2.1 copy on port 5054

