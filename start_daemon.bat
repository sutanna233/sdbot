@echo off
cd /d "%~dp0"
chcp 65001 >nul

echo ================================
echo   sdbot - 后台守护启动
echo ================================

:: 启动 WebUI（后台端口 7861）
echo [1/2] 启动 WebUI...
start /B pythonw sdbot.py webui --port 7861 --host 127.0.0.1

:: 启动 Telegram Bot（如果有 token 的话）
echo [2/2] 启动 Telegram Bot...
start /B pythonw sdbot.py telegram start

echo.
echo 所有服务已后台启动！
echo WebUI: http://127.0.0.1:7861
echo.
echo 关闭方式:
echo   1) taskkill /f /im pythonw.exe
echo   2) 或重启电脑
echo.
