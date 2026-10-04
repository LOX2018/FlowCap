"""通知队列语义 —— 负控/正控。

判据（每条都要能变红）：
  Q1 入队后能取到（内容保真）
  Q2 **取走即清空** —— 第二次取必须为空
     （否则前端每轮轮询重复弹同一条，用户被反复打扰）
  Q3 超上限时有界（丢弃最旧），不无限增长
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "dy_cred_queue_root"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["DY_APP_ROOT"] = str(_TMP_ROOT)
os.environ["DATA_DIR"] = str(_TMP_ROOT / "data")
os.environ["ACCOUNTS_DIR"] = str(_TMP_ROOT / "accounts")

from services import cred_notify as cn  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    cn.drain_pending()
    yield
    cn.drain_pending()


def test_q1_enqueue_then_drain_preserves_content():
    """Q1：入队后能取到，且字段保真。"""
    cn._enqueue("acctQ", "标题Q", "正文Q", "原因Q")
    items = cn.drain_pending()
    assert len(items) == 1
    it = items[0]
    assert it["account"] == "acctQ"
    assert it["title"] == "标题Q"
    assert it["body"] == "正文Q"
    assert it["reason"] == "原因Q"
    assert "ts" in it and "id" in it


def test_q2_drain_clears_queue():
    """Q2 关键：取走即清空（防前端轮询重复弹）。"""
    cn._enqueue("acctQ2", "T", "B")
    assert len(cn.drain_pending()) == 1
    # 第二次必须为空 —— 若这里返回 1，前端会每轮重复弹同一条
    assert cn.drain_pending() == [], "队列未清空 ⇒ 前端会重复弹同一条通知"


def test_q3_bounded_under_flood():
    """Q3：超上限时有界（丢弃最旧），不无限增长。"""
    limit = cn._MAX_PENDING
    for i in range(limit + 20):
        cn._enqueue(f"acct{i}", f"T{i}", "B")
    items = cn.drain_pending()
    assert len(items) == limit, f"应封顶在 {limit}，实际 {len(items)}"
    # 保留的是**最新的**（最旧的被丢弃）
    assert items[-1]["title"] == f"T{limit + 19}", "应保留最新、丢弃最旧"
