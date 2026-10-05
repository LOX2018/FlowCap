<#
.SYNOPSIS
    DYAutoDM v2 一键开发启动脚本（后端 sidecar + Vite 前端，免打包）
.DESCRIPTION
    拉起「已构建的 backend sidecar + Vite dev server + 浏览器」用于日常联调。

    与 dev.ps1 的分工（两者互补，按需选）：
      dev.ps1       —— 全栈：含 Rust 桌面壳。需要桌面壳能力（原生对话框 / invoke 命令 /
                       write_boot_log）/ 改了 src-tauri 时用；首次或改 Rust 后约 84s 编译。
      本脚本        —— 不起桌面壳：纯浏览器版，**省掉 Rust 编译**，最轻量；
                       改了 frontend / backend 后用它联调最快。

    两者都把 DY_APP_ROOT 与会员态（DY_MEMBER / DY_MEMBER_KEY）注入子进程 ——
    这是分支隔离铁律的要求：源码树内不得产生 profile / db
    （不注入时 vbrowser.app_root() 会回落到项目根，data/ accounts/ logs/ 会落进源码仓库）。
.EXAMPLE
    .\start_dev.ps1                 # 启动后端 + 前端 + 浏览器（设计分支环境）
    .\start_dev.ps1 -DevBackend     # 后端用 Python 源码跑（uvicorn 思路，改 .py 存盘即生效）
    .\start_dev.ps1 -Stop           # 停止所有服务
    .\start_dev.ps1 -NoBrowser      # 启动服务但不打开浏览器
    .\start_dev.ps1 -AppRoot C:\temp\dyautodm_test   # 显式切到主分支环境
#>
param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [switch]$DevBackend,  # 用 python 源码运行后端，而非打包 exe（便于 debug）
    [string]$AppRoot = "C:\temp\dyautodm_design"
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
# 项目标准解释器（含 fastapi/uvicorn/camoufox/patchright；与 build_all.py 口径一致）
$PY314 = "C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"

# ---------------------------------------------------------------------------
# 环境注入（分支隔离铁律；显式配置原则：只认 -AppRoot，绝不探测本机状态自行决定）
# ---------------------------------------------------------------------------
if (-not (Test-Path $AppRoot)) {
    Write-Host "[ERROR] 环境根不存在: $AppRoot" -ForegroundColor Red
    Write-Host "        设计分支环境应为 C:\temp\dyautodm_design；主分支为 C:\temp\dyautodm_test。" -ForegroundColor DarkGray
    exit 1
}
$env:DY_APP_ROOT = $AppRoot
$sf = Join-Path $AppRoot "members\.session.json"
if (Test-Path $sf) {
    try {
        $j = Get-Content $sf -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($j.member_id) {
            $env:DY_MEMBER = [string]$j.member_id
            if ($j.master_key) { $env:DY_MEMBER_KEY = [string]$j.master_key }
        }
    } catch {
        Write-Host "[WARN] members\.session.json 解析失败: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Stop-All {
    Write-Host "[1/3] 停止 Vite 前端..." -ForegroundColor Yellow
    # 2026-09-17 修补（OCR 审查 HIGH —— Get-Process 无 CommandLine 属性）：
    # `Get-Process` 返回的是 System.Diagnostics.Process，**没有 `CommandLine`**
    # 属性（只有 `Get-CimInstance Win32_Process` 有）。在
    # `$ErrorActionPreference = "Stop"` 下访问不存在的属性会**抛终止错误**，
    # 且 `-or` 右支恒为 $null → 原过滤条件形同"匹配任意含 DYAutoDM_v2 的 node"。
    # 改用 Get-CimInstance 拿真实命令行。
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*vite*" -and $_.CommandLine -like "*DYAutoDM_v2*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    Write-Host "[2/3] 停止后端 sidecar..." -ForegroundColor Yellow
    # 2026-09-17 修补（OCR 审查 HIGH —— `-Name` 不支持通配符）：
    # `Get-Process -Name "dyautodm-*"` 把 `dyautodm-*` 当**字面量**进程名，
    # 永远匹配不到；且 `-ErrorAction SilentlyContinue` 并不能可靠抑制
    # 名称解析错误（配合 `$ErrorActionPreference = "Stop"` 会让脚本在此中止，
    # 导致下面的「[3/3] 端口检查」永不执行）。改用 CIM 的 Like 过滤。
    Get-CimInstance Win32_Process -Filter "Name LIKE 'dyautodm-%'" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

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

function Show-Env {
    Write-Host "  环境根   : $($env:DY_APP_ROOT)" -ForegroundColor White
    if ($env:DY_MEMBER) {
        Write-Host "  会员态   : $($env:DY_MEMBER)（DY_MEMBER_KEY 已设，不打印）" -ForegroundColor White
    } else {
        Write-Host "  会员态   : (未读到 members\.session.json —— .env.enc 可能无法解密)" -ForegroundColor Yellow
    }
}

if ($Stop) { Stop-All; exit 0 }

# 清理可能残留的旧进程
Write-Host "`n=== DYAutoDM v2 开发模式启动（免打包 · 不带桌面壳）===" -ForegroundColor Cyan
Show-Env
Write-Host ""
Write-Host "[0/3] 清理旧进程..." -ForegroundColor Yellow
# 2026-09-17 修补（OCR 审查 HIGH —— 同 Stop-All：`-Name` 不支持通配符）：
# `Get-Process -Name "dyautodm-backend*"` 匹配不到任何进程 → 「清理旧进程」失效，
# 残留后端会占用端口导致后续启动失败。改用 CIM Like 过滤。
Get-CimInstance Win32_Process -Filter "Name LIKE 'dyautodm-backend%'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# 只关闭本项目 Vite
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*vite*1420*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# 1. 启动后端
Write-Host "[1/3] 启动后端 FastAPI sidecar..." -ForegroundColor Yellow
if ($DevBackend) {
    if (-not (Test-Path $PY314)) {
        Write-Host "[ERROR] 未找到项目解释器: $PY314" -ForegroundColor Red
        exit 1
    }
    Write-Host "  (源码模式) $PY314 -X utf8 backend\main.py" -ForegroundColor DarkGray
    # Start-Process 继承当前进程环境 → $env:DY_APP_ROOT / DY_MEMBER* 自动透传给后端
    # 用绝对路径跑（Python 会把「脚本所在目录」即 backend/ 放进 sys.path，
    # 所以 `from config import settings` 之类的扁平导入仍然可用），
    # 而 CWD 给 $AppRoot —— 与 sidecar 一致，日志不落源码树。
    Start-Process -WindowStyle Minimized -FilePath $PY314 `
        -ArgumentList "-X","utf8",(Join-Path $ROOT "backend\main.py") `
        -WorkingDirectory $AppRoot
} else {
    if (-not (Test-Path $BACKEND_EXE)) {
        Write-Host "[ERROR] 后端 exe 不存在: $BACKEND_EXE" -ForegroundColor Red
        Write-Host "请先运行: .\scripts\build_sidecar.ps1" -ForegroundColor Red
        Write-Host "或改用源码模式: .\start_dev.ps1 -DevBackend" -ForegroundColor Red
        exit 1
    }
    # CWD 必须是 $AppRoot：backend 的日志用相对路径 `logs/run_*.log`（main.py），
    # 以启动方 CWD 为基准 —— 用 $ROOT 会把日志写进源码树。
    Start-Process -WindowStyle Minimized -FilePath $BACKEND_EXE -WorkingDirectory $AppRoot
}

# 实测：sidecar 冷启动到 uvicorn 绑定 8000 约 18s（lifespan 内并行拉起守护），
# 原 15s 必然假报 FAIL。给 60s 余量。
if (Wait-For-Url "$BACKEND_URL/api/status" 60) {
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

if (Wait-For-Url $FRONTEND_URL 60) {
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

Write-Host "`n=== 全部就绪（免打包）===" -ForegroundColor Cyan
Write-Host "前端:       $FRONTEND_URL" -ForegroundColor White
Write-Host "后端:       $BACKEND_URL" -ForegroundColor White
Write-Host "API 文档:   $BACKEND_URL/docs" -ForegroundColor White
Write-Host "`n停止服务:   .\start_dev.ps1 -Stop" -ForegroundColor DarkGray
Write-Host "源码调试:   .\start_dev.ps1 -DevBackend" -ForegroundColor DarkGray
Write-Host "要桌面壳:   .\dev.ps1（含 Rust 壳，首次/改 Rust 后约 84s 编译）`n" -ForegroundColor DarkGray
