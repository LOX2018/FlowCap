# 一键打包 sidecar（Windows）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\build_sidecar.ps1

$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\.."
Set-Location "$root\backend"

Write-Host "==> 安装 PyInstaller" -ForegroundColor Cyan
python -m pip install --upgrade pyinstaller -q

Write-Host "==> 打包 dyautodm-backend" -ForegroundColor Cyan
python -m PyInstaller --onefile --name dyautodm-backend `
    --distpath "..\src-tauri\binaries" `
    --workpath "build\backend" `
    --specpath "build\backend" `
    --clean --noconfirm main.py

Write-Host "==> 打包 dyautodm-browser-daemon" -ForegroundColor Cyan
python -m PyInstaller --onefile --name dyautodm-browser-daemon `
    --distpath "..\src-tauri\binaries" `
    --workpath "build\browser_daemon" `
    --specpath "build\browser_daemon" `
    --clean --noconfirm daemon\browser_daemon.py

Write-Host "==> 打包 dyautodm-recv-daemon" -ForegroundColor Cyan
python -m PyInstaller --onefile --name dyautodm-recv-daemon `
    --distpath "..\src-tauri\binaries" `
    --workpath "build\recv_daemon" `
    --specpath "build\recv_daemon" `
    --clean --noconfirm daemon\recv_daemon.py

Write-Host "==> 全部完成，二进制位于 src-tauri\binaries\" -ForegroundColor Green
