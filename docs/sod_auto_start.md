# SOD Auto-Start Suite

`Y:\Aastha\SOD\run_all_sod.bat` is the one-click launcher for the SOD dashboards and bots:

- Aggression dashboard v1 on `http://127.0.0.1:5050`
- Aggression dashboard v2.2 on `http://127.0.0.1:5058` (Tailscale `https://aasthajain.tail174727.ts.net:8444/`)
- Aggression dashboard v3 on `http://127.0.0.1:5053`
- `telegram_bot.py`
- `bbg_news_stream_bot.py --mode event --lookback-minutes 1`
- `x_macro_stream_bot.py`
- `polymarket_probability_tracker.py`

The launcher is additive and idempotent. It checks ports and Python command lines before starting anything, skips services that are already running, and does not kill, restart, or replace existing processes.

## V1 crash keep-alive

V1 on port `5050` is allowed to come back after a crash (approved 2026-09-07). Two layers, both additive (they start V1 only when it is down; they never kill V2 / V2.2 / V2.6 / V2.7 / V3):

- `Y:\Aastha\SOD\run_aggression_monitor.bat` loops after the Python process exits. `/watchdog` skips opening a browser.
- Scheduled task `AggressionMonitorV1Watchdog` runs `watch_aggression_monitor_v1.ps1` every minute with a hidden PowerShell window (`-WindowStyle Hidden`). If `5050` is down and no V1 launcher/app process is present, it starts the bat in `/watchdog` mode (also hidden). Log: `%LOCALAPPDATA%\aggression_v1_watchdog.log`.

To stop V1 and keep it stopped, disable or delete `AggressionMonitorV1Watchdog` first. Otherwise it will start V1 again within a minute.

## External Dependencies

AccessAPI at `100.79.30.21:8001` and PostgreSQL at `100.82.143.79:5432` are externally managed. The launcher runs warn-only connectivity checks for both and continues even if either check fails.

Classic Outlook is local and is started automatically for Bloomberg news ingestion. The launcher resolves `OUTLOOK.EXE` from the Windows `App Paths\OUTLOOK.EXE` registration, falls back to `C:\Program Files\Microsoft Office\Root\Office16\OUTLOOK.EXE`, and starts it with `/recycle`. It also sets `BBG_OUTLOOK_START_IF_CLOSED=true` so the Bloomberg news collector can self-start Classic Outlook through COM if needed.

## Optional Task Scheduler Setup

Mapped drive `Y:` is safest when the Windows user is logged in, so use an at-logon interactive task rather than a logged-off background task.

```powershell
schtasks /Create /TN "SODAutoStart" /SC ONLOGON /TR "\"Y:\Aastha\SOD\run_all_sod.bat\"" /RL LIMITED /F
```

To run it immediately after registration:

```powershell
schtasks /Run /TN "SODAutoStart"
```

To remove it:

```powershell
schtasks /Delete /TN "SODAutoStart" /F
```
