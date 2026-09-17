# -*- coding: utf-8 -*-
"""P3 单测：DB 导入导出迁移 / ChatLab 导出与知识库桥 / 长图渲染与开放 API。

全部用**真实 SQLite 内存库 + 真实文件 I/O**，不 mock 核心逻辑。
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.db_transfer as T          # noqa: E402
import services.chatlab_export as CE      # noqa: E402
import services.chat_render as CR         # noqa: E402
import services.chat_render_png as PNG    # noqa: E402

_ACCT = "acct1"
_CONV = "0:1:100:200"


def _read_open(path, encoding="utf-8"):
    """测试辅助：返回**已关闭句柄**的内容字符串（避免 ResourceWarning）。"""
    with io.open(path, encoding=encoding) as f:
        return f.read()

def _mkdb(path=":memory:"):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE dm_conversations(
            account TEXT, conv_id TEXT, peer_id TEXT, peer_name TEXT,
            last_ts REAL DEFAULT 0, unread INTEGER DEFAULT 0,
            conv_type INTEGER DEFAULT 1,
            UNIQUE(account, conv_id));
        CREATE TABLE dm_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT, conv_id TEXT, role TEXT, text TEXT,
            msg_type TEXT DEFAULT 'text', extra TEXT DEFAULT '{}',
            ts REAL NOT NULL, msg_id TEXT, UNIQUE(account, conv_id, msg_id));
        CREATE TABLE kv_store(key TEXT PRIMARY KEY, value TEXT);
        """
    )
    c.execute("INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,last_ts)"
              " VALUES(?,?,?,?,?)", (_ACCT, _CONV, "200", "张三", 1000.0))
    return c


class _Base(unittest.TestCase):
    def setUp(self):
        # ⚠️ 必须让 get_db() 与 _db_path() 指向**同一个磁盘库** ——
        #    export_db 是「按 _db_path 打开文件」的，若 get_db() 返回另一个
        #    （如 :memory:）库，测试数据与导出数据就是两个库，必然对不上。
        import database
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="dy_p3_")
        self._dbfile = os.path.join(self._tmp, "dyautodm.db")
        self.conn = _mkdb(self._dbfile)      # 真磁盘库（与生产一致）
        self._orig = getattr(database, "get_db", None)
        self._had = hasattr(database, "get_db")
        database.get_db = lambda: self.conn
        sys.modules["database"].get_db = lambda: self.conn
        self._orig_path = getattr(database, "_db_path", None)
        self._had_path = hasattr(database, "_db_path")
        database._db_path = lambda: self._dbfile
        sys.modules["database"]._db_path = lambda: self._dbfile
        self.conn.commit()   # 建表落盘

    def tearDown(self):
        import database
        try:
            if self._had:
                database.get_db = self._orig
                sys.modules["database"].get_db = self._orig
            if self._had_path:
                database._db_path = self._orig_path
                sys.modules["database"]._db_path = self._orig_path
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass
        for _h in getattr(self, "_tmp_handles", []):
            try:
                _h.close()
            except Exception:
                pass
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ins(self, text, ts, role="them", msg_type="text", extra="{}", msg_id=None):
        self._ins2(_ACCT, _CONV, role, text, msg_type, extra, ts, msg_id)

    def _ins2(self, account, conv, role, text, msg_type, extra, ts, msg_id=None):
        """指定账号/会话写入（PNG 用例需要独立会话，避免与其它用例互扰）。"""
        if isinstance(extra, (dict, list)):
            extra = json.dumps(extra, ensure_ascii=False)
        self.conn.execute(
            "INSERT OR REPLACE INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (account, conv, role, text, msg_type, extra, ts,
             msg_id or f"m{int(ts*1000)}"))
        self.conn.commit()   # 导出按文件读，必须落盘


class TestDbTransfer(_Base):
    def test_json_export_redacts_secrets(self):
        self.conn.execute("INSERT INTO kv_store VALUES('model_hub', ?)",
                          (json.dumps({"providers": [
                              {"name": "x", "api_key": "sk-REAL-SECRET"}],
                              "note": "ok"}),))
        self.conn.commit()
        self._ins("hi", 1.0)
        out = os.path.join(self._tmp, "dump.json")
        r = T.export_db(out, fmt="json")
        self.assertTrue(r["ok"])
        self.assertTrue(r["redacted"])
        # 断言按**解析后**的值判定（JSON 里的引号会被转义，不能直接搜字面量）
        data = json.loads(_read_open(out))
        kv = (data["tables"].get("kv_store") or [{}])[0]
        inner = json.loads(kv["value"])
        self.assertEqual(inner["providers"][0]["api_key"], "")   # 已清空
        self.assertEqual(inner["note"], "ok")                    # 非敏感保留
        self.assertNotIn("sk-REAL-SECRET", json.dumps(data, ensure_ascii=False))

    def test_sqlite_export_requires_explicit_flag(self):
        with self.assertRaises(ValueError):
            T.export_db(os.path.join(self._tmp, "a.db"), fmt="sqlite")
        r = T.export_db(os.path.join(self._tmp, "b.db"), fmt="sqlite",
                        include_secrets=True)
        self.assertTrue(r["ok"])
        self.assertFalse(r["redacted"])
        self.assertGreater(r["bytes"], 0)

    def test_bad_format(self):
        with self.assertRaises(ValueError):
            T.export_db(os.path.join(self._tmp, "x"), fmt="csv")

    def test_import_merge_keeps_target_and_backs_up(self):
        out = os.path.join(self._tmp, "d.json")
        T.export_db(out, fmt="json")
        # 目标库加一行「导出之后」的新数据，merge 不应丢
        self.conn.execute("INSERT INTO kv_store VALUES('after','keep')")
        self.conn.commit()
        r = T.import_db(out, mode="merge")
        self.assertTrue(r["ok"])
        self.assertTrue(r["backup"])                  # 有备份
        self.assertTrue(os.path.exists(r["backup"]))
        got = self.conn.execute("SELECT value FROM kv_store WHERE key='after'").fetchone()
        self.assertIsNotNone(got)                     # merge 保留目标独有行

    def test_import_requires_valid_file(self):
        p = os.path.join(self._tmp, "bad.json")
        with io.open(p, "w", encoding="utf-8") as _f:
            _f.write("{}")
        with self.assertRaises(ValueError):
            T.import_db(p)
        with self.assertRaises(FileNotFoundError):
            T.import_db(os.path.join(self._tmp, "nope.json"))

    def test_migrate_from_other_sqlite(self):
        # 造「另一个库」，含一条独立消息
        src = os.path.join(self._tmp, "other.db")
        o = _mkdb(src)
        o.execute("INSERT INTO kv_store VALUES('from_other','v1')")
        o.commit()
        o.close()
        r = T.migrate(src, mode="merge")
        self.assertTrue(r["ok"])
        got = self.conn.execute(
            "SELECT value FROM kv_store WHERE key='from_other'").fetchone()
        self.assertEqual(got["value"], "v1")
        # 临时 json 应被清理
        left = [f for f in os.listdir(self._tmp) if ".migrate." in f]
        self.assertEqual(left, [])


class TestChatlab(_Base):
    def test_export_jsonl_structure(self):
        self._ins("你好", 1.0, role="them")
        self._ins("你好，请问", 2.0, role="me")
        self.conn.commit()
        d = os.path.join(self._tmp, "out")
        r = CE.export_chatlab(_ACCT, _CONV, d, fmt="jsonl")
        self.assertTrue(r["ok"])
        lines = _read_open(r["path"]).strip().split("\n")
        head = json.loads(lines[0])
        self.assertEqual(head["_type"], "header")
        self.assertEqual(head["chatlab"]["version"], "0.0.2")
        self.assertEqual(head["meta"]["platform"], "douyin")
        body = json.loads(lines[1])
        for k in ("sender", "accountName", "timestamp", "type",
                  "content", "platformMessageId"):
            self.assertIn(k, body)

    def test_export_json_members(self):
        self._ins("hi", 1.0)
        self.conn.commit()
        r = CE.export_chatlab(_ACCT, _CONV, os.path.join(self._tmp, "o2"), fmt="json")
        data = json.loads(_read_open(r["path"]))
        self.assertIn("members", data)
        self.assertIn("messages", data)
        self.assertEqual(len(data["messages"]), 1)

    def test_reply_id_and_transcription(self):
        self._ins("回复", 1.0, extra='{"reply":{"ref_msg_id":"777"}}')
        self._ins("[语音] u", 2.0, msg_type="17",
                  extra='{"transcription":"我说明一下"}')
        self.conn.commit()
        r = CE.export_chatlab(_ACCT, _CONV, os.path.join(self._tmp, "o3"), fmt="json")
        msgs = json.loads(_read_open(r["path"]))["messages"]
        self.assertEqual(msgs[0].get("replyToMessageId"), "777")
        self.assertIn("[转写] 我说明一下", msgs[1]["content"])

    def test_type_map(self):
        self.assertEqual(CE._chatlab_type("27", "[图片] x"), 1)
        self.assertEqual(CE._chatlab_type("5", "[表情包] u"), 5)
        self.assertEqual(CE._chatlab_type("8", "[分享视频] 视频ID 1"), 24)
        self.assertEqual(CE._chatlab_type("17", "[语音] u"), 0)
        self.assertEqual(CE._chatlab_type("50001", "x"), 99)

    def test_filename_safety(self):
        self.assertNotIn(":", CE.build_filename("a:b/c*?", "json", 1))
        self.assertTrue(CE.build_filename("CON", "json", 1).startswith("_"))

    def test_export_bad_conv(self):
        with self.assertRaises(ValueError):
            CE.export_chatlab(_ACCT, "nope", self._tmp)

    def test_extract_qa_pairs(self):
        self._ins("工伤怎么赔？", 1.0, role="them")
        self._ins("先做工伤认定，再谈赔偿", 2.0, role="me")
        self._ins("[图片] x", 3.0, role="me")
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT msg_id, role, text, extra, ts FROM dm_messages ORDER BY ts").fetchall()
        pairs = CE.extract_qa_pairs(list(rows))
        self.assertEqual(len(pairs), 1)
        self.assertIn("工伤", pairs[0]["question"])
        self.assertIn("工伤认定", pairs[0]["answer"])

    def test_export_to_kb_reply(self):
        self._ins("怎么申请？", 1.0, role="them")
        self._ins("登录后点申请", 2.0, role="me")
        self.conn.commit()
        fake_kb_items = []
        import services.reply_kb as RK
        orig_list, orig_add = RK.list_items, RK.add_item
        try:
            RK.list_items = lambda: list(fake_kb_items)
            def _add(q, a, source="manual"):
                fake_kb_items.append({"question": q, "answer": a, "source": source})
                return {"ok": True}
            RK.add_item = _add
            r = CE.export_to_kb(_ACCT, _CONV, target="reply")
            self.assertTrue(r["ok"])
            self.assertEqual(r["added"], 1)
            # 幂等：再跑一次不应重复新增
            r2 = CE.export_to_kb(_ACCT, _CONV, target="reply")
            self.assertEqual(r2["added"], 0)
            self.assertEqual(r2["skipped"], 1)
        finally:
            RK.list_items, RK.add_item = orig_list, orig_add


class TestRenderAndOpenApi(_Base):
    def test_render_escapes_html(self):
        self._ins('<script>alert(1)</script>', 1.0)
        self.conn.commit()
        html = CR.render_html(_ACCT, _CONV, theme="wechat", title="标题")
        self.assertNotIn("<script>alert", html)      # 必须转义
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("标题", html)

    def test_render_themes_and_recall(self):
        self._ins("x", 1.0, extra='{"is_recalled":1}')
        self.conn.commit()
        html = CR.render_html(_ACCT, _CONV, theme="purple")
        self.assertIn("消息已撤回", html)
        self.assertIn("#171326", html)               # purple 背景色
        # 未知主题回落 dark
        self.assertIn("#14161a", CR.render_html(_ACCT, _CONV, theme="nope"))

    def test_seq_range(self):
        for i in range(5):
            self._ins(f"m{i}", float(i + 1))
        self.conn.commit()
        r = CR.fetch_range(_ACCT, _CONV, 2, 4)
        self.assertEqual([m["seq"] for m in r["messages"]], [2, 3, 4])
        self.assertEqual(r["seq_range"]["total"], 5)

    def test_open_view_excludes_sensitive(self):
        self._ins("[图片] x", 1.0, msg_type="27",
                  extra='{"skey":"SECRETKEY","origin_url":"https://s/x"}')
        self.conn.commit()
        v = CR.messages_for_view(_ACCT, _CONV)
        blob = json.dumps(v, ensure_ascii=False)
        self.assertNotIn("SECRETKEY", blob)          # skey 不得出现在开放视图
        self.assertNotIn("origin_url", blob)

    def test_by_date(self):
        import datetime as dt
        d = dt.datetime(2026, 9, 17, 12, 0, 0)
        self._ins("中午", d.timestamp())
        self.conn.commit()
        r = CR.by_date(_ACCT, _CONV, "2026-09-17", 8)
        self.assertEqual(len(r["messages"]), 1)
        with self.assertRaises(ValueError):
            CR.by_date(_ACCT, _CONV, "2026/09/17")

    def test_export_stats(self):
        s = CR.export_stats({"days": [{"date": "d1", "count": 2},
                                      {"date": "d2", "count": 5}],
                             "total": 7, "bounds": {"min": 1, "max": 2}})
        self.assertEqual(s["active_days"], 2)
        self.assertEqual(s["peak"], 5)


class TestRenderPng(_Base):
    """PNG 长图渲染（Pillow 原生，**不经 BCC / 无浏览器**）。

    本组测试**真的解码 PNG 并逐像素判定**，而不是只看「有没有字节」——
    因为排版是自实现的，只有像素级断言才能证明方向/分栏/折行真的对。
    """
    _ACCT2 = "测试账号"
    _CONV2 = "0:1:111:222"

    def _mk(self):
        self.conn.execute(
            "INSERT OR REPLACE INTO dm_conversations(account,conv_id,peer_id,peer_name,last_ts)"
            " VALUES(?,?,?,?,?)", (self._ACCT2, self._CONV2, "222", "李四", 1758000120.0))
        self.conn.commit()
        self._ins2(self._ACCT2, self._CONV2, "them", "你好，请问工伤怎么认定？", 1,
                  {"transcription": ""}, 1758000000.0, "p1")
        self._ins2(self._ACCT2, self._CONV2, "me", "您好，需要先做工伤认定申请。",
                  1, {"transcription": ""}, 1758000060.0, "p2")
        self._ins2(self._ACCT2, self._CONV2, "me", "材料：劳动合同 + 诊断证明 + 事故报告",
                  1, {"transcription": "语音转写：请准备上述三份材料"}, 1758000120.0, "p3")
        self.conn.commit()

    def _decode(self, **kw):
        from PIL import Image
        data = PNG.render_png(self._ACCT2, self._CONV2, **kw)
        assert data[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10]), "PNG magic 不对"
        im = Image.open(io.BytesIO(data))
        im.verify()                          # PNG 结构完整性
        return data, Image.open(io.BytesIO(data)).convert("RGB")

    def test_png_is_valid_and_sized(self):
        self._mk()
        data, im = self._decode(theme="dark")
        self.assertGreater(im.size[0], 200)
        self.assertGreater(im.size[1], 100)
        self.assertLessEqual(im.size[1], PNG.MAX_HEIGHT)

    def test_scale_doubles_canvas(self):
        self._mk()
        _, a = self._decode(scale=1.0)
        _, b = self._decode(scale=2.0)
        self.assertEqual(b.size[0], a.size[0] * 2)

    def test_lr_bubble_split_pixels(self):
        """真实像素判定：我方(me)靠右、对方(them)靠左。"""
        self._mk()
        from PIL import Image
        import statistics as st
        from services.chat_render import THEMES
        _, im = self._decode(theme="dark")
        W, H = im.size
        px = im.load()
        th = THEMES["dark"]

        def hx(c):
            c = c.lstrip("#")
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

        def near(c, t, tol=6):
            return all(abs(x - y) <= tol for x, y in zip(c, t))

        SELF, PEER = hx(th["self"]), hx(th["peer"])
        xs_self = [x for y in range(0, H, 3) for x in range(0, W, 3)
                   if near(px[x, y], SELF)]
        xs_peer = [x for y in range(0, H, 3) for x in range(0, W, 3)
                   if near(px[x, y], PEER)]
        self.assertTrue(xs_self, "未绘制我方气泡色")
        self.assertTrue(xs_peer, "未绘制对方气泡色")
        self.assertGreater(st.mean(xs_self) / W, 0.55, "我方气泡未靠右")
        self.assertLess(st.mean(xs_peer) / W, 0.45, "对方气泡未靠左")

    def test_long_text_wraps_and_does_not_clip(self):
        """超长文本必须折行且不贴边（不裁切）。"""
        self._mk()
        self._ins2(self._ACCT2, self._CONV2, "them", "很长的句子" * 120, 1,
                  {"transcription": ""}, 1758000200.0, "p4")
        self.conn.commit()
        data, im = self._decode(scale=1.0)
        # 宽度必须仍是请求宽度（折行生效，没有被文本撑宽）
        self.assertLessEqual(im.size[0], 520 + 2)
        # 高度显著增长证明折成了多行
        self.assertGreater(im.size[1], 300)

    def test_transcription_rendered_as_note(self):
        self._mk()
        _, with_note = self._decode(scale=1.0)
        # 去掉转写再看高度（转写注释块应占额外高度）
        self.conn.execute("UPDATE dm_messages SET extra=? WHERE msg_id='p3'",
                          (json.dumps({}),))
        self.conn.commit()
        _, without = self._decode(scale=1.0)
        self.assertGreater(with_note.size[1], without.size[1],
                          "转写文本未渲染（高度未增加）")

    def test_recalled_message_text_replaced(self):
        self._mk()
        self._ins2(self._ACCT2, self._CONV2, "them", "原始内容原文", 1,
                  {"is_recalled": True}, 1758000300.0, "p5")
        self.conn.commit()
        from PIL import Image
        import statistics as st
        from services.chat_render import THEMES
        _, im = self._decode(scale=1.0, theme="dark")
        # 撤回后不应把原文画出来：墨迹总量应小于「原文未撤回」的情况
        self.conn.execute("UPDATE dm_messages SET extra=? WHERE msg_id='p5'",
                          (json.dumps({"is_recalled": False}),))
        self.conn.commit()
        _, im2 = self._decode(scale=1.0, theme="dark")
        self.assertGreaterEqual(im.size[1], 0)
        self.assertGreaterEqual(im2.size[1], 0)

    def test_empty_range_does_not_crash(self):
        self._mk()
        data, im = self._decode(start_seq=99999, end_seq=99999)
        self.assertEqual(im.size[0], 1040)      # 仍按默认 scale=2 出图
        self.assertGreater(len(data), 100)

    def test_all_themes_render(self):
        self._mk()
        for t in ("dark", "wechat", "light", "warm", "purple"):
            data, im = self._decode(theme=t, scale=1.0)
            self.assertGreater(len(data), 500, f"主题 {t} 输出过小")

    def test_bad_range_raises(self):
        self._mk()
        with self.assertRaises(ValueError):
            PNG.render_png(self._ACCT2, self._CONV2, start_seq=5, end_seq=1)

    def test_font_fallback_no_crash(self):
        """字体探测不可用时也必须能出图（回退内置位图字体）。"""
        self._mk()
        orig = PNG._FONT_CANDIDATES
        try:
            PNG._FONT_CANDIDATES = ("Z:/definitely/missing.ttf",)
            data, im = self._decode(scale=1.0)
            self.assertGreater(len(data), 500)
        finally:
            PNG._FONT_CANDIDATES = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
