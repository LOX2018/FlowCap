@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ========== 配置区（按需修改）==========
set "PY=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
set "ROOT=%~dp0"
REM =======================================

cd /d "%ROOT%"

echo ===================================================
echo   Douyin Live Auto-DM - GUI Manager
echo   Work dir: %ROOT%
echo ===================================================

if not exist "%PY%" (
    echo [ERROR] Python not found: %PY%
    echo Please edit PY variable in this script.
    pause
    exit /b 1
)

"%PY%" -c "import tkinter" 2>nul
if errorlevel 1 (
    echo [ERROR] tkinter not available in this Python build.
    pause
    exit /b 1
)

echo [INFO] Launching GUI ...
"%PY%" -m auto_dm.gui

endlocal
pause
