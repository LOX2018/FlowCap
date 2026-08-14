<#
.SYNOPSIS
    DYAutoDM v2 一键开发启动脚本
.DESCRIPTION
    自动拉起 FastAPI 后端 sidecar + Vite 前端，并打开浏览器。
    支持 -Stop 一键停止所有服务。
.EXAMPLE
    .\start_dev.ps1            # 启动后端+前端+浏览器
    .\start_dev.ps1 -Stop      # 停止所有服务
    .\start_dev.ps1 -NoBrowser # 启动服务但不打开浏览器
#>
param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [switch]$DevBackend  # 用 python 源码运行后端，而非打包 exe（便于 debug）
)

$ErrorActionPreference = "Stop"
# 修复 PowerShell 5.1 中文输出编码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { chcp 65001 > $null } catch {}
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$BACKEND_EXE = Join-Path $ROOT "src-tauri\binaries\dyautodm-backend-x86_64-pc-windows-msvc.exe"
$FRONTEND_DIR = Join-Path $ROOT "frontend"
$BACKEND_URL = "http://127.0.0.1:8000"
$FRONTEND_URL = "http://127.0.0.1:1420"

function Stop-All {
    Write-Host "[1/3] 停止 Vite 前端..." -ForegroundColor Yellow
    Get-Process -Name "node","vite" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*DYAutoDM_v2*" -or $_.CommandLine -like "*vite*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    Write-Host "[2/3] 停止后端 sidecar..." -ForegroundColor Yellow
    Get-Process -Name "dyautodm-*" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    Write-Host "[3/3] 端口检查..." -ForegroundColor Yellow
    $b = Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet -WarningAction SilentlyContinue
    $f = Test-NetConnection -ComputerName 127.0.0.1 -Port 1420 -InformationLevel Quiet -WarningAction SilentlyContinue
    if (-not $b -and -not $f) {
        Write-Host "[OK] 所有服务已停止" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 端口仍被占用: backend=$b frontend=$f" -ForegroundColor Yellow
    }
}

function Wait-For-Url($url, $timeoutSec = 20) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2
            return $true
        } catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

if ($Stop) { Stop-All; exit 0 }

# 清理可能残留的旧进程
Write-Host "`n=== DYAutoDM v2 开发模式启动 ===`n" -ForegroundColor Cyan
Write-Host "[0/3] 清理旧进程..." -ForegroundColor Yellow
Get-Process -Name "dyautodm-backend*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# 只关闭本项目 Vite
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*vite*1420*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# 1. 启动后端
Write-Host "[1/3] 启动后端 FastAPI sidecar..." -ForegroundColor Yellow
if ($DevBackend) {
    Write-Host "  (源码模式) python backend/main.py" -ForegroundColor DarkGray
    Start-Process -WindowStyle Minimized -FilePath "py" `
        -ArgumentList "-X","utf8","backend/main.py" `
        -WorkingDirectory $ROOT
} else {
    if (-not (Test-Path $BACKEND_EXE)) {
        Write-Host "[ERROR] 后端 exe 不存在: $BACKEND_EXE" -ForegroundColor Red
        Write-Host "请先运行: .\scripts\build_sidecar.ps1" -ForegroundColor Red
        exit 1
    }
    Start-Process -WindowStyle Minimized -FilePath $BACKEND_EXE
}

if (Wait-For-Url "$BACKEND_URL/api/status" 15) {
    $r = Invoke-WebRequest "$BACKEND_URL/api/status" -UseBasicParsing
    Write-Host "  [OK] 后端就绪: $($r.Content)" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] 后端启动超时" -ForegroundColor Red
    exit 1
}

# 2. 启动前端
Write-Host "[2/3] 启动 Vite 前端..." -ForegroundColor Yellow
# 用 cmd /k 保持窗口，便于查看 vite 日志；用 Start-Process 工作目录
Start-Process -WindowStyle Minimized -FilePath "cmd" `
    -ArgumentList "/c","cd /d `"$FRONTEND_DIR`" && npx vite --port 1420 --host 127.0.0.1" `
    -WorkingDirectory $FRONTEND_DIR

if (Wait-For-Url $FRONTEND_URL 20) {
    Write-Host "  [OK] 前端就绪: $FRONTEND_URL" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] 前端启动超时" -ForegroundColor Red
    exit 1
}

# 3. 打开浏览器
Write-Host "[3/3] 打开浏览器..." -ForegroundColor Yellow
if (-not $NoBrowser) {
    Start-Process $FRONTEND_URL
    Write-Host "  [OK] 浏览器已打开" -ForegroundColor Green
}

Write-Host "`n=== 全部就绪 ===" -ForegroundColor Cyan
Write-Host "前端:       $FRONTEND_URL" -ForegroundColor White
Write-Host "后端:       $BACKEND_URL" -ForegroundColor White
Write-Host "API 文档:   $BACKEND_URL/docs" -ForegroundColor White
Write-Host "`n停止服务:   .\start_dev.ps1 -Stop" -ForegroundColor DarkGray
Write-Host "源码调试:   .\start_dev.ps1 -DevBackend`n" -ForegroundColor DarkGray
