# -*- coding: utf-8 -*-
"""门禁：**默认登录路径 = 接口桥扫码（无头）** + **失败真实回落手动** + **服务端确认判据**。

## 为什么需要（2026-09-30 用户实测报障）
用户原话：「我要求实现的接口是扫码登录，没有实现依旧是打开的有头浏览器要求手动登录」
+「没有等到二维码的生成凭证获取机制直接显示已补货，但是这个补货的凭证是过期的」。

三个缺陷被本门禁固化：
  · **D1 默认路径**：默认曾直接走 `enrich_auth`（有头），接口桥只在显式 mode 时才跑
    ⇒ 桥等于没接进主路径。G-B1/G-B2 固化「默认走桥、桥败才回落」。
  · **D2 谎报源头**：判据只看「本地 cookie 存在（sessionid/sid_tt）」——而陈旧 sessionid
    一直在 profile 里 ⇒ **未扫码也立刻假成功**。G-S1/G-S2 固化「必须服务端确认」。
  · **D3 逐层回落**：每层失败必须真实落到下一层，禁中间层假成功。G-B3 固化。

判据以**源码结构 + 行为**双重锚定（行为桩尽量轻，避免引入浏览器依赖）。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_ROOT = tempfile.mkdtemp(prefix="h30_bridge_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

_BACKEND = Path(__file__).resolve().parent
_ACCOUNTS = (_BACKEND / "api" / "accounts.py").read_text(encoding="utf-8")
_LR = (_BACKEND / "auto_dm" / "login_remote.py").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
#  D1 默认路径：接口桥优先，手动仅兜底
# ══════════════════════════════════════════════════════════════════

def test_b1_do_scan_default_calls_bridge_first():
    """G-B1：`_do_scan` 默认（无 _force_manual）必须**先调 `_rpa_scan_login`**（接口桥）。"""
    seg = _ACCOUNTS[_ACCOUNTS.find("def _do_scan("):]
    seg = seg[: seg.find("\ndef ")]
    # 默认分支必须存在「调 _rpa_scan_login」且由 _force_manual 反向门控
    assert "_force_manual" in seg, "必须存在显式手动开关（_force_manual）"
    assert "if not _manual_direct:" in seg, "默认（非显式手动）必须进入桥分支"
    # 用**真实调用语句**锚定（注释里也会提到这两个名字，不能用裸名字判序）
    bridge_at = seg.find("_rpa_scan_login(name, env_path, st)")
    enrich_at = seg.find("enrich_auth(None, force=True")
    assert bridge_at != -1 and enrich_at != -1, "必须同时存在桥调用与兜底调用"
    assert bridge_at < enrich_at, "接口桥调用必须**先于** enrich_auth（默认桥优先）"


def test_b2_bridge_fail_path_is_manual_fallback():
    """G-B2：桥未成 ⇒ 必须真实调用 enrich_auth（最初方案作兜底）。"""
    seg = _ACCOUNTS[_ACCOUNTS.find("def _do_scan("):]
    seg = seg[: seg.find("\ndef ")]
    i = seg.find("else:")
    assert i != -1, "必须有 else 兜底分支"
    tail = seg[i:]
    assert "enrich_auth(" in tail, "兜底分支必须真实调用 enrich_auth"
    assert 'st["path"] = "manual"' in tail, "兜底必须如实标注 path=manual"


def test_b3_manual_fallback_verifies_server_not_just_cookie():
    """G-B3：兜底（手动）路径**不得**只凭本地 cookie 宣称成功（服务端复判）。"""
    seg = _ACCOUNTS[_ACCOUNTS.find("def _do_scan("):]
    seg = seg[: seg.find("\ndef ")]
    tail = seg[seg.find("else:"):]
    assert "_cred_verify_after_write(" in tail, \
        "手动兜底路径必须以服务端权威判据复判（禁 bool(cookie) 假成功）"
    # 旧写法（只判 cookie 存在 且无复判）必须已消失
    assert 'st["loggedIn"] = bool(getattr(auth, "cookie", None))\n' not in tail, \
        "旧的「只判 cookie 存在」必须已被服务端复判取代"


# ══════════════════════════════════════════════════════════════════
#  D1' scan_login / update_login 默认契约
# ══════════════════════════════════════════════════════════════════

def test_b4_scan_login_default_is_bridge_not_manual():
    """G-B4：/scan 默认（无 mode）⇒ force_manual=False ⇒ 走桥；"manual" 才置 True。"""
    seg = _ACCOUNTS[_ACCOUNTS.find("async def scan_login("):]
    seg = seg[: seg.find("\n@router")]
    assert 'force_manual = mode in ("manual", "browser")' in seg, \
        "scan_login 必须只认 manual/browser 为显式手动"
    assert '["_force_manual"] = force_manual' in seg
    # 默认文案必须指向「接口桥·无头」而不是「已打开有头指纹浏览器」
    assert "接口桥" in seg, "默认返回文案必须如实标注走的是接口桥"


def test_b5_update_login_default_routes_to_qr_bridge():
    """G-B5：/update-login 省略 mode ⇒ 走扫码（桥），不再默认有头手动。"""
    seg = _ACCOUNTS[_ACCOUNTS.find("async def update_login("):]
    seg = seg[: seg.find("\n@router")]
    i = seg.find("if not mode:")
    assert i != -1
    blk = seg[i: i + 900]
    assert 'scan_login(name, {"mode": "qr"})' in blk, \
        "默认分支必须走扫码（桥），不得直接 await scan_login(name)（=有头手动）"
    assert "manual" in seg and 'mode == "manual"' in seg, \
        "必须保留显式 manual 路径（最初方案）"


# ══════════════════════════════════════════════════════════════════
#  D2 谎报源头：必须服务端确认，不得只判 cookie 存在
# ══════════════════════════════════════════════════════════════════

def _func_body(src: str, name: str) -> str:
    i = src.find("async def %s(" % name)
    if i == -1:
        i = src.find("def %s(" % name)
    assert i != -1, f"未找到函数 {name}"
    j = src.find("\nasync def ", i + 1)
    k = src.find("\ndef ", i + 1)
    ends = [x for x in (j, k) if x != -1]
    return src[i: min(ends)] if ends else src[i:]


def test_s1_poll_bridge_confirmed_requires_server():
    """G-S1：桥的确认轮询必须调用服务端探活，且**不得**只凭 cookie 存在返回 ok。"""
    body = _func_body(_LR, "poll_bridge_confirmed")
    assert "_probe_session_valid_by_cookies(" in body, \
        "确认轮询必须调用服务端权威探活（禁只判本地 cookie 存在）"
    # 旧判据（cookie 存在 ⇒ 直接 ok）必须已消失
    bad = re.search(r'if\s+state\["confirmed"\]\s+or\s+cookies\.get\("sessionid"\)', body)
    assert bad is None, "旧的「confirmed or cookie 存在 ⇒ 成功」假成功判据必须已移除"
    assert 'if srv["ok"]:' in body, "必须以服务端结论作为成功条件"


def test_s2_poll_qr_scanned_requires_server():
    """G-S2：截图兜底轮询同样必须服务端确认（两条路径同一判据）。"""
    body = _func_body(_LR, "poll_qr_scanned")
    assert "_probe_session_valid_by_cookies(" in body, \
        "兜底轮询必须调用服务端权威探活"
    bad = re.search(r'if\s+cookies\.get\("sessionid"\)\s+or\s+cookies\.get\("sid_tt"\):\s*\n\s*logger\.info\("\[login_remote\]\s*检测到真实登录态', body)
    assert bad is None, "旧的「cookie 存在 ⇒ 判已扫码」假成功判据必须已移除"


def test_s3_server_probe_uses_official_endpoint():
    """G-S3：服务端探活必须打官方 passport 端点（判据真源，非自造）。"""
    body = _func_body(_LR, "_probe_session_valid_by_cookies")
    assert "passport/account/info/v2" in body, "必须以官方会话端点为准"
    assert "user_id" in body, "必须校验 user_id>0"

# ══════════════════════════════════════════════════════════════════
#  2026-10-01：API 优先 + A 方案（稳定 id）兜底 —— 防回归
# ══════════════════════════════════════════════════════════════════

def test_a1_default_is_bridge_api_explicit_only():
    """G-A1：默认必须走**接口桥**；API **仅**在 DY_LOGIN_QR_BACKEND=api 时启用。

    🔴 2026-10-01 实测订正（曾误把 API 提为默认，已撤回）：
       API 出码仅 3.5s（vs 桥 25~30s），但**二维码约 65 秒即被服务端判 expired**
       （三轮一致：65s / 65s / 64s；正常应 5 分钟 ⇒ 压缩到 1/5）。
       用户实扫 ⇒ 提示过期；ok=False，四项签名与 sessionid 全无。
       轮询 poll_err=None ⇒ 非限频/拦截，是会话被降级（合成指纹：缺 fpk1/dtrait）。
       ⇒ **快而无用**，不得作默认。
    """
    body = _func_body(_ACCOUNTS, "_rpa_scan_login")
    # ① API 必须被**显式门控**为可选（只有 == "api" 才走）
    assert '_backend == "api"' in body, \
        "API 必须是显式可选（DY_LOGIN_QR_BACKEND=api），不得作默认"
    # ② 默认分支不得调用 API（默认走桥）
    assert 'logger.info(f"[scan] 账号 {name} 走接口桥' in body, \
        "默认分支必须走接口桥"
    # ③ 「API 先于桥」的旧断言必须已撤销（否则等于又把 API 设成默认）
    assert '_backend not in ("bridge", "rpa")' not in body, \
        "旧的『API 默认优先』门控必须已移除"


def test_a1b_api_65s_expiry_is_recorded():
    """G-A1b：API 通道的「二维码约 65s 过期」实测结论必须**留在代码里**。

    这是**防重蹈**门禁：若未来有人又想当然把 API 提为默认，
    本条与 G-A1 会立刻提醒他去看这条实测（而不是只看「出码快」）。
    """
    body = _func_body(_ACCOUNTS, "_rpa_scan_login")
    assert "65" in body and ("expired" in body or "过期" in body), \
        "API 的 65 秒过期实测必须记录在案（防未来误判『出码快=可用』）"


def test_a2_bridge_click_prefers_stable_id():
    """G-A2：桥点「登录」必须**优先用稳定 id**，不得只用文本匹配+坐标。

    上游情报（2026-10-01）：仓库已配好 `div[id=douyin_login_comp_btn_id]`
    （dom_locator.py:214 btn_submit）。旧实现全页文本匹配 + getBoundingClientRect
    算坐标 ⇒ 分辨率/跨设备/改版即点空（用户质疑点）。
    """
    body = _func_body(_LR, "bridge_qr_login")
    assert "SEL_ONE_CLICK_BTN" in body, "必须优先使用稳定 id 锚点（SEL_ONE_CLICK_BTN）"
    assert '_adaptive_xpath(page, "btn_submit")' in body, \
        "必须有自适应兜底（dom_locator 的 btn_submit）"
    # 顺序：稳定 id → 自适应 → 文本
    i_id = body.find("SEL_ONE_CLICK_BTN")
    i_xp = body.find('_adaptive_xpath(page, "btn_submit")')
    i_txt = body.find("querySelectorAll('button,div,span,a')")
    assert i_id < i_xp < i_txt, "定位顺序必须是：稳定 id → 自适应 → 文本兜底"


def test_a3_bridge_no_hardcoded_viewport():
    """G-A3：桥**不得**硬编码 1600x1000（会覆盖账号档案视口 ⇒ 破坏指纹单源化）。

    证据：errcode_data.py:663 列为错误码根因；services/env_audit 会报 screen 不一致。
    """
    body = _func_body(_LR, "bridge_qr_login")
    assert 'set_viewport_size({"width": 1600' not in body, \
        "桥不得硬编码视口（应沿用账号档案视口）"
