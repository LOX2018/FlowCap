# -*- coding: utf-8 -*-
"""spike: Scrapling 自适应定位 —— 作为「DOM 定位兜底层」的核心模块（v2，修误命中）。

设计要点（对应 ui-element-locating-strategy 的不变量）：
  1. 降级链：① Scrapling 自适应(旧选择器) → ② 自身文本锚点 → ③ 结构域(属性匹配) → ④ 报错
  2. **每个候选都必须过确定性校验器**；校验不过 = 不采用（诚实降级，绝不静默错点）
  3. **唯一性铁律**：候选 >1 = 歧义 ⇒ 拒绝（fail-closed），绝不猜
  4. 输出可交给 Playwright 的绝对 XPath（复用项目既有 playwright/Camoufox 点击能力）
  5. 只消费 Scrapling 的 parser 层（不引入它的浏览器栈）

v2 关键修正（v1 在真实 DOM 上实测暴露的误命中）：
  v1 用 `get_all_text()`（含子孙文本）做校验 ⇒ **父容器**（登录面板同时含两段文案）
  会同时命中多个目标 ⇒ tab_scan 与 tab_sms 解析到**同一个**元素 id。
  v2 改为「**自身直接文本** + 候选唯一性」，歧义即拒绝。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

try:
    from scrapling.parser import Selector
    from scrapling.core.storage import SQLiteStorageSystem
    _HAVE = True
except Exception:  # pragma: no cover
    _HAVE = False


# ─────────────────────────── 目标定义 + 校验器 ───────────────────────────
@dataclass
class Target:
    """一个待定位控件。validator 是**确定性**判据，非相似度。"""
    key: str                        # 自适应库里的 identifier
    css: str                        # 初始/旧选择器（漂移后失效）
    tag_in: tuple                   # 可接受标签集合
    want_text: tuple = ()           # 自身直接文本 / 指定属性 须含其一
    scope_text: tuple = ()          # 祖先域必须包含任一子串（防误命中导航栏等同名控件）
    attr_keys: tuple = ("placeholder", "name", "aria-label")
    desc: str = ""


def _as_list(x) -> list:
    """归一化：css() 返回列表，find_by_text() 唯一命中时返回单个 Selector。"""
    if x is None:
        return []
    if hasattr(x, "tag"):
        return [x]
    try:
        return list(x)
    except TypeError:
        return [x]


def _root(el) -> Any:
    return getattr(el, "_root", el)


def _direct_text(el) -> str:
    """**自身直接文本**（不含子孙元素文本）—— 唯一性判据的关键。"""
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
    if not t.scope_text:
        return True
    node = _root(el)
    try:
        for anc in node.iterancestors():
            if any(w in (anc.text_content() or "") for w in t.scope_text):
                return True
    except Exception:
        return False
    return False


def validate(el, t: Target) -> tuple:
    """确定性校验：标签 + 自身文本/属性关键字 + 祖先域。任一不满足即拒绝。"""
    tag = getattr(el, "tag", None)
    if tag not in t.tag_in:
        return False, f"tag={tag}∉{t.tag_in}"
    own = _direct_text(el)
    attrs = _attrs(el)
    hay = own
    for k in t.attr_keys:
        if k in attrs:
            hay += " " + attrs[k]
    if t.want_text and not any(w in hay for w in t.want_text):
        return False, f"自身文本/属性未命中 want={t.want_text} own={own[:30]!r}"
    if not _in_scope(el, t):
        return False, f"不在期望域 scope={t.scope_text}"
    return True, "ok"


def to_playwright_xpath(el) -> str:
    """lxml 元素 -> 可交给 Playwright 的绝对 XPath。"""
    node = _root(el)
    try:
        xp = node.getroottree().getpath(node)
    except Exception:
        return ""
    return f"xpath={xp}"


@dataclass
class LocateResult:
    ok: bool
    key: str
    strategy: str = ""
    xpath: str = ""
    element: Any = None
    reason: str = ""
    attempts: list = field(default_factory=list)


class AdaptiveLocator:
    """把 Scrapling 自适应定位包装成「带校验 + 唯一性」的降级链。"""

    def __init__(self, storage_file: str, url: str = "https://www.douyin.com/"):
        if not _HAVE:
            raise RuntimeError("scrapling 未安装（仅需 base 包）")
        self.storage_file = storage_file
        self.url = url
        self._kw = dict(storage=SQLiteStorageSystem,
                        storage_args={"storage_file": storage_file, "url": url})

    def _sel(self, html: str) -> "Selector":
        return Selector(html, adaptive=True, **self._kw)

    # --- 观测（播种：记录基线元素指纹）---
    def seed(self, html: str, targets: list) -> dict:
        sel = self._sel(html)
        out = {}
        for t in targets:
            try:
                els = _as_list(sel.css(t.css, auto_save=True, identifier=t.key))
                out[t.key] = bool(els)
            except Exception as e:
                out[t.key] = f"ERR {e!r}"
        return out

    # --- 候选收集：每条策略只返回「通过校验」的候选 ---
    def _collect(self, html: str, t: Target):
        sel = self._sel(html)
        res = LocateResult(ok=False, key=t.key)
        by: dict = {}

        # ① Scrapling 自适应（旧选择器）
        try:
            for c in _as_list(sel.css(t.css, adaptive=True, identifier=t.key)):
                good, why = validate(c, t)
                res.attempts.append(("adaptive", why))
                if good:
                    by.setdefault("adaptive", []).append(c)
        except Exception as e:
            res.attempts.append(("adaptive", f"ERR {e!r}"))

        # ② 自身文本锚点（最具体者优先；应对「无稳定 id/class，但文案在」）
        if t.want_text:
            try:
                root = _root(sel)
                for el in root.iter():
                    if not isinstance(getattr(el, "tag", None), str):
                        continue
                    own = (el.text or "").strip()
                    if own and any(w in own for w in t.want_text):
                        cand = el   # 直接用 lxml 元素：validate/_root/xpath/attrib 均兼容
                        good, why = validate(cand, t)
                        res.attempts.append(("text", why))
                        if good:
                            by.setdefault("text", []).append(cand)
            except Exception as e:
                res.attempts.append(("text", f"ERR {e!r}"))

        # ③ 结构域：按 tag + 属性包含匹配（输入框这类无直接文本的目标）
        for tag in t.tag_in:
            for k in t.attr_keys:
                for w in (t.want_text or ()):
                    try:
                        for c in _as_list(sel.css(f'{tag}[{k}*="{w}"]')):
                            good, why = validate(c, t)
                            res.attempts.append(("domain", why))
                            if good:
                                by.setdefault("domain", []).append(c)
                    except Exception as e:
                        res.attempts.append(("domain", f"ERR {e!r}"))
        return res, by

    def locate(self, html: str, t: Target) -> LocateResult:
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
                el = uniq[0]
                res.ok, res.element, res.strategy = True, el, strat
                res.xpath = to_playwright_xpath(el)
                res.reason = "ok"
                return res
            res.reason = f"[{strat}] 候选不唯一（{len(uniq)}）⇒ 拒绝（fail-closed）"
            return res
        res.reason = res.reason or "全部策略失败（拒绝返回未通过校验的元素）"
        return res


# ─────────────────────────── 目标集（登录页，按真实 DOM 校准）───────────────────────────
# 真实结构（2026-09-29 实机捕获，无头首屏）：
#   · 「扫码登录」「验证码登录」= <span class="C6OZQwMA">（哈希类名、无 id）→ 只能靠文本/自适应
#   · 「登录」提交按钮 = #douyin_login_comp_btn_id（项目旧注释误标为「验证码登录」）
#   · 手机号框 = input#normal-input / 验证码框 = input#button-input（真实存在）
#   · 二维码 = #animate_qrcode_container 内 SVG（不是 img，旧锚点 `... img` 已失效）
LOGIN_TARGETS = [
    Target(key="tab_scan", css='div[id="douyin_login_comp_scan_code"]',
           tag_in=("div", "span", "a", "p"), want_text=("扫码登录", "二维码登录"),
           scope_text=("登录",), desc="扫码登录 tab"),
    Target(key="tab_sms", css='div[id="douyin_login_comp_mobile_code"]',
           tag_in=("div", "span", "a", "p"), want_text=("验证码登录", "短信登录"),
           scope_text=("登录",), desc="验证码登录 tab"),
    Target(key="input_phone", css='input[name="normal-input"]',
           tag_in=("input",), want_text=("手机号", "请输入手机"),
           scope_text=("登录",), attr_keys=("placeholder", "name"), desc="手机号输入框"),
    Target(key="input_code", css='input[name="button-input"]',
           tag_in=("input",), want_text=("验证码",),
           scope_text=("登录",), attr_keys=("placeholder", "name"), desc="验证码输入框"),
    Target(key="btn_submit", css='div[id="douyin_login_comp_btn_id"]',
           tag_in=("div", "button", "span", "a"),
           want_text=("登录", "立即登录", "登 录"),
           scope_text=("登录",), desc="登录提交按钮"),
]

