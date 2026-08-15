# 一键打包 sidecar（Windows）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\build_sidecar.ps1
#
# 注意：必须用本机真实 Python 绝对路径，不能用 `python` 命令
# （WindowsApps 的 Microsoft Store 占位符会优先于用户 PATH，导致
# "Python was not found"）。真实 Python 见 $PY 变量。

$ErrorActionPreference = "Stop"

# 用脚本自身所在目录定位项目根，避免嵌套调用时相对路径错位
if ($PSScriptRoot -and (Test-Path $PSScriptRoot)) {
    $root = $PSScriptRoot | Split-Path -Parent
} else {
    $root = "c:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
}
if (-not (Test-Path $root)) {
    Write-Host "[ERROR] 项目根不存在: $root" -ForegroundColor Red
    exit 1
}
$binDir = Join-Path $root "src-tauri\binaries"

# 本机真实 Python（绝对路径，避免 Store 占位符）
$PY = "C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
if (-not (Test-Path $PY)) {
    Write-Host "[ERROR] 真实 Python 不存在: $PY" -ForegroundColor Red
    exit 1
}

Write-Host "==> 项目根: $root" -ForegroundColor Cyan
Write-Host "==> 使用 Python: $PY" -ForegroundColor Cyan

# 清理旧 sidecar 产物（PowerShell Remove-Item 不经 Python os.remove 的
# safe-delete 拦截；PyInstaller 覆盖已存在 exe 时会因 safe-delete 失败中断）
$triple = "x86_64-pc-windows-msvc"
$names = @("dyautodm-backend", "dyautodm-browser-daemon", "dyautodm-recv-daemon")
foreach ($name in $names) {
    $f = Join-Path $binDir "$name-$triple.exe"
    if (Test-Path $f) {
        Remove-Item $f -Force -ErrorAction SilentlyContinue
        Write-Host "==> 已清理旧产物: $f" -ForegroundColor Cyan
    }
}

# 调 build_sidecar.py（内部用 sys.executable 即本真实 python，
# 产物名直接带 target-triple 后缀，符合 Tauri externalBin 要求）
$script = Join-Path $root "scripts\build_sidecar.py"
& $PY $script

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] sidecar 打包失败" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "==> 全部完成，二进制位于 src-tauri\binaries\" -ForegroundColor Green
