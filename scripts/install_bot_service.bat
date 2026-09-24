@echo off
REM Telegram Bot Windows Service Installer
REM Uses NSSM (https://nssm.cc/download)

echo ============================================
echo Telegram Bot Service Installer
echo ============================================
echo.
echo 1. Download NSSM from: https://nssm.cc/download
echo 2. Extract nssm.exe to a folder in PATH
echo 3. Run the following commands as Administrator:
echo.
echo    nssm install TelegramMarketBot "C:\Python311\python.exe"
echo    nssm set TelegramMarketBot AppParameters "scripts\telegram_bot.py"
echo    nssm set TelegramMarketBot AppDirectory "Z:\Aastha\market_data_puller"
echo    nssm set TelegramMarketBot DisplayName "Market Data Telegram Bot"
echo    nssm set TelegramMarketBot Start SERVICE_AUTO_START
echo    nssm start TelegramMarketBot
echo.
echo To remove: nssm remove TelegramMarketBot confirm
