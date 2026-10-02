# -*- coding: utf-8 -*-
"""门禁：`crawl.batch_min_score` 的门槛语义在**所有批量路径**上必须一致（SSOT 归位）。

## 为什么需要（2026-10-02 审计发现的契约漂移）

同一语义「高价值关键词最低得分门槛」当时有**两套实现**：

    · /comments/batch  →  min_score 来自**请求体**（前端从配置中心读后传）
    · /dm/batch         →  min_score 只认**请求体**，前端恒传 0

前端 `crawl-page.tsx` 的注释写着「后端读取配置中心 batch_min_score」，
但 `/dm/batch` 后端**从不读**。⇒ 用户在配置中心调高门槛，批量采集会过滤，
批量私信**一条都不过滤** ⇒ 筛选形同虚设 → 对不匹配用户批量发私信
（账号风控面，不只是 UI bug）。

## 现行契约（2026-10-02 修复后）

配置中心 `crawl.batch_min_score` 是**权威来源**；请求体 `min_score` 仅作
「本次覆盖」，且 **0 = 不覆盖**（不是「不过滤」）。

## 本门禁的判据（离线、可回放，不出网、不发私信）

  G1  `/dm/batch` 门槛来自配置中心（请求体 0 时门槛仍生效）
  G2  请求体传 0 **不得**把门槛踩回不过滤
  G3  请求体传**正数**时作为「本次覆盖」生效
  G4  负控：旧实现（只认请求体）必须与新实现给出**不同**结果
  G5  负控：把决议式改回 `int(body.min_score or 0)` ⇒ 门禁必须变红
  G6  `/comments/batch` 的**请求体 min_score 仍被接受**（前端从配置中心读后传，
      是「本次覆盖」的另一入口；不得被本次修复误伤）

## 运行

    cd backend && python -m unittest test_crawl_min_score_ssot -v
"""
from __future__ import annotations

import os
import re
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CRAWL_PY = os.path.join(_HERE, "api", "crawl.py")

# `/dm/batch` 的门槛决议式（SSOT 归位后）。G5 的负控即「改回它就会变红」。
_SSOT_LINE = "min_score = max(0, _cfg_min) if _req_min <= 0 else max(0, _req_min)"
_LEGACY_LINE = "min_score = max(0, int(body.min_score or 0))"


def _src() -> str:
    with open(_CRAWL_PY, encoding="utf-8") as f:
        return f.read()


def _dm_batch_body(src: str) -> str:
    """切出 `crawl_dm_batch` 函数体（只到下一个顶层装饰器/定义为止）。"""
    m = re.search(r"^async def crawl_dm_batch\b.*?(?=^@router\.|^async def |^def |^class )",
                  src, re.M | re.S)
    if not m:
        raise AssertionError("找不到 crawl_dm_batch 函数体")
    return m.group(0)


def _resolve_gate(cfg_value, req_value) -> int:
    """复刻 `/dm/batch` 的门槛决议并返回生效门槛（从源码抽片段求值）。

    刻意与实现同源：若决议式被改回「只认请求体」，本函数返回的语义随之改变
    ⇒ 门禁变红。为避免 exec 整个模块（牵出 fastapi/db 等重依赖），
    只取决议所需的最小片段并 dedent。
    """
    src = _src()
    if _LEGACY_LINE in _dm_batch_body(src):
        raise AssertionError("`/dm/batch` 已退回「只认请求体」实现（契约漂移复发）")

    frag = []
    for pat in (
        r"^([ \t]*)_cfg_min = _crawl_cfg\([^\n]*\n",
        r"^([ \t]*)try:\n[ \t]*_cfg_min = int\(_cfg_min\)\n[ \t]*except \(TypeError, ValueError\):\n[ \t]*_cfg_min = 0\n",
        r"^([ \t]*)try:\n[ \t]*_req_min = int\(body\.min_score or 0\)\n[ \t]*except \(TypeError, ValueError\):\n[ \t]*_req_min = 0\n",
        r"^([ \t]*)" + re.escape(_SSOT_LINE) + r"\n",
    ):
        m = re.search(pat, src, re.M)
        if not m:
            raise AssertionError(
                f"抓不到门槛决议片段（pattern={pat!r}）—— 实现形态已变，门禁须更新")
        frag.append(m.group(0))

    code = textwrap.dedent("".join(frag))
    ns: dict = {
        "_crawl_cfg": lambda k, a: cfg_value,
        "body": type("B", (), {"min_score": req_value, "account": "acc1"})(),
        "max": max, "int": int, "logger": None,
    }
    exec(compile(code, "<gate>", "exec"), ns)  # noqa: S102 - 门禁自测内联求值
    return ns["min_score"]


class TestCrawlMinScoreSSOT(unittest.TestCase):

    def test_g1_config_center_is_source(self):
        """G1：配置中心是权威来源（请求体 0 时门槛仍生效）。"""
        self.assertEqual(_resolve_gate(50, 0), 50,
                         "配置=50、请求体=0 ⇒ 门槛必须是 50")

    def test_g2_zero_body_does_not_disable(self):
        """G2：请求体传 0 **不得**把门槛踩回不过滤。"""
        self.assertEqual(_resolve_gate(30, 0), 30, "请求体 0 = 不覆盖，不是不过滤")

    def test_g3_positive_body_overrides(self):
        """G3：请求体传正数 = 本次覆盖。"""
        self.assertEqual(_resolve_gate(10, 80), 80, "请求体 80 应覆盖配置 10")
        self.assertEqual(_resolve_gate(80, 10), 10, "请求体 10 应覆盖配置 80")

    def test_g4_negative_control_legacy_differs(self):
        """G4（负控）：旧实现（只认请求体）必须与新实现给出不同结果。"""
        legacy = max(0, int(0 or 0))               # 旧实现对 (cfg=30, req=0) ⇒ 0
        self.assertEqual(legacy, 0)
        self.assertNotEqual(_resolve_gate(30, 0), legacy,
                            "负控失效：新旧实现行为已无区别，门禁形同虚设")

    def test_g5_negative_control_regression_detection(self):
        """G5（负控）：决议式被改回旧写法 ⇒ 必须检出（变红）。"""
        body = _dm_batch_body(_src())
        self.assertIn(_SSOT_LINE, body, "现行实现必须含 SSOT 决议式")
        self.assertNotIn(_LEGACY_LINE, body,
                         "`/dm/batch` 不得残留「只认请求体」的旧决议式")

    def test_g6_comments_batch_still_accepts_body_min_score(self):
        """G6：`/comments/batch` 的请求体 min_score 仍被接受（不得误伤）。"""
        src = _src()
        m = re.search(r"^async def crawl_comments_batch\b.*?(?=^@router\.|^async def |^def |^class )",
                      src, re.M | re.S)
        self.assertIsNotNone(m, "找不到 crawl_comments_batch")
        self.assertIn("min_score", m.group(0),
                      "/comments/batch 必须仍接受请求体 min_score（本次覆盖入口）")
        model = re.search(r"class CrawlCommentsBatchRequest\b.*?(?=^@router\.|^class )",
                          src, re.M | re.S)
        self.assertIsNotNone(model)
        self.assertIn("min_score: int = 0", model.group(0),
                      "请求模型必须仍声明 min_score")


if __name__ == "__main__":
    unittest.main(verbosity=2)
