@echo off
REM Start Telegram Bot in Background
REM Double-click this to start the bot

cd /d "%~dp0.."
echo Starting Telegram Bot...
echo Press Ctrl+C to stop.
echo.

python scripts\telegram_bot.py

pause




