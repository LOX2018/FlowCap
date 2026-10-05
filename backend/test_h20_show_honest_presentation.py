# -*- coding: utf-8 -*-
"""H-20 门禁：`/show` 假阳性根治的**呈现层闭环**（2026-09-26）。

## 缺陷本质（实测，v0.44.67 后端已修一半）
`POST /show` 是**异步受理** —— 返回 `ok=true` 只代表「已受理」，
**不代表**「窗口已就绪」。后台重建可能失败（实测 `BCC-058`）。

v0.44.67 已把达成态写进 **msg 文案**（`settled=True/False` 对应不同措辞），
但存在**两个缺口**，导致同一假阳性从另一条路复发：

  ① 后端 `ScanLoginResponse` 只有 `ok` / `msg` 两字段 ⇒ 达成态**未结构化**，
     前端只能靠解析 msg 文案（或干脆不解析）；
  ② 前端 `accounts-page.tsx` 硬编码
     `push("已打开指纹浏览器 · ${name}")` + `addLog("SUCCESS", …)`
     ⇒ 用户看到「成功」弹窗而屏幕上没有窗口。

## 判据（G1~G3b）
  G1  `ScanLoginResponse` 暴露结构化 `settled` / `switching`
  G2  `open-browser` 端点把 `settled` / `switching` 透出到响应
  G3  前端**读取** `d.settled` 分档表述；且**不得**再无条件用
      「已打开指纹浏览器」+ `SUCCESS`（负控正则）
  G3b 负控自证：旧形态片段必须被 G3 的正则命中（证非空转）
"""
from __future__ import annotations

import ast
import os
import re
import sys
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BACKEND)
_FRONT = os.path.join(_ROOT, "frontend", "src")

_MODEL = os.path.join(_BACKEND, "models", "account.py")
_API_ACC = os.path.join(_BACKEND, "api", "accounts.py")
_FRONT_PAGE = os.path.join(_FRONT, "components", "accounts", "accounts-page.tsx")
_FRONT_CLIENT = os.path.join(_FRONT, "api", "client.ts")

#: 无条件「完成态」措辞 + SUCCESS 的组合（H-20 假阳性形态）
_BAD_UNCONDITIONAL_SUCCESS = re.compile(
    r'addLog\(\s*"SUCCESS"\s*,\s*`[^`]*已打开指纹浏览器')


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestH20ShowHonestPresentation(unittest.TestCase):

    def test_g1_model_exposes_settled_structurally(self):
        """G1：达成态必须**结构化**字段，不得只藏在 msg 文案里。"""
        tree = ast.parse(_read(_MODEL))
        cls = next((n for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "ScanLoginResponse"),
                   None)
        self.assertIsNotNone(cls, "ScanLoginResponse 未找到")
        fields = {t.target.id: t.annotation
                  for t in cls.body if isinstance(t, ast.AnnAssign)
                  and isinstance(t.target, ast.Name)}
        for f in ("settled", "switching"):
            self.assertIn(f, fields,
                          f"ScanLoginResponse 缺结构化字段 {f}（H-20：ok 只代表受理，"
                          f"达成态必须结构化透出，否则前端只能猜）")

    def test_g2_open_browser_exposes_settled(self):
        """G2：open-browser 响应必须携带 settled / switching。"""
        src = _read(_API_ACC)
        i = src.find("async def open_fingerprint_browser")
        self.assertGreater(i, 0, "open_fingerprint_browser 未找到")
        seg = src[i:i + 6000]
        for f in ("settled=", "switching="):
            self.assertIn(f, seg, f"open-browser 未透出 {f.rstrip('=')}（H-20 回归）")

    def test_g3_frontend_reads_settled(self):
        """G3：前端必须读取结构化 settled（而非凭 ok 宣称已打开）。"""
        src = _read(_FRONT_PAGE)
        self.assertIn("d.settled", src,
                      "前端未读取结构化 settled ⇒ 仍会凭 ok 宣称『已打开』（H-20 复发）")

    def test_g3_no_unconditional_success_wording(self):
        """G3 负控：不得存在无条件「已打开指纹浏览器」+ SUCCESS。"""
        src = _read(_FRONT_PAGE)
        m = _BAD_UNCONDITIONAL_SUCCESS.search(src)
        self.assertIsNone(m, "前端仍无条件把『已打开指纹浏览器』记为 SUCCESS（H-20 假阳性复发）")

    def test_g3b_negative_control_would_catch_regression(self):
        """G3b 负控自证：构造旧形态 → 正则必须命中（证明门禁非空转）。"""
        old = 'api.addLog("SUCCESS", `已打开指纹浏览器 · ${name}`).catch(() => {});'
        self.assertIsNotNone(
            _BAD_UNCONDITIONAL_SUCCESS.search(old),
            "负控失效：旧形态未被正则命中，门禁是空的")

    def test_g4_frontend_client_declares_settled(self):
        """G4：前端 client.ts 的返回类型须声明 settled（类型层一致性）。"""
        src = _read(_FRONT_CLIENT)
        i = src.find("openFingerprintBrowser")
        self.assertGreater(i, 0, "前端未声明 openFingerprintBrowser")
        seg = src[i:i + 800]
        self.assertIn("settled", seg,
                      "前端 client.ts 未声明 settled 字段（类型与后端契约不一致）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
