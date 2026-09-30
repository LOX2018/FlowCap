# coding=utf-8
"""login_remote —— IM 远程登录编排层（ADR-017 · RPA/DOM 方案）。

## 职责（SoC）
本模块**只做编排**，不重造底层能力：
  · 浏览器生命周期 → `auto_dm.vbrowser.launch_async`
  · profile 归属     → `auto_dm.accounts.profile_dir_of` / `services.browser_gate.ProfileOwnership`
  · 凭证写盘         → 复用既有 `login_capture` / `persistenceLoginInfo`（本模块不写 .env）

## 用户拍板的流程（2026-09-26）
```
【按账号状态自动分流 —— 不让用户选】

状态 A：全新账号（无登录态）
  → 优先【DOM 二维码】：登录页元素截图 → cv2 解码校验 → 回调出去（推 IM）
  → 备用【验证码】

状态 B：已有账号（登录态在、凭证过期）
  → 只用【验证码】：DOM 点「获取验证码」→ 用户回传 → keyboard.type → 点「登录」
```

## 关键技术事实（全部实测，见 docs/adr/ADR-017-调研记录-20260926.md）
| 项 | 结论 |
|---|---|
| 登录页入口 | `https://www.douyin.com/?modal_id=login` 可靠弹出登录框 |
| 验证码框锚点 | `input[name="button-input"]`（type=tel, maxLength=6）|
| 手机号框锚点 | `input[name="normal-input"]`（type=tel, maxLength=50）|
| 扫码/验证码 tab | 文本锚点「扫码登录」/「验证码登录」 |
| **二维码判据** | **`cv2.QRCodeDetector` 能解码**（唯一可靠；像素统计假阳性 100%）|
| 填入方式 | **`keyboard.type` 逐字**（`Ctrl+V` 实测不可靠，已否决）|
| 接口方案 | ❌ 未登录态被反爬挑战页拦截（`get_qrcode` 返回 HTML），**不用** |
| 🔴 E1 | 浏览器异常退出 → `parent.lock` 残留 → 下次启动**卡 180s** ⇒ 须自愈 |

## 契约（DbC）
Pre:
  P1 账号存在，env_path 可推导
  P2 已取得该账号 profile 所有权（调用方或本模块内获取）
  P3 该账号凭证守护已停（释放 profile 锁）
Post:
  C1 成功时返回 auth 且凭证经 `login_capture` 判非污染态（由调用方校验）
  C2 无论成败，profile 锁必须释放、守护必须回拉（调用方负责；本模块提供钩子）
  C3 二维码/验证码**绝不出现在日志明文**
Invariants:
  I1 单 profile 铁律：只用 `profile_dir_of(env_path)`，绝不建临时 profile
  I2 锁自愈：启动前若发现陈旧锁（无同名进程）则安全清除
  I3 状态可观测：每个阶段有明确 status 返回值
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Callable, Optional

from loguru import logger
from utils.fingerprint import get_profile, user_agent

# ── DOM 自适应定位兜底（可选依赖；缺失/异常即优雅降级，零回归）─────────────
#   见 auto_dm/dom_locator.py（Scrapling parser 层 + 确定性校验 + 唯一性铁律）。
#   **只作兜底**：各函数的既有确定性判据（稳定锚点 / JS 文本 / 服务端校验）永远优先；
#   仅当它们找不到控件时，才用自适应重定位（应对抖音登录页改版）。
try:
    from auto_dm import dom_locator as _domloc
except Exception:  # noqa: BLE001
    try:
        from . import dom_locator as _domloc  # type: ignore
    except Exception:  # noqa: BLE001
        _domloc = None

# 文本 → 自适应目标键（供文本点击失败时兜底）
_TEXT_TO_KEY = {"扫码登录": "tab_scan", "验证码登录": "tab_sms"}


async def _adaptive_xpath(page, key: str):
    """自适应兜底：依赖缺失/定位失败 ⇒ 返回 None（调用方保持原行为）。"""
    if not key or _domloc is None or not _domloc.available():
        return None
    try:
        return await _domloc.locate_xpath(page, key)
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 自适应兜底异常: {}", e)
        return None


async def _mouse_click_locator(page, loc) -> bool:
    """用**真实鼠标**点某个 locator（沿用项目惯例；JS .click() 在 Semi Design 不可靠）。"""
    try:
        box = await loc.bounding_box()
        if not box:
            return False
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await asyncio.sleep(0.12)
        await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 真实鼠标点击失败: {}", e)
        return False


# 登录页入口（实测可靠，绕开 A/B 差异）
LOGIN_URL = "https://www.douyin.com/?modal_id=login"

# 说明：防 GC 强引用池已统一实现在 `vbrowser_camoufox`（创建点登记，全路径生效）：
#   `vbrowser_camoufox._CAMOUFOX_KEEP` / `_keep_camoufox_alive` / `_release_camoufox`。
#   本模块不再单独维护，避免两处实现漂移。

# ── DOM 锚点（全部实测得来，勿凭猜修改）─────────────────────────────────
SEL_CODE_INPUT = 'input[name="button-input"]'      # 验证码框（maxLength=6）
SEL_PHONE_INPUT = 'input[name="normal-input"]'     # 手机号框（maxLength=50）
SEL_LOGIN_PANEL = "#douyin-login-new-id"           # 登录面板（实测 id）
SEL_SCAN_COMP = "#douyin_login_comp_scan_code"     # 扫码组件整块
SEL_QR_CONTAINER = "#animate_qrcode_container"     # 二维码【动画外框】（内部才是码）

# ★ 2026-09-30 实测修正（真机 DOM 快照 `artifacts/spike-scrapling-locator/_live/`）
#   抖音登录页已改版，旧锚点**全部失效**，按新结构重建：
#   · 二维码本体 = `#animate_qrcode_container` 内 **13 个 `<image>`** 拼成的 SVG
#     ⇒ 旧主锚点 `"#animate_qrcode_container img"` **恒命中 0**（真机读数 `qr_img=0`）
#     ⇒ 改为「**对该容器元素截图**（Playwright 会把 SVG 栅格化）→ cv2 解码」。
#   · 两个 tab 是 `<span class="C6OZQwMA">`（**哈希类名、无 id**，不可作点击锚点）
#     ⇒ 改点**稳定 id 的父容器**（下方实测 id 稳定、非构建期哈希）。
_SEL_QR_IMG = f"{SEL_QR_CONTAINER} img"          # 旧锚点：仅历史兼容（现已恒 0）
_QR_ANCHORS = (
    SEL_QR_CONTAINER,                             # ★ 容器本体（SVG ⇒ 栅格化后解码）
    _SEL_QR_IMG,                                  # 旧 img 锚点（若改回 img 仍可用）
    SEL_SCAN_COMP,
)

# ── 稳定 id 的 tab / 入口（点击目标；2026-09-30 真机 DOM 实测）──────────────
SEL_TAB_SMS_BOX = "#douyin_login_comp_mobile_code"      # 「验证码登录」tab 容器（稳定 id）
SEL_ONE_CLICK_BTN = "#douyin_login_comp_btn_id"         # 「一键登录」面板主按钮（文本实测=「登录」）
SEL_PHONE_BOX = "#douyin_login_comp_normal_input_id"    # 手机号框外层容器
SEL_CODE_BOX = "#douyin_login_comp_button_input_id"     # 验证码框外层容器

TAB_SCAN = "扫码登录"
TAB_PHONE = "验证码登录"
BTN_SEND_CODE = "获取验证码"
BTN_LOGIN = "登录"
# 「一键登录」面板 → 进入手机号表单的**唯一入口**（真机 DOM 实测文案）
ONE_CLICK_ENTRY = "登录其他账号"

# 陈旧锁文件名（Firefox 系）
_LOCK_FILES = ("parent.lock", ".parentlock")

# ★ 2026-09-26 实测修正（ADR-017 M4）：Camoufox 的实际 profile 在**子目录**里。
#   真实路径: <profile>/_camoufox/parent.lock
#   当初只查 <profile>/ 根目录 ⇒ 漏判 ⇒ 启动卡 180s（F2 实测踩到）。
#   ⇒ 采用**递归**查找（限深度），兼容不同内核的目录布局。
_LOCK_MAX_DEPTH = 3


def find_lock_files(profile_dir: str, max_depth: int = _LOCK_MAX_DEPTH) -> list:
    """递归找 profile 下所有 Firefox 系锁文件（含子目录）。

    返回绝对路径列表。深度受限，避免在巨型 profile 里深挖。
    """
    import os as _os
    hits = []
    base_depth = profile_dir.rstrip("\\/").count(_os.sep)
    for root, dirs, files in _os.walk(profile_dir):
        cur_depth = root.count(_os.sep) - base_depth
        if cur_depth >= max_depth:
            dirs[:] = []          # 不再深入
        for fn in files:
            if fn in _LOCK_FILES:
                hits.append(_os.path.join(root, fn))
    return hits


def heal_stale_profile_lock(profile_dir: str, retries: int = 3) -> dict:
    """检测并清除**陈旧** profile 锁（E1）。

    判据（保守）：**仅当** `camoufox/firefox` 进程数为 0 时，才删除锁文件。
    只要有进程存活就**绝不删除**（避免破坏正在运行的实例）。

    ⚠️ 锁文件可能位于**子目录**（如 `<profile>/_camoufox/parent.lock`）——
    故递归查找，不只看根目录（2026-09-26 实测教训）。

    返回 {'healed': bool, 'removed': [相对路径], 'procs': int, 'checked': int}
    """
    out = {"healed": False, "removed": [], "procs": -1, "checked": 0, "reaped": 0}
    locks = find_lock_files(profile_dir)
    out["checked"] = len(locks)
    if not locks:
        logger.debug("[login_remote] profile 无锁文件，无需自愈")
        return out
    # 有锁 → 看是否有**本 profile 的**进程；无进程 ⇒ 陈旧锁
    # 2026-09-28 修：改用 profile 级判据（原 `count_browser_processes()` 是机器级
    # 总数 —— 别的账号 / 用户手动开的 Camoufox 都算在内 ⇒ 陈旧锁永不被清理）。
    procs = count_profile_processes(profile_dir)
    out["procs"] = procs
    if procs < 0:
        # 「不能可靠识别」⇒ 不下结论、不删锁（保守优先，沿用原语义）
        logger.debug("[login_remote] 无法判定 profile 占用（procs=-1），保守不清理锁")
        return out
    if procs != 0:
        logger.debug("[login_remote] profile 有 {} 个锁文件且本 profile 存活进程 {} 个，"
                     "判定为在用，不清理", len(locks), procs)
        return out
    for fp in locks:
        for _ in range(retries):
            try:
                os.remove(fp)
                rel = os.path.relpath(fp, profile_dir)
                out["removed"].append(rel)
                out["healed"] = True
                logger.info("[login_remote] E1 陈旧锁已清除: {}", rel)
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("[login_remote] 删锁 {} 失败（重试）: {}", fp, e)
                time.sleep(0.5)
    return out


def reap_profile_processes(profile_dir: str) -> int:
    """启动前清扫仍占用该 profile 的 Camoufox 残留进程（复用项目既有能力）。

    ## 为什么必须做（2026-09-26 实测，非推测）
    实测复现：`v_close.py` 连续启动/关闭两次后，**紧接着**启动新实例
    （同一 profile 或空 profile 均然）报
        `BrowserType.launch_persistent_context: Target page, context or
         browser has been closed`（BCC-058），而 **4 分钟后自动恢复**。
    这正是 `vbrowser_camoufox._reap_camoufox_processes` 文档记录的时序竞态：
    `close_camoufox_context()` 后进程【需时间退净】，新实例撞上旧进程即失败。

    ## 为什么调项目既有函数而不是自己写
    `_reap_camoufox_processes` 已含**安全边界**：只杀命令行命中本 profile
    绝对路径的进程（含 -contentproc 子进程），绝不按进程名盲杀；且路径过短
    时拒绝执行（防误杀无关进程，含测试宿主自身）。

    返回清扫的进程数；任何异常都不阻塞启动流程。
    """
    try:
        from vbrowser_camoufox import _reap_camoufox_processes
        n = _reap_camoufox_processes(profile_dir)
        if n:
            logger.info("[login_remote] 启动前清扫残留进程 {} 个: {}", n, profile_dir)
        return n
    except Exception as e:  # noqa: BLE001
        logger.warning("[login_remote] 残留清扫失败（不阻塞启动）: {}", e)
        return 0


def ensure_profile_released(profile_dir: str) -> dict:
    """启动前把 profile 归位到「可安全启动」状态：**先清扫 → 再判锁 → 才自愈**。

    ## 为什么必须是这个顺序（2026-09-28 实测教训，顺序错了等于没做）
    原两个调用点把顺序写反了 —— 先按「陈旧锁」判，**之后**才清扫残留进程：
    判锁那一刻残留进程还在 ⇒ 恒判「在用」⇒ 清扫成功了锁却一次都没被清过
    （`_camoufox/parent.lock` 从 09:36:14 一直留到 09:41+，最终撞 BCC-058）。
    正确顺序：**先**把残留进程扫干净，**再**在「进程数=0」的事实上判锁并清理。

    ## 契约
    pre : profile_dir 为该账号固定 profile（绝对路径）
    post: 返回 {'reaped', 'healed', 'removed', 'procs', 'checked'}；
          失败不抛 —— 任何异常都降级为「不清扫」，绝不阻断启动流程本身。
    """
    out = {"reaped": 0, "healed": False, "removed": [], "procs": -1, "checked": 0}
    # ① 先清扫残留（这一步让「进程数=0」成为可判定的事实）
    try:
        out["reaped"] = reap_profile_processes(profile_dir)
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 残留清扫跳过: {}", e)
    # ② 再判锁并自愈（此刻若 procs=0，陈旧锁才会真的被删）
    try:
        heal = heal_stale_profile_lock(profile_dir)
        out.update({"healed": heal.get("healed", False),
                    "removed": heal.get("removed", []),
                    "checked": heal.get("checked", 0)})
        # procs 以清扫后的实测为准（heal 内的计数即此刻事实）
        out["procs"] = heal.get("procs", -1)
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 陈旧锁自愈跳过: {}", e)
    if out["healed"]:
        logger.info("[login_remote] profile 已自愈（清扫 {} 个残留 + 清除 {} 个陈旧锁）",
                    out["reaped"], len(out["removed"]))
    return out


def bcc_launch_allowed(name: str) -> tuple[bool, str]:
    """**拉起前**检查 BCC 节流（供不含 spawn 的启动路径复用同一判据）。

    背景（DSSCC-BCC-002，实测风控风险）：`browser_daemon` 走的是
    `vbrowser.launch_async` **直连**路径、**不经** `accounts.ensure_bcc` 的 spawn
    节流 —— 若只把节流做在 ensure_bcc，则「账号级 BCC 守护」这条路径仍可对
    持续性失效账号无限冷启动（实测：张老师被反复拉起 8+ 进程）。
    故把节流做成**可复用判据**，两条启动路径共用同一限额。

    返回 (allowed, reason)。**只读**，不记账（记账在真正 spawn 处）。
    """
    try:
        from auto_dm import accounts as _A
        import time as _t
        hist = [t for t in _A._bcc_spawn_hist.get(name, [])
                if _t.time() - t < _A._BCC_SPAWN_WINDOW]
        if len(hist) >= _A._BCC_SPAWN_MAX:
            return False, (f"{int(_A._BCC_SPAWN_WINDOW)}s 内已拉起 {len(hist)} 次"
                           f"（上限 {_A._BCC_SPAWN_MAX}）")
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 节流判据不可用（放行）: {}", e)
    return True, ""


def count_profile_processes(profile_dir: str) -> int:
    """统计**命令行命中该 profile 路径**的 camoufox/firefox 进程数。

    ## 为什么不能用 `count_browser_processes()`
    实测（2026-09-28，用户报「BCC 又异常」）：`heal_stale_profile_lock` 原用
    机器级进程总数 —— 只要**任何**账号的 BCC 或用户手动开的 Camoufox 还在跑，
    总数就 ≠ 0 ⇒ 陈旧锁**永远判为「在用」而不清理**。本案张老师 profile 的
    `_camoufox/parent.lock` 一直残留（09:36:14），而机器上另有 16 个 camoufox
    进程 ⇒ 自愈逻辑全程空转，最终以 BCC-058「Failed to launch the browser
    process」收场。

    ## 判据（与 `_reap_camoufox_processes` 同一安全边界）
    只统计命令行含**本 profile 绝对路径**的进程；路径过短/非绝对 ⇒ 返回 -1
    （「不能可靠识别」时**不下结论**，调用方须保守处理，绝不据此删锁）。

    返回：进程数（≥0）；-1 = 无法可靠判定（psutil 不可用 / 路径可疑）。
    """
    import os as _os
    try:
        _ud_norm = str(profile_dir).replace("\\", "/")
        if len(_ud_norm) < 20 or (":" not in _ud_norm and not _ud_norm.startswith("/")):
            logger.debug("[login_remote] profile 路径过短/非绝对，拒绝进程计数: {}", profile_dir)
            return -1
        import psutil
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] psutil 不可用，无法判定 profile 占用: {}", e)
        return -1
    needle = _ud_norm.lower()
    n = 0
    try:
        for _p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                _nm = (_p.info.get("name") or "").lower()
                if "camoufox" not in _nm and "firefox" not in _nm:
                    continue
                _cl = " ".join(_p.info.get("cmdline") or []).replace("\\", "/").lower()
                if needle in _cl:
                    n += 1
            except Exception:
                continue
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] profile 进程遍历失败: {}", e)
        return -1
    return n


def count_browser_processes() -> int:
    """统计本机 camoufox/firefox 进程数（**仅用于观测/诊断**）。

    ⚠️ 自 2026-09-28 起，**不再用作锁陈旧判据** —— 判锁请用
    `count_profile_processes()`（profile 级），机器级总数会把别的账号的
    正常进程误判成本档案在用。

    ⚠️ Windows 中文系统 `tasklist` 输出为 **GBK**，用 `text=True` 会 UnicodeDecodeError
    且解码失败会让 stdout 变 None ⇒ 必须 capture bytes 后手动 decode。
    """
    import subprocess
    try:
        cp = subprocess.run(["tasklist"], capture_output=True, timeout=20)
        txt = (cp.stdout or b"").decode("gbk", "replace").lower()
        return txt.count("camoufox") + txt.count("firefox")
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 进程探测失败（按有进程处理，保守不删锁）: {}", e)
        return 1






# ══════════════════════════════════════════════════════════════════════
#  二维码：DOM 元素截图 + cv2 解码校验（唯一可靠判据）
# ══════════════════════════════════════════════════════════════════════

def decode_qr_file(png_path: str) -> tuple[bool, str]:
    """判定 PNG 是否为二维码 —— **唯一可靠判据 = cv2 能否解码**。

    ⚠️ 血泪教训：用「黑白占比 / 色数」等像素统计判二维码，实测假阳性 100%
    （文字区、黑底导航栏全被误判；真二维码带装饰边反而被漏）。
    故此处**只认解码结果**。

    返回 (ok, data_or_reason)
    """
    try:
        import cv2
    except ImportError:
        # 无 cv2 时不谎报成功，如实降级（调用方可据 reason 决定）
        return False, "cv2 未安装（无法机械判二维码）"
    img = cv2.imread(png_path)
    if img is None:
        return False, "图片读取失败"
    data, _pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
    if data:
        return True, data
    # 预处理重试（放大 + 二值化）——提高小尺寸/压缩失真的解出率
    try:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        data2, _, _ = cv2.QRCodeDetector().detectAndDecode(bw)
        if data2:
            return True, data2
    except Exception:  # noqa: BLE001
        pass
    return False, "解码失败（非二维码或图像质量不足）"


# 页面内 JS：枚举登录面板内的方形容器（截图候选）
_JS_ENUM_SQUARE = r"""
(() => {
  const panel = document.querySelector('#douyin-login-new-id')
               || document.querySelector('[id*=login i]')
               || document.body;
  const res = [];
  Array.from(panel.querySelectorAll('div,canvas,img,section,article')).forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width >= 130 && r.height >= 130 && Math.abs(r.width - r.height) <= 70
        && r.width < 700) {
      res.push({tag: el.tagName.toLowerCase(),
                cls: (el.className||'').toString().slice(0,90), id: el.id||'',
                w: Math.round(r.width), h: Math.round(r.height),
                x: Math.round(r.x), y: Math.round(r.y)});
    }
  });
  return res;
})()
"""

# 页面内 JS：点击文本锚点（tab / 按钮）
_JS_CLICK_TEXT = r"""
((text) => {
  const els = Array.from(document.querySelectorAll('div,span,a,p,li,button'));
  const t = els.find(e => (e.innerText||'').trim() === text);
  if (t) { t.click(); return true; }
  return false;
})
"""


async def handle_one_click_login(page, timeout_s: float = 6.0) -> dict:
    """处理「一键登录」界面：若出现则点「登录其他账号」进入表单。

    ## 为什么需要（2026-09-26 P6 / 2026-09-30 真机 DOM 复核，非推测）
    profile 里抖音**记住了上次账号**时，点「登录」弹出的**不是手机号表单**，
    而是「一键登录」面板（真机 DOM）::

        <p class="B_Nj1uaz">尚进工伤小助理</p>            ← 显示记住的账号昵称
        <div id="douyin_login_comp_btn_id">登录</div>       ← 主按钮（★ 文本是「登录」！
                                                              旧注释误标为「一键登录」，
                                                              故**不能**拿它当判据）
        <div class="cq1UKYpd"><p>登录其他账号</p></div>      ← ★ 进入表单的唯一入口

    此面板下 `input[name="normal-input"]`、「验证码登录」tab **都不存在** ⇒
    直接 `fill_phone` 必然失败（2026-09-30 运行日志实证：
    `短信登录失败于 fill_phone: 手机号填入失败（input[name="normal-input"]）`）。

    ## 判据（决定性，不猜按钮文案）
      ① `input[name="normal-input"]` 存在 ⇒ **已是表单**，无需处理；
      ② 页面出现「**登录其他账号**」文案 ⇒ **一键登录面板**（这是唯一入口，确定性）；
      ③ 两者都无 ⇒ 保持原行为返回（**绝不猜**、绝不乱点）。

    返回 {'one_click': bool, 'switched': bool, 'form': bool}
    """
    out = {"one_click": False, "switched": False, "form": False}
    try:
        t0 = time.time()
        st: dict = {}
        while time.time() - t0 < timeout_s:
            st = await page.evaluate(r"""
(() => {
  const form = !!document.querySelector('input[name="normal-input"]');
  // 「登录其他账号」是进入手机号表单的**唯一入口**（决定性判据）——
  // 用「自身直接文本」精确匹配，避免父容器含子孙文本造成误命中。
  let other = false;
  for (const el of document.querySelectorAll('p,span,div,a,button')) {
    const own = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3)
        .map(n => (n.textContent || '').trim())
        .join('')
        .trim();
    if (own === '登录其他账号') { other = true; break; }
  }
  return {form, other};
})()
""")
            if st.get("form") or st.get("other"):
                break
            await asyncio.sleep(0.4)

        if st.get("form"):
            out["form"] = True
            logger.debug("[login_remote] 已是表单界面，无需处理一键登录")
            return out
        if not st.get("other"):
            logger.debug("[login_remote] 未检测到「一键登录」界面（也无表单）")
            return out

        out["one_click"] = True
        logger.info("[login_remote] 检测到「一键登录」面板，点「{}」进入表单", ONE_CLICK_ENTRY)
        # ① 先走既有文本点击（JS .click()）
        done = await click_by_text(page, ONE_CLICK_ENTRY)
        # ② 兜底：Semi/Firefox 下 JS .click() 可能不生效 ⇒ 用**真实鼠标**点该文案节点
        if not done:
            done = await _mouse_click_by_text(page, ONE_CLICK_ENTRY)
        if done:
            out["switched"] = True
            await asyncio.sleep(1.5)
            # 切换后确认表单真的出现（否则如实返回 switched=False 的语义）
            try:
                out["form"] = await page.locator(SEL_PHONE_INPUT).count() > 0
            except Exception:  # noqa: BLE001
                out["form"] = False
            logger.info("[login_remote] 「{}」已点击，表单出现={}",
                        ONE_CLICK_ENTRY, out["form"])
        else:
            logger.warning("[login_remote] 「{}」点击失败（文本/鼠标两种方式均未命中）",
                           ONE_CLICK_ENTRY)
    except Exception as e:  # noqa: BLE001
        logger.warning("[login_remote] 一键登录处理异常（不阻塞）: {}", e)
    return out


async def _mouse_click_by_text(page, text: str) -> bool:
    """用**真实鼠标**点击「自身直接文本 === text」的最小元素（JS .click() 的兜底）。

    为什么需要：本项目既有实测结论 —— Semi Design 组件在 Camoufox/Firefox 下
    JS `.click()` 常不生效，必须走真实鼠标事件（与 `click_send_code` 同源做法）。
    """
    try:
        box = await page.evaluate(r"""
(t) => {
  let best = null, bestArea = Infinity;
  for (const el of document.querySelectorAll('p,span,div,a,button,label')) {
    const own = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3)
        .map(n => (n.textContent || '').trim())
        .join('')
        .trim();
    if (own !== t) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    const a = r.width * r.height;
    if (a < bestArea) { bestArea = a; best = el; }   // 取**最小**者＝文字本体
  }
  if (!best) return null;
  const r = best.getBoundingClientRect();
  return {cx: Math.round(r.x + r.width / 2), cy: Math.round(r.y + r.height / 2),
          w: Math.round(r.width), h: Math.round(r.height)};
}
""", text)
        if not box:
            return False
        await page.mouse.move(box["cx"], box["cy"])
        await asyncio.sleep(0.12)
        await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()
        logger.info("[login_remote] 真实鼠标点击「{}」成功（{}x{}）", text, box["w"], box["h"])
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 真实鼠标点击「{}」失败: {}", text, e)
        return False


async def grab_login_qrcode(page, out_png: str, shots_dir: Optional[str] = None,
                            wait_render_s: float = 30.0) -> dict:
    """在**已打开的登录页**上抓取二维码（稳定锚点 + cv2 校验）。

    前置：调用方已 goto LOGIN_URL。

    ## 锚点选择（2026-09-26 实测，见 docs/adr/ADR-017-调研记录 §7）
    | 锚点 | 实测结果 |
    |---|---|
    | `#animate_qrcode_container img` | ⭐ **最优**：178×178 精确，暗像素 33.9%，解码成功 |
    | `#douyin_login_comp_scan_code`   | 兜底：248×226 整块，暗像素 19.4%，解码成功 |
    | `#animate_qrcode_container svg`  | ❌ 40×40 装饰小图标，非二维码（暗像素 65% 假特征）|
    | `#animate_qrcode_container` 本体 | ❌ 是**动画外框**，截出几乎全白（暗像素 3.3%）|

    ## ⚠️ 必须等渲染完成
    实测：goto 后立刻截图 → 容器内**尚无 img** → 截到空白。
    须轮询直到 `img` 出现在容器内（实测约 1.5s，保守给 30s 上限）。

    返回 {'ok','png','decoded','box','reason','waited_s'}
    """
    shots_dir = shots_dir or os.path.join(os.path.dirname(out_png), "_qr_cands")
    os.makedirs(shots_dir, exist_ok=True)

    # ── ① 等二维码渲染（轮询容器内 img/canvas 出现）──────────────────
    t0 = time.time()
    waited = 0.0
    while time.time() - t0 < wait_render_s:
        try:
            n = await page.locator(_SEL_QR_IMG).count()
        except Exception:  # noqa: BLE001
            n = 0
        if n > 0:
            # 再等半秒让图片解码完成（img 出现 ≠ 位图已就绪）
            await asyncio.sleep(0.5)
            waited = time.time() - t0
            break
        await asyncio.sleep(0.5)
    logger.debug(f"[login_remote] 二维码渲染等待 {waited:.1f}s")

    # ── ② 按优先级试锚点 ────────────────────────────────────────────
    for sel in _QR_ANCHORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            fp = os.path.join(shots_dir, "anch.png")
            await loc.screenshot(path=fp)
        except Exception as e:  # noqa: BLE001
            logger.debug("[login_remote] 锚点 {} 截图失败: {}", sel, e)
            continue
        ok, data = decode_qr_file(fp)
        if ok:
            _crop_to_code(fp, out_png)
            logger.info("[login_remote] 二维码已抓取并解码（锚点 {}，{} 字节，等渲染 {:.1f}s）",
                        sel, os.path.getsize(out_png), waited)
            return {"ok": True, "png": out_png, "decoded": data,
                    "box": {"selector": sel}, "reason": "", "waited_s": round(waited, 1)}

    # ── ③ 兜底：枚举方形元素（旧法，慢但通用）──────────────────────
    logger.debug("[login_remote] 稳定锚点均未解出，回退枚举")
    cands = await page.evaluate(_JS_ENUM_SQUARE)
    for i, c in enumerate(cands):
        fp = os.path.join(shots_dir, f"cand_{i}.png")
        try:
            await page.screenshot(path=fp, clip={
                "x": c["x"], "y": c["y"], "width": c["w"], "height": c["h"]})
        except Exception as e:  # noqa: BLE001
            logger.debug("[login_remote] 候选[{}] 截图失败: {}", i, e)
            continue
        ok, data = decode_qr_file(fp)
        if ok:
            _crop_to_code(fp, out_png)
            logger.info("[login_remote] 二维码已抓取并解码成功（枚举兜底，{} 字节）",
                        os.path.getsize(out_png))
            return {"ok": True, "png": out_png, "decoded": data, "box": c,
                    "reason": "", "waited_s": round(waited, 1)}
    return {"ok": False, "png": None, "decoded": None, "box": None,
            "reason": f"稳定锚点+枚举({len(cands)})均未解出二维码", "waited_s": round(waited, 1)}


def _crop_to_code(src_png: str, out_png: str) -> None:
    """用 cv2 四点坐标把二维码本体裁出来（比容器截图更干净）；失败则原样复制。"""
    import shutil
    try:
        import cv2
        img = cv2.imread(src_png)
        if img is None:
            raise ValueError("read fail")
        _d, pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
        if pts is not None:
            xs = [p[0] for p in pts[0]]
            ys = [p[1] for p in pts[0]]
            pad = 14
            x0 = max(0, int(min(xs)) - pad)
            y0 = max(0, int(min(ys)) - pad)
            x1 = min(img.shape[1], int(max(xs)) + pad)
            y1 = min(img.shape[0], int(max(ys)) + pad)
            cv2.imwrite(out_png, img[y0:y1, x0:x1])
            return
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 裁切失败，原样复制: {}", e)
    shutil.copy(src_png, out_png)


# ══════════════════════════════════════════════════════════════════════
#  验证码 RPA：填空 / 点按钮（全部用真实输入事件）
# ══════════════════════════════════════════════════════════════════════

async def click_by_text(page, text: str) -> bool:
    """按**文本精确匹配**点击元素（tab / 按钮）。

    兜底（2026-09-29）：精确文本未命中时，用**自适应定位**重定位后再用真实鼠标点击
    （应对抖音改版 / 文案带空格 / 类名哈希化）。原判据永远优先。
    """
    try:
        if await page.evaluate(_JS_CLICK_TEXT, text):
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[login_remote] 点击「{}」异常: {}", text, e)
    key = _TEXT_TO_KEY.get(text)
    xp = await _adaptive_xpath(page, key) if key else None
    if xp:
        try:
            if await _mouse_click_locator(page, page.locator(xp).first):
                logger.info("[login_remote] 「{}」精确文本未命中 → 自适应重定位点击成功", text)
                return True
        except Exception as e:  # noqa: BLE001
            logger.debug("[login_remote] 自适应点击「{}」失败: {}", text, e)
    return False


# ── 「获取验证码」按钮：启用态判据（2026-09-26 实测）─────────────────────
#   禁用：color = rgba(22, 24, 35, 0.34)（34% 灰）
#   启用：color = rgb(254, 44, 85)（抖音红）
# ⇒ 必须**等启用再点**，否则点了也不发短信（静默失败）。
_JS_SEND_BTN_STATE = r"""
(() => {
  const all = Array.from(document.querySelectorAll('#douyin-login-new-id *'));
  // 兼容两种文本：未发送时「获取验证码」；已发送后「55s后重新发送」
  const m = all.filter(e => {
    const t = (e.innerText||'').trim();
    return t === '获取验证码' || /^\d+\s*s?\s*后重新发送$/.test(t);
  });
  if (!m.length) return {found: false, phase: 'unknown'};
  const el = m[m.length - 1];
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const text = (el.innerText||'').trim();
  const counting = /后重新发送/.test(text);          // 倒计时中 = 已发送成功
  const color = cs.color;
  const enabled = !counting
                  && !/rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0?\.\d+/.test(color)
                  && cs.cursor === 'pointer';
  return {found: true, text, phase: counting ? 'cooldown' : 'idle',
          color, cursor: cs.cursor, enabled,
          rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
          cx: Math.round(r.x + r.width / 2), cy: Math.round(r.y + r.height / 2)};
})()
"""


async def wait_send_code_enabled(page, timeout_s: float = 12.0) -> dict:
    """等「获取验证码」变为启用态（填了合法手机号之后）。

    返回 {'ok', 'state', 'reason'}
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = await page.evaluate(_JS_SEND_BTN_STATE)
        if not st.get("found"):
            return {"ok": False, "state": st, "reason": "未找到「获取验证码」"}
        if st.get("enabled"):
            return {"ok": True, "state": st, "reason": ""}
        await asyncio.sleep(0.5)
    return {"ok": False, "state": st, "reason": f"等 {timeout_s}s 按钮仍未启用（color={st.get('color')}）"}


async def click_send_code(page, timeout_s: float = 12.0) -> dict:
    """点「获取验证码」（**必须先等按钮启用** —— 禁用态点击是静默失败的）。

    返回 {'ok', 'state', 'reason', 'outcome', 'server_msg'}
      outcome ∈ {'sent', 'rate_limited', 'unknown'}
    """
    w = await wait_send_code_enabled(page, timeout_s)
    if not w["ok"]:
        logger.error("[login_remote] 获取验证码按钮不可点: {}", w["reason"])
        return {**w, "outcome": "unknown", "server_msg": ""}
    st = w["state"]
    # 用**真实鼠标**点（JS .click() 在 Semi Design 上不可靠，M3 已实测）
    try:
        await page.mouse.move(st["cx"], st["cy"])
        await asyncio.sleep(0.12)
        await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()
        logger.info("[login_remote] 已点「获取验证码」(color={})", st["color"])
    except Exception as e:  # noqa: BLE001
        logger.error("[login_remote] 点获取验证码异常: {}", e)
        return {"ok": False, "state": st, "reason": f"{type(e).__name__}: {e}",
                "outcome": "unknown", "server_msg": ""}

    # ── 识别服务端反馈（2026-09-26 实测：有频率限制）────────────────
    # 成功：按钮变「NNs后重新发送」倒计时
    # 限频：面板出现「验证码发送太频繁，请稍后再试」
    outcome, msg = "unknown", ""
    t0 = time.time()
    while time.time() - t0 < 6.0:
        await asyncio.sleep(0.6)
        s = await page.evaluate(_JS_SEND_BTN_STATE)
        if s.get("phase") == "cooldown":
            outcome = "sent"
            break
        tip = await page.evaluate(
            "(() => { const p = document.querySelector('#douyin-login-new-id');"
            " return p ? (p.innerText||'').replace(/\\s+/g,' ') : ''; })()")
        tip = tip or ""
        if "太频繁" in tip or "稍后再试" in tip:
            outcome, msg = "rate_limited", "验证码发送太频繁，请稍后再试"
            break
    logger.info("[login_remote] 获取验证码结果: outcome={} msg={}", outcome, msg[:40])
    return {"ok": outcome == "sent", "state": st, "reason": msg,
            "outcome": outcome, "server_msg": msg}


async def click_login(page) -> dict:
    """点「登录」按钮（真实鼠标）。"""
    box = await page.evaluate(r"""
    (() => {
      const all = Array.from(document.querySelectorAll('#douyin-login-new-id *'));
      const m = all.filter(e => (e.innerText||'').trim() === '登录');
      if (!m.length) return null;
      // 取最大的那个（按钮本体，非文字节点）
      let best = m[0], bestArea = 0;
      for (const el of m) {
        const r = el.getBoundingClientRect();
        const a = r.width * r.height;
        if (a > bestArea) { bestArea = a; best = el; }
      }
      const r = best.getBoundingClientRect();
      const cs = getComputedStyle(best);
      return {rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
              cx: Math.round(r.x + r.width/2), cy: Math.round(r.y + r.height/2),
              cls: (best.className||'').toString().slice(0,60), color: cs.color};
    })()
    """)
    if not box:
        # 兜底（2026-09-29）：面板内精确「登录」文本未命中 → 自适应重定位（应对改版）
        xp = await _adaptive_xpath(page, "btn_submit")
        if xp:
            try:
                if await _mouse_click_locator(page, page.locator(xp).first):
                    logger.info("[login_remote] 「登录」文本未命中 → 自适应重定位点击成功")
                    await asyncio.sleep(2.5)
                    return {"ok": True, "box": {"via": "adaptive"}, "reason": ""}
            except Exception as e:  # noqa: BLE001
                logger.debug("[login_remote] 自适应点击「登录」失败: {}", e)
        return {"ok": False, "reason": "未找到「登录」按钮"}
    try:
        await page.mouse.move(box["cx"], box["cy"])
        await asyncio.sleep(0.12)
        await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()
        logger.info("[login_remote] 已点「登录」(cls={})", box["cls"])
        await asyncio.sleep(2.5)
        return {"ok": True, "box": box, "reason": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


async def detect_login_result(handle: dict, timeout_s: int = 20,
                              interval_s: int = 2,
                              verify_server: bool = True) -> dict:
    """判定登录是否成功（**必须服务端确认**，不能只看 cookie 存在）。

    ## ⚠️ 2026-09-26 修正（实测假阳性事故 —— 原判据不可靠）
    原判据只有一条：`cookie 出现 sessionid / sid_tt ⇒ 已登录`。
    实测（张老师扫码流程）该判据**严重假阳性**：
      · 浏览器启动时会**自动带上上一会话的 cookie**（profile 复用），
        其中本就含有 sessionid ⇒ 未做任何登录动作也判 "ok=True"；
      · 实测未扫码即报 `ok=True, cookies=75`，而抖音官方 passport 接口
        同时返回 `{"error_code":13,"description":"会话过期，请重新登录"}`
        ⇒ 结论完全相反（违反 Live-Instance Verification）。
    ## 现判据（两级，缺一不可）
      ① 本地：cookie 出现登录标识（必要条件，不充分）
      ② 服务端：`passport/account/info/v2` 返回 `user_id>0` 且 `error_code` 为空
    ② 未通过即判失败 —— 因为「cookie 存在」不能证明「会话有效」。

    `verify_server=False` 仅供离线单测使用，生产路径**必须**为 True。
    """
    context = handle.get("context")
    if context is None:
        return {"ok": False, "reason": "handle 无效"}
    t0 = time.time()
    last_reason = ""
    while time.time() - t0 < timeout_s:
        try:
            cookies = {c["name"]: c["value"] for c in await context.cookies()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"读 cookie 失败: {e}"}
        # ① 本地必要条件
        if cookies.get("sessionid") or cookies.get("sid_tt"):
            if not verify_server:
                return {"ok": True, "cookies": cookies, "reason": "（未做服务端确认）"}
            # ② 服务端权威判据
            sv = await _probe_session_valid_by_cookies(cookies)
            if sv.get("ok"):
                return {"ok": True, "cookies": cookies,
                        "uid": sv.get("uid"), "reason": ""}
            last_reason = f"本地有 cookie 但服务端未确认: {sv.get('reason')}"
            logger.warning("[login_remote] {}", last_reason)
        await asyncio.sleep(interval_s)
    return {"ok": False,
            "reason": last_reason or f"等 {timeout_s}s 未见有效登录（含服务端确认）"}


async def _probe_session_valid_by_cookies(cookies: dict) -> dict:
    """用抖音官方 passport 接口判定会话是否真的有效。

    为什么必须用它（2026-09-26 实测）：
      · 本地 cookie 齐全（8/8 登录标识）+ UI 显示已登录，
        但官方返回 `{"error_code":13,"description":"会话过期，请重新登录"}`
      · `query/user` 接口**不能**替代：它容忍陈旧会话，会返回历史 uid
        （配置里「查询自身 uid 的接口是 query/user」这一事实本身没错，
         但它**不用于**判定会话是否有效）。
    """
    import httpx
    try:
        if not cookies:
            return {"ok": False, "reason": "无 cookie"}
        cs = "; ".join(f"{k}={v}" for k, v in cookies.items())
        hdrs = {
            "Cookie": cs,
            # ★ 2026-09-26（ADR-016 D4 参数层收口 / H-22 审计外发现）：
            #   原硬编码 Chrome/131 UA，而真实内核是 Camoufox(Firefox 152)
            #   ⇒ 服务端看到「UID 来自 Firefox 会话、探活却自称 Chrome」的身份矛盾，
            #   可能据此误判**会话失效**（正是 H-27「凭证频繁失效」的同族形态）。
            #   统一走档案 UA（与全部出站请求同一身份，零硬编码）。
            # ★ 2026-10-01 修正：原写 `get_profile()["user_agent"]` —— 该键**不存在**
            #   （档案真键名是 `ua`；全仓既有 5 处均走 `utils.fingerprint.user_agent()`），
            #   实测必然抛 `KeyError: 'user_agent'`（真机日志 [ACC-046]），导致本函数
            #   **永远探活失败** ⇒ 凭证校验恒不过。此处改用**既有单一真源** user_agent()。
            "User-Agent": user_agent(),
            "Referer": "https://www.douyin.com/",
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as cli:
            r = await cli.get("https://www.douyin.com/passport/account/info/v2/",
                              headers=hdrs)
        d = (r.json().get("data") or {})
        uid = d.get("user_id") or 0
        ec = d.get("error_code")
        if int(uid or 0) > 0 and not ec:
            return {"ok": True, "uid": uid, "reason": ""}
        return {"ok": False,
                "reason": f"error_code={ec} {d.get('description', '')!r}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"探活异常 {type(e).__name__}: {str(e)[:120]}"}


async def fill_phone(page, phone: str) -> bool:
    """填手机号（逐字真实按键，避免 fill 的 JS 直赋值特征）。

    兜底（2026-09-29）：稳定锚点落空时用**自适应定位**找回输入框。原判据优先。
    """
    loc = page.locator(SEL_PHONE_INPUT).first
    if await loc.count() == 0:
        xp = await _adaptive_xpath(page, "input_phone")
        if xp:
            loc = page.locator(xp).first
            logger.info("[login_remote] 手机号框稳定锚点落空 → 自适应重定位命中")
        else:
            logger.error("[login_remote] 未找到手机号输入框 {}", SEL_PHONE_INPUT)
            return False
    await loc.click()
    await page.keyboard.type(phone, delay=60)
    got = await loc.input_value()
    # 手机号框可能带 formatter（空格分组），比对去分隔符后的数字
    ok = got.replace(" ", "").replace("-", "") == phone.replace(" ", "").replace("-", "")
    logger.info("[login_remote] 手机号填入 {}（读回长度 {}）", "成功" if ok else "异常", len(got))
    return ok


async def fill_code(page, code: str) -> bool:
    """填验证码（**逐字真实按键**）。

    ⚠️ 实测：`Ctrl+V` 粘贴在 Camoufox headless 下**整体不可用**（连阳性对照
    手机号框都失败），故采用 `keyboard.type` —— 真实按键序列，最接近真人。

    兜底（2026-09-29）：稳定锚点落空时用**自适应定位**找回输入框。原判据优先。
    """
    loc = page.locator(SEL_CODE_INPUT).first
    if await loc.count() == 0:
        xp = await _adaptive_xpath(page, "input_code")
        if xp:
            loc = page.locator(xp).first
            logger.info("[login_remote] 验证码框稳定锚点落空 → 自适应重定位命中")
        else:
            logger.error("[login_remote] 未找到验证码输入框 {}", SEL_CODE_INPUT)
            return False
    await loc.click()
    await page.keyboard.type(code, delay=80)
    got = await loc.input_value()
    ok = got.strip() == code.strip()
    # 注意：**不打印验证码本身**（安全契约 I3/C3）
    logger.info("[login_remote] 验证码填入 {}（读回长度 {}）", "成功" if ok else "异常", len(got))
    return ok


# ══════════════════════════════════════════════════════════════════════
#  编排入口：生成二维码（供 IM 推送）
# ══════════════════════════════════════════════════════════════════════

async def prepare_qr_login(env_path: str, out_png: str, headless: bool = True,
                           timeout_s: int = 90) -> dict:
    """启动浏览器 → 打开登录页 → 抓二维码 → **保持浏览器打开**（供用户扫码）。

    ⚠️ 调用方责任：本函数**不关闭浏览器**，因为用户需要时间扫码。
    调用方须在扫码完成后调用 `finish_qr_login()` 或自行清理
    （否则 profile 会被长期占用）。

    返回 {'ok', 'png', 'decoded', 'handle': <内部句柄>, 'reason'}
      handle = {'pw','browser','context','backend','env_path','profile'}
    """
    from auto_dm import accounts as _acc
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import launch_async, should_use_vb

    profile = _acc.profile_dir_of(env_path)
    os.makedirs(profile, exist_ok=True)
    # E1：启动前自愈 —— **先清扫残留进程，再判/清陈旧锁**（顺序是契约，见
    # `ensure_profile_released` 文档：顺序颠倒会让锁的自愈一次都不生效）。
    heal = ensure_profile_released(profile)

    _vb, mode = should_use_vb(_cfg)
    acc_name = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
    pw, browser, context, backend = await launch_async(
        mode, _cfg, headless=headless, force=False,
        account=acc_name, user_data_dir=profile)
    handle = {"pw": pw, "browser": browser, "context": context,
              "backend": backend, "env_path": env_path, "profile": profile}
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.set_viewport_size({"width": 1600, "height": 1000})
        await page.goto(LOGIN_URL, wait_until="domcontentloaded",
                        timeout=max(30, timeout_s) * 1000)
        await asyncio.sleep(4)
        clicked = await click_by_text(page, TAB_SCAN)
        logger.info("[login_remote] 点「{}」: {}", TAB_SCAN, "成功" if clicked else "未找到（可能默认已在此 tab）")
        await asyncio.sleep(5)
        res = await grab_login_qrcode(page, out_png)
        res["handle"] = handle
        res["heal"] = heal
        # 出码成功 → 通过 IM 通道把二维码推给用户（用户不在电脑旁也能扫码）。
        # 用途：远程更新抖音凭证（ADR-017）。推送失败绝不影响出码结果本身。
        #
        # ⚠️ **L-15（2026-09-26）：只发【单条图片】，不发正文文本。**
        # 依据：iLink 官方规范「每 10 条外发需一次新入站」——文本+图片=2 条会吃掉
        # 1/5 额度，且文本条失败时图片仍发出会造成体验割裂。故把关键信息折进
        # `caption`，由渠道层在【图片消息内】携带（不额外占用一条外发）。
        if res.get("ok") and res.get("png"):
            try:
                from notify.notifier import notifier as _notifier

                _acct = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
                _notifier.emit(
                    "login_qrcode",
                    "抖音扫码登录",
                    "",  # 正文留空 ⇒ 不单独发文本（L-15）
                    level="critical",
                    dedup_key=f"login_qrcode:{out_png}",
                    image_path=res["png"],
                    image_caption=f"账号「{_acct}」需扫码登录，二维码约 60 秒内有效。",
                )
                logger.info("[login_remote] 二维码已提交 IM 推送（单条图片）: {}", res["png"])
            except Exception as e:  # noqa: BLE001
                logger.warning("[login_remote] 二维码 IM 推送失败（不影响出码）: {}", e)
        return res
    except Exception as e:  # noqa: BLE001
        logger.error("[login_remote] 二维码流程异常: {}", e)
        # 异常时必须清理，避免锁残留
        await close_handle(handle)
        return {"ok": False, "png": None, "decoded": None, "box": None,
                "reason": f"{type(e).__name__}: {e}", "handle": None, "heal": heal}


# ══════════════════════════════════════════════════════════════════════
#  接口桥（Bridge）：无头浏览器里「页面自己发接口」→ 我们**只被动监听**
# ══════════════════════════════════════════════════════════════════════
#  【设计来源】用户 2026-09-30 指定：
#    「先起个无头浏览器，然后把 API 使用的方法在无头浏览器上面进行监测……
#      相当于一个桥梁，把浏览器上面的接口暴露给前端」。
#  【实测依据】（2026-09-30 真机，bridge_probe2）
#    · 无头 Camoufox + 深链 `?modal_id=login` ⇒ 页面**自己**发 `get_qrcode`
#      （响应体含 token / qrcode_index_url / error_code=0），并**自维持**
#      `check_qrconnect` 轮询（计数 2→3→5→7）。
#    · 面板约 30s 挂载（早采样会误判"面板不存在"）。
#    · 首页 + 点「登录」**不发**这些接口 ⇒ 入口必须是深链。
#  【为什么优于 DOM 截图】二维码数据来自**网络响应**（原始 URL），
#    不依赖元素锚点/像素解码 ⇒ 对改版免疫；且**同一 Firefox 身份**，
#    无 API 纯协议路径的「Chrome ⊗ Firefox」跨信号矛盾。
#  【关键判据】页面**自己**发请求 = 与真人在浏览器里操作等价，
#    不存在 API 路径那种「缺 x-tt-session-dtrait / 身份不一致」的问题。

_BRIDGE_QR_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;display:flex;align-items:center;justify-content:center;background:#fff}
img{width:100%;height:100%;object-fit:contain}</style></head>
<body><img src="%s" alt="qr"></body></html>"""


def _render_qr_png(url: str, out_png: str, box_size: int = 10) -> bool:
    """把二维码 URL 渲染成 PNG。返回是否成功。

    `qrcode` / `PIL` 已随 sidecar 打包（build_sidecar `--hidden-import qrcode.image.pil`）。
    """
    try:
        import qrcode
        qr = qrcode.QRCode(border=2, box_size=int(box_size))
        qr.add_data(url)
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(out_png)
        return os.path.getsize(out_png) > 0
    except Exception as e:  # noqa: BLE001
        logger.warning("[bridge] 二维码渲染失败（url 仍可用）: {}", e)
        return False


async def bridge_qr_login(env_path: str, out_png: str, headless: bool = True,
                          timeout_s: int = 90) -> dict:
    """**接口桥**：起无头浏览器 → 被动监听页面自己发的登录接口 → 出码。

    与 `prepare_qr_login` 的区别：
      · 不点 DOM tab、**不截图猜二维码**；二维码来自 `get_qrcode` 的**响应体**。
      · 二维码**渲染成 PNG**（前端展示需要，且兼容既有 `qrPng` 契约），
        同时把 `qr_url` 一并返回（前端/IM 也可直接用 url）。

    返回 {'ok','png','decoded','qr_url','token','handle','reason','via'}
      via = "bridge"（接口桥）| "bridge-url-only"（拿到 url 但渲染失败）| ...
    """
    from auto_dm import accounts as _acc
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import launch_async, should_use_vb

    profile = _acc.profile_dir_of(env_path)
    os.makedirs(profile, exist_ok=True)
    heal = ensure_profile_released(profile)
    _vb, mode = should_use_vb(_cfg)
    acc_name = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
    pw, browser, context, backend = await launch_async(
        mode, _cfg, headless=headless, force=False,
        account=acc_name, user_data_dir=profile)
    handle = {"pw": pw, "browser": browser, "context": context,
              "backend": backend, "env_path": env_path, "profile": profile}
    caps: dict = {}
    lock = asyncio.Lock()

    async def _on_response(resp):
        try:
            u = resp.url or ""
        except Exception:  # noqa: BLE001
            return
        if "get_qrcode" in u:
            try:
                body = await resp.text()
                import json as _json
                data = (_json.loads(body).get("data") or {})
                async with lock:
                    caps["token"] = data.get("token")
                    caps["qr_url"] = (data.get("qrcode_index_url")
                                      or data.get("qrcode_url"))
                    caps["error_code"] = data.get("error_code")
                logger.info("[bridge] 截获 get_qrcode：token={} error_code={}",
                            str(caps.get("token"))[:18], caps.get("error_code"))
            except Exception as e:  # noqa: BLE001
                logger.debug("[bridge] 解析 get_qrcode 响应失败: {}", e)

    try:
        context.on("response", lambda r: asyncio.create_task(_on_response(r)))
        # ★ 2026-10-01 根治「桥永不出码」：清掉**陈旧登录态** cookie，让登录弹窗重新出现。
        #   为什么要清：桥复用账号固定 profile（保设备指纹），但该 profile 常残留
        #   **已过期**的 sessionid/sid_tt ⇒ 抖音视作「已登录」⇒ **不弹登录框** ⇒
        #   页面**不发 `get_qrcode`** ⇒ 桥必然超时（真机实测 [ACC-044] 90s 未截获）。
        #   项目自身 `force=True` 亦**不清 cookie**（`login_api.py:579` 注释自承
        #   「残留旧登录态导致没出现登录按钮」），故此处显式清理。
        #   做法：**全量取回 → 清空 → 回填「除登录态以外」的全部 cookie**
        #   ⇒ 设备/风控 cookie（ttwid / s_v_web_id / msToken / __ac_*）逐字保留，
        #     只丢登录态（后者是**过期**的，留着只会阻止出码）。
        _LOGIN_STATE_COOKIES = {
            "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "uid_tt_ss",
            "sso_uid_tt", "sso_uid_tt_ss", "passport_csrf_token",
            "passport_csrf_token_default", "sid_ucp_v1", "ssid_ucp_v1", "d_ticket",
        }
        try:
            _before = await context.cookies()
            _login_names = [c["name"] for c in _before if c["name"] in _LOGIN_STATE_COOKIES]
            if _login_names:
                _keep = []
                for _c in _before:
                    if _c["name"] in _LOGIN_STATE_COOKIES:
                        continue
                    _keep.append({k: _c[k] for k in (
                        "name", "value", "domain", "path", "expires",
                        "httpOnly", "secure", "sameSite") if k in _c})
                await context.clear_cookies()
                if _keep:
                    await context.add_cookies(_keep)
                logger.info("[bridge] 已清陈旧登录态 cookie {} 个（保留设备/风控 cookie {} 个）: {}",
                            len(_login_names), len(_keep), _login_names)
        except Exception as _e_clr:  # noqa: BLE001
            logger.warning("[bridge] 清登录态 cookie 失败（不阻断，继续尝试出码）: {}", _e_clr)
        page = context.pages[0] if context.pages else await context.new_page()
        # ★ 2026-10-01：**不再硬编码 1600x1000** —— launch_async 已按账号档案设好
        #   viewport（`_viewport_for_account`），此处覆盖会【破坏指纹单源化】
        #   （errcode_data.py:663 列为错误码根因；services/env_audit 会报 screen 不一致）。
        #   档案视口是账号级稳定值 ⇒ 跨设备一致性由档案保证，不由本函数决定。

        await page.goto(LOGIN_URL, wait_until="domcontentloaded",
                        timeout=max(30, timeout_s) * 1000)
        # ★ 2026-10-01 根因修复：**必须用真实鼠标点击「登录」按钮才会发 get_qrcode**。
        #   实测三种做法对照：
        #     · 只 goto 登录页 ⇒ 被重定向到 /jingxuan，登录弹窗不挂载 ⇒ 永不出码；
        #     · JS 合成 `el.click()` ⇒ 返回 clicked 但**不发** get_qrcode（抖音忽略合成事件）；
        #     · **真实鼠标** `page.mouse.click(x,y)` ⇒ 立刻截获 get_qrcode + 二维码容器 ✅
        #   先给页面一点挂载时间，再（可重试地）点。
        async def _try_click_login(max_wait: float = 25.0) -> str:
            """点「登录」——**稳定 id 优先**，自适应/文本兜底（消除坐标脆弱性）。

            2026-10-01（上游情报 + 用户质疑「分辨率变了 / 跨设备怎么办」）：
              旧做法 = 全页文本匹配 + getBoundingClientRect 算屏幕坐标 ⇒
              分辨率/断点/改版一变即点空（重试只救抖动、不救结构变化）。
              按 ADR-034 §7-3「改版免疫的定位顺序：确定性接口/入口 → 稳定 id →
              容器栅格化 → 文本兜底」，改用仓库**已配好**的稳定 id：
                `div[id="douyin_login_comp_btn_id"]`（dom_locator.py:214 btn_submit）
              三级策略全部走**真实鼠标**（抖音忽略 JS 合成 click —— 实测结论）。
            """
            _t = time.time()
            while time.time() - _t < max_wait:
                _box = None
                # (a) 稳定 id —— 最优先
                try:
                    _loc = page.locator(SEL_ONE_CLICK_BTN).first
                    if await _loc.count() > 0 and await _loc.is_visible():
                        _bb = await _loc.bounding_box()
                        if _bb and _bb.get("width", 0) >= 8 and _bb.get("height", 0) >= 8:
                            _box = {"x": int(_bb["x"] + _bb["width"] / 2),
                                    "y": int(_bb["y"] + _bb["height"] / 2),
                                    "how": "stable-id"}
                except Exception:  # noqa: BLE001
                    _box = None
                # (b) 自适应 XPath（dom_locator，仓库既有兜底能力）
                if _box is None:
                    try:
                        _xp = await _adaptive_xpath(page, "btn_submit")
                        if _xp:
                            _loc2 = page.locator(f"xpath={_xp}").first
                            if await _loc2.count() > 0:
                                _bb2 = await _loc2.bounding_box()
                                if _bb2:
                                    _box = {"x": int(_bb2["x"] + _bb2["width"] / 2),
                                            "y": int(_bb2["y"] + _bb2["height"] / 2),
                                            "how": "adaptive-xpath"}
                    except Exception:  # noqa: BLE001
                        _box = None
                # (c) 文本兜底（最后）
                if _box is None:
                    try:
                        _box = await page.evaluate(
                            "() => {"
                            "const n = Array.from(document.querySelectorAll('button,div,span,a'))"
                            ".find(x => (x.textContent||'').trim().replace(/\\s/g,'') === '登录');"
                            "if (!n) return null;"
                            "const r = n.getBoundingClientRect();"
                            "if (r.width < 8 || r.height < 8) return null;"
                            "return {x: Math.round(r.x + r.width/2),"
                            " y: Math.round(r.y + r.height/2), how: 'text'};}")
                    except Exception:  # noqa: BLE001
                        _box = None
                if _box:
                    try:
                        # 真实鼠标：先移动（带轨迹），再点击
                        await page.mouse.move(_box["x"] - 40, _box["y"] - 25)
                        await asyncio.sleep(0.35)
                        await page.mouse.move(_box["x"], _box["y"])
                        await asyncio.sleep(0.22)
                        await page.mouse.click(_box["x"], _box["y"])
                        logger.info("[bridge] 已真实点击「登录」按钮（{},{}） via={}",
                                    _box["x"], _box["y"], _box.get("how", "?"))
                        return "clicked"
                    except Exception as _e_clk:  # noqa: BLE001
                        logger.debug("[bridge] 点击登录按钮异常: {}", _e_clk)
                await asyncio.sleep(2.0)
            return "not-found"

        # 点击可能因布局未稳/动画偏移落空 ⇒ **点击 → 校验是否出码 → 未出再点**（最多 3 轮）
        _clk = "not-found"
        for _round in range(3):
            _clk = await _try_click_login(max_wait=(18.0 if _round == 0 else 8.0))
            if _clk != "clicked":
                break
            # 给前端一点时间发 get_qrcode
            for _i in range(6):
                await asyncio.sleep(1.0)
                if caps.get("qr_url"):
                    break
            if caps.get("qr_url"):
                break
            logger.info("[bridge] 第 {} 次点击后仍未出码，重试点击（布局可能尚未稳定）",
                        _round + 1)
        if _clk != "clicked":
            logger.warning("[bridge] 未找到「登录」按钮（页面可能已登录或结构已变），"
                           "继续等待页面自发接口")
        # 轮询等待：页面自己发接口（实测约 30~40s；面板 30s 才挂载）
        t0 = time.time()
        while time.time() - t0 < max(30, timeout_s):
            qr_url = caps.get("qr_url")
            if qr_url:
                rendered = _render_qr_png(qr_url, out_png)
                if rendered:
                    logger.info("[bridge] 二维码已由接口响应渲染（token={} 等 {:.1f}s）",
                                str(caps.get("token"))[:18], time.time() - t0)
                else:
                    logger.warning("[bridge] 二维码渲染失败，仅返回 url（前端可自行渲染）")
                # ★ 强引用已在 vbrowser_camoufox.launch_camoufox_async 创建点统一登记
                #   （见 _keep_camoufox_alive），此处无需重复；等待期间 context 保持有效。
                return {"ok": True, "png": (out_png if rendered else None),
                        "decoded": bool(caps.get("token")),
                        "qr_url": qr_url, "token": caps.get("token"),
                        "handle": handle,
                        "reason": "" if rendered else "渲染失败，url 可用",
                        "via": "bridge" if rendered else "bridge-url-only",
                        "waited_s": round(time.time() - t0, 1), "heal": heal}
            if caps.get("error_code") not in (None, 0):
                return {"ok": False, "png": None, "decoded": None, "qr_url": None,
                        "token": None, "handle": handle, "heal": heal,
                        "reason": f"get_qrcode error_code={caps.get('error_code')}",
                        "via": "bridge"}
            await asyncio.sleep(1.0)
        # 超时：如实返回（不静默）
        return {"ok": False, "png": None, "decoded": None, "qr_url": None,
                "token": None, "handle": handle, "heal": heal, "via": "bridge",
                "reason": f"桥超时（{timeout_s}s 内未截获 get_qrcode 响应；"
                          f"面板/DOM 不参与判据）"}
    except Exception as e:  # noqa: BLE001
        logger.error("[bridge] 接口桥异常: {}", e)
        await close_handle(handle)
        return {"ok": False, "png": None, "decoded": None, "qr_url": None,
                "token": None, "handle": None, "heal": heal, "via": "bridge",
                "reason": f"{type(e).__name__}: {e}"}


async def poll_bridge_confirmed(handle: dict, timeout_s: int = 240,
                                interval_s: int = 3) -> dict:
    """轮询「扫码是否确认成功」——**被动**观察页面自己发的 `check_qrconnect`。

    ★ 2026-09-30 修正（用户实测报障「没扫码却显示凭证已读回/拿到的是过期凭证」）：
      原兜底判据是「context cookie 出现 `sessionid`/`sid_tt`」= **本地 cookie 存在**。
      但铁律是「**本地 cookie 存在 ≠ 会话有效**」（案例 rpa-sms-login-three-bugs §3）：
      账号**陈旧**的 sessionid 一直在 profile/cookie 里 ⇒ 旧判据会**立刻假成功**，
      把过期凭证当新凭证落盘。这正是用户看到的现象。

    现判据（**服务端权威**，与 `_probe_session_valid_by_cookies` 同一真源）：
      ① 截获 `check_qrconnect` 响应 `data.status == 'confirmed'`（页面自维持）；且
      ② 官方 `passport/account/info/v2` → `user_id>0 且无 error_code`（会话真有效）。
      两次探活只在 **cookie 签名变化** 时触发（不空转打服务端）。
    返回 {'ok','cookies','status','risk','reason','uid'}
    """
    context = handle.get("context")
    if context is None:
        return {"ok": False, "reason": "handle 无效"}
    state: dict = {"status": None, "confirmed": False}

    async def _on_response(resp):
        try:
            u = resp.url or ""
        except Exception:  # noqa: BLE001
            return
        if "check_qrconnect" in u:
            try:
                body = await resp.text()
                import json as _json
                st = (_json.loads(body).get("data") or {}).get("status")
                state["status"] = st
                if st == "confirmed":
                    state["confirmed"] = True
                    logger.info("[bridge] 页面自报扫码已确认（check_qrconnect=confirmed）")
            except Exception:  # noqa: BLE001
                pass

    try:
        context.on("response", lambda r: asyncio.create_task(_on_response(r)))
    except Exception as e:  # noqa: BLE001
        logger.debug("[bridge] 挂 check_qrconnect 监听失败: {}", e)
    t0 = time.time()
    seen_sig = ""          # 上次探活时的 cookie 签名（值变化才重探，避免空转）
    srv = {"ok": False, "uid": "", "reason": ""}
    while time.time() - t0 < timeout_s:
        try:
            cookies = {c["name"]: c["value"] for c in await context.cookies()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"读取 cookie 失败: {e}"}
        sig = f"{cookies.get('sessionid', '')}|{cookies.get('sid_tt', '')}"
        # 仅在「页面自报 confirmed」或「cookie 签名变化」时做服务端探活
        if (state["confirmed"] or sig != "|") and sig != seen_sig:
            seen_sig = sig
            try:
                sv = await _probe_session_valid_by_cookies(cookies)
            except Exception as e:  # noqa: BLE001
                sv = {"ok": False, "reason": f"探活异常 {type(e).__name__}: {e}"}
            srv = {"ok": bool(sv.get("ok")), "uid": str(sv.get("uid") or ""),
                   "reason": sv.get("reason") or ""}
            if srv["ok"]:
                logger.info("[bridge] 服务端已确认会话有效（uid={}）", srv["uid"])
            else:
                logger.info("[bridge] 本地 cookie 存在但服务端未确认（{}），继续等待真实扫码",
                            srv["reason"] or "未知")
        if srv["ok"]:
            return {"ok": True, "cookies": cookies, "status": state["status"],
                    "risk": False, "reason": "", "uid": srv["uid"]}
        await asyncio.sleep(interval_s)
    return {"ok": False, "cookies": {}, "status": state["status"], "risk": False,
            "uid": "", "reason": f"等待确认超时（{timeout_s}s，最后 status={state['status']}；"
                                 f"服务端未确认：{srv['reason'] or '未取得有效会话'}）"}


def risk_keywords() -> tuple:
    """风控页判据关键词 —— **复用既有 SSOT，禁止在此另立一份**。

    2026-09-26（ADR-017 F6）：`login_remote.py` 原先对风控**完全无检测**
    （grep「安全风险/风控」零命中）。正确做法不是新造一份关键词表，
    而是复用 `dy_apis/login_api.py::RiskControlError.KEYWORDS`（既有 SSOT），
    保证两条登录路径（API 路径 / RPA 路径）**同一判据**，不会各判各的。
    """
    try:
        from dy_apis.login_api import RiskControlError
        return tuple(RiskControlError.KEYWORDS)
    except Exception:  # noqa: BLE001
        # 导入失败时**显式降级为空**并告警 —— 绝不静默编造一份关键词。
        logger.warning("[login_remote] 风控 SSOT 导入失败，F6 检测降级为不命中")
        return ()


async def detect_risk_control(page) -> dict:
    """F6：检测当前页是否为抖音风控/验证码页（RPA 路径）。

    **策略沿用既有**（`login_api.py:470` 2026-08-17 修订，实测有效）：
      · 命中 ⇒ **绝不关闭浏览器、绝不立即 raise**（用户要在里面手动过验证）
      · 只告警一次（防刷屏）
      · 调用方继续轮询；用户处理完页面离开风控页即自动恢复

    返回 {'hit': bool, 'url': str}
    """
    kws = risk_keywords()
    if not kws or page is None:
        return {"hit": False, "url": ""}
    try:
        url = await page.evaluate("location.href")
        html = await page.evaluate(
            "document.documentElement.outerHTML.slice(0, 4000)")
    except Exception:  # noqa: BLE001
        return {"hit": False, "url": ""}
    hit = any(k in ((url or "") + (html or "")) for k in kws)
    return {"hit": hit, "url": url or ""}


async def poll_qr_scanned(handle: dict, timeout_s: int = 240,
                          interval_s: int = 3) -> dict:
    """轮询「用户是否已扫码登录成功」。

    判据（**服务端权威**，与 `poll_bridge_confirmed` 同一真源）：
      ① cookie 中出现真实登录标识（sessionid / sid_tt）**且**服务端确认会话有效；
         ——★ 2026-09-30：**本地 cookie 存在 ≠ 会话有效**（案例 rpa-sms-login-three-bugs §3）。
         账号**陈旧**的 sessionid 一直在 profile 里 ⇒ 旧判据（只看存在）会**立刻假成功**，
         把过期凭证当新凭证落盘（用户实测报障的「没扫码却显示凭证已读回」）。
      ② 页面离开登录弹窗 —— 仅作**辅助**，不再单独作为成功判据。

    ⚠️ 2026-09-26（ADR-017 F6）：循环中**实时检测风控页**（见 `detect_risk_control`）。
       命中只告警一次并把 `risk` 标记写入返回，由调用方决定是否继续等。
    """
    context = handle.get("context")
    if context is None:
        return {"ok": False, "reason": "handle 无效"}
    t0 = time.time()
    _risk_notified = False          # F6：风控告警只打一次，防刷屏
    _risk_seen = False
    _seen_sig = ""                  # 上次探活时的 cookie 签名（值变化才重探，避免空转）
    _srv_reason = ""
    while time.time() - t0 < timeout_s:
        # ── F6 风控实时检测（ADR-017）──────────────────────────────
        # 用 context.pages[0] 取当前页（handle 未携带 page，避免改能力层契约）。
        # 命中 ⇒ **不关浏览器、不中断**，只告警一次并继续等用户手动过验证
        # （策略沿用 login_api.py:470 既有实测结论）。
        try:
            _pg = context.pages[0] if context.pages else None
        except Exception:  # noqa: BLE001
            _pg = None
        if _pg is not None:
            rk = await detect_risk_control(_pg)
            if rk.get("hit"):
                _risk_seen = True
                if not _risk_notified:
                    logger.warning(
                        "[AUTH-080] [风控] RPA 路径检测到验证码/风控页（{}）。"
                        "【浏览器保持打开】，请在其中手动完成验证；"
                        "完成后页面离开风控页，本流程将自动继续。", rk.get("url"))
                    _risk_notified = True
        try:
            cookies = {c["name"]: c["value"] for c in await context.cookies()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"读取 cookie 失败: {e}",
                    "risk": _risk_seen}
        # ★ 服务端确认才判成功（cookie 存在仅触发一次探活，不作为成功依据）
        sig = f"{cookies.get('sessionid', '')}|{cookies.get('sid_tt', '')}"
        if sig != "|" and sig != _seen_sig:
            _seen_sig = sig
            try:
                sv = await _probe_session_valid_by_cookies(cookies)
            except Exception as e:  # noqa: BLE001
                sv = {"ok": False, "reason": f"探活异常 {type(e).__name__}: {e}"}
            _srv_reason = sv.get("reason") or ""
            if sv.get("ok"):
                logger.info("[login_remote] 服务端已确认登录态有效（uid={}）",
                            sv.get("uid"))
                return {"ok": True, "cookies": cookies, "reason": "",
                        "risk": _risk_seen}
            logger.info("[login_remote] 本地 cookie 存在但服务端未确认（{}），继续等待真实扫码",
                        _srv_reason or "未知")
        await asyncio.sleep(interval_s)
    return {"ok": False, "reason": f"等待扫码超时（{timeout_s}s；服务端未确认："
                                   f"{_srv_reason or '未取得有效会话'}）",
            "risk": _risk_seen}


async def do_sms_login(env_path: str, phone: str, code_provider,
                       headless: bool = True, timeout_s: int = 90) -> dict:
    """短信验证码登录**端到端编排**（RPA）。

    ## 流程（每一步都有真机实测判据，见各函数 docstring）
    ```
    ① prepare_sms_login  → 启动浏览器 + 打开登录页 + 填手机号
    ② click_send_code    → 等按钮启用 → 真鼠标点击 → 识别 sent/rate_limited
    ③ code_provider()    → 取验证码（**由调用方提供**，本模块不接触明文渠道）
    ④ fill_code          → 逐字键入验证码
    ⑤ click_login        → 点登录
    ⑥ detect_login_result→ 硬判据：cookie 出现 sessionid / sid_tt
    ```

    ## 参数
    - `code_provider`: 无参可调用对象（sync/async 均可），返回验证码字符串。
      **本模块只负责调用它**，不关心验证码从哪来（IM 回执 / Web 前端 / 人工）。
      这样把「验证码获取渠道」与「RPA 操作」解耦（SoC）。
    - 返回 {'ok', 'stage', 'reason', 'handle', 'outcome', ...}
      - 失败时 `stage` 指出卡在哪一步（便于定位与告知用户）
      - **成功时 handle 不关闭**（调用方需落凭证后自行 close_handle）
    """
    from auto_dm import accounts as _acc
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import launch_async, should_use_vb

    profile = _acc.profile_dir_of(env_path)
    os.makedirs(profile, exist_ok=True)
    # E1：启动前自愈 —— 先清扫残留进程，再判/清陈旧锁（顺序契约同 QR 路径）。
    heal = ensure_profile_released(profile)
    _vb, mode = should_use_vb(_cfg)
    acc_name = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
    pw, browser, context, backend = await launch_async(
        mode, _cfg, headless=headless, force=False,
        account=acc_name, user_data_dir=profile)
    handle = {"pw": pw, "browser": browser, "context": context,
              "backend": backend, "env_path": env_path, "profile": profile}

    def _fail(stage, reason):
        logger.error("[login_remote] 短信登录失败于 {}: {}", stage, reason)
        return {"ok": False, "stage": stage, "reason": reason,
                "handle": handle, "heal": heal}

    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.set_viewport_size({"width": 1600, "height": 1000})
        await page.goto(LOGIN_URL, wait_until="domcontentloaded",
                        timeout=max(30, timeout_s) * 1000)
        await asyncio.sleep(5)

        # ⓪ 界面分流（★ 2026-09-30 修根因）
        # profile 记住账号时，抖音弹的是「一键登录」面板 —— 手机号框
        # `input[name="normal-input"]` **根本不存在**，直接 fill_phone 必失败。
        # 实证：本函数此前**从不调用** `handle_one_click_login`（全仓零调用点），
        # 短信路径 100% 卡在 fill_phone（运行日志：
        # `短信登录失败于 fill_phone: 手机号填入失败（input[name="normal-input"]）`）。
        oc = await handle_one_click_login(page)
        if not oc.get("form"):
            # 仍未见到表单 ⇒ 用**稳定 id** 点「验证码登录」tab
            # （两个 tab 的真实节点是哈希类名 `C6OZQwMA`，不可作锚点；
            #   其父容器 id 实测稳定，故点 id。）
            try:
                if await page.locator(SEL_TAB_SMS_BOX).count() > 0:
                    await _mouse_click_locator(page, page.locator(SEL_TAB_SMS_BOX).first)
                    logger.info("[login_remote] 已点「验证码登录」tab（稳定 id {}）", SEL_TAB_SMS_BOX)
                    await asyncio.sleep(1.2)
            except Exception as e:  # noqa: BLE001
                logger.debug("[login_remote] 点「验证码登录」tab 失败: {}", e)
        logger.info("[login_remote] 界面分流结果：one_click={} switched={} form={}",
                    oc.get("one_click"), oc.get("switched"), oc.get("form"))

        # ① 填手机号
        if not await fill_phone(page, phone):
            return _fail("fill_phone", f"手机号填入失败（{SEL_PHONE_INPUT}）")
        await asyncio.sleep(1.5)

        # ② 点获取验证码
        send = await click_send_code(page)
        if send.get("outcome") == "rate_limited":
            return _fail("send_code", "验证码发送太频繁，请稍后再试")
        if not send.get("ok"):
            return _fail("send_code", f"获取验证码失败：{send.get('reason')}")

        # ③ 取验证码（渠道由调用方决定）
        #   ★ 2026-10-01 修复死锁：同步 provider（如 `threading.Event.wait(300)`）
        #     若在事件循环内直调，会**阻塞整个 loop 300s** ⇒ Playwright 传输停摆
        #     （页面卡死/context 被关），且同一循环上的 `/sms-code` 端点无法被调度
        #     ⇒ 用户提交的验证码永远送不进来（死锁）。故同步 provider 一律放 worker 线程。
        #     async provider 仍按原语义直接 await（行为不变）。
        import inspect
        if inspect.iscoroutinefunction(code_provider):
            code = await code_provider()
        else:
            code = await asyncio.to_thread(code_provider)
            if inspect.isawaitable(code):
                code = await code
        code = (code or "").strip()
        if not code:
            return _fail("get_code", "调用方未提供验证码")
        if not code.isdigit() or len(code) != 6:
            return _fail("get_code", f"验证码格式非法（应为 6 位数字，实得 {len(code)} 位）")

        # ④ 填验证码
        if not await fill_code(page, code):   # 注意：fill_code 内部不打印验证码
            return _fail("fill_code", f"验证码填入失败（{SEL_CODE_INPUT}）")
        await asyncio.sleep(1.0)

        # ⑤ 点登录
        lg = await click_login(page)
        if not lg.get("ok"):
            return _fail("click_login", f"点登录失败：{lg.get('reason')}")

        # ⑥ 硬判据
        det = await detect_login_result(handle, timeout_s=25)
        if not det.get("ok"):
            return _fail("verify", det.get("reason", "未检测到登录态"))
        logger.info("[login_remote] ✅ 短信登录成功（cookie 已含登录标识）")
        return {"ok": True, "stage": "done", "reason": "", "handle": handle,
                "heal": heal, "cookies": det.get("cookies")}
    except Exception as e:  # noqa: BLE001
        import traceback
        logger.error("[login_remote] 短信登录异常: {}", e)
        traceback.print_exc()
        return _fail("exception", f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════
#  上游 API 扫码（ADR-017「扫码走 API」）—— 子进程隔离 + 文件交接
# ══════════════════════════════════════════════════════════════════════
#  【为什么是子进程】vendor 与 backend 有 5 个同名顶层包 ⇒ 同进程必静默半坏
#    （AUTH-073 / 门禁 G1 已钉死）。唯一正确形态 = 干净解释器子进程。
#  【为什么单进程 + 文件交接】P-256 私钥与票据是会话内状态，跨进程重建需
#    序列化私钥（脆弱）。子进程从头跑到尾，只把「二维码图/进度/最终凭证」落盘。

def _api_runner_cmd(jobdir: str, timeout_s: int, proxy: str) -> list:
    """构造子进程命令行（源码态走脚本；冻结态走 `--qr-api-runner` 前置分发）。"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--qr-api-runner", "--jobdir", jobdir,
               "--timeout", str(int(timeout_s))]
    else:
        runner = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "login_qr_api_runner.py"))
        cmd = [sys.executable, runner, "--jobdir", jobdir,
               "--timeout", str(int(timeout_s))]
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd


def spawn_api_qr_login(jobdir: str, timeout_s: int = 300, proxy: str = "") -> dict:
    """启动 API 扫码子进程（立即返回，**不阻塞**）。

    返回 handle: {'proc','jobdir','log','timeout_s'}；失败抛异常（由调用方回落 RPA）。
    """
    import subprocess
    os.makedirs(jobdir, exist_ok=True)
    env = dict(os.environ)
    # 显式传 vendor 位置，避免冻结态靠相对路径猜（判据：runner._resolve_vendor）
    try:
        from auto_dm.login_api_vendor import VENDOR_DIR as _VD  # type: ignore
        if os.path.isdir(_VD):
            env["DY_VENDOR_DIR"] = _VD
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 注入 DY_VENDOR_DIR 失败（不阻塞）: {}", e)
    cmd = _api_runner_cmd(jobdir, timeout_s, proxy)
    log_path = os.path.join(jobdir, "runner.log")
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                            env=env, creationflags=creationflags)
    logger.info("[login_remote] API 扫码子进程已启动 pid={} jobdir={}", proc.pid, jobdir)
    return {"proc": proc, "jobdir": jobdir, "log": log_path,
            "timeout_s": int(timeout_s), "logf": logf}


def _read_json_quiet(path: str) -> Optional[dict]:
    """读 JSON；文件不存在/半截/损坏一律返回 None（绝不抛）。"""
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def poll_api_qr_login(handle: dict, timeout_s: Optional[int] = None,
                      interval_s: float = 2.0, on_status=None) -> dict:
    """轮询 API 扫码子进程，直到「确认/失败/超时」。

    返回 {'ok','reason','result','status'}
      ok=True  ⇒ result 为子进程写出的凭证 payload（含 cookie_str + 四项签名）
    每读到新的 status.json 会调用 `on_status(status_dict)`（供编排层回填 qrPng 等）。
    """
    jobdir = handle.get("jobdir") or ""
    status_path = os.path.join(jobdir, "status.json")
    result_path = os.path.join(jobdir, "result.json")
    deadline = time.time() + (timeout_s or handle.get("timeout_s", 300) + 90)
    _last_stage = ""

    def _consume_result(r: dict) -> dict:
        if r.get("ok"):
            return {"ok": True, "reason": "", "result": r,
                    "status": _read_json_quiet(status_path) or {}}
        return {"ok": False, "result": None,
                "reason": f"{r.get('stage')}: {r.get('error')}",
                "status": _read_json_quiet(status_path) or {}}

    while time.time() < deadline:
        # ① 最终结果优先（原子写，见到即可用）
        r = _read_json_quiet(result_path)
        if isinstance(r, dict):
            return _consume_result(r)
        # ② 进度
        s = _read_json_quiet(status_path) or {}
        stage = s.get("stage") or ""
        if stage and stage != _last_stage:
            _last_stage = stage
            logger.info("[login_remote] API 扫码阶段={} token={} png={}",
                        stage, str(s.get("token"))[:12], bool(s.get("has_png")))
            if callable(on_status):
                try:
                    on_status(s)
                except Exception as e:  # noqa: BLE001
                    logger.debug("[login_remote] on_status 回调异常: {}", e)
            if stage == "failed":
                return {"ok": False, "result": None,
                        "reason": s.get("error") or "子进程报失败", "status": s}
        # ③ 子进程意外退出且无 result ⇒ 立刻失败（不等满超时）
        proc = handle.get("proc")
        if proc is not None and proc.poll() is not None:
            r = _read_json_quiet(result_path)
            if isinstance(r, dict):
                return _consume_result(r)
            return {"ok": False, "result": None,
                    "reason": f"子进程提前退出（rc={proc.returncode}），日志: {handle.get('log')}",
                    "status": s}
        time.sleep(interval_s)
    return {"ok": False, "result": None, "reason": f"API 扫码等待超时（{timeout_s or handle.get('timeout_s')}s）",
            "status": _read_json_quiet(status_path) or {}}


def reap_api_qr_login(handle: Optional[dict]) -> None:
    """收尾：确保子进程结束、关闭日志句柄（幂等，绝不抛）。"""
    if not handle:
        return
    try:
        proc = handle.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] API 扫码子进程收尾异常: {}", e)
    try:
        f = handle.get("logf")
        if f is not None and not f.closed:
            f.close()
    except Exception:  # noqa: BLE001
        pass


async def bridge_qr_login_and_wait(env_path: str, out_png: str,
                                   headless: bool = True,
                                   qr_timeout_s: int = 90,
                                   wait_timeout_s: int = 240,
                                   on_qr_ready=None) -> dict:
    """**出码 + 等待扫码确认，全程在同一个事件循环内**（根治跨循环断连）。

    2026-10-01 根因实测：
      · 旧写法是两次独立 `asyncio.run`（出码一个、等待一个）⇒ Camoufox context
        的传输随前一个循环销毁而关闭 ⇒ `poll_bridge_confirmed` 第一次读 cookie 就炸
        （`'NoneType' object has no attribute 'send'`），表现为「用户扫了码但程序没在等」。
      · 同循环但无强引用 ⇒ 稳定 40s 后 `TargetClosedError`（ctx_mgr 被 GC 回收）。
      · **同循环 + 模块级强引用** ⇒ 实测 150s 稳定。
    本函数即把两者合为一体，是**唯一的桥扫码入口**。

    返回：{"prep": <bridge_qr_login 结果>, "waited": <poll_bridge_confirmed 结果 或 None>}
    """
    prep = await bridge_qr_login(env_path=env_path, out_png=out_png,
                                 headless=headless, timeout_s=qr_timeout_s)
    # ★ UX：出码成功**立即**回调（让前端马上看到二维码），不要等 240s 等待结束。
    #   未提供回调时行为与从前完全一致（向后兼容）。
    if on_qr_ready is not None:
        try:
            r = on_qr_ready(prep)
            if hasattr(r, "__await__"):
                await r
        except Exception as e:  # noqa: BLE001
            logger.warning("[bridge] on_qr_ready 回调异常（不阻断）: {}", e)
    if not prep.get("ok"):
        return {"prep": prep, "waited": None}
    waited = await poll_bridge_confirmed(prep.get("handle"), timeout_s=wait_timeout_s)
    return {"prep": prep, "waited": waited}


async def close_handle(handle: Optional[dict]) -> None:
    """收尾：关闭 Camoufox 浏览器（幂等），**并释放 _CAMOUFOX_KEEP 中的强引用**。

    ⚠️ 2026-09-26 修正（ADR-017 M4 实测踩坑 —— **原先的实现是错的**）
    ------------------------------------------------------------------
    原实现写成 `if backend == "exe": await browser.close()`，**双重失效**：
      ① `launch_async` 对 Camoufox 返回的 mode 是 `"camoufox"`（非 `"exe"`），
         条件不成立 ⇒ `browser.close()` 根本不执行；
      ② 该分支返回的是 `(None, None, context, mode)` —— `browser` 本就是 None。
    后果（实机实测）：关闭后进程仍是 7 个、锁文件残留、紧接第二次启动
    撞上 profile 占用 ⇒ `launch_persistent_context Timeout 180000ms`。

    正确做法：**复用项目既有工具**（不要自己造关闭逻辑）——
      `vbrowser_camoufox.close_camoufox_context(context, user_data_dir)`，
    它内部会走 `ctx_mgr.__aexit__()` **并清扫残留进程**
    （因为实测 `__aexit__` 后进程 60s 仍不退净，见该函数文档）。
    """
    if not handle:
        return
    context = handle.get("context")
    ui = handle.get("backend")
    # 兼容两种 launch_async 返回形状：
    #   Camoufox: (None, None, context, "camoufox")
    #   兼容旧调用方可能传 (pw, browser, context, mode)
    pw = handle.get("pw")
    browser = handle.get("browser")
    user_data_dir = handle.get("profile")

    # ① 首选：项目既有的统一关闭（含残留清扫）
    try:
        from vbrowser_camoufox import close_camoufox_context
        if context is not None:
            await close_camoufox_context(context, user_data_dir=user_data_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("[login_remote] close_camoufox_context 失败，尝试逐项兜底: {}", e)
        # ② 兜底：直接关 context
        try:
            if context is not None:
                await context.close()
        except Exception as e2:  # noqa: BLE001
            logger.debug("[login_remote] context.close() 亦失败: {}", e2)

    # ③ 其他模式（非 camoufox）的兼容清理
    try:
        if ui != "camoufox" and browser is not None:
            await browser.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] browser.close() 异常: {}", e)
    try:
        if pw is not None:
            await pw.stop()
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 停止 playwright 异常: {}", e)
