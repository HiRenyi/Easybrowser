@echo off
chcp 65001 >nul
title EasyBrowser
cd /d %~dp0

REM ============================================================
REM  EasyBrowser one-click launcher (portable version)
REM  Double-click this file to start the service
REM ============================================================

if not exist "EasyBrowser.exe" (
    echo [ERROR] EasyBrowser.exe not found in current folder
    pause
    exit /b 1
)

echo ========================================
echo    EasyBrowser starting...
echo ========================================

REM Start main program (background, launch mode uses bundled Chromium)
start "" "EasyBrowser.exe" serve --mode launch

REM Wait 5 seconds, then open health check
timeout /t 5 /nobreak >nul
start msedge "http://127.0.0.1:58086/health"

echo.
echo   Service is running: http://127.0.0.1:58086
echo   Health check:      http://127.0.0.1:58086/health
echo   API docs:          http://127.0.0.1:58086/docs
echo  ------------------------------------
echo   First time: login to your OA/systems in the opened browser
echo   AI connection: http://127.0.0.1:58086
echo   Stop: close the EasyBrowser window
echo ========================================
pause
