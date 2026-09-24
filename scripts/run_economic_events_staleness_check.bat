@echo off
setlocal EnableExtensions

REM Daily economic_events.db staleness checker wrapper.
REM Uses a local log so it can still record failures when the repo share is unavailable.

set "SCRIPT_DIR=%~dp0"
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "LOCAL_LOG_FILE=%LOCAL_LOG_DIR%\economic_events_staleness.log"

if not exist "%LOCAL_LOG_DIR%" mkdir "%LOCAL_LOG_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to create local log directory: %LOCAL_LOG_DIR%
    exit /b 1
)

echo ============================================================>> "%LOCAL_LOG_FILE%"
echo [%DATE% %TIME%] Starting EconomicEventsDbStalenessCheck>> "%LOCAL_LOG_FILE%"

py -3 "%SCRIPT_DIR%check_economic_events_db_staleness.py" >> "%LOCAL_LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo [%DATE% %TIME%] EconomicEventsDbStalenessCheck completed successfully>> "%LOCAL_LOG_FILE%"
) else (
    echo [%DATE% %TIME%] EconomicEventsDbStalenessCheck failed with exit code %EXIT_CODE%>> "%LOCAL_LOG_FILE%"
)

exit /b %EXIT_CODE%
