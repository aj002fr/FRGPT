@echo off
cd /d "%~dp0\.."
python scripts\live_unified_dashboard.py --event CPI --no-live
pause

