@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Persistent Iran headlines Telegram listener.
REM The Python process owns single-instance locking, heartbeat markers, and logs.

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "DB_PATH=%REPO_ROOT%\workspace\iran_headlines.db"
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "STATUS_DIR=%LOCALAPPDATA%\market_data_puller\status"
set "WRAPPER_LOG=%LOCAL_LOG_DIR%\iran_headlines_stream_wrapper.log"
set "ERROR_MARKER=%STATUS_DIR%\iran_headlines_stream_wrapper_last_error.txt"
set "SUCCESS_MARKER=%STATUS_DIR%\iran_headlines_stream_wrapper_last_success.txt"

if not exist "%LOCAL_LOG_DIR%" mkdir "%LOCAL_LOG_DIR%" >nul 2>&1
if not exist "%STATUS_DIR%" mkdir "%STATUS_DIR%" >nul 2>&1

echo ============================================================>> "%WRAPPER_LOG%"
echo [%DATE% %TIME%] Starting IranHeadlinesStream wrapper>> "%WRAPPER_LOG%"
echo RepoRoot=%REPO_ROOT%>> "%WRAPPER_LOG%"
echo DB=%DB_PATH%>> "%WRAPPER_LOG%"

py -3 "%REPO_ROOT%\scripts\iran_headlines_stream.py" --db "%DB_PATH%" >> "%WRAPPER_LOG%" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"

if not "!EXIT_CODE!"=="0" (
    (
        echo [%DATE% %TIME%] IranHeadlinesStream wrapper failed.
        echo ExitCode=!EXIT_CODE!
        echo Log=%WRAPPER_LOG%
    ) > "%ERROR_MARKER%"
    exit /b !EXIT_CODE!
)

if exist "%ERROR_MARKER%" del /q "%ERROR_MARKER%" >nul 2>&1
> "%SUCCESS_MARKER%" echo [%DATE% %TIME%] SUCCESS
exit /b 0
