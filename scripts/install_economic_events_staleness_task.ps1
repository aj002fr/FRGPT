param(
    [string]$TaskPassword
)

$ErrorActionPreference = "Stop"

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

function New-TaskXml {
    param(
        [string]$TaskName,
        [string]$TaskCommand,
        [string]$CurrentUser,
        [string]$LogonType
    )

    $startBoundary = (Get-Date).Date.AddHours(9).ToString("s")
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
      <LogonType>$LogonType</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
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

$taskName = "EconomicEventsDbStalenessCheck"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$batPath = Join-Path $repoRoot "scripts\run_economic_events_staleness_check.bat"
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
$logonType = if ([string]::IsNullOrWhiteSpace($TaskPassword)) { "InteractiveToken" } else { "Password" }
$xml = New-TaskXml -TaskName $taskName -TaskCommand $taskCommand -CurrentUser $currentUser -LogonType $logonType
$xmlPath = Join-Path $env:TEMP "${taskName}_task.xml"
Set-Content -Path $xmlPath -Value $xml -Encoding Unicode

try {
    $prevErrorAction = $ErrorActionPreference
    $prevNativePreference = $PSNativeCommandUseErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $PSNativeCommandUseErrorActionPreference = $false
    & $schtasks /Query /TN $taskName *> $null
    $ErrorActionPreference = $prevErrorAction
    $PSNativeCommandUseErrorActionPreference = $prevNativePreference
    if ($LASTEXITCODE -eq 0) {
        & $schtasks /Delete /TN $taskName /F | Out-Null
    }

    $createArgs = @("/Create", "/TN", $taskName, "/XML", $xmlPath, "/F")
    if (-not [string]::IsNullOrWhiteSpace($TaskPassword)) {
        $createArgs += @("/RU", $currentUser, "/RP", $TaskPassword)
    }
    & $schtasks @createArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks.exe failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item $xmlPath -ErrorAction SilentlyContinue
}

Write-Host "Scheduled task '$taskName' created."
Write-Host "Command: cmd /c `"$taskCommand`""
Write-Host "Schedule: daily 09:00 local with StartWhenAvailable=true and battery restrictions disabled"
if ([string]::IsNullOrWhiteSpace($TaskPassword)) {
    Write-Warning "Created in interactive mode because no -TaskPassword was provided. Re-run with -TaskPassword to switch to 'Run whether user is logged on or not'."
} else {
    Write-Host "Logon mode: password-backed ('Run whether user is logged on or not')."
}
