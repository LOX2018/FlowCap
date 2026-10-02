# -*- coding: utf-8 -*-
"""门禁：直播搜索条目解析（`_pick_live`）必须适配**现行响应结构**。

## 为什么需要（2026-10-02 实测事故）

`/aweme/v1/web/live/search/` 的 `data[]` 结构**改版**了：

    旧（上游源码与本项目 v0.46.11 之前均按此实现）:
        data[i] = {room_id, title, nickname, user_count, cover, ...}      # 扁平

    新（2026-10-02 实机取证）:
        data[i] = {"type": 1, "lives": {..., "aweme_type": 101,
                                        "rawdata": "<JSON 字符串>"}}
        # 真正的直播间数据全在 lives.rawdata 里（含 id_str/status/title/
        #   user_count/owner.nickname/cover）

**缺陷表现（用户报障）**：「关键词搜索直播间存在问题，无法返回有效结果」。
根因：`_pick_live` 只找顶层 `room_id` ⇒ 新结构下**恒为空** ⇒
`discover_live` 的 `items = [x for x in items if x.get("room_id")]` 把
**全部**条目过滤掉 ⇒ 前端显示「未搜索到」（**假成功**，项目铁律禁止）。

## 本门禁的判据（离线、可回放）

用**真实响应冻结样本**（下方 `_REAL_ITEM_NEW_SHAPE`，取自部署环境实测）断言：

  G1 新结构（lives.rawdata）能解析出 room_id
  G2 能解析出 status（=2 直播中，ENG-023 同判据）
  G3 能解析出 title / online_count / nickname
  G4 旧扁平结构仍兼容（零回归）
  G5 负控：若把 rawdata 当普通 dict（不解析 JSON 字符串）⇒ 必须解析失败

## 运行

    cd backend && python -m unittest test_live_search_shape -v
"""
from __future__ import annotations

import json
import os
import unittest

from api.live_rooms import _pick_live, _unwrap_live_item

# ── 真实冻结样本（2026-10-02 部署环境实测，已裁剪至门禁所需字段）──────────
_REAL_RAWDATA = {
    "id": 7691946497272744755,
    "id_str": "7691946497272744755",
    "status": 2,
    "title": "明法通老涂讲工伤义乌正在直播",
    "user_count": 85,
    "owner": {
        "id": 7645585977474565169,
        "nickname": "义乌市百瑞法律工伤交通事故理赔",
    },
    "cover": {"url_list": ["https://p3-webcast-sign.douyinpic.com/image-cut-tos-priv/x.jpeg"]},
}
_REAL_ITEM_NEW_SHAPE = {
    "type": 1,
    "lives": {
        "aweme_type": 101,
        "author": {
            "nickname": "义乌市百瑞法律工伤交通事故理赔",
            "sec_uid": "MS4wLjABAAAALJ5QTc2MkCpIlOCfzApTwfS0LkXPLlHHgyFHJHCmmo0NcnJVlrqNHWNRmdP0D3x5",
        },
        # ★ 关键：rawdata 是 **JSON 字符串**，不是 dict
        "rawdata": json.dumps(_REAL_RAWDATA, ensure_ascii=False),
    },
}
# 旧扁平结构（改版前，上游源码形态）
_LEGACY_ITEM_FLAT = {
    "room_id": "1234567890",
    "title": "旧结构直播间",
    "nickname": "旧主播",
    "user_count": 12,
    "cover": {"url_list": ["https://example.com/c.jpeg"]},
}


class TestLiveSearchItemShape(unittest.TestCase):
    """直播搜索条目解析（现行结构 + 零回归 + 负控）。"""

    def test_g1_new_shape_resolves_room_id(self):
        """G1：新结构（lives.rawdata）必须能解析出 room_id。

        这是 2026-10-02 事故的**直接判据** —— 解析不出 room_id 时
        discover_live 会把全部条目过滤掉，前端显示「未搜索到」。
        """
        got = _pick_live(_REAL_ITEM_NEW_SHAPE)
        self.assertEqual(got.get("room_id"), "7691946497272744755",
                         "新结构未解析出 room_id ⇒ 搜索结果会被全部过滤（假成功）")

    def test_g2_status_is_live_marker(self):
        """G2：status 必须解析出来（2 = 直播中，与 ENG-023 同判据）。"""
        got = _pick_live(_REAL_ITEM_NEW_SHAPE)
        self.assertEqual(got.get("status"), 2, "未解析出直播状态")

    def test_g3_title_online_nickname(self):
        """G3：title / online_count / nickname 必须解析（前端展示所需）。"""
        got = _pick_live(_REAL_ITEM_NEW_SHAPE)
        self.assertEqual(got.get("title"), "明法通老涂讲工伤义乌正在直播")
        self.assertEqual(got.get("online_count"), 85)
        self.assertEqual(got.get("nickname"), "义乌市百瑞法律工伤交通事故理赔")

    def test_g4_legacy_flat_shape_still_works(self):
        """G4：旧扁平结构必须仍兼容（零回归）。"""
        got = _pick_live(_LEGACY_ITEM_FLAT)
        self.assertEqual(got.get("room_id"), "1234567890")
        self.assertEqual(got.get("title"), "旧结构直播间")
        self.assertEqual(got.get("online_count"), 12)

    def test_g5_negative_control_rawdata_as_dict_fails(self):
        """G5（负控）：把 rawdata 当普通 dict（不解析 JSON 字符串）⇒ 解析不出房间号。

        证明本门禁**真的在测 JSON 解析**，而不是碰巧从别处拿到 room_id。
        """
        broken = {
            "type": 1,
            "lives": {
                "aweme_type": 101,
                # rawdata 故意留成未解析的字符串，且顶层无 room_id/id_str
                "rawdata": json.dumps({"title": "x"}, ensure_ascii=False),
            },
        }
        got = _pick_live(broken)
        self.assertEqual(got.get("room_id") or "", "",
                         "负控失败：rawdata 无 id_str 时不应凭空得到 room_id")

    def test_unwrap_returns_flat_live_dict(self):
        """解包函数本身：新结构 → 扁平直播字典（含 status/title）。"""
        flat = _unwrap_live_item(_REAL_ITEM_NEW_SHAPE)
        self.assertEqual(flat.get("id_str"), "7691946497272744755")
        self.assertEqual(flat.get("status"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
