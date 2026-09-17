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
        self.conn.execute(
            "INSERT OR REPLACE INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (_ACCT, _CONV, role, text, msg_type, extra, ts,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
