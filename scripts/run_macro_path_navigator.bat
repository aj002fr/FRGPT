@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "STATUS_DIR=%LOCALAPPDATA%\market_data_puller\status"
set "LOG_FILE=%LOCAL_LOG_DIR%\macro_path_navigator.log"

if not exist "%LOCAL_LOG_DIR%" mkdir "%LOCAL_LOG_DIR%" >nul 2>&1
if not exist "%STATUS_DIR%" mkdir "%STATUS_DIR%" >nul 2>&1

echo ============================================================>> "%LOG_FILE%"
echo [%DATE% %TIME%] Starting Macro Path Navigator>> "%LOG_FILE%"
echo RepoRoot=%REPO_ROOT%>> "%LOG_FILE%"

py -3 "%REPO_ROOT%\scripts\macro_path_navigator\app.py" --host 127.0.0.1 --port 5088 >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"

if not "!EXIT_CODE!"=="0" (
    (
        echo [%DATE% %TIME%] Macro Path Navigator failed.
        echo ExitCode=!EXIT_CODE!
        echo Log=%LOG_FILE%
    ) > "%STATUS_DIR%\macro_path_last_error.txt"
    exit /b !EXIT_CODE!
)

exit /b 0
