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
        [string]$LogonType
    )

    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$CurrentUser</Author>
    <URI>\$TaskName</URI>
    <Description>Persistent real-time Telegram listener for Iran/geopolitics headlines.</Description>
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
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$CurrentUser</UserId>
    </LogonTrigger>
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

$taskName = "IranHeadlinesStream"
$cleanupTaskNames = @("IranHeadlinesRefresh", "IranHeadlinesStream")
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$batPath = Join-Path $repoRoot "scripts\run_iran_headlines_stream.bat"
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
$xml = New-TaskXml `
    -TaskName $taskName `
    -TaskCommand $taskCommand `
    -CurrentUser $currentUser `
    -LogonType $logonType
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
Write-Host "Schedule: at user logon, long-running listener"
Write-Host "Settings: StartWhenAvailable=true, RestartOnFailure=1 minute, ExecutionTimeLimit=unlimited"
Write-Host "Removed/replaced old batch task: IranHeadlinesRefresh"
if ([string]::IsNullOrWhiteSpace($TaskPassword)) {
    Write-Warning "Created in interactive mode because no -TaskPassword was provided. Re-run with -TaskPassword to switch to 'Run whether user is logged on or not'."
} else {
    Write-Host "Logon mode: password-backed ('Run whether user is logged on or not')."
}
