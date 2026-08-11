@echo off
chcp 65001 >nul
REM ============================================================
REM  DouYin Auto-DM - main launcher (GUI + auto-start daemons)
REM  Double-click to: 1) launch browser daemon (keep credential alive)
REM                    2) launch receive daemon (per-account isolated DM)
REM                    3) start the GUI
REM  Daemons run as standalone no-window processes; they keep running
REM  after the GUI is closed. Stop them from the GUI or kill the processes.
REM ============================================================
set PYTHON_EXE=C:\Users\LOX\AppData\Local\Programs\Python\Python314\pythonw.exe
set PYTHON_EXE_CON=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe
set BASE_DIR=%~dp0

if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=%PYTHON_EXE_CON%
)

cd /d "%BASE_DIR%"

REM 1) browser daemon (no window, keep credential alive)
echo [1/3] Starting browser daemon...
start "" "%PYTHON_EXE%" -m auto_dm.browser_daemon --account 主
timeout /t 2 >nul

REM 2) receive daemon (no window, per-account isolated DM)
echo [2/3] Starting receive daemon...
start "" "%PYTHON_EXE%" -m auto_dm.recv_daemon
timeout /t 2 >nul

REM 3) GUI (needs a window)
echo [3/3] Starting GUI...
"%PYTHON_EXE_CON%" -m auto_dm.gui
if errorlevel 1 (
    echo.
    echo [ERROR] GUI launch failed, check logs or Python env.
    pause
)
