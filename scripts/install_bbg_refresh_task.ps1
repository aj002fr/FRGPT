$ErrorActionPreference = "Stop"

# Run this installer on the Bloomberg machine.
# Preferred local layout:
#   C:\bbg_refresh\
#   |-- refresh_bbg_excel.py
#   |-- refresh_bbg_excel.bat
#   \-- install_bbg_refresh_task.ps1
# Local disk is preferred over a UNC launch path so a transient share blip
# cannot stop the refresh wrapper itself from starting.

function Resolve-UncPath {
    param([string]$PathValue)
    $item = Get-Item -LiteralPath $PathValue
    $full = $item.FullName
    if ($full -match "^[A-Za-z]:\\") {
        $driveName = $full.Substring(0, 1)
        $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
        if ($drive -and $drive.DisplayRoot) {
            $relative = $full.Substring(2)
            return "$($drive.DisplayRoot)$relative"
        }
    }
    return $full
}

function Get-SchtasksPath {
    $schtasksCmd = Get-Command schtasks.exe -ErrorAction SilentlyContinue
    if ($schtasksCmd) {
        return $schtasksCmd.Source
    }

    $systemRoot = $env:SystemRoot
    if (-not $systemRoot) {
        $systemRoot = $env:WINDIR
    }
    $candidates = @()
    if ($systemRoot) {
        $candidates += (Join-Path $systemRoot "System32\schtasks.exe")
        $candidates += (Join-Path $systemRoot "Sysnative\schtasks.exe")
    }

    return $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Get-NextThreeHourStartBoundary {
    param([int]$SlotMinute)

    $now = Get-Date
    $candidate = $now.Date.AddHours([int]([math]::Ceiling($now.Hour / 3.0)) * 3).AddMinutes($SlotMinute)
    if ($candidate -le $now) {
        $candidate = $candidate.AddHours(3)
    }
    return $candidate.ToString("yyyy-MM-ddTHH:mm:ss")
}

function Remove-TaskIfExists {
    param(
        [string]$SchtasksPath,
        [string]$TaskName
    )

    $prevErrorAction = $ErrorActionPreference
    $prevNativePreference = $PSNativeCommandUseErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $PSNativeCommandUseErrorActionPreference = $false
    & $SchtasksPath /Query /TN $TaskName *> $null
    $ErrorActionPreference = $prevErrorAction
    $PSNativeCommandUseErrorActionPreference = $prevNativePreference
    if ($LASTEXITCODE -eq 0) {
        & $SchtasksPath /Delete /TN $TaskName /F | Out-Null
    }
}

function New-TaskXml {
    param(
        [string]$TaskName,
        [string]$TaskCommand,
        [string]$CurrentUser,
        [string]$StartBoundary
    )

    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$CurrentUser</Author>
    <URI>\$TaskName</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$CurrentUser</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$StartBoundary</StartBoundary>
      <Repetition>
        <Interval>PT3H</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>cmd</Command>
      <Arguments>/c "$TaskCommand"</Arguments>
    </Exec>
  </Actions>
</Task>
"@
}

$taskName = "BBGRefreshExcel"
$cleanupTaskNames = @("BBGRefreshExcel", "BBGRefreshExcelBaseline", "BBGRefreshExcelMorningWindow")
$scriptRoot = Resolve-Path $PSScriptRoot
$batPath = Join-Path $scriptRoot "refresh_bbg_excel.bat"
$batPathForTask = Resolve-UncPath -PathValue $batPath
$schtasks = Get-SchtasksPath

if (-not $schtasks) {
    Write-Error "Unable to locate schtasks.exe (checked PATH, System32, Sysnative)."
}

if (-not (Test-Path $batPath)) {
    Write-Error "Missing run script: $batPath"
}

$currentUser = if ($env:USERDOMAIN) { "$($env:USERDOMAIN)\$($env:USERNAME)" } else { $env:USERNAME }
$taskCommand = $batPathForTask
$startBoundary = Get-NextThreeHourStartBoundary -SlotMinute 0
$xml = New-TaskXml -TaskName $taskName -TaskCommand $taskCommand -CurrentUser $currentUser -StartBoundary $startBoundary
$xmlPath = Join-Path $env:TEMP "${taskName}_task.xml"
Set-Content -Path $xmlPath -Value $xml -Encoding Unicode

try {
    foreach ($cleanupTaskName in $cleanupTaskNames) {
        Remove-TaskIfExists -SchtasksPath $schtasks -TaskName $cleanupTaskName
    }

    & $schtasks /Create /TN $taskName /XML $xmlPath /F /IT | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks.exe failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item $xmlPath -ErrorAction SilentlyContinue
}

Write-Host "Scheduled task '$taskName' created."
Write-Host "Command: cmd /c `"$taskCommand`""
Write-Host "Schedule: every 3 hours at HH:00, 24x7"
Write-Host "First run: $startBoundary"
Write-Host "Settings: interactive Excel attach (/IT), StartWhenAvailable=true, battery restrictions disabled, ExecutionTimeLimit=PT15M"
