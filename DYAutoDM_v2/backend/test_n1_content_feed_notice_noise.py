# -*- coding: utf-8 -*-
"""门禁：内容页「推荐流噪音/无刷新」与「站内通知噪音」的防复发检查（2026-09-30）。

## 为什么需要（用户报障，根因已实测）

**推荐流**（`frontend` 报「空视频噪音，且只有 6 个，没有刷新按钮」）：
  1. 后端 `get_feed` 把上游的换一批旋钮 `refresh_index` **硬编码为 "2"**
     ⇒ 每次都是同一批，且上游单次只返少量条目（实测 2~6 条，与 count 无关）。
  2. 上游 `aweme_list` **混入非作品卡**：实测 `aweme_type=101` 的直播推荐卡
     （顶层带 `cell_room`、**无 `video` 子树**）⇒ `_pick_aweme` 只读 `video.cover`
     ⇒ 前端渲染「无封面 / 0 赞 / 0 评 / 无描述」占位噪音。

**站内通知**（用户报「全是噪音」）：
  `notice_list_v2` 的通知**无顶层 `content`**，文案分散在 type 专属子对象：
  `33`→`follow.from_user.nickname`、`31`→`comment.comment.text`、
  `41`→`digg.aweme.desc`。旧实现**只读 digg** ⇒ 24/47 新粉丝 + 10/47 评论
  全部显示「（无内容）」占位噪音（实测）。

## 判据（N1~N7）

  N1  `_is_playable`：有 play_addr / bit_rate / images ⇒ 可播；直播卡/空壳 ⇒ 不可播
  N2  `get_feed` 路由**透传** refresh_index（不再硬编码 "2"）
  N3  `get_feed` 响应**剔除**不可播条目并**如实上报** `filtered`（禁假成功）
  N4  `_notice_actor_text`：type=33/31/41 各自能从正确子对象取到文案
  N5  `_notice_actor_text`：上游缺文本时给**方向中立**兜底（不编「关注了你」等）
  N6  `_notice_type_label`：31/33/41 → 评论/新粉丝/点赞
  N7  **负控**：注入 101 直播卡 ⇒ N1 必须判不可播；注入缺 content 的 41 ⇒ N4 兜底

## 安全性
纯函数 + 打桩，**零真实抖音请求**；路由用例把 `_api()`/`_auth_for` 全部替换为桩。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
_ROOT = tempfile.mkdtemp(prefix="n1_content_noise_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

from api import platform as P  # noqa: E402


def _live_card(aweme_id="7691236409843256090"):
    """上游实测形态：aweme_type=101 直播推荐卡（有 cell_room、无 video）。"""
    return {
        "aweme_id": aweme_id,
        "aweme_type": 101,
        "author": None,
        "author_user_id": 4128046328843305,
        "cell_room": {"rawdata": "{\"id\":1}"},
    }


def _video(aweme_id="1"):
    """正常视频（有 play_addr + cover）。"""
    return {
        "aweme_id": aweme_id,
        "aweme_type": 0,
        "desc": "正常作品",
        "author": {"uid": "9", "nickname": "作者"},
        "statistics": {"digg_count": 1, "comment_count": 2},
        "video": {
            "duration": 1000,
            "cover": {"url_list": ["https://x/cover.jpg"]},
            "play_addr": {"url_list": ["https://x/play.mp4"]},
            "bit_rate": [{"gear_name": "hd"}],
        },
    }


class TestIsPlayable(unittest.TestCase):
    """N1 + N7a：可播判据。"""

    def test_n1_video_playable(self):
        self.assertTrue(P._is_playable(_video()))

    def test_n1_image_playable(self):
        w = _video(); w.pop("video"); w["images"] = [{"url_list": ["https://x/a.jpg"]}]
        self.assertTrue(P._is_playable(w))

    def test_n7a_live_card_not_playable(self):
        # 负控：直播卡必须判为不可播（这就是前端「空视频」的本体）
        self.assertFalse(P._is_playable(_live_card()))

    def test_n1_empty_shell_not_playable(self):
        self.assertFalse(P._is_playable({"aweme_id": "x", "video": {}}))
        self.assertFalse(P._is_playable({}))


class _FakeAPI:
    """桩：模拟基座 DouyinAPI.get_feed，记录收到的 refresh_index。"""

    def __init__(self, aweme_list):
        self._al = aweme_list
        self.seen_ri = []
        self.seen_count = []

    def get_feed(self, auth, count, refresh_index):
        self.seen_count.append(count)
        self.seen_ri.append(refresh_index)
        return {"status_code": 0, "has_more": 1, "aweme_list": self._al}


class TestFeedRoute(unittest.TestCase):
    """N2 + N3 + N7b：路由透传 refresh_index + 过滤并如实上报。"""

    def _run(self, aweme_list, refresh_index, count=20):
        fake = _FakeAPI(aweme_list)
        orig_api, orig_auth = P._api, P._auth_for
        P._api = lambda: fake
        P._auth_for = lambda acct: object()
        # 2026-10-02 修补（实测 503 假失败）：`get_feed` 已改走
        # `services.auth_policy.get_auth_for(...)`（v0.46.2 实测推荐流
        # 不可匿名 ⇒ fail-closed），而本用例只桩了 `P._auth_for` ——
        # 桩挂在一个**已不再被调用**的名字上 ⇒ auth 为 None ⇒ 撞 503，
        # 报错与被测的「refresh_index 是否透传 / 过滤是否如实上报」无关。
        # 补桩真实取值路径（局部导入在调用时从模块取属性，patch 模块属性即生效）。
        from services import auth_policy
        orig_get_auth_for = auth_policy.get_auth_for
        auth_policy.get_auth_for = lambda endpoint, account: object()
        try:
            return asyncio.run(P.get_feed(P.FeedReq(
                account="a", count=count, refresh_index=refresh_index))), fake
        finally:
            P._api, P._auth_for = orig_api, orig_auth
            auth_policy.get_auth_for = orig_get_auth_for

    def test_n2_refresh_index_passthrough(self):
        # 负控对应：原实现硬编码 "2" ⇒ 本断言会红
        for ri in (1, 7, 13):
            resp, fake = self._run([_video()], ri)
            self.assertEqual(fake.seen_ri, [str(ri)])

    def test_n3_filters_unplayable_and_reports(self):
        resp, _ = self._run([_video("v1"), _live_card("c1"), _video("v2")], 3)
        ids = [it["aweme_id"] for it in resp["items"]]
        self.assertEqual(ids, ["v1", "v2"])
        self.assertEqual(resp["filtered"], 1)
        self.assertEqual(resp["refresh_index"], 3)

    def test_n3_all_noise_falls_back_to_raw(self):
        # 整批都是噪音时**不伪装成空**（保留原批，前端可「换一批」）
        resp, _ = self._run([_live_card("c1")], 4)
        self.assertEqual(len(resp["items"]), 1)
        self.assertEqual(resp["filtered"], 1)


class TestNoticeExtract(unittest.TestCase):
    """N4 + N5 + N6 + N7c：通知文案提取。"""

    def _n33(self, nick):
        return {"type": 33, "create_time": 1, "has_read": False,
                "follow": {"from_user": {"uid": "1", "nickname": nick,
                                         "follower_status": 1}}}

    def _n31(self, nick, text):
        return {"type": 31, "create_time": 1, "has_read": False,
                "comment": {"comment": {"text": text, "user": {"nickname": nick}}}}

    def _n41(self, nick, desc):
        return {"type": 41, "create_time": 1, "has_read": False,
                "digg": {"aweme": {"desc": desc, "author": {"nickname": nick}}}}

    def test_n4_follow(self):
        who, text = P._notice_actor_text(self._n33("张三"))
        self.assertEqual(who, "张三")
        self.assertIn("关注了你", text)

    def test_n4_comment(self):
        who, text = P._notice_actor_text(self._n31("李四", "说得好"))
        self.assertEqual(who, "李四")
        self.assertIn("说得好", text)

    def test_n4_digg(self):
        who, text = P._notice_actor_text(self._n41("王五", "某作品"))
        self.assertEqual(who, "王五")
        self.assertIn("某作品", text)

    def test_n7c_missing_digg_desc_neutral_fallback(self):
        # 负控：上游 desc=null 时必须给**方向中立**兜底，且不得为空
        who, text = P._notice_actor_text(self._n41("", ""))
        self.assertTrue(text.strip())
        self.assertNotIn("关注了你", text)
        self.assertNotIn("：", text)

    def test_n5_missing_comment_text_neutral_fallback(self):
        who, text = P._notice_actor_text(self._n31("", ""))
        self.assertTrue(text.strip())
        self.assertNotIn("关注了你", text)

    def test_n6_type_labels(self):
        self.assertEqual(P._notice_type_label(31), "评论")
        self.assertEqual(P._notice_type_label("33"), "新粉丝")
        self.assertEqual(P._notice_type_label(41), "点赞")
        # 未知 type 原样回显数字，不猜语义
        self.assertEqual(P._notice_type_label(99), "99")


if __name__ == "__main__":
    unittest.main(verbosity=2)
