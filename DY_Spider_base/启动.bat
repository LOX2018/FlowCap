@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ========== 配置区（按需修改）==========
set "PY=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
set "ROOT=%~dp0"
REM =======================================

cd /d "%ROOT%"

echo ===================================================
echo   Douyin Live Auto-DM - Full Auto Start (auto_dm)
echo   Work dir: %ROOT%
echo ===================================================

REM 1) 检查 Python
if not exist "%PY%" (
    echo [ERROR] Python not found: %PY%
    echo Please edit PY variable in this script.
    pause
    exit /b 1
)

REM 2) 依赖检查（loguru/playwright 缺失则自动安装）
"%PY%" -c "import loguru, playwright" 2>nul
if errorlevel 1 (
    echo [INFO] Missing deps, installing requirements.txt ...
    "%PY%" -m pip install -r "%ROOT%requirements.txt"
    "%PY%" -m playwright install chromium 2>nul
)

REM 3) 全自动启动（Python 侧自动开浏览器登录/抓 cookie/抓签名）
echo.
echo [AUTO START] First run will pop a browser - scan QR to login Douyin (one time only, then auto reuse)
echo Press Ctrl+C to stop. Credentials saved in .env (DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY).
echo.
"%PY%" -m auto_dm.run

endlocal
pause
