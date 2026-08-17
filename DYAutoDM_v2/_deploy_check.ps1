$ErrorActionPreference = 'SilentlyContinue'
$dirs = @(
    'C:\temp\dyautodm_test',
    'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\dist',
    'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\src-tauri\target\release',
    'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\src-tauri\target\release\bundle\nsis',
    'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\src-tauri\target\release\bundle\msi'
)
$out = @()
foreach ($d in $dirs) {
    if (Test-Path $d) {
        $out += "=== $d ==="
        Get-ChildItem $d -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'dyautodm|DYAutoDM|\.exe$|\.msi$' } | ForEach-Object {
            $out += ("{0,-60} {1,12} {2}" -f $_.Name, $_.Length, $_.LastWriteTime.ToString('yyyy/MM/dd HH:mm:ss'))
        }
    } else {
        $out += "=== $d (NOT EXISTS) ==="
    }
}
Set-Content -Path (Join-Path $PSScriptRoot '_deploy_check.txt') -Value ($out -join "`r`n") -Encoding UTF8
Write-Host "done"
