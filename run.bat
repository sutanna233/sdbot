@echo off
cd /d "%~dp0"
chcp 65001 >nul

if "%~1"=="" (
    python sdbot.py shell
) else (
    python sdbot.py %*
)
if errorlevel 1 echo.
pause
