@echo off
setlocal

REM Wrapper for Task Scheduler to refresh unified dashboard backing data.
REM Writes logs to both the repo workspace and a local fallback directory.

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "REPO_LOG_DIR=%REPO_ROOT%\workspace\logs"

if not exist "%LOCAL_LOG_DIR%" mkdir "%LOCAL_LOG_DIR%"
if not exist "%REPO_LOG_DIR%" mkdir "%REPO_LOG_DIR%"

set "LOG_FILE=%LOCAL_LOG_DIR%\dashboard_refresh.log"
set "REPO_LOG_FILE=%REPO_LOG_DIR%\dashboard_refresh.log"

echo [%DATE% %TIME%] Starting dashboard refresh >> "%LOG_FILE%"
echo [%DATE% %TIME%] Starting dashboard refresh >> "%REPO_LOG_FILE%"

py -3 "%SCRIPT_DIR%refresh_dashboard_data.py" %* >> "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"

type "%LOG_FILE%" > "%REPO_LOG_FILE%" 2>nul

echo [%DATE% %TIME%] Dashboard refresh exit code %RC% >> "%LOG_FILE%"
echo [%DATE% %TIME%] Dashboard refresh exit code %RC% >> "%REPO_LOG_FILE%"

exit /b %RC%
