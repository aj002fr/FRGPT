# Run Telegram Bot in Background with Auto-Restart
# Usage: powershell -ExecutionPolicy Bypass -File scripts\run_bot_background.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$LogFile = Join-Path $ProjectRoot "logs\telegram_bot_service.log"

function Write-Log {
    param($Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$timestamp - $Message" | Tee-Object -FilePath $LogFile -Append
}

Write-Log "Starting Telegram Bot Service..."
Write-Log "Project Root: $ProjectRoot"

# Keep restarting if it crashes
while ($true) {
    Write-Log "Starting bot process..."
    
    try {
        $process = Start-Process -FilePath "python" `
            -ArgumentList "scripts\telegram_bot.py" `
            -WorkingDirectory $ProjectRoot `
            -NoNewWindow `
            -PassThru `
            -Wait
        
        $exitCode = $process.ExitCode
        Write-Log "Bot exited with code: $exitCode"
    }
    catch {
        Write-Log "Error: $_"
    }
    
    Write-Log "Restarting in 10 seconds..."
    Start-Sleep -Seconds 10
}





