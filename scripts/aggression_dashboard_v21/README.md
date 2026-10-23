# Aggression V2.1 Event Flow Paper Dashboard

Isolated display/paper dashboard for the provisional scheduled-event flow
strategy.

## Manual launch

```powershell
py -3 -m scripts.aggression_dashboard_v21.app --host 127.0.0.1 --port 5054
```

Open `http://127.0.0.1:5054/`.

## Isolation

- V1/V2/V3 application files and stores are not modified.
- All V2.1 runtime writes go to `workspace/analysis/aggression_v21_live/`.
- `workspace/economic_events.db` and V2 model artifacts are read-only.
- No SOD, Tailscale, Telegram, or automated execution integration.

## Paper semantics

The first five post-release minutes form signed flow. The paper signal locks at
T+5 and enters at the observed lock price. Outcomes are measured +5/+15/+30
minutes after lock with a 0.5-tick cost. Release-to-lock movement is diagnostic
only.

The corrected executable research does not show a validated after-cost edge.
The dashboard must remain labeled `PROVISIONAL / RESEARCH-ONLY`.

## Magnitude confidence

V2.1 also displays a `PROVISIONAL / NON-DIRECTIONAL` magnitude-confidence card.
It uses the Phase-1 selection thresholds over the same 15-row pre-window:

- realized volatility p70: `0.012949`
- price range p70: `3.0` TY ticks

`ELEVATED` means the historical holdout had a higher >=5-tick move rate
(26.2% vs 13.8%) and residual-resolution rate (52.5% vs 39.9%). It does not
predict direction and did not validate after-cost PnL.
