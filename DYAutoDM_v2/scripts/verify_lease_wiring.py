# -*- coding: utf-8 -*-
"""v0.43.11 修复验收：跨调用窗口租约接线（源码级，绝不启动第二个 BCC）。

对应症状（2026-09-14 实机日志）：
  「更新会话」自己请 gate 拿租约 → 5s 后自己的 /capture_userinfo 请求
  被同一把租约判为并发冲突（BCC-047 → BCC-030）→ 昵称 0 个 → 前端只显示数字。

本脚本**不启浏览器、不连 BCC、不碰 profile**，只做静态与桩函数断言：
  A. 租约贯穿：lease_id 从 gate 一路透传到 BCC 端点（4 条链路）
  B. 早退判据：读 DOM 计数器而非已废弃的 hook 计数器
  C. 内部线程豁免：prewarm / 保活回写不参与租约仲裁
  D. 显式失败：拿不到 BCC 不再静默跑出假成功
  E. 释放接线：release_active_lease 存在且被 refresh/finally 调用
  F. 错误码契约：CAP-017 六段齐全

跑法： python scripts/verify_lease_wiring.py
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "backend")
sys.path.insert(0, BK)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail else ""))


def src(rel: str) -> str:
    with io.open(os.path.join(BK, rel), "r", encoding="utf-8") as f:
        return f.read()


def tree(rel: str) -> ast.Module:
    return ast.parse(src(rel))


# ══════════════════════════════════════════════════════════════════
print("=" * 74)
print("A. 跨调用窗口租约：lease_id 全链路透传")
print("=" * 74)

cd = src("auto_dm/conversation_capture.py")
# A1 gate 用途＝用户显式（prio=0 / ttl≤300s）
check("A1 capture_all 用 PURPOSE_USER（用户显式，非后台 auto）",
      "purpose=PURPOSE_USER" in cd and "PURPOSE_USER" in cd,
      "用户点按钮的路径必须豁免后台守卫（既有铁律）")
# A2 gate 取得租约并登记
check("A2 capture_all 把 lease_id 登记进 _ACTIVE_LEASE",
      "_ACTIVE_LEASE[name] = _lease_id" in cd)
# A3 /cookie 带上租约
check("A3 refresh_cookie_via_owner 收到 lease_id",
      "lease_id=_lease_id" in cd)
# A4 capture_userinfo 带上租约
check("A4 capture_userinfo_via_browser 收到 lease_id",
      "capture_userinfo_via_browser(name, lease_id=_lease_id)" in cd)

gate = src("services/browser_gate.py")
check("A5 gate.refresh_cookie_via_owner 签名含 lease_id 并向下传",
      "lease_id: str = \"\"" in gate and "lease_id=lease_id" in gate
      and "release_lease" in gate)
check("A6 gate.release_lease 函数存在（设计文档 §3.6 的释放环节）",
      re.search(r"def release_lease\(", gate) is not None)

la = src("dy_apis/login_api.py")
check("A7 login_api.refresh_cookie_from_profile 接 lease_id 并转给 _bcc_post",
      "lease_id=\"\"" in la and "_bcc_post(account_name, \"/cookie\", timeout=15, lease_id=lease_id)" in la)
check("A8 login_api._bcc_post 把 lease_id 塞进请求体",
      '"lease_id"] = lease_id' in la)

bd = src("daemon/browser_daemon.py")
check("A9 BCC /capture_userinfo 请求体模型带 lease_id（WaitBody）",
      re.search(r"class WaitBody\(BaseModel\):[\s\S]{0,400}?lease_id: str = \"\"", bd) is not None)
check("A10 BCC /cookie 请求体模型带 lease_id（CookieBody）",
      re.search(r"class CookieBody\(BaseModel\):[\s\S]{0,200}?lease_id", bd) is not None)
check("A11 端点把 lease_id 转给容器方法",
      "capture_userinfo_map(wait=body.wait or 15," in bd
      and "lease_id=body.lease_id or \"\"" in bd
      and "refresh_cookie_to_env(\n        lease_id=(body.lease_id if body else \"\"))" in bd)
check("A12 container.capture_userinfo_map 签名含 lease_id 且传进 _exec",
      "lease_id: str = \"\"," in bd
      and "lease_id=lease_id)" in bd)
check("A13 container.get_cookies / refresh_cookie_to_env 透传 lease_id",
      "async def get_cookies(self, lease_id: str = \"\"," in bd
      and "async def refresh_cookie_to_env(self, lease_id: str = \"\"," in bd)

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print("B. 早退判据：只看 DOM 累计（hook 计数器已废）")
print("=" * 74)
check("B1 早退判据用 _dom_total 而非 hook 的 _cur",
      "_prev >= 0 and _dom_total <= _prev" in bd,
      "原判据读 __CAP_USERINFO__.map（恒 0）→ 第 3 轮必 break")
check("B2 DOM 抓取块位于早退判据之前",
      bd.index("CAP_DOM_SWEEP_JS") < bd.index("_prev >= 0 and _dom_total <= _prev"),
      "判据依赖本屏 DOM 结果，顺序不可反")
check("B3 滚动日志同时打印 DOM 与 hook（保留可观测性）",
      "DOM累计昵称={_dom_total}(本屏+{_dnew})" in bd and "hook={_cur}" in bd)

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print("C. 内部线程豁免租约（预热不再被业务租约饿死）")
print("=" * 74)
check("C1 _exec 新增 internal 参数且豁免租约门",
      "internal: bool = False" in bd
      and re.search(r"if internal:[\s\S]{0,200}?_is_reentry = True", bd) is not None)
check("C2 预热线程走 internal=True",
      "capture_userinfo_map(wait=15, internal=True)" in bd)
check("C3 保活回写走 internal=True",
      "refresh_cookie_to_env(internal=True)" in bd)
check("C4 _prewarm_running 标记存在且在 finally 复位",
      "self._prewarm_running: bool = False" in bd
      and "container._prewarm_running = True" in bd
      and "container._prewarm_running = False" in bd)
check("C5 业务请求会先等预热完成（最多 40s）",
      "_prewarm_running and time.time() < _deadline" in bd,
      "消除「预热完成 7s 后业务才到、扛不过租约」的时序错配")

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print("D. 拿不到资源必须显式失败（不再假成功）")
print("=" * 74)
ms = src("api/messages.py")
check("D1 refresh：BCC 未就绪时 raise 而非只 warning",
      "浏览器守护(BCC)未能就绪" in ms and "raise RuntimeError" in ms)
check("D2 refresh 的 finally 释放跨调用窗口租约",
      "release_active_lease(account)" in ms and "finally:" in ms)
check("D3 capture_all 统一入口失败仍保留 CAP-016（含显式失败契约）",
      'logger.warning("CAP-016"' in cd)

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print("E. 租约释放函数可用性（不连 BCC，桩掉网络层）")
print("=" * 74)
try:
    import importlib
    import types
    # 桩掉 requests / loguru 之外的重依赖，避免连网
    sys.modules.setdefault("requests", types.ModuleType("requests"))
    cc = importlib.import_module("auto_dm.conversation_capture")
    check("E1 release_active_lease 可导入", hasattr(cc, "release_active_lease"))
    check("E2 空登记表调用幂等且不抛",
          cc.release_active_lease("不存在的账号__test") is None)
    cc._ACTIVE_LEASE["__t__"] = "deadbeef0000"
    # 打桩 gate.release_lease，验证确实被调用
    import services.browser_gate as gmod
    _called = {}
    _orig = gmod.release_lease

    def _stub(account, lease_id, holder=""):
        _called.update(account=account, lease_id=lease_id, holder=holder)
        return {"ok": True}

    gmod.release_lease = _stub
    cc.release_active_lease("__t__")
    check("E3 release 用正确的 account/lease_id 调用 gate",
          _called.get("account") == "__t__" and _called.get("lease_id") == "deadbeef0000",
          str(_called))
    check("E4 释放后登记表已清空（不会重复释放）", "__t__" not in cc._ACTIVE_LEASE)
    gmod.release_lease = _orig
except Exception as e:  # noqa: BLE001
    check("E1~E4 导入/桩测", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print("F. 错误码契约（六段齐全，用户铁律）")
print("=" * 74)
try:
    import errcode
    d = errcode.CODE_DESIGN.get("CAP-017") or {}
    need = ["design", "contract", "deviation", "chain", "root", "verify"]
    miss = [k for k in need if not d.get(k)]
    check("F1 CAP-017 六段契约齐全", not miss, f"缺 {miss}" if miss else "全部就位")
    check("F2 CAP-017 已登记进 ERRCODES", "CAP-017" in errcode.ERRCODES)
    lk = errcode.lookup("CAP-017") or {}
    check("F3 lookup() 透出 design（域级回落不空）", bool(lk.get("design")))
except Exception as e:  # noqa: BLE001
    check("F1~F3 errcode 契约", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print(f"结果：PASS={len(PASS)}  FAIL={len(FAIL)}")
print("=" * 74)
if FAIL:
    for f in FAIL:
        print("  ❌ " + f)
    sys.exit(1)
print("全部通过 ✅")
