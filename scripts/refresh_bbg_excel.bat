@echo off
setlocal

REM Daily Bloomberg workbook refresh wrapper.
REM Run this on the machine with Bloomberg Excel access.
REM Portable mode: this .bat and refresh_bbg_excel.py can live side-by-side
REM in a standalone folder (for example the Meini shared folder).

set SCRIPT_DIR=%~dp0
set BBG_EXCEL=\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx
set LOG_DIR=%SCRIPT_DIR%logs
set LOG_FILE=%LOG_DIR%\bbg_excel_refresh.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================>> "%LOG_FILE%"
echo [%DATE% %TIME%] Starting BBGRefreshExcel run>> "%LOG_FILE%"

py -3 "%SCRIPT_DIR%refresh_bbg_excel.py" --workbook "%BBG_EXCEL%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 exit /b %errorlevel%

echo [%DATE% %TIME%] BBGRefreshExcel run completed successfully>> "%LOG_FILE%"

endlocal
