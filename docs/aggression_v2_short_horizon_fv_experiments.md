# Aggression V2 Short-Horizon Fair-Value Experiments

## Executive Read

This was **research-only**. Live V1 / V1.2 / V2 / V2.1 / V2.2 / V2.3 / V2.6 / V3 were **not** modified, restarted, or written. No `*_live/` writes. No SOD, Tailscale, or Telegram changes. The published TBBO matrix and the 2026-07-09 `aggression_v2_forward_return/` artifacts were not overwritten.

A follow-up clock study (wall vs print/volume/tick/adaptive deadlines on the same B1_W10 residuals) is in [docs/aggression_v2_alert_clock_experiments.md](aggression_v2_alert_clock_experiments.md). It does not retune live V2.7.

Script: `scripts/aggression_v2_short_horizon_fv_research.py`

Output: `workspace/analysis/aggression_v2_short_horizon_fv/`

This is **not** a retune of the failed after-cost forward-return gate, and **not** authorization to swap live V2.2 fair value.

### Bottom line

| Question | Pre-registered verdict | What it actually means |
| --- | --- | --- |
| Track A: does a new `price_{t+h}−price_t` Ridge track the next few minutes better than random walk? | **Informative** (every core horizon has R² lift > 0) | The lift is ~10⁻⁵. Same story as 2026-07-09: short-horizon **sign** skill (1m AUC 0.67, 5m 0.60) with almost no extra price-level fit beyond `price_t`. |
| Track B: does a local-anchor nowcast explain the last W minutes from flow? | **Informative** (example `B0_W3`) | Local-target R² is real for most W. `W=5` is **invalid** (`mom_5m` is the target). Fade-hit is only a few points above 50%. |
| Track C sweet spot for a 5–10m / ≤3-tick hold | **Found:** `B1_W10` + enter `|z|≥1.5` / exit `|z|<0.5` / min-hold 5m | Median lean 5.5m, 5.8 flips/hour (session raw is 7.0 and 2m), `P(5m adverse ≤ 3 ticks)=98%`, n=506. Research-only. `ship_gate=false`. |

Session raw residual sign is **too flippy** for this hold (median lean **2 minutes**, 7 flips/hour). Raw local nowcasts are worse (1 minute, 14–20 flips/hour). The hysteresis pack is what moves duration into the 5–15m band.

## Isolation Check

| Constraint | Status |
| --- | --- |
| Edit live V1 / V2 / V2.2 / V3 dashboards | No |
| Write `*_live/` | No |
| Touch ports / SOD / Tailscale / Telegram | No |
| Overwrite `tbbo_feature_matrix.csv` | No |
| Overwrite `aggression_v2_forward_return/` | No |
| Build or retune V2.2 live FV | No |
| New outputs | `workspace/analysis/aggression_v2_short_horizon_fv/` + this doc |

## Hold Profile (frozen)

These numbers defined the grid. They are not a live-trade spec.

- Typical hold: 5–10 minutes
- Typical wrong-way: about 3 ticks maximum
- Too flippy: lean duration < 3 minutes
- Too sticky: lean duration > 20 minutes
- Sweet-spot duration band: [5, 15] minutes

## Sample

- Input: `workspace/analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` (read-only)
- Raw rows: 57,179
- Clean rows / dates after the V2 model-research filter: 54,835 / 74
- Walk-forward: expanding by ET date, `MIN_TRAIN_DAYS=5`, RidgeCV + StandardScaler (live V2 alphas)
- Locked last 20% dates (15 days): 2026-05-13 through 2026-06-05

## What Was Tested

### Track A — forward FV

```text
y_A = price_{t+h} - price_t
FV_A(t) = price_t + Ridge(X_session, y_A)
```

Horizons: 3 / 5 / 8 / 10 (core), 1 and 15 (diagnostic). No 30m cell.

### Track B — local nowcast

```text
y_B = price_t - price_{t-W}
FV_B(t) = price_{t-W} + Ridge(X, y_B)
```

Anchors `W ∈ {3, 5, 8, 10, 15, 30}`. Packs: **B0** session features; **B1** short pack (`agg_1m`/`agg_5m`, local cumulated flow, `realized_vol_5`, `minutes_since_anchor`; no `agg_30m`).

### Track C — lean display rules (no new Ridge)

On every construction’s holdout residuals:

- Raw sign after a 1-tick dead zone
- Frozen hysteresis: enter `|z| ∈ {1.0, 1.5, 2.0}`, exit `|z| ∈ {0.5, 1.0}`, min-hold `{0, 5}` minutes
- Fade-the-residual episodes, 5-minute decluster, 5m/10m path adverse, `P(adverse ≤ 3 ticks)`

## Holdout Results

### Track A

| Horizon | OOS R² of `price_{t+h}` | Random-walk R² | R² lift | Sign AUC | Rank IC |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.99843 | 0.99841 | +0.000027 | 0.670 | 0.208 |
| 3 | 0.99633 | 0.99632 | +0.000016 | 0.622 | 0.164 |
| 5 | 0.99430 | 0.99429 | +0.000003 | 0.600 | 0.135 |
| 8 | 0.99119 | 0.99117 | +0.000017 | 0.592 | 0.132 |
| 10 | 0.98918 | 0.98915 | +0.000025 | 0.591 | 0.128 |
| 15 | 0.98438 | 0.98438 | +0.000006 | 0.578 | 0.107 |

R² is high because `price_{t+h}` is already almost `price_t`. The model does not draw a useful new fair-value line. Raw Track A leans barely fire (`n` about 5–19) because `residual = −predicted_fwd` is usually inside the 1-tick dead zone.

This matches [docs/aggression_v2_forward_return_experiments.md](aggression_v2_forward_return_experiments.md): short-horizon classification edge, not an expected-move edge. After-cost tradability is **not** reopened.

### Track B

| Cell | Local-target OOS R² | Tracking MAE (ticks) | Fade hit 5m | Fade hit 10m |
| --- | ---: | ---: | ---: | ---: |
| session (session target) | 0.324 | 5.66 | 0.511 | 0.519 |
| B0_W3 | 0.550 | 0.65 | 0.542 | 0.527 |
| B1_W3 | 0.700 | 0.56 | 0.554 | 0.543 |
| B0_W5 / B1_W5 | 1.000 | ~0 | 0.570 | 0.563 |
| B0_W8 | 0.610 | 0.87 | 0.524 | 0.513 |
| B1_W8 | 0.764 | 0.72 | 0.529 | 0.519 |
| B0_W10 | 0.525 | 1.06 | 0.525 | 0.515 |
| B1_W10 | 0.724 | 0.85 | 0.525 | 0.514 |
| B0_W15 | 0.436 | 1.38 | 0.515 | 0.514 |
| B1_W15 | 0.670 | 1.10 | 0.512 | 0.512 |
| B0_W30 | 0.625 | 1.61 | 0.508 | 0.505 |
| B1_W30 | 0.627 | 1.61 | 0.509 | 0.505 |

Caveats:

1. **`W=5` is a specification leak.** Live feature `mom_5m = price_t − price_{t-5}` is exactly `y_B` at `W=5`, so R²=1 and residual≈0. Those cells are not evidence.
2. **`session_r2_local_target` is largely negative** because session FV is a session-scale level. Beating it on a 3–15m increment is not the same as beating session nowcast quality.
3. B1 beats B0 on local R² at every non-leaked W except 30m, where they are tied.
4. Fade-the-residual hit rates stay near 51–55%. That is not a tradable edge.

### Track C (holdout)

Session raw lean: median duration **2.0m**, **7.0 flips/hour**, 22% of 10-minute windows have ≥3 changes. **Too flippy.**

Raw local nowcasts: median duration **1–2m**, **14–20 flips/hour**. Also too flippy. `W=5` raw emits no lean (zero residual).

Hysteresis is what creates a 5–10m lean. Among duration-band cells with flip rate no worse than session raw, the pre-registered winner is:

| Field | Value |
| --- | --- |
| Construction | `B1_W10` (10-minute local anchor, short feature pack) |
| Rule | enter `|z|≥1.5`, exit `|z|<0.5`, min-hold 5m |
| Median lean duration | 5.5 minutes |
| Flips / hour | 5.81 (session raw 6.98) |
| Median minutes between changes | 6.0 |
| Share of 10m windows with ≥3 changes | 4.3% (session raw 22%) |
| `P(5m adverse ≤ 3 ticks)` | 0.980 |
| `P(10m adverse ≤ 3 ticks)` | 0.932 |
| Median adverse | 0 ticks (5m), 1 tick (10m) |
| Episodes (5-minute decluster) | 506 |

Nearby cells (same band, slightly lower 3-tick rate): `B1_W10` enter 1.5 / exit 1.0 / hold 5; `B1_W8` and `B0_W10` with enter 1.5 / exit 0.5 / hold 5.

High `P(adverse ≤ 3)` is easier here than on session FV because local residuals are typically **sub-tick to ~1 tick**. The path can stay inside 3 ticks without that being a large economic edge. Treat the sweet spot as a **stability** result, not a PnL result.

## Verdicts (pre-registered, non-tradability)

- Track A **informative** by the letter of the R²-vs-random-walk rule. Economically empty as a new fair-value line.
- Track B **informative** for local-move fit (B1 better than B0; ignore `W=5`). Fade quality is not materially better than session.
- Track C **sweet-spot candidate:** `B1_W10` + `e1.5_x0.5_h5`.
- **`ship_gate`: false.** No V2.2 overlay, no live FV swap, no after-cost PASS.

## Artifacts

| Artifact | Path |
| --- | --- |
| Summary / verdicts | `workspace/analysis/aggression_v2_short_horizon_fv/summary.json` |
| All-OOS construction metrics | `construction_metrics_all_oos.csv` |
| Holdout construction metrics | `construction_metrics_holdout.csv` |
| Track C stability grid | `track_c_stability.csv` |
| Walk-forward fold log | `walkforward_folds.csv` |
| OOS / holdout predictions | `oos_predictions.csv`, `holdout_predictions.csv` |
| Plots | `track_a_r2.png`, `flip_vs_sticky_raw.png` |

## What This Means

If the goal is a fair-value line that **tracks the next 5–10 minutes of price**, a forward Ridge on the current V2 features does not beat “price is here now.”

If the goal is a **more local nowcast**, a 8–10 minute rolling anchor plus the short pack (`B1`) explains the recent move better than session-from-open Ridge. Raw residual sign will chatter. A 1.5z enter / 0.5z exit / 5-minute min-hold is the frozen cell that sat in the 5–15 minute duration band without flipping more than session raw.

That cell is a research display candidate only. It does not reopen the 2026-07-09 after-cost FAIL and does not change live V2.2.
