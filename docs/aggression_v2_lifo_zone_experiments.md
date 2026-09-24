# Aggression V2 LIFO-Zone Experiments

Research-only. Live V1 / V2 / V2.2 / V2.7 were **not** modified, restarted, or written.
No `*_live/` writes. No SOD, Tailscale, or Telegram changes. H5 thresholds were not retuned.

Script: `scripts/aggression_v2_lifo_zone_research.py`  
Output: `workspace/analysis/aggression_v2_lifo_zone/`  
Run date: 2026-09-07  
`ship_gate`: **false**

## Executive read

The live **LIFO FRIENDLY** banner (H5: `|z|≥1` and quiet trade intensity) is pointed at the **wrong object** for the trade you described.

Your trade needs a two-way box with at least 3 ticks of meat. H5 finds a quiet leftover. On this tape those are different things:

- A 15-minute LIFO zone is uncommon (**9.9%** of nonevent minutes).
- Half of 15-minute windows already have ≥3 ticks of range (**51%**). Almost all are “contained” under an 8-tick net cap (**99%**). The scarce piece is the **reversal** (**10.3%** two-way). The zone rate is basically the two-way rate.
- Only **5%** of ATR CONTINUED_RANGE minutes are 15-minute LIFO zones. H5’s old label is not a proxy for this one.

When H5 is on, the next 15 minutes is a LIFO zone **less often** than the nonevent baseline: validation **4.2% vs 8.5% (−4.3pp)**, discovery −3.8pp, FINAL −4.4pp. Quiet intensity alone is −3.9pp. `|z|≥1` alone is about zero. So the banner’s work is done by **low activity**, and low activity is where 3-tick two-way does **not** happen.

H5 plus “already swinging” (trailing 15m high–low ≥3) only cancels the penalty (about flat). H5 on a dead trailing window is the worst cell (−5.2pp). BuySell flips and flow/price disagreement do not rescue it.

The guessed anti-LIFO veto is backwards: **high** intensity minutes have a slightly **higher** zone rate. You need prints to get a 3-tick swing.

A fade of the last 3-tick / 5-minute push while H5 is on almost never fires (validation n=3). Quiet tape and a fresh 3-tick push rarely coexist.

Do not change the live LIFO banner from this run. If the next experiment happens, hunt two-way meat in **moderate** intensity, not the quiet tercile.

## Goal

Identify LIFO zones — two-way boxes with ≥3-tick meat — from existing flow flags.  
Not a fade-to-fair-value clock. Not the 2026-07-17 CONTINUED_RANGE label.

## Branch 0 label (frozen)

- Horizons: 15 and 30 minutes. Primary **15m** (closer to a quick clip).
- Meat: forward high–low ≥ 3 ticks.
- Contained: |net| < 8 ticks.
- Two-way: first 3-tick push, then a 3-tick retrace from that extreme.
- Path: a 3-tick swing before a 2-tick move against that swing (either direction).
- Event-near ±30m censored. Incomplete horizons censored.
- Splits (same chronological recipe as the V1 dispersion work): discovery 2026-01-27…2026-04-08 (43), validation 2026-04-10…2026-05-08 (19), FINAL 2026-05-12…2026-06-05 (16). 78 clean dates, 57,125 rows.

## Base rates (nonevent, scored minutes)

| Piece | 15m | 30m |
| --- | ---: | ---: |
| LIFO zone | 9.9% | 27.3% |
| Meat (≥3 tick range) | 51.1% | 78.0% |
| Contained (\|net\|<8) | 98.7% | 96.1% |
| Two-way reversal | 10.3% | 29.5% |
| Path (3 before 2) | 33.7% | 52.0% |

The 8-tick contain cap is almost vacuous at 15 minutes. Tightening it would change n, not the H5 story.

## Overlap vs CONTINUED_RANGE (30m, k=1 ATR)

| Conditional | Rate |
| --- | ---: |
| P(LIFO_ZONE 15m \| CONTINUED_RANGE 30m) | **5.1%** |
| P(CONTINUED_RANGE 30m \| LIFO_ZONE 15m) | 31.0% |
| Both (minutes) | 1,707 |

ATR-range is “didn’t travel far.” LIFO zone is “traveled and came back.” Most ATR-range minutes have no 3-tick reversal in the next 15 minutes.

## Branch A — frozen H5 against the new label

Validation, 15m zone, 15-minute decluster. Baseline 8.5%.

| Flag | n | rate | lift pp | read |
| --- | ---: | ---: | ---: | --- |
| `large_z` only | 682 | 7.6% | −0.9 | leftover size is not the zone |
| `intensity_lo` only | 478 | 4.6% | **−3.9** | quiet is anti-zone |
| **`h5` (live rule)** | 313 | 4.2% | **−4.3** | quiet leftover ≠ LIFO box |
| `h5_and_trail_meat` | 120 | 7.5% | −1.0 | “already swinging” only undoes the hole |
| `h5_quiet_dead` | 243 | 3.3% | **−5.2** | the H5 mass is dead tape |
| `h5_and_widen` | 82 | 4.9% | −3.6 | widening leftover does not add meat |

Discovery and FINAL agree on the sign for `h5` (−3.8pp / −4.4pp). This is not a split accident.

On the looser 15m **path** metric (any 3-tick swing before 2-tick adverse), `h5` is worse still (−9.0pp validation). Quiet tape does not even give you the one-way 3-tick clip.

## Branch B — two-way flow signatures

None raise 15m zone rate on validation.

| Flag | n | lift pp |
| --- | ---: | ---: |
| `buysell_two_way` | 484 | −0.4 |
| `disagree_15m` | 496 | −0.8 |
| `wing_vs_ty` | 703 | −1.1 |
| `large_share_lo` | 601 | −2.5 |
| `h5_and_disagree` | 106 | −4.7 |
| `h5_and_flips` | 167 | **−6.1** |

H5 plus BuySell flipping is the worst zone flag: sign changes in a quiet book without a 3-tick reversal.

## Branch D — anti-LIFO veto

The pre-registered veto (hot tape / H5 off and intensity high) has a **higher** zone rate than baseline on validation (about +1.3pp). That is the wrong sign for a stand-aside. Swings need activity. A useful veto is closer to **H5 itself** (quiet leftover) than to aggress-hot.

## Branch C — fade the last 3-tick / 5m push when a zone flag is on

| Flag | discovery n | val n | FINAL n | val hit |
| --- | ---: | ---: | ---: | ---: |
| `h5` | 18 | **3** | 2 | 67% |
| `h5_and_trail_meat` | 18 | 3 | 2 | 67% |
| `h5_and_flips` | 12 | 3 | 0 | 67% |

Not enough episodes. H5 and a fresh 3-tick push almost never sit on the same bar. Hit rates on n=3 are not evidence.

## What this means

1. **Do not read live LIFO FRIENDLY as “good for 3-tick clips.”** It is a quiet-leftover / ATR-range lean. On this label it is a negative.
2. **Meat is common; the reversal is the product.** Next work should predict two-way, not “won’t break 8 bps.”
3. **Look in moderate activity**, not the low tercile. Quiet is where the box has no meat.
4. V2.7’s leftover remains a consistency check on the last 10 minutes. It is not a zone detector and was not used as one here.
5. `ship_gate` false. No live banner, clock, or port change.

## Isolation

| Constraint | Status |
| --- | --- |
| Edit live V1 / V2 / V2.2 / V2.7 | No |
| Write `*_live/` | No |
| Retune H5 thresholds | No |
| Overwrite `tbbo_feature_matrix.csv` | No |
| New outputs | `workspace/analysis/aggression_v2_lifo_zone/` + this doc |

Tests: `tests/test_v2_lifo_zone.py`

## L1–L4 run (2026-09-08)

Ran `scripts/aggression_v2_lifo_literature_research.py`. Outputs only under `workspace/analysis/aggression_v2_lifo_literature/`. Full freeze + results: `docs/aggression_v2_lifo_literature_experiments.tex`. This file’s 2026-09-07 H5 story is unchanged.

**Which model:** same-bar \(R^2(\Delta P, \texttt{agg\_1m})=0.25\) on validation; lead-1 \(R^2=0.001\). Contemporaneous flow–price is real and is not a 15-minute zone. Two-sidedness persists only +2.6pp.

| Cell | Val lift | Gate |
| --- | ---: | --- |
| L1 `mod_twosided` | −2.1pp | FAIL |
| L2 `absorb_extreme` zone | +8.3pp | PASS (not a ship: fade hit 48%, `absorb_bar` alone −0.5pp) |
| L3 `flippy_active` | +1.2pp | FAIL |
| H5 control | −4.3pp | FAIL (still anti-zone) |

`ship_gate` false. No live banner or H5 change.

Follow-up (same day): past flow does not forecast future flow at \(W\in\{1,5,15,30\}\), \(H\in\{1,5,15\}\). Oracle \(\Delta P\sim\) actual future flow is strong (\(0.25/0.40/0.52\)); two-stage \(\lambda\times\hat F\) is not. See `docs/aggression_v2_lifo_literature_experiments.tex` and `workspace/analysis/aggression_v2_lifo_flow_forecast/`.

Path follow-up (same day, event-censored): trail meat buys meat not the reversal; last-15m two-way fails validation; fade of a 3-tick push is 53%; US morning 08:00–12:00 ET letters the zone on all splits. `ship_gate` false. See `workspace/analysis/aggression_v2_lifo_path_followup/`.
