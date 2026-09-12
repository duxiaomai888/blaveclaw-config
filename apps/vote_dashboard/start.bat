@echo off
chcp 65001 >nul
setlocal

REM VoteStrategy 桌面客户端启动脚本
REM 双击此文件即可启动

cd /d "%~dp0"
cd /d "%~dp0..\..\.."

echo ============================================================
echo   VoteStrategy Desktop Client
echo   BTCUSDT 1h - Blave API - Vote Algorithm
echo ============================================================
echo.

REM 用项目根的 venv (如果有)
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [start] using Python: %PY%
echo [start] launching desktop window ...
echo.

%PY% apps/vote_dashboard/desktop.py

if errorlevel 1 (
    echo.
    echo [error] 程序异常退出, 按任意键关闭
    pause >nul
)
