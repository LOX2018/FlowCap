# -*- coding: utf-8 -*-
"""门禁：评论采集效率与「多作品串行批量」（★ 2026-09-30，v0.45.125）。

## 为什么需要本门禁（防复发）

三件事同时被钉死，任何一件回退都会**变红**：

  ① **单页条数不得再被硬编码为 5**（原实现 `count="5"` ⇒ 采 100 条发 20 次请求）。
     实测服务端支持 20~50 条/页；本门禁断言「请求的 count 真的传到了基座」。
  ② **末级取数链路必须单一**：`/comments` 与 `/batch` 曾各写一套翻页
     （`cases/2026-09-28_内容中心评论区恒加载_末级链路分叉修复` 的反模式）。
     本门禁断言两条路径都经 `features.work_comments`。
  ③ **多作品批量必须串行 + 间隔**（同端点高频并发是账号级限流高发区）：
     断言两作品之间真的 `sleep(interval)`，且顺序不重叠。

## 负控（真实变红）
把 `_fetch_work_comments` 里的 count 改回固定 `"5"` ⇒ E1/E2 红；
把 `/batch` 改回直连 `DouyinAPI` ⇒ E4 红（直连哨兵抛出）；
去掉 `asyncio.sleep(interval)` ⇒ E5 红（间隔用时 < 阈值）。

## 安全性
全程打桩，**零真实抖音请求**；DY_APP_ROOT 指向临时目录，不碰仓库 data/。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
_ROOT = tempfile.mkdtemp(prefix="dyautodm_crawleff_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

import services.app_config as ac  # noqa: E402


class _StubAuth:
    pass


class _Feed:
    """记录调用并按预置页序列返回的 features.work_comments 桩。

    pages: [(comments, has_more, cursor), ...]；用尽后返回空批次。
    """

    def __init__(self, pages=None, fail=False):
        self.calls: list[tuple] = []
        self._pages = list(pages or [])
        self._fail = fail

    def __call__(self, auth, url, cursor="0", count="20"):
        self.calls.append((auth, url, cursor, count))
        if self._fail:
            return {"ok": False, "error": "被风控"}
        if self._pages:
            comments, has_more, cur = self._pages.pop(0)
        else:
            comments, has_more, cur = [], 0, ""
        return {"ok": True, "data": {"comments": comments, "has_more": has_more,
                                     "cursor": cur}}


def _c(cid):
    return {"cid": cid, "text": "t", "user": {"uid": "u" + cid, "nickname": "n"}}


class TestCommentEfficiency(unittest.TestCase):
    def setUp(self):
        self.features = sys.modules.get("features") or __import__("features")
        from api import crawl as c
        self.crawl = c
        self._saved_load = c._load_auth
        c._load_auth = lambda account: _StubAuth()
        self._saved_wc = self.features.work_comments
        # 间隔置 0，避免测试真等；间隔断言单独用例显式设置
        ac.reset_section("crawl")
        ac.save_section("crawl", {"batch_interval": 0.0})

    def tearDown(self):
        self.features.work_comments = self._saved_wc
        self.crawl._load_auth = self._saved_load
        ac.reset_section("crawl")

    # ---------------- E1：count 真实传导到基座 ----------------
    def test_e1_page_count_reaches_base(self):
        feed = _Feed()
        self.features.work_comments = feed
        body = self.crawl.CrawlCommentsRequest(
            account="acc", aweme_id="123", limit=100, count=50)
        asyncio.run(self.crawl.crawl_comments(body))
        self.assertTrue(feed.calls, "features.work_comments 未被调用")
        got = feed.calls[0][3]
        self.assertEqual(int(got), 50,
                         f"单页条数未传导到基座（得到 {got!r}）—— 是否又硬编码回 5？")

    # ---------------- E2：页数收敛（100 条 / 每页 50 ⇒ ≤3 页）----------------
    def test_e2_pages_collapse_not_twenty(self):
        # 语料：前两页各 50 条（has_more=1），第三页空 → 至多 3 次请求
        feed = _Feed(pages=[
            ([_c(str(i)) for i in range(50)], 1, "50"),
            ([_c(str(50 + i)) for i in range(50)], 0, "100"),
        ])
        self.features.work_comments = feed
        body = self.crawl.CrawlCommentsRequest(
            account="acc", aweme_id="123", limit=100, count=50)
        r = asyncio.run(self.crawl.crawl_comments(body))
        self.assertLessEqual(len(feed.calls), 3,
                             f"请求数 {len(feed.calls)} 未收敛（旧行为=20 次）")
        self.assertEqual(r["total"], 100)

    # ---------------- E3：派生上限与 clamp ----------------
    def test_e3_clamp_and_config_read(self):
        ac.save_section("crawl", {"comment_page_count": 50})
        self.assertEqual(self.crawl._page_count(0), 50, "未读配置中心值")
        self.assertEqual(self.crawl._page_count(999), 50, "上限未 clamp 到 50")
        self.assertEqual(self.crawl._page_count(1), 5, "下限未 clamp 到 5")
        self.assertEqual(self.crawl._page_count(20), 20, "显式入参被忽略")

    # ---------------- E4：/batch 也走 features（单一链路，无直连）----------------
    def test_e4_batch_uses_features_not_direct(self):
        from dy_apis.douyin_api import DouyinAPI
        saved = DouyinAPI.get_work_out_comment

        def _blocked(*a, **k):
            raise AssertionError("[负控] /batch 直连 DouyinAPI —— 末级链路又分叉了")
        DouyinAPI.get_work_out_comment = staticmethod(_blocked)

        feed = _Feed()
        self.features.work_comments = feed
        try:
            body = self.crawl.CrawlBatchRequest(
                account="acc", aweme_id="123", text="hi", limit=10, count=20)
            # max_send=0 ⇒ 不发送；但会先采评论
            asyncio.run(self.crawl.crawl_batch(body))
        finally:
            DouyinAPI.get_work_out_comment = saved
        self.assertTrue(feed.calls, "/batch 未走 features.work_comments")

    # ---------------- E5：多作品串行 + 间隔 ----------------
    def test_e5_multi_work_is_serial_with_interval(self):
        ac.save_section("crawl", {"batch_interval": 0.3})
        feed = _Feed()
        self.features.work_comments = feed
        body = self.crawl.CrawlCommentsBatchRequest(
            account="acc", aweme_ids=["a1", "a2", "a3"], limit=10, count=20)
        t0 = time.time()
        r = asyncio.run(self.crawl.crawl_comments_batch(body))
        dt = time.time() - t0
        self.assertEqual(r["works"], 3)
        # 3 个作品 ⇒ 2 个间隔
        self.assertGreaterEqual(dt, 0.55,
                                f"批量未按间隔串行（用时 {dt:.2f}s < 2×0.3s）")
        self.assertEqual(len(feed.calls), 3, "作品数≠取数次数")

    # ---------------- E6：失败隔离（单作品失败不拖垮整批）----------------
    def test_e6_single_failure_does_not_abort_batch(self):
        ac.save_section("crawl", {"batch_interval": 0.0})
        calls = {"n": 0}

        def _flaky(auth, url, cursor="0", count="20"):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": False, "error": "被风控"}
            return {"ok": True, "data": {"comments": [_c("x")], "has_more": 0,
                                         "cursor": "1"}}
        self.features.work_comments = _flaky
        body = self.crawl.CrawlCommentsBatchRequest(
            account="acc", aweme_ids=["bad", "good"], limit=10, count=20)
        r = asyncio.run(self.crawl.crawl_comments_batch(body))
        st = [w["status"] for w in r["per_work"]]
        self.assertEqual(st, ["failed", "ok"],
                         f"失败未隔离或被吞（{st}）—— 不得把失败静默成成功")
        self.assertEqual(r["ok_works"], 1)
        self.assertEqual(r["total_comments"], 1)

    # ---------------- E7：作品数上限（防误点长跑）----------------
    def test_e7_batch_work_cap(self):
        from fastapi import HTTPException
        ac.save_section("crawl", {"batch_max_works": 2})
        self.features.work_comments = _Feed()
        body = self.crawl.CrawlCommentsBatchRequest(
            account="acc", aweme_ids=["a", "b", "c"], limit=10)
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(self.crawl.crawl_comments_batch(body))
        self.assertEqual(cm.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
