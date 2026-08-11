@echo off
chcp 65001 >nul
REM ============================================================
REM  Browser daemon launcher (no console window, standalone process)
REM  Keeps the browser container and login credential alive even
REM  after the GUI is closed.
REM  Stop: GUI "Account" tab -> "Stop daemon", or kill browser_daemon.
REM ============================================================
set PYTHON_EXE=C:\Users\LOX\AppData\Local\Programs\Python\Python314\pythonw.exe
set BASE_DIR=%~dp0

if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe
)

cd /d "%BASE_DIR%"
start "" "%PYTHON_EXE%" -m auto_dm.browser_daemon --account 主
echo Browser daemon started (no window). Check status / stop from GUI.
timeout /t 2 >nul
