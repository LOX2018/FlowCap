# coding=utf-8
"""直播监听启动「防连点 + 误导文案」门禁（2026-09-29）。

## 要防住的缺陷（实测取证）

用户在「直播监听」页点「开始自动私信」后收到 **409「如需换房请先停止」**，
误以为启动失败。日志铁证（run_20260929_110902.log）：

    11:19:21 [engine] 引擎启动中 acct=四川工伤张老师    ← 第一次成功
    11:19:21 引擎启动: live_url=992931212705 ...      ← 正常推进
    11:19:21 [engine] acct=... 已在 starting，拒绝重复启动（409）← 同一秒第二次
    11:19:24 [LIVE-022] live-ws 连接已建立             ← 第一次其实已成功

根因（两个，均已修）：
  ① **启动按钮无「请求进行中」锁** —— `live-page.tsx` 的 `engineBusy` 由
     **后端状态轮询**派生（2~5s 才刷新），点击后到状态更新前的窗口内按钮
     不禁用 ⇒ 连点第二下必被后端 409 拒绝。
     对比 `engine-cards.tsx` 有独立 `loading` 标志（请求前设、finally 清）。
  ② **409 文案误导** —— 原文「该账号已在监听…如需换房请先停止」，
     在「其实是第一次已成功」的场景下把人引向"停掉重来"。

本门禁守：
  G1 启动按钮必须有独立请求锁，且该锁要在 onClick 里真正被设置与释放；
  G2 disabled 必须包含该锁（否则锁设了也不禁用按钮）；
  G3 后端 409 文案不得再出现误导性的「如需换房请先停止」原文，
     且必须区分 starting / running 两种状态如实说明。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_PAGE = ROOT / "frontend" / "src" / "components" / "live" / "live-page.tsx"
ENGINE_API = ROOT / "backend" / "api" / "engine.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ===========================================================================
# G1 · 前端：启动按钮必须有独立请求锁
# ===========================================================================

def test_g1_start_has_request_lock_state():
    """G1：必须有独立的「请求进行中」状态（不得只依赖后端轮询派生的 engineBusy）。"""
    s = _read(LIVE_PAGE)
    m = re.search(r"const \[(\w*[Rr]eqBusy\w*|\w*[Bb]usy\w*),\s*set\w+\]\s*=\s*useState",
                  s)
    assert m, "live-page 未见独立 busy 状态"
    # 必须存在一个明确用于「引擎请求」的 busy（名字含 Req/Busy）
    assert "engineReqBusy" in s, (
        "未见 engineReqBusy —— 启动按钮可能仍只依赖轮询派生的 engineBusy（连点复发）")


def test_g2_lock_is_set_and_released():
    """G2：锁必须在 onClick 中真正 set，并在 finally 中释放。

    只看声明等于没锁 —— 实测旧代码 `engineBusy` 被声明但 onClick 里从不设置。
    """
    s = _read(LIVE_PAGE)
    assert re.search(r"if\s*\(\s*engineReqBusy\s*\)\s*return", s), \
        "onClick 开头未见 `if (engineReqBusy) return` 早退"
    assert "setEngineReqBusy(true)" in s, \
        "未见 setEngineReqBusy(true) —— 锁从未被设置（等于没锁）"
    assert "setEngineReqBusy(false)" in s, \
        "未见 setEngineReqBusy(false) —— 锁不会释放（按钮将永久禁用）"


def test_g3_disabled_includes_lock():
    """G3：按钮 disabled 必须包含该锁（否则锁设了也不禁用）。"""
    s = _read(LIVE_PAGE)
    i = s.find('data-od-id="live-start"')
    assert i > 0, "找不到直播页启动按钮"
    blk = s[i: i + 400]
    assert "engineReqBusy" in blk, (
        "启动按钮 disabled 未包含 engineReqBusy ⇒ 请求期间仍可点（连点复发）")


def test_g4_lock_released_in_finally():
    """G4：释放必须在 finally —— 否则请求失败时锁不释放，按钮永久禁用。"""
    s = _read(LIVE_PAGE)
    i = s.find("setEngineReqBusy(true)")
    assert i > 0
    blk = s[i: i + 1500]
    assert ".finally(" in blk, "锁未在 .finally 中释放 ⇒ 失败路径下按钮永久禁用"


# ===========================================================================
# G5 · 后端：409 文案不得误导
# ===========================================================================

def test_g5_409_message_not_misleading():
    """G5：409 不得再出现「如需换房请先停止」这类误导文案。

    实测该文案在「第一次已成功、第二次是连点」场景下把人引向"停掉重来"，
    反而中断了本已正常运行的监听。

    判据只查**字符串字面量**（AST）—— 本判据首版用源码 grep，结果在
    "注释里复述旧文案"上误命中（自造假红，本项目已两次踩到同类）。
    """
    import ast

    src = _read(ENGINE_API)
    bad = "如需换房请先停止"
    lits: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lits.append(node.value)
    assert not any(bad in x for x in lits), \
        "409 文案（字符串字面量）仍是误导性的「如需换房请先停止」"


def test_g6_409_distinguishes_starting_and_running():
    """G6：409 文案必须区分 starting / running 两种状态如实说明。"""
    s = _read(ENGINE_API)
    i = s.find("拒绝重复启动（409）")
    assert i > 0, "找不到 409 分支"
    blk = s[i: i + 900]
    assert "_st ==" in blk or "starting" in blk, "409 分支未区分 starting 状态"
    assert "正在启动中" in blk or "已在监听中" in blk, \
        "409 文案未给出可区分的状态说明"
