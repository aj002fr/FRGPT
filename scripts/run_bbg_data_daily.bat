@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Daily Bloomberg data loader (Task Scheduler wrapper).
REM Assumes Python launcher is available on PATH.
REM Writes to both the shared repo log and a local fallback log so
REM scheduler failures are still diagnosable when the network share is flaky.

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "BBG_EXCEL=\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx"
set "DB_PATH_1=%REPO_ROOT%\workspace\economic_events.db"
set "DB_PATH_2=D:\FRDBs\workspace\economic_events.db"
set "SHARE_LOG_DIR=%REPO_ROOT%\workspace\logs"
set "SHARE_LOG_FILE=%SHARE_LOG_DIR%\bbg_data_daily.log"
set "LOCAL_LOG_DIR=%LOCALAPPDATA%\market_data_puller\logs"
set "LOCAL_LOG_FILE=%LOCAL_LOG_DIR%\bbg_data_daily.log"
set "STATUS_DIR=%LOCALAPPDATA%\market_data_puller\status"
set "ERROR_MARKER=%STATUS_DIR%\bbg_data_daily_last_error.txt"
set "SUCCESS_MARKER=%STATUS_DIR%\bbg_data_daily_last_success.txt"
set "SHARE_LOG_DISABLED=0"
set "FAIL_COUNT=0"

call :ensure_dir "%LOCAL_LOG_DIR%" "local log directory" || exit /b 1
call :ensure_dir "%STATUS_DIR%" "status directory" || exit /b 1

if not exist "%SHARE_LOG_DIR%" mkdir "%SHARE_LOG_DIR%" >nul 2>&1
if errorlevel 1 call :log_local_only "[WARN] Unable to create shared log directory: %SHARE_LOG_DIR%"

call :log_line "============================================================"
call :log_line "[%DATE% %TIME%] Starting BBGDataDaily run"

call :run_target "primary" "%DB_PATH_1%"
if errorlevel 1 set /a FAIL_COUNT+=1

call :run_target "secondary" "%DB_PATH_2%"
if errorlevel 1 set /a FAIL_COUNT+=1

if not "%FAIL_COUNT%"=="0" (
    call :write_marker "[%DATE% %TIME%] BBGDataDaily finished with %FAIL_COUNT% failed target(s)."
    call :log_line "[%DATE% %TIME%] BBGDataDaily run completed with %FAIL_COUNT% failed target(s)"
    exit /b 1
)

if exist "%ERROR_MARKER%" del /q "%ERROR_MARKER%" >nul 2>&1
> "%SUCCESS_MARKER%" echo [%DATE% %TIME%] SUCCESS
call :log_line "[%DATE% %TIME%] BBGDataDaily run completed successfully"
exit /b 0

:ensure_dir
if exist "%~1" exit /b 0
mkdir "%~1" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to create %~2: %~1
    exit /b 1
)
exit /b 0

:run_target
set "TARGET_NAME=%~1"
set "TARGET_DB=%~2"
call :log_line "[%DATE% %TIME%] Updating %TARGET_NAME% DB: %TARGET_DB%"

set "CURRENT_STEP=%TARGET_NAME% create_bbg_data_table"
call :run_and_capture py -3 "%REPO_ROOT%\data_setup\create_bbg_data_table.py" --excel "%BBG_EXCEL%" --db "%TARGET_DB%"
if errorlevel 1 (
    call :write_marker "[%DATE% %TIME%] %TARGET_NAME% create_bbg_data_table failed for %TARGET_DB%."
    exit /b 1
)

set "CURRENT_STEP=%TARGET_NAME% ingest_excel_calendar_to_sqlite"
call :run_and_capture py -3 "%REPO_ROOT%\data_setup\ingest_excel_calendar_to_sqlite.py" --db "%TARGET_DB%"
if errorlevel 1 (
    call :write_marker "[%DATE% %TIME%] %TARGET_NAME% ingest_excel_calendar_to_sqlite failed for %TARGET_DB%."
    exit /b 1
)

call :log_line "[%DATE% %TIME%] %TARGET_NAME% DB finished successfully"
exit /b 0

:run_and_capture
set "STEP_LOG=%TEMP%\bbg_data_daily_%RANDOM%_%RANDOM%.log"

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


