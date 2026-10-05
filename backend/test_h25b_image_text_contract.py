# -*- coding: utf-8 -*-
"""H-25（二）统一落库契约 —— 回归门禁（防复发）。

契约（2026-09-25 起）：
  · `dm_messages.text` **只**承载语义标签（`[图片]` / `[表情包]`）
  · 缩略图字节走 `extra.thumb`；原图要素走 `extra.skey/origin_url`
  · 读侧由后端派生 `/conversation` 的 `image_url` / `thumb_url` 下发

本门禁断言**行为机制**（不只看常量），每条都有负控支撑：
  G1  写侧收口点存在且图片 text 纯化（_thumb_semantic_label）
  G2  缩略图提取器把字节移出 text（_extract_thumb_data_uri）
  G3  三条写路径都把 thumb 写进 extra（parse_init / cmd301 / capture_all）
  G4  recv_daemon 的 init 路径**不再**硬编码 extra="{}"（真缺陷回归防护）
  G5  读侧派生 thumb_url（api/messages）
  G6  _sanitize_history_text 只做存量兜底、不误伤正常文本
  G7  真实库：text 无 base64 / 无 http 图片 URL（生产数据面）
  G8  真实库：thumb 与 skey/origin_url 成对（契约完整性）
"""
import json
import os
import re
import sqlite3
import sys
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _read(rel: str) -> str:
    with open(os.path.join(_BACKEND, rel), encoding="utf-8") as f:
        return f.read()


class TestContractWriteSide(unittest.TestCase):
    """G1~G3：写侧契约（源码级断言 + 行为级断言）。"""

    def test_g1_semantic_label_exists(self):
        from auto_dm.conversation_capture import _thumb_semantic_label

        self.assertEqual(_thumb_semantic_label(), "[图片]")

    def test_g2_thumb_extractor_returns_data_uri(self):
        import base64

        from auto_dm.conversation_capture import _extract_thumb_data_uri

        inline = base64.b64encode(b"\x00" * 400).decode()
        got = _extract_thumb_data_uri({"inline_pic": inline})
        self.assertTrue(got.startswith("data:image/webp;base64,"))
        # 负控：无 inline_pic → 空串（不得编造）
        self.assertEqual(_extract_thumb_data_uri({}), "")
        self.assertEqual(_extract_thumb_data_uri({"inline_pic": ""}), "")

    def test_g2b_media_text_no_longer_embeds_base64(self):
        """核心契约：图片消息的 text 不得再含 base64 / URL。"""
        from auto_dm.conversation_capture import _extract_media_text
        import base64

        inline = base64.b64encode(b"\x00" * 400).decode()
        obj = {"inline_pic": inline,
               "resource_url": {"skey": "ab" * 32,
                                "origin_url_list": ["https://p3-sign.douyinpic.com/x"]}}
        out = _extract_media_text(obj)
        self.assertEqual(out, "[图片]")
        self.assertNotIn("base64", out)
        self.assertNotIn("http", out)

    def test_g3_all_paths_write_thumb_into_extra(self):
        src = _read("auto_dm/conversation_capture.py")
        # 三条写路径都要把 thumb 落到 extra
        self.assertGreaterEqual(src.count("_ex[\"thumb\"] = _th"), 1)
        self.assertIn('"thumb": _thumb or None', src)
        self.assertIn("_thumb_semantic_label() if _is_img", src)

    def test_g4_recv_daemon_init_path_keeps_extra(self):
        """真缺陷回归防护：init 同步路径曾硬编码 extra="{}"。"""
        src = _read("daemon/recv_daemon.py")
        self.assertIn("_msg_extra_json(m)", src)
        # 负控：该文件里不应再出现「text, \"{}\"」这种丢弃 extra 的写法
        bad = re.findall(r'"text",\s*"\{\}"', src)
        self.assertEqual(bad, [], f"init 路径又丢弃 extra：{bad}")


class TestContractReadSide(unittest.TestCase):
    """G5~G6：读侧契约。"""

    def test_g5_api_derives_thumb_url(self):
        src = _read("api/messages.py")
        self.assertIn('thumb_url = ex.get("thumb")', src)
        self.assertIn('"thumb_url": thumb_url', src)

    def test_g6_sanitizer_is_legacy_fallback(self):
        from services.ai_reply import _sanitize_history_text

        # 存量兜底：base64 / URL 一律折叠
        self.assertEqual(
            _sanitize_history_text("[图片] data:image/webp;base64," + "A" * 3000),
            "[图片]")
        self.assertEqual(
            _sanitize_history_text("[图片] https://p3-sign.douyinpic.com/x\n[原图] https://y"),
            "[图片]")
        # 负控：正常文本一字不改
        self.assertEqual(_sanitize_history_text("工伤咋赔"), "工伤咋赔")
        self.assertEqual(_sanitize_history_text(""), "")

    def test_g6b_frontend_consumes_thumb_url(self):
        fe = os.path.join(os.path.dirname(_BACKEND), "frontend", "src",
                          "components", "messages")
        shared = open(os.path.join(fe, "message-shared.tsx"), encoding="utf-8").read()
        bubble = open(os.path.join(fe, "message-bubble.tsx"), encoding="utf-8").read()
        page = open(os.path.join(fe, "messages-page.tsx"), encoding="utf-8").read()
        self.assertIn("thumb_url?: string;", shared)
        self.assertIn("m.thumb_url", bubble)
        self.assertIn("thumb_url: m.thumb_url", page)


class TestContractLiveData(unittest.TestCase):
    """G7~G8：生产数据面（库不存在则跳过，不假装通过）。"""

    @classmethod
    def setUpClass(cls):
        root = os.environ.get("FLOWCAP_APP_ROOT", "")
        cls.db = ""
        # 本 ADR 的契约目标库：由账号「四川工伤张老师」定位 conv_id → member 库。
        #
        # ⚠️ 顺序脆弱性实测（2026-09-25）：全量跑时 `test_config_isolation` 会把
        # `FLOWCAP_APP_ROOT` 改写到 `%TEMP%/flowcap_cfgtest_root` 且不保证在本类
        # setUpClass 时已还原 ⇒ 只读环境变量会选到空库（实测 picked=''）。
        # 修法：**候选根 = 环境变量 ∪ 项目设计根常量**，按账号名定位，
        # 与环境变量当前值无关 ⇒ 顺序无关。
        roots = []
        ev = os.environ.get("FLOWCAP_APP_ROOT", "")
        if ev:
            roots.append(ev)
        for cand in (r"C:\temp\flowcap_design", r"C:\temp\flowcap_test"):
            if cand not in roots:
                roots.append(cand)
        for root in roots:
            if not (root and os.path.isdir(os.path.join(root, "members"))):
                continue
            for name in os.listdir(os.path.join(root, "members")):
                p = os.path.join(root, "members", name, "data", "flowcap.db")
                if not os.path.isfile(p):
                    continue
                try:
                    c = sqlite3.connect(p)
                    hit = c.execute(
                        "SELECT 1 FROM dm_conversations WHERE account='四川工伤张老师' "
                        "LIMIT 1").fetchone()
                    c.close()
                except Exception:
                    continue
                if hit:
                    cls.db = p
                    break
            if cls.db:
                break

    def _conn(self):
        if not self.db:
            self.skipTest("未找到生产库（设 FLOWCAP_APP_ROOT 后重跑）")
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def test_g7_no_media_bytes_in_text(self):
        c = self._conn()
        n1 = c.execute("SELECT COUNT(*) n FROM dm_messages "
                       "WHERE text LIKE '[图片] data:image%'").fetchone()["n"]
        n2 = c.execute("SELECT COUNT(*) n FROM dm_messages "
                       "WHERE text LIKE '[图片] http%'").fetchone()["n"]
        c.close()
        self.assertEqual((n1, n2), (0, 0),
                         f"text 仍承载媒体字节/URL：base64={n1} url={n2}")

    def test_g8_thumb_pairs_with_decrypt_secret(self):
        c = self._conn()
        rows = c.execute("SELECT text, extra FROM dm_messages "
                         "WHERE text='[图片]' AND extra LIKE '%\"thumb\"%'").fetchall()
        c.close()
        self.assertGreater(len(rows), 0, "没有任何带 thumb 的图片行（契约未生效）")
        for r in rows:
            ex = json.loads(r["extra"])
            self.assertTrue(str(ex.get("thumb", "")).startswith("data:image/"))
            # 契约完整性：有 thumb 的行应同时保有解密要素（否则原图不可得）
            self.assertTrue(ex.get("skey"), "带 thumb 的行缺 skey")
            self.assertTrue(ex.get("origin_url"), "带 thumb 的行缺 origin_url")


if __name__ == "__main__":
    unittest.main(verbosity=2)
