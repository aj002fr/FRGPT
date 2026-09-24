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

function Get-NextDaemonStartBoundary {
    $now = Get-Date
    $candidate = $now.Date.AddHours(6)
    while ($candidate -le $now -or $candidate.DayOfWeek -in @("Saturday", "Sunday")) {
        $candidate = $candidate.AddDays(1).Date.AddHours(6)
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
        [string]$LogonType,
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
      <LogonType>$LogonType</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT18H</ExecutionTimeLimit>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$StartBoundary</StartBoundary>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
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

$taskName = "AuctionDataPuller"
$cleanupTaskNames = @("AuctionDataPuller")
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$batPath = Join-Path $repoRoot "scripts\run_auction_data_puller.bat"
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
$startBoundary = Get-NextDaemonStartBoundary
$xml = New-TaskXml `
    -TaskName $taskName `
    -TaskCommand $taskCommand `
    -CurrentUser $currentUser `
    -LogonType $logonType `
    -StartBoundary $startBoundary
$xmlPath = Join-Path $env:TEMP "${taskName}_task.xml"
Set-Content -Path $xmlPath -Value $xml -Encoding Unicode

try {
    foreach ($cleanupTaskName in $cleanupTaskNames) {
        Remove-TaskIfExists -SchtasksPath $schtasks -TaskName $cleanupTaskName
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
Write-Host "Schedule: weekdays at 6:00 AM local time; daemon sleeps between auction windows"
Write-Host "First run: $startBoundary"
Write-Host "Settings: StartWhenAvailable=true, battery restrictions disabled, ExecutionTimeLimit=PT18H"
if ([string]::IsNullOrWhiteSpace($TaskPassword)) {
    Write-Warning "Created in interactive mode because no -TaskPassword was provided. Re-run with -TaskPassword to switch to 'Run whether user is logged on or not'."
} else {
    Write-Host "Logon mode: password-backed ('Run whether user is logged on or not')."
}

