# -*- coding: utf-8 -*-
"""E1 机械门禁：`/user/info/batch` 的**默认通道不得是 BCC**，且事实基础不得被悄悄改掉。

背景（BCC 替换评估 · 阶段 0 · E1，2026-09-28）：
  方案 `FlowCap/.hermes/plans/2026-09-28_162130-bcc-replacement-plan.md`
  附录 A（尖刺规格）/ 附录 B（BCC 20 路由剥离清单）。

  `api/platform.py` 的 `/user/info/batch` 端点曾以 `use_bcc=True` 作默认 ——
  即「**不声明就默认拉起常驻浏览器容器**」。该端点全仓零调用方，但这个默认值
  是一个**隐式 BCC 依赖口**：任何未来调用者都会重新把 BCC 拉进链路，而同一能力
  早已有纯 HTTP 等价物（`_im_user_info_by_sec`，2026-09-14 真机实测
  `status_code=0`）。E1 把默认翻为 `False`。

判据（全部**静态**，零 import 被测模块 ⇒ 不参与 FLOWCAP_APP_ROOT 争夺、不拉依赖）：
  G1  `UsersInfoReq.use_bcc` 的**默认值是 False**（AST 读取源码字面值）。
  G2  BCC 分支仍保留（`if req.use_bcc:` 存在）⇒ 能力未被删除，显式 opt-in 可用。
  G3  「事实基础」仍在（E1 与路线 B 的推理都建立在它上面，被改掉必须大声报红）：
        · 发送主通道无浏览器：`api/messages.py` 含「不依赖浏览器」；
        · 接收无浏览器：`daemon/recv_daemon.py` 含 `wss://frontier-im.douyin.com/ws/v2`。
  G4  **负控**（元门禁）：把同一条 G1 判据喂给「`use_bcc` 改回 `True`」的伪源码，
      必须**报红**。证明 G1 不是恒绿。

设计取舍：
  · **不 import `api.platform`**：该模块会牵入 fastapi 与项目包，可能触发
    `database` / `FLOWCAP_APP_ROOT` 的进程级副作用（本项目已有跨模块污染血案，
    见 `test_design_root_isolation.py`）。用 `ast` 直读字面值，确定且隔离。
  · 零第三方依赖（只用 stdlib）。
  · G1 与 G4 共用**同一个**判据函数 `_default_of_use_bcc(src)` —— 负控必须打在
    同一个函数上，否则证明不了被判据本身有效。

运行： cd FlowCap/backend && python -m unittest test_bcc_duty_surface_e1 -v
"""
from __future__ import annotations

import ast
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _read(rel: str) -> str:
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# 判据内核（G1 / G4 共用同一份实现）

def _default_of_use_bcc(src: str) -> bool | None:
    """从 `platform.py` 源码里取 `UsersInfoReq.use_bcc` 的默认值字面量。

    返回 True / False；字段缺失或无默认值返回 None。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "UsersInfoReq":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            tgt = stmt.target
            if not (isinstance(tgt, ast.Name) and tgt.id == "use_bcc"):
                continue
            if stmt.value is None:
                return None
            try:
                return bool(ast.literal_eval(stmt.value))
            except Exception:  # noqa: BLE001 —— 非字面量（表达式）视为未定
                return None
    return None


# --------------------------------------------------------------------------
# G1 / G2 —— 默认通道 + 能力保留

class TestDefaultChannelIsBrowserFree(unittest.TestCase):
    """G1：`/user/info/batch` 默认不得走 BCC。"""

    def test_g1_default_is_false(self):
        src = _read(os.path.join("api", "platform.py"))
        val = _default_of_use_bcc(src)
        self.assertIsNotNone(
            val, "未能在 UsersInfoReq 中找到 use_bcc 的字面量默认值 —— "
                 "字段被改名/改成表达式都会让本条判据失效，故必须显式失败")
        self.assertIs(
            val, False,
            f"use_bcc 默认值是 {val!r}，应为 False —— "
            "默认即 BCC = 隐式拉起常驻浏览器容器（E1 要关闭的口子）")

    def test_g2_bcc_branch_still_exists(self):
        """能力未删除：显式 opt-in 仍可用，便于与纯 HTTP 通道做对照。"""
        src = _read(os.path.join("api", "platform.py"))
        self.assertIn(
            "if req.use_bcc:", src,
            "BCC 分支被删除 —— E1 只应翻默认值，不应移除能力（否则无法回滚/对照）")


# --------------------------------------------------------------------------
# G3 —— 事实基础（E1 的推理依据）不得被悄悄改掉

class TestFactBaseStillHolds(unittest.TestCase):
    """G3：路线 B / E1 的立论事实。改掉它们必须强制重新评估，而不是静默通过。"""

    def test_g3a_send_main_channel_is_browser_free(self):
        src = _read(os.path.join("api", "messages.py"))
        self.assertIn(
            "不依赖浏览器", src,
            "发送主通道『不依赖浏览器』的记载消失 —— 若实现已改回依赖浏览器，"
            "必须回头重读方案 附录A/附录B 再改本条，不得直接删断言")

    def test_g3b_recv_channel_is_frontier_ws(self):
        src = _read(os.path.join("daemon", "recv_daemon.py"))
        self.assertIn(
            "wss://frontier-im.douyin.com/ws/v2", src,
            "接收链路（frontier-im WS，无浏览器）的地址消失 —— 同上，须重新评估")


# --------------------------------------------------------------------------
# G4 —— 负控（元门禁）：证明 G1 不是恒绿

class TestNegativeControl(unittest.TestCase):
    """G4：同一判据函数对『改回 True』的伪源码必须报红。"""

    def test_g4_gate_can_go_red(self):
        src = _read(os.path.join("api", "platform.py"))
        # 伪源码：把默认值改回 True（模拟回退）
        mutated = src.replace(
            "use_bcc: bool = False", "use_bcc: bool = True", 1)
        self.assertNotEqual(
            mutated, src,
            "负控注入失败：源码里没有 `use_bcc: bool = False` 字样。"
            "格式变了要同步改本负控，否则负控是假绿")
        val = _default_of_use_bcc(mutated)
        self.assertIs(
            val, True,
            "负控未生效：判据函数对改回 True 的源码仍返回非 True ⇒ G1 可能是恒绿")

    def test_g4b_missing_field_is_detected(self):
        """字段缺失应返回 None（而不是默认当成 False 而静默放行）。"""
        src = _read(os.path.join("api", "platform.py"))
        mutated = src.replace("use_bcc: bool = False", "use_bcc_renamed: bool = False", 1)
        self.assertIsNone(
            _default_of_use_bcc(mutated),
            "字段改名后判据仍返回非 None ⇒ 改名会静默绕过 G1")

    def test_selftest(self):
        """元门禁：本文件必须真的被 unittest 收集到用例（防脚本式门禁假绿）。"""
        self.assertGreaterEqual(
            len([n for n in dir(self) if n.startswith("test_")]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
