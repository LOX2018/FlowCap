@echo off
REM Install DYAutoDM daemon as a Windows service (auto-start, always running, non-tray)
REM 以管理员身份运行：注册开机自启的 Windows 服务，同时拉起私信接收(9912)与凭证保活(9911)守护
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] 安装服务（开机自启）...
"%PY%" auto_dm/service.py install --startup auto
if errorlevel 1 goto :fail

echo [2/3] 启动服务...
"%PY%" auto_dm/service.py start
if errorlevel 1 goto :fail

echo [3/3] 完成。服务已开机自启并运行。
echo       私信接收守护: http://127.0.0.1:9912
echo       凭证保活守护: http://127.0.0.1:9911
goto :eof

:fail
echo 安装/启动服务失败，请以管理员身份运行本脚本。
exit /b 1
