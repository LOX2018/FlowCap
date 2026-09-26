# coding=utf-8
"""内核真值档案（Kernel Truth Profile）—— ADR-016 D3-A。

## 为什么需要它（★ 2026-09-26 实测根因）

修复前的**两层脱节**实测事实：

```
HTTP 层（utils/fingerprint）：项目自造的 GEO_PRESETS / GPU_PRESETS / _CORES_POOL
                              —— 5 屏幕 × 4 GPU × 11 核数，且**独立随机**
Camoufox 内核：               BrowserForge 自动生成（上游维护、组合自洽、
                              自带 clamp_screen_to_display 修正越界）
                              —— 项目**完全没有使用档案值**（启动只传 os="windows"）
```

实测症状（同一账号、同一时刻）：

| 字段 | 档案（HTTP 层） | 浏览器真值（JS 层） |
|---|---|---|
| screen | `1280x720` | `2560x1440` / `5120x1440`（随会话变） |
| cores | `12` | `8` |
| GPU | 随机预设（RTX 4070 等） | `GTX 980`（真实） |

## 设计决策（D3-A：**档案从内核读真值**）

**方向**：让 HTTP 层**跟随**内核，而不是把项目预设注入内核。

**理由**（两条硬依据）：
1. Camoufox 官方警告（camoufox.com/python/usage.md）：
   > Do NOT randomly assign values to these properties. WAFs hash your WebGL
   > fingerprint and compare it against a dataset. **Randomly assigning values
   > will lead to detection as an unknown device.**
   ⇒ 项目自造预设正是"randomly assign"，而 BrowserForge 是**受支持的数据集**。
2. BrowserForge 的组合数是项目预设的**数百倍**（5×4×11=220 vs 数千），
   多账号场景下项目预设会**大量撞车**。

## 数据流

```
BCC 启动 / 保活心跳
  → env_audit_snapshot()（现有探针，已能取回 screen/cores/webgl/platform/timezone/UA）
  → record_kernel_truth(account, js_view)
  → kv_store 持久化（复用项目既有 KV，不新造存储层）
                             ↓
utils/fingerprint.fingerprint_profile()
  → get_kernel_truth(account)：**有真值则用真值**；无则降级到预设（并记录）
```

## 降级纪律（重要）

真值**可能读不到**（BCC 未启动 / 页面未就绪 / 首次冷启）。此时：

- **不得**编造真值；
- **不得**静默使用预设而不留痕（那会让"档案=真值"的假设悄悄失效）；
- **必须**记录降级事件，并让 `check_fingerprint_consistency.py` 能据此告警。

⇒ `get_kernel_truth()` 返回 `(truth | None, stale_reason)`，调用方据此决定。
"""
from __future__ import annotations

import time
from typing import Any

_KEY = "kernel_truth:{account}"

# 真值新鲜度上限：超过则视为过期（内核指纹可能已被 BrowserForge 重新生成）。
# 取值依据：Camoufox 每次 `Camoufox(...)` 启动会生成一份新指纹；
# 保活心跳周期 300s（daemon/bcc_login.py），故 30 分钟足够覆盖正常重启窗口。
_MAX_AGE_SEC = 1800


def _key(account: str) -> str:
    return _KEY.format(account=account or "default")


def record_kernel_truth(account: str, js_view: dict) -> dict | None:
    """把 BCC 探针取回的**浏览器真值**写入 KV。返回写入的记录（失败返回 None）。

    `js_view` 即 `services.env_audit.ENV_AUDIT_JS` 的返回结构。
    """
    if not isinstance(js_view, dict) or not js_view.get("ok", True):
        return None
    scr = js_view.get("screen") or {}
    webgl = js_view.get("webgl") or {}
    rec = {
        "ts": time.time(),
        "userAgent": js_view.get("userAgent") or "",
        "platform": js_view.get("platform") or "",
        "hardwareConcurrency": js_view.get("hardwareConcurrency") or 0,
        "deviceMemory": js_view.get("deviceMemory"),
        "timezone": js_view.get("timezone") or "",
        "screen": {
            "w": scr.get("w"), "h": scr.get("h"),
            "aw": scr.get("aw"), "ah": scr.get("ah"),
            "outerW": scr.get("outerW"), "outerH": scr.get("outerH"),
            "innerW": scr.get("innerW"), "innerH": scr.get("innerH"),
            "cd": scr.get("cd"),
        },
        "webgl": {
            "vendor": webgl.get("vendor") or "",
            "renderer": webgl.get("renderer") or "",
        },
    }
    # 必须至少拿到 UA 与屏幕宽高才算有效真值
    if not rec["userAgent"] or not rec["screen"].get("w"):
        return None
    try:
        from services.kv_store import kv_set
        kv_set(_key(account), rec)
        return rec
    except Exception:
        return None


def get_kernel_truth(account: str) -> tuple[dict | None, str]:
    """取该账号的内核真值。返回 `(rec | None, reason)`。

    `rec` 为 None 时，`reason` 说明原因（供降级告警与门禁使用）：
      · `"no_record"`    从未记录过（BCC 未跑过 / 冷启首次）
      · `"stale"`        记录过期（> _MAX_AGE_SEC，内核指纹可能已重新生成）
      · `"kv_error"`     KV 读取异常
    """
    try:
        from services.kv_store import kv_get
        rec = kv_get(_key(account), None)
    except Exception:
        return None, "kv_error"
    if not isinstance(rec, dict):
        return None, "no_record"
    ts = float(rec.get("ts") or 0)
    if not ts or (time.time() - ts) > _MAX_AGE_SEC:
        return None, "stale"
    return rec, ""


def clear_kernel_truth(account: str) -> None:
    """清除该账号真值（重扫/换内核后调用，避免用旧内核的值）。"""
    try:
        from services.kv_store import kv_set
        kv_set(_key(account), None)
    except Exception:
        pass
