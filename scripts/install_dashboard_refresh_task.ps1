param(
    [string]$NightlyTime = "02:30",
    [string]$PostReleaseTime = "09:15",
    [switch]$ForceL1,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$batPath = Join-Path $repoRoot "scripts\run_dashboard_refresh.bat"

if (-not (Test-Path $batPath)) {
    Write-Error "Missing run script: $batPath"
}

function Convert-ToTaskPath {
    param([string]$Path)
    if ($Path -match "^[A-Za-z]:\\") {
        try {
            $drive = $Path.Substring(0, 2)
            $rest = $Path.Substring(2)
            $driveInfo = Get-PSDrive -Name $drive.Substring(0, 1) -ErrorAction Stop
            if ($driveInfo.DisplayRoot) {
                return ($driveInfo.DisplayRoot.TrimEnd("\") + $rest)
            }
        } catch {
            return $Path
        }
    }
    return $Path
}

function Reset-Task {
    param(
        [string]$TaskName,
        [string]$Schedule,
        [string]$StartTime,
        [string]$Args,
        [string]$Days = "",
        [switch]$DryRun
    )

    $taskBat = Convert-ToTaskPath $batPath
    $taskCmd = "cmd /c `"$taskBat`" $Args"
    if ($DryRun) {
        Write-Host "[DryRun] Would create task '$TaskName'."
        Write-Host "[DryRun] Schedule: $Schedule $StartTime $Days"
        Write-Host "[DryRun] Command: $taskCmd"
        return
    }

    cmd.exe /c "schtasks /Query /TN `"$TaskName`" >NUL 2>NUL"
    if ($LASTEXITCODE -eq 0) {
        schtasks.exe /Delete /TN $TaskName /F | Out-Null
    }

    if ($Days) {
        schtasks.exe /Create /SC $Schedule /D $Days /ST $StartTime /TN $TaskName /TR $taskCmd | Out-Null
    } else {
        schtasks.exe /Create /SC $Schedule /ST $StartTime /TN $TaskName /TR $taskCmd | Out-Null
    }
    Write-Host "Scheduled task '$TaskName' created."
    Write-Host "Command: $taskCmd"
}

$forceArg = ""
if ($ForceL1) {
    $forceArg = "--force-l1"
}

# Nightly refresh: full cycle, missing-only L1 by default.
Reset-Task `
    -TaskName "UnifiedDashboardRefreshNightly" `
    -Schedule "DAILY" `
    -StartTime $NightlyTime `
    -Args $forceArg `
    -DryRun:$DryRun

# Post-release weekday refresh: rerun after common 8:30am ET macro release time.
Reset-Task `
    -TaskName "UnifiedDashboardRefreshPostRelease" `
    -Schedule "WEEKLY" `
    -StartTime $PostReleaseTime `
    -Args $forceArg `
    -Days "MON,TUE,WED,THU,FRI" `
    -DryRun:$DryRun

Write-Host "Dashboard refresh tasks installed."
