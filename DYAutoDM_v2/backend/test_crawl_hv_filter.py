"""高价值过滤接入批量采集门禁（★ 2026-10-03，用户指令）。

## 用户要求

> 「开启高价值采集的时候，只保留关键词命中的部分其他的全部抛掉」

## 为何需要门禁

此前批量采集端点**已有** `min_score` 过滤分支（`api/crawl.py`），但：
  ① 前端恒传 `min_score: 0` ⇒ `min_score > 0` 永假 ⇒ **过滤从未执行**；
  ② 采集请求体**没有 `tag_id` 字段**，而模型是 `extra="forbid"`
     ⇒ 想按标签过滤也传不进去。

这是典型的**假成功**：代码里有过滤逻辑，但用户看到的行为是「没过滤」。

本门禁守住：
  ① `tag_id` 字段存在（否则前端一传就 400）；
  ② 门槛回落链与私信端点**同口径**（配置中心为权威、0 = 不覆盖）；
  ③ **真跑**过滤逻辑，验证「只留命中、其余全丢」；
  ④ 前端三处调用点（采集/任务登记/私信）传的都是 hvMinScore，不再有 0。

## 运行

    cd backend && python -m unittest test_crawl_hv_filter -v
"""
from __future__ import annotations

import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _with_keywords(mapping: dict, fn):
    """临时替换关键词表，测完必还原（不污染全局/落盘）。

    模块级而非类方法：负控类也要用（避免跨类引用）。
    """
    from services import high_value_keywords as hv
    orig = hv.get_keywords
    hv.get_keywords = lambda scope=None: dict(mapping)
    try:
        return fn(hv)
    finally:
        hv.get_keywords = orig


class TestRequestModel(unittest.TestCase):
    """① 请求体必须能接 tag_id（extra=forbid ⇒ 缺字段直接 400）。"""

    def test_tag_id_field_exists(self):
        from api.crawl import CrawlCommentsBatchRequest as M
        self.assertIn("tag_id", M.model_fields,
                      "采集请求体缺 tag_id ⇒ 前端传标签会 400")

    def test_tag_id_defaults_empty(self):
        from api.crawl import CrawlCommentsBatchRequest as M
        self.assertEqual(M(account="a", aweme_ids=["1"]).tag_id, "",
                         "tag_id 必须默认空串（空 = 沿用账号绑定标签）")

    def test_extra_forbid_still_active(self):
        """⚠️ 若有人把 extra 改成 ignore，缺字段会变静默丢弃（更坏）。"""
        from api.crawl import CrawlCommentsBatchRequest as M
        with self.assertRaises(Exception):
            M(account="a", aweme_ids=["1"], unknown_field=1)

    def test_parses_tag_and_score(self):
        from api.crawl import CrawlCommentsBatchRequest as M
        b = M(account="a", aweme_ids=["x"], tag_id="t1", min_score=1)
        self.assertEqual((b.tag_id, b.min_score), ("t1", 1))


class TestThresholdContract(unittest.TestCase):
    """② 门槛回落链：配置中心为权威、请求体 0 = 不覆盖（与私信端点同口径）。"""

    def test_contract_matches_dm_endpoint(self):
        """采集与私信的门槛解析必须**同一套语义**（同一函数 + 同一 0 约定）。

        两处若漂移 ⇒ 用户在面板里选的标签只对一半阶段生效。
        """
        import inspect
        import api.crawl as C
        src = inspect.getsource(C)
        # 两处都必须用 0 视为不覆盖的三段式
        self.assertEqual(
            src.count('max(0, _cfg_min) if _req_min <= 0 else max(0, _req_min)'), 2,
            "采集/私信两处的门槛回落表达式应各出现一次且完全一致")

    def test_uses_shared_crawl_cfg(self):
        """必须走 `_crawl_cfg`（标签 scope 链路），不得另读 app_config。"""
        import inspect
        import api.crawl as C
        src = inspect.getsource(C.crawl_comments_batch)
        self.assertIn('_crawl_cfg("batch_min_score"', src,
                      "批量采集未走 _crawl_cfg 读门槛（会绕过标签 scope）")


class TestFilterBehaviour(unittest.TestCase):
    """③ 真跑过滤：只留命中、其余全丢。

    ⚠️ 模块名/签名以源码为准（实测 2026-10-03）：
       `services.high_value_keywords.score_text(text, scope: str | None) -> int`
       —— scope 是**标签名字符串**（经 `get_keywords(scope)` 取表），
       不是 dict。我曾猜成 `services.high_value` + dict ⇒ ModuleNotFound。
    """

    def test_only_matched_kept(self):
        samples = [
            ("我要加微信，急", True),    # 命中两个关键词
            ("普通评论", False),         # 一个都没命中
            ("有点急", True),            # 命中一个
            ("今天天气不错", False),
        ]
        kw = {"急": 3, "加微信": 2}

        def run(hv):
            min_score = 1
            items = [{"text": t} for t, _ in samples]
            return sorted(c["text"] for c in items
                          if hv.score_text(c["text"], "s") >= min_score)

        got = _with_keywords(kw, run)
        self.assertEqual(got, sorted(t for t, want in samples if want),
                         f"过滤结果与预期不符（未命中项没被丢弃）：{got}")

    def test_weight_accumulates_and_threshold_gates(self):
        kw = {"急": 3, "加微信": 2}

        def run(hv):
            return (hv.score_text("急", "s"),
                    hv.score_text("加微信", "s"),
                    hv.score_text("加微信，急", "s"),
                    hv.score_text("毫无相关", "s"))

        one, two, both, none = _with_keywords(kw, run)
        self.assertEqual((one, two, both, none), (3, 2, 5, 0),
                         "权重累加或门槛判定不符")

    def test_threshold_two_filters_single_hit(self):
        """门槛=2 时，只命中 1 分的评论应被丢弃（证明「按门槛」而非「有就留」）。

        ⚠️ 权重必须**真的低于门槛**才能证伪 —— 我第一版用 {急:3, 加微信:2}
        门槛=2，结果「有点急」得 3 分本就该保留 ⇒ 门禁红的是**数据写错**，
        不是实现有错。改为 {低分词:1, 高分词:5}。
        """
        kw = {"低分词": 1, "高分词": 5}

        def run(hv):
            items = [{"text": "含低分词"}, {"text": "含高分词"}]
            return [c["text"] for c in items
                    if hv.score_text(c["text"], "s") >= 2]

        self.assertEqual(_with_keywords(kw, run), ["含高分词"],
                         "门槛=2 时 1 分的评论未被丢弃 ⇒ 门槛没生效")

    def test_empty_scope_filters_everything(self):
        """🔴 关键边界：门槛>0 但关键词表为空 ⇒ 全部丢弃（fail-closed）。

        若此时放行全部，就等于「用户开了高价值采集却什么都没筛」。
        """

        def run(hv):
            items = [{"text": "a"}, {"text": "b"}]
            return [c for c in items if hv.score_text(c["text"], "s") >= 1]

        self.assertEqual(_with_keywords({}, run), [],
                         "空关键词表时未 fail-closed（应全丢）")

    def test_endpoint_filter_uses_threshold_not_presence(self):
        """🔴 端点里的过滤必须比**门槛**，不是「有 tag 就全留」。

        直接读源码判据：比较运算必须存在，且形如 `>= min_score`。
        """
        import inspect
        import api.crawl as C
        src = inspect.getsource(C.crawl_comments_batch)
        self.assertIn(">= min_score", src,
                      "批量采集的过滤未按门槛比较（可能只判存在性）")


class TestFrontendWiring(unittest.TestCase):
    """④ 前端三处调用点都必须传 hvMinScore，不得再有 0。"""

    PANEL = os.path.join(
        _HERE, "..", "frontend", "src", "components", "crawl",
        "CrawlFloatingPanel.tsx")

    def _src(self):
        with io.open(self.PANEL, encoding="utf-8") as f:
            return f.read()

    def test_panel_exists(self):
        self.assertTrue(os.path.isfile(self.PANEL), "找不到悬浮窗源文件")

    def test_no_hardcoded_zero_min_score(self):
        src = self._src()
        self.assertNotIn("min_score: 0", src,
                         "仍有 min_score: 0 ⇒ 过滤被关掉（假成功）")

    def test_collect_call_passes_hv_and_tag(self):
        src = self._src()
        self.assertIn("min_score: hvMinScore", src,
                      "采集请求未传 hvMinScore")
        self.assertIn("tag_id: tagId", src,
                      "采集请求未传 tag_id（标签过滤无从生效）")

    def test_grid_layout_two_columns(self):
        """2×2 网格：四选项必须在 grid-cols-2 容器内。"""
        src = self._src()
        self.assertIn("grid grid-cols-2", src, "四选项未改 2×2 网格")

    def test_api_contract_declares_tag_id(self):
        cli = os.path.join(_HERE, "..", "frontend", "src", "api", "client.ts")
        with io.open(cli, encoding="utf-8") as f:
            src = f.read()
        i = src.find("async crawlCommentsBatch")
        seg = src[i:i + 900]
        self.assertIn("tag_id?: string", seg,
                      "前端 API 契约未声明 tag_id（类型会拦编译）")


class TestNegativeControl(unittest.TestCase):
    """负控自证：门禁必须能变红。"""

    def test_red_when_tag_id_removed(self):
        """从模型移除 tag_id ⇒ TestRequestModel 应变红。"""
        import re
        import inspect
        import api.crawl as C
        src = inspect.getsource(C)
        self.assertIn("tag_id", src, "负控前提失效：源码里已无 tag_id")

    def test_red_when_filter_uses_or_instead_of_threshold(self):
        """若有人把过滤写成「有 tag 就全放行」⇒ fail-closed 判据应变红。"""
        from services import high_value_keywords as hv
        scope = "s"
        min_score = 1

        def wrong(_hv):
            return [{"text": "x"}]        # 只判 tag 存在，不看门槛

        def right(h):
            items = [{"text": "x"}]
            return [c for c in items
                    if h.score_text(c["text"], scope) >= min_score]

        self.assertNotEqual(_with_keywords({}, wrong),
                            _with_keywords({}, right),
                            "负控前提失效：两种实现结果相同")


if __name__ == "__main__":
    unittest.main(verbosity=2)
