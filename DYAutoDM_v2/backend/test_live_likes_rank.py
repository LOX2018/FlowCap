# coding=utf-8
"""贡献榜 / 点赞累计 的确定性门禁（2026-09-30）。

覆盖本次改动两块根因：
  ① 「实时信息流」卡头红心恒 0 —— `WebcastLikeMessage` 只推 feed 未累计 likes；
  ② 贡献榜空区域 —— 上游 `/webcast/ranklist/audience/` 归一化（上游无解析器，须防御式）。

全确定性：不触网、不建 WS，只测纯函数与合并判据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

from collections import deque  # noqa: E402

from core.live_hook import LiveChatHook  # noqa: E402


def _hook() -> LiveChatHook:
    """不跑 __init__（会触网）——手工铺最小状态。"""
    h = LiveChatHook.__new__(LiveChatHook)
    h.feed = deque(maxlen=500)
    h.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
    h.heat_series = deque(maxlen=180)
    h._likes_total = 0
    h._likes_from_ws = False
    return h


# ── ① 点赞累计 ──────────────────────────────────────────────────────────
def test_ws_like_accumulates():
    h = _hook()
    for c in (5, 3):  # 模拟两帧 LikeMessage（count=5、3）
        h._likes_total += c
        h._likes_from_ws = True
        h._merge_likes(0)
    assert h.room_stats["likes"] == 8


def test_stats_zero_does_not_clobber_ws_total():
    h = _hook()
    h._likes_total, h._likes_from_ws = 8, True
    h._merge_likes(0)
    assert h.room_stats["likes"] == 8
    h._merge_likes(0)  # 文案解析到 0（=未解析到）
    assert h.room_stats["likes"] == 8


def test_stats_abs_wins_when_larger():
    h = _hook()
    h._likes_total, h._likes_from_ws = 8, True
    h._merge_likes(0)
    h._merge_likes(20000)  # 文案绝对值更大 → 取它作校准锚
    assert h.room_stats["likes"] == 20000


def test_stats_only_when_no_ws_events():
    h = _hook()
    h._merge_likes(1234)  # 未收到任何 LikeMessage → 用绝对值
    assert h.room_stats["likes"] == 1234


def test_on_room_stats_merges_and_feeds_heat():
    h = _hook()
    h._likes_total, h._likes_from_ws = 10, True
    h.room_stats["likes"] = 10

    class _M:
        displayLong = "在线 100人 · 2.0万点赞"
        displayMiddle = ""
        displayShort = ""

    h._on_room_stats(_M())
    assert h.room_stats["likes"] == 20000          # max(10, 20000)
    assert h.heat_series[-1][2] == 20000           # 热度点用**合并后**的值
    assert h.room_stats["online"] == 100


def test_reset_stream_clears_like_sources():
    h = _hook()
    h._likes_total, h._likes_from_ws = 9, True
    h.room_stats["likes"] = 9
    h.contribution_rank = [{"rank": 1}]
    h.reset_stream()
    assert h.room_stats["likes"] == 0 and h._likes_total == 0
    assert h._likes_from_ws is False and h.contribution_rank == []


# ── ② 贡献榜归一化 ──────────────────────────────────────────────────────
def test_normalize_rank_users_shape():
    data = {"status_code": 0, "data": {"ranks": [
        {"user": {"id_str": "1", "nickname": "A",
                  "avatar_thumb": {"url_list": ["u1"]}}, "score": 5000},
        {"user": {"id_str": "2", "nickname": "B"}, "score": 100},
    ]}}
    rows = LiveChatHook._normalize_rank(data)
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["nickname"] == "A"
    assert rows[0]["score"] == 5000
    assert rows[0]["avatar"] == "u1"


def test_normalize_rank_alt_shape_and_score_text():
    data = {"data": {"rank_list": [
        {"nickname": "C", "user_id": "9", "score_str": "1.2万", "value": 12000},
    ]}}
    rows = LiveChatHook._normalize_rank(data)
    assert rows[0]["uid"] == "9"
    assert rows[0]["score"] == 12000
    assert rows[0]["score_text"] == "1.2万"


def test_normalize_rank_business_error_returns_empty():
    assert LiveChatHook._normalize_rank({"status_code": 20003, "data": {}}) == []


def test_normalize_rank_garbage_never_raises():
    for bad in (None, [], "x", {}, {"data": {"ranks": "nope"}}):
        assert LiveChatHook._normalize_rank(bad) == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
