# -*- coding: utf-8 -*-
"""A-2 门禁：`backend/features.py` 基座封装层必须被 API 层**真实调用**（2026-09-28）。

## 为什么需要

`features.py`（24 个基座能力封装、136 行）此前**全仓零 import** —— 典型的
「已建未用」：能力在位、可发现性为零、用户路径根本不经过它。本门禁把
`api/crawl.py` 的两处真实调用点钉死：任何人把它改回直连 `DouyinAPI`
都会**变红**（负控自证，见文件末「负控」一节）。

## 判据（G1~G6）

  G1  `/search` 的 user 分支：必须经 `features.search_user`（记录调用、消费其 data）
  G2  `/comments`：必须经 `features.work_comments`（记录调用、消费其 data）
  G3  失败不得吞成成功：`features.*` 返回 `ok=False` ⇒ 端点必须抛 502
  G4  正常 `ok=True` 数据必须能正常映射返回（防「接通了但读错字段」）
  G5  直连哨兵有判别力：`DouyinAPI.search_some_user` 被直调 ⇒ 哨兵必抛
  G6  直连哨兵有判别力：`DouyinAPI.get_work_out_comment` 被直调 ⇒ 哨兵必抛

## 负控（真实变红复现）

本模块的 setUp 把 `DouyinAPI.search_some_user` / `get_work_out_comment`
替换成「被调用即抛 AssertionError」的哨兵。因此若 `api/crawl.py` 被改回
直连 DouyinAPI，G1/G2 会因为 `features.*` 未被调用而**立即变红**。
（施工时已真实执行过该负控，见 artifacts 报告。）

## 安全性

全部走**打桩**（替换 `features.*` 与 `DouyinAPI.*` 为内存桩），**零真实抖音请求**。
DY_APP_ROOT 指向新建临时目录，不污染仓库 `data/`。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
# 隔离根（**先 mkdir**：不存在的路径会被忽略，见 test_uid_sink_ext.py 注释）
_ROOT = tempfile.mkdtemp(prefix="dyautodm_a2feat_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

from fastapi import HTTPException  # noqa: E402


class _StubAuth:
    """最小 auth 桩：本门禁全程零真实请求，只需占位对象。"""


class _CallLog:
    """记录被调用的封装函数；返回预置值。"""

    def __init__(self, ret):
        self.calls: list = []
        self._ret = ret

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret


class TestFeaturesWiring(unittest.TestCase):
    """features 封装层是否真的接在 API 真实调用路径上。"""

    def setUp(self):
        import features
        from api import crawl as c
        from dy_apis.douyin_api import DouyinAPI

        self.features = features
        self.crawl = c

        # 打桩 _load_auth（避免真实凭证/账号校验）
        self._saved_load = c._load_auth
        c._load_auth = lambda account: _StubAuth()

        # 保存 features 真身，tearDown 还原（防跨用例污染）
        self._saved_search_user = features.search_user
        self._saved_work_comments = features.work_comments

        # 直连哨兵：DouyinAPI 若被直接调用即抛（证明走没走封装层）
        self._saved_ssu = DouyinAPI.search_some_user
        self._saved_gwoc = DouyinAPI.get_work_out_comment

        def _blocked(name):
            def _fn(*a, **k):
                raise AssertionError(
                    f"[负控] 直连 DouyinAPI.{name} 被调用 —— features 封装层被绕过")
            return staticmethod(_fn)
        DouyinAPI.search_some_user = _blocked("search_some_user")
        DouyinAPI.get_work_out_comment = _blocked("get_work_out_comment")

    def tearDown(self):
        from dy_apis.douyin_api import DouyinAPI
        DouyinAPI.search_some_user = self._saved_ssu
        DouyinAPI.get_work_out_comment = self._saved_gwoc
        self.features.search_user = self._saved_search_user
        self.features.work_comments = self._saved_work_comments
        self.crawl._load_auth = self._saved_load

    # ---------------- G1：search user 经 features ----------------
    def test_g1_search_user_goes_through_features(self):
        log = _CallLog({"ok": True, "data": [
            {"user_info": {"uid": "u1", "nickname": "甲", "sec_uid": "s1"}}]})
        self.features.search_user = log
        body = self.crawl.CrawlSearchRequest(
            account="acc", query="kw", kind="user", num=5)
        r = asyncio.run(self.crawl.crawl_search(body))
        self.assertEqual(len(log.calls), 1,
                         "features.search_user 未被调用 —— user 搜索未走封装层")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["uid"], "u1")
        self.assertEqual(r["items"][0]["nickname"], "甲")

    # ---------------- G2：comments 经 features ----------------
    def test_g2_comments_go_through_features(self):
        log = _CallLog({"ok": True, "data": {
            "comments": [{"cid": "c1", "text": "hi",
                          "user": {"uid": "u9", "nickname": "乙"}}],
            "has_more": 0, "cursor": "1"}})
        self.features.work_comments = log
        body = self.crawl.CrawlCommentsRequest(
            account="acc", aweme_id="123", limit=100)
        r = asyncio.run(self.crawl.crawl_comments(body))
        self.assertGreaterEqual(len(log.calls), 1,
                                "features.work_comments 未被调用 —— 评论未走封装层")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["cid"], "c1")
        self.assertEqual(r["items"][0]["nickname"], "乙")

    # ---------------- G3：失败不得吞成成功 ----------------
    def test_g3_search_failure_is_not_swallowed(self):
        log = _CallLog({"ok": False, "error": "被风控"})
        self.features.search_user = log
        body = self.crawl.CrawlSearchRequest(
            account="acc", query="kw", kind="user", num=5)
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(self.crawl.crawl_search(body))
        self.assertEqual(cm.exception.status_code, 502,
                         "features 失败竟未按原语义抛 502（假成功）")
        self.assertEqual(len(log.calls), 1, "未走 features.search_user（负控点）")

    def test_g3b_comments_failure_is_not_swallowed(self):
        log = _CallLog({"ok": False, "error": "被风控"})
        self.features.work_comments = log
        body = self.crawl.CrawlCommentsRequest(
            account="acc", aweme_id="123", limit=100)
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(self.crawl.crawl_comments(body))
        self.assertEqual(cm.exception.status_code, 502,
                         "features 评论失败竟未抛 502（假成功）")
        self.assertEqual(len(log.calls), 1, "未走 features.work_comments（负控点）")

    # ---------------- G4：ok=True 数据正常可用（防字段读错）----------------
    def test_g4_ok_true_empty_data_is_empty_not_error(self):
        self.features.search_user = _CallLog({"ok": True, "data": []})
        body = self.crawl.CrawlSearchRequest(
            account="acc", query="kw", kind="user", num=5)
        r = asyncio.run(self.crawl.crawl_search(body))
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["items"], [])

    # ---------------- G5/G6：直连哨兵有判别力（负控自证）----------------
    def test_g5_direct_search_sentinel_is_in_discriminating(self):
        from dy_apis.douyin_api import DouyinAPI
        with self.assertRaises(AssertionError) as cm:
            DouyinAPI.search_some_user(_StubAuth(), "kw", 5)
        self.assertIn("负控", str(cm.exception))

    def test_g6_direct_comments_sentinel_is_discriminating(self):
        from dy_apis.douyin_api import DouyinAPI
        with self.assertRaises(AssertionError) as cm:
            DouyinAPI.get_work_out_comment(_StubAuth(), "u", "0")
        self.assertIn("负控", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
