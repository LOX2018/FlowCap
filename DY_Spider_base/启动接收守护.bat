@echo off
chcp 65001 >nul
REM ============================================================
REM  Private-message receive daemon launcher (no console window).
REM  Per-account isolation: one frontier-im.douyin.com long connection
REM  per account; messages are split by conversation_id and exposed to
REM  the WebView "Messages" tab via local HTTP (127.0.0.1:9912).
REM  Keeps receiving in background even after the WebView window is closed.
REM  Stop: WebView "Messages" tab -> "Stop receive daemon", or kill recv_daemon.
REM  Example for specific accounts:
REM   "%PYTHON_EXE%" -m auto_dm.recv_daemon --accounts zhu,xiaohao2
REM ============================================================
set PYTHON_EXE=C:\Users\LOX\AppData\Local\Programs\Python\Python314\pythonw.exe
set BASE_DIR=%~dp0

if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe
)

cd /d "%BASE_DIR%"
start "" "%PYTHON_EXE%" -m auto_dm.recv_daemon
echo Receive daemon started (no window). Check status / stop from WebView.
timeout /t 2 >nul
