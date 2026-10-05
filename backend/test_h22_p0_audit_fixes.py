# -*- coding: utf-8 -*-
"""H-22 审计 P0 修复的机械门禁（防复发）—— 2026-09-26。

## 为什么需要（本案教训）
4 条 P0 全部是「**主链路静默失效**」型缺陷：WS 收消息全废、消息去重/归属错乱、
受保护流 403 回归、并发永久丢消息。它们的共同特征是**测试全绿也照样上线**
（既有门禁只覆盖各自单元，不覆盖这些跨模块契约），故必须专门补机械判据。

## 四条判据（每条都来自一次实测复现）
  G1  daemon/recv_daemon.py ：`RecvChannel` **必须**直接持有 `_extract` 方法
      —— 防止「模块级函数被插进类体中间 ⇒ 后续方法被吞成其嵌套函数」
      （实测：`_extract` 被解析为 `_msg_extra_json` 的嵌套函数 ⇒ `self._extract` AttributeError）。
  G2  auto_dm/conversation_capture.py ：`_rec_of(...).tuple(...)` **必须**显式传
      `ts` / `msg_id` / `role` —— 防止漏传致 `ts=0.0 / msg_id=None / role="them"`
      （实测：2 条真实 me 消息被 uniq 索引吞成 1 条 + 收发归属反转）。
  G3  downloader/media_request.py ：`resolve_playable` 的请求头 **必须**含 `Referer`
      —— 抖音 CDN 按 Referer 白名单放行，缺该头必 403（实测案例 v0.45.9）。
  G4  services/ai_reply.py ：`_tick` 的水位推进 **必须**晚于 in-flight 判定，
      且 in-flight 分支 **必须** `break`（不得 `continue` 越过）—— 防止永久丢消息
      （实测：同会话 id 5&6 同 tick，修复前 6 永不回捞）。

每条判据都配**负控**：把「旧形态」源码喂给同一断言函数，必须判为失败。
（旧形态源码取自真实提交态，非杜撰。）
"""
from __future__ import annotations

import ast
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)          # .../FlowCap
_BACKEND = _HERE                        # .../FlowCap/backend


def _read(rel: str) -> str:
    with open(os.path.join(_BACKEND, rel), encoding="utf-8") as f:
        return f.read()


def _func_src(src: str, name: str) -> str | None:
    """取某函数定义的源码片段（含 def 行）。"""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return "".join(lines[n.lineno - 1:n.end_lineno])
    return None


# ══════════════════════════ 判据实现（可被负控复用） ══════════════════════════

def check_g1(source: str) -> tuple[bool, str]:
    """RecvChannel 类必须直接持有 _extract 方法。"""
    tree = ast.parse(source)
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "RecvChannel":
            meths = [m.name for m in n.body
                     if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if "_extract" in meths:
                return True, f"RecvChannel 直接持有 _extract（共 {len(meths)} 方法）"
            return False, f"RecvChannel 缺 _extract（现有 {meths}）"
    return False, "未找到 RecvChannel 类"


def check_g2(source: str) -> tuple[bool, str]:
    """所有 `_rec_of(...).tuple(...)` 必须显式传 ts/msg_id/role。"""
    tree = ast.parse(source)
    bad = []
    total = 0
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "tuple"):
            continue
        v = f.value
        if not (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == "_rec_of"):
            continue
        total += 1
        kws = {k.arg for k in n.keywords if k.arg}
        if not {"ts", "msg_id", "role"} <= kws:
            bad.append((n.lineno, sorted(kws)))
    if total == 0:
        return False, "未找到任何 _rec_of(...).tuple(...) 调用（判据失效？）"
    if bad:
        return False, f"{len(bad)}/{total} 处漏传字段: {bad}"
    return True, f"{total} 处全部显式传 ts/msg_id/role"


def check_g3(source: str) -> tuple[bool, str]:
    """resolve_playable 的 headers 必须含 Referer。"""
    fn = _func_src(source, "resolve_playable")
    if not fn:
        return False, "未找到 resolve_playable"
    if re.search(r'["\']Referer["\']\s*:', fn):
        return True, "resolve_playable 请求头含 Referer"
    return False, "resolve_playable 请求头缺 Referer（CDN 必 403）"


def check_g4(source: str) -> tuple[bool, str]:
    """_tick 水位推进必须晚于 in-flight 判定，且 in-flight 分支须 break。"""
    fn = _func_src(source, "_tick")
    if not fn:
        return False, "未找到 _tick"
    lines = fn.splitlines()
    i_inflight = i_lastid = i_break = None
    for i, l in enumerate(lines):
        if "if _skey in self._inflight" in l:
            i_inflight = i
        if "last_id = max(last_id, r[\"id\"])" in l:
            i_lastid = i
        if l.strip() == "break":
            i_break = i
    if i_inflight is None:
        return False, "未找到 in-flight 判定"
    if i_lastid is None:
        return False, "未找到水位推进语句"
    if i_break is None:
        return False, "in-flight 分支缺少 break（不得 continue 越过 ⇒ 会永久丢消息）"
    if i_lastid < i_inflight:
        return False, f"水位推进(line {i_lastid}) 早于 in-flight 判定(line {i_inflight}) ⇒ 丢消息"
    if not (i_inflight < i_break < i_lastid):
        return False, f"语句次序异常（inflight={i_inflight} break={i_break} lastid={i_lastid}）"
    return True, "水位推进晚于 in-flight 判定且分支为 break"


# ══════════════════════════ 正向断言（真实文件） ══════════════════════════

class TestP0RealFiles(unittest.TestCase):
    def test_g1_recv_channel_extract(self):
        ok, msg = check_g1(_read(os.path.join("daemon", "recv_daemon.py")))
        self.assertTrue(ok, f"G1 失败: {msg}")

    def test_g2_capture_tuple_fields(self):
        ok, msg = check_g2(_read(os.path.join("auto_dm", "conversation_capture.py")))
        self.assertTrue(ok, f"G2 失败: {msg}")

    def test_g3_media_referer(self):
        ok, msg = check_g3(_read(os.path.join("downloader", "media_request.py")))
        self.assertTrue(ok, f"G3 失败: {msg}")

    def test_g4_tick_watermark_order(self):
        ok, msg = check_g4(_read(os.path.join("services", "ai_reply.py")))
        self.assertTrue(ok, f"G4 失败: {msg}")


# ══════════════════════════ 负控（旧形态必须判红） ══════════════════════════

# G1 旧形态：_extract 缩进 4 但被模块级函数包住（真实事故形态的等价最小样本）
_G1_OLD = '''\
class RecvChannel:
    def _sync_conversations(self):
        pass

def _msg_extra_json(m):
    ex = {}
    return "{}"

    @staticmethod
    def _extract(content_json, msg_type):
        return None, {}
'''

# G2 旧形态：漏传三字段（真实提交态原文）
_G2_OLD = '''\
def _rec_of(m, extra):
    return MessageRecord.build(text="", msg_type="", extra={}, role=None)

def f():
    conn.execute(SQL, _rec_of(m, _extra).tuple(name, cid))
'''

# G3 旧形态：headers 无 Referer（真实提交态原文）
_G3_OLD = '''\
def resolve_playable(url, *, timeout=10.0, max_redirects=5):
    try:
        headers = {
            "User-Agent": __import__("utils.fingerprint", fromlist=["user_agent"]).user_agent(),
            "Accept": "*/*",
        }
        return (url, "direct")
    except Exception:
        return (url, "passthrough")
'''

# G4 旧形态：水位在循环首 + continue（真实提交态原文）
_G4_OLD = '''\
def _tick(self):
    last_id = 0
    rows = []
    for r in rows:
        last_id = max(last_id, r["id"])
        _kv_set(_KV_MARKER, last_id)
        _skey = "%s:%s" % (r["account"], r["conv_id"])
        with self._sess_locks_guard:
            if _skey in self._inflight:
                continue
            self._inflight.add(_skey)
'''


class TestP0NegativeControls(unittest.TestCase):
    """负控：把旧形态喂给同一断言函数，必须判红。"""

    def test_g1_detects_nested_extract(self):
        ok, msg = check_g1(_G1_OLD)
        self.assertFalse(ok, f"G1 负控失效（旧形态被判绿）: {msg}")

    def test_g2_detects_missing_fields(self):
        ok, msg = check_g2(_G2_OLD)
        self.assertFalse(ok, f"G2 负控失效（漏传被判绿）: {msg}")

    def test_g3_detects_missing_referer(self):
        ok, msg = check_g3(_G3_OLD)
        self.assertFalse(ok, f"G3 负控失效（缺 Referer 被判绿）: {msg}")

    def test_g4_detects_early_watermark(self):
        ok, msg = check_g4(_G4_OLD)
        self.assertFalse(ok, f"G4 负控失效（旧水位被判绿）: {msg}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
