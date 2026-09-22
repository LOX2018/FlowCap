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
        """BootSplash 的守卫不得引用 ready（它来自需登录的 /api/overview）。

        ## 2026-09-21 修正（原断言绑死了具体实现写法）

        原实现用正则找 `if (...) return <BootSplash` —— 但 App.tsx 登录门控
        已重构为**三元表达式**：

            const gate = !prealigned ? <BootSplash .../> : memberChecked ? ... : ...;

        结构含义未变（仍是「未就绪 → 闪屏」），写法变了，正则就恒红 ——
        **守的是写法，不是不变式**。这是「断言字面量」的典型失效：
        实现一重构，守卫立刻失能（而它守的循环依赖缺陷反而没人看了）。

        ## 现在的判据（只看不变式，不看写法）

        1. 能切出 `if (!memberName) { ... }` 门控段（切不出 = 结构真变了，报错）；
        2. 该段内**不出现裸 `ready`**（`prealigned` / `memberChecked` 允许）；
        3. 该段内仍须有 `BootSplash`（否则门控被删，未就绪界面会闪过）。
        """
        m = re.search(r"if \(!memberName\)\s*\{(.{0,1500}?)\n  \}", self.src, re.S)
        self.assertIsNotNone(m, "未能切出登录门控段（结构可能已变）")
        seg = m.group(1)
        # ③ 门控段必须仍渲染 BootSplash（写法不限：if-return / 三元 / 变量赋值）
        self.assertIn("BootSplash", seg, "登录门控段内不再渲染 BootSplash")
        # ② 段内不得出现裸 ready（prealigned 含 'align' 不含 'ready'，无需额外剔除）
        #
        # ★ 必须先剥注释再判（2026-09-21 实测踩坑）：本段注释里原文写着
        #   「**不得引用 ready**：它需要登录」——那是**防复发说明**，不是引用。
        #   不剥注释会被自己的说明文字绊倒（假红）。
        #   同时这也是「断言不得断言字面量/注释文本」的同一条纪律的另一面：
        #   判据只看**代码**，不看叙述。
        code_only = re.sub(r"//[^\n]*", "", seg)
        code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)
        bare_ready = [
            w for w in re.findall(r"\b(\w*ready\w*)\b", code_only, re.I)
            if w.lower() not in ("prealigned", "already")
        ]
        self.assertFalse(
            bare_ready,
            f"登录门控段引用了需登录的 ready → 循环依赖复发: {sorted(set(bare_ready))}",
        )

    def test_prealigned_guard_exists(self):
        """必须存在基于免鉴权 prealigned 的守卫（否则会闪过未就绪界面）。

        ## 2026-09-21 修正：判据改为「prealigned 出现在 BootSplash 之前」

        原正则要求 `if (!prealigned...) return <BootSplash` 连续出现，三元写法
        下同样不成立。不变式其实是：**prealigned 参与决定 BootSplash 的渲染**。
        故改为：门控段内 `prealigned` 与 `BootSplash` 必须同时存在，且
        `prealigned` 出现在 `BootSplash` 首次出现之前（顺序即因果关系）。
        """
        m = re.search(r"if \(!memberName\)\s*\{(.{0,1500}?)\n  \}", self.src, re.S)
        self.assertIsNotNone(m, "未能切出登录门控段（结构可能已变）")
        seg = m.group(1)
        i_pre = seg.find("prealigned")
        i_bs = seg.find("BootSplash")
        self.assertGreaterEqual(i_pre, 0, "门控段内未使用免鉴权的 prealigned")
        self.assertGreaterEqual(i_bs, 0, "门控段内未渲染 BootSplash")
        self.assertLess(
            i_pre, i_bs,
            "prealigned 未参与 BootSplash 的渲染判定（门控可能引用了其它状态）",
        )

    def test_ready_still_used_after_login(self):
        """`ready` 仍须用于登录**后**的业务 UI（不能为了修 bug 把它删干净）。"""
        self.assertGreaterEqual(
            len(re.findall(r"\bready\b", self.src)), 5,
            "ready 被删得过多 —— 登录后的业务门控/顶栏状态依赖它",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
