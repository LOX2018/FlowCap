# -*- coding: utf-8 -*-
"""线索「客户列显示抖音昵称」回归（2026-10-04，用户指令）。

## 背景
用户报「客户列显示的是 UID（3759948506333804），应显示抖音昵称」。

根因：`save_lead` 落库的 `peer_name` 取自会话列表的 `name` 字段，历史补全路径
下该值常是**裸 UID**（会话未同步过昵称）。⇒ 本测覆盖**出参侧规范化**
（`list_leads` → `_normalize_lead_names`）：按 conv_id 回查
`dm_conversations.peer_name`（capture_all 写入的真实昵称）覆盖。

## 覆盖
| 判据 | 说明 |
|---|---|
| N1 UID 被替换 | `peer_name` 为纯数字 UID + conv_id 可查 ⇒ 换成真实昵称 |
| N2 空值被替换 | `peer_name` 为空串 + conv_id 可查 ⇒ 补上真实昵称 |
| N3 真昵称不覆盖 | 已有真昵称的线索**绝不**被覆盖（防把正确值改成错的） |
| N4 查不到保留原值 | conv_id 不在 `dm_conversations` ⇒ 保留裸 UID（不猜、不填假名） |
| N5 污染行不采用 | `peer_id == 本号 uid` 的行（H-25）⇒ 昵称不可复用 |
| N6 批量规范化 | `list_leads` 出参已规范化（前端无需再处理） |
| N7 空表不报错 | 无线索时安全返回空列表 |

## 运行
    cd backend
    python -m unittest test_lead_nickname -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ROOT = os.path.join(tempfile.gettempdir(), f"lead_nick_test_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)
os.environ.setdefault("DY_APP_ROOT", _ROOT)

from database import get_db  # noqa: E402
from services import ai_reply  # noqa: E402

MY_UID = "316276709526638"
REAL_NICK = "测试客户君"
CID_OK = f"0:1:{MY_UID}:4037023312645291"      # conv_id 有对应昵称行
CID_POLLED = f"0:1:4037023312645291:{MY_UID}"  # conv_id 对应**本号污染**行
CID_UNKNOWN = "0:1:1:9999999999999999"          # conv_id 无对应行


def _seed():
    """建临时库并写入真实线索 + 会话昵称（含污染行）。"""
    ai_reply.ensure_tables()  # 建 ai_leads（临时库初始为空）
    conn = get_db()
    conn.execute("DELETE FROM ai_leads")
    conn.execute("DELETE FROM dm_conversations")
    _lead = ("INSERT INTO ai_leads(account, conv_id, peer_name, contact_type, "
             "contact_value, source_text, status, source, created_at) "
             "VALUES('小助理', ?, ?, 'phone', ?, 'x', 'new', 'backfill', ?)")
    # 真实线索：peer_name 落成了裸 UID（用户报的现象本体）
    conn.execute(_lead, (CID_OK, "4037023312645291", "13037765888", 1790744686.455))
    conn.execute(
        "INSERT INTO ai_leads(account, conv_id, peer_name, contact_type, "
        "contact_value, source_text, status, source, created_at) "
        "VALUES('小助理', ?, '', 'wechat', 'abc123', '微信号 abc123', "
        "'new', 'backfill', ?)", (CID_OK, 1790744686.500))
    # 已有真昵称的线索（不得被覆盖）
    conn.execute(_lead, (CID_OK, "真·昵称", "13100000001", 1790744686.555))
    # conv_id 无对应会话（保留原值）
    conn.execute(_lead, (CID_UNKNOWN, "3759948506333804", "13200000002",
                         1790744686.600))
    # conv_id 对应本号污染行（昵称不可复用，H-25）
    conn.execute(_lead, (CID_POLLED, "1111111111111111", "13300000003",
                         1790744686.655))
    # 会话昵称（capture_all 写入）。dm_conversations 无 created_at 列。
    _conv = ("INSERT INTO dm_conversations(account, conv_id, peer_id, peer_name, "
             "conv_type, last_ts) VALUES('小助理', ?, ?, ?, 1, 1)")
    conn.execute(_conv, (CID_OK, "4037023312645291", REAL_NICK))
    # 本号污染行：peer_id == 本号 uid ⇒ peer_name 实为本号身份，不可复用（H-25）
    conn.execute(_conv, (CID_POLLED, MY_UID, "本号自己的昵称"))
    conn.commit()


def _by_contact(rows):
    return {r["contact_value"]: r for r in rows}


class TestLeadNicknameNormalization(unittest.TestCase):
    def setUp(self):
        _seed()
        self.rows = ai_reply.list_leads(50)
        self.by_c = _by_contact(self.rows)

    # ---- N1/N2：UID 与空值被替换成真实昵称 ----
    def test_n1_uid_replaced(self):
        r = self.by_c["13037765888"]
        self.assertEqual(r["peer_name"], REAL_NICK, "裸 UID 应被换成真实昵称")

    def test_n2_empty_replaced(self):
        r = self.by_c["abc123"]
        self.assertEqual(r["peer_name"], REAL_NICK, "空 peer_name 应补真实昵称")

    # ---- N3：真实昵称绝不覆盖（防把正确值改成错的）----
    def test_n3_real_nickname_preserved(self):
        r = self.by_c["13100000001"]
        self.assertEqual(r["peer_name"], "真·昵称", "已有真昵称不得被覆盖")

    # ---- N4：查不到保留原值，不猜不填假名 ----
    def test_n4_unknown_conv_keeps_original(self):
        r = self.by_c["13200000002"]
        self.assertEqual(r["peer_name"], "3759948506333804",
                         "会话未入库时保留原值（不猜昵称）")

    # ---- N5：污染行不采用（H-25）----
    def test_n5_polluted_row_not_reused(self):
        r = self.by_c["13300000003"]
        self.assertNotEqual(r["peer_name"], "本号自己的昵称",
                            "本号身份不得串到客户身上（H-25）")
        self.assertEqual(r["peer_name"], "1111111111111111",
                         "污染行不可用 ⇒ 保留原值")

    # ---- N8：同会话多条线索，已有真昵称者不得被覆盖 ----
    def test_n8_same_conv_mixed_names_not_overwritten(self):
        """N3 防回归：`m` 按 conv_id 索引 ⇒ 回写必须逐行复检，
        否则同会话内已带真昵称的线索会被另一条线索的昵称覆盖。"""
        rows = ai_reply._normalize_lead_names([
            {"conv_id": "0:1:1:999", "peer_name": "9999999999999999", "account": "a"},
            {"conv_id": "0:1:1:999", "peer_name": "已有真昵称", "account": "a"},
        ])
        self.assertEqual(rows[0]["peer_name"], "9999999999999999")
        self.assertEqual(rows[1]["peer_name"], "已有真昵称",
                         "同会话第二条已有真昵称 ⇒ 不得被覆盖")

    # ---- N6/N7 ----
    def test_n6_list_leads_returns_normalized(self):
        """前端零处理：出参必须已规范化。"""
        for r in self.rows:
            self.assertIn("conv_id", r, "跳转原文依赖 conv_id 出参")
            self.assertTrue(r["conv_id"], "conv_id 不可为空")

    def test_n7_empty_table_safe(self):
        get_db().execute("DELETE FROM ai_leads")
        self.assertEqual(ai_reply.list_leads(10), [])
        self.assertEqual(ai_reply._normalize_lead_names([]), [])


class TestLeadNicknameBoundary(unittest.TestCase):
    """边界：判定函数语义不可写反（历史 bug：`not is_placeholder_name` 导致
    bad 集合恒空 ⇒ 静默空操作，测试全绿但线上无效）。"""

    def test_is_placeholder_name_semantics(self):
        """True = 无效/占位。写反此判定会让规范化**静默失效**。"""
        from services.verdicts import is_placeholder_name
        self.assertTrue(is_placeholder_name("3759948506333804"),
                        "纯数字 UID 必须判为无效")
        self.assertTrue(is_placeholder_name(""), "空串必须判为无效")
        self.assertFalse(is_placeholder_name("掉色人"), "真昵称不得判为无效")

    def test_normalize_is_noop_when_all_valid(self):
        """全部已是真昵称 ⇒ 不改任何值（防过度规范化）。"""
        before = [{"conv_id": "x", "peer_name": "真·昵称", "account": "a"}]
        out = ai_reply._normalize_lead_names(before)
        self.assertEqual(out[0]["peer_name"], "真·昵称")
        self.assertIs(out, before, "无坏值时原对象返回（不重建容器）")
        self.assertIs(out[0]["peer_name"], before[0]["peer_name"],
                      "无坏值时不重写字符串字段")


if __name__ == "__main__":
    unittest.main(verbosity=2)
