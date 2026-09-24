# Aggression V2.4 — Paper Trade Spec

Isolated copy of V2 that turns the DISLOCATION alert into a **fully specified paper trade**:
explicit entry, stop, target, time stop, one position at a time, and an explicit cost.

Live V1 (5050), V2 (5052), V1.2 (5056), V2.2 (5058) and V2.3 (5059) are **unchanged**.

## Why this exists

The alert told you *something is happening* but never *what to do*. V2.4 answers the
second question. It does **not** claim the answer is profitable.

The offline study behind this spec is a **FAIL**:

| Finding | Value |
| --- | --- |
| Cells scored | 576 |
| Cells passing the gate | **0** |
| Specs positive after 0.5t cost in both explore and holdout | **0 of 288** |
| Explore/holdout gross correlation | 0.234 |
| Live V2 alert gross edge at 30m | +0.022 ticks vs ~0.5t cost |

The LIFO gate helped in-sample (explore gross 0.144 vs 0.090) and **inverted out of
sample** (holdout 0.085 vs 0.139). See
`docs/aggression_v2_lifo_dislocation_trade_spec_experiments.md`.

So V2.4 runs **two books side by side** on the same live stream. Forward, genuinely
out-of-sample paper evidence is the only thing that can settle which is right:

| Book | Rule |
| --- | --- |
| `DISLOCATION_ONLY` | Fade every bar with `\|residual_z\| >= 2.0` |
| `DISLOCATION_X_LIFO` | Same, but only when `trade_intensity <= session expanding p33` |

## The trade spec

| Component | Value |
| --- | --- |
| Direction | `-sign(residual)` — fade rich/cheap |
| Entry | **next bar** after the signal bar, never the signal bar itself |
| Stop | 3 ticks |
| Target | 2 ticks |
| Time stop | 30 minutes |
| Cost | 0.5 ticks round trip, booked on exit |
| Position | one at a time; no stacking |

Entering at the signal bar's own price is look-ahead and is what made earlier versions
look better than they were. V2.4 arms on the signal bar and fills on the next one. A bar
that spans both stop and target is booked as a **stop** (conservative).

The newest bar is excluded from evaluation because it is still forming.

## Isolation

| Item | Value |
| --- | --- |
| Port | **5060** (manual only) |
| Store | `workspace/analysis/aggression_v24_live/` |
| Executable | **No** — paper only |
| SOD / Tailscale / Telegram | Not wired |
| V1 / V1.2 / V2 / V2.1 / V2.2 / V2.3 / V3 | **Not modified** |
| Research artifacts | reads shared `aggression_v2_tbbo_models/` (read-only) |

Label is fixed at `PAPER / NOT VALIDATED / NON-EXECUTABLE`.

## Launch

```text
py -3 -m scripts.aggression_dashboard_v24.app --host 127.0.0.1 --port 5060
```

Open: `http://127.0.0.1:5060`

File changes require a manual restart to appear.

## Verification

```text
py -3 -m scripts.aggression_dashboard_v24.replay_check   # engine vs offline harness
py -3 -m scripts.aggression_dashboard_v24.smoke_check    # routes, assets, payload fields
```

`replay_check` replays the live engine over the 48 explore dates and compares to the
harness. Last run: `DISLOCATION_ONLY` n=934 vs reference 951 (gross 0.110 vs 0.091) and
`DISLOCATION_X_LIFO` n=270 vs 280 (gross 0.196 vs 0.218). Small gaps come from excluding
the still-forming bar on each session. Both books show **negative net**, matching the
FAIL verdict — that agreement is the point of the check.

## Reading the page

- **ARMED** — signal fired on the last completed bar; paper entry is at the next bar.
- **IN TRADE** — shows live entry/stop/target prices, the time-stop clock, unrealized
  ticks, and running MAE/MFE.
- **Net vs gross ticks** — only net matters. 0.5 ticks is already optimistic for TY,
  where the resting spread is typically a full tick.

## Do not

Size real risk from this page, promote either book to production from live rates alone,
wire it to execution, or treat a positive paper run as a gate pass. Promotion requires a
clean pass through `scripts/aggression_gate_lib.py` on independent data plus explicit
approval.
