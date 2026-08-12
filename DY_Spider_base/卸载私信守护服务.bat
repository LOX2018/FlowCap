@echo off
REM Uninstall DYAutoDM daemon Windows service
REM 以管理员身份运行：停止并卸载服务
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] 停止服务...
"%PY%" auto_dm/service.py stop
echo [2/2] 卸载服务...
"%PY%" auto_dm/service.py remove
echo 完成。服务已卸载。
