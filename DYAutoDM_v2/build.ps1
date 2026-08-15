<#
.SYNOPSIS
    DYAutoDM v2 一键打包脚本（Windows）
.DESCRIPTION
    1. 从 package.json 读取版本号
    2. 重新打包 3 个 Python sidecar（含指纹内核随附资源）
    3. 运行 npx tauri build（Rust 全量编译 + 安装包）
    4. 将主程序复制为 dist/DYAutoDM_v2_<version>.exe（带版本号尾椎）
       并复制 3 个带 triple 的 sidecar 到 dist/binaries/
.EXAMPLE
    .\build.ps1
#>
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

# 1) 读取版本号（UTF-8 读取，避免中文 description 导致 ConvertFrom-Json 失败）
$pkgText = Get-Content -Raw -Path "$ROOT\package.json" -Encoding UTF8
$pkg = $pkgText | ConvertFrom-Json
$VERSION = $pkg.version
Write-Host "==> 当前版本: $VERSION" -ForegroundColor Cyan

# 2) 重打包 sidecar
Write-Host "==> 重新打包 Python sidecar..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File "$ROOT\scripts\build_sidecar.ps1"

# 3) tauri build
Write-Host "==> 编译 Tauri 主程序 + 安装包..." -ForegroundColor Cyan
$env:PATH = "C:\Users\LOX\.cargo\bin;" + $env:PATH
Start-Process cmd -ArgumentList "/c", "npx tauri build > build_tauri.log 2>&1" -Wait -NoNewWindow

# 4) 复制主程序（带版本号尾椎）+ sidecars
$SRC_EXE = "$ROOT\src-tauri\target\release\dyautodm-v2.exe"
$DIST_DIR = "$ROOT\dist"
$BIN_DIR = "$DIST_DIR\binaries"
if (-not (Test-Path $DIST_DIR)) { New-Item -ItemType Directory -Path $DIST_DIR | Out-Null }
if (-not (Test-Path $BIN_DIR)) { New-Item -ItemType Directory -Path $BIN_DIR | Out-Null }

$DST_EXE = "$DIST_DIR\DYAutoDM_v2_$VERSION.exe"
if (Test-Path $SRC_EXE) {
    Copy-Item $SRC_EXE $DST_EXE -Force
    Write-Host "==> 主程序: $DST_EXE" -ForegroundColor Green
} else {
    Write-Host "[ERROR] 未找到编译产物: $SRC_EXE" -ForegroundColor Red
    exit 1
}

$triple = "x86_64-pc-windows-msvc"
$sidecars = @("dyautodm-backend", "dyautodm-browser-daemon", "dyautodm-recv-daemon")
foreach ($s in $sidecars) {
    $src = "$ROOT\src-tauri\binaries\$s-$triple.exe"
    $dst = "$BIN_DIR\$s-$triple.exe"
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
        Write-Host "==> sidecar: $dst" -ForegroundColor Green
    } else {
        Write-Host "[WARN] sidecar not found: $src" -ForegroundColor Yellow
    }
}

# 5) deploy to C:\temp\dyautodm_test\ per user standard:
#    main exe renamed to fixed dyautodm-v2.exe (no version suffix)
#    binaries\ 3 sidecars with triple suffix
$TEST_DIR = "C:\temp\dyautodm_test"
$TEST_BIN = "$TEST_DIR\binaries"
if (-not (Test-Path $TEST_DIR)) { New-Item -ItemType Directory -Path $TEST_DIR | Out-Null }
if (-not (Test-Path $TEST_BIN)) { New-Item -ItemType Directory -Path $TEST_BIN | Out-Null }

$TEST_MAIN = "$TEST_DIR\dyautodm-v2.exe"
if (Test-Path $SRC_EXE) {
    Copy-Item $SRC_EXE $TEST_MAIN -Force
    Write-Host "==> test deploy main: $TEST_MAIN" -ForegroundColor Green
} else {
    Write-Host "[ERROR] main exe missing, cannot deploy" -ForegroundColor Red
    exit 1
}
foreach ($s in $sidecars) {
    $src = "$ROOT\src-tauri\binaries\$s-$triple.exe"
    $dst = "$TEST_BIN\$s-$triple.exe"
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
        Write-Host "==> test deploy sidecar: $dst" -ForegroundColor Green
    } else {
        Write-Host "[WARN] sidecar not found for deploy: $src" -ForegroundColor Yellow
    }
}

Write-Host "`n=== Build done ===" -ForegroundColor Green
Write-Host "  dist (archived with version):  $DST_EXE" -ForegroundColor Cyan
Write-Host "  test deploy (fixed name):      $TEST_MAIN" -ForegroundColor Cyan
Write-Host "  test sidecars:                 $TEST_BIN" -ForegroundColor Cyan
