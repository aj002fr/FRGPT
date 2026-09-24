$ErrorActionPreference = "Stop"

$taskName = "L1AutoBuilder"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$batPath = Join-Path $repoRoot "scripts\run_l1_auto_builder.bat"

if (-not (Test-Path $batPath)) {
    Write-Error "Missing run script: $batPath"
}

# Remove existing task if present (ignore if missing)
schtasks.exe /Query /TN $taskName > $null 2>&1
if ($LASTEXITCODE -eq 0) {
    schtasks.exe /Delete /TN $taskName /F | Out-Null
}

# Create daily task at 02:00 local time
$taskCmd = "cmd /c `"$batPath`""
schtasks.exe /Create /SC DAILY /ST 02:00 /TN $taskName /TR $taskCmd | Out-Null

Write-Host "Scheduled task '$taskName' created."
Write-Host "Command: $taskCmd"

