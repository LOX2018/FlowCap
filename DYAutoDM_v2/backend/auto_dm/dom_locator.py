# coding=utf-8
"""dom_locator —— DOM 自适应定位兜底层（Scrapling parser 层封装）。

## 定位（SoC）
本模块**只做一件事**：把「按旧选择器/文本找不到控件」时，用**自适应重定位**找回来，
并交回一个可交给 Playwright/Camoufox 的 **XPath**。它**不启动浏览器**、**不写业务状态**。

## 为什么需要（实证，2026-09-29）
`login_remote.py` 的登录页锚点全是硬编码，而抖音登录页会改版：
  · 「扫码登录 / 验证码登录」真实是 `<span class="C6OZQwMA">`（**哈希类名、无 id**）；
  · 二维码已改为 **SVG**（旧锚点 `#animate_qrcode_container img` 命中 0）；
  · 面板结构随入口/状态变化。
实证（spike `artifacts/spike-scrapling-locator/`）：id/name/class 全漂移后仍能重定位（真实 DOM 5/5），
且加「确定性校验 + 唯一性」后误命中为 0。

## 铁律（不可删）
1. **只作兜底**：确定性判据（接口信号 / 现有稳定锚点）永远优先；本模块是第二选择。
2. **校验前置 + 唯一性**：候选必须过确定性校验器（标签 / 自身直接文本 / 属性 / 祖先域），
   且**恰好 1 个**；>1 = 歧义 ⇒ **拒绝返回**（fail-closed，宁缺勿错）。
3. **优雅降级**：scrapling 缺失或任何异常 ⇒ 返回 None，调用方保持原行为（零回归）。
4. **只读**：只解析 HTML 快照，不点击、不输入（点击/输入由调用方用真实事件完成）。

## 依赖
仅需 `scrapling` **base 包**（parser 层）：lxml/cssselect/orjson/tld/w3lib/typing-extensions。
**不装** `scrapling[fetchers]`（那会引入 playwright/patchright —— 与项目 Camoufox 单内核冲突）。
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = None
try:  # 本项目统一用 loguru；缺失时退化为静默（不引入新依赖）
    from loguru import logger  # type: ignore
except Exception:  # pragma: no cover
    class _NullLog:
        def _n(self, *a, **k): pass
        debug = info = warning = error = exception = _n
    logger = _NullLog()  # type: ignore

try:
    from scrapling.parser import Selector
    from scrapling.core.storage import SQLiteStorageSystem
    _HAVE = True
except Exception:  # pragma: no cover - 优雅降级
    _HAVE = False


def available() -> bool:
    """scrapling parser 层是否可用（不可用 ⇒ 调用方走原路径）。"""
    return _HAVE


# ─────────────────────────── 目标定义 + 确定性校验器 ───────────────────────────
@dataclass
class Target:
    key: str
    css: str
    tag_in: tuple
    want_text: tuple = ()
    scope_text: tuple = ()
    attr_keys: tuple = ("placeholder", "name", "aria-label")
    desc: str = ""
    text_mode: str = "contains"   # "contains" | "exact"（exact：忽略空白的全等，防短词误收）


def _as_list(x) -> list:
    """归一化：scrapling css() 返回列表；find_by_text() 唯一命中时返回单元素。"""
    if x is None:
        return []
    if hasattr(x, "tag"):
        return [x]
    try:
        return list(x)
    except TypeError:
        return [x]


def _root(el) -> Any:
    """取底层 lxml 元素（Selector 包装体不透明，勿假设它透传 lxml 方法）。"""
    return getattr(el, "_root", el)


def _direct_text(el) -> str:
    """**自身直接文本**（不含子孙）——唯一性判据的关键（见铁律 2）。"""
    node = _root(el)
    parts = []
    if getattr(node, "text", None):
        parts.append(node.text)
    try:
        for ch in node.iterchildren():
            if getattr(ch, "tail", None):
                parts.append(ch.tail)
    except Exception:
        pass
    return " ".join(p.strip() for p in parts if p and p.strip()).strip()


def _attrs(el) -> dict:
    return {k: str(v) for k, v in (dict(getattr(el, "attrib", {}) or {})).items()}


def _in_scope(el, t: Target) -> bool:
    """祖先域判据：scope 词须命中任一祖先的 **id/class**（不区分大小写）。

    2026-09-29 修正（门禁 G3 抓出的真误命中）：
      ① 只用「祖先**文本**含 scope」会被**导航栏**蒙混过关（导航 div 文本含「登录」）；
      ② 即便加 id/class 标记再把文本作 OR 兜底，导航栏仍会命中 ⇒ **必须去掉文本兜底**。
    登录控件应锚定在**登录面板**这一语义域：面板 id/class 含 latin 标记 `login`/`comp`
    （`login-full-panel-*` / `douyin-login-new-id` / `douyin_login_comp_*` / `login-wrap`），
    导航栏（`#nav`、`body`）不含 ⇒ 被正确拒绝。
    域标记缺失时**保守判否**（fail-closed，宁缺勿错）。
    """
    if not t.scope_text:
        return True
    node = _root(el)
    try:
        for anc in node.iterancestors():
            marker = ((anc.get("id") or "") + " " + (anc.get("class") or "")).lower()
            if any(w.lower() in marker for w in t.scope_text):
                return True
    except Exception:
        return False
    return False


def _norm_ws(s: str) -> str:
    """归一**全部** Unicode 空白（含 NBSP U+00A0、全角空格 U+3000、制表/换行等）。

    2026-09-29 修正（D-1 · OCR[13]）：旧实现 `s.replace(" ", "")` 只去 **ASCII 空格**；
    登录页标签常用 NBSP（如 `登\\u00a0录`），视觉正确的标签在 exact 模式下被拒
    ⇒ 兜底静默 miss（正是本模块存在的目的）。`str.split()` 按任意 Unicode 空白切分，
    `"".join(...)` 即「去除全部空白」，比正则 `\\s` 更全（`\\s` 在部分引擎不含 NBSP）。
    """
    return "".join(s.split())


def _text_hit(own: str, attrs: dict, t: Target) -> bool:
    """自身文本/属性是否命中 want_text（支持 contains / exact 两种模式）。"""
    if not t.want_text:
        return True
    hay = own
    for k in t.attr_keys:
        if k in attrs:
            hay += " " + attrs[k]
    if t.text_mode == "exact":
        return any(_norm_ws(own) == _norm_ws(w) for w in t.want_text)
    return any(w in hay for w in t.want_text)


def _css_attr_value(v: str) -> str:
    """把属性值安全转义进 CSS 字符串字面量（供 `[k*="..."]` 使用）。

    2026-09-29 修正（D-2 · OCR[14]）：旧实现直接 `f'{tag}[{k}*="{w}"]'` 插值 `w`；
    一旦 want_text 含 `"`/`\\` 即拼出坏选择器（SelectorSyntaxError，域策略静默失效）。
    这里转义反斜杠与双引号，并把控制字符写成 CSS 十六进制转义（`\\<hex> `），
    保证产出**语法合法**的选择器。普通文本不受影响（惰性：无非字母才转义）。
    """
    if not any(c == "\\" or c == '"' or ord(c) < 0x20 for c in v):
        return v  # 热路径：常见文本零开销
    out = []
    for ch in v:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 0x20:
            out.append(f"\\{ord(ch):x} ")
        else:
            out.append(ch)
    return "".join(out)


def validate(el, t: Target) -> tuple:
    """确定性校验：标签 + 自身文本/属性关键字 + 祖先域。任一不满足即拒绝。"""
    tag = getattr(el, "tag", None)
    if tag not in t.tag_in:
        return False, f"tag={tag}∉{t.tag_in}"
    own = _direct_text(el)
    if not _text_hit(own, _attrs(el), t):
        return False, f"自身文本/属性未命中 want={t.want_text} mode={t.text_mode} own={own[:30]!r}"
    if not _in_scope(el, t):
        return False, f"不在期望域 scope={t.scope_text}"
    return True, "ok"


def _to_xpath(el) -> str:
    node = _root(el)
    try:
        return f"xpath={node.getroottree().getpath(node)}"
    except Exception:
        return ""


# ─────────────────────────── 登录页目标集（按真实 DOM 校准）───────────────────────────
# 真实结构（2026-09-29 实机捕获，无头首屏）：
#   · 「扫码登录」「验证码登录」= <span class="C6OZQwMA">（哈希类名、无 id）
#   · 「登录」提交按钮 = #douyin_login_comp_btn_id（项目旧注释误标为「验证码登录」）
#   · 手机号框 = input#normal-input / 验证码框 = input#button-input
#   · 二维码 = #animate_qrcode_container 内 SVG（旧锚点 `... img` 已失效）
LOGIN_TARGETS = (
    Target("tab_scan", 'div[id="douyin_login_comp_scan_code"]',
           ("div", "span", "a", "p"), ("扫码登录", "二维码登录"), ("login",),
           desc="扫码登录 tab", text_mode="exact"),
    Target("tab_sms", 'div[id="douyin_login_comp_mobile_code"]',
           ("div", "span", "a", "p"), ("验证码登录", "短信登录"), ("login",),
           desc="验证码登录 tab", text_mode="exact"),
    Target("input_phone", 'input[name="normal-input"]',
           ("input",), ("手机号", "请输入手机"), ("login",), ("placeholder", "name"), "手机号输入框"),
    Target("input_code", 'input[name="button-input"]',
           ("input",), ("验证码",), ("login",), ("placeholder", "name"), "验证码输入框"),
    Target("btn_submit", 'div[id="douyin_login_comp_btn_id"]',
           ("div", "button", "span", "a"), ("登录", "立即登录", "登 录"), ("login",),
           desc="登录提交按钮", text_mode="exact"),
)
_TARGETS = {t.key: t for t in LOGIN_TARGETS}


@dataclass
class LocateResult:
    ok: bool
    key: str
    strategy: str = ""
    xpath: str = ""
    element: Any = None
    reason: str = ""
    attempts: list = field(default_factory=list)


def _storage_file() -> str:
    """自适应元素库路径（显式配置优先；否则 app data 目录；再退临时目录）。"""
    env = os.environ.get("DY_DOM_ADAPTIVE_STORE")
    if env:
        return env
    candidates = []
    try:
        from vbrowser import app_root  # 项目既有实现（frozen/源码两态自适应）
        candidates.append(os.path.join(app_root(), "data"))
    except Exception:
        pass
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))
    candidates.append(os.path.join(os.environ.get("TEMP", "."), "dyautodm_dom_adaptive"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            return os.path.join(d, "dom_adaptive.sqlite")
        except Exception:
            continue
    return os.path.join(os.environ.get("TEMP", "."), "dom_adaptive.sqlite")


class AdaptiveLocator:
    """Scrapling 自适应定位 + 确定性校验的降级链（只读）。"""

    def __init__(self, storage_file: Optional[str] = None,
                 url: str = "https://www.douyin.com/"):
        if not _HAVE:
            raise RuntimeError("scrapling 未安装（仅需 base 包）")
        self.storage_file = storage_file or _storage_file()
        self.url = url
        self._kw = dict(storage=SQLiteStorageSystem,
                        storage_args={"storage_file": self.storage_file, "url": url})

    def _sel(self, html: str) -> "Selector":
        return Selector(html, adaptive=True, **self._kw)

    def seed(self, html: str, keys=None) -> dict:
        """在**基线页面**上播种（首次成功定位后记录指纹）。"""
        sel = self._sel(html)
        out = {}
        for k in (keys or list(_TARGETS)):
            t = _TARGETS[k]
            try:
                out[k] = bool(_as_list(sel.css(t.css, auto_save=True, identifier=t.key)))
            except Exception as e:
                out[k] = f"ERR {e!r}"
        return out

    def _collect(self, html: str, t: Target):
        sel = self._sel(html)
        res = LocateResult(ok=False, key=t.key)
        by: dict = {}
        # ① 自适应（旧选择器）
        try:
            for c in _as_list(sel.css(t.css, adaptive=True, identifier=t.key)):
                good, why = validate(c, t)
                res.attempts.append(("adaptive", why))
                if good:
                    by.setdefault("adaptive", []).append(c)
        except Exception as e:
            res.attempts.append(("adaptive", f"ERR {e!r}"))
        # ② 自身文本锚点（最具体者优先）
        if t.want_text:
            try:
                for el in _root(sel).iter():
                    if not isinstance(getattr(el, "tag", None), str):
                        continue
                    own = (el.text or "").strip()
                    # D-1：预筛也须容忍 Unicode 空白（如 `扫\u00a0码登录`），
                    # 否则视觉正确的标签连候选都进不来 ⇒ 兜底静默 miss。
                    if own and any(_norm_ws(w) in _norm_ws(own) for w in t.want_text):
                        good, why = validate(el, t)
                        res.attempts.append(("text", why))
                        if good:
                            by.setdefault("text", []).append(el)
            except Exception as e:
                res.attempts.append(("text", f"ERR {e!r}"))
        # ③ 结构域（无直接文本的目标，如输入框）
        for tag in t.tag_in:
            for k in t.attr_keys:
                for w in (t.want_text or ()):
                    try:
                        # D-2：属性值经转义后再拼选择器，防 `"`/`\` 拼出坏 CSS
                        for c in _as_list(sel.css(f'{tag}[{k}*="{_css_attr_value(w)}"]')):
                            good, why = validate(c, t)
                            res.attempts.append(("domain", why))
                            if good:
                                by.setdefault("domain", []).append(c)
                    except Exception as e:
                        res.attempts.append(("domain", f"ERR {e!r}"))
        return res, by

    def locate(self, html: str, key: str) -> LocateResult:
        t = _TARGETS.get(key)
        if t is None:
            return LocateResult(ok=False, key=key, reason=f"未知目标 {key}")
        res, by = self._collect(html, t)
        for strat in ("adaptive", "text", "domain"):
            cands = by.get(strat) or []
            if not cands:
                continue
            uniq = []
            for c in cands:
                if not any(_root(c) is _root(u) for u in uniq):
                    uniq.append(c)
            if len(uniq) == 1:
                res.ok, res.element, res.strategy = True, uniq[0], strat
                res.xpath = _to_xpath(uniq[0])
                res.reason = "ok"
                return res
            res.reason = f"[{strat}] 候选不唯一（{len(uniq)}）⇒ 拒绝（fail-closed）"
            return res
        res.reason = res.reason or "全部策略失败（拒绝返回未通过校验的元素）"
        return res


# 进程级单例（避免每调用重复建存储连接）
_LOCATOR: Optional[AdaptiveLocator] = None


def _get_locator() -> Optional[AdaptiveLocator]:
    global _LOCATOR
    if not _HAVE:
        return None
    if _LOCATOR is None:
        try:
            _LOCATOR = AdaptiveLocator()
        except Exception as e:  # pragma: no cover
            logger.debug("[dom_locator] 初始化失败（降级）: {}", e)
            return None
    return _LOCATOR


# ─────────────────────────── 供 login_remote 使用的异步门面 ───────────────────────────
async def locate_xpath(page, key: str) -> Optional[str]:
    """在**当前页面**上定位控件，返回 Playwright 可用的 XPath；失败/不可用 ⇒ None。"""
    loc = _get_locator()
    if loc is None:
        return None
    try:
        html = await page.content()
    except Exception as e:
        logger.debug("[dom_locator] 读取页面失败（降级）: {}", e)
        return None
    try:
        r = loc.locate(html, key)
    except Exception as e:
        logger.debug("[dom_locator] 定位异常（降级）: {}", e)
        return None
    if r.ok and r.xpath:
        logger.info("[dom_locator] 自适应命中 {} ← 策略 {}（{}）", key, r.strategy, r.xpath[:80])
        return r.xpath
    logger.debug("[dom_locator] 自适应未命中 {}: {}", key, r.reason)
    return None


async def click_adaptive(page, key: str) -> bool:
    """自适应定位并用**真实鼠标**点击（沿用项目「真 mouse 事件」惯例）。

    2026-09-29 修正（D-3 · OCR[15]）：`login_remote.py` 另写了一份近乎相同的
    `_mouse_click_locator`（sleep 值不一致 ⇒ 两条路径漂移）。本模块保留此导出作为
    **唯一**真鼠标点击实现，并把时序**对齐**到 login_remote 实测值
    （move→0.12s→down→0.06s→up），使 login_remote 改调本函数时点击行为**零变化**，
    从而消除漂移（具体改哪一行见交付报告）。
    """
    xp = await locate_xpath(page, key)
    if not xp:
        return False
    try:
        loc = page.locator(xp).first
        box = await loc.bounding_box()
        if not box:
            return False
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await page.mouse.move(cx, cy)
        await asyncio.sleep(0.12)
        await page.mouse.down()
        await asyncio.sleep(0.06)
        await page.mouse.up()
        logger.info("[dom_locator] 已点（自适应）{}", key)
        return True
    except Exception as e:
        logger.warning("[dom_locator] 点击失败 {}: {}", key, e)
        return False


__all__ = ["available", "AdaptiveLocator", "LOGIN_TARGETS", "locate_xpath",
           "click_adaptive", "validate", "Target"]
