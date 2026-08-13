@echo off
REM ============================================================
REM  DYAutoDM one-click package entry (double-click to run)
REM  Calls build-oneclick.ps1 in the same directory.
REM  Usage:  this.bat             (normal build, version +1)
REM          this.bat clean       (clean dist/build first)
REM          this.bat nobump      (keep current version)
REM ============================================================
setlocal
cd /d "%~dp0"

set EXTRA=
if /i "%~1"=="clean"  set EXTRA=%EXTRA% -Clean
if /i "%~1"=="nobump" set EXTRA=%EXTRA% -NoBump

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-oneclick.ps1"%EXTRA%

echo.
if %errorlevel%==0 (
    echo [ok] Build success. Output in dist\DYAutoDM.
) else (
    echo [fail] Build failed, see log above.
)
echo.
pause
