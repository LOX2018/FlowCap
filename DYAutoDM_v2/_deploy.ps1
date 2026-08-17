$ErrorActionPreference = 'Stop'
$root = 'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2'
$dist = Join-Path $root 'dist'
$test = 'C:\temp\dyautodm_test'
$testBin = Join-Path $test 'binaries'

# 停占用进程
@('dyautodm-v2','DYAutoDM_v2_0.10.0','dyautodm-backend-x86_64-pc-windows-msvc','dyautodm-browser-daemon-x86_64-pc-windows-msvc','dyautodm-recv-daemon-x86_64-pc-windows-msvc') | ForEach-Object {
    Get-Process -Name $_ -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 确保测试目录 bin 存在
if (-not (Test-Path $testBin)) { New-Item -ItemType Directory -Path $testBin | Out-Null }

# 主程序：带版本号尾椎 + 固定名
Copy-Item (Join-Path $dist 'DYAutoDM_v2_0.10.0.exe') (Join-Path $test 'DYAutoDM_v2_0.10.0.exe') -Force
Copy-Item (Join-Path $dist 'DYAutoDM_v2_0.10.0.exe') (Join-Path $test 'dyautodm-v2.exe') -Force

# 3 个 sidecar
$sc = @('dyautodm-backend','dyautodm-browser-daemon','dyautodm-recv-daemon')
foreach ($s in $sc) {
    $name = "$s-x86_64-pc-windows-msvc.exe"
    Copy-Item (Join-Path $dist $name) (Join-Path $testBin $name) -Force
}

Write-Host "DEPLOY_DONE"
Get-ChildItem $test -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '0\.10|dyautodm-v2\.exe' } | ForEach-Object { Write-Host $_.Name $_.Length $_.LastWriteTime }
Get-ChildItem $testBin -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('  ' + $_.Name + ' ' + $_.Length + ' ' + $_.LastWriteTime) }
