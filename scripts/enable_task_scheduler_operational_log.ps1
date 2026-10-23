$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Error "Administrator privileges are required. Re-run this script from an elevated PowerShell session."
}

wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true
Write-Host "Enabled Microsoft-Windows-TaskScheduler/Operational."
