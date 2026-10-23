# Aggression Dashboard V2 Research

Research-only dashboard for TBBO-backed fair-value residual studies.

This app reads artifacts already generated under:

`workspace/analysis/aggression_v2_tbbo_models/`

It does not fit models, does not fetch live data, and does not modify the V1 live dashboard.

## Run

```powershell
cd Y:\Aastha\market_data_puller
py -3 -m scripts.aggression_dashboard_v2.app --host 127.0.0.1 --port 5052
```

Open:

`http://127.0.0.1:5052`

## Current Use

- Review walk-forward model metrics.
- Review Ridge residual fair-value charts.
- Review holdout rules and conditional signal tests.
- Read the model/signal explainer.

This is not a live trading monitor.
