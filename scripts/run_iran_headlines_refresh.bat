@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Iran headlines + Hormuz flow refresh (Task Scheduler wrapper).
REM Uses the saved Telethon session under %%USERPROFILE%%\.market_data.

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "DB_PATH=%REPO_ROOT%\workspace\iran_headlines.db"
set "HORMUZ_EXCEL=Z:\Aastha\hormuz_tracker.xlsx"
set "SHARE_LOG_DIR=%REPO_ROOT%\workspace\logs"
set "SHARE_LOG_FILE=%SHARE_LOG_DIR%\iran_headlines_refresh.log"
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "LOCAL_LOG_FILE=%LOCAL_LOG_DIR%\iran_headlines_refresh.log"
set "STATUS_DIR=%LOCALAPPDATA%\market_data_puller\status"
set "ERROR_MARKER=%STATUS_DIR%\iran_headlines_refresh_last_error.txt"
set "SUCCESS_MARKER=%STATUS_DIR%\iran_headlines_refresh_last_success.txt"
set "SHARE_LOG_DISABLED=0"

call :ensure_dir "%LOCAL_LOG_DIR%" "local log directory" || exit /b 1
call :ensure_dir "%STATUS_DIR%" "status directory" || exit /b 1

if not exist "%SHARE_LOG_DIR%" mkdir "%SHARE_LOG_DIR%" >nul 2>&1
if errorlevel 1 call :log_local_only "[WARN] Unable to create shared log directory: %SHARE_LOG_DIR%"

call :log_line "============================================================"
call :log_line "[%DATE% %TIME%] Starting IranHeadlinesRefresh"

set "CURRENT_STEP=scrape_iran_headlines"
call :run_and_capture py -3 "%REPO_ROOT%\scripts\scrape_iran_headlines.py" --db "%DB_PATH%" --refresh-hormuz --hormuz-excel "%HORMUZ_EXCEL%"
if errorlevel 1 (
    call :write_marker "[%DATE% %TIME%] IranHeadlinesRefresh failed."
    exit /b 1
)

if exist "%ERROR_MARKER%" del /q "%ERROR_MARKER%" >nul 2>&1
> "%SUCCESS_MARKER%" echo [%DATE% %TIME%] SUCCESS
call :log_line "[%DATE% %TIME%] IranHeadlinesRefresh completed successfully"
exit /b 0

:ensure_dir
if exist "%~1" exit /b 0
mkdir "%~1" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to create %~2: %~1
    exit /b 1
)
exit /b 0

:run_and_capture
set "STEP_LOG=%TEMP%\iran_headlines_refresh_%RANDOM%_%RANDOM%.log"

call :log_line "---- %CURRENT_STEP% ----"
%* > "%STEP_LOG%" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
call :append_file_to_logs "%STEP_LOG%"
if exist "%STEP_LOG%" del /q "%STEP_LOG%" >nul 2>&1

if not "!STEP_EXIT!"=="0" (
    call :log_line "[%DATE% %TIME%] ERROR: %CURRENT_STEP% exited with code !STEP_EXIT!"
    exit /b !STEP_EXIT!
)
exit /b 0

:append_file_to_logs
if not exist "%~1" exit /b 0
type "%~1" >> "%LOCAL_LOG_FILE%"
if "%SHARE_LOG_DISABLED%"=="1" exit /b 0
type "%~1" >> "%SHARE_LOG_FILE%" 2>nul
if errorlevel 1 (
    set "SHARE_LOG_DISABLED=1"
    call :log_local_only "[WARN] Shared log unavailable; continuing with local log only: %SHARE_LOG_FILE%"
)
exit /b 0

:log_line
call :log_local_only "%~1"
if "%SHARE_LOG_DISABLED%"=="1" exit /b 0
echo %~1>> "%SHARE_LOG_FILE%" 2>nul
if errorlevel 1 (
    set "SHARE_LOG_DISABLED=1"
    call :log_local_only "[WARN] Shared log unavailable; continuing with local log only: %SHARE_LOG_FILE%"
)
exit /b 0

:log_local_only
echo %~1>> "%LOCAL_LOG_FILE%"
exit /b 0

:write_marker
(
    echo %~1
    echo LocalLog=%LOCAL_LOG_FILE%
    echo ShareLog=%SHARE_LOG_FILE%
) > "%ERROR_MARKER%"
if exist "%SUCCESS_MARKER%" del /q "%SUCCESS_MARKER%" >nul 2>&1
exit /b 0
