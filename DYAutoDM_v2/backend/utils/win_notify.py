"""Windows 系统通知（Win32 原生 Toast，走 Windows PowerShell 5.1）。

## 为什么存在

凭证失效是最高优先级事件，但既有告警只走 IM 渠道（notify/events.py）——
用户没接手机时完全无感知，等发现时凭证早已反复失效。

2026-10-05 用户明确要求：**凭证失效不自动打开有头指纹浏览器，改用 Windows
通知提醒用户手动处理**。

## 实现选型（全部实测，非猜测）

| 方案 | 实测结论 |
|---|---|
| 系统内置 `toast.exe` | ❌ **本机不存在**（System32 下无此文件） |
| PowerShell 7 (pwsh) | ❌ **无 WinRT** —— .NET Core 已移除 WinRT interop |
| Windows PowerShell **5.1** | ✅ 可用（.NET Framework 4.8 带 WinRT） |
| `CreateToastNotifier(appId, $true)` 双参数 | ❌ 本机**无此重载** |
| `CreateToastNotifier(appId)` 单参数 | ✅ 可用 |
| `mgr.CreateToast($doc)` | ❌ **方法不存在** |
| `New-Object ToastNotification -ArgumentList $doc` | ✅ 可用 |
| AppID `Microsoft.WindowsTerminal_...!App` | ✅ 已注册，实测 NOTIFY_OK |

⇒ 最终实现：powershell 5.1 + WinRT ToastNotificationManager + 单参数重载
   + `New-Object ToastNotification -ArgumentList $doc` + WindowsTerminal AppID。

## 降级链

1. PowerShell 5.1 WinRT Toast（首选）
2. ctypes `MessageBoxW`（兜底，任何 Windows 版本都有，但会阻塞 —— 仅最后手段）

## 🔴 只读约束

本模块**只发通知**，绝不启动浏览器、绝不改凭证。通知是「告诉用户」，
不是「替用户处理」—— 处理权交给用户（见 bcc_login / browser_daemon 的
有头观测态保护）。
"""
from __future__ import annotations

import os
import subprocess
import sys

# WindowsTerminal 是 Win11 自带的已注册 App，无需自建 AppID 注册。
DEFAULT_APP_ID = "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"

_PS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
try {
  $null = [Type]::GetType('Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime')
  $null = [Type]::GetType('Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime')
  $null = [Type]::GetType('Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime')
  function Esc([string]$s) { $s.Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;').Replace('"','&quot;') }
  $xml = '<toast><visual><binding template="ToastGeneric"><text>' + (Esc $Title) + '</text><text>' + (Esc $Message) + '</text></binding></visual></toast>'
  $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
  $doc.LoadXml($xml)
  $mgr = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId)
  $toast = New-Object Windows.UI.Notifications.ToastNotification -ArgumentList $doc
  $mgr.Show($toast)
  Write-Output 'NOTIFY_OK'
} catch {
  Write-Output ('NOTIFY_FAIL: ' + $_.Exception.Message)
  exit 1
}
'''


def _ps_preamble(title: str, message: str, app_id: str) -> str:
    """拼 PowerShell 变量前置段（单引号字符串 + 双写单引号转义）。

    🔴 为什么不用 `param()`：实测 `-Command <script> -Title X` 时 PowerShell
    不会把后续参数绑到脚本的 param 块（$AppId 为空 ⇒ CreateToastNotifier
    报「参数错误 applicationId」）。改为**先赋值变量再执行脚本体**，
    规避该绑定问题（实测修复后 NOTIFY_OK）。
    """
    def q(s: str) -> str:
        return "'" + str(s).replace("'", "''") + "'"
    return (f"$Title = {q(title)}; $Message = {q(message)}; "
            f"$AppId = {q(app_id)}; ")


def _is_windows() -> bool:
    return sys.platform.startswith("win") or os.name == "nt"


def _ps5_exe() -> str:
    """Windows PowerShell 5.1 的绝对路径。

    🔴 必须是 5.1，不能是 pwsh 7 —— 后者无 WinRT（实测：类型加载失败）。
    """
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(
        root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _toast_ps(title: str, message: str, app_id: str) -> bool:
    """PowerShell 5.1 WinRT Toast。"""
    ps = _ps5_exe()
    if not os.path.isfile(ps):
        return False
    try:
        # 🔴 2026-10-05 实测：PowerShell 5.1 输出是**系统 ANSI 码页**（中文环境
        # = GBK），`text=True` 会按 UTF-8 解码并抛 UnicodeDecodeError，导致
        # 「其实通知已发出」却被判 False —— 假阴性。故用 bytes + errors="replace"。
        r = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             _ps_preamble(title, message, app_id) + _PS_SCRIPT],
            capture_output=True, timeout=20,
            creationflags=0x08000000)  # CREATE_NO_WINDOW: 绝不闪控制台窗口
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        return "NOTIFY_OK" in out
    except Exception:  # noqa: BLE001
        return False


def _messagebox(title: str, message: str) -> bool:
    """兜底：原生 MessageBoxW。

    ⚠️ 会阻塞调用线程等待用户点确定 —— 仅在 Toast 全失败时用，
    且调用方应在**非事件循环线程**里调。
    """
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x30)  # MB_ICONWARNING|MB_OK
        return True
    except Exception:  # noqa: BLE001
        return False


def notify_windows(title: str, message: str, *,
                   app_id: str = DEFAULT_APP_ID,
                   fallback_messagebox: bool = False) -> bool:
    """发一条 Windows 系统通知。

    :param title: 标题（≤ 100 字）
    :param message: 正文
    :param app_id: Toast AppID（默认 WindowsTerminal，已注册）
    :param fallback_messagebox: Toast 失败时是否降级到 MessageBoxW（会阻塞）
    :return: 是否成功发出

    🔴 本函数**无状态、不做节流** —— 凭证失效高频触发时**调用方必须自己
       节流**（参考 notify/events.py 的 throttle_sec=600）。
       不节流会把用户通知栏刷爆，与「风控面浪费」同类。
    """
    if not _is_windows():
        return False
    if not title:
        return False
    if _toast_ps(title, message, app_id):
        return True
    if fallback_messagebox:
        return _messagebox(title, message)
    return False
