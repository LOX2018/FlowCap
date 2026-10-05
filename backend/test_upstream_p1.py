# -*- coding: utf-8 -*-
"""P1 接入项单测：撤回字段(f11/f12) / 分享卡解析 / 一起看视频(9000)。

不联网、不读库：全部走纯解析函数与真实形态字节。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_dm.im_protobuf import _parse_message_flags  # noqa: E402
from auto_dm.conversation_capture import (  # noqa: E402
    _share_card_text, _extract_media_text,
)


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            break
    return bytes(out)


def tag(f, wt):
    return varint((f << 3) | wt)


def V(f, n):
    return tag(f, 0) + varint(n)


def L(f, d):
    if isinstance(d, str):
        d = d.encode()
    return tag(f, 2) + varint(len(d)) + d


class TestRecallFlags(unittest.TestCase):
    def test_recalled_true(self):
        rec, vis = _parse_message_flags(V(3, 999) + V(11, 1) + V(12, 1))
        self.assertEqual(rec, 1)
        self.assertEqual(vis, 1)

    def test_recalled_false(self):
        rec, vis = _parse_message_flags(V(3, 999) + V(11, 0) + V(12, 0))
        self.assertEqual(rec, 0)
        self.assertEqual(vis, 0)

    def test_missing_fields(self):
        rec, vis = _parse_message_flags(V(3, 999))
        self.assertIsNone(rec)
        self.assertIsNone(vis)

    def test_empty_and_no_raise(self):
        self.assertEqual(_parse_message_flags(b""), (None, None))
        self.assertEqual(_parse_message_flags(b"\xff\xff\xff"), (None, None))

    def test_len_wiretype_ignored(self):
        """f11 若出现在 len 线型上（异常形态）应被忽略，不得误报撤回。"""
        rec, _ = _parse_message_flags(L(11, b"\x01"))
        self.assertIsNone(rec)


class TestShareCard(unittest.TestCase):
    def test_itemid_returns_none_contract_preserved(self):
        """含 itemId 必须返回 None —— 交给既有 [分享视频] 分支，契约不得变。"""
        self.assertIsNone(_share_card_text({"itemId": "123"}))
        self.assertEqual(
            _extract_media_text({"itemId": "123"}), "[分享视频] 视频ID 123")

    def test_product_card(self):
        got = _share_card_text({"aweType": 11029, "content_title": "硅胶保护套"})
        self.assertEqual(got, "[分享商品] 硅胶保护套")

    def test_article_card(self):
        self.assertEqual(
            _share_card_text({"awemeType": 163, "aweme_title": "如何维权"}),
            "[分享文章] 如何维权")

    def test_photo_card_and_live_photo(self):
        # 无标题时产出裸标签（前端因 shareM 要求 sBody 非空 → 退化为普通文本渲染，
        # 属合理降级：至少不丢消息，也不会造空卡片）
        self.assertEqual(_share_card_text({"awemeType": 68}), "[分享图文]")
        self.assertEqual(
            _share_card_text({"awemeType": 68, "is_live_photo": 1}), "[分享动图]")
        # 带标题 → 完整卡片
        self.assertEqual(
            _share_card_text({"awemeType": 68, "aweme_title": "三张图"}),
            "[分享图文] 三张图")

    def test_comment_card(self):
        self.assertEqual(
            _share_card_text({"comment": "这视频讲得好", "content_title": "工伤认定"}),
            "[分享评论] 工伤认定")

    def test_push_detail_type(self):
        self.assertEqual(
            _share_card_text({"push_detail": "[分享视频] 我的作品", "content_title": "我的作品"}),
            "[分享视频] 我的作品")

    def test_link_when_no_title(self):
        self.assertEqual(_share_card_text({"aweType": 800}), "[分享链接]")

    def test_profile_card(self):
        """2026-09-17 契约订正：13600 **不是**名片，是**合并转发**。

        订正依据（上游源码实测）：`getForwardInfo()` 以 `aweType==='13600'` 判定合并转发；
        `getProfileCard()` 判据是 `name && (secUID || sec_uid || source==='others_homepage')`，
        与 13600 无关。原断言把 13600 当名片，固化了错误契约。
        """
        self.assertEqual(
            _share_card_text({"aweType": 13600, "content_title": "张三"}),
            "[分享聊天记录] 张三")
        # 名片改用上游同款判据（不再依赖 aweType）
        self.assertEqual(
            _share_card_text({"name": "张三", "sec_uid": "MS4wLjABxyz"}),
            "[分享名片] 张三")

    def test_unknown_returns_none(self):
        self.assertIsNone(_share_card_text({}))
        self.assertIsNone(_share_card_text({"aweType": 700, "text": "普通文本"}))
        self.assertIsNone(_share_card_text(None))

    def test_dynamic_layout_title(self):
        got = _share_card_text({
            "aweType": 800,
            "im_dynamic_patch": {"top_bottom_top": {"content": "动态标题"}},
        })
        self.assertEqual(got, "[分享视频] 动态标题")


class TestWatchTogether(unittest.TestCase):
    def test_9000_card(self):
        got = _extract_media_text({
            "aweType": 9000, "title": "一起看电影",
            "sub_title": "等你加入",
            "cover_url": {"url_list": ["https://cover/x.jpg"]},
        })
        self.assertTrue(got.startswith("[一起看视频] 一起看电影"))
        self.assertIn("等你加入", got)
        self.assertIn("[封面] https://cover/x.jpg", got)

    def test_9000_defaults(self):
        got = _extract_media_text({"aweType": 9000})
        self.assertEqual(got, "[一起看视频] 一起看视频")

    def test_9000_string_type(self):
        got = _extract_media_text({"aweType": "9000", "title": "T"})
        self.assertEqual(got, "[一起看视频] T")

    def test_9000_not_matched_by_other(self):
        """aweType=9001 不得误命中 9000 分支。"""
        got = _extract_media_text({"aweType": 9001, "title": "X"})
        self.assertNotIn("一起看视频", got or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
