# -*- coding: utf-8 -*-
"""前端登录门控不可引入「需鉴权依赖」的回归守卫（2026-09-18 事故固化）。

## 为什么要这个测试（真实事故）

v0.43.44 为了「登录框延迟到后端就绪后再弹出」，把登录框渲染门控写成：

    if (!prealigned || !ready) return <BootSplash/>;

但 `ready` 取自 **`/api/overview` 的 isSuccess**，而 `/api/overview` 受会员门禁保护
（未登录一律 401）→ **循环依赖**：

    未登录 → overview 401 → ready 恒 false → 登录框不渲染 → 无法登录

用户侧表现：**会话过期（>12h TTL）或首次安装时，永远卡在 BootSplash
「正在唤醒后端引擎」**，界面完全不可用。

v0.43.44 验证时没暴露，是因为当时**会话有效（自动登录）**，`overview` 成功 →
看起来正常。只有「未登录」这一条路径才暴露 —— 典型的「只测顺利路径」假通过。

## 本测试守的不变式

**登录框（MemberGate）之前的渲染门控，不得依赖任何需要登录态的接口。**

具体判据：`App.tsx` 里 `if (!memberName) { ... }` 这一段中，
`return <BootSplash/>` 的守卫条件**只能引用免鉴权状态**（`prealigned` /
`memberChecked`），**不得引用 `ready`**（它来自需登录的 `/api/overview`）。

免鉴权就绪针是 `prealigned`，来源 `/api/ready`（其 docstring 明写它就是
「前端等 daemons_ready=true 才显示登录框」的就绪针，且内部已分未登录分支）。
"""
from __future__ import annotations

import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_TSX = os.path.normpath(os.path.join(_HERE, "..", "frontend", "src", "App.tsx"))


class TestLoginGateNoAuthedDependency(unittest.TestCase):
    """登录门控不得依赖需鉴权接口（循环依赖守卫）。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_APP_TSX):
            raise unittest.SkipTest(f"找不到 {_APP_TSX}")
        with open(_APP_TSX, encoding="utf-8", errors="replace") as f:
            cls.src = f.read()

    def test_file_decodes_and_has_gate(self):
        """源码必须能解码（防写入非法字节）且含登录门控。"""
        self.assertIn("if (!memberName)", self.src, "未找到登录门控段")
        # 非法 UTF-8 会已被 errors='replace' 变成 U+FFFD
        self.assertNotIn("\ufffd", self.src, "App.tsx 含非法字节（写入被破坏）")

    def test_boot_splash_guard_does_not_use_ready(self):
        """BootSplash 的守卫不得引用 ready（它来自需登录的 /api/overview）。"""
        # 取 `if (!memberName) {` 之后到下一个顶层 `}` 的近似区段
        m = re.search(r"if \(!memberName\)\s*\{(.{0,1200}?)\n  \}", self.src, re.S)
        self.assertIsNotNone(m, "未能切出登录门控段（结构可能已变）")
        seg = m.group(1)
        guards = re.findall(r"if \(([^)]*)\)\s*return <BootSplash", seg)
        self.assertTrue(guards, "登录门控段内未找到 BootSplash 守卫")
        for g in guards:
            self.assertNotIn(
                "ready", g.replace("prealigned", ""),
                f"BootSplash 守卫引用了需登录的 ready → 循环依赖复发: if ({g})",
            )

    def test_prealigned_guard_exists(self):
        """必须存在基于免鉴权 prealigned 的守卫（否则会闪过未就绪界面）。"""
        self.assertRegex(
            self.src, r"if \(!prealigned[^)]*\)\s*return <BootSplash",
            "缺少 prealigned 守卫",
        )

    def test_ready_still_used_after_login(self):
        """`ready` 仍须用于登录**后**的业务 UI（不能为了修 bug 把它删干净）。"""
        self.assertGreaterEqual(
            len(re.findall(r"\bready\b", self.src)), 5,
            "ready 被删得过多 —— 登录后的业务门控/顶栏状态依赖它",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
