# Unified Dashboard Runbook

The live unified dashboard replaces the frozen `dashboards/*_dd.html` snapshots for day-to-day use.
Open the local server instead:

```powershell
python scripts/run_server.py --event NFP --port 8800 --workers 1 --threads 2
```

Then browse to `http://localhost:8800/`. The page has one event selector (`NFP`, `CPI`, `PPI`, `JOLTS`) and the Pre Event / Post Event tabs in one dashboard.

## Data Sources

- Economic releases, actuals, consensus, and consensus std dev: `workspace/economic_events.db` (`bbg_data` primary for recent releases).
- Historical TY price paths: PostgreSQL `ohlcv_1m`, served through local L1 Arrow files under `data/event_features/`.
- New/missing TY bars: Databento via `scripts/backfill_ty_from_databento.py`, then inserted into `ohlcv_1m` as continuous `TY`.
- Live TY price: Databento live state, cached by the server.

## Manual Refresh

Run the full refresh cycle:

```powershell
python scripts/refresh_dashboard_data.py
```

Common variants:

```powershell
python scripts/refresh_dashboard_data.py --force-l1
python scripts/refresh_dashboard_data.py --skip-staleness --skip-backfill
python scripts/backfill_ty_from_databento.py --dates 2026-05-08
```

Expected L1 outputs:

- `data/event_features/<EVENT>/<YYYY-MM-DD>.meta.json`
- `data/event_features/<EVENT>/<YYYY-MM-DD>.price_1m.arrow`
- `data/event_features/<EVENT>/<YYYY-MM-DD>.price_5m.arrow`
- `data/event_features/<EVENT>/<YYYY-MM-DD>.stats.arrow`

## Scheduled Refresh

Install or refresh the scheduled tasks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_dashboard_refresh_task.ps1
```

This creates:

- `UnifiedDashboardRefreshNightly` at 02:30 daily.
- `UnifiedDashboardRefreshPostRelease` at 09:15 on weekdays.

Both call `scripts/run_dashboard_refresh.bat`, which logs locally under `%LOCALAPPDATA%\market_data_puller\logs\dashboard_refresh.log` and mirrors to `workspace/logs/dashboard_refresh.log`.

## Verification

```powershell
python - <<'PY'
import requests
base = "http://127.0.0.1:8800"
print(requests.get(base + "/api/event_types").json()["event_types"])
print(requests.get(base + "/api/events", params={"event_type": "NFP"}).json()["events"][-1]["date"])
print(requests.get(base + "/api/live").json().get("source"))
PY
```

On PowerShell, use:

```powershell
@'
import requests
base = "http://127.0.0.1:8800"
print(requests.get(base + "/api/event_types").json()["event_types"])
print(requests.get(base + "/api/events", params={"event_type": "NFP"}).json()["events"][-1]["date"])
print(requests.get(base + "/api/live").json().get("source"))
'@ | python -
```

## Troubleshooting

- **Port 8800 already in use**: run `netstat -ano | Select-String ':8800'`, stop stale dashboard Python PIDs, then restart.
- **No latest event details**: run `python scripts/refresh_dashboard_data.py --force-l1`.
- **No recent TY bars**: run `python scripts/backfill_ty_from_databento.py --dates <YYYY-MM-DD>`.
- **Slow first load**: the cold refresh path reads remote PG and Databento; normal dashboard requests should read L1 files and server TTL caches.



