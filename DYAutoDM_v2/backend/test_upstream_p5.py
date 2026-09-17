# -*- coding: utf-8 -*-
"""P5 单测：合并转发解析/解密/补抓校验 + 昵称兜底限速 + 13600 契约订正。

验证策略：**真 AES-GCM 加解密**（不是假数据），并逐条验证上游安全约束
（域名白名单、skey 形态、条数/ID 完整性、非零 status_code 拒绝）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.merged_forward as MF     # noqa: E402
import services.nickname_fallback as NF  # noqa: E402
import auto_dm.conversation_capture as CC  # noqa: E402

_KEY = "00112233445566778899aabbccddeeff"          # 32 hex = AES-128
_UPLOAD = "douyin-im-merge-share/v01/part-1"
_URL = "https://p3-aweme-im-merge-share-sign.byteimg.com/obj/x"


def _encrypt(messages: list[dict], skey: str = _KEY, aad=None) -> bytes:
    """构造一段真实的 `nonce(12) + AES-GCM(明文)` 资源。"""
    import os as _os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = _os.urandom(12)
    pt = json.dumps({"Messages": messages}, ensure_ascii=False).encode()
    return nonce + AESGCM(bytes.fromhex(skey)).encrypt(nonce, pt, aad)


def _msg(mid: str, text: str, who: str = "张三") -> dict:
    return {"server_message_id": int(mid), "sender_name": who,
            "content": {"aweType": "700", "text": text}}


def _card(msg_ids: list[str], *, inline=None, upload=None, title="出差报销",
          wait_note="正文未获取") -> dict:
    c = {"aweType": "13600", "title": title,
         "msg_ids": [{"msg_id": int(i)} for i in msg_ids],
         "list_content": [{"nick_name": "张三", "text": wait_note}]}
    if inline is not None:
        c["inline_content"] = inline
    if upload is not None:
        c["upload_key_list"] = upload
        c["skey"] = _KEY
    return c


class TestMergeForwardDecode(unittest.TestCase):
    """真 AES-GCM 解密 + 上游安全约束逐条验证。"""

    def test_roundtrip_real_aesgcm(self):
        body = _encrypt([_msg("1001", "发票已交"), _msg("1002", "明天补", "李四")])
        got = MF.decode_resource(body, _KEY)
        self.assertEqual([m["server_message_id"] for m in got], [1001, 1002])

    def test_wrong_key_rejected(self):
        body = _encrypt([_msg("1001", "x")])
        with self.assertRaises(ValueError):
            MF.decode_resource(body, "ff" * 16)

    def test_short_body_rejected(self):
        for bad in (b"", b"\x00" * 12, b"\x00" * 27):
            with self.assertRaises(ValueError):
                MF.decode_resource(bad, _KEY)

    def test_skey_forms_accepted_and_rejected(self):
        for ok in ("00" * 16, "00" * 24, "00" * 32):
            self.assertEqual(MF.validate_skey(ok), ok)
        for bad in ("", "zz" * 16, "00" * 15, "00" * 20, None, 123):
            with self.assertRaises(ValueError):
                MF.validate_skey(bad)

    def test_missing_messages_rejected(self):
        import os as _os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        n = _os.urandom(12)
        body = n + AESGCM(bytes.fromhex(_KEY)).encrypt(n, b'{"Other":1}', None)
        with self.assertRaises(ValueError):
            MF.decode_resource(body, _KEY)

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            MF.decode_resource(_encrypt([_msg("1001", "a"), _msg("1001", "b")]), _KEY)

    def test_empty_content_rejected(self):
        with self.assertRaises(ValueError):
            MF.decode_resource(
                _encrypt([{"server_message_id": 1001, "content": {}}]), _KEY)

    def test_bad_message_id_rejected(self):
        for bad in ("0", "-1", "abc", "1.5", True, None):
            with self.assertRaises(ValueError):
                MF.decode_resource(
                    _encrypt([{"server_message_id": bad, "content": {"text": "x"}}]),
                    _KEY)

    def test_resource_url_whitelist(self):
        self.assertEqual(MF.check_resource_url(_URL), _URL)
        bad = ["http://p3-aweme-im-merge-share-sign.byteimg.com/x",     # 非 https
               "https://evil.com/x",                                    # 非白名单域
               "https://p3-aweme-im-merge-share-sign.byteimg.com.evil.com/x",
               "https://user:pw@p3-aweme-im-merge-share-sign.byteimg.com/x",
               "https://p3-aweme-im-merge-share-sign.byteimg.com:8443/x",
               "not-a-url", None]
        for u in bad:
            with self.assertRaises(ValueError, msg=f"{u!r} 应被拒"):
                MF.check_resource_url(u)

    def test_upload_keys_validation(self):
        c = _card(["1"], upload=[{"key": _UPLOAD, "count": 2}])
        self.assertEqual(MF.upload_keys(c), [(_UPLOAD, 2)])
        for bad_ups in ([{"key": "evil/x", "count": 1}],
                        [{"key": _UPLOAD, "count": 0}],
                        [{"key": _UPLOAD, "count": "2"}],
                        [{"key": _UPLOAD, "count": 1},
                         {"key": _UPLOAD, "count": 1}],      # 重复 key
                        ["not-dict"]):
            with self.assertRaises(ValueError, msg=f"{bad_ups!r} 应被拒"):
                MF.upload_keys(_card(["1"], upload=bad_ups))


class TestMergeForwardFetch(unittest.IsolatedAsyncioTestCase):
    """补抓流程：完整性校验（条数/ID/status_code）必须**逐条**生效。"""

    def _setup(self, n=2):
        self.msgs = [_msg(str(1000 + i), f"内容{i}") for i in range(n)]
        self.body = _encrypt(self.msgs)
        self.card = _card([str(m["server_message_id"]) for m in self.msgs],
                          upload=[{"key": _UPLOAD, "count": n}])

    async def _req_ok(self, path, body):
        self.assertEqual(path, MF.OBJECT_URL_PATH)
        return {"url": _URL}          # 网页成功响应**没有** status_code

    async def _dl(self, url, max_bytes):
        MF.check_resource_url(url)    # 下载前必须过白名单
        return self.body

    async def test_happy_path_orders_by_index(self):
        self._setup(3)
        out = await MF.fetch_uploaded_bodies(self.card, self._req_ok, self._dl)
        self.assertEqual([m["server_message_id"] for m in out], [1000, 1001, 1002])

    async def test_count_mismatch_rejected(self):
        self._setup(2)
        self.card["upload_key_list"][0]["count"] = 3     # 声明 3 实得 2
        with self.assertRaises(ValueError) as cm:
            await MF.fetch_uploaded_bodies(self.card, self._req_ok, self._dl)
        self.assertIn("条数不符", str(cm.exception))

    async def test_unknown_id_rejected(self):
        self._setup(2)
        self.card["msg_ids"] = [{"msg_id": 1000}, {"msg_id": 9999}]
        with self.assertRaises(ValueError):
            await MF.fetch_uploaded_bodies(self.card, self._req_ok, self._dl)

    async def test_nonzero_status_code_rejected(self):
        self._setup(2)

        async def req(path, body):
            return {"status_code": 5, "url": _URL}
        with self.assertRaises(ValueError) as cm:
            await MF.fetch_uploaded_bodies(self.card, req, self._dl)
        self.assertIn("status_code", str(cm.exception))

    async def test_bad_download_url_rejected_before_download(self):
        self._setup(2)
        called = []

        async def req(path, body):
            return {"url": "https://evil.com/x"}

        async def dl(url, mx):
            called.append(url)
            return self.body
        with self.assertRaises(ValueError):
            await MF.fetch_uploaded_bodies(self.card, req, dl)
        self.assertEqual(called, [], "非法 URL 不应触发下载")

    async def test_no_upload_keys_returns_empty(self):
        self._setup(2)
        self.card.pop("upload_key_list")
        out = await MF.fetch_uploaded_bodies(self.card, self._req_ok, self._dl)
        self.assertEqual(out, [])


class TestMergeForwardRender(unittest.TestCase):
    def test_render_inline_no_network(self):
        inline = [_msg("1001", "发票已交"), _msg("1002", "明天补", "李四")]
        c = _card(["1001", "1002"], inline=inline)
        t = MF.render_text(c)
        self.assertIn("[聊天记录]", t)
        self.assertIn("发票已交", t)
        self.assertIn("李四", t)
        self.assertIn("2 条", t)

    def test_missing_body_labelled_not_faked(self):
        """正文缺失时必须**如实标注**，不得用摘要冒充完整内容。"""
        c = _card(["1001", "1002"], wait_note="预览摘要")
        t = MF.render_text(c)
        self.assertIn("正文未获取", t)
        self.assertIn("预览摘要", t)

    def test_local_map_fallback(self):
        c = _card(["1001", "1002"])
        local = {"1001": {"text": "本地一条"}, "1002": {"text": "本地二条"}}
        t = MF.render_text(c, None, local)
        self.assertIn("本地一条", t)
        self.assertIn("本地二条", t)

    def test_status_reports_can_fetch(self):
        c = _card(["1"], upload=[{"key": _UPLOAD, "count": 1}])
        st = MF.status(c)
        self.assertTrue(st["is_merge_forward"])
        self.assertTrue(st["can_fetch"])
        self.assertFalse(st["has_inline"])
        self.assertEqual(st["count"], 1)

    def test_complete_rejects_partial(self):
        c = _card(["1", "2"], inline=[_msg("1", "a")])       # 只给 1 条
        self.assertFalse(MF.complete(c, c["inline_content"]))
        self.assertEqual(MF.status(c)["has_inline"], False)


class TestContractFix13600(unittest.TestCase):
    """13600 契约订正：必须是**合并转发**，名片改用上游 getProfileCard 判据。"""

    def test_13600_is_merge_forward_not_profile_card(self):
        merge = {"aweType": "13600", "title": "聊天记录", "msg_ids": []}
        self.assertTrue(MF.is_merge_forward(merge))
        self.assertFalse(MF.is_profile_card(merge))
        self.assertEqual(MF.classify_card(merge), "merge_forward")

    def test_profile_card_uses_upstream_criteria(self):
        for c in ({"name": "老王", "secUID": "MS4wLjAB"},
                  {"name": "老王", "sec_uid": "MS4wLjAB"},
                  {"name": "老王", "uid": "123", "source": "others_homepage"}):
            self.assertTrue(MF.is_profile_card(c), c)
            self.assertEqual(MF.classify_card(c), "profile_card")
        # 上游判据不成立 → 不是名片
        for c in ({"name": "老王", "uid": "123"},
                  {"secUID": "MS4wLjAB"},
                  {"name": "", "sec_uid": "x"}):
            self.assertFalse(MF.is_profile_card(c), c)

    def test_capture_labels_13600_as_chat_record(self):
        """回归：capture 必须把 13600 渲染成「分享聊天记录」，**不是**「分享名片」。"""
        t = CC._share_card_text({"aweType": "13600", "title": "群聊记录"})
        self.assertIsNotNone(t)
        self.assertIn("聊天记录", t)
        self.assertNotIn("名片", t)

    def test_capture_labels_profile_card_without_awe_type(self):
        t = CC._share_card_text({"name": "老王", "sec_uid": "MS4wLjABxyz"})
        self.assertIsNotNone(t)
        self.assertIn("名片", t)
        self.assertNotIn("聊天记录", t)


def _mkdb():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE dm_conversations(
            account TEXT, conv_id TEXT, peer_id TEXT, peer_name TEXT,
            short_id TEXT, last_ts REAL DEFAULT 0, conv_type INTEGER DEFAULT 1,
            UNIQUE(account, conv_id));
        CREATE TABLE dm_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, conv_id TEXT,
            role TEXT, text TEXT, msg_type TEXT DEFAULT 'text',
            extra TEXT DEFAULT '{}', ts REAL NOT NULL, msg_id TEXT,
            UNIQUE(account, conv_id, msg_id));
    """)
    return c


class TestNicknameFallback(unittest.TestCase):
    """昵称兜底：默认关 / 硬限速 / 只补缺失 / 只取三键。"""

    def setUp(self):
        NF._last_run_at = 0.0
        NF._day_key = ""
        NF._day_count = 0
        self.conn = _mkdb()
        self._orig = __import__("database").get_db
        sys.modules["database"].get_db = lambda: self.conn
        # 默认关（配置读不到时也必须关）
        self._orig_cfg = NF._cfg

    def tearDown(self):
        sys.modules["database"].get_db = self._orig
        NF._cfg = self._orig_cfg
        NF._last_run_at = 0.0
        NF._day_key = ""
        NF._day_count = 0

    def test_disabled_by_default(self):
        allow, why = NF.rate_limit_check()
        self.assertFalse(allow)
        self.assertEqual(why, "disabled")

    def test_enabled_but_cooldown(self):
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 600,
                           "max_per_run": 10, "daily_cap": 50}
        NF._last_run_at = 1000.0
        allow, why = NF.rate_limit_check(now=1100.0)
        self.assertFalse(allow)
        self.assertTrue(why.startswith("cooldown"))

    def test_daily_cap(self):
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 0,
                           "max_per_run": 10, "daily_cap": 3}
        NF._day_key = __import__("time").strftime("%Y-%m-%d")
        NF._day_count = 3
        allow, why = NF.rate_limit_check()
        self.assertFalse(allow)
        self.assertEqual(why, "daily-cap")

    def test_candidates_only_missing(self):
        rows = [("1", "0:1:1:2", "真实昵称", "SEC_A", 5.0),   # 有昵称 → 排除
                ("2", "0:1:1:3", "", "SEC_B", 4.0),           # 空 → 候选
                ("3", "0:1:1:4", "3887506227210423", "SEC_C", 3.0),  # 昵称=数字UID → 候选
                ("4", "0:1:1:5", "昵称", "", 2.0)]            # 无 sec_uid → 排除
        for cid, pid, nm, sec, ts in rows:
            self.conn.execute(
                "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,short_id,last_ts)"
                " VALUES(?,?,?,?,?,?)", ("acct", cid, pid, nm, sec, ts))
        self.conn.commit()
        got = NF.missing_nickname_convs("acct", 10)
        self.assertEqual([c["conv_id"] for c in got], ["2", "3"])

    def test_no_candidates_no_query(self):
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 0,
                           "max_per_run": 10, "daily_cap": 50}
        called = []
        r = NF.run_fallback("acct", lambda js, arg: called.append(1), db=self.conn)
        self.assertTrue(r["ok"])
        self.assertEqual(r["reason"], "no-candidates")
        self.assertEqual(called, [], "无候选时不得发请求")

    def test_dry_run_does_not_query(self):
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,short_id)"
            " VALUES(?,?,?,?,?)", ("acct", "2", "0:1:1:3", "", "SEC_B"))
        self.conn.commit()
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 0,
                           "max_per_run": 10, "daily_cap": 50}
        called = []
        r = NF.run_fallback("acct", lambda js, arg: called.append(1), db=self.conn,
                            dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["reason"], "dry-run")
        self.assertEqual(called, [], "dry_run 不得发请求")
        self.assertEqual(r["would_query"], ["SEC_B"])

    def test_parse_user_info_only_three_keys(self):
        body = {"status_code": 0, "data": [
            {"sec_uid": "S1", "nickname": "老王", "uid": "9",
             "avatar_small": {"url_list": ["https://a/x"]},
             "extra_secret": "SECRET"},
            {"sec_uid": "S2", "nickname": ""},           # 无昵称 → 丢弃
            {"sec_uid": "", "nickname": "无sec"},        # 无 sec → 丢弃
        ]}
        got = NF.parse_user_info(json.dumps(body))
        self.assertEqual(set(got), {"S1"})
        self.assertEqual(got["S1"]["nickname"], "老王")
        self.assertEqual(got["S1"]["avatar"], "https://a/x")
        self.assertNotIn("extra_secret", got["S1"])

    def test_parse_user_info_rejects_nonzero(self):
        self.assertEqual(NF.parse_user_info({"status_code": 5, "data": []}), {})
        self.assertEqual(NF.parse_user_info("not json"), {})
        self.assertEqual(NF.parse_user_info(None), {})

    def test_run_updates_only_missing_and_never_overwrites(self):
        for cid, nm, sec in (("2", "", "SEC_B"), ("3", "真实昵称", "SEC_C")):
            self.conn.execute(
                "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,short_id)"
                " VALUES(?,?,?,?,?)", ("acct", cid, "0:1:1:9", nm, sec))
        self.conn.commit()
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 0,
                           "max_per_run": 10, "daily_cap": 50}

        def exec_js(js, arg):
            ids = arg[1]
            self.assertEqual(ids, ["SEC_B"], "不应查询已有昵称的会话")
            return {"status": 200, "body": json.dumps(
                {"status_code": 0, "data": [
                    {"sec_uid": "SEC_B", "nickname": "补上的昵称"},
                    {"sec_uid": "SEC_C", "nickname": "不该覆盖"}]})}

        r = NF.run_fallback("acct", exec_js, db=self.conn)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["updated"], 1)
        rows = {x["conv_id"]: x["peer_name"] for x in
                self.conn.execute("SELECT conv_id, peer_name FROM dm_conversations")}
        self.assertEqual(rows["2"], "补上的昵称")
        self.assertEqual(rows["3"], "真实昵称", "已有昵称被覆盖了")

    def test_run_respects_daily_cap(self):
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 0,
                           "max_per_run": 10, "daily_cap": 1}
        NF._day_key = __import__("time").strftime("%Y-%m-%d")
        NF._day_count = 1
        called = []
        r = NF.run_fallback("acct", lambda js, arg: called.append(1), db=self.conn)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "daily-cap")
        self.assertEqual(called, [])

    def test_run_counts_toward_daily_cap(self):
        NF._cfg = lambda: {"enabled": True, "min_interval_sec": 0,
                           "max_per_run": 10, "daily_cap": 50}
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,short_id)"
            " VALUES(?,?,?,?,?)", ("acct", "2", "0:1:1:9", "", "SEC_B"))
        self.conn.commit()
        NF.run_fallback("acct", lambda js, arg: {"status": 200, "body": json.dumps(
            {"status_code": 0, "data": [{"sec_uid": "SEC_B", "nickname": "n"}]})},
            db=self.conn)
        self.assertEqual(NF._day_count, 1, "查询数应计入当日额度")

    def test_status_reports_disabled(self):
        s = NF.status()
        self.assertTrue(s["ok"])
        self.assertFalse(s["would_allow_now"])
        self.assertEqual(s["gate"], "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
