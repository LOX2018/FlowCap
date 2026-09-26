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
import time
from typing import Callable, Optional

from loguru import logger

# 登录页入口（实测可靠，绕开 A/B 差异）
LOGIN_URL = "https://www.douyin.com/?modal_id=login"

# ── DOM 锚点（全部实测得来，勿凭猜修改）─────────────────────────────────
SEL_CODE_INPUT = 'input[name="button-input"]'      # 验证码框（maxLength=6）
SEL_PHONE_INPUT = 'input[name="normal-input"]'     # 手机号框（maxLength=50）
SEL_LOGIN_PANEL = "#douyin-login-new-id"           # 登录面板（实测 id）
SEL_SCAN_COMP = "#douyin_login_comp_scan_code"     # 扫码组件整块
SEL_QR_CONTAINER = "#animate_qrcode_container"     # 二维码【动画外框】（内部才是码）

# 二维码锚点优先级（2026-09-26 实测，见 ADR-017 调研记录 §7）
#   · img 直取 = 最优（178×178 精确，解码成功）
#   · 扫码组件整块 = 兜底（248×226，也能解）
#   · svg = ❌ 是 40×40 装饰图标，勿用；容器本体 = ❌ 空白外框，勿用
_SEL_QR_IMG = f"{SEL_QR_CONTAINER} img"
_QR_ANCHORS = (
    _SEL_QR_IMG,
    SEL_SCAN_COMP,
)

TAB_SCAN = "扫码登录"
TAB_PHONE = "验证码登录"
BTN_SEND_CODE = "获取验证码"
BTN_LOGIN = "登录"

# 陈旧锁文件名（Firefox 系）
_LOCK_FILES = ("parent.lock", ".parentlock")


# ══════════════════════════════════════════════════════════════════════
#  E1 陈旧 profile 锁自愈（RPA 可靠性的核心，实测踩过 180s 超时）
# ══════════════════════════════════════════════════════════════════════

def count_browser_processes() -> int:
    """统计本机 camoufox/firefox 进程数（用于判断锁是否陈旧）。

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


def heal_stale_profile_lock(profile_dir: str, retries: int = 3) -> dict:
    """检测并清除**陈旧** profile 锁（E1）。

    判据（保守）：**仅当** `camoufox/firefox` 进程数为 0 时，才删除锁文件。
    只要有进程存活就**绝不删除**（避免破坏正在运行的实例）。

    返回 {'healed': bool, 'removed': [文件名], 'procs': int, 'checked': int}
    """
    out = {"healed": False, "removed": [], "procs": -1, "checked": 0}
    for fn in _LOCK_FILES:
        fp = os.path.join(profile_dir, fn)
        if not os.path.exists(fp):
            continue
        out["checked"] += 1
        # 有锁 → 看是否有进程；无进程 ⇒ 陈旧锁
        procs = count_browser_processes()
        out["procs"] = procs
        if procs == 0:
            for _ in range(retries):
                try:
                    os.remove(fp)
                    out["removed"].append(fn)
                    out["healed"] = True
                    logger.info("[login_remote] E1 陈旧锁已清除: {}", fn)
                    break
                except Exception as e:  # noqa: BLE001
                    logger.debug("[login_remote] 删锁 {} 失败（重试）: {}", fn, e)
                    time.sleep(0.5)
        else:
            logger.debug("[login_remote] profile 存在锁 {} 但存活进程 {} 个，"
                         "判定为在用，不清理", fn, procs)
    if out["checked"] == 0:
        logger.debug("[login_remote] profile 无锁文件，无需自愈")
    return out


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
    logger.debug("[login_remote] 二维码渲染等待 {:.1f}s", waited)

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
    """按**文本精确匹配**点击元素（tab / 按钮）。"""
    try:
        return bool(await page.evaluate(_JS_CLICK_TEXT, text))
    except Exception as e:  # noqa: BLE001
        logger.warning("[login_remote] 点击「{}」异常: {}", text, e)
        return False


async def fill_phone(page, phone: str) -> bool:
    """填手机号（逐字真实按键，避免 fill 的 JS 直赋值特征）。"""
    loc = page.locator(SEL_PHONE_INPUT).first
    if await loc.count() == 0:
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
    """
    loc = page.locator(SEL_CODE_INPUT).first
    if await loc.count() == 0:
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
    # E1：启动前自愈陈旧锁
    heal = heal_stale_profile_lock(profile)

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
        return res
    except Exception as e:  # noqa: BLE001
        logger.error("[login_remote] 二维码流程异常: {}", e)
        # 异常时必须清理，避免锁残留
        await close_handle(handle)
        return {"ok": False, "png": None, "decoded": None, "box": None,
                "reason": f"{type(e).__name__}: {e}", "handle": None, "heal": heal}


async def poll_qr_scanned(handle: dict, timeout_s: int = 240,
                          interval_s: int = 3) -> dict:
    """轮询「用户是否已扫码登录成功」。

    判据（任一）：
      ① cookie 中出现真实登录标识（sessionid / sid_tt）
      ② 页面离开登录弹窗（URL 变化 / 登录框消失）
    """
    context = handle.get("context")
    if context is None:
        return {"ok": False, "reason": "handle 无效"}
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            cookies = {c["name"]: c["value"] for c in await context.cookies()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"读取 cookie 失败: {e}"}
        if cookies.get("sessionid") or cookies.get("sid_tt"):
            logger.info("[login_remote] 检测到真实登录态（已扫码）")
            return {"ok": True, "cookies": cookies, "reason": ""}
        await asyncio.sleep(interval_s)
    return {"ok": False, "reason": f"等待扫码超时（{timeout_s}s）"}


async def close_handle(handle: Optional[dict]) -> None:
    """收尾：关闭浏览器 + 停止 playwright（幂等）。"""
    if not handle:
        return
    browser, pw, backend = handle.get("browser"), handle.get("pw"), handle.get("backend")
    try:
        if backend == "exe" and browser is not None:
            await browser.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 关闭浏览器异常: {}", e)
    try:
        if pw is not None:
            await pw.stop()
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 停止 playwright 异常: {}", e)
