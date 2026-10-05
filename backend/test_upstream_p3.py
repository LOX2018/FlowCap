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
from pathlib import Path

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
            msg_type TEXT DEFAULT 'text', msg_code TEXT,
            extra TEXT DEFAULT '{}',
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
        self._dbfile = os.path.join(self._tmp, "flowcap.db")
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
        self._inject_export_root()

    # ── (2026-10-03) 显式注入 `system.export_dir`，去掉「环境巧合」绿灯 ──────
    def _inject_export_root(self) -> None:
        """把配置中心的 `system.export_dir` 钉到**本用例的临时目录**。

        🔴 为什么必须显式注入（此前是真缺陷，不只是洁癖）：
        本组 `TestExportDownload` 直接调 `CE.default_export_dir()` /
        `export_chatlab(..., dest_dir="")`，二者最终都读配置中心
        `system.export_dir`（`export_paths.root()`）。而旧断言写死
        `d.parent.name == "exports"` —— 那**只在真实配置恰好为空时**成立。
        于是绿灯是环境巧合：实测注入一个自定义值立刻打红
        （`AssertionError: 'inject_xxxx' != 'exports'`），换台机器、
        用户在系统页改过导出目录、或配置中心默认值一变，红灯就与代码无关地出现。

        注入到 `self._tmp` 之下还有第二重收益：`export_chatlab` 默认落点、
        `_safe_export_file` 的中文样本文件都随之写进 `%TEMP%`，**测试产物不再
        落进仓库**（此前每次跑都在 `<项目根>/exports/聊天记录/` 留下真实导出件）。
        其他配置键仍走真实配置中心 ⇒ 不掩盖任何其它行为。
        """
        from services import app_config as _ac

        self._export_root = os.path.join(self._tmp, "exports")
        self._real_get = getattr(_ac, "get", None)
        self._had_get = hasattr(_ac, "get")
        root = self._export_root

        def _get(section, key, default=None, scope=None):  # noqa: ANN001
            if section == "system" and key == "export_dir":
                return root
            return self._real_get(section, key, default, scope)

        _ac.get = _get                     # type: ignore[assignment]

    def _set_export_root(self, path: str) -> None:
        """临时改注入值（给「自定义 export_dir 也得跟随」的用例用）。"""
        from services import app_config as _ac

        _ac.get = lambda section, key, default=None, scope=None: (  # type: ignore[assignment]
            path if (section, key) == ("system", "export_dir")
            else self._real_get(section, key, default, scope))

    def tearDown(self):
        import database
        from services import app_config as _ac
        if self._had_get:
            _ac.get = self._real_get      # type: ignore[assignment]
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


def _rendered_texts(account: str, conv_id: str) -> str:
    """抓取 `render_png` 实际写入画布的**正文文本**（用于撤回态断言）。

    实现：临时替换 `ImageDraw.text`，把每次绘制的字符串收集起来。
    这样判据直接对准「正文有没有被画出来」，不受提示块/边距的像素干扰。
    """
    from PIL import ImageDraw
    got: list[str] = []
    orig = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **kw):          # noqa: ANN001
        try:
            got.append(str(text))
        except Exception:
            pass
        return orig(self, xy, text, *a, **kw)

    ImageDraw.ImageDraw.text = spy
    try:
        import services.chat_render_png as PNG
        PNG.render_png(account, conv_id, scale=1.0, theme="dark")
    finally:
        ImageDraw.ImageDraw.text = orig
    return "\n".join(got)


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
        """撤回后**不应**把原文画出来。

        2026-09-18 审查修复（A15）：旧断言是两条 `assertGreaterEqual(..., 0)`
        —— **恒为真**，撤回路径即使退化回「照画原文」也会通过（假通过）。
        现改为把两种状态都渲染出来，比较**含原文的墨迹量**：
        撤回态必须严格少于未撤回态，且不再出现原文的像素痕迹。
        """
        self._mk()
        self._ins2(self._ACCT2, self._CONV2, "them", "原始内容原文", 1,
                  {"is_recalled": True}, 1758000300.0, "p5")
        self.conn.commit()
        _, recalled = self._decode(scale=1.0, theme="dark")
        # 判据①（主）：撤回态渲染的是「已撤回」占位，**不得包含原文**
        # 说明：这里比较的是**渲染后的正文文本**（PNG.render_png 内部的写入内容），
        # 而不是像素墨迹量 —— 实测撤回态因为多渲染了「已撤回」提示，
        # 像素反而更多，用墨迹量会得到相反结论（这正是旧断言退化为恒真的原因）。
        import services.chat_render_png as PNG
        texts_recalled = _rendered_texts(self._ACCT2, self._CONV2)
        self.assertNotIn("原始内容原文", texts_recalled,
                         "撤回态仍渲染了原文 —— 撤回路径失效")
        self.conn.execute("UPDATE dm_messages SET extra=? WHERE msg_id='p5'",
                          (json.dumps({"is_recalled": False}),))
        self.conn.commit()
        _, normal = self._decode(scale=1.0, theme="dark")
        texts_normal = _rendered_texts(self._ACCT2, self._CONV2)
        # 判据②（反向对照）：未撤回态**必须**包含原文 —— 证明①不是因为整体没渲染
        self.assertIn("原始内容原文", texts_normal,
                      "未撤回态未渲染原文 —— 前置条件不成立（测试无效）")
        # 判据③：两态渲染结果确实不同（避免「两边都空 → 恒真」）
        self.assertNotEqual(texts_recalled, texts_normal)

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




class TestExportDownload(_Base):
    """ChatLab 导出 → 默认目录 → 下载链路（2026-09-17 乙方案）。"""

    def test_default_export_dir_is_under_app_root(self):
        d = CE.default_export_dir()
        self.assertTrue(d.is_dir(), "默认导出目录应可创建")
        # 2026-10-02 用户要求「导出目录按图片/表格等类型划分」⇒ 分类目录
        # 改用中文名（用户面向中文，资源管理器里一眼可辨）。分类 SSOT =
        # `services/export_paths.CATEGORIES`；此处断言**经由该 SSOT** 求出，
        # 避免两处各写一份字面量而漂移（禁硬编码，见铁律 R8）。
        from services import export_paths

        self.assertEqual(d.name, export_paths.CATEGORIES["chat"][0])
        self.assertEqual(d.name, "聊天记录")
        # 必须是 SSOT 解析出的根目录，不能是别处自算的一套。
        # ⚠️ 2026-10-03：原先此处断言 `d.parent.name == "exports"` ——
        # 那只在**真实配置恰好为空**时成立，绿灯属环境巧合（注入自定义
        # `export_dir` 即打红）。现改为断言「跟着注入值走」，与配置中心无关。
        self.assertEqual(d.parent, export_paths.root())
        self.assertEqual(str(d.parent), self._export_root)
        self.assertEqual(str(export_paths.root()), self._export_root)

    def test_follows_custom_export_dir_not_just_empty(self):
        """🔴 负控：绿灯不能只由「配置为空」撑起来。

        旧实现只断言 `parent.name == "exports"`，即**恰好等于**默认值时才通过
        —— 换一个非空 `export_dir` 就崩。显式注入一个自定义值后，
        根目录与四个分类目录必须**全部**跟着走，才说明真的读了配置中心。
        """
        from services import export_paths

        custom = os.path.join(self._tmp, "custom_export_root")
        self._set_export_root(custom)
        try:
            self.assertEqual(str(export_paths.root()), custom)
            d = CE.default_export_dir()
            self.assertEqual(str(d.parent), custom)
            self.assertEqual(d.name, "聊天记录")
            # 四个分类都落在自定义根下（不是只改了根、分类还留在旧处）
            for key in export_paths.CATEGORIES:
                self.assertEqual(str(export_paths.dir_for(key).parent), custom)
        finally:
            self._set_export_root(self._export_root)

    def test_export_artifacts_stay_out_of_repo(self):
        """测试产物只写 `%TEMP%`：不得在**项目目录**里留下导出件。

        此前 `export_chatlab(dest_dir="")` 会真往
        `<项目根>/exports/聊天记录/` 写文件（哪怕该目录被 .gitignore 挡住），
        等于每次跑测试都往用户的数据目录里丢垃圾。
        """
        from services import export_paths

        self._ins("你好", 1700000000.0)
        self.conn.commit()
        import database
        database._db_path = lambda: self._dbfile
        r = CE.export_chatlab(_ACCT, _CONV, "", fmt="jsonl")
        self.assertTrue(r["ok"], r)
        written = Path(r["path"]).resolve()
        self.assertTrue(str(written).startswith(str(Path(self._tmp).resolve())),
                        f"导出件落在临时目录之外：{written}")
        # 注入根的上游必须是临时目录，且不等于项目根
        self.assertNotEqual(Path(export_paths.root()),
                            Path(__file__).resolve().parent.parent)

    def test_export_without_dest_dir_lands_in_default(self):
        self._ins("你好", 1700000000.0)
        self.conn.commit()
        import database
        database._db_path = lambda: self._dbfile
        r = CE.export_chatlab(_ACCT, _CONV, "", fmt="jsonl")
        self.assertTrue(r["ok"], r)
        self.assertIn("filename", r)
        self.assertEqual(os.path.dirname(r["path"]),
                         str(CE.default_export_dir()))
        data = _read_open(r["path"]).strip().split("\n")
        head = json.loads(data[0])
        self.assertEqual(head["_type"], "header")
        self.assertEqual(len(data) - 1, r["messages"])

    def test_safe_export_file_accepts_chinese_name(self):
        """★ 中文昵称文件名必须可下载（曾因 ASCII 白名单恒 404）。"""
        p = CE.default_export_dir() / "四川工伤-张老师_20260917_export.jsonl"
        p.write_text("{}", encoding="utf-8")
        self.assertIsNotNone(CE._safe_export_file(p.name),
                             "含中文的文件名应被接受")

    def test_safe_export_file_blocks_traversal(self):
        p = CE.default_export_dir() / "ok.jsonl"
        p.write_text("{}", encoding="utf-8")
        for bad in ("../" + p.name, ".." + chr(92) + p.name, "a/" + p.name,
                    "", ".", "..", "not-exist.jsonl", p.name[:-6] + ".../../x"):
            self.assertIsNone(CE._safe_export_file(bad), f"{bad!r} 应被拒")

    def test_render_png_service_is_real_png(self):
        self._ins("测试长图", 1700000000.0)
        self.conn.commit()
        import database
        database._db_path = lambda: self._dbfile
        data = PNG.render_png(_ACCT, _CONV, theme="dark", scale=1.0)
        self.assertEqual(data[:4], b"\x89PNG")
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.verify()


if __name__ == "__main__":
    unittest.main(verbosity=2)
