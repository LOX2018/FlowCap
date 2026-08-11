@echo off
chcp 65001 >nul
REM ============================================================
REM  DouYin Auto-DM - headless launcher (background run)
REM  Use real Python path to avoid the Microsoft Store stub.
REM  Logs are written to logs/run_*.log automatically.
REM ============================================================
set PYTHON_EXE=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe
set BASE_DIR=%~dp0

cd /d "%BASE_DIR%"
"%PYTHON_EXE%" -m auto_dm.run
if errorlevel 1 (
    echo.
    echo [ERROR] launch failed, check logs or Python env.
    pause
)
