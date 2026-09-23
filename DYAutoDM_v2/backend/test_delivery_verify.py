# -*- coding: utf-8 -*-
"""P0-1 投递验证闭环修复的回归测试（2026-09-23 审计）。

覆盖四条被击穿的链路（每条都必须在**旧实现下变红**）：

  T1 `delivery_verdict()` 解析与判定口径 —— 上游 isMessageDelivered()：
     server_message_id 非空非 0 且 check_code != 8610 才算投递。
     负控：只回 `message='OK'`（历史上被当成功的那一类）必须判 **unknown**。
  T2 `mark_delivery_verified()` **无证据不写标记**（修复前恒写）。
  T3 标记行 msg_id 走 `verify:` 命名空间 ⇒ ① 不与真实消息行撞唯一索引；
     ② 同一条投递重复标记**幂等**（修复前 msg_id=NULL ⇒ 无上限堆积）。
  T4 `probe_send_delivery()` 只认「带 server_message_id 的标记」：
     盲标记（extra 无 sid）不得报 healthy。
  T5 `api/messages.get_conversation` 不把标记行渲染成「我发的消息」。

隔离：复用 test_config_isolation 的临时根，绝不污染真实库。
运行：cd backend && python -m unittest test_delivery_verify -v
"""
import asyncio
import importlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_config_isolation as iso  # noqa: E402

_ROOT = iso._ROOT


# ── 伪造 message/send 响应原始字节（不依赖 .proto：field100 未定义） ──
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field_no: int, wire: int) -> bytes:
    return _varint((field_no << 3) | wire)


def _bytes_field(field_no: int, payload: bytes) -> bytes:
    return _tag(field_no, 2) + _varint(len(payload)) + payload


def _int_field(field_no: int, val: int) -> bytes:
    return _tag(field_no, 0) + _varint(val)


def _send_response_bytes(server_message_id=None, check_code=None, message="OK") -> bytes:
    """构造 Response{cmd,message, body{100: SendMessageResponseBody}} 的原始字节。"""
    inner = b""
    if server_message_id is not None:
        inner += _int_field(1, int(server_message_id))
    if check_code is not None:
        inner += _int_field(5, int(check_code))
    body = _byte100 = _bytes_field(100, inner) if inner else b""
    top = _int_field(1, 100) + _bytes_field(4, message.encode()) + _bytes_field(6, body)
    return top


def _reload_db():
    os.environ["DY_APP_ROOT"] = _ROOT
    sys.modules.pop("database", None)
    import database
    importlib.reload(database)
    database.reset_connection()
    conn = database.get_db()
    conn.execute("DELETE FROM dm_messages")
    conn.execute("DELETE FROM dm_conversations")
    conn.commit()
    return database


def _fresh_probe():
    os.environ["DY_APP_ROOT"] = _ROOT
    for k in [k for k in list(sys.modules)
              if k == "database" or k == "services.probe" or k.startswith("services.")]:
        sys.modules.pop(k, None)
    import services.probe as P
    return importlib.reload(P)


class TestDeliveryVerdict(unittest.TestCase):
    """T1：解析 + 判定口径。"""

    @classmethod
    def setUpClass(cls):
        os.environ["DY_APP_ROOT"] = _ROOT
        cls.sr = importlib.import_module("services.send_response")

    def test_delivered_with_server_message_id(self):
        raw = _send_response_bytes(server_message_id=7621372915, check_code=8101)
        v = self.sr.delivery_verdict(raw)
        self.assertTrue(v["delivered"], v)
        self.assertEqual(v["state"], "delivered")
        self.assertEqual(v["server_message_id"], "7621372915")
        self.assertEqual(v["check_code"], 8101)

    def test_ok_without_message_id_is_not_delivered(self):
        """**负控（修复前会判成功）**：只回 message='OK'、无消息号 ⇒ 不得算投递。"""
        raw = _send_response_bytes(message="OK")          # body 为空
        v = self.sr.delivery_verdict(raw)
        self.assertFalse(v["delivered"])
        self.assertEqual(v["state"], "unknown")
        self.assertIn("无投递直接证据", v["reason"])

    def test_safety_block_is_blocked(self):
        raw = _send_response_bytes(server_message_id=123, check_code=8610)
        v = self.sr.delivery_verdict(raw)
        self.assertFalse(v["delivered"])
        self.assertEqual(v["state"], "blocked")

    def test_under_review_is_not_delivered(self):
        raw = _send_response_bytes(server_message_id=123, check_code=10502)
        v = self.sr.delivery_verdict(raw)
        self.assertFalse(v["delivered"])
        self.assertEqual(v["state"], "review")

    def test_http_failure_is_blocked(self):
        v = self.sr.delivery_verdict(b"", http_ok=False)
        self.assertFalse(v["delivered"])
        self.assertEqual(v["state"], "blocked")

    def test_zero_message_id_treated_as_absent(self):
        raw = _send_response_bytes(server_message_id=0, check_code=8101)
        v = self.sr.delivery_verdict(raw)
        self.assertFalse(v["delivered"])
        self.assertEqual(v["state"], "unknown")


class TestMarkerWrite(unittest.TestCase):
    """T2/T3：写入条件 + 幂等 + 命名空间。"""

    def setUp(self):
        self.db = _reload_db()
        sys.modules.pop("services.delivery_verify", None)
        import services.delivery_verify as dv
        self.dv = importlib.reload(dv)

    def _count(self, sql, args=()):
        return self.db.get_db().execute(sql, args).fetchone()[0]

    def test_no_evidence_writes_nothing(self):
        """**负控**：无证据（旧实现的无条件调用形态）必须不写标记。"""
        wrote = self.dv.mark_delivery_verified("acc1", "0:1:me:p1", msg_id_hint="",
                                               status_code=0, check_code=0)
        self.assertFalse(wrote)
        self.assertEqual(self._count("SELECT COUNT(*) FROM dm_messages"), 0)

    def test_evidence_from_verdict_writes_marker(self):
        v = {"delivered": True, "server_message_id": "999", "state": "delivered",
             "reason": "ok"}
        self.assertTrue(self.dv.mark_delivery_verified("acc1", "0:1:me:p1", verdict=v))
        row = self.db.get_db().execute(
            "SELECT text, msg_id, msg_type, extra FROM dm_messages").fetchone()
        self.assertTrue(row["text"].startswith("[投递验证]"))
        self.assertEqual(row["msg_id"], "verify:999")        # T3 命名空间
        self.assertEqual(row["msg_type"], "delivery_marker")
        self.assertEqual(json.loads(row["extra"])["server_message_id"], "999")

    def test_marker_idempotent(self):
        """T3：同一条投递重复标记 → 只留一行（修复前每次调用 +1 行）。"""
        v = {"delivered": True, "server_message_id": "777", "state": "delivered"}
        for _ in range(3):
            self.dv.mark_delivery_verified("acc1", "0:1:me:p1", verdict=v)
        self.assertEqual(self._count(
            "SELECT COUNT(*) FROM dm_messages WHERE msg_id='verify:777'"), 1)

    def test_marker_does_not_collide_with_real_message_row(self):
        """T3：真实消息行 msg_id=<sid> 与标记行 msg_id=verify:<sid> 必须并存。"""
        conn = self.db.get_db()
        conn.execute("INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                     " VALUES('acc1','0:1:me:p1','me','你好','text','{}',1.0,'555')")
        conn.commit()
        v = {"delivered": True, "server_message_id": "555", "state": "delivered"}
        self.assertTrue(self.dv.mark_delivery_verified("acc1", "0:1:me:p1", verdict=v))
        rows = conn.execute(
            "SELECT msg_id FROM dm_messages ORDER BY msg_id").fetchall()
        self.assertEqual([r["msg_id"] for r in rows], ["555", "verify:555"])


class TestProbeConsumesOnlyRealEvidence(unittest.TestCase):
    """T4：探针只认带 server_message_id 的标记。"""

    def setUp(self):
        self.db = _reload_db()
        self.P = _fresh_probe()

    def _ins(self, text, msg_id=None, extra=None):
        self.db.get_db().execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            ("acc1", "0:1:me:p1", "me", text, "text",
             json.dumps(extra or {}), 1.0, msg_id))
        self.db.get_db().commit()

    def test_blind_marker_is_not_healthy(self):
        """**负控（修复前的假成功）**：标记存在但无 server_message_id ⇒ degraded。"""
        self._ins("普通消息")                                    # 真实消息 1 条
        self._ins("[投递验证] conv_id=0:1:me:p1", msg_id=None,
                  extra={"delivery_verified": True})             # 盲标记
        r = self.P.probe_send_delivery("acc1")
        self.assertEqual(r["state"], "degraded", r["reasons"])
        self.assertEqual(r["metrics"]["delivery_verified"], 0)
        self.assertEqual(r["metrics"]["marker_without_msgid"], 1)

    def test_marker_with_sid_is_healthy(self):
        self._ins("普通消息")
        self._ins("[投递验证] conv_id=0:1:me:p1 msg_id=888", msg_id="verify:888",
                  extra={"delivery_verified": True, "server_message_id": "888"})
        r = self.P.probe_send_delivery("acc1")
        self.assertEqual(r["state"], "healthy", r["reasons"])
        self.assertEqual(r["metrics"]["delivery_verified"], 1)
        # 标记行不得虚增真实消息数
        self.assertEqual(r["metrics"]["real_msg_rows"], 1)

    def test_no_send_at_all_is_unknown(self):
        r = self.P.probe_send_delivery("acc1")
        self.assertEqual(r["state"], "unknown")


class TestChatRenderExcludesMarker(unittest.TestCase):
    """T5：标记行不得渲染进用户聊天框。"""

    def setUp(self):
        self.db = _reload_db()
        conn = self.db.get_db()
        conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,last_ts,unread)"
            " VALUES('acc1','0:1:me:p1','p1','小张',1.0,0)")
        conn.execute("INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                     " VALUES('acc1','0:1:me:p1','me','真实消息','text','{}',1.0,'real1')")
        conn.execute("INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                     " VALUES('acc1','0:1:me:p1','me','[投递验证] conv_id=0:1:me:p1 msg_id=9',"
                     "'delivery_marker','{\"server_message_id\":\"9\"}',2.0,'verify:9')")
        conn.commit()

    def _fetch(self):
        os.environ["DY_APP_ROOT"] = _ROOT
        # 2026-09-23 修复（HC-10 收尾实测发现，顺序相关的**假失败**）：
        # 原先在此「pop 所有 api.* / database」后不还原 ⇒ 其它测试模块已
        # `from api import live_rooms, live_config` 拿到的**旧模块对象**会与
        # 此后 `sys.modules["api.live_rooms"]` 里的**新对象**脱钩。
        # 实测：test_delivery_verify 先跑时，test_p2_live_guards 的
        # mock.patch.object(live_rooms,'unbind_strategy') 打不中新对象 ⇒
        # delete_strategy 走真解绑分支并谎报 ok=True（假失败，非产品缺陷）。
        # 定式：临时卸载/重载模块后，**退出时必须逐键还原**。
        _saved = {k: v for k, v in sys.modules.items()
                  if k.startswith("api.") or k == "database"}
        try:
            for k in list(_saved):
                sys.modules.pop(k, None)
            import api.messages as M
            importlib.reload(M)
            return asyncio.run(M.get_conversation("acc1", "0:1:me:p1"))
        finally:
            for k in [k for k in list(sys.modules)
                      if k.startswith("api.") or k == "database"]:
                if k not in _saved:
                    sys.modules.pop(k, None)
            sys.modules.update(_saved)

    def test_marker_filtered_out(self):
        got = self._fetch()
        self.assertTrue(got.get("ok"), got)
        msgs = (got.get("conversation") or {}).get("messages") or []
        texts = [m.get("text") for m in msgs]
        self.assertIn("真实消息", texts, f"真实消息被误滤: {texts}")
        self.assertFalse(any(str(t or "").startswith("[投递验证]") for t in texts),
                         f"标记泄漏进聊天框: {texts}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
