<#
.SYNOPSIS
  Release 0 deploy / restore for a 17:00-18:00 ET window, one app at a time.

.DESCRIPTION
  Items:
    v3      5053         scripts\aggression_dashboard_v3\{print_reconcile,live_publisher,app}.py
    v1      5050         scripts\aggression_dashboard\app.py (V1's launcher loop restarts it)
    events  5064 + 5065  flow-history events.py + app.py, supply-weeks app.py, keys.env DSN line,
                         the 5064 calendar-source note in .cursorrules

  status  : ports, and for each file whether Y: holds the base, the release, or something else.
  deploy  : refuses unless every Y: file still equals the base this release was merged against,
            backs each original up once (never overwritten), writes the release version with the
            Y: file's own line endings, restarts the item's apps headless, then verifies.
  restore : writes the backed-up original bytes back and restarts.
  verify  : health checks only.

  Y: is written only by deploy and restore. Backups and the log live under -BackupRoot on C:.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('v3', 'v1', 'events')][string]$Item,
    [ValidateSet('status', 'deploy', 'restore', 'verify')][string]$Action = 'status',
    [string]$Repo = 'C:\dev\market_data_puller',
    [string]$Target = 'Y:\Aastha\market_data_puller',
    [string]$BackupRoot = 'C:\dev\mdp_backups\release0_20260924',
    [string]$BaseRef = 'yshare-sync',
    [string]$ReleaseRef = 'ops/local-runtime',
    [switch]$SkipRestart
)
$ErrorActionPreference = 'Stop'

$VenvPython = 'Y:\Aastha\market_data_puller\.venv\Scripts\python.exe'
$BasePython = 'C:\Users\aasth\AppData\Local\Programs\Python\Python313\python.exe'
$LogDir = Join-Path $env:LOCALAPPDATA 'market_data_puller\logs'
$Utf8 = New-Object System.Text.UTF8Encoding($false)

$Items = @{
    v3     = @{ Files = @('scripts/aggression_dashboard_v3/print_reconcile.py',
                          'scripts/aggression_dashboard_v3/live_publisher.py',
                          'scripts/aggression_dashboard_v3/app.py') }
    v1     = @{ Files = @('scripts/aggression_dashboard/app.py') }
    events = @{ Files = @('scripts/aggression_flow_history_dash/events.py',
                          'scripts/aggression_flow_history_dash/app.py',
                          'scripts/supply_weeks_live/app.py') }
}
$Apps = @{
    5053 = @{ Task = 'MDP R0 v3 5053'; Python = $VenvPython; Module = 'scripts.aggression_dashboard_v3.app'; Log = 'v3_5053.log'; Bat = 'run_aggression_monitor_v3.bat' }
    5064 = @{ Task = 'MDP R0 flow 5064'; Python = $VenvPython; Module = 'scripts.aggression_flow_history_dash.app'; Log = 'flow_5064.log'; Bat = 'run_v1_flow_history_dash.bat' }
    5065 = @{ Task = 'MDP R0 supply 5065'; Python = $BasePython; Module = 'scripts.supply_weeks_live.app'; Log = 'supply_5065.log'; Bat = 'supply_weeks_live' }
}
$CursorRulesOld = 'Calendar source is `Y:\Yaman\Fundamentals\Economic Events\Data\events.sqlite` ('
$CursorRulesNew = 'Calendar source is Yaman''s Postgres `economic_events` (read-only role `economic_events_reader`, DSN `ECON_EVENTS_PG_DSN` in `config/keys.env`, refreshed in memory every 60 s with a last-good file under `%LOCALAPPDATA%\market_data_puller\cache`; ' 

$RollbackHint = 'rollback: `powershell -File C:\dev\market_data_puller\ops\release0\deploy_release0.ps1 -Item {ITEM} -Action restore`; source of truth `C:\dev\market_data_puller` branch `ops/local-runtime`.'
$Notes = @{
    v3     = '- Release 0 R0.2 deployed {TIME} ET (V3 5053): vectorized exact print matcher (`_matched_incoming`; the 11:37 bisect loop is kept as `_matched_incoming_reference` and the bad-input fallback), `LiveOverlayCache` re-matches only the lookback edges, rebuilds coalesce to >=60 s (5 s while warming), the picture is prepared off-lock and JSON-encoded once, trade broadcasts are queued under the lock, catch-up runs in 2000-print batches. Golden/replay tests on the 9/23 US-open fixture: `tests/test_v3_reconcile_golden.py`. 5053 now runs headless from task `MDP R0 v3 5053` (log `%LOCALAPPDATA%\market_data_puller\logs\v3_5053.log`); ' + $RollbackHint
    v1     = '- Release 0 R0.3 deployed {TIME} ET (V1 5050): ten-day Databento bars are no longer persisted to `flow_history.sqlite` (refresher and startup seed); live AccessAPI writes are unchanged, and minutes when V1 was down stay as holes. Test: `tests/test_v1_no_tenday_persist.py`; ' + $RollbackHint
    events = '- Release 0 R0.4 deployed {TIME} ET (5064/5065): economic events come from Yaman''s Postgres `economic_events` (read-only role; DSN `ECON_EVENTS_PG_DSN` in `config/keys.env`) through an in-memory cache refreshed every 60 s with a last-good file; 5064 `/api/status` and 5065 `/api/bootstrap` + `/api/live` carry an `events` block. Postgres is a one-time copy of the 9/23 12:26 SQLite build until Yaman''s pipeline writes to it. 5064 now runs headless from task `MDP R0 flow 5064`; ' + $RollbackHint
}
$NoteMarker = @{ v3 = 'Release 0 R0.2 deployed'; v1 = 'Release 0 R0.3 deployed'; events = 'Release 0 R0.4 deployed' }
$NoteTargets = @(
    @{ Rel = 'memory-bank/progress.md'; Heading = '## 2026-09-24' },
    @{ Rel = 'memory-bank/activeContext.md'; Heading = '## Recent Changes (2026-09-24)' }
)

function Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} [{1}/{2}] {3}' -f (Get-Date), $Item, $Action, $Message
    Write-Host $line
    New-Item -ItemType Directory -Force $BackupRoot | Out-Null
    Add-Content -Path (Join-Path $BackupRoot 'deploy_log.txt') -Value $line -Encoding utf8
}

function Get-GitBytes([string]$Ref, [string]$Path) {
    $tmp = [IO.Path]::GetTempFileName()
    try {
        cmd.exe /c "git -C `"$Repo`" show `"${Ref}:$Path`" > `"$tmp`"" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "git show ${Ref}:$Path failed" }
        return [IO.File]::ReadAllBytes($tmp)
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

function Norm([byte[]]$Bytes) {
    $text = $Utf8.GetString($Bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    return $text.Replace("`r`n", "`n")
}

function Write-Release([string]$Path, [string]$NormText, [byte[]]$Original) {
    $text = if ($Utf8.GetString($Original).Contains("`r`n")) { $NormText.Replace("`n", "`r`n") } else { $NormText }
    [IO.File]::WriteAllText($Path, $text, $Utf8)
}

function File-State([string]$Rel) {
    $yPath = Join-Path $Target ($Rel -replace '/', '\')
    $y = Norm ([IO.File]::ReadAllBytes($yPath))
    $base = Norm (Get-GitBytes $BaseRef $Rel)
    $release = Norm (Get-GitBytes $ReleaseRef $Rel)
    if ($y -eq $release) { return 'release' }
    if ($y -eq $base) { return 'base' }
    return 'OTHER'
}

function Backup-Path([string]$Rel) { return Join-Path (Join-Path $BackupRoot $Item) ($Rel -replace '/', '\') }

function Save-Backup([string]$Rel) {
    $src = Join-Path $Target ($Rel -replace '/', '\')
    $dst = Backup-Path $Rel
    if (Test-Path $dst) { Log "backup kept (already present): $dst"; return }
    New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst
    $hash = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
    Add-Content -Path (Join-Path (Join-Path $BackupRoot $Item) 'manifest.txt') -Value "$hash  $Rel" -Encoding utf8
    Log "backed up $Rel ($hash)"
}

function Get-ListenerPid([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return $null
}

function Stop-App([int]$Port, [string]$ParentToken, [switch]$KeepLauncherLoop) {
    $procId = Get-ListenerPid $Port
    if (-not $procId) { Log "port $Port already down"; return }
    $child = Get-CimInstance Win32_Process -Filter "ProcessId=$procId"
    $victims = @($child)
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ParentProcessId)" -ErrorAction SilentlyContinue
    if ($parent -and $parent.Name -eq 'python.exe') {
        $victims += $parent
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($parent.ParentProcessId)" -ErrorAction SilentlyContinue
    }
    if (-not $KeepLauncherLoop -and $parent -and $parent.Name -eq 'cmd.exe' -and $parent.CommandLine -like "*$ParentToken*") {
        $victims += $parent
    }
    foreach ($v in $victims) {
        Log ("stopping pid {0} {1}: {2}" -f $v.ProcessId, $v.Name, ($v.CommandLine -replace '\s+', ' '))
        Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-ListenerPid $Port) -and (Get-Date) -lt $deadline) { Start-Sleep 1 }
    if (Get-ListenerPid $Port) { throw "port $Port still listening after stop" }
}

function Ensure-Task([int]$Port) {
    $app = $Apps[$Port]
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    if (Get-ScheduledTask -TaskName $app.Task -ErrorAction SilentlyContinue) { return }
    $inner = "cd /d $Target && `"$($app.Python)`" -u -m $($app.Module) --host 127.0.0.1 --port $Port >> `"$LogDir\$($app.Log)`" 2>&1"
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless cmd.exe /c `"$inner`""
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $app.Task -Action $action -Settings $settings -Principal $principal `
        -Description 'Release 0 stopgap launcher; replaced by the dashboard supervisor (Milestone 6).' | Out-Null
    Log "registered task $($app.Task)"
}

function Start-App([int]$Port, [int]$WaitSec = 300) {
    $app = $Apps[$Port]
    Ensure-Task $Port
    Stop-ScheduledTask -TaskName $app.Task -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $app.Task
    Log "started task $($app.Task); waiting for port $Port"
    Wait-Port $Port $WaitSec
}

function Wait-Port([int]$Port, [int]$WaitSec) {
    $deadline = (Get-Date).AddSeconds($WaitSec)
    while (-not (Get-ListenerPid $Port)) {
        if ((Get-Date) -gt $deadline) { throw "port $Port not listening after $WaitSec s" }
        Start-Sleep 2
    }
    Log "port $Port listening (pid $(Get-ListenerPid $Port))"
}

function Get-Json([string]$Url, [int]$TimeoutSec = 120) {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.Content | ConvertFrom-Json
}

function Restart-Item {
    switch ($Item) {
        'v3' { Stop-App 5053 $Apps[5053].Bat; Start-App 5053 }
        'v1' { Stop-App 5050 'run_aggression_monitor.bat' -KeepLauncherLoop; Log 'V1 launcher loop restarts V1 in ~15 s'; Wait-Port 5050 240 }
        'events' { Stop-App 5064 $Apps[5064].Bat; Start-App 5064; Stop-App 5065 $Apps[5065].Bat; Start-App 5065 }
    }
}

function Verify-Item {
    switch ($Item) {
        'v3' {
            $s = Get-Json 'http://127.0.0.1:5053/api/live_status' 60
            Log ("v3 live_status: connected={0} source={1} market_open={2}" -f $s.connected, $s.source, $s.market_open)
            $probe = & $BasePython (Join-Path $Repo 'ops\release0\stream_probe.py') --seconds 75 2>&1
            Log "v3 stream probe: $probe"
            if ("$probe" -notmatch '"first_picture_sec": [0-9]') { throw 'v3 stream sent no snapshot within 75 s' }
        }
        'v1' {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5050/' -UseBasicParsing -TimeoutSec 60
            Log "v1 / -> HTTP $($r.StatusCode)"
        }
        'events' {
            $a = Get-Json 'http://127.0.0.1:5064/api/status' 60
            Log ("5064 events: ok={0} rows={1} host={2} error={3} fallback={4}" -f $a.events.ok, $a.events.rows, $a.events.host, $a.events.last_error, $a.events.fallback)
            if (-not $a.events.ok) { throw '5064 events not ok' }
            $b = Get-Json 'http://127.0.0.1:5065/api/live' 300
            Log ("5065 events: ok={0} rows={1} host={2} error={3}" -f $b.events.ok, $b.events.rows, $b.events.host, $b.events.last_error)
            if (-not $b.events.ok) { throw '5065 events not ok' }
        }
    }
}

function Deploy-Extras {
    if ($Item -ne 'events') { return }
    $keys = Join-Path $Target 'config\keys.env'
    $text = [IO.File]::ReadAllText($keys)
    if ($text -notmatch '(?m)^\s*ECON_EVENTS_PG_DSN=') {
        Save-Backup 'config/keys.env'
        $line = (Get-Content (Join-Path $Repo 'config\keys.env') | Where-Object { $_ -match '^\s*ECON_EVENTS_PG_DSN=' } | Select-Object -First 1)
        if (-not $line) { throw 'C: config\keys.env has no ECON_EVENTS_PG_DSN line' }
        $sep = if ($text.EndsWith("`n")) { '' } else { "`n" }
        [IO.File]::AppendAllText($keys, "$sep# Economic events: Yaman's Postgres, SELECT-only role (D:\FRDBs\EconomicEvents\README.md)`n$line`n", $Utf8)
        Log 'appended ECON_EVENTS_PG_DSN to Y: config\keys.env'
    } else { Log 'Y: keys.env already has ECON_EVENTS_PG_DSN' }
    $rules = Join-Path $Target '.cursorrules'
    $rtext = [IO.File]::ReadAllText($rules)
    if ($rtext.Contains($CursorRulesOld)) {
        Save-Backup '.cursorrules'
        [IO.File]::WriteAllText($rules, $rtext.Replace($CursorRulesOld, $CursorRulesNew), $Utf8)
        Log 'updated the 5064 calendar-source note in Y: .cursorrules'
    } else { Log '.cursorrules note already updated or not found' }
}

function Add-MemoryNote {
    $text = $Notes[$Item].Replace('{TIME}', (Get-Date -Format 'HH:mm')).Replace('{ITEM}', $Item)
    foreach ($spec in $NoteTargets) {
        $path = Join-Path $Target ($spec.Rel -replace '/', '\')
        if (-not (Test-Path $path)) { continue }
        $raw = [IO.File]::ReadAllText($path)
        if ($raw.Contains($NoteMarker[$Item])) { Log "memory note already in $($spec.Rel)"; continue }
        $nl = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
        $idx = $raw.IndexOf($spec.Heading + $nl)
        if ($idx -lt 0) { Log "heading '$($spec.Heading)' not found in $($spec.Rel); note skipped"; continue }
        $copy = Join-Path (Join-Path $BackupRoot "$Item-notes") ($spec.Rel -replace '/', '\')
        New-Item -ItemType Directory -Force (Split-Path $copy) | Out-Null
        Copy-Item -LiteralPath $path -Destination $copy -Force
        $at = $idx + $spec.Heading.Length + $nl.Length
        [IO.File]::WriteAllText($path, $raw.Insert($at, "$nl$text$nl"), $Utf8)
        Log "added deploy note to $($spec.Rel)"
    }
}

function Remove-MemoryNote {
    foreach ($spec in $NoteTargets) {
        $path = Join-Path $Target ($spec.Rel -replace '/', '\')
        if (-not (Test-Path $path)) { continue }
        $raw = [IO.File]::ReadAllText($path)
        $nl = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
        $pattern = [regex]::Escape($nl) + [regex]::Escape($Notes[$Item].Split('{')[0]) + '[^\r\n]*' + [regex]::Escape($nl)
        $clean = [regex]::Replace($raw, $pattern, '', 1)
        if ($clean -ne $raw) {
            [IO.File]::WriteAllText($path, $clean, $Utf8)
            Log "removed deploy note from $($spec.Rel)"
        }
    }
}

switch ($Action) {
    'status' {
        foreach ($rel in $Items[$Item].Files) {
            $bk = if (Test-Path (Backup-Path $rel)) { 'backup present' } else { 'no backup' }
            Write-Host ('{0,-8} {1,-55} {2}' -f (File-State $rel), $rel, $bk)
        }
        foreach ($p in 5050, 5053, 5064, 5065) { Write-Host ("port {0}: {1}" -f $p, $(if (Get-ListenerPid $p) { "listening pid $(Get-ListenerPid $p)" } else { 'DOWN' })) }
    }
    'deploy' {
        $states = @{}
        foreach ($rel in $Items[$Item].Files) { $states[$rel] = File-State $rel }
        $other = @($states.Keys | Where-Object { $states[$_] -eq 'OTHER' })
        if ($other.Count) { throw "Y: changed since the merge base; re-sync before deploying: $($other -join ', ')" }
        foreach ($rel in $Items[$Item].Files) {
            if ($states[$rel] -eq 'release') { Log "already deployed: $rel"; continue }
            Save-Backup $rel
            $yPath = Join-Path $Target ($rel -replace '/', '\')
            $original = [IO.File]::ReadAllBytes($yPath)
            Write-Release $yPath (Norm (Get-GitBytes $ReleaseRef $rel)) $original
            if ((File-State $rel) -ne 'release') { throw "copy verification failed: $rel" }
            Log "deployed $rel"
        }
        Deploy-Extras
        if (-not $SkipRestart) { Restart-Item; Verify-Item }
        Add-MemoryNote
        Log 'deploy complete'
    }
    'restore' {
        $manifest = Join-Path (Join-Path $BackupRoot $Item) 'manifest.txt'
        if (-not (Test-Path $manifest)) { throw "no backups for $Item" }
        foreach ($entry in Get-Content $manifest) {
            $hash, $rel = $entry -split '\s+', 2
            $src = Backup-Path $rel
            if ((Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash -ne $hash) { throw "backup hash mismatch: $rel" }
            Copy-Item -LiteralPath $src -Destination (Join-Path $Target ($rel -replace '/', '\')) -Force
            Log "restored $rel"
        }
        Remove-MemoryNote
        if (-not $SkipRestart) { Restart-Item; Verify-Item }
        Log 'restore complete'
    }
    'verify' { Verify-Item }
}
