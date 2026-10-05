<#
.SYNOPSIS
    FlowCap v2 免打包全栈开发启动（tauri dev + 分支隔离环境）
.DESCRIPTION
    术语澄清（"免打包"的边界）：
      "免打包"省掉的是 **Python sidecar 的 PyInstaller 打包（~4.6~9min）与部署**，
      省不掉 **Rust 桌面壳的编译** —— Tauri 的壳是一个原生 exe，必须被编译出来才能运行。
      不过 dev profile（opt-level=0、多 codegen-units）比 release 快一个数量级：
      实测首次 84s、改 Rust 后增量同级；而 release 打包每次约 7min
      （根因：Cargo.toml 的 codegen-units=1 + lto=true —— "产物最小 = 编译最慢"）。
      **改前端 .tsx / 后端 .py 不会触发 Rust 重编**：起一次本脚本后，
      前端存盘即 HMR；后端改用 start_dev.ps1 / uvicorn --reload。
      要"彻底零编译"就别起桌面壳 —— 用 start_dev.ps1（纯浏览器 + sidecar）。

    一键启动 `npx tauri dev`（Rust 桌面壳 + Vite 前端 + 已构建的 sidecar），
    并把 FLOWCAP_APP_ROOT / 会员态注入子进程，使运行时数据全部落在**本分支独立环境**
    （默认 C:\temp\flowcap_design），源码树零污染。

    为什么需要本脚本（而不是直接 npx tauri dev）：
      1) `tauri dev` 拉起的 sidecar 继承**启动它的 shell 环境**；不导出 FLOWCAP_APP_ROOT 时
         `vbrowser.app_root()` 回落到项目根 → data/ accounts/ logs/ 落进源码仓库
         （违反分支隔离铁律：源码目录不得有 profile / db）。
      2) 会员加密凭证（.env.enc）需要 DY_MEMBER + DY_MEMBER_KEY 才能解密；
         缺会员态会报「无 .env（索引未登记？）」并可能引发重启循环。
      3) cargo 不在 PATH 时 `tauri dev` 报 program not found。
    以上三点都在本脚本内一次性固化，不依赖调用方记得 export。
.EXAMPLE
    .\dev.ps1                        # 免打包全栈启动（设计分支环境，前台运行）
    .\dev.ps1 -EnvOnly               # 只打印将要注入的环境与自检结果，不启动
    .\dev.ps1 -Stop                  # 停止 dev 启动的全部进程（BCC 走 /quit 优雅退出）
#     注：-Stop 是「清孤儿」用的兜底强停。它会把正在前台跑的 tauri dev 一并终止，
#     于是那个窗口会打印 npm error / exit code 0xffffffff —— 那是被停止的正常签名，
#     不是 dev 坏了。日常收工请在 dev.ps1 窗口按 Ctrl+C（或关掉 Tauri 窗口）。
    .\dev.ps1 -AppRoot C:\temp\flowcap_test   # 切到主分支环境（显式指定，禁止自动探测）
#>
[CmdletBinding()]
param(
    [string]$AppRoot = "C:\temp\flowcap_design",
    [switch]$EnvOnly,
    [switch]$Stop,
    [switch]$NoVersionCheck
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { chcp 65001 > $null } catch {}

$ROOT         = Split-Path -Parent $MyInvocation.MyCommand.Path
$TAURI_DIR    = Join-Path $ROOT "src-tauri"
$FRONTEND_DIR = Join-Path $ROOT "frontend"
$CARGO_BIN    = "C:\Users\LOX\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
$BACKEND_PORT = 8000
$FRONTEND_PORT= 1420

function Write-Step($n, $msg) { Write-Host "[$n] $msg" -ForegroundColor Yellow }
function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "  [ERROR] $msg" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# 进程清理（-Stop 与启动结束的 finally 共用）
#
# 为什么需要"按名字兜底"而不是只杀自己记录的 handle：
#   Rust 侧 on_window_event 用 taskkill /F /T 强杀后端 → Python 的 atexit /
#   lifespan 优雅清理被跳过 → 后端自 spawn 的 BCC/recv（daemon_launcher
#   ._spawn_sidecar 起的，**不在** Rust 的 AppState.daemons 台账里）残留成孤儿，
#   占住 8000/11231/账号端口 → 二次启动端口争用卡死。
#   进程名是唯一能覆盖全部形态的判据，故以名字为准。
# ---------------------------------------------------------------------------
function Stop-DyAll {
    param([switch]$Quiet)

    # ① BCC / recv 守护：优先 /quit 优雅退出（铁律：绝不强杀浏览器）
    $graceful = 0
    foreach ($nm in @('flowcap-browser-daemon', 'flowcap-recv-daemon')) {
        foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                         Where-Object { $_.Name -like "$nm*" })) {
            $port = $null
            if ($p.CommandLine -match "--port\s+(\d+)") { $port = $Matches[1] }
            if ($port) {
                try {
                    Invoke-WebRequest "http://127.0.0.1:$port/quit" -Method POST `
                        -UseBasicParsing -TimeoutSec 5 | Out-Null
                    $graceful++
                } catch { }
            }
        }
    }
    if (-not $Quiet) {
        if ($graceful -gt 0) { Write-OK "$graceful 个守护已 /quit 优雅退出" }
        else { Write-Host "   （无守护需优雅退出）" -ForegroundColor DarkGray }
    }
    Start-Sleep -Seconds 2

    # ② 残留强清：3 份 sidecar + dev 主程序 + 编排进程（npx/tauri-cli/vite/cargo）
    #    + 本项目 camoufox（按命令行含本数据根精准匹配，绝不误杀用户的正常浏览器）。
    #
    #    为什么要连编排进程一起清（实测）：关掉 Tauri 窗口后，Rust 壳会退出，
    #    但 `npx tauri dev` 的 CLI 父进程**有时并不随之退出**（只报告子进程结束），
    #    于是 dev.ps1 的 finally 悬在那里不返回 → 表面看"窗口关了"，实际
    #    node/cargo 仍占着 1420 端口与 target\debug → 二次启动卡死。
    #    故必须按名字把它们一并收干净，不能只依赖 finally。
    $killed = 0
    $targets = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like 'flowcap-backend*' -or
        $_.Name -like 'flowcap-browser-daemon*' -or
        $_.Name -like 'flowcap-recv-daemon*' -or
        $_.Name -like 'flowcap*' -or
        # 本项目相关的 node（vite / tauri-cli / npm run dev），按命令行精准匹配
        # 2026-10-06 去壳：仓库根即项目根，改按仓库目录名匹配（原 *FlowCap*）
        ($_.Name -eq 'node.exe' -and $_.CommandLine -like "*DYchajian*") -or
        # cargo / rustc 编译期进程（只在本项目目录下运行）
        (($_.Name -eq 'cargo.exe' -or $_.Name -eq 'rustc.exe') -and
         $_.CommandLine -like "*DYchajian*") -or
        ($_.Name -like 'camoufox*' -and $_.CommandLine -like "*$AppRoot*")
    })
    foreach ($p in $targets) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
    Start-Sleep -Seconds 1

    # ③ 复核：端口必须释放（否则二次启动会卡死）
    $busy = @()
    foreach ($pt in @($BACKEND_PORT, $FRONTEND_PORT)) {
        if (Test-NetConnection -ComputerName 127.0.0.1 -Port $pt -InformationLevel Quiet `
                -WarningAction SilentlyContinue) { $busy += $pt }
    }
    if (-not $Quiet) {
        Write-OK "已清理 $killed 个残留进程"
        if ($busy.Count -eq 0) { Write-OK "端口已释放（$BACKEND_PORT / $FRONTEND_PORT 均空闲）" }
        else { Write-Warn2 "端口仍被占用: $($busy -join ', ')" }
    }
    return ($busy.Count -eq 0)
}

# ---------------------------------------------------------------------------
# 环境解析（显式配置原则：只认 -AppRoot，绝不探测本机状态自行决定）
# ---------------------------------------------------------------------------
function Get-MemberEnv([string]$root) {
    $sf = Join-Path $root "members\.session.json"
    if (-not (Test-Path $sf)) { return $null }
    try {
        $j = Get-Content $sf -Raw -Encoding UTF8 | ConvertFrom-Json
        return @{ id = [string]$j.member_id; key = [string]$j.master_key }
    } catch {
        Write-Warn2 "members\.session.json 解析失败: $($_.Exception.Message)"
        return $null
    }
}

$env:FLOWCAP_APP_ROOT = $AppRoot
# ---------------------------------------------------------------------------
# 编码固定（2026-09-20 修「dev 日志中文乱码」）
#   现象：run_*.log 文件本身是**正确 UTF-8**（utf-8 可解码、gbk 解出「鍚庣」乱码），
#         但控制台/dev 日志显示乱码。
#   根因：Python 3.15+ 起 stdout 默认编码不再是 UTF-8 而是**区域编码**（本机 GBK），
#         子进程把 UTF-8 文本按 GBK 写出 → 显示端乱码。
#   修法：显式钉死 Python I/O 为 UTF-8。子进程继承环境变量（daemon_launcher
#         只透传 DY_*/PYTHONPATH 等白名单，故这里加到白名单键上见 build 侧；
#         但 DY_* 全部透传的路径下本行足以覆盖 sidecar 本体）。
# ---------------------------------------------------------------------------
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONLEGACYWINDOWSSTDIO = "0"
$mem = Get-MemberEnv $AppRoot
if ($mem -and $mem.id) {
    $env:DY_MEMBER = $mem.id
    if ($mem.key) { $env:DY_MEMBER_KEY = $mem.key }
}

if (-not (Test-Path $AppRoot)) {
    Write-Err2 "环境根不存在: $AppRoot"
    Write-Host "   本分支环境应为 C:\temp\flowcap_design；如要建新环境请显式给出 -AppRoot。" -ForegroundColor DarkGray
    exit 1
}

# ---------------------------------------------------------------------------
# -Stop：停止 dev 启动的进程（BCC 先走 /quit 优雅退出）
# ---------------------------------------------------------------------------
if ($Stop) {
    Write-Host "`n=== 停止 dev 进程 ===`n" -ForegroundColor Cyan
    [void](Stop-DyAll)
    Write-Host ""
    exit 0
}

# ---------------------------------------------------------------------------
# 启动前自检
# ---------------------------------------------------------------------------
Write-Host "`n=== FlowCap v2 免打包开发启动（tauri dev）===" -ForegroundColor Cyan
Write-Host "  环境根   : $AppRoot" -ForegroundColor White
Write-Host "  会员态   : $(if ($mem -and $mem.id) { $mem.id } else { '(未读到 members/.session.json)' })" -ForegroundColor White
Write-Host ""

Write-Step "1/6" "检查本机是否已有实例在跑（防端口/ profile 抢占）..."
$conflicts = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'flowcap*' -or
                   ($_.Name -eq 'node.exe' -and $_.CommandLine -like "*vite*" -and $_.CommandLine -like "*DYchajian*") }
if ($conflicts) {
    Write-Warn2 "发现 $(@($conflicts).Count) 个疑似在跑的进程，可能是上一次 dev 未收干净："
    foreach ($c in @($conflicts)) { Write-Host "        $($c.Name) pid=$($c.ProcessId)" -ForegroundColor DarkGray }
    Write-Host "        （如确认要重开，先跑： .\dev.ps1 -Stop ）" -ForegroundColor DarkGray
} else {
    Write-OK "无残留实例"
}

Write-Step "2/6" "检查端口 $FRONTEND_PORT / $BACKEND_PORT ..."
$pF = Test-NetConnection -ComputerName 127.0.0.1 -Port $FRONTEND_PORT -InformationLevel Quiet -WarningAction SilentlyContinue
$pB = Test-NetConnection -ComputerName 127.0.0.1 -Port $BACKEND_PORT  -InformationLevel Quiet -WarningAction SilentlyContinue
if ($pF) { Write-Warn2 "前端端口 $FRONTEND_PORT 已被占用（vite strictPort=true 会直接失败）" } else { Write-OK "前端端口 $FRONTEND_PORT 空闲" }
if ($pB) { Write-Warn2 "后端端口 $BACKEND_PORT 已被占用（sidecar 会起不来）" }    else { Write-OK "后端端口 $BACKEND_PORT 空闲" }

Write-Step "3/6" "检查 Rust 侧前置（cargo / node_modules / sidecar 产物）..."
if (Test-Path (Join-Path $CARGO_BIN "cargo.exe")) { Write-OK "cargo: $CARGO_BIN" } else { Write-Warn2 "未找到 cargo：$CARGO_BIN" }
if (Test-Path (Join-Path $FRONTEND_DIR "node_modules")) { Write-OK "frontend/node_modules 已安装" } else { Write-Warn2 "缺 frontend/node_modules，请先 npm install" }
$sdc = Get-ChildItem (Join-Path $TAURI_DIR "binaries") -Filter "flowcap-*-x86_64-pc-windows-msvc.exe" -ErrorAction SilentlyContinue
if ($sdc) { Write-OK "sidecar 产物 $($sdc.Count) 份（含 _internal 时必须为 onedir 目录形态）" } else { Write-Warn2 "src-tauri/binaries 下无 sidecar，请先跑 scripts\build_sidecar.py --onedir" }

Write-Step "4/6" "确保 target\debug\binaries junction（tauri dev 不带 _internal 的唯一坑）..."
$jlink  = Join-Path $TAURI_DIR "target\debug\binaries"
$jtarget= Join-Path $TAURI_DIR "binaries"
if (Test-Path $jlink) {
    Write-OK "junction 已存在"
} else {
    $dbg = Join-Path $TAURI_DIR "target\debug"
    if (-not (Test-Path $dbg)) { New-Item -ItemType Directory -Path $dbg -Force | Out-Null }
    New-Item -ItemType Junction -Path $jlink -Target $jtarget | Out-Null
    Write-OK "junction 已建立: $jlink -> $jtarget"
}

Write-Step "5/6" "版本一致性预检（不一致会被前端阻断式门禁全屏遮罩）..."
if ($NoVersionCheck) {
    Write-Warn2 "已跳过（-NoVersionCheck）"
} else {
    $feVer = $null
    $fj = Join-Path $FRONTEND_DIR "package.json"
    if (Test-Path $fj) { try { $feVer = [string](Get-Content $fj -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch {} }
    $beVer = $null
    $bv = Join-Path $ROOT "backend\_build_version.py"
    if (Test-Path $bv) {
        $m = Select-String -Path $bv -Pattern 'BUILD_VERSION\s*=\s*"([^"]+)"' -ErrorAction SilentlyContinue
        if ($m) { $beVer = $m.Matches[0].Groups[1].Value }
    }
    if ($feVer -and $beVer -and $feVer -eq $beVer) {
        Write-OK "前端 $feVer == sidecar $beVer"
    } else {
        Write-Warn2 "版本不一致：前端=$feVer / sidecar=$beVer"
        Write-Host "       前端 dev server 报的是 frontend/package.json，后端报的是已构建 sidecar 内嵌常量。" -ForegroundColor DarkGray
        Write-Host "       免打包调试**不需要**重打包；若确实不一致，把 frontend/package.json 对齐 sidecar 即可，否则应用启动会被版本门禁挡住。" -ForegroundColor DarkGray
    }
}

Write-Step "6/6" "准备启动..."
# ---------------------------------------------------------------------------
# 6.5/6 源码树污染自检（铁律：源码目录不得产生 data/ accounts/ 未展开变量目录）
#   实测事故：tauri dev 期间 Windows 字体缓存工具在 src-tauri/ 下创建了字面量目录
#   `%SystemDrive%/ProgramData/...`（环境变量未展开被当路径），且空 data/ accounts/
#   也出现过。加机械门禁，避免"下次又悄悄长出来"。
# ---------------------------------------------------------------------------
$POLLUTION = @(
    (Join-Path $ROOT "data"),
    (Join-Path $ROOT "accounts"),
    (Join-Path $TAURI_DIR "data"),
    (Join-Path $TAURI_DIR "accounts"),
    (Join-Path $TAURI_DIR "%SystemDrive%"),
    (Join-Path $TAURI_DIR "%ProgramData%"),
    (Join-Path $TAURI_DIR "%SystemRoot%")
)
$dirty = @($POLLUTION | Where-Object { Test-Path $_ })
if ($dirty.Count -gt 0) {
    Write-Warn2 "源码树出现疑似运行时污染目录（铁律：源码目录不得产生 data/accounts/）："
    foreach ($d in $dirty) {
        $n = @(Get-ChildItem $d -Recurse -File -ErrorAction SilentlyContinue).Count
        Write-Host "        $d  ($n 文件)" -ForegroundColor DarkYellow
    }
    Write-Host "        如为空目录可直接删；有内容先查引用。gen/ 与 logs/ 属正常。" -ForegroundColor DarkGray
} else {
    Write-OK "源码树无污染目录"
}

$env:PATH = "$CARGO_BIN;$env:PATH"
Write-Host ""
Write-Host "  注入环境: FLOWCAP_APP_ROOT=$($env:FLOWCAP_APP_ROOT)" -ForegroundColor DarkGray
if ($env:DY_MEMBER) { Write-Host "            DY_MEMBER=$($env:DY_MEMBER)（DY_MEMBER_KEY 已设，不打印）" -ForegroundColor DarkGray }
Write-Host "  停止方式: Ctrl+C（或另开窗口 .\dev.ps1 -Stop）" -ForegroundColor DarkGray
Write-Host ""

if ($EnvOnly) {
    Write-Host "（-EnvOnly：仅自检，未启动）`n" -ForegroundColor Cyan
    exit 0
}

# ---------------------------------------------------------------------------
# 启动 tauri dev（前台运行，便于看 HMR 与 sidecar 日志）
#
# ★ 关键：dev 模式下 Rust 的 on_window_event 用 taskkill /F 强杀后端
#   → Python atexit/lifespan 清理被跳过 → 后端 spawn 的 BCC/recv 变孤儿，
#   占住 8000/11231/账号端口 → 二次启动卡死。
#   故无论正常退出（关窗口/Ctrl+C）还是异常退出，finally 一律兜底清理干净。
# ---------------------------------------------------------------------------
Push-Location $TAURI_DIR
try {
    npx tauri dev
} finally {
    Pop-Location
    Write-Host "`n=== tauri dev 已结束，正在清理残留进程（防二次启动卡死）===" -ForegroundColor Cyan
    $clean = Stop-DyAll
    if ($clean) {
        Write-Host "`n  可以再次运行 .\dev.ps1 启动（端口已释放）。`n" -ForegroundColor Green
    } else {
        Write-Warn2 "仍有端口被占用，二次启动可能卡死；请检查上方告警。"
        Write-Host "        （手动重试： .\dev.ps1 -Stop ）" -ForegroundColor DarkGray
    }
}
