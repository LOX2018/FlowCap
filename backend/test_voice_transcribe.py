# -*- coding: utf-8 -*-
"""语音转写模块单测（2026-09-17 新增功能，对照上游 douyin-chat-export）。

覆盖：
  · extract_voice_fields —— 从多种 content 形态取 uri/skey
  · is_voice_content —— 语音/图片/视频的判别（防把图片误判为语音）
  · transcribe_batch —— 分批请求、成功判据、msg_id→文本 映射
  · fetch_self_uuid —— uuid 探测
  · _ok_status / _iter_texts / _pick_text —— 响应解析容错

不联网、不起浏览器：exec_js 用假函数注入，验证**请求体构造**与**响应解析**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.voice_transcribe import (  # noqa: E402
    BATCH_SIZE, VOICE_MESSAGE_TYPE, AUDIO_RECOGNITION_URL,
    extract_voice_fields, is_voice_content, transcribe_batch, fetch_self_uuid,
    _ok_status, _iter_texts, _pick_text, load_json,
)


VOICE_CONTENT = {
    "aweType": 0,
    "duration": 3000,
    "resource_url": {
        "uri": "v0d00fg10000abc",
        "skey": "a" * 32,
        "url_list": ["https://v.douyin.com/xxx"],
    },
}
IMAGE_CONTENT = {
    "aweType": 700,
    "resource_url": {
        "skey": "b" * 64,
        "origin_url_list": ["https://p26-sign.douyinpic.com/xxx"],
    },
}
VIDEO_CONTENT = {
    "aweType": 2702,
    "video": {"vid": "v0d00fg", "skey": "c" * 32},
    "resource_url": {"url_list": ["https://x"]},
}


class TestVoiceFields(unittest.TestCase):
    def test_extract_basic(self):
        f = extract_voice_fields(VOICE_CONTENT)
        self.assertEqual(f["uri"], "v0d00fg10000abc")
        self.assertEqual(f["skey"], "a" * 32)
        self.assertEqual(f["duration"], 3000)

    def test_extract_fallback_to_url_list(self):
        c = {"resource_url": {"url_list": ["https://cdn/x"]}, "duration": 1}
        self.assertEqual(extract_voice_fields(c)["uri"], "https://cdn/x")

    def test_extract_tkey_variant(self):
        # 历史形态：顶层 tkey（上游 _voice_resource 注释提到）
        c = {"tkey": "tk-123", "duration": 100}
        f = extract_voice_fields(c)
        self.assertEqual(f["uri"], "tk-123")

    def test_extract_empty(self):
        self.assertEqual(extract_voice_fields({})["uri"], "")
        self.assertEqual(extract_voice_fields(None)["uri"], "")

    def test_is_voice_true(self):
        self.assertTrue(is_voice_content(VOICE_CONTENT))

    def test_is_voice_false_for_image(self):
        # 图片：有 skey 但无 duration/tkey，且 aweType=700 非语音
        self.assertFalse(is_voice_content(IMAGE_CONTENT))

    def test_is_voice_false_for_video(self):
        self.assertFalse(is_voice_content(VIDEO_CONTENT))

    def test_is_voice_awe2702_without_marker_is_not_voice(self):
        # aweType 2702/2703/2704 是图片族；无显式语音标记 → 不是语音
        c = {"aweType": 2703, "duration": 100,
             "resource_url": {"url_list": ["https://x"]}}
        self.assertFalse(is_voice_content(c))

    def test_is_voice_awe2702_with_marker_is_voice(self):
        c = {"aweType": 2703, "duration": 100, "tkey": "tk",
             "resource_url": {"url_list": ["https://x"]}}
        self.assertTrue(is_voice_content(c))


class TestResponseParse(unittest.TestCase):
    def test_ok_status_variants(self):
        self.assertTrue(_ok_status({"status_code": 0}))
        self.assertTrue(_ok_status({"status_code": 200}))
        self.assertTrue(_ok_status({"code": 0}))
        self.assertTrue(_ok_status({"error_code": 0}))
        self.assertFalse(_ok_status({"status_code": 5}))
        self.assertFalse(_ok_status({}))
        self.assertFalse(_ok_status(None))

    def test_iter_texts_shapes(self):
        self.assertEqual(len(_iter_texts({"data": [{"text": "a"}]})), 1)
        self.assertEqual(len(_iter_texts({"data": {"resp_list": [1, 2]}})), 2)
        self.assertEqual(_iter_texts({}), [])

    def test_pick_text_variants(self):
        self.assertEqual(_pick_text({"text": " 你好 "}), "你好")
        self.assertEqual(_pick_text({"transcription": "hi"}), "hi")
        self.assertEqual(_pick_text({}), "")

    def test_load_json(self):
        self.assertEqual(load_json('{"a":1}'), {"a": 1})
        self.assertEqual(load_json({"a": 1}), {"a": 1})
        self.assertEqual(load_json("junk"), {})
        self.assertEqual(load_json(None), {})


class TestTranscribeBatch(unittest.TestCase):
    def _items(self, n):
        return [{"msg_id": f"m{i}", "message_id": f"m{i}",
                 "uri": f"uri{i}", "skey": "k", "sec_uid": "MS4wLjABAAAAx",
                 "conv_short_id": "cs"} for i in range(n)]

    def test_success_maps_msg_id(self):
        seen = {}

        def fake_exec(js, arg):
            seen["js"] = js
            seen["arg"] = arg
            return {"status": 200, "body": {"status_code": 0,
                                            "data": [{"text": "第一句"}, {"text": "第二句"}]}}

        res = transcribe_batch(fake_exec, self._items(2), "uuid-1")
        self.assertTrue(res["ok"])
        self.assertEqual(res["mapped"], {"m0": "第一句", "m1": "第二句"})
        # 请求体契约：端点 + req_list 字段 + message_type=7
        url, req_list = seen["arg"]
        self.assertEqual(url, AUDIO_RECOGNITION_URL)
        self.assertEqual(len(req_list), 2)
        self.assertEqual(req_list[0]["message_type"], VOICE_MESSAGE_TYPE)
        self.assertEqual(req_list[0]["uuid"], "uuid-1")
        self.assertEqual(req_list[0]["uri"], "uri0")

    def test_batch_size_cap(self):
        seen = {}

        def fake_exec(js, arg):
            seen["arg"] = arg
            return {"status": 200, "body": {"status_code": 0, "data": []}}

        transcribe_batch(fake_exec, self._items(BATCH_SIZE + 5), "u")
        self.assertEqual(len(seen["arg"][1]), BATCH_SIZE)

    def test_no_uuid_does_not_call(self):
        called = {"n": 0}

        def fake_exec(js, arg):
            called["n"] += 1
            return {}

        res = transcribe_batch(fake_exec, self._items(1), "")
        self.assertEqual(called["n"], 0)          # 不浪费请求
        self.assertEqual(res["reason"], "no-uuid")
        self.assertFalse(res["ok"])

    def test_status_code_5_is_failure(self):
        def fake_exec(js, arg):
            return {"status": 200, "body": {"status_code": 5}}

        res = transcribe_batch(fake_exec, self._items(1), "u")
        self.assertFalse(res["ok"])
        self.assertTrue(res["reason"].startswith("status:"))

    def test_exec_exception_handled(self):
        def fake_exec(js, arg):
            raise RuntimeError("boom")

        res = transcribe_batch(fake_exec, self._items(1), "u")
        self.assertFalse(res["ok"])
        self.assertTrue(res["reason"].startswith("exec:"))

    def test_no_items(self):
        res = transcribe_batch(lambda js, arg: {}, [], "u")
        self.assertEqual(res["reason"], "no-items")

    def test_skey_empty_allowed(self):
        """skey 历史消息常缺失，允许空串（上游注释明确 endpoint 接受）。"""
        seen = {}

        def fake_exec(js, arg):
            seen["arg"] = arg
            return {"status": 200, "body": {"status_code": 0, "data": [{"text": "t"}]}}

        items = self._items(1)
        items[0]["skey"] = ""
        res = transcribe_batch(fake_exec, items, "u")
        self.assertEqual(seen["arg"][1][0]["skey"], "")
        self.assertTrue(res["ok"])

    def test_fetch_self_uuid(self):
        self.assertEqual(fetch_self_uuid(lambda js, arg: "dev-9"), "dev-9")
        self.assertEqual(fetch_self_uuid(lambda js, arg: None), "")
        self.assertEqual(fetch_self_uuid(lambda js, arg: 1 / 0), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
