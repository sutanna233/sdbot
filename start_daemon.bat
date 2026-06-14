@echo off
chcp 65001 >nul

:: 支持 UNC 网络路径（映射临时驱动器）
pushd "%~dp0" || (
    echo [ERR] 无法进入项目目录: %~dp0
    echo 请从本地路径运行此脚本（例如 C:\sdbot\start_daemon.bat）
    pause
    exit /b 1
)

echo ================================
echo   sdbot - 后台守护启动
echo ================================

:: 启动 WebUI（后台端口 7861）
echo [1/2] 启动 WebUI...
start /B pythonw sdbot.py webui --port 7861 --host 127.0.0.1

:: 启动 Telegram Bot（如果有 token 的话）
echo [2/2] 启动 Telegram Bot...
start /B pythonw sdbot.py telegram start

popd

echo.
echo 所有服务已后台启动！
echo WebUI: http://127.0.0.1:7861
echo.
echo 关闭方式:
echo   1) taskkill /f /im pythonw.exe
echo   2) 或重启电脑
echo.
