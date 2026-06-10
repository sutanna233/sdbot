@echo off
cd /d "%~dp0"
chcp 65001 >nul

if "%~1"=="" (
    python generate_artists.py shell
) else (
    python generate_artists.py %*
)
if errorlevel 1 echo.
pause