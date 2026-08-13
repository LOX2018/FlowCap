@echo off
REM ============================================================
REM  DYAutoDM 一键打包入口（双击运行）
REM  - 调用同目录的 一键打包.ps1（默认 --no-clean，保留旧随附资源）
REM  - 传参数: 一键打包.bat clean   -> 先 clean 再打包
REM           一键打包.bat nobump   -> 不递增版本号
REM ============================================================
setlocal
cd /d "%~dp0"

REM 解析参数（clean / nobump）
set EXTRA=
if /i "%~1"=="clean"   set EXTRA=%EXTRA% -Clean
if /i "%~1"=="nobump"  set EXTRA=%EXTRA% -NoBump

REM 用当前会话的 PowerShell 执行脚本（不另开窗口）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0一键打包.ps1"%EXTRA%

REM 显示结果
echo.
if %errorlevel%==0 (
    echo [ok] 打包成功，产物在 dist\DYAutoDM 目录。
) else (
    echo [fail] 打包失败，请查看上方日志。
)
echo.
pause
