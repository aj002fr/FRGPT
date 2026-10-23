@echo off
setlocal

REM Simple wrapper for Task Scheduler to run the L1 auto builder.
REM Assumes python is on PATH and repository root is current working dir.

py -3 "%~dp0run_l1_auto_builder.py"

endlocal





