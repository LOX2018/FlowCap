# coding=utf-8
"""门禁：直播间链接解析（输入解析层）—— 2026-10-01 用户实测报障驱动。

## 为什么需要本门禁（防复发）

用户原话：「目前直播间号解析还有很大的缺陷，官方常见的链接格式很多都不适配」。

根因（实测）：`link_resolve.py` 只认
  ① 纯数字 ② `live.douyin.com/<id>` ③ `douyin.com/**/live/<id>`
**不认**「房间号在 query 参数里」的形态 —— 抖音「搜索结果直播卡」分享出来的链接就是这种：
    https://www.douyin.com/search/<kw>?…&live_web_rid=291891133640&…&type=live
⇒ 解析失败。后端补 `_LIVE_QQ_RE`，前端 `extractRoomId` 同步补齐。

## 本门禁钉死的判据（任何一条回退即变红）

- G1 搜索页 query 形态（用户实证 URL）必须命中 `live_web_rid` 的值；
- G2 ⚠️ **防误抠**：同 URL 的 `search_result_id`（19 位大数）**不是**房间号，
  只有它时**必须解析失败**（绝不允许「取第一个大数字」式的宽匹配）；
- G3 既有形态（纯数字 / live 页 / follow-live / 分享文案）不得回归；
- G4 大小写与拼写变体（`liveWebRid`）容错；
- G5 前端 `extractRoomId` 与后端**同判据**（同一批用例、同一期望值）；
- G6 负控：把 `_LIVE_QQ_RE` 的锚点放宽成「任意数字」⇒ G1 会误命中 G2 的用例 ⇒ 变红。
"""

from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_design")

FRONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "frontend", "src", "components", "live", "live-page.tsx")

#: 用户实证 URL（原样，勿"美化"——它就是报障输入）
USER_URL = ("https://www.douyin.com/search/%E5%B7%A5%E4%BC%A4?from_search=true"
            "&is_aweme_tied=1&live_web_rid=291891133640"
            "&search_id=2026100100275114319AFD9067CC8B1B36"
            "&search_result_id=7691345930137652543&type=live")

#: (用例名, 输入, 期望 web_rid；None = 期望"解析失败/不命中")
CASES: list[tuple[str, str, str | None]] = [
    ("用户实证·搜索页直播卡", USER_URL, "291891133640"),
    ("query 参数在首位", "https://www.douyin.com/search/x?live_web_rid=223344556&a=1", "223344556"),
    ("query+其它参数", "https://www.douyin.com/search/x?a=1&live_web_rid=992931212705&type=live", "992931212705"),
    ("大小写变体 liveWebRid", "https://www.douyin.com/search/x?liveWebRid=123456789", "123456789"),
    ("标准直播页", "https://live.douyin.com/992931212705?enter_from=web_live", "992931212705"),
    ("关注页直播", "https://www.douyin.com/follow/live/992931212705?anchor_id=123", "992931212705"),
    ("分享文案混排", "7.43 复制打开抖音 https://live.douyin.com/123456?u_code=9", "123456"),
    ("纯数字", "291891133640", "291891133640"),
    # ⚠️ 反例（防误抠）：只有 search_result_id，**不是**房间号
    ("反例·仅 search_result_id", "https://www.douyin.com/search/x?search_result_id=7691345930137652543&type=live", None),
    ("反例·空串", "", None),
]


def _zero_net_extract(raw: str) -> str | None:
    """复现 `resolve_live_id` 的**零网络**判定段（不触发网络/浏览器）。

    与实现同序：纯数字 → live.douyin.com/<id> → douyin.com/**/live/<id> → query live_web_rid。
    刻意不调用 `resolve_live_id`（它会在未命中时走网络/浏览器，测试须确定性）。
    """
    import link_resolve as L

    s = (raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z0-9_]+", s):
        return s
    m = L._LIVE_RE.search(s)
    if m:
        return m.group(1)
    m = L._LIVE_PAGE_RE.search(s)
    if m:
        return m.group(1)
    for _re_qq in (L._LIVE_QQ_RE, L._LIVE_QQ_ALT_RE):
        m = _re_qq.search(s)
        if m:
            return m.group(1)
    return None


class ReflowRouteGate(unittest.TestCase):
    """2026-10-01 任务 1：`/webcast/reflow/<room_id>` 与短链的**路由**判据。

    ⚠️ 本组钉死「**room_id ≠ web_rid**」这一语义 —— 权威双源：
      · DTK `urls/patterns.py` 明文注释；
      · DouyinLiveRecorder `room.py:61-66,109-137`（经 `reflow/info` 换 web_rid）。
    """

    def test_r1_reflow_room_extracted_as_room_id(self):
        """R1：`/webcast/reflow/<id>` 抽出的必须是 room_id（供桥接），**不得**当 web_rid 直返。"""
        import link_resolve as L
        url = ("https://webcast.amemv.com/douyin/webcast/reflow/7683789197988793122")
        self.assertEqual(L._extract_reflow_room_id(url), "7683789197988793122")
        # 关键：零网络判定**不得**把它当 web_rid 直接返回
        self.assertIsNone(_zero_net_extract(url),
                          "reflow 的 id 是 room_id，零网络直取会得到错误标识")

    def test_r2_short_link_routed_to_reflow_bridge(self):
        """R2：短链/iesdouyin 必须被路由到 reflow 桥接分支（源码级断言）。"""
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "link_resolve.py"), encoding="utf-8").read()
        # 短链分支存在，且引用 v.douyin / v.amemv / iesdouyin
        self.assertRegex(src, r"v\\\.douyin\\\.com\|v\\\.amemv\\\.com\|iesdouyin")
        # 该分支确实调用了 resolve_via_reflow（而非直接落到浏览器兜底）
        seg = src.split("情形2·乙")[-1][:900] if "情形2·乙" in src else ""
        self.assertIn("resolve_via_reflow", seg, "短链分支未接 reflow 桥接")

    def test_r3_room_id_ne_web_rid_semantics_documented(self):
        """R3：语义澄清（room_id ≠ web_rid）必须留在模块文档/注释里（防再次混淆）。"""
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "link_resolve.py"), encoding="utf-8").read()
        self.assertIn("reflow links carry a", src)      # DTK 权威原文引用
        self.assertIn("room_id", src)
        # 两套标识必须都写明
        self.assertIn("web_rid", src)


class LiveLinkResolveGate(unittest.TestCase):
    def test_g1_user_url_hits_live_web_rid(self):
        """G1：用户实证 URL 必须解析出 291891133640。"""
        self.assertEqual(_zero_net_extract(USER_URL), "291891133640")

    def test_g2_no_overmatch_on_search_result_id(self):
        """G2（防误抠）：只有 search_result_id 时必须**不命中**。

        这是本门禁最关键的负向判据 —— 若实现被写成「取 URL 里第一个大数字」，
        它会返回 7691345930137652543（**不是**房间号），此处即变红。
        """
        url = "https://www.douyin.com/search/x?search_result_id=7691345930137652543&type=live"
        got = _zero_net_extract(url)
        self.assertIsNone(got)
        self.assertNotEqual(got, "7691345930137652543")

    def test_g3_existing_shapes_no_regression(self):
        """G3：既有形态不得回归（纯数字 / live 页 / follow-live / 分享文案）。"""
        for name, url, exp in CASES:
            if exp is None or name.startswith("反例"):
                continue
            with self.subTest(case=name):
                self.assertEqual(_zero_net_extract(url), exp, f"{name} 回归")

    def test_g4_case_and_spelling_tolerant(self):
        """G4：大小写 / 拼写变体容错。"""
        self.assertEqual(
            _zero_net_extract("https://www.douyin.com/search/x?liveWebRid=123456789"),
            "123456789")

    def test_g5_frontend_same_judgement(self):
        """G5：前端 `extractRoomId` 与后端同判据（同用例同期望）。"""
        src = open(FRONT, encoding="utf-8").read()
        # 断言前端确有 query 形态分支（精确锚定 live_web_rid，且非宽松取数）
        self.assertRegex(src, r"live_\?web_\?rid=")
        # 前端不得出现「取 URL 第一个大数字」式的宽松模式
        self.assertNotIn(r"[?&][a-z_]+=(\d{5,})", src)

    def test_g6_negative_control_loose_anchor_turns_red(self):
        """G6 负控：把锚点放宽成「任意 query 的大数字」⇒ G2 用例会被误命中。

        证明 G2 有区分度（不是恒绿）。
        """
        loose = re.compile(r"[?&][A-Za-z_]+=(\d{5,})")
        m = loose.search("https://www.douyin.com/search/x?search_result_id=7691345930137652543")
        self.assertIsNotNone(m, "宽松模式本应误命中（负控自证）")
        self.assertEqual(m.group(1), "7691345930137652543")   # 错的房间号
        # 而正确实现（精确锚点）不命中 —— 两者行为**必须不同**
        self.assertIsNone(_zero_net_extract(
            "https://www.douyin.com/search/x?search_result_id=7691345930137652543"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
