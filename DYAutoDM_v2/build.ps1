<#
.SYNOPSIS
    DYAutoDM v2 一键打包脚本（Windows）
.DESCRIPTION
    1. 从 package.json 读取版本号，自动递增 +0.01（次版本 +1，如 0.4.0 -> 0.5.0），
       并写回 4 个版本文件（package.json / frontend/package.json / Cargo.toml / tauri.conf.json）
    2. 重新打包 3 个 Python sidecar（含指纹内核随附资源）
    3. 运行 npx tauri build（Rust 全量编译 + 安装包）
    4. 将主程序复制为 dist/DYAutoDM_v2_<version>.exe（带版本号尾椎）
       并复制 3 个带 triple 的 sidecar 到 dist/binaries/
    5. 部署到 C:\temp\dyautodm_test\，主程序同样命名为 DYAutoDM_v2_<version>.exe
       随附 binaries\ 下 3 个带 triple 的 sidecar
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

# 1.1) 自动递增版本号 +0.01（次版本 +1，如 0.4.0 -> 0.5.0）
function Bump-Version([string]$v) {
    $parts = $v.Split('.')
    if ($parts.Length -ne 3) { return $v }
    $major = [int]$parts[0]; $minor = [int]$parts[1]; $patch = [int]$parts[2]
    $minor += 1
    return "$major.$minor.$patch"
}
$NEW_VERSION = Bump-Version $VERSION
Write-Host "==> 递增版本: $VERSION -> $NEW_VERSION" -ForegroundColor Green

# 1.2) 写回 4 个版本文件
function Set-VersionInFile([string]$path, [string]$pattern, [string]$newVal) {
    if (-not (Test-Path $path)) { Write-Host "[WARN] 版本文件不存在: $path" -ForegroundColor Yellow; return }
    $content = Get-Content -Raw -Path $path -Encoding UTF8
    $content = $content -replace $pattern, "`$1$newVal"
    Set-Content -Path $path -Value $content -Encoding UTF8 -NoNewline
}

# package.json / frontend/package.json: "version": "x.y.z"
Set-VersionInFile "$ROOT\package.json" '("version"\s*:\s*")[^"]+(")' $NEW_VERSION
Set-VersionInFile "$ROOT\frontend\package.json" '("version"\s*:\s*")[^"]+(")' $NEW_VERSION
# Cargo.toml: version = "x.y.z"
Set-VersionInFile "$ROOT\src-tauri\Cargo.toml" '(version\s*=\s*")[^"]+(")' $NEW_VERSION
# tauri.conf.json: "version": "x.y.z"
Set-VersionInFile "$ROOT\src-tauri\tauri.conf.json" '("version"\s*:\s*")[^"]+(")' $NEW_VERSION

$VERSION = $NEW_VERSION
Write-Host "==> 使用新版本: $VERSION" -ForegroundColor Cyan

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
#    main exe named DYAutoDM_v2_<version>.exe (with version suffix, consistent with dist)
#    binaries\ 3 sidecars with triple suffix
$TEST_DIR = "C:\temp\dyautodm_test"
$TEST_BIN = "$TEST_DIR\binaries"
if (-not (Test-Path $TEST_DIR)) { New-Item -ItemType Directory -Path $TEST_DIR | Out-Null }
if (-not (Test-Path $TEST_BIN)) { New-Item -ItemType Directory -Path $TEST_BIN | Out-Null }

$TEST_MAIN = "$TEST_DIR\DYAutoDM_v2_$VERSION.exe"
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
Write-Host "  dist (with version):  $DST_EXE" -ForegroundColor Cyan
Write-Host "  test deploy (with version): $TEST_MAIN" -ForegroundColor Cyan
Write-Host "  test sidecars:                 $TEST_BIN" -ForegroundColor Cyan
