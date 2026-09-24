@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "LOG_FILE=%LOCAL_LOG_DIR%\macro_path_health_monitor.log"

if not exist "%LOCAL_LOG_DIR%" mkdir "%LOCAL_LOG_DIR%" >nul 2>&1

echo ============================================================>> "%LOG_FILE%"
echo [%DATE% %TIME%] Starting Macro Path health monitor>> "%LOG_FILE%"

py -3 "%REPO_ROOT%\scripts\check_macro_path_health.py" --loop --interval 60 --url http://127.0.0.1:5088/api/health >> "%LOG_FILE%" 2>&1
exit /b !ERRORLEVEL!
