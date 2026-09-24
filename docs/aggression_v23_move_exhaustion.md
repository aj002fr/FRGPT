# Aggression V2.3 — Move Exhaustion Context Dashboard

## Status

User-approved isolated V2 copy with display-only **move-exhaustion context** badges from the offline research that worked (E03/E11/E12/E13/E15/E16 + reduced E18/E21/E22 readouts).

Live V2 is untouched. **Not after-cost validated** (E26 failed). Holdout was never opened.

## Isolation


| Item                       | Value                                                    |
| -------------------------- | -------------------------------------------------------- |
| Package                    | `scripts/aggression_dashboard_v23/`                      |
| Port                       | **5059** manual-only                                     |
| Store                      | `workspace/analysis/aggression_v23_live/`                |
| Live V2                    | unchanged (5052 / `aggression_v2_live/`)                 |
| SOD / Tailscale / Telegram | **not** wired                                            |
| Fixed label                | `PROVISIONAL / RESEARCH-ONLY / NOT AFTER-COST VALIDATED` |


## What each badge means


| Badge / readout                              | Research      | Meaning on the board                                                                                                  |
| -------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------- |
| **STAND ASIDE**                              | E16           | Opening 01:00–03:00 ET or calendar roll proxy (days 1–12 of Mar/Jun/Sep/Dec). Do not lean on other exhaustion badges. |
| **MOVE EVENT**                               | E01           | `B_price_plus_flow` shock: large price move z with same-sign flow z.                                                  |
| **V4 ALIGN**                                 | E03           | `                                                                                                                     |
| **WIDE SPREAD**                              | E11           | Spread in session upper regime — higher exhaustion base rate offline; non-directional context.                        |
| **BOOK FADING**                              | E12           | Microprice/imbalance no longer supporting the move (+10.4pp AUC offline).                                             |
| **DYNAMICS LEAN**                            | E13           | Residual still large while price velocity slows (+9.5pp vs static |z|).                                               |
| **CURVE WIDE**                               | E15           | Neighbour tenors confirm duration-norm flow → **continuation lean** (not a long/short).                               |
| **HIGH CONF EXH / POSSIBLE EXH / NO_SIGNAL** | E18/E27 style | Reduced live score (not the offline F7 pickle). Abstains in stand-aside and below threshold.                          |
| **Extension q50/q80**                        | E21 style     | Heuristic additional-extension risk while a move event is active.                                                     |
| **rev 5m**                                   | E22 style     | Heuristic expected reversal ticks if exhaustion lean is on.                                                           |


## Explicitly not shown as “working”

Failed / unavailable / opposite-sign results are **not** promoted: E04–E10 claims, E07 shortfall as a trade, E10 absorption→exhaustion, E17 trees, E19/E20 hazard, MBP-1/10/MBO, costed P4 (E26).

## Launch

```text
py -3 -m scripts.aggression_dashboard_v23.app --host 127.0.0.1 --port 5059
```

Open: `http://127.0.0.1:5059`

File changes require a **manual restart** of the V2.3 process to appear. Do not kill/restart without approval if already running.

## Tests

```text
py -3 -m pytest tests/test_v23_exhaustion_context.py -q
```

## Caveats

- Display / paper context only — **no** executable long/short claim.
- Live `exh_prob` is a **reduced heuristic**, not the frozen offline F7 artifact.
- Stability was **unstable** offline (E30); treat live rates as observational.
- Full observations: `docs/aggression_v2_move_exhaustion_observations.md`

