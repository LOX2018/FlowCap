# -*- coding: utf-8 -*-
"""元素选择模式（Element Inspector）的静态契约守卫（2026-09-19 新增功能固化）。

## 为什么需要它

「元素选择模式」的设计契约核心是 **只选择、不触发**：
进入选择模式后点任何元素，都不得执行该元素的业务功能。这条契约靠的是
`element-inspector.tsx` 里的三件事同时成立，**缺一件就会静默破约**
（比如漏掉 `pointerdown`：菜单类组件在 pointerdown 就展开，点击被吞也没用）：

1. 拦截清单 `BLOCKED_EVENTS` 覆盖 click / pointer / mouse / submit 等；
2. 拦截在 **window 捕获阶段** 注册（`addEventListener(ev, fn, true)`），
   否则 React 根节点（#root 的捕获+冒泡）会先收到事件；
3. `swallow()` 里做 `preventDefault + stopPropagation`。

同时守住一条**实测踩过的坑**：`swallow` 与 `onClick` 是 `click` 上的兄弟监听器，
`stopImmediatePropagation()` 会连自己的 `onClick` 一起吞掉 —— 现象是
「点击能选中（高亮变），但结果面板永不出现」。此坑曾真实发生，故写进断言。

另守两条集成契约：
- 工具按钮必须挂在 App 的 TopBar `topRight`（用户点得到），面板挂 App 根部（全局唯一）；
- 工具自身的 CSS 类前缀 `ei-` 必须在生成选择器时被过滤（不能把 `ei-hover` 写进选择器）。

> 行为级验证（真浏览器驱动、真的点击不触发）见 `scripts/verify_element_inspector.py`；
> 本文件是**廉价回归网**，防的是「后来人改坏了这几行」。
"""
from __future__ import annotations

import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_INSPECTOR = os.path.join(_ROOT, "frontend", "src", "lib", "element-inspector.tsx")
_APP = os.path.join(_ROOT, "frontend", "src", "App.tsx")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _strip_comments(src: str) -> str:
    """去掉块注释与整行注释后再断言。

    必须这么做：源码注释里本来就会出现「不得使用 stopImmediatePropagation」
    「不读 localStorage」这类**说明文字**，直接对全文断言会把注释当成违规
    （实测首版即因此误报 2 项）。断言只应针对**可执行代码**。
    """
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)          # 块注释
    src = re.sub(r"(?m)^\s*\*.*$", "", src)            # 块注释续行（若上面正则已处理则为空转）
    src = re.sub(r"(?m)^\s*//.*$", "", src)            # 整行 // 注释
    return src


class TestElementInspectorContract(unittest.TestCase):
    """只选择、不触发的静态契约。"""

    @classmethod
    def setUpClass(cls):
        for p in (_INSPECTOR, _APP):
            if not os.path.isfile(p):
                raise unittest.SkipTest(f"找不到 {p}")
        # 全文（含注释）用于「文件完整性」类断言
        cls.src_full = _read(_INSPECTOR)
        cls.app_full = _read(_APP)
        # 去注释后的代码，用于「不得出现某符号」类断言
        cls.src = _strip_comments(cls.src_full)
        cls.app = _strip_comments(cls.app_full)

    # ── 文件完整性 ──
    def test_source_decodes(self):
        """源码必须能无替换解码（防写入非法字节，同 login_gate 守卫）。"""
        self.assertNotIn("\ufffd", self.src_full, "element-inspector.tsx 含非法字节")
        self.assertNotIn("\ufffd", self.app_full, "App.tsx 含非法字节")

    def test_contract_documented(self):
        """设计契约必须写在源码里（设计优先律：无契约说明的模块不允许提交）。"""
        for kw in ("设计意图", "设计契约", "只选择，不触发"):
            self.assertIn(kw, self.src_full, f"缺少契约说明：{kw}")

    # ── 契约 1：拦截清单覆盖关键事件 ──
    def test_blocked_events_cover_pointer_and_click(self):
        """必须拦 click 与 pointerdown/pointerup（菜单类组件在 pointerdown 就展开）。"""
        for ev in ('"click"', '"pointerdown"', '"pointerup"', '"mousedown"', '"contextmenu"'):
            self.assertIn(ev, self.src, f"拦截清单缺少 {ev}")

    # ── 契约 2：捕获阶段拦截 ──
    def test_listeners_registered_in_capture_phase(self):
        """拦截必须在 window 捕获阶段（第三个参数 true），否则 React 先收到。"""
        self.assertIn("window.addEventListener(ev, swallow, true)", self.src,
                      "swallow 未在捕获阶段注册")
        self.assertIn("window.addEventListener(\"click\", onClick, true)", self.src,
                      "onClick 未在捕获阶段注册")
        self.assertIn("window.addEventListener(\"mousemove\", onMove, true)", self.src,
                      "onMove 未在捕获阶段注册")

    def test_cleanup_removes_all_listeners(self):
        """卸载时必须逐个移除（否则退出选择模式后仍吞事件 = 界面假死）。"""
        self.assertIn("window.removeEventListener(ev, swallow, true)", self.src)
        self.assertIn('window.removeEventListener("click", onClick, true)', self.src)

    # ── 契约 3：吞事件的具体动作 ──
    def test_swallow_prevents_default_and_propagation(self):
        self.assertIn("e.preventDefault()", self.src)
        self.assertIn("e.stopPropagation()", self.src)

    def test_no_stop_immediate_propagation(self):
        """★实战坑：stopImmediatePropagation 会吞掉自己的 onClick（面板永不出现）。"""
        hits = [ln.strip() for ln in self.src.splitlines() if "stopImmediatePropagation" in ln]
        self.assertEqual(hits, [],
                         "不得使用 stopImmediatePropagation（会吞掉同节点的 onClick）："
                         + " | ".join(hits))

    def test_ui_own_elements_are_exempt(self):
        """面板/提示条自身必须可交互（data-ei-ui 标记被放行）。"""
        self.assertIn('[data-ei-ui]', self.src)

    # ── 契约 4：退出路径 ──
    def test_escape_exits(self):
        self.assertIn('e.key === "Escape"', self.src)

    # ── 契约 5：工具自身类名不进选择器 ──
    def test_tool_classes_filtered_from_selectors(self):
        self.assertIn('!c.startsWith("ei-")', self.src,
                      "生成选择器前必须过滤工具自身的 ei-* 类名")

    # ── 契约 6：取址锚点优先 data-od-id（本项目稳定锚点体系） ──
    def test_prefers_data_od_id_anchor(self):
        self.assertIn("data-od-id", self.src, "未使用项目稳定锚点 data-od-id")
        self.assertIn("xpathOf", self.src, "缺少 XPath 生成")

    # ── 契约 7：集成点（按钮在 TopBar、面板在 App 根部） ──
    def test_mounted_in_app_topbar_and_root(self):
        self.assertIn('import { ElementInspectorButton } from "./lib/element-inspector"', self.app,
                      "App.tsx 未引入元素选择器")
        self.assertIn('data-od-id="debug-inspector-toggle"', self.src,
                      "工具按钮缺少稳定锚点 data-od-id")
        # 2026-09-19 用户定调：入口改为「全局顶层悬浮」，不再挂在 TopBar。
        self.assertIn("ei-fab", self.src, "缺失全局顶层悬浮入口样式（ei-fab）")
        self.assertNotIn("showButton", self.src,
                         "showButton 开关已废弃（入口全局唯一，不再可关）")
        # 必须**无条件**渲染：登录门与主界面两处各一个实例
        self.assertEqual(self.app.count("<ElementInspectorButton"), 2,
                         "ElementInspectorButton 应恰好挂两处：登录门分支 + 主界面分支")
        # 登录门分支必须在 `if (!memberName)` 段内（否则未登录时看不到入口）
        gate_seg = self.app.split("if (!memberName)", 1)[1].split("// =====", 1)[0]
        self.assertIn("<ElementInspectorButton", gate_seg,
                      "登录门/闪屏分支未渲染入口 —— 未登录时无法定位元素")

    def test_no_business_data_access(self):
        """零业务耦合：工具不得读写业务 API / localStorage。"""
        for forbidden in ("localStorage", "sessionStorage", "fetch(", "/api/"):
            hits = [ln.strip() for ln in self.src.splitlines() if forbidden in ln]
            self.assertEqual(hits, [], f"元素选择器不应访问 {forbidden}：{' | '.join(hits)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
