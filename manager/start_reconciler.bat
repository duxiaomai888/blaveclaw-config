@echo off
REM Watchdog wrapper for Windows — restarts reconciler.py on crash and sends Telegram alert.
REM Usage: cd C:\Users\Blaw-D\Desktop\BBAC-D ^&^& manager\start_reconciler.bat
REM
REM Mirrors manager/start_reconciler.sh. Requires:
REM   - Python 3 on PATH (or set PYTHON below)
REM   - Project root contains .env with telegram + blave credentials
REM   - manager/reconciler.py + lib/notify.py wired
REM
REM Differences from the .sh version:
REM   * No `bash` heredoc → uses python -c with single-quoted strings for notify
REM   * No `sleep`  → uses `timeout` (built-in to Windows 10+)
REM   * No `python3` → uses `python` (or PYTHON env var)
REM   * Runs in foreground; close the window to stop

setlocal EnableDelayedExpansion
set PYTHON=python
if not "%PYTHON%"=="" set PYTHON=%PYTHON%

REM Resolve script directory and cd to project root
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."

REM ── Watchdog: message helper (python -c equivalent of bash get_msg) ───────────
:get_msg <key> <default> [<arg>]
set "KEY=%~1"
set "DEFAULT=%~2"
set "ARG=%~3"
set "MSG="
for /f "usebackq delims=" %%M in (`%PYTHON% -c "import sys, json; from lib.portfolio import load_portfolio_config; cfg=load_portfolio_config(); msgs=cfg.get('messages',{}); tpl=msgs.get(r'%KEY%', r'%DEFAULT%'); arg=r'%ARG%' if r'%ARG%' else ''; print(tpl.format(code=arg) if arg else tpl)" 2^>nul`) do set "MSG=%%M"
if "!MSG!"=="" set "MSG=%DEFAULT%"
echo !MSG!
goto :eof

REM ── Startup notification ──────────────────────────────────────────────────────
call :get_msg "watchdog_started" "Reconciler started (Windows)" | %PYTHON% -c "import sys; sys.path.insert(0, '.'); from lib.notify import make_sender; s=make_sender(); s(sys.stdin.read())" 2>nul

REM ── Watchdog loop ────────────────────────────────────────────────────────────
:loop
%PYTHON% manager\reconciler.py
set "EXIT_CODE=%ERRORLEVEL%"

REM Build restart message via get_msg (passes exit code as {code})
call :get_msg "watchdog_restart" "Reconciler crashed (code {code}), restarting in 10s" "%EXIT_CODE%"
set "RESTART_MSG=%MSG%"
echo !RESTART_MSG!
%PYTHON% -c "import sys; sys.path.insert(0, '.'); from lib.notify import make_sender; s=make_sender(); s(sys.argv[1])" "!RESTART_MSG!" 2>nul

REM Sleep 10 seconds
timeout /t 10 /nobreak >nul
goto :loop
