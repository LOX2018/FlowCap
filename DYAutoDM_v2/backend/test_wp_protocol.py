# coding=utf-8
"""WP 协议层监听器机械门禁（含负控）。

覆盖：
  · URL 过滤判据与 legacy JS hook **逐项对齐**（换传输层不换过滤口径）
  · 事件契约 shape（kind/url/body/ts）—— 消费方 wp_recv 依赖
  · 读后清空语义 / 上限防爆 / bytes→B64 / body 截断
  · 负控：非 IM URL 绝不入缓冲（防「什么都收」）
  · 异常吞掉但不静默丢数据（回调内异常不得外抛打断事件循环）
"""
import asyncio
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from daemon.wp_protocol import (  # noqa: E402
    WpProtocolListener, wp_im_url, wp_ws_url,
    _MAX_EVENTS, _MAX_BODY,
)


class TestUrlFilters(unittest.TestCase):
    """过滤判据必须与 CAP_WP_MESSAGE_HOOK_JS 的 IMAPI_RE 对齐。"""

    def test_im_path_features_hit(self):
        # 与原 JS 正则列表逐项对齐
        for path in ("get_message_by_init", "get_by_conversation", "get_user_message",
                     "get_info_list", "mark_read", "message/send",
                     "conversation/create", "conversation/info"):
            url = f"https://imapi.douyin.com/v1/{path}/?x=1"
            with self.subTest(path=path):
                self.assertTrue(wp_im_url(url), f"{path} 应命中")

    def test_firefox_v2_shape(self):
        # Camoufox(Firefox) 下 /v2/ 形态同样命中
        self.assertTrue(wp_im_url("https://imapi.douyin.com/v2/conversation/info"))

    def test_keyword_fallback_without_version_segment(self):
        # 无 /v1|v2/ 前缀时，关键字兜底判据应命中
        self.assertTrue(wp_im_url("https://imapi.douyin.com/get_message_by_init"))

    def test_negative_control_non_im_url(self):
        """负控：非 IM 接口绝不命中（防「什么都收」）。"""
        for url in (
            "https://www.douyin.com/aweme/v1/web/aweme/detail/",
            "https://p3-sign.douyinpic.com/tos-cn-o-00061/abc",
            "https://www.douyin.com/v1/user/profile",
            "",
            None,
        ):
            with self.subTest(url=url):
                self.assertFalse(wp_im_url(url))

    def test_ws_url_heuristic(self):
        # 对齐原 JS `/im|message|conversation/i`
        self.assertTrue(wp_ws_url("wss://imapi.douyin.com/ws"))
        self.assertTrue(wp_ws_url("wss://webcast.douyin.com/webcast/im/ws"))
        self.assertTrue(wp_ws_url("wss://x/conversation"))
        self.assertFalse(wp_ws_url("wss://cdn.example.com/asset"))
        self.assertFalse(wp_ws_url(None))


class _Resp:
    """最小响应替身：只提供 _consume_response 需要的 url/text/body。"""

    def __init__(self, url, text=None, raw=None):
        self.url = url
        self._text = text
        self._raw = raw

    async def text(self):
        if self._text is None:
            raise RuntimeError("二进制响应")
        return self._text

    async def body(self):
        return self._raw


class TestEventContract(unittest.TestCase):
    def setUp(self):
        self.lis = WpProtocolListener()

    def test_push_contract_shape(self):
        self.lis._push("http", "https://imapi.douyin.com/v1/get_message_by_init",
                       '{"a":1}')
        evs = self.lis.drain()
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        # 消费方 wp_recv.process_events 只读这三个键（ts 用于排序/诊断）
        for k in ("kind", "url", "body", "ts"):
            self.assertIn(k, ev, f"契约缺字段 {k}")
        self.assertEqual(ev["kind"], "http")
        self.assertEqual(ev["body"], '{"a":1}')

    def test_drain_reads_and_clears(self):
        self.lis._push("ws", "wss://imapi.douyin.com/ws", "frame-1")
        self.assertEqual(len(self.lis.drain()), 1)
        # 读后清空（与原 JS hook 取值语义一致）——第二次必空
        self.assertEqual(self.lis.drain(), [])

    def test_bytes_to_b64(self):
        """二进制帧/响应 → 'B64:' + base64（wp_recv 按该前缀识别）。"""
        raw = b"\x08\x01\x12\x03abc"
        self.lis._push("ws", "wss://imapi.douyin.com/ws", raw)
        body = self.lis.drain()[0]["body"]
        self.assertTrue(body.startswith("B64:"))
        self.assertEqual(base64.b64decode(body[4:]), raw)

    def test_body_truncation(self):
        self.lis._push("http", "https://imapi.douyin.com/v1/get_message_by_init",
                       "x" * (_MAX_BODY + 5000))
        self.assertEqual(len(self.lis.drain()[0]["body"]), _MAX_BODY)

    def test_buffer_cap_fifo(self):
        for i in range(_MAX_EVENTS + 10):
            self.lis._push("ws", "wss://imapi.douyin.com/ws", f"f{i}")
        evs = self.lis.drain()
        self.assertEqual(len(evs), _MAX_EVENTS)
        # FIFO：最早的被丢弃
        self.assertEqual(evs[0]["body"], "f10")
        self.assertEqual(self.lis._dropped, 10)

    def test_push_none_ignored(self):
        self.lis._push("ws", "wss://imapi.douyin.com/ws", None)
        self.assertEqual(self.lis.drain(), [])


class TestCallbacksRobust(unittest.TestCase):
    """回调内异常不得外抛（否则会打断 patchright 事件循环）。"""

    def setUp(self):
        self.lis = WpProtocolListener()

    def test_on_response_ignores_non_im(self):
        class R:
            url = "https://cdn.example.com/a.js"
        self.lis._on_response(R())            # 不得抛
        self.assertEqual(self.lis.stats["http_matched"], 0)
        self.assertEqual(self.lis.buffered, 0)

    def test_on_response_matches_im_and_schedules(self):
        class R:
            url = "https://imapi.douyin.com/v1/get_message_by_init"
        async def _run():
            self.lis._on_response(R())
            await asyncio.sleep(0)           # 让 create_task 跑起来
        # 该回调用 asyncio.create_task ⇒ 需在运行的 loop 内调用
        asyncio.run(_run())
        self.assertEqual(self.lis.stats["http_matched"], 1)

    def test_on_frame_bad_payload_no_raise(self):
        class Weird:
            def __str__(self):
                raise ValueError("boom")
        self.lis._on_frame("wss://imapi.douyin.com/ws", Weird())   # 不得抛
        self.assertEqual(self.lis.stats["ws_frames"], 1)

    def test_on_websocket_filters_url(self):
        class WS:
            url = "wss://cdn.example.com/asset"
            def on(self, *a):  # noqa: A003
                raise AssertionError("非 IM URL 不该挂 framereceived")
        self.lis._on_websocket(WS())          # 不得抛
        self.assertEqual(self.lis.stats["ws_conn"], 0)

    def test_consume_response_binary_fallback(self):
        resp = _Resp("https://imapi.douyin.com/v1/get_message_by_init",
                     text=None, raw=b"\x08\x01")
        asyncio.run(self.lis._consume_response(
            resp, resp.url))
        body = self.lis.drain()[0]["body"]
        self.assertTrue(body.startswith("B64:"))


class TestAttachDetach(unittest.TestCase):
    def test_attach_records_and_is_idempotent(self):
        class Ctx:
            def __init__(self):
                self.calls = []
            def on(self, ev, fn):  # noqa: A003
                self.calls.append(ev)
            def remove_listener(self, ev, fn):
                self.calls.append("rm:" + ev)

        class Page:
            def __init__(self):
                self.calls = []
            def on(self, ev, fn):  # noqa: A003
                self.calls.append(ev)

        ctx, page = Ctx(), Page()
        lis = WpProtocolListener()

        async def _run():
            await lis.attach(ctx, page)
            await lis.attach(ctx, page)      # 幂等：不得重复挂
        asyncio.run(_run())
        self.assertEqual(ctx.calls.count("response"), 1)
        self.assertEqual(ctx.calls.count("page"), 1)
        self.assertEqual(page.calls.count("websocket"), 1)

    def test_attach_requires_context(self):
        lis = WpProtocolListener()
        with self.assertRaises(ValueError):
            asyncio.run(lis.attach(None))


class TestWsTextEncoding(unittest.TestCase):
    """WS 文本帧编码修正（Firefox/juggler latin-1 交付缺陷）。"""

    def test_mojibake_restored(self):
        from daemon.wp_protocol import fix_ws_text
        good = "在吗：中文测试"
        moji = good.encode("utf-8").decode("latin-1")   # 复现实测形态
        self.assertNotEqual(moji, good)
        self.assertEqual(fix_ws_text(moji), good)

    def test_already_correct_not_corrupted(self):
        """🔴 负控：已正确解码的中文，绝不能被二次破坏。"""
        from daemon.wp_protocol import fix_ws_text
        good = "在吗：中文测试"
        self.assertEqual(fix_ws_text(good), good)

    def test_ascii_fast_path(self):
        from daemon.wp_protocol import fix_ws_text
        for s in ('{"messages":[]}', "", "plain english"):
            self.assertEqual(fix_ws_text(s), s)

    def test_invalid_bytes_returned_unchanged(self):
        """非 UTF-8 字节序列 → 原样返回（不制造新乱码）。"""
        from daemon.wp_protocol import fix_ws_text
        weird = "ÿþ\u00ff"
        self.assertEqual(fix_ws_text(weird), weird)

    def test_frame_path_applies_fix(self):
        """端到端：_on_frame 收到的 mojibake 应在缓冲里已是正确中文。"""
        lis = WpProtocolListener()
        good = "你好"
        lis._on_frame("wss://imapi.douyin.com/ws",
                      good.encode("utf-8").decode("latin-1"))
        self.assertEqual(lis.drain()[0]["body"], good)


if __name__ == "__main__":
    unittest.main(verbosity=2)
