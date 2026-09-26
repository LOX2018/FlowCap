# -*- coding: utf-8 -*-
"""M-20 门禁：搜索链路的**传输层事实上抛**（2026-09-27）。

## 为什么需要

项目铁律「禁止假成功」要求：**被风控拦截 ≠ 真的没有结果**。
搜索链路上有两处会把前者**静默降级**成后者：

1. `search_some_general_work` 原返回**裸 list** —— 传输层事实（`_transport`）
   无处承载 ⇒ 上层只能把拦截读成「该关键词没有作品」。
2. `search_stream` 归一化返回体原**不含** `_transport` —— 被 Argus 拦截时
   `buf` 只有 46 字节错误页、chunked 分块解析全失败 ⇒ `aweme_list=[]`
   且 `status_code=0`，与「真的搜不到」**形态完全相同**。

两者的后果同族：前端 `items: []` → 渲染空列表 → 用户以为「没这个关键词」。

## 判据（G1~G6）

  G1  注入 403 空响应 → `search_some_general_work` 返回值必须携带 `last_transport`
  G2  注入 403 46B  → `search_stream` 返回体 `["_transport"]` 必须非 None
  G3  注入正常 200  → 两者 transport 均须为 None（**不得误报**）
  G4  负控：裸 `list` 挂属性必抛 `AttributeError`（根因自证，防有人改回裸 list）
  G5  `take_search_transport` 兼容 `LiveSearchResult` 与 `dict` 两种承载形态
  G6  负控：把 `search_some_general_work` 的返回值改回裸 list ⇒ G1 必须红

## 安全性
全部走**打桩**（替换 `requests.get` 为内存桩），**零真实抖音请求**。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")


class _StubResp:
    """内存响应桩：模拟真实 `requests.Response` 的 `status_code`/`content`/`json()`。

    ⚠️ `json()` **必须**实现：`safe_json()` 正是靠调它来区分「可解析」与
    「降级」。少了它，所有响应都会走降级分支 ⇒ 正常 200 被误判成「被拦截」
    （实测踩到：G3 报红，根因在桩不在产品代码）。

    `json_obj=None` 表示该响应**不可解析**（403 错误页的真实形态）。
    """

    def __init__(self, status_code: int, content: bytes, json_obj=None):
        self.status_code = status_code
        self.content = content
        self._json_obj = json_obj

    def json(self):
        if self._json_obj is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)  # 非 JSON（错误页）")
        return self._json_obj


class _StubAuth:
    """最小 auth 桩：满足两个端点实际访问的字段（`cookie` / `msToken`）。

    字段清单由 grep 实测得出（勿凭记忆增删）：
      `search_general_work` → auth.cookie, auth.msToken
      `search_stream`       → auth.cookie
    签名/指纹接口已全部打桩，故不需要真实凭证 —— **零真实抖音请求**。
    """

    cookie = "stub=1"
    msToken = "stub_ms_token"


def _install_stubs(resp: _StubResp):
    """把 requests.get 与签名/指纹接口换成桩，返回被替换对象的引用。"""
    from dy_apis import client_search as cs
    from builder import params as bp

    saved = {
        "get": cs.requests.get,
        "signed_url": bp.Params.signed_url,
        "with_web_id": bp.Params.with_web_id,
        "with_a_bogus": bp.Params.with_a_bogus,
    }
    cs.requests.get = lambda *a, **k: resp          # 零真实网络
    bp.Params.signed_url = lambda self, base, auth=None, ts=None: base
    bp.Params.with_web_id = lambda self, auth=None, url="", fake=False: self
    bp.Params.with_a_bogus = lambda self, data=None, host="www.douyin.com": self
    return saved


def _restore_stubs(saved):
    from dy_apis import client_search as cs
    from builder import params as bp
    cs.requests.get = saved["get"]
    bp.Params.signed_url = saved["signed_url"]
    bp.Params.with_web_id = saved["with_web_id"]
    bp.Params.with_a_bogus = saved["with_a_bogus"]


# 403 的真实形态（实测 46B `Blocked by ArgusSecurityPlugin Uifid Not Found`）
_ERR_403 = b"Blocked by ArgusSecurityPlugin Uifid Not Found"
# 正常响应：两个端点的**线格式不同**，桩必须分别如实模拟（勿混用）
_OK_OBJ = {"status_code": 0, "data": [{"aweme_info": {"aweme_id": "1"}}]}
_OK_BODY = json.dumps(_OK_OBJ).encode()
# single 搜索：普通 JSON 体
_OK_JSON = _StubResp(200, _OK_BODY, json_obj=_OK_OBJ)
# stream 搜索：chunked 分块（十六进制长度前缀 + CRLF + JSON + CRLF）
_OK_CHUNKED = _StubResp(200, b"%x\r\n" % len(_OK_BODY) + _OK_BODY + b"\r\n")


class TestM20SearchTransport(unittest.TestCase):

    def test_g1_general_work_carries_transport_on_403(self):
        """G1：403 空响应 ⇒ `search_some_general_work` 必须带出传输层事实。"""
        from dy_apis.douyin_api import DouyinAPI  # mixin 组装处（client_search 只定义 SearchMixin）
        saved = _install_stubs(_StubResp(403, _ERR_403))
        try:
            r = DouyinAPI.search_some_general_work(
                _StubAuth(), "kw", 5, "0", "0")
        finally:
            _restore_stubs(saved)
        self.assertEqual(list(r), [], "403 下本就无结果（列表内容不是本判据重点）")
        self.assertIsNotNone(
            getattr(r, "last_transport", None),
            "403 被拦截时未带出传输层事实 —— 上层只能读成「没搜到」（假成功）")

    def test_g2_stream_carries_transport_on_403(self):
        """G2：403 46B ⇒ `search_stream` 返回体 `_transport` 必须非 None。"""
        from dy_apis.douyin_api import DouyinAPI  # mixin 组装处（client_search 只定义 SearchMixin）
        saved = _install_stubs(_StubResp(403, _ERR_403))
        try:
            r = DouyinAPI.search_stream(_StubAuth(), "kw")
        finally:
            _restore_stubs(saved)
        self.assertEqual(r.get("aweme_list"), [], "403 下无结果")
        self.assertIsNotNone(
            r.get("_transport"),
            "403 被拦截时 `_transport` 为 None —— 与「真的搜不到」无法区分（假成功）")
        self.assertEqual(r["_transport"].get("status"), 403)

    def test_g3_normal_response_no_false_positive(self):
        """G3：正常 200 ⇒ 两者 transport 均须为 None（不得误报为被拦截）。"""
        from dy_apis.douyin_api import DouyinAPI  # mixin 组装处（client_search 只定义 SearchMixin）
        saved = _install_stubs(_OK_JSON)
        try:
            gw = DouyinAPI.search_some_general_work(_StubAuth(), "kw", 5, "0", "0")
        finally:
            _restore_stubs(saved)
        saved = _install_stubs(_OK_CHUNKED)
        try:
            st = DouyinAPI.search_stream(_StubAuth(), "kw")
        finally:
            _restore_stubs(saved)
        self.assertEqual(gw, [{"aweme_info": {"aweme_id": "1"}}],
                         "正常 200 下应正常拿到作品（桩或产品逻辑有误）")
        self.assertIsNone(getattr(gw, "last_transport", None),
                          "正常响应被误报为「被风控拦截」（门禁过宽）")
        self.assertIsNone(st.get("_transport"),
                          "正常响应被误报为「被风控拦截」（门禁过宽）")

    def test_g4_negative_control_bare_list_rejects_attribute(self):
        """G4 负控（根因自证）：裸 `list` 挂属性必抛 ⇒ 当年「吞异常」才会丢事实。"""
        with self.assertRaises(AttributeError):
            lst = []
            lst.last_transport = {"status": 403}   # noqa: B010

    def test_g5_take_search_transport_handles_both_forms(self):
        """G5：统一读取须兼容两种承载形态（list 子类 / dict）。"""
        from dy_apis.douyin_api import DouyinAPI
        from dy_apis.client_search import LiveSearchResult
        d = {"status": 403, "bytes": 46}
        self.assertEqual(DouyinAPI.take_search_transport(LiveSearchResult([], d)), d)
        self.assertEqual(DouyinAPI.take_search_transport({"_transport": d}), d)
        # 无事实时一律 None（不得伪造）
        self.assertIsNone(DouyinAPI.take_search_transport(LiveSearchResult([], None)))
        self.assertIsNone(DouyinAPI.take_search_transport({"_transport": None}))
        self.assertIsNone(DouyinAPI.take_search_transport(None))

    def test_g6_negative_control_gate_catches_bare_list_regression(self):
        """G6 负控：判据必须能抓到「改回裸 list」的回归。"""
        saved = _install_stubs(_StubResp(403, _ERR_403))
        try:
            from dy_apis.client_search import LiveSearchResult
            # 模拟回归：把返回改回裸 list（丢掉 transport）
            def _regressed(work_list, transport):
                return list(work_list)
            regressed = _regressed([], {"status": 403})
        finally:
            _restore_stubs(saved)
        self.assertIsNone(getattr(regressed, "last_transport", None),
                          "负控构造有误（裸 list 竟带了属性）")
        # 而正解（LiveSearchResult）必须带得住 —— 证明 G1 判据有判别力
        self.assertIsNotNone(getattr(LiveSearchResult([], {"status": 403}),
                                     "last_transport", None),
                             "LiveSearchResult 竟承载不住 transport ⇒ G1 判据失效")


if __name__ == "__main__":
    unittest.main(verbosity=2)