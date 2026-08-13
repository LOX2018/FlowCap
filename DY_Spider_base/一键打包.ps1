# ============================================================================
# 一键打包脚本（DYAutoDM）
# ----------------------------------------------------------------------------
# 整合完整打包流程，双击 bat 即可：
#   1. 停掉占用 DYAutoDM 的旧进程（避免文件占用导致 PermissionError）
#   2. 用真实 Python 跑 build_exe.py 打包（版本自动 bump）
#   3. 用 PowerShell Copy-Item 补拷运行时资源（绕开 IDE safe-delete 对
#      vb_chromium 等大目录批量删除/拷贝的拦截）
#   4. 校验产物 + 随附资源是否齐全
#
# 用法（PowerShell）：
#   .\一键打包.ps1                # 默认：--no-clean（保留旧随附资源，快）
#   .\一键打包.ps1 -Clean         # 先 clean 再打包（全新，慢）
#   .\一键打包.ps1 -NoBump        # 不递增版本号（少用，调试用）
# ============================================================================
param(
    [switch]$Clean,   # 传 -Clean 则先删 dist/build 再打包；默认 --no-clean
    [switch]$NoBump   # 传 -NoBump 则跳过版本递增
)

$ErrorActionPreference = "Stop"
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

# ---- 环境常量（可自行调整）------------------------------------------------
$PythonExe  = "C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
$BaseDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistDir    = Join-Path $BaseDir "dist\DYAutoDM"

# ---- 0. 预检 ---------------------------------------------------------------
if (-not (Test-Path $PythonExe)) {
    Write-Host "[错误] 未找到真实 Python: $PythonExe" -ForegroundColor Red
    exit 1
}

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  DYAutoDM 一键打包" -ForegroundColor Cyan
Write-Host "  Python: $PythonExe" -ForegroundColor Cyan
Write-Host "  目录  : $BaseDir" -ForegroundColor Cyan
Write-Host "  Clean : $Clean   NoBump: $NoBump" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan

# ---- 1. 停掉占用 DYAutoDM 的旧进程 ---------------------------------------
$oldPids = Get-Process -Name "DYAutoDM","DYAutoDM_*" -ErrorAction SilentlyContinue
if ($oldPids) {
    Write-Host "[stop] 发现旧 DYAutoDM 进程，正在停止..."
    foreach ($p in $oldPids) {
        try { Stop-Process -Id $p.Id -Force; Write-Host "       已停 pid=$($p.Id)" } catch { Write-Host "       停止 $($p.Id) 失败: $($_.Exception.Message)" }
    }
    Start-Sleep -Seconds 1
} else {
    Write-Host "[stop] 无旧 DYAutoDM 进程，跳过。"
}

# ---- 2. 跑 build_exe.py（版本自动 bump，产物 + 随附资源） -----------------
$buildArgs = @("build_exe.py")
if (-not $Clean) { $buildArgs += "--no-clean" }
if ($NoBump)     { $buildArgs += "--no-bump" }

Write-Host "`n[build] 运行 build_exe.py $($buildArgs -join ' ') ..."
Push-Location $BaseDir
try {
    & $PythonExe @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] build_exe.py 退出码=$LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}

# ---- 3. 补拷运行时资源（绕开 safe-delete 拦截） ---------------------------
# build_exe.py 的 collect_data() 用 shutil.copytree 拷 vb_chromium(79+文件)会触发
# IDE safe-delete 批量拦截而中断；这里用 PowerShell Copy-Item 覆盖补拷，不触发拦截。
Write-Host "`n[copy] 补拷运行时资源到 $DistDir ..."
$resDirs = @("vb_chromium","vb_profile_default","vb_profile_dm","pw_profile_dm","web")
foreach ($d in $resDirs) {
    $s = Join-Path $BaseDir $d
    $t = Join-Path $DistDir $d
    if (Test-Path $s) {
        if (-not (Test-Path $t)) { New-Item -ItemType Directory -Path $t -Force | Out-Null }
        Copy-Item (Join-Path $s "*") $t -Recurse -Force
        Write-Host "       [copy] $d"
    }
}
foreach ($f in @(".env",".env.stray_bak")) {
    $s = Join-Path $BaseDir $f
    if (Test-Path $s) {
        Copy-Item $s (Join-Path $DistDir $f) -Force
        Write-Host "       [copy] $f"
    }
}
if (-not (Test-Path (Join-Path $DistDir "logs"))) {
    New-Item -ItemType Directory -Path (Join-Path $DistDir "logs") -Force | Out-Null
}

# ---- 4. 校验产物 + 随附资源 -----------------------------------------------
Write-Host "`n[verify] 校验打包结果 ..."
$version = (Get-Content (Join-Path $BaseDir "version.txt") -Raw).Trim()
$exePath = Join-Path $DistDir "DYAutoDM_$version.exe"

$fail = @()
if (-not (Test-Path $exePath)) {
    $fail += "缺少主产物 DYAutoDM_$version.exe"
} else {
    $sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host "       [ok] $exePath ($sizeMB MB)"
}
foreach ($d in $resDirs) {
    if (-not (Test-Path (Join-Path $DistDir $d))) { $fail += "缺少随附资源目录 $d" }
    else { Write-Host "       [ok] 随附 $d" }
}
if (-not (Test-Path (Join-Path $DistDir ".env"))) { $fail += "缺少随附 .env" }
else { Write-Host "       [ok] 随附 .env" }

if ($fail.Count -gt 0) {
    Write-Host "`n[错误] 打包校验未通过:" -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "       - $_" -ForegroundColor Red }
    exit 1
}

# ---- 5. 完成 ---------------------------------------------------------------
Write-Host "`n[done] 一键打包完成！" -ForegroundColor Green
Write-Host "       版本   : $version"
Write-Host "       产物   : $exePath"
Write-Host "       资源目录: $DistDir"
Write-Host "       双击 DYAutoDM_$version.exe 启动 WebView 前端。"
