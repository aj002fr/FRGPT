# Cutover runbook: Y: runtime to the local C: runtime (Milestone 8)

Moves the canonical flow writer and the five served dashboards (V3, V2.2, V2.7, flow history, supply weeks) from the `Y:` share to `C:\dev\market_data_puller`, inside one 17:00–18:00 ET CME halt. V1 (`5050`) stops for good; the canonical writer replaces it as the only flow writer.

## Go / no-go (16:45 ET, all required)

| Gate | Check |
|---|---|
| Writer shadow | `shadow_compare` clean over 4+ live hours that include 08:20–10:30 ET and a 17:00–18:00 halt/reopen, all produced by the code being cut over (restart times in `C:\dev\mdp_shadow\restarts.txt`) |
| Rehearsal | report written, every item passed, measured timings below fit the window |
| V3 payload | Milestone 5 passed on the alternate port |
| Approval | the user approved this window; one named go/no-go owner; abort deadline 17:40 ET |
| Open decisions | banner alerts and the V2 signal log (below) decided |

Open decisions to settle before the window:

- `banner_alerts.sqlite` has two writers, V1 banner changes and V2.7 ENTRY/EXIT stamped with the latest V1 banner. When V1 stops, banner rows stop and V2.7's stamp goes stale.
- V2.7's same-sign re-entry rule reads V2's (`5052`) signal log. V2 is outside this plan and has been down since 2026-09-23 19:05.
- After cutover the apps listen on `6053/6058/6063/6064/6065`. Tailscale URLs do not change; local `127.0.0.1:50xx` links stop working.

## Before 17:00

```powershell
$r = 'C:\dev\market_data_puller'; $bk = "C:\dev\mdp_backups\cutover_$(Get-Date -Format yyyyMMdd)"
git -C $r status --short            # must be empty
git -C $r tag "cutover-$(Get-Date -Format yyyyMMdd)"
New-Item -ItemType Directory -Force $bk | Out-Null
& 'C:\Program Files\Tailscale\tailscale.exe' serve status --json > "$bk\tailscale_serve.json"
'AggressionMonitorV1Watchdog','MDP R0 v27 5063','MDP R0 supply 5065','MDP shadow flow writer' |
  ForEach-Object { Export-ScheduledTask -TaskName $_ > "$bk\task_$($_ -replace ' ','_').xml" }
git -C $r worktree add C:\dev\mdp_rollback HEAD   # last known-good code for per-app rollback
New-Item -ItemType Junction -Path C:\dev\mdp_rollback\workspace -Target "$r\workspace" | Out-Null
Copy-Item "$r\config\keys.env" C:\dev\mdp_rollback\config\   # git carries neither the data nor the keys
```

Refresh the read-only inputs listed in `docs/local_runtime_paths.md` with `robocopy /E /IPG:2` (never `/MIR` toward Y:).

## During the halt (17:00–17:40)

1. Stop V1 and its keep-alive. The watchdog restarts V1 when `5050` is down, so disable it first.

   ```powershell
   Disable-ScheduledTask -TaskName AggressionMonitorV1Watchdog
   function Stop-Tree($like) {
     Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'cmd.exe','conhost.exe' -and $_.CommandLine -like $like } |
       ForEach-Object { taskkill /PID $_.ProcessId /T /F }
   }
   Stop-Tree '*run_aggression_monitor.bat*'
   ```

2. Stop the Y: apps, the shadow writer and the rehearsal supervisor.

   ```powershell
   Stop-Tree '*run_aggression_monitor_v3.bat*'; Stop-Tree '*run_aggression_monitor_v22.bat*'
   Stop-Tree '*run_v1_flow_history_dash.bat*'
   'MDP R0 v27 5063','MDP R0 supply 5065','MDP shadow flow writer' | ForEach-Object { Disable-ScheduledTask -TaskName $_ }
   Stop-Tree '*aggression_dashboard_v27.app*5063*'; Stop-Tree '*supply_weeks_live.app*5065*'; Stop-Tree '*mdp_shadow*'
   Stop-Process -Id (Get-Content C:\dev\mdp_rehearsal\supervisor\pid.txt) -ErrorAction SilentlyContinue
   5050,5053,5058,5063,5064,5065 | ForEach-Object { if (Get-NetTCPConnection -LocalPort $_ -State Listen -EA SilentlyContinue) { "STILL UP: $_" } }
   ```

   Every port must be down before step 3.

3. Migrate the flow database. Copy the files first so all SQLite locking stays local, then compact into place.

   ```powershell
   $y = 'Y:\Aastha\market_data_puller\workspace\analysis'; $c = "$r\workspace\analysis"; $stage = "$bk\stage"
   robocopy "$y\aggression_v1_live" $stage flow_history.sqlite flow_history.sqlite-wal /COPY:DAT /R:2 /W:5
   ```

   ```python
   import sqlite3
   stage = r"C:\dev\mdp_backups\cutover_<date>\stage\flow_history.sqlite"
   dst = r"C:\dev\market_data_puller\workspace\analysis\aggression_v1_live\flow_history.sqlite"
   src = sqlite3.connect(stage)
   n_src = src.execute("select count(*) from v1_aggress_flow_1m").fetchone()[0]
   src.execute(f"VACUUM INTO '{dst}'"); src.close()
   out = sqlite3.connect(dst)
   assert out.execute("PRAGMA quick_check").fetchone()[0] == "ok"
   assert out.execute("select count(*) from v1_aggress_flow_1m").fetchone()[0] == n_src
   out.execute("PRAGMA journal_mode=WAL"); out.close()
   ```

   Rehearsed on the 1.03 GB snapshot: `VACUUM INTO` 8.5 s, `quick_check` 3.1 s locally. Add the SMB copy time measured at rehearsal.

4. Copy the live state while its owners are stopped.

   ```powershell
   'aggression_v3_live','aggression_v22_live','aggression_v27_live','aggression_v1_v27_banner_alerts' |
     ForEach-Object { robocopy "$y\$_" "$c\$_" /E /COPY:DAT /R:2 /W:5 }
   ```

5. Start the supervisor (production profile). It starts the writer first; the writer takes the lease and records `canonical_start_utc` at the next minute.

   ```powershell
   $py = "$r\.venv\Scripts\python.exe"
   $act = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $r `
     -Argument "--headless `"$py`" -u -m scripts.dashboard_runtime.supervisor --profile production --python `"$py`""
   $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
     -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
   Register-ScheduledTask -TaskName 'MDP dashboard supervisor' -Action $act -Settings $set `
     -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)
   Start-ScheduledTask -TaskName 'MDP dashboard supervisor'
   Get-Content "$c\supervisor\supervisor_status.json"   # every app running; v3 may show "starting" for up to 300 s
   ```

6. Repoint Tailscale, one mapping at a time, checking each URL after its command.

   ```powershell
   $ts = 'C:\Program Files\Tailscale\tailscale.exe'
   & $ts serve --bg --yes --https=443  http://127.0.0.1:6053
   & $ts serve --bg --yes --https=8444 http://127.0.0.1:6058
   & $ts serve --bg --yes --https=8445 http://127.0.0.1:6063
   & $ts serve --bg --yes --https=8446 http://127.0.0.1:6064
   & $ts serve --bg --yes --https=8447 http://127.0.0.1:6065
   & $ts serve status                  # :8443 must still point at 8055
   ```

7. Verify through the tailnet URLs: `https://aasthajain.tail174727.ts.net/api/live_status`, `:8444/api/signal_status`, `:8445/api/signal_status`, `:8446/api/status`, `:8447/api/live`. Each returns 200; V3 shows `market_open` false, 5064/5065 show `feed.phase` `HALT`.

If any step fails or 17:40 passes, use rollback A.

## At the reopen (18:00–18:15)

- Writer `status.json`: `connected` true, lease held, the 18:00 minute sealed by about 18:01:10.
- `:8446/api/status` and `:8447/api/live`: latest bar within 2 minutes, `source` `accessapi`, no PROVISIONAL badge.
- V3: `python ops\release0\stream_probe.py --url http://127.0.0.1:6053/api/live/stream --seconds 300`, p95 delta lag under 2 s.
- V2.2/V2.7: `signal_status` evaluated within the last 20 s.
- `supervisor_status.json`: no restarts; `alerts.jsonl` empty since 17:00.

## Rollback

A. Before 18:00, or anything fails during the halt. The Y: flow database was only read, so there is nothing to restore on Y:.

```powershell
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
& $ts serve --bg --yes --https=443  http://127.0.0.1:5053
& $ts serve --bg --yes --https=8444 http://127.0.0.1:5058
& $ts serve --bg --yes --https=8445 http://127.0.0.1:5063
& $ts serve --bg --yes --https=8446 http://127.0.0.1:5064
& $ts serve --bg --yes --https=8447 http://127.0.0.1:5065
Stop-ScheduledTask -TaskName 'MDP dashboard supervisor'; Disable-ScheduledTask -TaskName 'MDP dashboard supervisor'
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*dashboard_runtime.supervisor*production*' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F }
Enable-ScheduledTask -TaskName AggressionMonitorV1Watchdog   # restarts V1 within its poll interval
'MDP R0 v27 5063','MDP R0 supply 5065' | ForEach-Object { Enable-ScheduledTask -TaskName $_; Start-ScheduledTask -TaskName $_ }
Start-Process cmd.exe -ArgumentList '/K','Y:\Aastha\SOD\run_aggression_monitor_v3.bat'  -WorkingDirectory 'Y:\Aastha\market_data_puller'
Start-Process cmd.exe -ArgumentList '/K','Y:\Aastha\SOD\run_aggression_monitor_v22.bat' -WorkingDirectory 'Y:\Aastha\market_data_puller'
Start-Process cmd.exe -ArgumentList '/c','Y:\Aastha\SOD\run_v1_flow_history_dash.bat'  -WorkingDirectory 'Y:\Aastha\market_data_puller'
```

B. After 18:00, one app fails. Disable it in the supervisor, run the last known-good code from the rollback worktree on the same local data, and repoint only that mapping.

```powershell
New-Item "$r\workspace\analysis\supervisor\flow.disabled" -ItemType File   # stops "flow" and keeps it stopped
# start C:\dev\mdp_rollback's copy on the same port with the production env (FLOW_HISTORY_DB_PATH, MDP_PG_HOST, ...)
Remove-Item "$r\workspace\analysis\supervisor\flow.disabled"               # hand the app back to the supervisor
```

C. After 18:00, the writer misbehaves. Stop it (the marker makes the supervisor send `stop.request` and keep it down), then run V1 from the C: checkout as the fallback writer on the same local database. V1 does not take the writer lease, so the writer must stay disabled while V1 runs.

```powershell
New-Item "$r\workspace\analysis\supervisor\writer.disabled" -ItemType File
$env:FLOW_HISTORY_DB_PATH = "$r\workspace\analysis\aggression_v1_live\flow_history.sqlite"
Start-Process $py -ArgumentList '-u','-m','scripts.aggression_dashboard.app' -WorkingDirectory $r
```

## After the window

- Soak one week: daily `shadow_compare` of the previous session against AccessAPI historical; `metrics.jsonl` CPU and memory trend.
- Then delete the R0 stopgap tasks and the shadow task, and archive the Y: launchers (Milestone 9).
