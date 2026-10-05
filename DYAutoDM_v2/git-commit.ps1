# 通用 git 提交脚本 —— 彻底规避 PowerShell 中文转义问题
#
# 关键：本文件必须是 UTF-8 含 BOM 编码，PowerShell 5.1 才能正确读取。
# commit message 也绝不通过命令行传入，统一从 _commit_msg.txt 读取。
#
# 用法（在 DYAutoDM_v2 目录执行）：
#   1) 把提交说明写进 _commit_msg.txt（UTF-8 含 BOM）
#   2) .\git-commit.ps1            # 提交全部变更
#      .\git-commit.ps1 f1 f2      # 只提交指定文件

param(
    [Parameter(Mandatory=$false, ValueFromRemainingArguments=$true)]
    [string[]]$Paths
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$msgFile = Join-Path $scriptDir "_commit_msg.txt"
if (-not (Test-Path $msgFile)) {
    Write-Error "missing _commit_msg.txt"
    exit 1
}

$bytes = [System.IO.File]::ReadAllBytes($msgFile)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $tmpMsg = $msgFile
} else {
    $content = [System.IO.File]::ReadAllText($msgFile)
    $tmpMsg = Join-Path $env:TEMP ("gitmsg_" + [guid]::NewGuid().ToString("N") + ".txt")
    [System.IO.File]::WriteAllText($tmpMsg, $content, (New-Object System.Text.UTF8Encoding($true)))
}

try {
    if ($Paths -and $Paths.Count -gt 0) {
        git add @Paths
    } else {
        git add -A
    }
    git commit -F $tmpMsg
    Write-Host "[ok] commit done"
}
finally {
    if ($tmpMsg -ne $msgFile) {
        Remove-Item $tmpMsg -Force -ErrorAction SilentlyContinue
    }
}
